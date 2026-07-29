"""Concise segmenter results slide — key stats, one per segmentation model."""

from __future__ import annotations

from typing import Any

from deck_builder.manifest import HoleSpec, HoleValue
from deck_builder.run_bundle import ModelInfo, RunBundle
from deck_builder.templates._shared import (
    extract_mean_iou,
    extract_total_images,
    model_cache,
    skeleton,
    text,
)

TEMPLATE_ID = "concise_results_segmenter"
SKELETON_PATH = skeleton("concise_results_segmenter")
HOLE_SCHEMA = [
    HoleSpec(name="mean_iou", kind="text", label="Mean IoU"),
    HoleSpec(name="train_imgs", kind="text", label="Training images"),
    HoleSpec(name="deployment_time", kind="text", label="Deployment time"),
]

_DEFAULT_DEPLOYMENT_TIME = "2 hours"


def applies(bundle: RunBundle) -> list[ModelInfo]:
    return bundle.models_of_type("segmentation")


def build(bundle: RunBundle, ctx: ModelInfo, llm_cache: dict[str, Any]) -> dict[str, HoleValue]:
    cache = model_cache(llm_cache, ctx)
    report_desc = bundle.description_for(ctx.report_screenshot)

    mean_iou = (cache.get("mean_iou") or "").strip() or extract_mean_iou(report_desc)
    train_imgs = (cache.get("train_imgs") or "").strip() or extract_total_images(report_desc)
    deployment = (llm_cache.get("deployment_time") or "").strip() or _DEFAULT_DEPLOYMENT_TIME

    return {
        "mean_iou": text(mean_iou),
        "train_imgs": text(train_imgs),
        "deployment_time": text(deployment),
    }
