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

import io

from core import llm
from core.llm import LLMRefusal, complete
from deck.content import asset_path

CATALOG_DESC_CHARS = 800
VERIFY_MAX_PX = 1568
# Vision verification is one independent call per matched slot.
VERIFY_WORKERS = 4


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
        widened = (
            "\n  NOTE: no convention-based selector matched this slot, so the whole "
            "catalog is offered — be strict: pick only an asset that genuinely shows "
            "what the slot needs, else -1."
            if s.get("widened")
            else ""
        )
        lines.append(
            f'- slot "{s["id"]}" on slide "{s["slide"]}" '
            f'(headline: "{s.get("slide_title") or "?"}"{geom})\n'
            f"  needs: {s['expects']}\n"
            f"  allowed assets: {allowed}{widened}"
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
- Photos provided by the sales engineer (source=engineer) take PRECEDENCE over extractor
  screenshots: when an engineer photo genuinely shows what a slot needs, choose it over
  the equivalent extractor capture. If the engineer's notes direct a photo to a specific
  slide, follow them. Never force an engineer photo into a slot it doesn't fit.
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
    return complete(prompt, schema=schema, max_tokens=4000)


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


def _downscaled_png(path: str) -> bytes:
    """Downscaled PNG bytes — full-page captures can be huge."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        scale = VERIFY_MAX_PX / max(im.size)
        if scale < 1:
            im = im.resize((int(im.width * scale), int(im.height * scale)))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
    return buf.getvalue()


def verify_pair(image_path: str, slot: dict) -> dict:
    prompt = VERIFY_PROMPT.format(
        title=slot.get("slide_title") or "?", expects=slot["expects"]
    )
    try:
        return complete(
            prompt,
            schema=VERIFY_SCHEMA,
            images=[_downscaled_png(image_path)],
            max_tokens=1000,
            model=llm.SONNET,
        )
    except LLMRefusal:
        return {"match": True, "reason": "verification refused; keeping assignment"}


def match_images(
    slots: list[dict],
    catalog: list[dict],
    pool: dict,
    verify: bool = False,
    log=print,
    _repair: bool = False,
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

    # Slots whose selector already determined the asset need no assignment
    # call — they arrive pre-assigned and only pass through verification.
    pending = [s for s in slots if s.get("preassigned") is None]
    picks = assign_images(pending, catalog, pool) if pending else {}
    entries: dict[str, dict] = {}
    for s in slots:
        pre = s.get("preassigned")
        if pre is not None:
            pick = {
                "asset": pre,
                "reason": s.get("preassigned_reason", "determined by selector"),
            }
        else:
            pick = picks.get(s["id"]) or {"asset": -1, "reason": "no answer from model"}
        idx = pick["asset"]
        allowed = s["candidates"] is None or idx in (s["candidates"] or [])
        if idx is not None and 0 <= idx < len(catalog) and allowed and idx not in s["exclude"]:
            choices[s["id"]] = idx
            entry = {
                "stage": "deterministic" if pre is not None else "assigned",
                "asset": catalog[idx]["path"],
            }
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
            **({"widened": True} if s.get("widened") else {}),
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

    by_id = {s["id"]: s for s in slots}
    # Widened slots (no selector matched; full-pool assignment) are always
    # vision-verified — they are the least constrained picks in the deck.
    # In the repair round the caller verifies the new picks itself, so this
    # auto-verification stays off to avoid checking the same pair twice.
    auto_verify = not _repair
    if verify or (
        auto_verify
        and any(s.get("widened") and choices.get(s["id"]) is not None for s in slots)
    ):
        failed: list[dict] = []
        todo = [
            (sid, idx)
            for sid, idx in choices.items()
            if idx is not None
            and (verify or (auto_verify and by_id[sid].get("widened")))
        ]
        # Independent calls; run them concurrently and consume in slot order
        # so the report stays deterministic.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=VERIFY_WORKERS) as pool_exec:
            futures = [
                pool_exec.submit(verify_pair, catalog[idx]["abs_path"], by_id[sid])
                for sid, idx in todo
            ]
            for (sid, idx), fut in zip(todo, futures):
                slot = by_id[sid]
                try:
                    verdict = fut.result()
                except Exception as e:
                    verdict = {"match": True, "reason": f"verification errored ({e})"}
                entries[sid]["verified"] = verdict["match"]
                entries[sid]["verify_reason"] = verdict["reason"]
                log(
                    f"  verify {sid} <- {catalog[idx]['path']}: "
                    f"{'ok' if verdict['match'] else 'MISMATCH'} ({verdict['reason'][:80]})"
                )
                if not verdict["match"]:
                    slot["exclude"].add(idx)
                    # A rejected pick loses its head start: let the assigner
                    # choose from the whole pool, and re-verify what it picks.
                    slot.pop("preassigned", None)
                    slot["candidates"] = None
                    slot["widened"] = True
                    failed.append(slot)
        if failed:
            log(f"  re-assigning {len(failed)} slot(s) after verification failures")
            retry_choices, retry_report = match_images(
                failed, catalog, pool, verify=False, log=log, _repair=True
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
