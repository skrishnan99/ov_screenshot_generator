"""ROI inspection setup slide — text + an inspection-editor screenshot (step 3).

Emitted once per deck. The screenshot is the first model's
inspection-editor capture (every ``04_roi_*`` screenshot shows ALL of
the recipe's ROIs on the part — they differ only in which model's ROI
list is selected in the side panel), so any one of them serves as the
recipe-level ROI overview.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from deck_builder.manifest import HoleSpec, HoleValue
from deck_builder.run_bundle import RunBundle
from deck_builder.templates._shared import (
    first_sentences,
    section_summary,
    skeleton,
    system_image,
    text,
)

TEMPLATE_ID = "roi_setup"
SKELETON_PATH = skeleton("roi_setup")
HOLE_SCHEMA = [
    HoleSpec(name="step_no", kind="text", label="Step number"),
    HoleSpec(
        name="setup_text", kind="text",
        label="Setup description",
        editor_config={"multiline": True, "max_chars": 600},
    ),
    HoleSpec(
        name="setup_screenshot", kind="image",
        label="ROI screen screenshot",
        match_hint=(
            "The OV inspection-setup (ROI editor) screen: the part "
            "with labeled ROI boxes drawn across it"
        ),
    ),
]

_TEXT_PLACEHOLDER = "Regions of interest defined for inspection."


def _first_roi_screenshot(bundle: RunBundle) -> Optional[Path]:
    for model in bundle.models:
        if model.roi_screenshot is not None:
            return model.roi_screenshot
    return None


def applies(bundle: RunBundle) -> list[None]:
    """Emit when at least one inspection-editor screenshot exists."""
    return [None] if _first_roi_screenshot(bundle) else []


def build(bundle: RunBundle, ctx: Any, llm_cache: dict[str, Any]) -> dict[str, HoleValue]:
    shot = _first_roi_screenshot(bundle)
    assert shot is not None  # guaranteed by applies()
    summary = (
        section_summary(llm_cache, "roi")
        or first_sentences(bundle.description_for(shot))
        or _TEXT_PLACEHOLDER
    )
    return {
        "step_no": text(""),
        "setup_text": text(summary),
        "setup_screenshot": system_image(shot, label="Inspection ROI setup"),
    }
