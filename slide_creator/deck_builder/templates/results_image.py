"""Results-with-image slide — one per model, right after Problem & Solution.

Shows what the inspection does at a glance: recipe name, a short
success tagline, and two images of the inspected part. The run bundle
has no raw part captures, so the system defaults are the two most
part-forward UI screenshots for the model:

* ``image``              ← the model's AI-block page (05/07 — shows the
                           live capture of the part in the canvas)
* ``image_with_overlay`` ← the model's inspection-editor screenshot
                           (04_roi_* — the part with ROI overlays)

These are exactly the holes user photos of the part should land in —
the ``match_hint``s steer the asset matcher accordingly.
"""

from __future__ import annotations

from typing import Any, Optional
from pathlib import Path

from deck_builder.manifest import HoleSpec, HoleValue
from deck_builder.run_bundle import ModelInfo, RunBundle
from deck_builder.templates._shared import skeleton, system_image, text

TEMPLATE_ID = "results_image"
SKELETON_PATH = skeleton("results_image")
HOLE_SCHEMA = [
    HoleSpec(name="recipe_name", kind="text", label="Recipe name"),
    HoleSpec(
        name="brief_description", kind="text",
        label="Success one-liner",
        editor_config={"max_chars": 60},
    ),
    HoleSpec(
        name="image", kind="image",
        label="Example image (no overlay)",
        match_hint=(
            "A raw example inspection image or photo of the part, "
            "no overlays or annotations"
        ),
    ),
    HoleSpec(
        name="image_with_overlay", kind="image",
        label="Example image (with overlay)",
        match_hint=(
            "The inspected part with inspection overlays "
            "(ROI boxes / defect masks) drawn on it"
        ),
    ),
]

_DEFAULT_TAGLINE = "Automated AI visual inspection"

_BLOCK_PAGE_STEP = {
    "classification": "classification_block",
    "segmentation": "segmentation_block",
}


def _image_candidates(bundle: RunBundle, model: ModelInfo) -> tuple[Optional[Path], Optional[Path]]:
    """(raw-ish, overlay) system screenshot candidates for one model."""
    block_page = bundle.screenshot(_BLOCK_PAGE_STEP.get(model.block_type, ""))
    return block_page, model.roi_screenshot


def applies(bundle: RunBundle) -> list[ModelInfo]:
    """One context per model with at least one usable system image."""
    out = []
    for model in bundle.models:
        raw, overlay = _image_candidates(bundle, model)
        if raw or overlay:
            out.append(model)
    return out


def build(bundle: RunBundle, ctx: ModelInfo, llm_cache: dict[str, Any]) -> dict[str, HoleValue]:
    raw, overlay = _image_candidates(bundle, ctx)
    # Either candidate can be missing; each hole falls back to the other
    # so both picture holes are always filled when the slide is emitted.
    raw = raw or overlay
    overlay = overlay or raw
    assert raw is not None and overlay is not None  # guaranteed by applies()

    tagline = (llm_cache.get("success_tagline") or "").strip() or _DEFAULT_TAGLINE
    return {
        "recipe_name": text(bundle.recipe_name),
        "brief_description": text(tagline),
        "image": system_image(raw, label=f"{ctx.name} — example capture"),
        "image_with_overlay": system_image(overlay, label=f"{ctx.name} — ROI overlay"),
    }
