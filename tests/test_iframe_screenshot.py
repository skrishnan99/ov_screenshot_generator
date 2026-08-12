"""The Node-RED deliverable is the flow iframe, not the page around it.

`screenshot_iframe: true` on a capture step makes cli screenshot only the
page's dominant embedded <iframe> — largest visible one, and it must cover a
meaningful fraction of the viewport so hidden/utility iframes can never win.
A page without such an iframe degrades to the normal full-page capture with
a warning, never a failure.

Run: uv run python tests/test_iframe_screenshot.py
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

import cli  # noqa: E402
from core.browser import Browser  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class _FakeHandle:
    def __init__(self, box, png=b"iframe-png", broken=False):
        self._box = box
        self._png = png
        self._broken = broken

    def bounding_box(self):
        if self._broken:
            raise RuntimeError("detached")
        return self._box

    def screenshot(self):
        return self._png


class _FakePage:
    viewport_size = {"width": 1600, "height": 1000}

    def __init__(self, handles):
        self._handles = handles

    def query_selector_all(self, selector):
        assert selector == "iframe"
        return self._handles


def _browser(handles) -> Browser:
    b = Browser()
    b.page = _FakePage(handles)
    return b


def main() -> int:
    failures = []

    # --- the largest visible iframe wins ---
    small = _FakeHandle({"x": 0, "y": 0, "width": 400, "height": 300}, b"small")
    big = _FakeHandle({"x": 225, "y": 100, "width": 1300, "height": 850}, b"big")
    got = _browser([small, big]).iframe_screenshot_bytes()
    if got != b"big":
        failures.append(f"largest iframe not chosen: {got!r}")

    # --- tiny iframes (trackers, widgets) can never win ---
    if _browser([small]).iframe_screenshot_bytes() is not None:
        failures.append("a sub-threshold iframe was captured")

    # --- no iframes at all -> None, caller falls back ---
    if _browser([]).iframe_screenshot_bytes() is not None:
        failures.append("no-iframe page did not return None")

    # --- detached/boxless handles are tolerated, not fatal ---
    broken = _FakeHandle(None, broken=True)
    boxless = _FakeHandle(None)
    try:
        got = _browser([broken, boxless, big]).iframe_screenshot_bytes()
        if got != b"big":
            failures.append(f"broken handles hid the real iframe: {got!r}")
    except Exception as e:
        failures.append(f"broken handle raised instead of being skipped: {e!r}")

    # --- wiring: the capture branch prefers the iframe and falls back ---
    src = inspect.getsource(cli.main)
    if "screenshot_iframe" not in src or "iframe_screenshot_bytes" not in src:
        failures.append("cli capture branch is not wired for screenshot_iframe")
    elif src.index("iframe_screenshot_bytes") > src.index(
        "png = browser.screenshot_bytes(full_page=True)"
    ):
        failures.append("iframe capture must be tried BEFORE the full-page fallback")

    # --- and the node-red view step opts in ---
    spec = yaml.safe_load((REPO / "tasks" / "ov80i.yaml").read_text())
    step = next(s for s in spec["steps"] if s["id"] == "io_node_red_view")
    if not step.get("screenshot_iframe"):
        failures.append("io_node_red_view does not request the iframe capture")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL IFRAME-SCREENSHOT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
