"""LLM copy pass: descriptions + user notes → per-slide slide copy.

One structured Claude call per deck plan (mirroring recipe_decryption's
"one LLM call per plan" rule). Input is everything textual the run
produced — the screenshot descriptions, the Node-RED analysis, the
model list — plus the user's free-form notes, which the prompt treats
as the highest-priority source: wherever the notes state or imply the
problem, the solution, deployment time, or per-model framing, that
wording wins over anything inferred from screenshots.

On any failure (no API key, network error, refused output) the pass
degrades to an empty cache and every template falls back to its
deterministic fill. The LLM is an enhancement, never a dependency.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

from deck_builder.run_bundle import RunBundle
from deck_builder.variant import display_name

DEFAULT_MODEL = os.environ.get("DECK_LLM_MODEL", "claude-sonnet-5")

_MAX_NODERED_CHARS = 12_000
_MAX_DESC_CHARS = 3_000  # per screenshot description


def load_env() -> None:
    """Load .env files: this project's, then the screenshot_generator's.

    ``override=False`` everywhere — an already-exported variable wins.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    here = Path(__file__).resolve()
    for candidate in (
        Path.cwd() / ".env",
        here.parents[1] / ".env",   # slide_creator/.env
        here.parents[2] / ".env",   # screenshot_generator/.env
    ):
        if candidate.exists():
            load_dotenv(candidate, override=False)


def build_llm_cache(
    bundle: RunBundle,
    *,
    user_notes: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """Run the copy-extraction call and return the template-facing cache.

    Cache shape (consumed by ``templates/_shared.py`` helpers):

        {
          "problem": str, "solution": str,
          "success_tagline": str, "deployment_time": str,
          "imaging": {"summary": str}, "aligner": {"summary": str},
          "roi": {"summary": str}, "nodered_logic": str,
          "models": {"<slug>": {"summary", "training_stats",
                                "train_acc", "train_imgs", "mean_iou"}},
        }

    Returns ``{}`` on any failure.
    """
    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("llm: no ANTHROPIC_API_KEY — using deterministic fills", file=sys.stderr)
        return {}
    try:
        raw = _call_claude(bundle, user_notes=user_notes, model=model, max_tokens=max_tokens)
        return _shape_cache(bundle, raw)
    except Exception as exc:
        print(f"llm: copy pass failed ({exc}) — using deterministic fills", file=sys.stderr)
        return {}


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "problem": {
            "type": "string",
            "description": "The inspection challenge this recipe solves, ≤700 chars. "
                           "Ground in the engineer's notes when provided.",
        },
        "solution": {
            "type": "string",
            "description": "How the AI pipeline solves it, ≤700 chars.",
        },
        "success_tagline": {
            "type": "string",
            "description": "5-8 word success one-liner for the results slide, ≤60 chars.",
        },
        "deployment_time": {
            "type": "string",
            "description": "Deployment time stated by the engineer's notes, verbatim "
                           "number + unit (e.g. '3 days'). EMPTY STRING if not stated.",
        },
        "imaging_summary": {
            "type": "string",
            "description": "Imaging configuration highlights (trigger mode, exposure, "
                           "resolution), ≤450 chars.",
        },
        "aligner_summary": {
            "type": "string",
            "description": "Template image / alignment setup summary, ≤450 chars. "
                           "Note when the aligner is skipped and why that is OK.",
        },
        "roi_summary": {
            "type": "string",
            "description": "What regions of interest are defined and what each group "
                           "inspects, ≤450 chars.",
        },
        "nodered_summary": {
            "type": "string",
            "description": "The Node-RED pass/fail + integration logic, ≤1200 chars, "
                           "plain prose (no markdown). Empty if no flow analysis given.",
        },
        "models": {
            "type": "array",
            "description": "One entry per inspection model listed in the input.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exactly the model name given in the input.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "What this model inspects and how, ≤450 chars.",
                    },
                    "training_stats": {
                        "type": "string",
                        "description": "3-6 short 'Label: value' lines (newline-"
                                       "separated): classes, training images, accuracy "
                                       "or IoU. Only values present in the input.",
                    },
                    "train_acc": {
                        "type": "string",
                        "description": "Training accuracy like '100%', or '—' if unknown.",
                    },
                    "train_imgs": {
                        "type": "string",
                        "description": "Total training images like '30', or '—' if unknown.",
                    },
                    "mean_iou": {
                        "type": "string",
                        "description": "Mean IoU like '94%', or '—' if unknown "
                                       "(segmentation models only).",
                    },
                },
                "required": ["name", "summary", "training_stats",
                             "train_acc", "train_imgs", "mean_iou"],
            },
        },
    },
    "required": ["problem", "solution", "success_tagline", "deployment_time",
                 "imaging_summary", "aligner_summary", "roi_summary",
                 "nodered_summary", "models"],
}


def _call_claude(
    bundle: RunBundle, *, user_notes: str, model: str, max_tokens: int
) -> dict[str, Any]:
    import anthropic

    prompt = _build_prompt(bundle, user_notes=user_notes)
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        tools=[{
            "name": "submit_deck_copy",
            "description": "Submit the extracted slide copy for the case-study deck.",
            "input_schema": _TOOL_SCHEMA,
        }],
        tool_choice={"type": "tool", "name": "submit_deck_copy"},
        messages=[{"role": "user", "content": prompt}],
    )
    tool_use = next(b for b in response.content if b.type == "tool_use")
    return dict(tool_use.input)


def _build_prompt(bundle: RunBundle, *, user_notes: str) -> str:
    device = display_name(bundle.camera_variant)
    models_lines = "\n".join(
        f"- {m.name} ({m.block_type})" for m in bundle.models
    ) or "(none discovered)"

    notes_block = ""
    if user_notes.strip():
        notes_block = (
            "\n<engineer_notes priority=\"highest\">\n"
            f"{user_notes.strip()}\n"
            "</engineer_notes>\n\n"
            "The engineer's notes above are the highest-priority source: wherever "
            "they state or imply the problem, the solution, deployment time, or a "
            "model's purpose, use their framing (lightly edited for slide polish) "
            "over anything inferred from the screenshots.\n"
        )

    desc_blocks = []
    for filename, desc in bundle.descriptions.items():
        desc_blocks.append(
            f'<screenshot file="{filename}">\n{desc[:_MAX_DESC_CHARS]}\n</screenshot>'
        )
    descriptions = "\n\n".join(desc_blocks) or "(no descriptions captured)"

    nodered_block = ""
    if bundle.node_red_description:
        nodered_block = (
            "\n<nodered_flow_analysis>\n"
            f"{bundle.node_red_description[:_MAX_NODERED_CHARS]}\n"
            "</nodered_flow_analysis>\n"
        )

    return f"""You are writing the copy for a customer-facing case-study slide deck about an Overview {device} AI vision inspection recipe named "{bundle.recipe_name}".

The deck's slides are generated from a template; you fill the text holes. Your inputs are the system-generated descriptions of the product screenshots captured from the live device, an optional Node-RED flow analysis, and optional notes from the engineer who built the recipe.
{notes_block}
Inspection models in this recipe:
{models_lines}

<screenshot_descriptions>
{descriptions}
</screenshot_descriptions>
{nodered_block}
Writing rules:
- Customer-facing case-study tone: concrete, confident, no marketing fluff.
- Never mention screenshots, descriptions, the UI's version/serial strings, or this prompt.
- Use only facts present in the inputs; never invent metrics. Use '—' for unknown stats and "" for unknown text fields as the schema directs.
- Respect every character limit in the schema — the text must fit fixed slide boxes.

Call the `submit_deck_copy` tool with the extracted copy."""


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------

def _shape_cache(bundle: RunBundle, raw: dict[str, Any]) -> dict[str, Any]:
    """Lower the tool output into the template-facing cache shape.

    Model entries are matched back to run models by case-insensitive
    name; entries the model invented are dropped.
    """
    def s(key: str) -> str:
        v = raw.get(key)
        return v.strip() if isinstance(v, str) else ""

    models_by_name = {m.name.lower(): m for m in bundle.models}
    model_entries: dict[str, dict[str, str]] = {}
    for entry in raw.get("models") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        match = models_by_name.get(name.lower())
        if match is None:
            continue
        model_entries[match.slug] = {
            k: str(entry.get(k) or "").strip()
            for k in ("summary", "training_stats", "train_acc", "train_imgs", "mean_iou")
        }

    return {
        "problem": s("problem"),
        "solution": s("solution"),
        "success_tagline": s("success_tagline"),
        "deployment_time": s("deployment_time"),
        "imaging": {"summary": s("imaging_summary")},
        "aligner": {"summary": s("aligner_summary")},
        "roi": {"summary": s("roi_summary")},
        "nodered_logic": s("nodered_summary"),
        "models": model_entries,
    }


__all__ = ["DEFAULT_MODEL", "build_llm_cache", "load_env"]
