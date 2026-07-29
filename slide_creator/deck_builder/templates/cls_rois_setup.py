"""Classifier ROIs slide — emitted once per classification model.

Second slide in the classifier group. Shows where this classifier's
ROIs sit on the part: the model's own inspection-editor screenshot
(``04_roi_<model>.png`` — that model's ROI list selected and its ROIs
highlighted), falling back to the run's View-All-ROIs modal capture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from deck_builder.manifest import HoleSpec, HoleValue
from deck_builder.run_bundle import ModelInfo, RunBundle
from deck_builder.templates._shared import skeleton, system_image, text

TEMPLATE_ID = "cls_rois_setup"
SKELETON_PATH = skeleton("cls_rois_setup")
HOLE_SCHEMA = [
    HoleSpec(name="step_no", kind="text", label="Step number"),
    HoleSpec(
        name="setup_screenshot", kind="image",
        label="All ROIs screenshot",
        match_hint=(
            "A grid or view of the classifier's ROI crops, or the ROI "
            "editor with this model's regions highlighted"
        ),
    ),
]

_FALLBACK_STEP_ID = "view_all_rois"


def _screenshot_for(bundle: RunBundle, model: ModelInfo) -> Optional[Path]:
    return model.roi_screenshot or bundle.screenshot(_FALLBACK_STEP_ID)


def applies(bundle: RunBundle) -> list[ModelInfo]:
    """One context per classification model with a usable screenshot."""
    return [
        m for m in bundle.models_of_type("classification")
        if _screenshot_for(bundle, m) is not None
    ]


def build(bundle: RunBundle, ctx: ModelInfo, llm_cache: dict[str, Any]) -> dict[str, HoleValue]:
    shot = _screenshot_for(bundle, ctx)
    assert shot is not None  # guaranteed by applies()
    return {
        "step_no": text(""),
        "setup_screenshot": system_image(shot, label=f"{ctx.name} — ROIs"),
    }
