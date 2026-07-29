"""Segmenter setup slide — emitted once per segmentation model.

First slide of the segmenter group (segmenter_setup → training_stats
→ concise_results_segmenter). The screenshot is the Segmentation Block
page, falling back to the model's inspection-editor screenshot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from deck_builder.manifest import HoleSpec, HoleValue
from deck_builder.run_bundle import ModelInfo, RunBundle
from deck_builder.templates._shared import (
    first_sentences,
    model_cache,
    skeleton,
    system_image,
    text,
)

TEMPLATE_ID = "segmenter_setup"
SKELETON_PATH = skeleton("segmenter_setup")
HOLE_SCHEMA = [
    HoleSpec(name="step_no", kind="text", label="Step number"),
    HoleSpec(
        name="setup_text", kind="text",
        label="Segmenter description",
        editor_config={"multiline": True, "max_chars": 600},
    ),
    HoleSpec(
        name="setup_screenshot", kind="image",
        label="Segmenter screen screenshot",
        match_hint=(
            "The OV segmentation-block screen: a part capture with "
            "pixel-level defect mask annotations or annotation tools"
        ),
    ),
]

_STEP_ID = "segmentation_block"


def _screenshot_for(bundle: RunBundle, model: ModelInfo) -> Optional[Path]:
    return bundle.screenshot(_STEP_ID) or model.roi_screenshot


def applies(bundle: RunBundle) -> list[ModelInfo]:
    """One context per segmentation model with a usable screenshot."""
    return [
        m for m in bundle.models_of_type("segmentation")
        if _screenshot_for(bundle, m) is not None
    ]


def build(bundle: RunBundle, ctx: ModelInfo, llm_cache: dict[str, Any]) -> dict[str, HoleValue]:
    shot = _screenshot_for(bundle, ctx)
    assert shot is not None  # guaranteed by applies()
    summary = (
        model_cache(llm_cache, ctx).get("summary")
        or first_sentences(bundle.description_for(shot))
        or f"{ctx.name} segmentation model."
    )
    return {
        "step_no": text(""),
        "setup_text": text(summary),
        "setup_screenshot": system_image(shot, label=f"{ctx.name} — segmentation block"),
    }
