"""Classifier setup slide — emitted once per classification model.

First slide of the classifier group (classifier_setup →
cls_rois_setup → training_stats → concise_results_classifier). The
screenshot is the Classification Block page (shared across models —
the run captures it once), falling back to the model's own
inspection-editor screenshot.
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

TEMPLATE_ID = "classifier_setup"
SKELETON_PATH = skeleton("classifier_setup")
HOLE_SCHEMA = [
    HoleSpec(name="step_no", kind="text", label="Step number"),
    HoleSpec(
        name="setup_text", kind="text",
        label="Classifier description",
        editor_config={"multiline": True, "max_chars": 600},
    ),
    HoleSpec(
        name="setup_screenshot", kind="image",
        label="Classifier screen screenshot",
        match_hint=(
            "The OV classification-block screen: a labeled part capture "
            "with class ROI boxes and pass/fail class counts"
        ),
    ),
]

_STEP_ID = "classification_block"


def _screenshot_for(bundle: RunBundle, model: ModelInfo) -> Optional[Path]:
    return bundle.screenshot(_STEP_ID) or model.roi_screenshot


def applies(bundle: RunBundle) -> list[ModelInfo]:
    """One context per classification model with a usable screenshot."""
    return [
        m for m in bundle.models_of_type("classification")
        if _screenshot_for(bundle, m) is not None
    ]


def build(bundle: RunBundle, ctx: ModelInfo, llm_cache: dict[str, Any]) -> dict[str, HoleValue]:
    shot = _screenshot_for(bundle, ctx)
    assert shot is not None  # guaranteed by applies()
    summary = (
        model_cache(llm_cache, ctx).get("summary")
        or first_sentences(bundle.description_for(shot))
        or f"{ctx.name} classification model."
    )
    return {
        "step_no": text(""),
        "setup_text": text(summary),
        "setup_screenshot": system_image(shot, label=f"{ctx.name} — classification block"),
    }
