"""Model-driven deck structure adaptation (--adaptive-structure).

When enabled and the engineer provided notes, ONE structured model call
regenerates the deck spec: the variant's default spec is the strong starting
point, and the model is instructed to copy it through verbatim except for
changes the notes explicitly request. The regenerated spec then drives
build_plan exactly as the file would have — the plan is model-driven; the
deterministically-computed diff against the default is only a debug artifact
(diff.json).

Robustness: the output is validated deterministically (known slide shapes,
skeleton existence, unique ids, a drift ceiling so "high affinity" is
enforced, not just requested). Validation failures feed back for up to
ATTEMPTS tries; after the ceiling the untouched default spec is used and the
failure is recorded — an engineer always gets a deck.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from core import llm
from core.paths import PACKAGE_ROOT

ATTEMPTS = 3
# "Modify a bit" enforced numerically: touching more than this fraction of
# the default deck's slides fails validation.
DRIFT_CEILING = 0.5

ADAPT_SCHEMA = {
    "type": "object",
    "properties": {
        "spec": {
            "type": "object",
            "properties": {
                "variant": {"type": "string"},
                "slides": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["variant", "slides"],
        },
        "change_summary": {
            "type": "string",
            "description": "One short paragraph: what was changed and which part "
            "of the notes requested it; 'no changes requested' if none.",
        },
    },
    "required": ["spec", "change_summary"],
    "additionalProperties": False,
}

SLIDE_TYPE_DOCS = """Slide forms the plan builder accepts (a slide is exactly one of these):
- Skeleton slide: {"id", "skeleton": "<name>.pptx", optional "tokens", "image"/"images",
  "step_counter": true, "when": {"select": {...}}}
- Freeform slide: {"id", "freeform": {"title": str, "body": str|{"llm": guidance}},
  optional "image"/"images", optional "donor": "<skeleton>.pptx"}
- Agent-built slide: {"id", "agent_slide": {"style": "open"|"adaptive",
  "description": str, "skeleton": "<name>.pptx" (required for adaptive)},
  optional "tokens", "image"/"images"}
- Per-model group: {"id", "repeat_for": "models", "slides": [slides...]} where inner
  slides may carry "only_type": "classification"|"segmentation" and use {model_name},
  {model_label}, {model_slug}, {model_type} placeholders.
Token values: literal string, {"literal": v}, {"source": "recipe"|"model_<field>"},
or {"llm": "guidance for the copywriter"}.
Image spec: {"select": <selector or ordered list of selectors>, "expects": "what the
image must show"}; selectors filter assets by step/kind/path_contains/source/path."""

PROMPT = """You maintain the slide-deck spec for an automatically generated, customer-facing
camera inspection test report. Below is the DEFAULT spec for this camera variant, and
the sales engineer's site-visit notes.

Output the COMPLETE spec to use for this deck. Follow these rules strictly:
- The default spec is authoritative. COPY IT THROUGH EXACTLY — same slides, same order,
  same field values, byte-for-byte — except where the notes EXPLICITLY request a
  structural change (omit/add/reorder/replace a slide, or change what a slide covers).
- Never edit for taste, style, or perceived improvement. Content wording is NOT your
  job (a separate copywriter fills tokens); only structure is.
- If the notes request nothing structural, output the default spec unchanged and say so
  in change_summary.
- Additions must use the slide forms documented below and only skeletons from the
  catalog; for genuinely new content prefer a freeform slide, or an agent_slide
  (style "open") when the notes describe a specific layout.
- Give every added slide a unique id. Keep every change minimal.

{feedback}=== DEFAULT SPEC (YAML) ===
{spec_yaml}

=== ENGINEER'S NOTES ===
{notes}

=== SLIDE FORMS ===
{slide_docs}

=== SKELETON CATALOG (name: purpose) ===
{catalog}

=== THIS RUN'S DATA (for feasibility of additions) ===
Models: {models}
Asset steps available: {steps}"""


def _skeleton_catalog(variant: str) -> str:
    lines = []
    for folder in (PACKAGE_ROOT / "deck" / "skeletons" / variant,
                   PACKAGE_ROOT / "deck" / "skeletons" / "_shared"):
        if not folder.is_dir():
            continue
        for p in sorted(folder.glob("*.pptx")):
            purpose = ""
            sidecar = p.with_suffix(".yaml")
            if sidecar.exists():
                try:
                    side = yaml.safe_load(sidecar.read_text()) or {}
                    slots = side.get("image_slots") or []
                    purpose = "; ".join(
                        " ".join(str(s.get("expects", "")).split())[:90] for s in slots
                    )
                except Exception:
                    pass
            lines.append(f"- {p.name}: {purpose or '(static/branding slide)'}")
    return "\n".join(lines)


def _skeleton_ok(variant: str, name) -> bool:
    from deck_cli import skeleton_path

    if not name or not isinstance(name, str) or not name.endswith(".pptx"):
        return False
    try:
        skeleton_path(variant, name)
        return True
    except FileNotFoundError:
        return False


def _validate_slide(s, variant: str, errors: list, ids: list, allow_group=True) -> None:
    if not isinstance(s, dict):
        errors.append(f"slide is not an object: {str(s)[:60]}")
        return
    sid = s.get("id")
    if not sid or not isinstance(sid, str):
        errors.append(f"slide missing string id: {str(s)[:60]}")
    else:
        ids.append(sid)
    if "slides" in s:
        if not allow_group:
            errors.append(f"slide {sid}: nested groups are not allowed")
            return
        if s.get("repeat_for") != "models":
            errors.append(f"group {sid}: must set repeat_for: models")
        for sub in s.get("slides") or []:
            _validate_slide(sub, variant, errors, ids, allow_group=False)
            if isinstance(sub, dict) and sub.get("only_type") not in (
                None, "classification", "segmentation",
            ):
                errors.append(f"slide {sub.get('id')}: invalid only_type")
        return
    forms = [k for k in ("skeleton", "freeform", "agent_slide") if s.get(k)]
    if len(forms) != 1:
        errors.append(
            f"slide {sid}: must have exactly one of skeleton/freeform/agent_slide "
            f"(found {forms or 'none'})"
        )
        return
    if s.get("skeleton") and not _skeleton_ok(variant, s["skeleton"]):
        errors.append(f"slide {sid}: unknown skeleton {s['skeleton']!r}")
    if s.get("freeform") is not None and not isinstance(s["freeform"], dict):
        errors.append(f"slide {sid}: freeform must be an object with title/body")
    if s.get("agent_slide"):
        a = s["agent_slide"]
        if not isinstance(a, dict) or a.get("style") not in ("open", "adaptive"):
            errors.append(f"slide {sid}: agent_slide.style must be open|adaptive")
        elif a["style"] == "adaptive" and not _skeleton_ok(variant, a.get("skeleton")):
            errors.append(f"slide {sid}: adaptive agent_slide needs a known skeleton")
    tokens = s.get("tokens")
    if tokens is not None:
        if not isinstance(tokens, dict):
            errors.append(f"slide {sid}: tokens must be a mapping")
        else:
            for name, t in tokens.items():
                if not isinstance(t, str) and not (
                    isinstance(t, dict) and any(k in t for k in ("literal", "source", "llm"))
                ):
                    errors.append(f"slide {sid}: token {name} has invalid spec")
    for key in ("image",):
        if s.get(key) is not None and not isinstance(s[key], dict):
            errors.append(f"slide {sid}: {key} must be a mapping")
    if s.get("images") is not None and not isinstance(s["images"], list):
        errors.append(f"slide {sid}: images must be a list")


def spec_diff(base: dict, new: dict) -> dict:
    """Slide-level structural diff, keyed by slide id (deterministic)."""
    base_slides = {s.get("id"): s for s in base.get("slides", []) if isinstance(s, dict)}
    new_slides = {s.get("id"): s for s in new.get("slides", []) if isinstance(s, dict)}
    base_order = [i for i in (s.get("id") for s in base.get("slides", [])) if i]
    new_order = [i for i in (s.get("id") for s in new.get("slides", [])) if i]
    common_base = [i for i in base_order if i in new_slides]
    common_new = [i for i in new_order if i in base_slides]
    modified = {}
    for i in common_new:
        if base_slides[i] != new_slides[i]:
            keys = sorted(set(base_slides[i]) | set(new_slides[i]))
            modified[i] = [k for k in keys if base_slides[i].get(k) != new_slides[i].get(k)]
    return {
        "added": [i for i in new_order if i not in base_slides],
        "removed": [i for i in base_order if i not in new_slides],
        "moved": common_new != common_base,
        "modified": modified,
        "unchanged": len(common_new) - len(modified),
    }


def validate_spec(spec: dict, base: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["output spec is not an object"]
    if spec.get("variant") != base.get("variant"):
        errors.append(f"variant must remain {base.get('variant')!r}")
    slides = spec.get("slides")
    if not isinstance(slides, list) or not slides:
        return errors + ["slides must be a non-empty list"]
    ids: list[str] = []
    for s in slides:
        _validate_slide(s, base.get("variant", ""), errors, ids)
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        errors.append(f"duplicate slide ids: {sorted(dupes)}")
    diff = spec_diff(base, spec)
    touched = len(diff["removed"]) + len(diff["modified"])
    if touched > max(2, int(len(base.get("slides", [])) * DRIFT_CEILING)):
        errors.append(
            f"too much drift: {touched} default slides removed/modified — copy "
            f"slides the notes did not mention through EXACTLY as given"
        )
    return errors


def adapt_spec(base_spec: dict, pool: dict, log=print) -> tuple[dict, dict]:
    """Returns (spec_to_use, debug_record). Falls back to base_spec after the
    retry ceiling; debug_record always describes what happened (-> diff.json)."""
    notes = pool.get("engineer_notes") or ""
    catalog = _skeleton_catalog(base_spec.get("variant", ""))
    models = [m.get("label", m.get("name", "?")) for m in pool.get("models", [])]
    steps = sorted({str(a.get("step") or "?") for a in pool.get("assets", [])})
    spec_yaml = yaml.safe_dump(base_spec, sort_keys=False, allow_unicode=True)
    record: dict = {"applied": False, "attempts": 0, "errors_by_attempt": []}

    feedback = ""
    for attempt in range(1, ATTEMPTS + 1):
        record["attempts"] = attempt
        prompt = PROMPT.format(
            feedback=feedback,
            spec_yaml=spec_yaml,
            notes=notes,
            slide_docs=SLIDE_TYPE_DOCS,
            catalog=catalog,
            models=", ".join(models) or "(none)",
            steps=", ".join(steps) or "(none)",
        )
        try:
            out = llm.complete(prompt, schema=ADAPT_SCHEMA, max_tokens=16000)
        except Exception as e:
            record["errors_by_attempt"].append([f"model call failed: {e}"])
            log(f"  structure adaptation attempt {attempt}: model call failed: {e}")
            continue
        spec = out.get("spec")
        errors = validate_spec(spec, base_spec)
        if not errors:
            diff = spec_diff(base_spec, spec)
            record.update(
                applied=True,
                change_summary=out.get("change_summary", ""),
                diff=diff,
            )
            n_changes = len(diff["added"]) + len(diff["removed"]) + len(diff["modified"])
            log(
                f"  structure adapted ({n_changes} change(s)): "
                f"{record['change_summary'][:120]}"
            )
            return spec, record
        record["errors_by_attempt"].append(errors)
        log(
            f"  structure adaptation attempt {attempt} invalid: "
            f"{'; '.join(errors)[:150]}"
        )
        feedback = (
            "Your previous output was rejected by validation:\n- "
            + "\n- ".join(errors)
            + "\nFix ONLY these problems and output the complete spec again.\n\n"
        )
    record["fallback"] = "validation ceiling reached; default spec used"
    log("  structure adaptation failed validation; using the default spec")
    return base_spec, record
