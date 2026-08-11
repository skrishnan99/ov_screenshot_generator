#!/usr/bin/env python3
"""Content resolution: every `llm:` token in one grounded, governed call.

This is the ONE place slide copy is generated, which is the point: the
register rules that lived as prose the model had to remember while writing
Python (v1) are here the system prompt of the only call that writes copy.

Ordering gotcha (learned from a real caption bug): matching runs BEFORE
content. Each image-adjacent token's context leads with the description of
the image ACTUALLY PLACED on that slide, so a caption cannot describe marks
the picture does not show.

Scoping gotcha: a token inside `repeat: models` sees a model-scoped slice —
that model's roster entry, its facts, its matched images' descriptions — not
the whole corpus. One global blob of material is how Model A's numbers end
up on Model B's slide.

Shapes: text (sentences) | lines (3-6 newline-separated plain values) |
pairs ("label | sub" lines for the flow diagram). Validation is code:
lengths, shapes, banned vocabulary; one targeted retry; then a loud failure
naming the token — a deck with one wrong sentence is worse than no deck.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = SKILL.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# Register lint. Production narration and absence-commentary are banned from
# slide copy outright; these are substrings, checked lowercase. "screenshot"
# is banned on purpose — copy describes "the Inspection Setup screen", not
# the artifact that captured it.
BANNED = (
    "composit", "screenshot", "captured", "extracted", "rendered into",
    "pipeline", "skeleton", "placeholder", "asset",
    "not available", "not run", "n/a", "not populated", "not present",
    "no validation", "not validated", "not configured yet", "not stated",
)

DEFAULT_MAX_CHARS = {"text": 240, "lines": 320, "pairs": 240}
LINE_RANGE = (2, 6)

SYSTEM_RULES = """You write slide copy for a customer-facing camera inspection test report,
as the vision sales engineer who ran the test. Readers are the customer's
quality manager, controls engineer and buyer — none of whom has ever used
the camera UI.

Hard rules, all of them:
- GROUNDING: every number, name and setting must appear in the material
  below. Never estimate, never infer a plausible figure. A stat with no
  supporting data is written as a single em dash: —
- ABSTRACTION: say what a setting ACHIEVES, never what it is called in the
  UI. No config minutiae, no menu names, no node or variable names from the
  IO flow (threshold values and plain-language rules are welcome).
- PRESENT DATA ONLY: describe what the data shows. Never mention what was
  not run, not configured, not populated or otherwise absent — absence is
  expressed by silence, never by commentary.
- NO PRODUCTION NARRATION: the material describes how it was gathered
  ("screenshot composited", "captured at step X"). That vocabulary never
  reaches copy. Write about the camera and the part, as if describing the
  screen itself: "the Inspection Setup screen shows...", never "the
  screenshot shows...".
- IDENTIFIERS: the camera MODEL (e.g. OV80i) is fine; never a serial,
  device nickname, hostname, firmware version or capture id.
- Plain declaratives. Concrete over evaluative ("training loss 0.028", not
  "excellent convergence"). No exclamation marks, no marketing register.
- When the material states a requirement (tolerance, budget), pair the
  achieved figure with it. Never remark that a requirement is missing.
- Numbers: keep the units the UI used; resolution as 3840x2160; thousands
  separators for counts.

Shapes:
- "text": full sentences, within the length limit.
- "lines": newline-separated short plain-value lines ("Training accuracy: 100%"),
  {lo}-{hi} lines. Only lines for data that exists.
- "pairs": newline-separated "label | sub" pairs for a flow diagram, 2-3 of
  them, plain language.

Return JSON: {{"values": {{token_id: string}}}} — every requested token, nothing else."""

RESOLVE_SCHEMA = {
    "type": "object",
    "properties": {"values": {"type": "object", "additionalProperties": {"type": "string"}}},
    "required": ["values"],
    "additionalProperties": False,
}


@dataclass
class TokenReq:
    id: str            # "<job id>.<token name>"
    brief: str
    shape: str
    max_chars: int
    scope: str         # rendered context slice


class ContentError(RuntimeError):
    pass


# ----------------------------------------------------------- material prep


def _facts_by_subject(meta: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in meta.get("facts", []):
        out.setdefault(str(f.get("subject", "")), []).append(
            f"{f.get('property')}: {f.get('value')}  [from {f.get('source', '?')}]"
        )
    return out


def _model_slice(model: dict, facts: dict, descriptions: dict, matched_paths: list[str]) -> str:
    """Everything this model's slides may draw on, and nothing else."""
    name = model.get("name", "")
    parts = [f"MODEL: {name} ({model.get('type', '')})"]
    for subj, lines in facts.items():
        if name.lower() in subj.lower() or subj.lower().startswith("class:"):
            parts.append(f"facts [{subj}]:\n  " + "\n  ".join(lines[:20]))
    for rel in matched_paths:
        d = descriptions.get(Path(rel).name)
        if d:
            parts.append(f"the image on this slide ({Path(rel).name}):\n{str(d)[:1500]}")
    return "\n".join(parts)


def build_material(run_dir: Path, notes: str = "") -> dict:
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "data" / "meta.json").read_text())
    desc_path = run_dir / "deliverables" / "report" / "descriptions.json"
    descriptions = json.loads(desc_path.read_text()) if desc_path.exists() else {}
    nr = run_dir / "deliverables" / "report" / "node_red_description.md"
    return {
        "meta": meta,
        "facts": _facts_by_subject(meta),
        "descriptions": descriptions,
        "node_red": nr.read_text() if nr.exists() else "",
        "notes": notes,
    }


def collect(jobs, material: dict, assignments: dict[str, str]) -> list[TokenReq]:
    """Walk the jobs; every {llm: ...} token becomes a request with its scope
    rendered. Image-adjacent scope = the matched images' descriptions."""
    reqs: list[TokenReq] = []
    for job in jobs:
        matched = [assignments.get(f"{job.id}#{i}")
                   for i in range(len(job.images))]
        matched = [p for p in matched if p]
        scope_parts = [f'SLIDE: "{job.title or job.id}"']
        if job.model:
            scope_parts.append(_model_slice(job.model, material["facts"],
                                            material["descriptions"], matched))
        else:
            for rel in matched:
                d = material["descriptions"].get(Path(rel).name)
                if d:
                    scope_parts.append(
                        f"the image on this slide ({Path(rel).name}):\n{str(d)[:1500]}")
        if job.origin in ("logic",):  # the IO-logic slide gets the analysis
            scope_parts.append("IO LOGIC ANALYSIS:\n" + material["node_red"][:9000])
        scope = "\n\n".join(scope_parts)

        def walk(tokens: dict, prefix: str):
            for name, val in tokens.items():
                if isinstance(val, dict) and "llm" in val:
                    shape = val.get("shape", "text")
                    reqs.append(TokenReq(
                        id=f"{prefix}.{name}",
                        brief=" ".join(str(val["llm"]).split()),
                        shape=shape,
                        max_chars=int(val.get("max_chars", DEFAULT_MAX_CHARS[shape])),
                        scope=scope,
                    ))

        walk(job.tokens, job.id)
    return reqs


# ---------------------------------------------------------------- resolve


def resolve_call(reqs: list[TokenReq], material: dict) -> dict[str, str]:
    from core import llm

    shared = ["GENERAL MATERIAL:"]
    for subj in ("recipe", "camera"):
        if subj in material["facts"]:
            shared.append(f"facts [{subj}]:\n  " + "\n  ".join(material["facts"][subj][:30]))
    if material["notes"]:
        shared.append("ENGINEER NOTES (verbatim, authoritative where they speak):\n"
                      + material["notes"][:4000])

    blocks = []
    for r in reqs:
        blocks.append(
            f"### token: {r.id}\nshape: {r.shape}   max_chars: {r.max_chars}\n"
            f"brief: {r.brief}\ncontext:\n{r.scope[:6000]}"
        )
    prompt = (
        "\n".join(shared)
        + "\n\nResolve every token below. Use each token's own context first; "
          "the general material is shared background.\n\n"
        + "\n\n".join(blocks)
    )
    # core.llm.complete has no system-prompt parameter; the rules lead the
    # prompt, which for a single self-contained call is equivalent.
    out = llm.complete(
        SYSTEM_RULES.format(lo=LINE_RANGE[0], hi=LINE_RANGE[1]) + "\n\n" + prompt,
        schema=RESOLVE_SCHEMA,
        max_tokens=8000,
    )
    return out["values"]


def normalize(req: TokenReq, value: str) -> str:
    """Mechanical silence for absent data: a lines/pairs value drops every
    line whose data part is an em dash ("Training accuracy: —") — absence is
    expressed by the line not existing, and trusting the prompt alone was not
    enough (a real build emitted dash lines). A value that loses every line
    collapses to the sanctioned bare dash."""
    if req.shape not in ("lines", "pairs"):
        return value.strip()
    kept = []
    for ln in value.splitlines():
        body = ln.strip()
        if not body:
            continue
        data = body.split(":", 1)[-1] if ":" in body else body.split("|", 1)[-1]
        if data.strip() in ("—", "-", "--", ""):
            continue
        kept.append(body)
    return "\n".join(kept) if kept else "—"


def lint(req: TokenReq, value: str) -> list[str]:
    problems = []
    v = value.strip()
    if not v:
        return [f"{req.id}: empty"]
    if v == "—":
        return []  # the sanctioned no-data value, any shape
    low = v.lower()
    for b in BANNED:
        if b in low:
            problems.append(f"{req.id}: banned phrase {b!r} in {v[:60]!r}")
    if len(v) > req.max_chars:
        problems.append(f"{req.id}: {len(v)} chars > {req.max_chars}")
    if req.shape == "lines":
        n = len([ln for ln in v.splitlines() if ln.strip()])
        if not (1 <= n <= LINE_RANGE[1]):
            problems.append(f"{req.id}: {n} lines outside 1-{LINE_RANGE[1]}")
    if req.shape == "pairs":
        rows = [ln for ln in v.splitlines() if ln.strip()]
        if not (2 <= len(rows) <= 3) or any("|" not in ln for ln in rows):
            problems.append(f"{req.id}: pairs must be 2-3 'label | sub' lines")
    return problems


def resolve(jobs, material: dict, assignments: dict[str, str], log=print) -> dict[str, str]:
    """All tokens in one call; per-token lint; one targeted retry for the
    failures only; anything still failing raises with the token named."""
    reqs = collect(jobs, material, assignments)
    if not reqs:
        return {}
    values = resolve_call(reqs, material)

    failing: list[TokenReq] = []
    problems_all: list[str] = []
    for r in reqs:
        values[r.id] = normalize(r, values.get(r.id, ""))
        probs = lint(r, values[r.id])
        if probs:
            failing.append(r)
            problems_all.extend(probs)
    if failing:
        log(f"  content: retrying {len(failing)} token(s): "
            + "; ".join(problems_all[:4]))
        retry_reqs = []
        for r in failing:
            retry_reqs.append(TokenReq(
                id=r.id, shape=r.shape, max_chars=r.max_chars, scope=r.scope,
                brief=r.brief + "  IMPORTANT: previous attempt was rejected ("
                + "; ".join(p for p in problems_all if p.startswith(r.id))[:300]
                + "). Fix exactly that.",
            ))
        for k, v2 in resolve_call(retry_reqs, material).items():
            req = next((r for r in failing if r.id == k), None)
            values[k] = normalize(req, v2) if req else v2
        remaining = [p for r in failing for p in lint(r, values.get(r.id, ""))]
        if remaining:
            raise ContentError("unresolvable tokens: " + "; ".join(remaining[:6]))
    return {r.id: values[r.id].strip() for r in reqs}
