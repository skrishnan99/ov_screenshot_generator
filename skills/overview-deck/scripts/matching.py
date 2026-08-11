#!/usr/bin/env python3
"""Semantic image matching: content holes -> run images, by description.

Holes are described in the spec ("the Inspection Setup screen with
{model.name} selected..."); images are described by the extractor
(descriptions.json) or synthesized here. Matching is what decouples specs
from file naming — a renamed screenshot or a different camera variant can't
break a spec — and it is the only mechanism that can ever place an engineer's
photo, which has no step/kind metadata to join on.

Three v1 lessons are load-bearing here:

1. GLOBAL ASSIGNMENT. One call sees every hole and every image at once and
   reasons jointly, each image used at most once. Greedy per-hole matching is
   what made the original v1 matcher unreliable before its rebuild.
2. IDENTITY, NOT FULLNESS. Verification asks "is this the screen the hole
   expects", and an EMPTY image area still matches — a recipe with Skip
   Aligner on legitimately shows a blank template, and rejecting it once
   deleted two slides from the numbered run.
3. The catalog must cover deliverables/images/ too. descriptions.json only
   describes screenshots; the native captures (12_library_raw.jpg, the
   composites) get deterministic descriptions synthesized from meta.json —
   without them the raw-beside-overlay slide can never match.

Model calls are isolated in assign_call()/verify_call() so tests stub them.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = SKILL.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# Synthesized descriptions for meta.json main-image entries, keyed by the
# meta key prefix. Deterministic on purpose: these files' contents are known
# by construction, and a wrong guess here would poison every match.
_NATIVE = {
    "library_main_image": (
        "The library capture as imaged — the part photograph at native "
        "resolution, no inspection overlay drawn.",
        "The library capture with the inspection overlay drawn on the part — "
        "regions and marks as shown in the UI.",
    ),
    "template_image_main_image": (
        "The template/alignment capture as imaged — native resolution, no overlay.",
        "The template/alignment capture with its overlay drawn (search areas).",
    ),
}


@dataclass
class Hole:
    id: str                 # "<job id>#<index>"
    slide_title: str
    expects: str
    optional: bool = False


@dataclass
class MatchResult:
    assignments: dict[str, str | None] = field(default_factory=dict)  # hole id -> rel path
    report: list[dict] = field(default_factory=list)


def build_catalog(run_dir: Path, extra_images: list[Path] | None = None) -> list[dict]:
    """[{path, description}] over everything a slide could show.

    Sources: descriptions.json (screenshots, described by the extractor);
    meta.json main-image entries (native captures + composites, synthesized);
    engineer photos (described on the spot — the only vision calls at catalog
    time, and only when photos were supplied).
    """
    run_dir = Path(run_dir)
    catalog: list[dict] = []
    seen: set[str] = set()

    desc_path = run_dir / "deliverables" / "report" / "descriptions.json"
    descriptions = json.loads(desc_path.read_text()) if desc_path.exists() else {}
    for name, text in descriptions.items():
        if str(text).startswith("[description failed"):
            continue
        rel = f"deliverables/screenshots/{name}"
        if (run_dir / rel).exists():
            catalog.append({"path": rel, "description": str(text)})
            seen.add(rel)

    meta_path = run_dir / "data" / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    for key, (raw_desc, comp_desc) in _NATIVE.items():
        entry = meta.get(key) or {}
        raw = entry.get("file")
        if raw and (run_dir / raw).exists() and raw not in seen:
            catalog.append({"path": raw, "description": raw_desc})
            seen.add(raw)
        comp = (entry.get("composite") or {}).get("file")
        if comp and (run_dir / comp).exists() and comp not in seen:
            catalog.append({"path": comp, "description": comp_desc})
            seen.add(comp)

    for photo in extra_images or []:
        photo = Path(photo)
        if not photo.exists():
            continue
        from core.describer import describe_screenshot

        d = describe_screenshot(
            photo.read_bytes(),
            {"variant": "engineer photo", "recipe": "", "step": "engineer-photo",
             "intent": "a photo supplied by the sales engineer"},
        )
        catalog.append({"path": str(photo), "description": d["description"]})

    return catalog


def collect_holes(jobs) -> list[Hole]:
    holes = []
    for job in jobs:
        for i, img in enumerate(job.images):
            holes.append(Hole(
                id=f"{job.id}#{i}",
                slide_title=job.title or job.id,
                expects=" ".join(str(img["expects"]).split()),
                optional=bool(img.get("optional")),
            ))
    return holes


# ------------------------------------------------------------- model calls

ASSIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hole": {"type": "string"},
                    "path": {"type": ["string", "null"],
                             "description": "catalog path, or null when nothing fits"},
                    "confidence": {"type": "string", "enum": ["high", "low"]},
                    "reason": {"type": "string"},
                },
                "required": ["hole", "path", "confidence", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["assignments"],
    "additionalProperties": False,
}

ASSIGN_PROMPT = """You are placing images into a customer-facing camera inspection report.

Below: every content hole (with the slide it sits on and what it expects),
then every available image with its description. Assign each hole the image
that shows what it expects.

Rules:
- Reason JOINTLY: the best global assignment, not the best per-hole pick —
  if two holes want similar images, decide both together.
- Each image may be used for AT MOST ONE hole.
- A hole with no genuinely fitting image gets null. Never force a fit.
- Model names matter: a hole for one model must not receive another model's
  screen.
- Mark confidence "low" when the description leaves real doubt.

HOLES:
{holes}

IMAGES:
{images}"""

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {"match": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["match", "reason"],
    "additionalProperties": False,
}

# Identity, not fullness — verbatim v1 lesson (see module docstring).
VERIFY_PROMPT = """This image was chosen for a slide of a customer-facing inspection report.

The slide: "{title}"
The hole expects: {expects}

Judge ONE thing: is this the screen/content the hole expects? Mismatched
model names or types, or the wrong screen entirely, mean match = false.

An EMPTY image area does NOT mean match = false. If it is the expected
screen, it matches even when the picture inside is blank, black or grey — a
recipe legitimately has nothing to show when a step is disabled or no capture
was triggered. Cropping, resolution and cosmetic differences are not
mismatches either. Answer with match and a one-sentence reason."""


def assign_call(holes: list[Hole], catalog: list[dict]) -> list[dict]:
    from core import llm

    hole_text = "\n".join(
        f"- {h.id} (slide: {h.slide_title}){' [optional]' if h.optional else ''}: {h.expects}"
        for h in holes
    )
    img_text = "\n".join(f"- {c['path']}: {' '.join(c['description'].split())[:400]}"
                         for c in catalog)
    out = llm.complete(
        ASSIGN_PROMPT.format(holes=hole_text, images=img_text),
        schema=ASSIGN_SCHEMA, max_tokens=4000,
    )
    return out["assignments"]


def verify_call(run_dir: Path, hole: Hole, rel_path: str) -> dict:
    from core import llm
    from core.llm import downscale_for_vision

    p = Path(rel_path)
    data = (p if p.is_absolute() else Path(run_dir) / rel_path).read_bytes()
    return llm.complete(
        VERIFY_PROMPT.format(title=hole.slide_title, expects=hole.expects),
        schema=VERIFY_SCHEMA, images=[downscale_for_vision(data)], max_tokens=800,
    )


# ------------------------------------------------------------ orchestration


def match(run_dir: Path, jobs, extra_images=None, log=print) -> MatchResult:
    """Global assignment, uniqueness enforced in code (never trusted), vision
    verification on low-confidence picks, one repair round for anything
    rejected or conflicted. Deterministic bookkeeping throughout."""
    holes = collect_holes(jobs)
    result = MatchResult()
    if not holes:
        return result
    catalog = build_catalog(run_dir, extra_images)
    if not catalog:
        for h in holes:
            result.assignments[h.id] = None
            result.report.append({"hole": h.id, "path": None, "reason": "empty catalog"})
        return result

    by_id = {h.id: h for h in holes}
    valid_paths = {c["path"] for c in catalog}
    raw = assign_call(holes, catalog)

    used: dict[str, str] = {}          # path -> hole that holds it
    pending_repair: list[Hole] = []
    for a in raw:
        h = by_id.get(a["hole"])
        if h is None:
            continue
        path = a.get("path")
        if path is not None and path not in valid_paths:
            # Hallucinated path — treat as unmatched, repair round decides.
            result.report.append({"hole": h.id, "path": None,
                                  "reason": f"assigner named unknown path {path!r}"})
            pending_repair.append(h)
            continue
        if path is not None and path in used:
            # Uniqueness is enforced HERE, not assumed from the prompt.
            result.report.append({"hole": h.id, "path": None,
                                  "reason": f"conflict: {path} already used by {used[path]}"})
            pending_repair.append(h)
            continue
        if path is not None and a.get("confidence") == "low":
            verdict = verify_call(run_dir, h, path)
            if not verdict.get("match"):
                result.report.append({"hole": h.id, "path": None,
                                      "reason": f"verification rejected: {verdict.get('reason', '')[:120]}"})
                pending_repair.append(h)
                continue
        if path is not None:
            used[path] = h.id
        result.assignments[h.id] = path
        result.report.append({"hole": h.id, "path": path,
                              "reason": a.get("reason", ""), "confidence": a.get("confidence")})

    # every hole the assigner never mentioned also goes to repair
    for h in holes:
        if h.id not in result.assignments and h not in pending_repair:
            pending_repair.append(h)

    if pending_repair:
        remaining = [c for c in catalog if c["path"] not in used]
        if remaining:
            log(f"  matching: repair round for {len(pending_repair)} hole(s)")
            for a in assign_call(pending_repair, remaining):
                h = by_id.get(a["hole"])
                path = a.get("path")
                if h is None or (path is not None and (path not in valid_paths or path in used)):
                    continue
                if path is not None:
                    verdict = verify_call(run_dir, h, path)
                    if not verdict.get("match"):
                        path = None
                if path is not None:
                    used[path] = h.id
                result.assignments[h.id] = path
                result.report.append({"hole": h.id, "path": path,
                                      "reason": "repair round: " + a.get("reason", "")})
        for h in pending_repair:
            result.assignments.setdefault(h.id, None)

    return result
