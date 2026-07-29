"""Loader for a screenshot_generator run directory.

A run directory (``runs/<timestamp>/``) is the system-generated input
to deck building. It contains:

* ``manifest.json`` — ordered step records: which screens were
  captured, per-model screenshot groupings, camera variant, recipe name.
* ``descriptions.json`` — screenshot filename → rich prose description
  (system-generated text context).
* ``node_red_description.md`` — optional Node-RED flow analysis.
* ``*.png`` — the screenshots themselves.

``RunBundle`` is the read-only, pre-parsed view of all of that. It
plays the role ``LoadedRecipe`` plays in ``recipe_decryption``: the
single object templates and the planner read from.

Model discovery
---------------

The run manifest lists inspection models in three steps, each pairing
a model list with a screenshot list by index:

* ``inspection_rois``   — ``models`` + ``screenshots`` (ROI editor per model)
* ``training_reports``  — ``report_models`` + ``screenshots``
* ``model_settings``    — ``settings_models`` + ``screenshots``

Model strings look like ``"Horn Quality (Classification)"``. We parse
them into ``ModelInfo(name, block_type)`` and merge the three views by
model name, preserving ``inspection_rois`` order (that is recipe
pipeline order, which drives deck order).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from deck_builder.errors import RunBundleError

_MODEL_RE = re.compile(r"^\s*(?P<name>.*?)\s*\(\s*(?P<type>[A-Za-z]+)\s*\)\s*$")

# Block types the deck knows how to build slides for.
TRAINABLE_BLOCK_TYPES = ("classification", "segmentation")


@dataclass(frozen=True)
class ModelInfo:
    """One inspection model discovered from the run manifest."""

    name: str                      # "Horn Quality"
    block_type: str                # "classification" | "segmentation"
    roi_screenshot: Optional[Path] = None       # inspection editor, this model selected
    report_screenshot: Optional[Path] = None    # training report modal
    settings_screenshot: Optional[Path] = None  # per-model settings modal

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")


@dataclass(frozen=True)
class RunBundle:
    """Pre-parsed, validated view of one screenshot_generator run."""

    run_dir: Path
    run_id: str
    manifest: dict[str, Any]
    descriptions: dict[str, str]
    node_red_description: Optional[str]
    camera_variant: Optional[str]
    recipe_name: str
    # step id -> screenshot path (single-screenshot steps, successful only)
    step_screenshots: dict[str, Path] = field(default_factory=dict)
    models: list[ModelInfo] = field(default_factory=list)

    # -- convenience accessors -------------------------------------------

    def screenshot(self, step_id: str) -> Optional[Path]:
        """Path of a successful single-screenshot step, or None."""
        return self.step_screenshots.get(step_id)

    def description_for(self, path: Optional[Path]) -> str:
        """The system-generated description of a screenshot ('' if none)."""
        if path is None:
            return ""
        return self.descriptions.get(path.name, "")

    def models_of_type(self, block_type: str) -> list[ModelInfo]:
        return [m for m in self.models if m.block_type == block_type]

    def all_screenshots(self) -> list[Path]:
        """Every screenshot referenced by the run, deduplicated, in step order."""
        seen: dict[Path, None] = {}
        for p in self.step_screenshots.values():
            seen.setdefault(p)
        for m in self.models:
            for p in (m.roi_screenshot, m.report_screenshot, m.settings_screenshot):
                if p is not None:
                    seen.setdefault(p)
        return list(seen)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_run_bundle(run_dir: str | Path) -> RunBundle:
    """Load and validate a run directory into a ``RunBundle``.

    Raises ``RunBundleError`` for a missing/undecodable manifest. Missing
    screenshots or descriptions degrade to absent entries — a partially
    failed capture run still yields a (smaller) deck.
    """
    run_dir = Path(run_dir).resolve()
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise RunBundleError(f"No manifest.json in run directory: {run_dir}")
    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception as exc:
        raise RunBundleError(f"Cannot parse {manifest_path}: {exc}") from exc

    descriptions = _load_descriptions(run_dir, manifest)
    node_red = _load_node_red(run_dir)

    steps = [s for s in manifest.get("steps", []) if isinstance(s, dict)]
    ok_steps = [s for s in steps if s.get("status") == "success"]

    step_screenshots: dict[str, Path] = {}
    for step in ok_steps:
        shot = step.get("screenshot")
        if isinstance(shot, str) and shot:
            p = run_dir / shot
            if p.exists():
                step_screenshots[str(step.get("id"))] = p

    models = _discover_models(run_dir, ok_steps)
    recipe_name = _recipe_name(manifest, ok_steps)

    return RunBundle(
        run_dir=run_dir,
        run_id=run_dir.name,
        manifest=manifest,
        descriptions=descriptions,
        node_red_description=node_red,
        camera_variant=manifest.get("variant") or None,
        recipe_name=recipe_name,
        step_screenshots=step_screenshots,
        models=models,
    )


def _load_descriptions(run_dir: Path, manifest: dict) -> dict[str, str]:
    name = manifest.get("descriptions") or "descriptions.json"
    path = run_dir / name
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _load_node_red(run_dir: Path) -> Optional[str]:
    path = run_dir / "node_red_description.md"
    if not path.exists():
        return None
    try:
        text = path.read_text().strip()
    except Exception:
        return None
    return text or None


def _recipe_name(manifest: dict, ok_steps: list[dict]) -> str:
    for step in ok_steps:
        matched = step.get("matched_recipe")
        if isinstance(matched, str) and matched.strip():
            return matched.strip()
    raw = manifest.get("recipe_input")
    return raw.strip() if isinstance(raw, str) and raw.strip() else "Untitled Recipe"


def parse_model_label(label: str) -> Optional[tuple[str, str]]:
    """``"Horn Quality (Classification)"`` → ``("Horn Quality", "classification")``.

    Returns None for labels that don't carry a recognizable block type.
    """
    m = _MODEL_RE.match(label or "")
    if not m:
        return None
    block_type = m.group("type").lower()
    if block_type not in TRAINABLE_BLOCK_TYPES:
        return None
    name = m.group("name").strip()
    return (name, block_type) if name else None


def _discover_models(run_dir: Path, ok_steps: list[dict]) -> list[ModelInfo]:
    """Merge the three per-model step views into ordered ``ModelInfo``s."""
    steps_by_id = {str(s.get("id")): s for s in ok_steps}

    def paired(step_id: str, models_key: str) -> dict[str, Path]:
        """model label -> screenshot path, paired by index, existing files only."""
        step = steps_by_id.get(step_id) or {}
        labels = step.get(models_key) or []
        shots = step.get("screenshots") or []
        out: dict[str, Path] = {}
        for label, shot in zip(labels, shots):
            if not (isinstance(label, str) and isinstance(shot, str)):
                continue
            p = run_dir / shot
            if p.exists():
                out[label] = p
        return out

    roi_by_label = paired("inspection_rois", "models")
    report_by_label = paired("training_reports", "report_models")
    settings_by_label = paired("model_settings", "settings_models")

    # Union of models, in inspection_rois order first (pipeline order),
    # then any extras from the other two steps. Deduplicated on the
    # PARSED (name, block_type) key — the three manifest steps case the
    # type differently ("(Classification)" vs "(classification)"), so
    # raw labels cannot be compared directly.
    ordered: dict[tuple[str, str], str] = {}
    for label in (*roi_by_label, *report_by_label, *settings_by_label):
        parsed = parse_model_label(label)
        if parsed is not None:
            ordered.setdefault(parsed, label)

    models: list[ModelInfo] = []
    for (name, block_type), label in ordered.items():

        def lookup(table: dict[str, Path]) -> Optional[Path]:
            # Exact label match first, then match by parsed name (the
            # three steps sometimes case the type differently, e.g.
            # "(Classification)" vs "(classification)").
            if label in table:
                return table[label]
            for other_label, path in table.items():
                other = parse_model_label(other_label)
                if other and other[0] == name and other[1] == block_type:
                    return path
            return None

        models.append(ModelInfo(
            name=name,
            block_type=block_type,
            roi_screenshot=lookup(roi_by_label),
            report_screenshot=lookup(report_by_label),
            settings_screenshot=lookup(settings_by_label),
        ))
    return models


__all__ = [
    "ModelInfo",
    "RunBundle",
    "TRAINABLE_BLOCK_TYPES",
    "load_run_bundle",
    "parse_model_label",
]
