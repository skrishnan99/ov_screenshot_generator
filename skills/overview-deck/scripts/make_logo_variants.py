#!/usr/bin/env python3
"""Derive the logo variants a deck needs from the shipped brand pack.

The brand pack ships the horizontal and vertical logos with a BLACK wordmark,
which is only legal on light backgrounds. Deck title/section/closing slides use
the navy background, so a white-wordmark variant is required.

This script derives it deterministically: pixels that are neutral (low
saturation) become white, the purple logomark is left untouched. Nothing is
redrawn or re-typeset, so the letterforms remain the official artwork.

Outputs (written next to the source files, in assets/brand/derived/):
    logo-H-white.png    horizontal lockup, white wordmark, transparent bg
    logo-V-white.png    vertical lockup, white wordmark, transparent bg
    logomark.png        the mark alone, trimmed, transparent bg

Idempotent: safe to re-run. Run once after installing the skill, or any time
the brand pack is refreshed.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
BRAND = HERE.parent / "assets" / "brand"
OUT = BRAND / "derived"


def trim(im: Image.Image) -> Image.Image:
    """Crop to the alpha bounding box so placement math is predictable."""
    bbox = im.getchannel("A").getbbox()
    return im.crop(bbox) if bbox else im


def whiten_wordmark(src: Path, dst: Path) -> Image.Image:
    """Recolor near-neutral (black) pixels to white; keep chromatic pixels."""
    im = Image.open(src).convert("RGBA")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            mx, mn = max(r, g, b), min(r, g, b)
            sat = (mx - mn) / mx if mx else 0.0
            # Neutral + dark == wordmark ink. The purple mark has sat > 0.25.
            if sat < 0.18 and mx < 190:
                px[x, y] = (255, 255, 255, a)
    im = trim(im)
    im.save(dst)
    return im


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [
        (BRAND / "logo-H-colored.png", OUT / "logo-H-white.png"),
        (BRAND / "logo-V-colored.png", OUT / "logo-V-white.png"),
    ]
    missing = [s for s, _ in jobs if not s.exists()]
    if missing:
        print("ERROR: brand pack incomplete, missing:", *[str(m) for m in missing], sep="\n  ")
        return 1

    for src, dst in jobs:
        im = whiten_wordmark(src, dst)
        print(f"wrote {dst.relative_to(BRAND.parent.parent)}  {im.size}")

    mark_src = BRAND / "logo .png"
    if mark_src.exists():
        mark = trim(Image.open(mark_src).convert("RGBA"))
        mark.save(OUT / "logomark.png")
        print(f"wrote {(OUT / 'logomark.png').relative_to(BRAND.parent.parent)}  {mark.size}")

    # Also emit trimmed copies of the light-background lockups so every logo
    # this skill places has a known-tight bounding box.
    for name in ("logo-H-colored.png", "logo-V-colored.png"):
        im = trim(Image.open(BRAND / name).convert("RGBA"))
        im.save(OUT / name.replace("-colored", "-dark"))
        print(f"wrote {(OUT / name.replace('-colored', '-dark')).relative_to(BRAND.parent.parent)}  {im.size}")

    print("\nlogo variants ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
