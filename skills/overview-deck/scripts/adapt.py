#!/usr/bin/env python3
"""Governed spec mutation: the base spec changes iff the user asked, and
only in the way they asked.

The adapter never runs without an explicit request. When it does, the model
proposes an edited spec plus a justification for EVERY changed slide — a
verbatim quote from the request. Enforcement is mechanical, not trusted:
this module recomputes the slide-level diff and rejects the whole adaptation
if any changed slide lacks a justification whose quote actually appears in
the request. Rejected -> the base spec compiles unmodified and the caller
says so. There is no partial acceptance.

`permitted_ops` exists from day one (default: any operation, request-backed)
so a future flag can narrow to a whitelist without rework.

The result must still pass the full spec validation — a mutation cannot
produce a spec the compiler would refuse.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = SKILL.parent.parent
for p in (str(SKILL / "scripts"), str(PLUGIN_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import yaml  # noqa: E402

from deckspec import SpecError, validate  # noqa: E402

ALL_OPS = ("remove_slide", "add_slide", "modify_slide", "reorder")

ADAPT_SCHEMA = {
    "type": "object",
    "properties": {
        "spec_yaml": {"type": "string", "description": "the complete edited spec, YAML"},
        "justifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slide_id": {"type": "string"},
                    "op": {"type": "string", "enum": list(ALL_OPS)},
                    "quote": {"type": "string",
                              "description": "verbatim text from the request that demands this"},
                },
                "required": ["slide_id", "op", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["spec_yaml", "justifications"],
    "additionalProperties": False,
}

ADAPT_PROMPT = """Edit this deck spec to satisfy the user's request — and change NOTHING the
request does not explicitly demand.

Rules:
- Copy through every untouched slide byte-for-byte. No tidying, no
  reordering, no "while I'm here".
- Every slide you remove, add or modify needs a justification quoting the
  exact request text that demands it. A change you cannot quote for is a
  change you must not make.
- Keep the spec language: layout/skeleton/tier slides, tokens, images with
  `expects` descriptions, repeat/when/step. New slides for free-form asks
  are tier 3 (fixed content) or tier 4 (brief only).
- The closing run (`closing: default`) and slide ids stay unless the request
  names them.

USER REQUEST:
{request}

CURRENT SPEC:
{spec}"""


class AdaptError(RuntimeError):
    pass


def adapt_call(spec_text: str, request: str) -> dict:
    from core import llm

    return llm.complete(
        ADAPT_PROMPT.format(request=request, spec=spec_text),
        schema=ADAPT_SCHEMA, max_tokens=9000,
    )


def _by_id(spec: dict) -> dict[str, dict]:
    out = {}
    for s in spec.get("slides", []):
        key = s.get("id") or f"closing:{s.get('closing')}"
        out[key] = s
    return out


def diff_specs(base: dict, edited: dict) -> list[dict]:
    """Slide-level diff: removed / added / modified / reordered."""
    b, e = _by_id(base), _by_id(edited)
    changes = []
    for sid in b:
        if sid not in e:
            changes.append({"slide_id": sid, "op": "remove_slide"})
    for sid in e:
        if sid not in b:
            changes.append({"slide_id": sid, "op": "add_slide"})
        elif e[sid] != b[sid]:
            changes.append({"slide_id": sid, "op": "modify_slide"})
    common_b = [s for s in b if s in e]
    common_e = [s for s in e if s in b]
    if common_b != common_e:
        changes.append({"slide_id": "*", "op": "reorder"})
    return changes


def validate_mutation(base: dict, edited: dict, justifications: list[dict],
                      request: str, permitted_ops=ALL_OPS) -> list[dict]:
    """The enforcement. Returns the accepted diff, or raises AdaptError with
    every violation named. A quote must appear verbatim (case-insensitive,
    whitespace-normalized) in the request."""
    norm_req = " ".join(request.lower().split())
    just_by_slide: dict[str, dict] = {}
    problems = []
    for j in justifications:
        quote = " ".join(str(j.get("quote", "")).lower().split())
        if not quote or quote not in norm_req:
            problems.append(f"{j.get('slide_id')}: justification quote not found "
                            f"in the request: {j.get('quote', '')[:60]!r}")
        just_by_slide[j["slide_id"]] = j

    changes = diff_specs(base, edited)
    if not changes:
        problems.append("request produced no change — nothing to adapt")
    for c in changes:
        j = just_by_slide.get(c["slide_id"])
        if j is None:
            problems.append(f"{c['slide_id']}: {c['op']} has no justification")
        elif j["op"] != c["op"]:
            problems.append(f"{c['slide_id']}: justified as {j['op']} but diff shows {c['op']}")
        elif c["op"] not in permitted_ops:
            problems.append(f"{c['slide_id']}: {c['op']} is not a permitted operation")
    if problems:
        raise AdaptError("; ".join(problems))
    return changes


def adapt(base: dict, request: str, permitted_ops=ALL_OPS, log=print) -> tuple[dict, dict]:
    """(spec_to_compile, diff_record). On ANY violation the base spec is
    returned unmodified with the rejection recorded — a bad adaptation must
    never half-apply."""
    spec_text = yaml.safe_dump(base, sort_keys=False, width=90)
    try:
        out = adapt_call(spec_text, request)
        edited = yaml.safe_load(out["spec_yaml"])
        validate(edited)  # a mutation may not produce an uncompilable spec
        changes = validate_mutation(base, edited, out["justifications"],
                                    request, permitted_ops)
    except (AdaptError, SpecError, yaml.YAMLError) as e:
        log(f"  adapt: REJECTED — compiling the base spec unmodified ({e})")
        return copy.deepcopy(base), {
            "applied": False, "request": request, "rejected": str(e)[:500],
        }
    log(f"  adapt: applied {len(changes)} change(s), each request-justified")
    return edited, {
        "applied": True, "request": request, "changes": changes,
        "justifications": out["justifications"],
    }


if __name__ == "__main__":
    # maintainer smoke: adapt the shipped spec against a request, print diff
    import argparse

    from deckspec import load_spec

    ap = argparse.ArgumentParser()
    ap.add_argument("request")
    a = ap.parse_args()
    _, diff = adapt(load_spec(), a.request)
    print(json.dumps(diff, indent=2))
