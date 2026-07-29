"""Training stats slide — emitted once per classification/segmentation model.

Last content slide in both the classifier and segmenter groups. Both
holes are text: the model type and a multi-line stats summary. Stats
come from the LLM copy pass (which reads the training-report
descriptions), with a deterministic regex fallback over the same
descriptions when the LLM is unavailable.
"""

from __future__ import annotations

from typing import Any

from deck_builder.manifest import HoleSpec, HoleValue
from deck_builder.run_bundle import ModelInfo, RunBundle
from deck_builder.templates._shared import (
    extract_total_images,
    extract_train_accuracy,
    model_cache,
    skeleton,
    text,
)

TEMPLATE_ID = "training_stats"
SKELETON_PATH = skeleton("training_stats")
HOLE_SCHEMA = [
    HoleSpec(name="model_type", kind="text", label="Model type"),
    HoleSpec(
        name="training_stats", kind="text",
        label="Training statistics",
        editor_config={"multiline": True, "max_chars": 2000},
    ),
]

_MODEL_TYPE_DISPLAY = {
    "classification": "Classifier",
    "segmentation": "Segmenter",
}


def applies(bundle: RunBundle) -> list[ModelInfo]:
    """One context per trainable model."""
    return list(bundle.models)


def build(bundle: RunBundle, ctx: ModelInfo, llm_cache: dict[str, Any]) -> dict[str, HoleValue]:
    stats = (model_cache(llm_cache, ctx).get("training_stats") or "").strip()
    if not stats:
        stats = _fallback_stats(bundle, ctx)
    return {
        "model_type": text(_MODEL_TYPE_DISPLAY.get(ctx.block_type, "Model")),
        "training_stats": text(stats),
    }


def _fallback_stats(bundle: RunBundle, model: ModelInfo) -> str:
    """Regex-extracted stats from the model's training-report description."""
    desc = bundle.description_for(model.report_screenshot)
    lines = [f"Model: {model.name}"]
    if desc:
        acc = extract_train_accuracy(desc)
        imgs = extract_total_images(desc)
        if acc != "—":
            lines.append(f"Training accuracy: {acc}")
        if imgs != "—":
            lines.append(f"Total training images: {imgs}")
    if len(lines) == 1:
        lines.append("Training statistics not captured for this run.")
    return "\n".join(lines)
