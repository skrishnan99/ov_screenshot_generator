#!/usr/bin/env python3
"""Deck specs: the YAML structure layer of the v2 generator.

A deck is compiled from a spec. The shipped specs/default-deck.yaml is the
base spec and compiles verbatim; structure never comes from prose interpreted
per run — that was v1's central reliability leak (missing closing slides,
order drift, sections silently collapsed). Here structure is DATA:

    load_spec()      -> dict           (variant-resolved, schema-validated)
    build_context()  -> Context        (normalized run facts for conditions)
    expand()         -> [SlideJob]     (repeats, conditions, steps, interp)

Everything in this module is pure and deterministic — no model calls. The
model only ever touches a deck through content.py (token text), matching.py
(image assignment) and arrange.py (tier-3/4 layout), each behind its own
validation.

The spec language is deliberately tiny. Constructs: `layout` | `skeleton` |
`tier: 3|4` | `closing: default`, `tokens`, `images[].expects/optional`,
`repeat: models` (+ `where`), `when`, `step`, `{path}` interpolation. Every
construct beyond these is a proposal, not a default.

GOTCHA (conditions): specs may only reference the NORMALIZED context built
here — never raw extractor facts. Fact property names are vision-derived and
drift between runs ("skip_aligner" vs "aligner_skipped", "on" vs true); a
condition keyed on them would pass in the demo and silently mis-evaluate on
the next camera. Unknown context keys are a load-time error; known keys that
this run cannot resolve evaluate False and are RECORDED, never silent.
"""

from __future__ import annotations

import copy
import datetime
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parent.parent
SPEC_DIR = SKILL / "specs"

# Layouts a spec may name, with the tokens/images each accepts. The emitter
# (deckgen.py) maps these onto ovdeck's methods; keeping the surface listed
# here means a typo'd layout or token fails at load time, not mid-build.
LAYOUTS: dict[str, dict] = {
    "title_slide": {"tokens": {"title", "subtitle", "meta"}, "images": (0, 1)},
    "statement": {"tokens": {"title", "intro", "card_title", "bullets", "badge"}, "images": (0, 0)},
    "figure": {"tokens": {"title", "caption", "chips", "note", "subtitle"}, "images": (1, 1)},
    "split": {"tokens": {"title", "card_title", "para", "bullets", "chips", "subtitle"}, "images": (1, 1)},
    "two_up": {"tokens": {"title", "caption", "left_caption", "right_caption", "subtitle"}, "images": (2, 2)},
    "flow": {"tokens": {"title", "nodes", "rule", "caption"}, "images": (0, 0)},
    "rows": {"tokens": {"title", "entries", "intro"}, "images": (0, 0)},
    "cards": {"tokens": {"title", "cards", "subtitle"}, "images": (0, 0)},
}

TOKEN_SHAPES = ("text", "lines", "pairs")

# Context paths a `when:` may reference, each with a documented derivation in
# build_context(). This whitelist IS the conditions API.
CONTEXT_KEYS = {
    "camera.variant", "camera.model", "camera.title", "camera.ui_version",
    "recipe.name", "date",
    "models.count", "models.classification", "models.segmentation",
    "aligner.skipped", "trigger.manual",
}

_INTERP_RE = re.compile(r"\{([a-z_.]+)\}")


class SpecError(ValueError):
    """A spec that cannot be compiled. Raised at load/expand time with the
    slide id and field named — never mid-build."""


# --------------------------------------------------------------------------
# context: normalized run facts
# --------------------------------------------------------------------------


@dataclass
class Context:
    values: dict[str, object]
    unresolved: list[str] = field(default_factory=list)

    def get(self, path: str):
        """(value, resolved). Unknown paths raise — they should have been
        rejected at validation; reaching here means a code bug."""
        if path not in CONTEXT_KEYS and not path.startswith(("model.", "step")):
            raise SpecError(f"unknown context path {path!r}")
        if path in self.values:
            return self.values[path], True
        return None, False


def _camera_model(variant: str) -> str:
    """ov80i -> OV80i. Falls back to uppercasing the whole variant."""
    m = re.match(r"(ov)(\d+)([a-z]*)", variant or "", re.I)
    return f"OV{m.group(2)}{m.group(3)}" if m else (variant or "").upper()


def build_context(run_dir: Path) -> Context:
    """Normalize a run's manifest + meta into the documented context keys.

    Derivations live here and only here. A key whose source is absent in
    this run is simply not set — Context.get reports it unresolved and any
    condition on it evaluates False, recorded in the plan.
    """
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "data" / "manifest.json").read_text())
    meta_path = run_dir / "data" / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    # facts: first value wins per (subject, property) — later steps re-state
    # the same properties and the earliest capture is closest to the source.
    facts: dict[tuple[str, str], str] = {}
    for f in meta.get("facts", []):
        facts.setdefault((f.get("subject", ""), f.get("property", "")), str(f.get("value", "")))

    def fact(subject: str, prop: str) -> str | None:
        return facts.get((subject, prop))

    v: dict[str, object] = {}
    variant = manifest.get("variant") or ""
    if variant:
        v["camera.variant"] = variant
        v["camera.model"] = _camera_model(variant)
        v["camera.title"] = fact("camera", "product_name") or f"{_camera_model(variant)} AI Vision System"
    if manifest.get("ui_version"):
        v["camera.ui_version"] = manifest["ui_version"]
    recipe = fact("recipe", "name") or manifest.get("recipe_input")
    if recipe:
        v["recipe.name"] = recipe
    v["date"] = datetime.date.today().strftime("%Y.%m.%d")

    models = meta.get("models") or []
    v["models"] = models  # for repeat expansion, not for `when`
    v["models.count"] = len(models)
    for t in ("classification", "segmentation"):
        v[f"models.{t}"] = sum(1 for m in models if m.get("type") == t)

    skip = fact("recipe", "skip_aligner")
    if skip is not None:
        v["aligner.skipped"] = skip.strip().lower() in ("on", "true", "yes", "checked", "enabled")
    trig = fact("recipe", "trigger_mode")
    if trig is not None:
        v["trigger.manual"] = "manual" in trig.lower()

    unresolved = sorted(k for k in CONTEXT_KEYS if k not in v)
    return Context(values=v, unresolved=unresolved)


# --------------------------------------------------------------------------
# load + validate
# --------------------------------------------------------------------------


def load_spec(path: str | Path | None = None, variant: str | None = None) -> dict:
    """The base spec, variant-resolved like the skeleton store: an explicit
    path wins; otherwise specs/default-deck.<variant>.yaml when it exists,
    else specs/default-deck.yaml."""
    if path:
        p = Path(path)
    else:
        p = SPEC_DIR / f"default-deck.{variant}.yaml" if variant else None
        if not p or not p.exists():
            p = SPEC_DIR / "default-deck.yaml"
    if not p.exists():
        raise SpecError(f"spec not found: {p}")
    spec = yaml.safe_load(p.read_text())
    validate(spec)
    return spec


def _validate_token(sid: str, name: str, val) -> None:
    if isinstance(val, str) or (
        isinstance(val, list) and all(isinstance(x, str) for x in val)
    ):
        return  # literal (possibly interpolated)
    if isinstance(val, dict) and "llm" in val:
        shape = val.get("shape", "text")
        if shape not in TOKEN_SHAPES:
            raise SpecError(f"{sid}.tokens.{name}: shape {shape!r} not in {TOKEN_SHAPES}")
        if not isinstance(val["llm"], str) or not val["llm"].strip():
            raise SpecError(f"{sid}.tokens.{name}: llm brief must be non-empty text")
        return
    raise SpecError(f"{sid}.tokens.{name}: must be a literal, list, or {{llm: brief}}")


def _validate_when(sid: str, when: dict) -> None:
    if not isinstance(when, dict) or not when:
        raise SpecError(f"{sid}.when: must be a non-empty mapping")
    clauses = when.get("any", [when]) if "any" in when else [when]
    for clause in clauses:
        for key, expected in clause.items():
            if key == "any":
                continue
            if key not in CONTEXT_KEYS:
                raise SpecError(
                    f"{sid}.when: {key!r} is not a context key. Conditions may "
                    f"only reference the normalized context ({sorted(CONTEXT_KEYS)})"
                )
            if isinstance(expected, dict):
                bad = set(expected) - {"gte", "lte", "exists"}
                if bad:
                    raise SpecError(f"{sid}.when.{key}: unknown operators {sorted(bad)}")


def validate(spec: dict) -> None:
    if not isinstance(spec, dict) or spec.get("spec_version") != 1:
        raise SpecError("spec_version: 1 is required")
    slides = spec.get("slides")
    if not isinstance(slides, list) or not slides:
        raise SpecError("slides: a non-empty list is required")

    seen: set[str] = set()
    for s in slides:
        if not isinstance(s, dict):
            raise SpecError("every slide must be a mapping")
        if s.get("closing") == "default":
            continue  # expands later; carries nothing else
        sid = s.get("id")
        if not sid or not isinstance(sid, str):
            raise SpecError(f"slide missing id: {s}")
        if sid in seen:
            raise SpecError(f"duplicate slide id {sid!r}")
        seen.add(sid)

        kinds = [k for k in ("layout", "skeleton", "tier") if k in s]
        if len(kinds) != 1:
            raise SpecError(f"{sid}: exactly one of layout/skeleton/tier is required")
        kind = kinds[0]

        images = s.get("images", [])
        if not isinstance(images, list):
            raise SpecError(f"{sid}.images: must be a list")
        for img in images:
            if not isinstance(img, dict) or not img.get("expects", "").strip():
                raise SpecError(f"{sid}.images: every hole needs a non-empty `expects`")

        if kind == "layout":
            name = s["layout"]
            if name not in LAYOUTS:
                raise SpecError(f"{sid}: unknown layout {name!r} (have {sorted(LAYOUTS)})")
            allowed = LAYOUTS[name]["tokens"]
            for tname, tval in (s.get("tokens") or {}).items():
                if tname not in allowed:
                    raise SpecError(
                        f"{sid}.tokens.{tname}: layout {name!r} accepts {sorted(allowed)}"
                    )
                _validate_token(sid, tname, tval)
            lo, hi = LAYOUTS[name]["images"]
            n_req = sum(1 for i in images if not i.get("optional"))
            if not (lo <= n_req <= hi and len(images) <= hi):
                raise SpecError(
                    f"{sid}: layout {name!r} takes {lo}-{hi} image(s); "
                    f"spec declares {len(images)} ({n_req} required)"
                )
        elif kind == "skeleton":
            from template_slides import TEMPLATE_SLIDES

            if s["skeleton"] not in TEMPLATE_SLIDES:
                raise SpecError(f"{sid}: unknown skeleton {s['skeleton']!r}")
            if len(images) > 1:
                raise SpecError(f"{sid}: skeletons take at most one image hole")
        else:  # tier
            if s["tier"] not in (3, 4):
                raise SpecError(f"{sid}: tier must be 3 or 4")
            if s["tier"] == 3 and not (images or s.get("tokens")):
                raise SpecError(f"{sid}: tier 3 fixes content — declare images and/or tokens")
            if s["tier"] == 4 and not str(s.get("brief", "")).strip():
                raise SpecError(f"{sid}: tier 4 requires a `brief`")
            for tname, tval in (s.get("tokens") or {}).items():
                _validate_token(sid, tname, tval)

        if "when" in s:
            _validate_when(sid, s["when"])
        if "repeat" in s and s["repeat"] != "models":
            raise SpecError(f"{sid}.repeat: only 'models' is supported")
        if "where" in s and "repeat" not in s:
            raise SpecError(f"{sid}: `where` requires `repeat`")

        # every interpolation must resolve to a known namespace
        for text in _iter_strings(s):
            for ref in _INTERP_RE.findall(text):
                ok = (
                    ref in CONTEXT_KEYS
                    or ref == "step"
                    or (ref.startswith("model.") and "repeat" in s)
                )
                if not ok:
                    raise SpecError(
                        f"{sid}: interpolation {{{ref}}} is not a context key"
                        + (" (model.* needs repeat: models)" if ref.startswith("model.") else "")
                    )


def _iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


# --------------------------------------------------------------------------
# expansion
# --------------------------------------------------------------------------


@dataclass
class SlideJob:
    """One concrete slide to build. `origin` is the spec entry id; repeated
    slides share an origin and carry their model."""

    id: str
    origin: str
    kind: str                      # layout | skeleton | tier3 | tier4
    layout: str | None = None
    skeleton: str | None = None
    tier: int | None = None
    title: str = ""
    tokens: dict = field(default_factory=dict)
    images: list = field(default_factory=list)
    brief: str = ""
    model: dict | None = None
    step: int | None = None
    wants_step: bool = False


def _eval_when(when: dict, ctx: Context, notes: list[str], sid: str) -> bool:
    clauses = when["any"] if "any" in when else [when]
    results = []
    for clause in clauses:
        ok = True
        for key, expected in clause.items():
            val, resolved = ctx.get(key)
            if not resolved:
                notes.append(f"{sid}: condition unresolved: {key} (no source in run) -> false")
                ok = False
                continue
            if isinstance(expected, dict):
                if "exists" in expected:
                    ok &= bool(val) is bool(expected["exists"])
                if "gte" in expected:
                    ok &= isinstance(val, (int, float)) and val >= expected["gte"]
                if "lte" in expected:
                    ok &= isinstance(val, (int, float)) and val <= expected["lte"]
            else:
                a = str(val).strip().lower() if not isinstance(val, bool) else val
                b = str(expected).strip().lower() if not isinstance(expected, bool) else expected
                ok &= a == b
        results.append(ok)
    return any(results)


def _interp(text: str, ctx: Context, model: dict | None, step: int | None, sid: str) -> str:
    def sub(m):
        ref = m.group(1)
        if ref == "step":
            # Numbers are assigned AFTER matching (finalize_steps), so that a
            # slide dropped for an unmatched required image cannot leave a
            # hole in "Step N". Until then the placeholder survives verbatim.
            return "{step}" if step is None else str(step)
        if ref.startswith("model."):
            key = ref[len("model."):]
            if model is None or key not in model:
                raise SpecError(f"{sid}: {{{ref}}} has no value for this model")
            return str(model[key])
        val, resolved = ctx.get(ref)
        if not resolved:
            raise SpecError(f"{sid}: {{{ref}}} is unresolved in this run")
        return str(val)

    return _INTERP_RE.sub(sub, text)


def _interp_deep(obj, ctx, model, step, sid):
    if isinstance(obj, str):
        return _interp(obj, ctx, model, step, sid)
    if isinstance(obj, list):
        return [_interp_deep(v, ctx, model, step, sid) for v in obj]
    if isinstance(obj, dict):
        # llm briefs are interpolated too ("{model.name}'s labelled grid"),
        # shape/flags pass through untouched.
        return {k: (_interp_deep(v, ctx, model, step, sid) if k != "shape" else v)
                for k, v in obj.items()}
    return obj


def expand(spec: dict, ctx: Context) -> tuple[list[SlideJob], list[dict]]:
    """Spec -> concrete SlideJobs. Deterministic: conditions evaluated,
    repeats fanned out over the model roster, `closing: default` expanded in
    template order, step numbers assigned over the slides that SURVIVED
    (numbering after conditions is what keeps Step N contiguous — a v1
    lesson), every string interpolated. Returns (jobs, skip_report)."""
    notes: list[str] = []
    skipped: list[dict] = []
    pre: list[tuple[dict, dict | None]] = []  # (entry, model)

    for entry in spec["slides"]:
        if entry.get("closing") == "default":
            from template_slides import DEFAULT_CLOSING

            for name in DEFAULT_CLOSING:
                pre.append(({"id": f"closing_{name}", "skeleton": name}, None))
            continue

        sid = entry["id"]
        if "when" in entry and not _eval_when(entry["when"], ctx, notes, sid):
            skipped.append({"id": sid, "skipped": f"when {entry['when']} -> false",
                            "notes": [n for n in notes if n.startswith(sid)]})
            continue

        if entry.get("repeat") == "models":
            models = ctx.values.get("models") or []
            where = entry.get("where") or {}
            hits = [m for m in models
                    if all(str(m.get(k, "")).lower() == str(v).lower() for k, v in where.items())]
            if not hits:
                skipped.append({"id": sid, "skipped": f"repeat: models matched none (where={where})"})
            for m in hits:
                pre.append((entry, m))
        else:
            pre.append((entry, None))

    jobs: list[SlideJob] = []
    for entry, model in pre:
        sid = entry["id"]
        n = None  # numbering deferred to finalize_steps(), after match-skips
        suffix = f"_{model['slug']}" if model and model.get("slug") else ""
        kind = ("skeleton" if "skeleton" in entry
                else "layout" if "layout" in entry
                else f"tier{entry['tier']}")
        job = SlideJob(
            id=f"{sid}{suffix}",
            origin=sid,
            kind=kind,
            layout=entry.get("layout"),
            skeleton=entry.get("skeleton"),
            tier=entry.get("tier"),
            title=_interp(entry.get("title", ""), ctx, model, n, sid),
            tokens=_interp_deep(copy.deepcopy(entry.get("tokens") or {}), ctx, model, n, sid),
            images=_interp_deep(copy.deepcopy(entry.get("images") or []), ctx, model, n, sid),
            brief=_interp(entry.get("brief", ""), ctx, model, n, sid),
            model=model,
            step=None,
            wants_step=bool(entry.get("step")),
        )
        jobs.append(job)
    return jobs, skipped + [{"note": x} for x in notes]


def finalize_steps(jobs: list["SlideJob"]) -> None:
    """Assign contiguous step numbers over the slides that SURVIVED both
    conditions and matching, then resolve the deferred {step} placeholders.
    Numbering after every skip is what keeps "Step N" gap-free — a lesson
    v1 paid for twice."""
    n = 0
    for job in jobs:
        if not job.wants_step:
            continue
        n += 1
        job.step = n
        job.title = job.title.replace("{step}", str(n))
        job.tokens = _replace_step(job.tokens, n)


def _replace_step(obj, n: int):
    if isinstance(obj, str):
        return obj.replace("{step}", str(n))
    if isinstance(obj, list):
        return [_replace_step(v, n) for v in obj]
    if isinstance(obj, dict):
        return {k: _replace_step(v, n) for k, v in obj.items()}
    return obj
