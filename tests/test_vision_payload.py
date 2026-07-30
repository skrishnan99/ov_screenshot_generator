"""Regression: vision payloads must not overflow the SDK's stdio buffer.

The CLI-backed backends hand images to the model by writing them to disk and
having it Read them. That Read result travels back through the SDK's stdio
stream, which defaults to a 1MB cap and raises a FATAL reader error when a
single message exceeds it — it does not degrade.

Observed on a real run: full-resolution "View All ROIs" grid captures (up to
1600x3232, 1.6MB) overflowed it. Every image-load check then returned
"vision check error: Failed to decode JSON: JSON message exceeded maximum
buffer size", which reads as a FALSE "not loaded" verdict, so the step polled
to its ceiling and captured a possibly-unrendered page.

Two independent guards, both tested here:
  1. downscale_for_vision() caps the long edge at what the API resizes to
     anyway (~1568px), so no quality is lost.
  2. every ClaudeAgentOptions we build raises max_buffer_size, so a large
     tool result of any kind cannot kill a session.

Run: uv run python tests/test_vision_payload.py
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import llm  # noqa: E402

SDK_DEFAULT_BUFFER = 1024 * 1024


def _png(width: int, height: int, mode: str = "RGB") -> bytes:
    """A camera-UI-like image: flat chrome, panels, and a textured image area.

    Deliberately NOT random noise. Noise is incompressible, so it would make
    the payload assertions fail on an input shape that cannot occur — real
    captures are mostly flat UI around one photographic region. Nor is it a
    single flat colour, which would compress to nothing and assert nothing.
    """
    from PIL import Image, ImageDraw

    im = Image.new(mode, (width, height), (245, 245, 248)[: len(mode)] or 0)
    d = ImageDraw.Draw(im)
    # Sidebar + toolbar chrome.
    d.rectangle([0, 0, width // 6, height], fill=(90, 60, 150)[: len(mode)] or 0)
    d.rectangle([0, 0, width, height // 14], fill=(255, 255, 255)[: len(mode)] or 0)
    # A photographic-looking main image area: smooth gradient plus detail.
    x0, y0, x1, y1 = width // 5, height // 8, width - 40, height - 60
    for i in range(y0, y1, 2):
        t = (i - y0) / max(1, y1 - y0)
        d.line([(x0, i), (x1, i)], fill=(int(40 + 120 * t), int(60 + 90 * t), 70)[: len(mode)] or 0)
    for k in range(24):  # ROI boxes / annotations
        bx = x0 + (k * 53) % max(1, (x1 - x0 - 80))
        by = y0 + (k * 97) % max(1, (y1 - y0 - 60))
        d.rectangle([bx, by, bx + 70, by + 50], outline=(255, 210, 0)[: len(mode)] or 0, width=3)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _fits(data: bytes) -> bool:
    """Would this survive the SDK's default buffer once base64-encoded?"""
    return len(data) * 4 / 3 < SDK_DEFAULT_BUFFER


def main() -> int:
    from PIL import Image

    failures = []

    # Oversized captures, including the tall grid shape that actually broke.
    for w, h in [(1600, 3232), (1600, 1000), (2560, 1440)]:
        raw = _png(w, h)
        out = llm.downscale_for_vision(raw)
        with Image.open(io.BytesIO(out)) as im:
            # When the re-encode would cost more bytes than it saves we keep
            # the original, which legitimately stays over the pixel cap — the
            # API resizes it server-side. Only check dimensions when we
            # actually replaced the image.
            if out is not raw:
                if max(im.size) > llm.VISION_MAX_EDGE:
                    failures.append(
                        f"{w}x{h}: long edge {max(im.size)} > {llm.VISION_MAX_EDGE}"
                    )
                if abs((im.width / im.height) - (w / h)) > 0.01:
                    failures.append(f"{w}x{h}: aspect ratio not preserved -> {im.size}")
        # Representative captures must comfortably clear the SDK default.
        # Note the *guarantee* lives in SDK_BUFFER_BYTES, not here: downscaling
        # cannot bound an arbitrary image's compressed size. This asserts the
        # realistic case, which is what regressed in production.
        if not _fits(out):
            failures.append(
                f"{w}x{h}: UI-like capture still overflows the 1MB default "
                f"after downscale ({len(out)/1024:.0f}K)"
            )
        # Must never make things worse.
        if len(out) > len(raw):
            failures.append(
                f"{w}x{h}: downscale INCREASED payload {len(raw)/1024:.0f}K "
                f"-> {len(out)/1024:.0f}K"
            )

    # Already-small images must pass through byte-identical (no requantising).
    small = _png(800, 600)
    if llm.downscale_for_vision(small) is not small:
        failures.append("small image was re-encoded instead of passed through")

    # Transparency must survive as PNG rather than being flattened onto black.
    rgba = llm.downscale_for_vision(_png(2000, 1200, "RGBA"))
    with Image.open(io.BytesIO(rgba)) as im:
        if im.mode not in ("RGBA", "LA", "P"):
            failures.append(f"alpha lost on downscale: mode={im.mode}")

    # Never raise on junk — a vision call with an odd image beats no call.
    for junk in (b"", b"notanimage", b"\x89PNG\r\n\x1a\n truncated"):
        try:
            if llm.downscale_for_vision(junk) != junk:
                failures.append(f"junk input mutated: {junk[:12]!r}")
        except Exception as e:
            failures.append(f"junk input raised {type(e).__name__}: {junk[:12]!r}")

    # The buffer ceiling must actually be raised above the SDK default.
    if llm.SDK_BUFFER_BYTES <= SDK_DEFAULT_BUFFER:
        failures.append(f"SDK_BUFFER_BYTES={llm.SDK_BUFFER_BYTES} not above the 1MB default")

    # Every session we build must set it — a new call site that forgets is
    # exactly how this regresses.
    roots = {
        "core/llm.py": "SDK_BUFFER_BYTES",
        "core/navigator_sdk.py": "max_buffer_size",
        "deck/agent_slide.py": "max_buffer_size",
    }
    base = Path(__file__).resolve().parent.parent
    for rel, needle in roots.items():
        text = (base / rel).read_text()
        n_opts = text.count("ClaudeAgentOptions(")
        if n_opts and text.count(needle) < n_opts:
            failures.append(
                f"{rel}: {n_opts} ClaudeAgentOptions but only "
                f"{text.count(needle)} {needle} references"
            )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL VISION-PAYLOAD CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
