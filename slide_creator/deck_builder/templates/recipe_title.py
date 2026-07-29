"""Recipe title slide — one text hole for the recipe name."""

from __future__ import annotations

from typing import Any

from deck_builder.manifest import HoleSpec, HoleValue
from deck_builder.run_bundle import RunBundle
from deck_builder.templates._shared import skeleton, text

TEMPLATE_ID = "recipe_title"
SKELETON_PATH = skeleton("recipe_title")
HOLE_SCHEMA = [
    HoleSpec(name="recipe_title", kind="text", label="Recipe title"),
]


def applies(bundle: RunBundle) -> list[None]:
    """Always emit exactly one title slide."""
    return [None]


def build(bundle: RunBundle, ctx: Any, llm_cache: dict[str, Any]) -> dict[str, HoleValue]:
    return {"recipe_title": text(bundle.recipe_name)}
