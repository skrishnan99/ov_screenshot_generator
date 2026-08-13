#!/usr/bin/env python3
"""deckgen — compile a deck spec into a finished, gated .pptx.

    uv run --project "$PLUGIN_ROOT" python deckgen.py \
        --run runs/<ts> [--spec specs/default-deck.yaml] [--out out/report.pptx] \
        [--request "user's literal request"] [--notes notes.md] [--photos dir] \
        [--plan-only]

Pipeline (each stage pure, each recorded in the plan):

    load spec -> [adapt iff --request] -> context -> expand ->
    MATCH images -> drop slides whose REQUIRED hole is unmatched ->
    number steps over the survivors -> resolve content (register-governed) ->
    emit via ovdeck / skeleton_slide -> save (gates) -> audit files

Ordering gotchas carried from v1:
- match BEFORE content, so captions describe the image actually placed;
- steps numbered AFTER match-skips, so "Step N" stays contiguous;
- absence is silent: an optional hole with no image simply isn't there.

Outputs beside the deck: deck-spec.resolved.yaml (what was compiled),
deck-plan.json (every slide, token, match and skip with reasons),
spec-diff.json (only when a mutation was requested and applied).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL / "scripts"
PLUGIN_ROOT = SKILL.parent.parent
for p in (str(SCRIPTS), str(PLUGIN_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import arrange as arrange_mod  # noqa: E402
import content as content_mod  # noqa: E402
import matching as matching_mod  # noqa: E402
from deckspec import SlideJob, build_context, expand, finalize_steps, load_spec  # noqa: E402


# --------------------------------------------------------------- token glue


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return [ln.strip() for ln in str(value).splitlines() if ln.strip()]


def _as_pairs(value) -> list[tuple[str, str]]:
    pairs = []
    for ln in _as_list(value):
        left, _, right = ln.partition("|")
        pairs.append((left.strip(), right.strip()))
    return pairs


def token_value(job: SlideJob, name: str, resolved: dict[str, str], default=""):
    """A token's final value: literal from the spec, or the resolver's text.
    An em dash means 'no data' and collapses to the default (usually empty) —
    absence is silent on slides."""
    raw = job.tokens.get(name)
    if raw is None:
        return default
    if isinstance(raw, (str, list)):
        return raw
    v = resolved.get(f"{job.id}.{name}", "")
    return default if v.strip() == "—" else v


# ----------------------------------------------------------------- emitters


def _emit_layout(d, job: SlideJob, images: list[str], resolved: dict[str, str]) -> None:
    t = lambda name, default="": token_value(job, name, resolved, default)  # noqa: E731
    title = job.title or t("title")
    if job.layout == "title_slide":
        d.title_slide(t("title") or title, t("subtitle"),
                      meta=_as_list(t("meta", [])),
                      image=images[0] if images else None)
    elif job.layout == "statement":
        d.statement(t("title") or title, t("intro"),
                    card_title=t("card_title"), bullets=_as_list(t("bullets", [])),
                    badge=t("badge"))
    elif job.layout == "figure":
        d.figure(title, images[0], caption=t("caption"),
                 chips=_as_list(t("chips", [])), note=t("note"), subtitle=t("subtitle"))
    elif job.layout == "split":
        d.split(title, images[0], card_title=t("card_title"), para=t("para"),
                bullets=_as_list(t("bullets", [])), chips=_as_list(t("chips", [])),
                footnote=t("footnote"), subtitle=t("subtitle"))
    elif job.layout == "two_up":
        d.two_up(title, images[0], images[1], caption=t("caption"),
                 left_caption=t("left_caption"), right_caption=t("right_caption"),
                 subtitle=t("subtitle"))
    elif job.layout == "flow":
        rule = _as_list(t("rule", []))
        d.flow(title, nodes=_as_pairs(t("nodes")),
               cards=[("The rule", rule)] if rule else (), caption=t("caption"))
    elif job.layout == "rows":
        d.rows(title, entries=_as_pairs(t("entries")), intro=t("intro"))
    elif job.layout == "cards":
        d.cards(title, cards=_as_pairs(t("cards")), subtitle=t("subtitle"))
    else:  # unreachable: validated at load
        raise ValueError(f"no emitter for layout {job.layout!r}")


def _emit_arranged(d, planned: list[dict], run_dir: Path) -> None:
    for s in planned:
        text = s.get("text", {})
        imgs = [str(run_dir / p) if not Path(p).is_absolute() else p
                for p in s.get("images", [])]
        if s["layout"] == "figure":
            d.figure(s["title"], imgs[0], caption=text.get("caption", ""))
        elif s["layout"] == "split":
            d.split(s["title"], imgs[0], card_title=text.get("card_title", ""),
                    intro=text.get("intro", ""), para=text.get("para", ""),
                    bullets=_as_list(text.get("bullets", "")),
                    footnote=text.get("footnote", ""))
        elif s["layout"] == "two_up":
            d.two_up(s["title"], imgs[0], imgs[1], caption=text.get("caption", ""),
                     left_caption=text.get("left_caption", ""),
                     right_caption=text.get("right_caption", ""))
        elif s["layout"] == "rows":
            d.rows(s["title"], entries=_as_pairs(text.get("entries", "")),
                   intro=text.get("intro", ""))
        elif s["layout"] == "statement":
            d.statement(s["title"], text.get("intro", ""),
                        card_title=text.get("card_title", "Summary"),
                        bullets=_as_list(text.get("bullets", "")))


# ---------------------------------------------------------------- pipeline


def compile_deck(
    run_dir: Path,
    out_path: Path,
    spec_path: Path | None = None,
    request: str | None = None,
    notes: str = "",
    photos: list[Path] | None = None,
    plan_only: bool = False,
    log=print,
) -> dict:
    run_dir = Path(run_dir)
    out_path = Path(out_path)
    plan: dict = {"run": str(run_dir), "slides": [], "skipped": []}

    # Whose contact signs the thank-you slide: the SE profile, or the
    # visibly generic placeholders. Recorded so a placeholder contact is
    # surfaced in the summary, never shipped unnoticed.
    from core.engineer import load_profile

    contact, contact_source = load_profile()
    plan["contact"] = {"source": contact_source, "name": contact["name"]}
    if contact_source != "profile":
        log(f"  contact slide: no engineer profile ({contact_source}) — "
            f"generic placeholders will show; set it via /ov-test-report "
            f"or ~/.ov-report-generator/engineer.json")

    # Select the LLM backend up front, or complete() falls through to the
    # raw-API default and dies on a missing ANTHROPIC_API_KEY. agent-sdk runs
    # everything on the Claude Code login — same default as the extractor.
    import os

    from core import llm

    llm.select_backend(os.environ.get("SG_LLM_BACKEND", "agent-sdk"))

    ctx = build_context(run_dir)
    plan["context_unresolved"] = ctx.unresolved
    spec = load_spec(spec_path, variant=ctx.values.get("camera.variant"))

    if request and request.strip():
        from adapt import adapt

        spec, diff = adapt(spec, request, log=log)
        plan["spec_diff"] = diff
    jobs, skip_report = expand(spec, ctx)
    plan["skipped"].extend(skip_report)

    # ---- match first (captions must describe the image actually placed)
    m = matching_mod.match(run_dir, jobs, extra_images=photos, log=log)
    plan["matching"] = m.report

    survivors: list[SlideJob] = []
    for job in jobs:
        missing_required = [
            i for i, img in enumerate(job.images)
            if not img.get("optional") and not m.assignments.get(f"{job.id}#{i}")
        ]
        if missing_required:
            plan["skipped"].append({
                "id": job.id,
                "skipped": f"required image hole(s) {missing_required} unmatched "
                           f"(see matching report)",
            })
            continue
        if job.kind in ("tier3", "tier4") and job.images and not any(
            m.assignments.get(f"{job.id}#{i}") for i in range(len(job.images))
        ):
            # An adaptive slide whose every image hole (all optional) came up
            # empty has no evidence to show — a real build shipped a
            # title-only slide this way. Dropping HERE, before numbering,
            # keeps "Step N" contiguous.
            plan["skipped"].append({
                "id": job.id,
                "skipped": "adaptive slide: no image hole matched anything",
            })
            continue
        survivors.append(job)

    finalize_steps(survivors)

    if plan_only:
        plan["slides"] = [_job_record(j, m) for j in survivors]
        return plan

    # ---- tier 4 generates its content through the same governed resolver
    for job in survivors:
        if job.kind == "tier4" and "text" not in job.tokens:
            job.tokens["text"] = {"llm": job.brief, "shape": "text", "max_chars": 500}

    material = content_mod.build_material(run_dir, notes=notes)
    resolved = content_mod.resolve(survivors, material, m.assignments, log=log)

    # ---- emit. The save() gates hard-fail rather than ship a broken slide;
    # when they reject a build the defect is almost always one stochastic
    # arrangement, so ONE full re-emit with fresh arrangements is retried
    # before giving up. Matching and resolved content are fixed by then —
    # only the tier-3/4 layouts redraw.
    from ovdeck import Deck, LayoutError

    def _emit_once(gate_feedback: str = ""):
        d = Deck(str(out_path))
        records: list[dict] = []
        emit_skips: list[dict] = []
        for job in survivors:
            rec = _job_record(job, m, resolved)
            images = [str(run_dir / m.assignments[f"{job.id}#{i}"])
                      for i in range(len(job.images))
                      if m.assignments.get(f"{job.id}#{i}")]
            if job.kind == "skeleton":
                # Skeleton {{tokens}} take the resolver's RAW value: on a stat
                # card an em dash IS the designed no-data mark (v1's
                # deployment_time brief says 'otherwise exactly —'), unlike
                # layout copy where absence means omitting the line.
                skel_tokens = {}
                for name, raw in job.tokens.items():
                    if isinstance(raw, (str, list)):
                        skel_tokens[name] = raw if isinstance(raw, str) else "\n".join(raw)
                    else:
                        skel_tokens[name] = resolved.get(f"{job.id}.{name}", "—")
                d.skeleton_slide(job.skeleton, images=images or None,
                                 tokens=skel_tokens or None)
            elif job.kind == "layout":
                _emit_layout(d, job, images, resolved)
            else:  # tier3 / tier4: fixed content, arranged
                text = {}
                for name in job.tokens:
                    v = token_value(job, name, resolved)
                    if isinstance(v, list):
                        v = "\n".join(v)
                    if v:
                        text[name] = v
                rel_images = [m.assignments[f"{job.id}#{i}"]
                              for i in range(len(job.images))
                              if m.assignments.get(f"{job.id}#{i}")]
                if not rel_images and not any(v.strip() for v in text.values()):
                    # Nothing matched, nothing resolved: a title-only slide is
                    # worse than no slide (a real build shipped one). Skip +
                    # record.
                    emit_skips.append({
                        "id": job.id,
                        "skipped": "tier-3/4 slide with no matched images and "
                                   "no resolved text",
                    })
                    continue
                # Literal spec tokens are the author's exact words — the
                # arranger must carry them verbatim (code-enforced).
                carry = tuple(
                    "\n".join(v) if isinstance(v, list) else v
                    for v in (job.tokens.get(n) for n in job.tokens)
                    if isinstance(v, (str, list)) and str(v).strip()
                )
                planned = arrange_mod.arrange(job.title or job.id, rel_images, text,
                                              hint=job.hint, feedback=gate_feedback,
                                              log=log, must_carry=carry)
                rec["arranged"] = planned
                _emit_arranged(d, planned, run_dir)
            records.append(rec)
        return records, emit_skips, d.save()

    try:
        records, emit_skips, saved = _emit_once()
    except LayoutError as e:
        # Tell the retry WHAT failed: a deterministic overflow (fixed
        # content too tall for the chosen layout) redraws identically when
        # the arranger is blind, but with the gate's issues in hand it can
        # pick a roomier layout or split across slides.
        issues = "; ".join(str(i) for i in getattr(e, "issues", [])) or str(e)
        log(f"  emit: layout gate rejected the build ({issues}); "
            f"re-emitting with the issues fed to the arranger")
        plan["emit_retried"] = issues
        records, emit_skips, saved = _emit_once(
            "the previously built deck FAILED the overflow gate on: "
            + issues[:500]
            + ". Prefer arrangements with more text room — statement or rows "
              "layouts, or the content split across two slides."
        )

    plan["slides"].extend(records)
    plan["skipped"].extend(emit_skips)
    plan["deck"] = str(saved)
    return plan


def _job_record(job: SlideJob, m, resolved: dict[str, str] | None = None) -> dict:
    rec = {
        "id": job.id, "origin": job.origin, "kind": job.kind, "title": job.title,
        "images": [
            {"expects": img["expects"], "optional": bool(img.get("optional")),
             "path": m.assignments.get(f"{job.id}#{i}")}
            for i, img in enumerate(job.images)
        ],
    }
    if resolved:
        rec["tokens"] = {name: token_value(job, name, resolved)
                         for name in job.tokens}
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="Extractor run directory")
    ap.add_argument("--spec", help="Spec path (default: shipped base spec, variant-resolved)")
    ap.add_argument("--out", default="out/report.pptx")
    ap.add_argument("--request", help="The user's literal request, ONLY when they "
                                      "asked to deviate from the default deck")
    ap.add_argument("--notes", help="Engineer notes file (verbatim material)")
    ap.add_argument("--photos", help="Directory of engineer photos to consider")
    ap.add_argument("--plan-only", action="store_true",
                    help="Match + expand and write the plan; build nothing")
    args = ap.parse_args()

    notes = Path(args.notes).read_text() if args.notes else ""
    photos = sorted(Path(args.photos).glob("*")) if args.photos else None
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    plan = compile_deck(Path(args.run), out, spec_path=args.spec,
                        request=args.request, notes=notes, photos=photos,
                        plan_only=args.plan_only)

    (out.parent / "deck-plan.json").write_text(json.dumps(plan, indent=2))
    resolved_slides = [dataclasses.asdict(j) if dataclasses.is_dataclass(j) else j
                       for j in plan["slides"]]
    (out.parent / "deck-spec.resolved.yaml").write_text(
        yaml.safe_dump({"slides": resolved_slides}, sort_keys=False, width=90))
    if plan.get("spec_diff"):
        (out.parent / "spec-diff.json").write_text(json.dumps(plan["spec_diff"], indent=2))

    n_skip = len([s for s in plan["skipped"] if "skipped" in s])
    print(f"\nplan: {len(plan['slides'])} slide entr{'y' if len(plan['slides']) == 1 else 'ies'}, "
          f"{n_skip} skipped -> {out.parent / 'deck-plan.json'}")
    if not args.plan_only:
        print(f"deck: {plan['deck']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
