#!/usr/bin/env python3
"""Deck specs: the YAML structure layer of the v2 generator.

A deck is compiled from a spec. The shipped specs/default-deck.yaml is the
base spec and compiles verbatim; structure never comes from prose interpreted
per run — that was v1's central reliability leak (missing closing slides,
order drift, sections silently collapsed). Here structure is DATA:

    load_spec()      -> dict           (variant-resolved, schema-validated)
    build_context()  -> Context        (normalized run facts for conditions)
    expand()         -> [SlideJob]     (repeats, conditions, steps, interp)

Everything in this module is pure and deterministic, with ONE deliberate
exception: build_context may make a single small Haiku call to read a
toggle's state out of messy verbatim UI values (eval_toggle_call — a Traton
run recorded skip_aligner as "enabled (toggle ON)" and exact-token parsing
shipped an alignment slide for a skipped aligner). Unambiguous single-token
values never reach the model, and an unreadable state resolves to
"unknown" -> the context key stays unresolved and is RECORDED. Otherwise
the model only touches a deck through content.py (token text), matching.py
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


# Single-token toggle values that need no model to read. Anything outside
# this table ("enabled (toggle ON)", "Skip Aligner is Enabled") goes to the
# Haiku eval below — the extractor records UI values VERBATIM by design, so
# their phrasing is open-ended.
_TOGGLE_CLEAR = {
    "on": True, "enabled": True, "checked": True, "true": True, "yes": True,
    "off": False, "disabled": False, "unchecked": False, "false": False, "no": False,
}

TOGGLE_SCHEMA = {
    "type": "object",
    "properties": {
        "state": {"type": "string", "enum": ["on", "off", "unknown"]},
        "reason": {"type": "string"},
    },
    "required": ["state", "reason"],
    "additionalProperties": False,
}

TOGGLE_PROMPT = """Below are observations recorded VERBATIM from an industrial camera's web UI, \
all concerning the setting "{setting}". They may mix the toggle's own value, banner text, and \
help/description text that merely explains what the setting does.

Decide whether the setting is currently turned ON or OFF for this recipe.
- Judge the actual state only; ignore text that explains the setting without stating its state.
- If the observations genuinely do not reveal the state, answer "unknown". Never guess.

Observations:
{observations}"""


def eval_toggle_call(setting: str, observations: list[str]) -> str:
    """'on' | 'off' | 'unknown' from verbatim UI observations — one small
    text-only Haiku call. Isolated so tests stub it."""
    from core import llm

    out = llm.complete(
        TOGGLE_PROMPT.format(
            setting=setting,
            observations="\n".join(f"- {o}" for o in observations),
        ),
        schema=TOGGLE_SCHEMA,
        max_tokens=300,
        model=llm.HAIKU,
    )
    return out.get("state", "unknown")


def _toggle_state(setting: str, primary: str | None, observations: list[str]) -> bool | None:
    """Resolve a toggle: the primary fact's bare token when unambiguous,
    otherwise the Haiku eval over every observation. None means unknown —
    the caller leaves the context key unresolved (recorded, never guessed)."""
    token = (primary or "").strip().lower()
    if token in _TOGGLE_CLEAR:
        return _TOGGLE_CLEAR[token]
    if not observations:
        return None
    try:
        state = eval_toggle_call(setting, observations)
    except Exception:
        return None
    return {"on": True, "off": False}.get(state)


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
    if not variant:
        # Fallback: the variant is visible top-left on every product screen,
        # so the screenshot descriptions name it. Majority vote across them.
        import collections

        desc_path = run_dir / "deliverables" / "report" / "descriptions.json"
        if desc_path.exists():
            text = desc_path.read_text().lower()
            counts = collections.Counter()
            for cand in ("ov80i", "ov20i", "ov10i"):
                counts[cand] = text.count(cand)
            best, n = counts.most_common(1)[0]
            if n > 0:
                variant = best
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

    models = [dict(m) for m in (meta.get("models") or [])]
    # TRAINED derivation, per model. The Train screen's per-model facts are
    # the primary signal: last_trained is either a date or "Never trained".
    # Fallbacks: a training metric with a real digit, then a training-report
    # screenshot (reports only exist after training). When NO model carries
    # any signal the information is absent, not negative — include all and
    # record it, rather than silently emptying every trained-filtered slide.
    any_signal = False
    for m in models:
        # Slide-text form of the type ("Segmentation"), for titles like
        # "Training — Model 2 (Classification)".
        m["type_title"] = (m.get("type") or "").title()
        subj = f"model: {m.get('name', '')}".lower()
        last = next((val for (sj, pr), val in facts.items()
                     if sj.lower() == subj and "last_trained" in pr.lower()), None)
        metric = any(
            sj.lower() == subj and any(t in pr.lower() for t in ("train", "acc", "iou", "loss"))
            and any(c.isdigit() for c in val)
            for (sj, pr), val in facts.items()
        )
        if last is not None:
            m["trained"] = "never" not in last.lower() and any(c.isdigit() for c in last)
            any_signal = True
        elif metric:
            m["trained"] = True
            any_signal = True
        elif m.get("report_screenshot"):
            m["trained"] = True
            any_signal = True
        else:
            m["trained"] = False
    if models and not any_signal:
        for m in models:
            m["trained"] = True
    v["models"] = models  # for repeat expansion, not for `when`
    v["models.trained_signal"] = any_signal or not models
    v["models.count"] = len(models)
    for t in ("classification", "segmentation"):
        v[f"models.{t}"] = sum(1 for m in models if m.get("type") == t)

    skip_obs = [f"{pr} = {val}" for (sj, pr), val in facts.items()
                if "skip_aligner" in pr.lower()]
    skipped = _toggle_state("Skip Aligner", fact("recipe", "skip_aligner"), skip_obs)
    if skipped is not None:
        v["aligner.skipped"] = skipped
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


def _validate_entry(s: dict, sid: str, in_repeat: bool) -> None:
    """One slide definition — used for top-level entries and block inners."""
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
        if "foreach" in img:
            if img["foreach"] != "models":
                raise SpecError(f"{sid}.images: foreach supports only 'models'")
            if in_repeat:
                raise SpecError(
                    f"{sid}: foreach image holes inside repeat: models is "
                    f"ambiguous — use one or the other"
                )

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
        if any("foreach" in i for i in images):
            pass  # arity depends on the roster; checked at expansion
        elif not (lo <= n_req <= hi and len(images) <= hi):
            raise SpecError(
                f"{sid}: layout {name!r} takes {lo}-{hi} image(s); "
                f"spec declares {len(images)} ({n_req} required)"
            )
    elif kind == "skeleton":
        from template_slides import all_skeleton_names, profile

        if s["skeleton"] not in all_skeleton_names():
            raise SpecError(f"{sid}: unknown skeleton {s['skeleton']!r}")
        prof = profile(s["skeleton"])
        want = set(prof["tokens"])
        have = set(s.get("tokens") or {})
        if have - want:
            raise SpecError(
                f"{sid}: skeleton {s['skeleton']!r} has no token(s) "
                f"{sorted(have - want)}; it has {sorted(want) or 'none'}"
            )
        if want - have:
            # An unfilled {{token}} ships literal braces to a customer.
            raise SpecError(
                f"{sid}: skeleton {s['skeleton']!r} tokens {sorted(want - have)} "
                f"are not provided by the spec"
            )
        for tname, tval in (s.get("tokens") or {}).items():
            _validate_token(sid, tname, tval)
        max_slots = len(prof["slots"])
        if len(images) > max_slots:
            raise SpecError(
                f"{sid}: skeleton {s['skeleton']!r} has {max_slots} image "
                f"slot(s); spec declares {len(images)}"
            )
    else:  # tier
        if s["tier"] not in (3, 4):
            raise SpecError(f"{sid}: tier must be 3 or 4")
        if s["tier"] == 3 and not (images or s.get("tokens")):
            raise SpecError(f"{sid}: tier 3 fixes content — declare images and/or tokens")
        if s["tier"] == 4 and not str(s.get("brief", "")).strip():
            raise SpecError(f"{sid}: tier 4 requires a `brief`")
        for tname, tval in (s.get("tokens") or {}).items():
            _validate_token(sid, tname, tval)
        if "hint" in s and not (s["tier"] == 3 and str(s["hint"]).strip()):
            raise SpecError(f"{sid}: `hint` is a non-empty tier-3 layout hint")

    if "when_model" in s:
        if not in_repeat:
            raise SpecError(f"{sid}: `when_model` only applies inside a repeated block")
        if not isinstance(s["when_model"], dict) or not s["when_model"]:
            raise SpecError(f"{sid}.when_model: must be a non-empty mapping")

    # every interpolation must resolve to a known namespace. A foreach image
    # hole fans over the roster, so ITS strings get model-scope even outside
    # a repeat — the rest of the entry does not (expansion would have no
    # model to interpolate).
    foreach_holes = [i for i in images if "foreach" in i]
    rest = {k: v for k, v in s.items() if k != "images"}
    rest["images"] = [i for i in images if "foreach" not in i]

    def _check(obj, model_ok: bool, where: str) -> None:
        for text in _iter_strings(obj):
            for ref in _INTERP_RE.findall(text):
                ok = (
                    ref in CONTEXT_KEYS
                    or ref == "step"
                    or (ref.startswith("model.") and model_ok)
                )
                if not ok:
                    raise SpecError(
                        f"{where}: interpolation {{{ref}}} is not a context key"
                        + (" (model.* needs repeat: models or a foreach hole)"
                           if ref.startswith("model.") else "")
                    )

    _check(rest, in_repeat, sid)
    for hole in foreach_holes:
        _check(hole, True, f"{sid}.images(foreach)")


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

        if "when" in s:
            _validate_when(sid, s["when"])
        if "repeat" in s and s["repeat"] != "models":
            raise SpecError(f"{sid}.repeat: only 'models' is supported")
        if "where" in s and "repeat" not in s:
            raise SpecError(f"{sid}: `where` requires `repeat`")

        if "slides" in s:
            # A block: a group of slides fanned out together per model, so a
            # model's slides stay adjacent (training then results) instead of
            # all-trainings-then-all-results — the v1 per_model_blocks lesson.
            if s.get("repeat") != "models":
                raise SpecError(f"{sid}: a `slides` block requires repeat: models")
            inner_seen: set[str] = set()
            for inner in s["slides"]:
                iid = inner.get("id")
                if not iid or iid in inner_seen:
                    raise SpecError(f"{sid}: block inners need unique ids")
                inner_seen.add(iid)
                if "repeat" in inner or "slides" in inner or "when" in inner:
                    raise SpecError(
                        f"{sid}.{iid}: block inners may not nest repeat/slides/when "
                        f"(use when_model)"
                    )
                _validate_entry(inner, f"{sid}.{iid}", in_repeat=True)
            continue

        _validate_entry(s, sid, in_repeat="repeat" in s)


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
    hint: str = ""


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


def _model_matches(m: dict, where: dict) -> bool:
    return all(str(m.get(k, "")).lower() == str(v).lower() for k, v in where.items())


def _make_job(entry: dict, model, ctx, sid: str, job_id: str) -> SlideJob:
    kind = ("skeleton" if "skeleton" in entry
            else "layout" if "layout" in entry
            else f"tier{entry['tier']}")
    # foreach image holes fan out over the roster INSIDE one slide — the
    # combined-ROI slide shows every trained model's regions together.
    images = []
    for img in entry.get("images") or []:
        if img.get("foreach") == "models":
            for m in ctx.values.get("models") or []:
                if not _model_matches(m, img.get("where") or {}):
                    continue
                fanned = {k: v for k, v in img.items() if k not in ("foreach", "where")}
                images.append(_interp_deep(copy.deepcopy(fanned), ctx, m, None, sid))
        else:
            images.append(_interp_deep(copy.deepcopy(img), ctx, model, None, sid))
    return SlideJob(
        id=job_id,
        origin=sid,
        kind=kind,
        layout=entry.get("layout"),
        skeleton=entry.get("skeleton"),
        tier=entry.get("tier"),
        title=_interp(entry.get("title", ""), ctx, model, None, sid),
        tokens=_interp_deep(copy.deepcopy(entry.get("tokens") or {}), ctx, model, None, sid),
        images=images,
        brief=_interp(entry.get("brief", ""), ctx, model, None, sid),
        model=model,
        step=None,
        wants_step=bool(entry.get("step")),
        hint=" ".join(str(entry.get("hint", "")).split()),
    )


def expand(spec: dict, ctx: Context) -> tuple[list[SlideJob], list[dict]]:
    """Spec -> concrete SlideJobs. Deterministic: conditions evaluated,
    repeats and blocks fanned out over the model roster (a block keeps each
    model's slides adjacent), foreach image holes fanned inside their slide,
    `closing: default` expanded in template order, every string interpolated.
    Step numbers are deferred to finalize_steps(), after match-skips.
    Returns (jobs, skip_report)."""
    notes: list[str] = []
    skipped: list[dict] = []
    jobs: list[SlideJob] = []

    for entry in spec["slides"]:
        if entry.get("closing") == "default":
            from template_slides import DEFAULT_CLOSING

            for name in DEFAULT_CLOSING:
                jobs.append(_make_job({"id": f"closing_{name}", "skeleton": name},
                                      None, ctx, f"closing_{name}", f"closing_{name}"))
            continue

        sid = entry["id"]
        if "when" in entry and not _eval_when(entry["when"], ctx, notes, sid):
            skipped.append({"id": sid, "skipped": f"when {entry['when']} -> false",
                            "notes": [n for n in notes if n.startswith(sid)]})
            continue

        if entry.get("repeat") == "models":
            models = ctx.values.get("models") or []
            where = entry.get("where") or {}
            hits = [m for m in models if _model_matches(m, where)]
            if not hits:
                skipped.append({"id": sid,
                                "skipped": f"repeat: models matched none (where={where})"})
                continue
            inners = entry.get("slides") or [entry]
            for m in hits:
                suffix = f"_{m['slug']}" if m.get("slug") else f"_{m.get('name', '')}"
                for inner in inners:
                    iid = inner["id"] if inner is not entry else sid
                    if "when_model" in inner and not _model_matches(m, inner["when_model"]):
                        continue  # silent per-model selection (e.g. cls vs seg results)
                    jobs.append(_make_job(inner, m, ctx, f"{sid}.{iid}" if inner is not entry else sid,
                                          f"{iid}{suffix}"))
        else:
            jobs.append(_make_job(entry, None, ctx, sid, sid))

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
