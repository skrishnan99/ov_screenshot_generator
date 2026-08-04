#!/usr/bin/env python3
"""Boilerplate slides as owned skeletons: extract once, fill, place exactly.

The library, capability and team slides are standing company content. They
used to be re-authored through the layout engine (a different approximation
every deck) and then transplanted verbatim from the 26 MB reference template
(byte-exact, but unownable — its defects shipped into every deck and could
never be fixed without editing the shared example).

This module implements the third way, borrowed from the plugin's skeleton
pipeline: each boilerplate slide is EXTRACTED ONCE into a small single-slide
pptx under assets/skeletons/, which the skill then owns. A sidecar YAML with
the same stem describes its content holes. Build scripts query a skeleton,
fill its holes (the library screenshot; {{tokens}} if any are ever added) and
append it verbatim — and because the skeletons are ours, a layout defect is
fixed once in the skeleton file and stays fixed.

Maintainer operations:

    python template_slides.py                # list skeletons and their holes
    python template_slides.py --extract      # (re)build skeletons from the
                                             # reference template; run only
                                             # when the company template
                                             # changes, then re-apply fixes
                                             # and review the diff

Build-script use (via ovdeck):

    d.skeleton_slide("library", image=run/"deliverables/screenshots/12_library.png")
    for name in ("capabilities", "team", "thank_you"):
        d.skeleton_slide(name)
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = SKILL.parent.parent
TEMPLATE = SKILL / "assets" / "example-decks" / "Overview AI blank test report.pptx"
SKELETON_DIR = SKILL / "assets" / "skeletons"

# The plugin owns the OPC surgery and the skeleton introspection; this skill
# ships inside it. Same three-levels-up convention publish.py uses.
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# Where each boilerplate slide lives in the reference template (1-based, as
# the slides read in PowerPoint). Used by --extract only; at build time the
# skeleton files are the source of truth.
TEMPLATE_SLIDES = {
    "library": 10,           # on-device storage -> retraining; takes the screenshot
    "capabilities": 11,      # "5 factors make us unique"
    "defect_generator": 12,  # "Can't wait for the rare defect? Generate it."
    "integration": 13,       # "Integration built for everyone."
    "team": 14,              # Team & Locations
    "thank_you": 15,         # closing
}

# Carried by every report unless the request says otherwise.
DEFAULT_CLOSING = ("library", "capabilities", "team", "thank_you")


class TemplateError(RuntimeError):
    """A skeleton is missing or does not match what the build needs."""


def skeleton_path(name: str) -> Path:
    if name not in TEMPLATE_SLIDES:
        raise TemplateError(
            f"unknown boilerplate slide {name!r}; have {sorted(TEMPLATE_SLIDES)}"
        )
    p = SKELETON_DIR / f"{name}.pptx"
    if not p.exists():
        raise TemplateError(
            f"skeleton not found at {p}. Skeletons ship with the skill; "
            f"regenerate with `python {Path(__file__).name} --extract` if the "
            f"directory was lost."
        )
    return p


def profile(name: str) -> dict:
    """The skeleton's content holes, via the plugin's introspection: tokens,
    image slots with geometry, sidecar guidance, drift warnings."""
    from deck.slots import skeleton_profile

    return skeleton_profile(str(skeleton_path(name)))


def append(prs, name: str, image: str | Path | None = None,
           tokens: dict | None = None) -> None:
    """Fill skeleton `name` and append it to an open Presentation, verbatim.

    `image` fills the slide's picture hole (the library slide has one);
    `tokens` fills any {{token}} text holes. Unknown surplus is an error —
    a hole that silently stays unfilled ships placeholder text to a customer.
    """
    from pptx import Presentation

    from deck.assemble import (
        append_slide,
        bake_theme_colors,
        fill_tokens,
        find_tokens,
        image_slots,
    )

    src = Presentation(str(skeleton_path(name)))
    slide = src.slides[0]

    if tokens:
        have = set(find_tokens(slide))
        surplus = set(tokens) - have
        if surplus:
            raise TemplateError(
                f"skeleton {name!r} has no {{{{token}}}} hole named "
                f"{sorted(surplus)}; it has {sorted(have) or 'none'}"
            )
        fill_tokens(slide, tokens)
    leftover = find_tokens(slide)
    if leftover:
        raise TemplateError(
            f"skeleton {name!r} still has unfilled tokens {leftover}; "
            f"pass them via tokens={{...}}"
        )

    # For a skeleton, holes are the "Insert screenshot here" placeholders;
    # PICTURE shapes are standing content and must never be replaced.
    # (fill_images targets pictures first, so it is the wrong tool here.)
    from deck.assemble import MSO_PICTURE, _fill_placeholder

    holes = [sh for sh in image_slots(slide) if sh.shape_type != MSO_PICTURE]
    if image is not None:
        if not holes:
            raise TemplateError(
                f"skeleton {name!r} has no 'Insert screenshot here' hole "
                f"to put an image in"
            )
        if not Path(image).exists():
            raise TemplateError(f"image for skeleton {name!r} not found: {image}")
        _fill_placeholder(slide, holes[0], Path(image))
        holes = holes[1:]
    if holes:
        # An unfilled hole would ship its placeholder note to a customer.
        raise TemplateError(
            f"skeleton {name!r} has {len(holes)} unfilled screenshot hole(s); "
            f"pass image=..."
        )

    # Idempotent when the extraction already baked; load-bearing if a skeleton
    # was hand-edited afterwards and picked up scheme colours again.
    bake_theme_colors(slide)
    ctx = {"used": {str(p.partname) for p in prs.part.package.iter_parts()},
           "by_hash": {}}
    append_slide(prs, src, ctx)


# ---------------------------------------------------------------- extraction
#
# Owning the skeletons is the point: template defects are fixed HERE, once,
# and re-extraction reapplies them. Never fix a defect in the built deck.

def _strip_page_numbers(slide) -> None:
    """The template's slides carry hardcoded "NN / 15" page markers. Any
    number would be wrong in a built deck, whose slide count varies."""
    import re

    from deck.assemble import iter_shapes

    page = re.compile(r"^\s*\d{1,2}\s*/\s*\d{1,2}\s*$")
    for sh in list(iter_shapes(slide)):
        if sh.has_text_frame and page.match(sh.text_frame.text or ""):
            sh._element.getparent().remove(sh._element)


def _fix_library_subtitle(slide) -> None:
    """The subtitle box is one line high (H 0.24) for a line that is marginal
    at its width: with substituted fonts it sometimes wraps, and the overflow
    lands on the screenshot at T 1.66. Give the box two lines of room — a
    wrapped second line then ends at 1.59, clear of the image."""
    from pptx.util import Inches

    from deck.assemble import iter_shapes

    for sh in iter_shapes(slide):
        if sh.has_text_frame and sh.text_frame.text.startswith("Easier root cause"):
            sh.height = Inches(0.45)


# Applied in order after transplant, before save. _strip_page_numbers runs on
# every skeleton; per-name fixups follow.
FIXUPS: dict[str, tuple] = {
    "*": (_strip_page_numbers,),
    "library": (_fix_library_subtitle,),
}


def _shrink_pictures(slide) -> int:
    """Re-embed each picture at the size its frame actually displays.

    The template's slides carry photography at capture resolution — one slide
    weighed 16 MB for four pictures displayed a few inches wide. The raster is
    downscaled to the frame size at the plugin's EMBED_DPI and re-encoded
    (PNG/JPEG chosen empirically, transparency preserved); only the blip's
    image part is re-pointed, so geometry, crops and stretch stay byte-exact.
    Uniform scaling keeps any srcRect crop fractions valid. Failures leave the
    original picture — a bigger skeleton beats a broken one.
    """
    import io

    from PIL import Image
    from pptx.oxml.ns import qn
    from pptx.util import Emu

    from deck.assemble import EMBED_DPI, MSO_PICTURE, _encode_smaller, iter_shapes

    shrunk = 0
    for sh in iter_shapes(slide):
        if sh.shape_type != MSO_PICTURE:
            continue
        try:
            blip = sh._element.blipFill.find(qn("a:blip"))
            part = sh.part.related_part(blip.get(qn("r:embed")))
            with Image.open(io.BytesIO(part.blob)) as im:
                tw = max(1, int(Emu(sh.width).inches * EMBED_DPI))
                th = max(1, int(Emu(sh.height).inches * EMBED_DPI))
                if im.width <= tw and im.height <= th:
                    continue
                scale = min(tw / im.width, th / im.height)
                if im.mode == "P":
                    out = im.convert("RGBA" if "transparency" in im.info else "RGB")
                elif im.mode == "CMYK":
                    out = im.convert("RGB")
                else:
                    out = im.copy()
                out = out.resize(
                    (max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                    Image.LANCZOS,
                )
                tmp = _encode_smaller(out)
            _, new_rId = sh.part.get_or_add_image_part(tmp)
            blip.set(qn("r:embed"), new_rId)
            shrunk += 1
        except Exception:
            continue
    if shrunk:
        _drop_orphan_image_rels(slide)
    return shrunk


def _drop_orphan_image_rels(slide) -> None:
    """Re-pointing a blip leaves the old image relationship on the slide
    part, and the transplant copies every relationship — so without this the
    skeleton carries the original AND the shrunk raster and comes out BIGGER
    than before. Drop image rels nothing in the slide XML references any
    more. The same part may back several pictures, so every referenced rId is
    collected before anything is dropped."""
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    from pptx.oxml.ns import qn

    used = set()
    for el in slide._element.iter():
        for attr in (qn("r:embed"), qn("r:link")):
            v = el.get(attr)
            if v:
                used.add(v)
    part = slide.part
    for rId, rel in list(part.rels.items()):
        if rel.reltype == RT.IMAGE and rId not in used:
            part.drop_rel(rId)


def extract(template: Path | None = None, out_dir: Path | None = None) -> list[Path]:
    """Lift each boilerplate slide out of the reference template into a small
    single-slide skeleton. Maintainer operation: rerun when the company
    template changes, then re-apply any skeleton fixes and review the diff.

    Each skeleton carries only the parts its slide references (its layout,
    master, theme and media travel via the plugin's OPC import), so the files
    are small and fully self-contained. Theme colours are baked at extraction,
    making the skeleton immune to recolouring by any host deck.
    """
    from pptx import Presentation

    from deck.assemble import append_slide, bake_theme_colors

    src_path = Path(template) if template else TEMPLATE
    if not src_path.exists():
        raise TemplateError(f"reference template not found at {src_path}")
    out = Path(out_dir) if out_dir else SKELETON_DIR
    out.mkdir(parents=True, exist_ok=True)

    src = Presentation(str(src_path))
    written = []
    for name, num in TEMPLATE_SLIDES.items():
        if num - 1 >= len(src.slides):
            raise TemplateError(
                f"template has {len(src.slides)} slides; {name!r} expected at "
                f"{num}. The template changed — update TEMPLATE_SLIDES."
            )
        # A fresh base per skeleton, sized like the template. The default
        # python-pptx master stays in the file unused (~10 KB) — harmless,
        # and pruning it is not worth the surgery.
        base = Presentation()
        base.slide_width = src.slide_width
        base.slide_height = src.slide_height
        n = _shrink_pictures(src.slides[num - 1])
        # Fixups run on the source slide: the transplanted copy's part is a
        # raw OPC Part without python-pptx's typed shape API. Mutating the
        # in-memory template is safe — it is never saved back.
        for fix in FIXUPS.get("*", ()) + FIXUPS.get(name, ()):
            fix(src.slides[num - 1])
        bake_theme_colors(src.slides[num - 1])
        ctx = {"used": {str(p.partname) for p in base.part.package.iter_parts()},
               "by_hash": {}}
        append_slide(base, src, ctx, index=num - 1)
        dest = out / f"{name}.pptx"
        base.save(str(dest))
        written.append(dest)
        print(f"  {dest.relative_to(SKILL)}  {dest.stat().st_size / 1024:.0f} KB"
              + (f"  ({n} picture(s) re-embedded at display size)" if n else ""))
    return written


def main() -> int:
    if "--extract" in sys.argv:
        extract()
        return 0
    print(f"skeletons in {SKELETON_DIR.relative_to(SKILL)}:\n")
    for name in TEMPLATE_SLIDES:
        try:
            p = profile(name)
        except TemplateError as e:
            print(f"  {name:17} MISSING ({e})")
            continue
        holes = [s["name"] + ("*" if not s["is_picture"] else "")
                 for s in p["slots"]]
        star = " [default]" if name in DEFAULT_CLOSING else ""
        print(f"  {name:17} tokens={p['tokens'] or '—'}  "
              f"image-holes={holes or '—'}  {p['title'][:40]!r}{star}")
        for w in p["warnings"]:
            print(f"        drift: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
