"""Shared constants and helpers used by multiple template modules.

Private module (leading underscore), same convention as
recipe_decryption: template modules import what they need directly;
nothing outside ``templates/`` should depend on this file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from deck_builder.manifest import ImageValue, TextValue

# All skeleton paths are resolved relative to this anchor.
_SKELETONS_DIR = Path(__file__).resolve().parent.parent / "skeletons"


def skeleton(name: str) -> Path:
    """Absolute path to a skeleton file by stem name (appends .pptx)."""
    return _SKELETONS_DIR / f"{name}.pptx"


# ---------------------------------------------------------------------------
# Hole value constructors
# ---------------------------------------------------------------------------

def text(value: str) -> TextValue:
    return TextValue(text=value or "")


def system_image(path: Path, label: str = "") -> ImageValue:
    """An ``ImageValue`` for a system-generated screenshot."""
    return ImageValue(path=str(path), source="system", label=label)


# ---------------------------------------------------------------------------
# LLM cache access
# ---------------------------------------------------------------------------
#
# The cache produced by ``deck_builder.llm.build_llm_cache`` has this shape
# (all fields optional — templates must tolerate an empty dict):
#
#   {
#     "problem": str, "solution": str,
#     "success_tagline": str, "deployment_time": str,
#     "imaging": {"summary": str},
#     "aligner": {"summary": str},
#     "roi": {"summary": str},
#     "nodered_logic": str,
#     "models": {
#       "<model slug>": {
#         "summary": str, "training_stats": str,
#         "train_acc": str, "train_imgs": str, "mean_iou": str,
#       },
#     },
#   }

def model_cache(llm_cache: dict[str, Any], model: Any) -> dict[str, Any]:
    """Per-model entry from the LLM cache ({} when absent)."""
    models = llm_cache.get("models")
    if isinstance(models, dict):
        entry = models.get(getattr(model, "slug", ""))
        if isinstance(entry, dict):
            return entry
    return {}


def section_summary(llm_cache: dict[str, Any], key: str) -> str:
    """``llm_cache[key]["summary"]`` with full tolerance for absence."""
    section = llm_cache.get(key)
    if isinstance(section, dict):
        summary = section.get("summary")
        if isinstance(summary, str):
            return summary.strip()
    return ""


# ---------------------------------------------------------------------------
# Deterministic text fallbacks from system descriptions
# ---------------------------------------------------------------------------

def first_sentences(description: str, max_chars: int = 350) -> str:
    """Leading sentences of a screenshot description, within a budget.

    The system descriptions open with a one-sentence statement of what
    the screen is and does — a serviceable slide blurb when the LLM
    copy pass is unavailable. Cuts on sentence boundaries; hard-trims
    with an ellipsis only when even the first sentence overflows.
    """
    stripped = " ".join((description or "").split())
    if not stripped:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", stripped)
    out = ""
    for sentence in sentences:
        candidate = f"{out} {sentence}".strip()
        if out and len(candidate) > max_chars:
            break
        out = candidate
        if len(out) > max_chars:
            break
    if len(out) > max_chars:
        out = out[: max_chars - 1].rstrip() + "…"
    return out


# Stats regexes over the training-report descriptions. The describer
# states these verbatim ("Training Accuracy 100%, Total Images 30").
_ACC_RE = re.compile(r"Training Accuracy[:\s]+(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
_IMGS_RE = re.compile(r"Total Images[:\s]+(\d+)", re.IGNORECASE)
_IOU_RE = re.compile(r"(?:Mean\s+)?IoU[:\s]+(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)

_DASH = "—"


def extract_train_accuracy(description: str) -> str:
    m = _ACC_RE.search(description or "")
    return f"{_trim_pct(m.group(1))}%" if m else _DASH


def extract_total_images(description: str) -> str:
    m = _IMGS_RE.search(description or "")
    return m.group(1) if m else _DASH


def extract_mean_iou(description: str) -> str:
    m = _IOU_RE.search(description or "")
    return f"{_trim_pct(m.group(1))}%" if m else _DASH


def _trim_pct(raw: str) -> str:
    return raw.rstrip("0").rstrip(".") if "." in raw else raw


__all__ = [
    "extract_mean_iou",
    "extract_total_images",
    "extract_train_accuracy",
    "first_sentences",
    "model_cache",
    "section_summary",
    "skeleton",
    "system_image",
    "text",
]
