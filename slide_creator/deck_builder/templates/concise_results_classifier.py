"""Concise classifier results slide — key stats, one per classification model.

``deployment_time`` comes from the user's notes when they stated it
(the LLM extracts it verbatim, number + unit); otherwise "2 hours",
same default as recipe_decryption.
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

TEMPLATE_ID = "concise_results_classifier"
SKELETON_PATH = skeleton("concise_results_classifier")
HOLE_SCHEMA = [
    HoleSpec(name="train_acc", kind="text", label="Training accuracy"),
    HoleSpec(name="train_imgs", kind="text", label="Training images"),
    HoleSpec(name="deployment_time", kind="text", label="Deployment time"),
]

_DEFAULT_DEPLOYMENT_TIME = "2 hours"


def applies(bundle: RunBundle) -> list[ModelInfo]:
    return bundle.models_of_type("classification")


def build(bundle: RunBundle, ctx: ModelInfo, llm_cache: dict[str, Any]) -> dict[str, HoleValue]:
    cache = model_cache(llm_cache, ctx)
    report_desc = bundle.description_for(ctx.report_screenshot)

    train_acc = (cache.get("train_acc") or "").strip() or extract_train_accuracy(report_desc)
    train_imgs = (cache.get("train_imgs") or "").strip() or extract_total_images(report_desc)
    deployment = (llm_cache.get("deployment_time") or "").strip() or _DEFAULT_DEPLOYMENT_TIME

    return {
        "train_acc": text(train_acc),
        "train_imgs": text(train_imgs),
        "deployment_time": text(deployment),
    }
