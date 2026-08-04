#!/usr/bin/env python3
"""Transplant boilerplate slides verbatim from the blank test-report template.

The library, capability and team slides are standing company content. Every
report carries the same ones, and re-authoring them through the layout engine
produced a different approximation each time — sometimes missing, sometimes
included, never exactly right.

So do not rebuild them. Lift the real slides out of

    assets/example-decks/Overview AI blank test report.pptx

with the OPC part surgery the plugin already uses to assemble decks, which
carries each slide's own master, theme, images and text intact. The only edit
is the library slide's screenshot, dropped into the "Insert screenshot here"
placeholder the template already provides.

Usage from a build script:

    from ovdeck import Deck
    d = Deck("out/report.pptx")
    ...
    d.template_slide("library", image=run/"deliverables/screenshots/12_library.png")
    d.template_slide("capabilities")
    d.template_slide("team")
    d.template_slide("thank_you")

Run directly to list what the template offers:

    python template_slides.py
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = SKILL.parent.parent
TEMPLATE = SKILL / "assets" / "example-decks" / "Overview AI blank test report.pptx"

# The plugin owns the transplant machinery; this skill ships inside it. Same
# three-levels-up convention publish.py uses to find publish_cli.py.
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# 1-based, matching how the slides read in PowerPoint.
BOILERPLATE = {
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
    """The template is missing or does not contain the expected slide."""


def _template(path: Path | None = None):
    from pptx import Presentation

    src = Path(path) if path else TEMPLATE
    if not src.exists():
        raise TemplateError(
            f"reference template not found at {src}. It ships with the skill; "
            f"a deck cannot carry the standard closing slides without it."
        )
    return Presentation(str(src))


def append(prs, name: str, image: str | Path | None = None, template: Path | None = None):
    """Append boilerplate slide `name` to an open Presentation, verbatim.

    `image` fills the slide's "Insert screenshot here" placeholder — the
    library slide is the one that takes one. Raises TemplateError rather than
    silently producing a deck without its standard closing.
    """
    from deck.assemble import append_slide, bake_theme_colors, fill_images, image_slots

    if name not in BOILERPLATE:
        raise TemplateError(f"unknown boilerplate slide {name!r}; have {sorted(BOILERPLATE)}")
    src = _template(template)
    idx = BOILERPLATE[name] - 1
    if idx >= len(src.slides):
        raise TemplateError(
            f"template has {len(src.slides)} slides; {name!r} expected at "
            f"{BOILERPLATE[name]}. The template changed — update BOILERPLATE."
        )

    slide = src.slides[idx]
    if image is not None:
        slots = image_slots(slide)
        if not slots:
            raise TemplateError(
                f"boilerplate slide {name!r} has nowhere to put an image "
                f"(no picture and no 'Insert screenshot here' placeholder)"
            )
        fill_images(slide, [str(image)])

    # Resolve scheme colours against the template's OWN theme before the slide
    # leaves it, so the transplant cannot be re-coloured by the host deck.
    bake_theme_colors(slide)
    ctx = {"used": {str(p.partname) for p in prs.part.package.iter_parts()}, "by_hash": {}}
    append_slide(prs, src, ctx, index=idx)


def main() -> int:
    src = _template()
    print(f"{TEMPLATE.name}: {len(src.slides)} slides\n")
    for name, n in BOILERPLATE.items():
        slide = src.slides[n - 1]
        head = next(
            (sh.text_frame.text.split("\n")[0][:52]
             for sh in slide.shapes
             if sh.has_text_frame and sh.text_frame.text.strip()), "")
        slots = len(image_slots_safe(slide))
        star = " *default" if name in DEFAULT_CLOSING else ""
        print(f"  {n:2}  {name:17} image-slots={slots}  {head}{star}")
    return 0


def image_slots_safe(slide):
    try:
        from deck.assemble import image_slots

        return image_slots(slide)
    except Exception:
        return []


if __name__ == "__main__":
    raise SystemExit(main())
