"""LLM binding: writes text token values.

One structured-output call fills every LLM text token in the deck.
Image-slot matching lives in deck/matcher.py.
"""

from __future__ import annotations

from core.llm import complete

STYLE = """You are a sales engineer at Overview writing copy for a customer-facing test-report
slide deck about a test conducted on the customer's plant with an Overview AI vision camera.
Audience: quality engineers at the plant. Rules for every field:
- Crisp, clear copy sized for a slide — respect each field's stated length.
- No technical internals: no camera configuration minutiae (exposure/gain/gamma), no IP
  addresses, no node or variable names, no UI/JSON field names, no UUIDs.
- Numbers only when they tell the story: class names, training example counts, accuracies,
  region counts.
- Use only facts present in the material — never invent metrics, part names, or outcomes.
- The engineer's notes, when present, are authoritative — where they conflict with anything
  inferred from the material, the notes win."""


def _material(pool: dict) -> str:
    desc_text = "\n\n".join(
        f"### {name}\n{text}" for name, text in pool["descriptions"].items()
    )
    notes = pool.get("engineer_notes") or "(none provided)"
    facts = pool.get("facts") or []
    facts_text = (
        "\n".join(
            f"- [{f.get('source', '?')}] {f.get('subject', '?')} | "
            f"{f.get('property', '?')} = {f.get('value', '?')}"
            for f in facts
        )
        or "(none extracted)"
    )
    return (
        f"=== ENGINEER'S SITE-VISIT NOTES (authoritative for intent and context) ===\n"
        f"{notes}\n\n"
        f"=== EXTRACTED FACTS (authoritative for on-screen values — copy values "
        f"VERBATIM from here; where facts conflict, prefer the one from the "
        f"later/loaded screen; the prose below is context, not a source of "
        f"numbers when a fact exists) ===\n{facts_text}\n\n"
        f"=== SCREENSHOT DESCRIPTIONS ===\n{desc_text}\n\n"
        f"=== NODE-RED IO LOGIC SUMMARY ===\n{pool['node_red'] or '(none)'}"
    )


def bind_text(token_specs: dict[str, str], pool: dict) -> dict[str, str]:
    """token_specs: {qualified_token_name: guidance} -> {name: value}."""
    if not token_specs:
        return {}
    schema = {
        "type": "object",
        "properties": {
            name: {"type": "string", "description": guidance}
            for name, guidance in token_specs.items()
        },
        "required": list(token_specs),
        "additionalProperties": False,
    }
    prompt = (
        f"{STYLE}\n\nFill every field of the response schema from the material below. "
        f"Field descriptions state what each slide slot needs.\n\n{_material(pool)}"
    )
    return complete(prompt, schema=schema, max_tokens=4000)


