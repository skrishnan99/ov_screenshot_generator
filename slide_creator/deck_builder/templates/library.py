"""Library slide — the OV capture-library screen, once per deck."""

from __future__ import annotations

from typing import Any

from deck_builder.manifest import HoleSpec, HoleValue
from deck_builder.run_bundle import RunBundle
from deck_builder.templates._shared import skeleton, system_image

TEMPLATE_ID = "library"
SKELETON_PATH = skeleton("library")
HOLE_SCHEMA = [
    HoleSpec(
        name="library_capture", kind="image",
        label="Library screen screenshot",
        match_hint=(
            "Screenshot of the OV capture library screen — a grid of "
            "past inspection captures with one featured image"
        ),
    ),
]

_STEP_ID = "library"


def applies(bundle: RunBundle) -> list[None]:
    return [None] if bundle.screenshot(_STEP_ID) else []


def build(bundle: RunBundle, ctx: Any, llm_cache: dict[str, Any]) -> dict[str, HoleValue]:
    shot = bundle.screenshot(_STEP_ID)
    assert shot is not None  # guaranteed by applies()
    return {"library_capture": system_image(shot, label="Capture library")}
