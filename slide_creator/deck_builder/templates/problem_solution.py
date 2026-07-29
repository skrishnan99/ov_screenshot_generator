"""Problem & solution slide — two text holes from the LLM copy pass.

The LLM grounds these in the user's notes first (their framing of the
problem always wins) and the system screenshot descriptions second.
"""

from __future__ import annotations

from typing import Any

from deck_builder.manifest import HoleSpec, HoleValue
from deck_builder.run_bundle import RunBundle
from deck_builder.templates._shared import skeleton, text

TEMPLATE_ID = "problem_solution"
SKELETON_PATH = skeleton("problem_solution")
HOLE_SCHEMA = [
    HoleSpec(
        name="problem", kind="text",
        label="Problem description",
        editor_config={"multiline": True, "max_chars": 800},
    ),
    HoleSpec(
        name="solution", kind="text",
        label="Solution description",
        editor_config={"multiline": True, "max_chars": 800},
    ),
]

_PROBLEM_PLACEHOLDER = "Describe the inspection challenge this recipe solves."
_SOLUTION_PLACEHOLDER = "Describe the AI pipeline that solves this challenge."


def applies(bundle: RunBundle) -> list[None]:
    """Always emit exactly one problem/solution slide."""
    return [None]


def build(bundle: RunBundle, ctx: Any, llm_cache: dict[str, Any]) -> dict[str, HoleValue]:
    return {
        "problem": text(llm_cache.get("problem") or _PROBLEM_PLACEHOLDER),
        "solution": text(llm_cache.get("solution") or _SOLUTION_PLACEHOLDER),
    }
