"""Aligner setup slide — text + the template-image/alignment screenshot (step 2)."""

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

TEMPLATE_ID = "aligner_setup"
SKELETON_PATH = skeleton("aligner_setup")
HOLE_SCHEMA = [
    HoleSpec(name="step_no", kind="text", label="Step number"),
    HoleSpec(
        name="setup_text", kind="text",
        label="Setup description",
        editor_config={"multiline": True, "max_chars": 600},
    ),
    HoleSpec(
        name="setup_screenshot", kind="image",
        label="Aligner screen screenshot",
        match_hint=(
            "The OV template-image-and-alignment screen: template "
            "capture with a search-area overlay"
        ),
    ),
]

_STEP_ID = "template_image"
_TEXT_PLACEHOLDER = "Template image and alignment setup."


def applies(bundle: RunBundle) -> list[None]:
    """Emit when the run captured the template/alignment screen."""
    return [None] if bundle.screenshot(_STEP_ID) else []


def build(bundle: RunBundle, ctx: Any, llm_cache: dict[str, Any]) -> dict[str, HoleValue]:
    shot = bundle.screenshot(_STEP_ID)
    assert shot is not None  # guaranteed by applies()
    summary = (
        section_summary(llm_cache, "aligner")
        or first_sentences(bundle.description_for(shot))
        or _TEXT_PLACEHOLDER
    )
    return {
        "step_no": text(""),
        "setup_text": text(summary),
        "setup_screenshot": system_image(shot, label="Template image and alignment"),
    }
