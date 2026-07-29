"""Node-RED setup slide — conditional on the run having a flow analysis.

Emitted only when the run directory contains
``node_red_description.md``. The text hole carries the LLM-condensed
flow logic; the fallback is the analysis document's own overview
sentences.
"""

from __future__ import annotations

import re
from typing import Any

from deck_builder.manifest import HoleSpec, HoleValue
from deck_builder.run_bundle import RunBundle
from deck_builder.templates._shared import first_sentences, skeleton, text

TEMPLATE_ID = "nodered_setup"
SKELETON_PATH = skeleton("nodered_setup")
HOLE_SCHEMA = [
    HoleSpec(name="step_no", kind="text", label="Step number"),
    HoleSpec(
        name="setup_text", kind="text",
        label="Node-RED logic description",
        editor_config={"multiline": True, "max_chars": 2000},
    ),
]

_PLACEHOLDER = "Node-RED flow logic description."


def applies(bundle: RunBundle) -> list[None]:
    return [None] if bundle.node_red_description else []


def build(bundle: RunBundle, ctx: Any, llm_cache: dict[str, Any]) -> dict[str, HoleValue]:
    summary = (llm_cache.get("nodered_logic") or "").strip()
    if not summary:
        summary = _fallback_summary(bundle.node_red_description or "")
    return {
        "step_no": text(""),
        "setup_text": text(summary or _PLACEHOLDER),
    }


def _fallback_summary(markdown: str) -> str:
    """Leading prose of the flow analysis, markdown syntax stripped."""
    plain = re.sub(r"^#+ .*$", "", markdown, flags=re.MULTILINE)   # headings
    plain = re.sub(r"^\|.*$", "", plain, flags=re.MULTILINE)       # tables
    plain = re.sub(r"[`*_]", "", plain)                            # inline marks
    plain = re.sub(r"^-{3,}$", "", plain, flags=re.MULTILINE)      # rules
    return first_sentences(plain, max_chars=900)
