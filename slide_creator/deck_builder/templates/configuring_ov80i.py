"""Hardcoded 'Configuring OV80i' slide — no holes, before imaging setup."""

from __future__ import annotations

from typing import Any

from deck_builder.manifest import HoleSpec, HoleValue
from deck_builder.run_bundle import RunBundle
from deck_builder.templates._shared import skeleton

TEMPLATE_ID = "configuring_ov80i"
SKELETON_PATH = skeleton("configuring_ov80i")
HOLE_SCHEMA: list[HoleSpec] = []


def applies(bundle: RunBundle) -> list[None]:
    return [None]


def build(bundle: RunBundle, ctx: Any, llm_cache: dict[str, Any]) -> dict[str, HoleValue]:
    return {}
