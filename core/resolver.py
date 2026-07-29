"""LLM recipe resolution: approximate user input -> exact on-screen recipe name.

Used on every run (agent runs resolve implicitly; replay calls this
explicitly), so recipe matching stays reliable even when the recipe set has
changed since the trace was recorded. Returns matched/ambiguous/not_found;
anything but a confident match makes the caller fall back to the full agent.
"""

from __future__ import annotations

from core import llm
from core.llm import LLMRefusal, complete

SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["matched", "ambiguous", "not_found"]},
        "name": {
            "type": "string",
            "description": "The exact recipe name as displayed in the UI, verbatim. Empty unless status is matched.",
        },
        "reason": {"type": "string"},
    },
    "required": ["status", "name", "reason"],
    "additionalProperties": False,
}

PROMPT = """Below is a snapshot of an industrial camera's web UI page that lists recipes.

The user asked for the recipe (approximate, may not match exactly): "{requested}"

Decide which recipe name displayed in the UI the user means. Match on meaning:
- Decorative suffixes the UI adds, like "(imported)", do not make a recipe different.
- Qualifiers that change meaning, like "_ zero escapes" or version markers, DO make it a \
different recipe.
- If exactly one recipe is clearly the intended one, return status "matched" with its exact \
on-screen name verbatim (copy it character-for-character from the snapshot).
- If two or more are equally plausible, return "ambiguous" and name them in reason.
- If nothing is a confident match, return "not_found" and list what you saw in reason.

UI snapshot:
{snapshot}"""


MODELS_SCHEMA = {
    "type": "object",
    "properties": {
        "models": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The model's display name to use in a filename.",
                    },
                    "entry_text": {
                        "type": "string",
                        "description": "The verbatim text of the clickable element for this model, copied exactly from the snapshot.",
                    },
                    "model_type": {
                        "type": "string",
                        "enum": [
                            "classification",
                            "segmentation",
                            "ocr",
                            "unsupervised",
                            "measurement",
                            "other",
                        ],
                        "description": "The model's type as shown in the UI.",
                    },
                },
                "required": ["name", "entry_text", "model_type"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["models", "notes"],
    "additionalProperties": False,
}

MODELS_PROMPT = """Below is a snapshot of the Inspection Setup page of an industrial camera's web UI.
It has a "Models" section listing the AI models configured for this recipe (there may be none).

List every model shown in the Models section:
- "entry_text": the verbatim text of that model's clickable element, copied exactly from the
  snapshot (e.g. a button labeled with the model).
- "name": the model's name ONLY, WITHOUT its type — the type goes in "model_type". If the
  UI shows "Horn Quality" as a Classification model, name is "Horn Quality", never
  "Horn Quality Classification".

Do NOT include navigation tabs (e.g. "Segmentation Block"), the "Add" button, or anything
outside the Models section. Return an empty list if no models are configured.

UI snapshot:
{snapshot}"""


def list_models(snapshot: str) -> list[dict]:
    # Sonnet: straightforward structured extraction of visible rows.
    try:
        return complete(
            MODELS_PROMPT.format(snapshot=snapshot),
            schema=MODELS_SCHEMA,
            max_tokens=2000,
            model=llm.SONNET,
        )["models"]
    except LLMRefusal:
        return []


REPORTS_SCHEMA = {
    "type": "object",
    "properties": {
        "models": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The model's name as shown."},
                    "type": {
                        "type": "string",
                        "description": "The model's type as shown (e.g. Classification, Segmentation).",
                    },
                    "entry_text": {
                        "type": "string",
                        "description": "Verbatim text of the clickable element that opens this model's training report (e.g. a View button/link), copied exactly from the snapshot.",
                    },
                },
                "required": ["name", "type", "entry_text"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["models", "notes"],
    "additionalProperties": False,
}

REPORTS_PROMPT = """Below is a snapshot of the Train Models page of an industrial camera's web UI.
It lists the recipe's AI models with training information (e.g. "Last trained ...").

List every model that has an AVAILABLE training report — i.e. a clickable, non-disabled
"View" control (typically below "Last trained") that opens the training report. For each:
- "name": the model's name ONLY, WITHOUT its type (the type goes in "type").
- "type": the model's type as displayed (e.g. Classification, Segmentation).
- "entry_text": the verbatim text of the clickable element that opens the report, copied
  exactly from the snapshot.

Skip models with no report available (no View control, or it is disabled). Return an
empty list if none have reports.

UI snapshot:
{snapshot}"""


def list_training_reports(snapshot: str) -> list[dict]:
    # Sonnet: straightforward structured extraction of visible rows.
    try:
        return complete(
            REPORTS_PROMPT.format(snapshot=snapshot),
            schema=REPORTS_SCHEMA,
            max_tokens=2000,
            model=llm.SONNET,
        )["models"]
    except LLMRefusal:
        return []


SETTINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "models": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The model's name as shown."},
                    "type": {
                        "type": "string",
                        "description": "The model's type as shown (e.g. Classification, Segmentation).",
                    },
                    "settings_ref": {
                        "type": "integer",
                        "description": "The [ref] number, from this snapshot, of the element to click to open this model's settings.",
                    },
                },
                "required": ["name", "type", "settings_ref"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["models", "notes"],
    "additionalProperties": False,
}

SETTINGS_PROMPT = """Below is a snapshot of the Train Models page of an industrial camera's web UI.
It lists the recipe's AI models. Each model has a settings control (often a gear icon —
icon buttons appear in the snapshot as elements with empty text, or as spans with
role=img and the icon name such as "setting", inside/near the model's row).

List every model that has a clickable settings control, with:
- "name": the model's name ONLY, WITHOUT its type (the type goes in "type").
- "type": the model's type as displayed (e.g. Classification, Segmentation).
- "settings_ref": the [ref] number of the element to click to open that model's settings.
  Use the model's row context (the "in: ..." text) to pick the control belonging to the
  right model. Do not confuse settings with delete or other row actions.

Return an empty list if no settings controls are identifiable.

UI snapshot:
{snapshot}"""


def list_model_settings(snapshot: str) -> list[dict]:
    try:
        return complete(
            SETTINGS_PROMPT.format(snapshot=snapshot), schema=SETTINGS_SCHEMA, max_tokens=2000
        )["models"]
    except LLMRefusal:
        return []


def resolve_recipe(requested: str, snapshot: str) -> dict:
    try:
        return complete(
            PROMPT.format(requested=requested, snapshot=snapshot),
            schema=SCHEMA,
            max_tokens=2000,
        )
    except LLMRefusal:
        return {"status": "not_found", "name": "", "reason": "model refused"}
