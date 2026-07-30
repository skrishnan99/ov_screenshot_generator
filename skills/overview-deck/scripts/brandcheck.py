#!/usr/bin/env python3
"""Audit a built .pptx against the Overview brand pack.

    python scripts/brandcheck.py out/report.pptx [--json report.json]

This inspects the saved file itself, not the builder that produced it, so it
also catches decks edited by hand or produced by another tool. It reads the
OOXML directly: every explicit colour, every typeface, the slide size, and the
images on the opening and closing slides.

Exit code 0 = clean, 1 = violations found.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOKENS = json.loads((HERE.parent / "assets" / "tokens.json").read_text())

PALETTE = {h.lstrip("#").upper()
           for ramp in TOKENS["ramps"].values() for h in ramp}
PALETTE |= {TOKENS["core"][k]["hex"].lstrip("#").upper() for k in TOKENS["core"]}
PALETTE |= {v.lstrip("#").upper() for v in TOKENS["neutral"].values()}
PALETTE |= {v.lstrip("#").upper() for v in TOKENS["semantic"].values()}

FONT = TOKENS["typography"]["family"]
EMU_PER_IN = 914400
EXPECT_W, EXPECT_H = TOKENS["geometry"]["slide_in"]

CLR_RE = re.compile(rb'srgbClr val="([0-9A-Fa-f]{6})"')
FONT_RE = re.compile(rb'typeface="([^"]+)"')
SLIDE_RE = re.compile(r"ppt/slides/slide(\d+)\.xml$")


def audit(pptx: Path) -> dict:
    findings: list[dict] = []
    colors_seen: Counter = Counter()
    fonts_seen: Counter = Counter()
    slide_media: dict[int, int] = {}

    with zipfile.ZipFile(pptx) as z:
        pres = z.read("ppt/presentation.xml").decode("utf-8", "ignore")
        m = re.search(r'sldSz[^/]*cx="(\d+)"[^/]*cy="(\d+)"', pres)
        if m:
            w, h = int(m.group(1)) / EMU_PER_IN, int(m.group(2)) / EMU_PER_IN
            if abs(w - EXPECT_W) > 0.01 or abs(h - EXPECT_H) > 0.01:
                findings.append({
                    "slide": 0, "kind": "slide-size",
                    "detail": f"{w:.2f}x{h:.2f}in, expected {EXPECT_W}x{EXPECT_H}in",
                })

        slide_names = sorted(
            (n for n in z.namelist() if SLIDE_RE.search(n)),
            key=lambda n: int(SLIDE_RE.search(n).group(1)),
        )
        for name in slide_names:
            idx = int(SLIDE_RE.search(name).group(1))
            raw = z.read(name)
            for c in CLR_RE.findall(raw):
                hexv = c.decode().upper()
                colors_seen[hexv] += 1
                if hexv not in PALETTE:
                    findings.append({
                        "slide": idx, "kind": "off-brand-colour",
                        "detail": f"#{hexv} is not in the Overview palette",
                    })
            for f in FONT_RE.findall(raw):
                fname = f.decode()
                fonts_seen[fname] += 1
                if fname not in (FONT, "+mj-lt", "+mn-lt"):
                    findings.append({
                        "slide": idx, "kind": "off-brand-font",
                        "detail": f"'{fname}' used; the deck font is {FONT}",
                    })
            rels = f"ppt/slides/_rels/slide{idx}.xml.rels"
            if rels in z.namelist():
                slide_media[idx] = z.read(rels).decode("utf-8", "ignore").count("../media/")
            else:
                slide_media[idx] = 0

        n_slides = len(slide_names)

    if n_slides:
        if not slide_media.get(1):
            findings.append({"slide": 1, "kind": "missing-logo",
                             "detail": "opening slide has no image — the logo is mandatory"})
        if not slide_media.get(n_slides):
            findings.append({"slide": n_slides, "kind": "missing-logo",
                             "detail": "closing slide has no image — the logo is mandatory"})

    # de-duplicate identical findings (one line per distinct problem per slide)
    seen, unique = set(), []
    for f in findings:
        key = (f["slide"], f["kind"], f["detail"])
        if key not in seen:
            seen.add(key)
            unique.append(f)

    return {
        "pptx": str(pptx),
        "slides": n_slides,
        "findings": unique,
        "colors": dict(colors_seen.most_common()),
        "fonts": dict(fonts_seen),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    rep = audit(Path(a.pptx))
    print(f"brandcheck: {rep['pptx']}")
    print(f"  slides: {rep['slides']}")
    print(f"  distinct colours: {len(rep['colors'])}  fonts: {list(rep['fonts'])}")
    if rep["findings"]:
        print(f"\n  {len(rep['findings'])} finding(s):")
        for f in rep["findings"]:
            where = f"slide {f['slide']}" if f["slide"] else "deck"
            print(f"    [{where}] {f['kind']}: {f['detail']}")
    else:
        print("\n  clean — every colour and typeface is on-brand")

    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=2))
        print(f"\n  report: {a.json}")
    return 1 if rep["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
