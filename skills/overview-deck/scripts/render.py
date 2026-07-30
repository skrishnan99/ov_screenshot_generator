#!/usr/bin/env python3
"""Render a .pptx to per-slide PNGs so the deck can actually be looked at.

    python scripts/render.py out/report.pptx [--dpi 80] [--out DIR] [--slides 1,4,9]

Writes <out>/slide-01.png ... and prints the paths. Read the PNGs — a deck that
passes every programmatic check can still look wrong, and this is the only way
to find that out before a customer does.

Requires LibreOffice (soffice) and PyMuPDF.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

SOFFICE_CANDIDATES = [
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "soffice",
]


def find_soffice() -> str | None:
    for c in SOFFICE_CANDIDATES:
        if Path(c).exists() or shutil.which(c):
            return c
    return None


def render(pptx: Path, out_dir: Path, dpi: int = 80,
           only: list[int] | None = None) -> list[Path]:
    import fitz

    soffice = find_soffice()
    if not soffice:
        raise SystemExit(
            "LibreOffice not found. Install it (brew install --cask libreoffice) — "
            "rendering is a required step, not an optional one."
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx)],
        capture_output=True, text=True, timeout=600,
    )
    pdf = out_dir / (pptx.stem + ".pdf")
    if not pdf.exists():
        raise SystemExit(f"conversion failed:\n{proc.stdout}\n{proc.stderr}")

    doc = fitz.open(pdf)
    written: list[Path] = []
    for i, page in enumerate(doc, 1):
        if only and i not in only:
            continue
        p = out_dir / f"slide-{i:02d}.png"
        page.get_pixmap(dpi=dpi).save(str(p))
        written.append(p)
    doc.close()
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx")
    ap.add_argument("--out", default=None, help="output dir (default: <pptx dir>/render)")
    ap.add_argument("--dpi", type=int, default=80)
    ap.add_argument("--slides", default=None, help="comma-separated slide numbers")
    a = ap.parse_args()

    pptx = Path(a.pptx).resolve()
    out = Path(a.out) if a.out else pptx.parent / "render"
    only = [int(s) for s in a.slides.split(",")] if a.slides else None
    files = render(pptx, out, a.dpi, only)
    print(f"rendered {len(files)} slide(s) to {out}")
    for f in files:
        print(f"  {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
