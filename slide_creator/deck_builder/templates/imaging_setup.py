"""Imaging setup slide — text + the imaging-setup screenshot (step 1)."""

from __future__ import annotations

from typing import Any

from deck_builder.manifest import HoleSpec, HoleValue
from deck_builder.run_bundle import RunBundle
from deck_builder.templates._shared import (
    first_sentences,
    section_summary,
    skeleton,
    system_image,
    text,
)

TEMPLATE_ID = "imaging_setup"
SKELETON_PATH = skeleton("imaging_setup")
HOLE_SCHEMA = [
    HoleSpec(name="step_no", kind="text", label="Step number"),
    HoleSpec(
        name="setup_text", kind="text",
        label="Imaging setup note",
        editor_config={"multiline": True, "max_chars": 600},
    ),
    HoleSpec(
        name="setup_screenshot", kind="image",
        label="Imaging screen screenshot",
        match_hint=(
            "The OV imaging-setup screen: camera settings panel with "
            "exposure/gain/gamma sliders and trigger settings"
        ),
    ),
]

_STEP_ID = "imaging_setup"
_TEXT_PLACEHOLDER = "Standard imaging configuration."


def applies(bundle: RunBundle) -> list[None]:
    """Emit when the run captured the imaging screen (the image hole
    cannot be left empty)."""
    return [None] if bundle.screenshot(_STEP_ID) else []


def build(bundle: RunBundle, ctx: Any, llm_cache: dict[str, Any]) -> dict[str, HoleValue]:
    shot = bundle.screenshot(_STEP_ID)
    assert shot is not None  # guaranteed by applies()
    summary = (
        section_summary(llm_cache, "imaging")
        or first_sentences(bundle.description_for(shot))
        or _TEXT_PLACEHOLDER
    )
    return {
        "step_no": text(""),
        "setup_text": text(summary),
        "setup_screenshot": system_image(shot, label="Imaging setup screen"),
    }
