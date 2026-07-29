"""Slot-to-asset image matching.

Three composable stages, each recorded in a match report:

  A. Deterministic filtering (in the planner): spec selectors NARROW the
     candidate set instead of picking the first hit; a single survivor is
     used without any LLM.
  B. Global assignment: ONE structured call sees every unresolved slot (with
     its layered expectation text and geometry) and every candidate asset
     card (full-length descriptions), and assigns them jointly. "No suitable
     asset" is a legal answer, every pick carries a reason, and duplicate
     assignments are discouraged in-prompt and flagged in the report.
  C. Optional vision verification: each matched (slot, image) pair is judged
     by a vision model against the slot's expectation; failing slots get one
     bounded re-assignment round with the rejected assets excluded.
"""

from __future__ import annotations

import base64
import io
import json

import anthropic

from deck.content import asset_path

MODEL = "claude-opus-5"
CATALOG_DESC_CHARS = 800
VERIFY_MAX_PX = 1568


def _trim(text: str, limit: int = CATALOG_DESC_CHARS) -> str:
    """Sentence-boundary truncation — mid-sentence cuts lose exactly the
    qualifiers ("...for the SEGMENTATION model") that disambiguate assets."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    dot = cut.rfind(". ")
    return (cut[: dot + 1] if dot > limit // 2 else cut) + " [...]"


def build_catalog(pool: dict) -> list[dict]:
    """Every matchable image asset with its description and pixel size."""
    catalog = []
    for a in pool["assets"]:
        if a.get("kind") not in ("screenshot", "image") or a.get("role") != "deliverable":
            continue
        p = asset_path(pool, a)
        if p is None:
            continue
        card = {**a, "abs_path": str(p)}
        card["description"] = pool["descriptions"].get(a.get("description_key", ""), "")
        try:
            from PIL import Image

            with Image.open(p) as im:
                card["px"] = im.size
        except Exception:
            pass
        catalog.append(card)
    return catalog


def _catalog_lines(catalog: list[dict], only: set[int] | None = None) -> str:
    lines = []
    for i, a in enumerate(catalog):
        if only is not None and i not in only:
            continue
        dims = f" {a['px'][0]}x{a['px'][1]}px" if a.get("px") else ""
        head = (
            f"[{i}] path={a['path']} step={a.get('step')} "
            f"item={a.get('item', '')} source={a.get('source', 'extractor')}{dims}"
        )
        lines.append(f"{head}\n    {_trim(a.get('description', '')) or '(no description)'}")
    return "\n".join(lines)


def _slot_lines(slots: list[dict]) -> str:
    lines = []
    for s in slots:
        allowed = (
            "any catalog entry"
            if s.get("candidates") is None
            else "ONLY " + ", ".join(f"[{i}]" for i in s["candidates"])
        )
        excluded = s.get("exclude") or set()
        if excluded:
            allowed += "; already rejected: " + ", ".join(f"[{i}]" for i in sorted(excluded))
        geom = ""
        if s.get("width_in"):
            geom = f" | slot size {s['width_in']}x{s['height_in']}in"
        lines.append(
            f'- slot "{s["id"]}" on slide "{s["slide"]}" '
            f'(headline: "{s.get("slide_title") or "?"}"{geom})\n'
            f"  needs: {s['expects']}\n"
            f"  allowed assets: {allowed}"
        )
    return "\n".join(lines)


ASSIGN_PROMPT = """You are placing screenshots into a customer-facing test-report slide deck
about an inspection recipe configured on an Overview AI vision camera. Below are the deck's
open image slots (each says what its slide needs to show) and a catalog of available image
assets with thorough descriptions.

Assign the best catalog asset to every slot, considering ALL slots together:
- A slot may only use an asset from its "allowed assets" list (never a rejected one).
- If NO allowed asset genuinely shows what the slot needs, answer -1 — a slide with a
  missing image is better than a slide with a wrong image. Do not force a bad fit.
- Do not assign the same asset to two slots unless both slots genuinely call for the same
  screen and no distinct alternative exists.
- Slots on model-specific slides (the slot text names the model) must get the asset for
  THAT model — check the descriptions for the model's name and type.
- Prefer assets whose orientation suits the slot's size; this is a tiebreaker, not a rule.
- Give a short reason for every answer, naming the evidence in the asset's description.

{notes_block}=== OPEN SLOTS ===
{slots}

=== ASSET CATALOG ===
{catalog}"""


def assign_images(slots: list[dict], catalog: list[dict], pool: dict) -> dict:
    """One structured call: {slot_id: {"asset": int (-1 = none), "reason": str}}."""
    if not slots:
        return {}
    schema = {
        "type": "object",
        "properties": {
            s["id"]: {
                "type": "object",
                "properties": {
                    "asset": {
                        "type": "integer",
                        "description": "Catalog index of the chosen asset, or -1 for none.",
                    },
                    "reason": {"type": "string"},
                },
                "required": ["asset", "reason"],
                "additionalProperties": False,
            }
            for s in slots
        },
        "required": [s["id"] for s in slots],
        "additionalProperties": False,
    }
    notes = pool.get("engineer_notes")
    notes_block = (
        f"=== ENGINEER'S SITE-VISIT NOTES (authoritative where relevant) ===\n{notes}\n\n"
        if notes
        else ""
    )
    prompt = ASSIGN_PROMPT.format(
        notes_block=notes_block, slots=_slot_lines(slots), catalog=_catalog_lines(catalog)
    )
    client = anthropic.Anthropic()
    with client.messages.stream(
        model=MODEL,
        max_tokens=4000,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "refusal":
        raise RuntimeError("image assignment refused by model")
    return json.loads("".join(b.text for b in response.content if b.type == "text"))


VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "match": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["match", "reason"],
    "additionalProperties": False,
}

VERIFY_PROMPT = """This image was placed into a slide of a customer-facing report deck about
an Overview AI vision camera inspection recipe.

The slide's headline: "{title}"
The slot it fills expects: {expects}

Judge whether the image actually shows what the slot expects. Mismatched model names or
types, the wrong screen of the camera UI, or an obviously unloaded/blank view all mean
match = false. Cropping, resolution, and cosmetic differences do not. Answer with match
and a one-sentence reason."""


def _image_block(path: str) -> dict:
    """Downscaled PNG content block — full-page captures can be huge."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        scale = VERIFY_MAX_PX / max(im.size)
        if scale < 1:
            im = im.resize((int(im.width * scale), int(im.height * scale)))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(buf.getvalue()).decode(),
        },
    }


def verify_pair(image_path: str, slot: dict) -> dict:
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        output_config={"format": {"type": "json_schema", "schema": VERIFY_SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": [
                    _image_block(image_path),
                    {
                        "type": "text",
                        "text": VERIFY_PROMPT.format(
                            title=slot.get("slide_title") or "?", expects=slot["expects"]
                        ),
                    },
                ],
            }
        ],
    )
    if response.stop_reason == "refusal":
        return {"match": True, "reason": "verification refused; keeping assignment"}
    return json.loads("".join(b.text for b in response.content if b.type == "text"))


def match_images(
    slots: list[dict], catalog: list[dict], pool: dict, verify: bool = False, log=print
) -> tuple[dict, list[dict]]:
    """Stage B (+ optional C) over the unresolved slots.

    slots: [{id, slide, slide_title, expects, candidates (indices or None),
             width_in, height_in, required}]
    Returns ({slot_id: catalog index | None}, report entries).
    """
    choices: dict[str, int | None] = {}
    report: list[dict] = []
    if not slots:
        return choices, report
    for s in slots:
        s.setdefault("exclude", set())

    picks = assign_images(slots, catalog, pool)
    entries: dict[str, dict] = {}
    for s in slots:
        pick = picks.get(s["id"]) or {"asset": -1, "reason": "no answer from model"}
        idx = pick["asset"]
        allowed = s["candidates"] is None or idx in (s["candidates"] or [])
        if idx is not None and 0 <= idx < len(catalog) and allowed and idx not in s["exclude"]:
            choices[s["id"]] = idx
            entry = {"stage": "assigned", "asset": catalog[idx]["path"]}
        else:
            choices[s["id"]] = None
            entry = {"stage": "unmatched", "asset": None}
            if idx not in (-1, None):
                pick["reason"] = f"invalid pick [{idx}] discarded; {pick['reason']}"
        entries[s["id"]] = {
            "slot": s["id"],
            "slide": s["slide"],
            "reason": pick["reason"],
            "candidates": "all" if s["candidates"] is None else len(s["candidates"]),
            **entry,
        }

    used: dict[int, list[str]] = {}
    for sid, idx in choices.items():
        if idx is not None:
            used.setdefault(idx, []).append(sid)
    for idx, sids in used.items():
        if len(sids) > 1:
            for sid in sids:
                entries[sid]["shared_with"] = [x for x in sids if x != sid]

    if verify:
        by_id = {s["id"]: s for s in slots}
        failed: list[dict] = []
        for sid, idx in choices.items():
            if idx is None:
                continue
            slot = by_id[sid]
            verdict = verify_pair(catalog[idx]["abs_path"], slot)
            entries[sid]["verified"] = verdict["match"]
            entries[sid]["verify_reason"] = verdict["reason"]
            log(
                f"  verify {sid} <- {catalog[idx]['path']}: "
                f"{'ok' if verdict['match'] else 'MISMATCH'} ({verdict['reason'][:80]})"
            )
            if not verdict["match"]:
                slot["exclude"].add(idx)
                failed.append(slot)
        if failed:
            log(f"  re-assigning {len(failed)} slot(s) after verification failures")
            retry_choices, retry_report = match_images(
                failed, catalog, pool, verify=False, log=log
            )
            retry_entries = {e["slot"]: e for e in retry_report}
            for slot in failed:
                sid = slot["id"]
                idx = retry_choices.get(sid)
                entry = retry_entries[sid]
                if idx is not None:
                    verdict = verify_pair(catalog[idx]["abs_path"], slot)
                    entry["verified"] = verdict["match"]
                    entry["verify_reason"] = verdict["reason"]
                    if not verdict["match"]:
                        # Two strikes: better an empty slot than a wrong image.
                        idx = None
                        entry.update(
                            stage="unmatched",
                            asset=None,
                            reason=f"re-assignment also failed verification: {verdict['reason']}",
                        )
                choices[sid] = idx
                entry["stage"] = "reassigned" if idx is not None else entry["stage"]
                entries[sid] = entry

    report.extend(entries[s["id"]] for s in slots)
    return choices, report
