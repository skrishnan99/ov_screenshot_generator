"""Slide deck generator: assemble a customer-facing test-report deck from an
extractor run's assets, optional engineer notes, and optional engineer photos.

Usage:
  uv run python deck_cli.py --run runs/<ts> --variant ov80i \
      [--context notes.md] [--images photos/] [--verify-images] \
      [--plan-only] [--plan plan.json]

Phases: bind (LLM fills text tokens; the image matcher assigns assets to
slots in stages — deterministic filters, one global assignment call, optional
vision verification) -> plan.json with a match report (auditable; re-render
without re-binding via --plan) -> assemble (deterministic pptx build).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path

import yaml

from core import llm, paths
from deck.assemble import build_deck
from deck.binder import bind_text
from deck.content import filter_assets, load_engineer_inputs, load_run, resolve_asset
from deck.matcher import build_catalog, match_images
from deck.slots import skeleton_profile

ROOT = Path(__file__).resolve().parent
SKELETONS = ROOT / "deck" / "skeletons"


def skeleton_path(variant: str, name: str) -> Path:
    for candidate in (SKELETONS / variant / name, SKELETONS / "_shared" / name):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"skeleton {name} not found for variant {variant}")


_MODEL_TOKEN_RE = re.compile(r"\{model_([a-z0-9_]+)\}")


def _tmpl(value, item: dict | None):
    """Substitute {model_<field>} placeholders from the repeat item (plain
    replace — no str.format, so braces in prose guidance are safe). Unknown
    fields are left intact so selectors relying on them fall through."""
    if item is None:
        return value
    if isinstance(value, str):
        return _MODEL_TOKEN_RE.sub(
            lambda m: str(item[m.group(1)]) if m.group(1) in item else m.group(0), value
        )
    if isinstance(value, dict):
        return {k: _tmpl(v, item) for k, v in value.items()}
    if isinstance(value, list):
        return [_tmpl(v, item) for v in value]
    return value


def _has_unresolved(sel: dict) -> bool:
    """A selector still containing {model_...} references a field the current
    item does not have (e.g. no structured roi_screenshot join) — skip it."""
    return any(isinstance(v, str) and "{model_" in v for v in sel.values())


def build_plan(
    spec: dict, pool: dict, verify_images: bool = False, work_dir: Path | None = None
) -> dict:
    variant = spec["variant"]
    slides: list[dict] = []
    llm_tokens: dict[str, str] = {}
    match_slots: list[dict] = []
    det_report: list[dict] = []
    catalog = build_catalog(pool)
    cat_index = {a["path"]: i for i, a in enumerate(catalog)}
    # Engineer-provided photos join every slot's candidate set (and force the
    # slot through semantic assignment even when a selector matched exactly
    # one extractor asset) so they can take precedence over extractor
    # screenshots wherever they genuinely fit.
    engineer_idxs = [i for i, a in enumerate(catalog) if a.get("source") == "engineer"]
    warned: set[str] = set()

    def emit(s: dict, item: dict | None):
        sid = s["id"] if item is None else f"{s['id']}_{item['slug']}"
        freeform = s.get("freeform")
        agent_spec = s.get("agent_slide")
        if agent_spec:
            # Agent-built slide: content resolves through the normal token and
            # image machinery below; an autonomous session lays it out later.
            ref = agent_spec.get("skeleton")
            if ref:
                skeleton = skeleton_path(variant, ref)
                profile = skeleton_profile(str(skeleton))
            else:
                skeleton = None
                profile = {
                    "title": "",
                    "tokens": [],
                    "token_guidance": {},
                    "slots": [],
                    "warnings": [],
                }
            entry: dict = {
                "id": sid,
                "skeleton": str(skeleton) if skeleton else "",
                "agent_slide": {
                    "style": agent_spec.get("style", "open"),
                    "description": _tmpl(agent_spec.get("description", ""), item) or "",
                    "skeleton": str(skeleton) if skeleton else None,
                },
            }
        elif freeform:
            # A donor skeleton supplies theme/decoration; its own content
            # holes are stripped at fill time, so its profile does not apply.
            skeleton = skeleton_path(variant, s.get("donor", "cls_rois_setup.pptx"))
            entry: dict = {"id": sid, "skeleton": str(skeleton), "freeform": True}
            profile = {
                "title": "",
                "tokens": [],
                "token_guidance": {},
                "slots": [],
                "warnings": [],
            }
        else:
            skeleton = skeleton_path(variant, s["skeleton"])
            entry = {"id": sid, "skeleton": str(skeleton)}
            profile = skeleton_profile(str(skeleton))
        for w in profile["warnings"]:
            if w not in warned:
                warned.add(w)
                print(f"  warning: {w}")
        if s.get("when"):
            sel = _tmpl(s["when"]["select"], item)
            if resolve_asset(pool, sel) is None:
                entry["skipped"] = f"condition not met: {sel}"
                slides.append(entry)
                return
        tokens: dict = {}
        for name, tspec in (s.get("tokens") or {}).items():
            tspec = _tmpl(tspec, item)
            if isinstance(tspec, str):
                tokens[name] = tspec
            elif "literal" in tspec:
                tokens[name] = str(tspec["literal"])
            elif "source" in tspec:
                source = tspec["source"]
                if source.startswith("model_") and item is not None:
                    tokens[name] = str(item.get(source.split("_", 1)[1], ""))
                else:
                    tokens[name] = str(pool.get(source, ""))
            elif "llm" in tspec:
                qualified = f"{sid}__{name}"
                guidance = tspec["llm"].strip()
                if item is not None:
                    guidance += (
                        f' This field is specifically about the model "{item["label"]}".'
                    )
                llm_tokens[qualified] = guidance
                tokens[name] = {"llm": qualified}
        # Sidecar-described tokens the spec does not cover: fill via LLM with
        # the skeleton's own guidance rather than shipping a raw {{token}}.
        for name, guidance in profile["token_guidance"].items():
            if name in tokens or name == "step_no":
                continue
            qualified = f"{sid}__{name}"
            if item is not None:
                guidance += f' This field is specifically about the model "{item["label"]}".'
            llm_tokens[qualified] = guidance
            tokens[name] = {"llm": qualified}
        if freeform:
            # Freeform title/body ride the normal token pipeline as reserved
            # names the assembler lays out programmatically.
            ff = _tmpl(freeform, item)
            for field in ("title", "body"):
                fspec = ff.get(field)
                if fspec is None:
                    continue
                name = f"_ff_{field}"
                if isinstance(fspec, str):
                    tokens[name] = fspec
                elif "llm" in fspec:
                    qualified = f"{sid}__{name}"
                    guidance = fspec["llm"].strip()
                    if item is not None:
                        guidance += (
                            f' This field is specifically about the model "{item["label"]}".'
                        )
                    llm_tokens[qualified] = guidance
                    tokens[name] = {"llm": qualified}
            if isinstance(tokens.get("_ff_title"), str):
                profile["title"] = tokens["_ff_title"]
        entry["tokens"] = tokens
        if s.get("step_counter"):
            entry["step_counter"] = True

        img_specs = s.get("images") or ([s["image"]] if s.get("image") else [])
        images: list = []
        for i, img in enumerate(img_specs):
            img = _tmpl(img, item)
            slot_id = f"{sid}__img{i}"
            required = i == 0
            slot_meta = profile["slots"][i] if i < len(profile["slots"]) else {}
            expects = " ".join(
                (
                    img.get("expects")
                    or img.get("llm")  # legacy spelling
                    or slot_meta.get("expects")
                    or slot_meta.get("placeholder_text")
                    or f"an image appropriate for the slide \"{profile['title'] or sid}\""
                ).split()
            )
            if item is not None and "expects" not in img and "llm" not in img:
                expects += f' This slide is specifically about the model "{item["label"]}".'
            cand_idxs = None
            widened = False
            preassigned = None
            preassigned_reason = ""
            if img.get("select") is not None:
                selectors = img["select"]
                selectors = selectors if isinstance(selectors, list) else [selectors]
                candidates: list = []
                used_sel = None
                for sel in selectors:
                    if _has_unresolved(sel):
                        continue
                    hits = [c for c in filter_assets(pool, sel) if c["path"] in cat_index]
                    if hits:
                        candidates, used_sel = hits, sel
                        break
                if not candidates:
                    if not required:
                        det_report.append(
                            {
                                "slot": slot_id,
                                "slide": sid,
                                "stage": "skipped",
                                "asset": None,
                                "reason": f"optional slot; no selector matched: {selectors}",
                                "candidates": 0,
                            }
                        )
                        continue
                    if not catalog:
                        entry["skipped"] = "no image assets in the pool"
                        slides.append(entry)
                        return
                    # Guardrail: selectors are an optimization, never the last
                    # line of defense. A required slot whose ladder came up
                    # empty is widened to the FULL pool and decided by
                    # semantic assignment (with forced vision verification);
                    # only a genuine no-match skips the slide.
                    cand_idxs = list(range(len(catalog)))
                    widened = True
                    print(
                        f"  slot {slot_id}: no selector matched; widening to "
                        f"full pool for semantic assignment"
                    )
                elif len(candidates) == 1 and not engineer_idxs:
                    only = cat_index[candidates[0]["path"]]
                    if not verify_images:
                        images.append(candidates[0]["abs_path"])
                        det_report.append(
                            {
                                "slot": slot_id,
                                "slide": sid,
                                "stage": "deterministic",
                                "asset": candidates[0]["path"],
                                "reason": f"single asset matched selector {used_sel}",
                                "candidates": 1,
                            }
                        )
                        continue
                    # --verify-images promises every placed image is checked,
                    # so a determined pick still goes through the matcher —
                    # pre-assigned (no assignment call), verified, and repaired
                    # from the wider pool if the picture disagrees.
                    preassigned = only
                    preassigned_reason = f"single asset matched selector {used_sel}"
                    cand_idxs = [only]
                else:
                    cand_idxs = [cat_index[c["path"]] for c in candidates]
            if cand_idxs is not None and engineer_idxs:
                cand_idxs = sorted(set(cand_idxs) | set(engineer_idxs))
            match_slots.append(
                {
                    "id": slot_id,
                    "slide": sid,
                    "slide_title": profile["title"],
                    "expects": expects,
                    "candidates": cand_idxs,
                    "width_in": slot_meta.get("width_in"),
                    "height_in": slot_meta.get("height_in"),
                    "required": required,
                    "widened": widened,
                    "preassigned": preassigned,
                    "preassigned_reason": preassigned_reason,
                }
            )
            images.append({"match": slot_id})
        if images:
            entry["images"] = images
        slides.append(entry)

    for s in spec["slides"]:
        if s.get("repeat_for") == "models":
            items = [
                m
                for m in pool.get("models", [])
                if not s.get("filter_type") or m["type"] == s["filter_type"]
            ]
            if not items:
                slides.append(
                    {
                        "id": s.get("id", "model_block"),
                        "skeleton": "",
                        "skipped": "no matching models",
                    }
                )
            for item in items:
                if s.get("slides"):
                    # Group: a block of slides repeated per model, in models-list
                    # order. only_type gates individual slides inside the block.
                    for sub in s["slides"]:
                        if sub.get("only_type") and item["type"] != sub["only_type"]:
                            continue
                        emit(sub, item)
                else:
                    emit(s, item)
        else:
            emit(s, None)

    text_values = bind_text(llm_tokens, pool)
    choices, match_report = match_images(
        match_slots, catalog, pool, verify=verify_images
    )

    # Pass 1: fill token values, resolve matched images, decide image skips.
    for entry in slides:
        if entry.get("skipped"):
            continue
        for name, val in list(entry.get("tokens", {}).items()):
            if isinstance(val, dict) and "llm" in val:
                entry["tokens"][name] = text_values.get(val["llm"], "")
        resolved: list[str] = []
        for i, img in enumerate(entry.get("images", [])):
            if isinstance(img, dict):
                idx = choices.get(img["match"])
                if idx is None:
                    if i == 0:
                        entry["skipped"] = (
                            "no suitable image matched (see match_report)"
                        )
                        break
                    continue
                resolved.append(catalog[idx]["abs_path"])
            else:
                resolved.append(img)
        if entry.get("images") and not entry.get("skipped"):
            entry["images"] = resolved

    # Pass 2: step numbering over the slides that actually survived. Runs
    # BEFORE agent slides are built so a generated slide inside a numbered
    # run receives its number and can title itself by the deck's convention.
    step_no = 0
    for entry in slides:
        if entry.pop("step_counter", None) and not entry.get("skipped"):
            step_no += 1
            entry["tokens"]["step_no"] = str(step_no)

    # Agent-built slides: an autonomous session lays out the now-resolved
    # content, guided by the deck's design guide and renders of the slides it
    # will sit between; a slide that fails acceptance twice falls back to the
    # deterministic freeform layout so the deck always completes.
    agent_entries = [e for e in slides if e.get("agent_slide") and not e.get("skipped")]
    if agent_entries:
        from deck.agent_slide import build_agent_slides
        from deck.design import group_brief

        if work_dir is None:
            # Agent sessions write real files; without an explicit output
            # directory a caller (notably a test) would litter the package.
            raise ValueError(
                "build_plan needs work_dir when the spec has agent slides — "
                "pass the run's output directory"
            )
        work_root = Path(work_dir) / "agent_slides"
        # Neighbour context resolves to the nearest FIXED slides, so it does
        # not depend on the order these are built in.
        brief = group_brief(slides)
        jobs = []
        for entry in agent_entries:
            aspec = entry["agent_slide"]
            jobs.append(
                {
                    "sid": entry["id"],
                    "style": aspec.get("style", "open"),
                    "description": aspec.get("description", ""),
                    "texts": {
                        k: v
                        for k, v in entry.get("tokens", {}).items()
                        if isinstance(v, str) and v.strip()
                    },
                    "images": [
                        p for p in entry.get("images", []) if isinstance(p, str)
                    ],
                    "skeleton": aspec.get("skeleton"),
                }
            )
            entry["design_brief"] = {
                side: {k: v for k, v in (info or {}).items() if k != "render"}
                for side, info in (brief["per_slide"].get(entry["id"]) or {}).items()
                if info
            }
        reports = build_agent_slides(jobs, work_root, brief)
        for entry in agent_entries:
            report = reports.get(entry["id"], {"pptx": None, "issues": ["not built"]})
            texts = {
                k: v
                for k, v in entry.get("tokens", {}).items()
                if isinstance(v, str) and v.strip()
            }
            entry["agent_report"] = {k: v for k, v in report.items() if k != "pptx"}
            if report.get("pptx"):
                entry["agent_pptx"] = report["pptx"]
            else:
                print(
                    f"  agent slide {entry['id']} failed acceptance twice; "
                    f"falling back to deterministic freeform layout"
                )
                entry["freeform"] = True
                entry["skeleton"] = entry["skeleton"] or str(
                    skeleton_path(spec["variant"], "cls_rois_setup.pptx")
                )
                # Reuse the slide's real title and step number rather than
                # inventing a name from its id: this slide still sits inside
                # the deck's numbered run, so it must carry the same
                # "Step N: <title>" convention as its neighbours. Falling
                # back to a titlecased id produced headings like
                # "Model View Rois Hole-Presence".
                ff_title = texts.get("title") or entry["id"].replace("_", " ").title()
                step_no = (texts.get("step_no") or "").strip()
                entry["tokens"].setdefault(
                    "_ff_title", f"Step {step_no}: {ff_title}" if step_no else ff_title
                )
                # Body copy is the prose only. "title" and "step_no" belong to
                # the heading and previously leaked in here, so the rendered
                # slide repeated its own title and ended with a bare "14".
                body = "\n".join(
                    v
                    for k, v in texts.items()
                    if k not in ("title", "step_no") and not k.startswith("_ff")
                )
                if body:
                    entry["tokens"].setdefault("_ff_body", body)

    return {
        "variant": variant,
        "recipe": pool["recipe"],
        "run_dir": str(pool["run_dir"]),
        "slides": slides,
        "match_report": det_report + match_report,
        "model_substitutions": llm.substitutions(),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="Extractor run directory")
    ap.add_argument("--variant", required=True, help="Deck spec variant (decks/<variant>.yaml)")
    ap.add_argument("--context", help="Engineer's site-visit notes (text/markdown file)")
    ap.add_argument("--images", help="Directory of engineer photos to add to the pool")
    ap.add_argument(
        "--verify-images",
        action="store_true",
        help="Vision-verify every matched slot image (one re-assignment round)",
    )
    ap.add_argument("--plan-only", action="store_true", help="Stop after writing plan.json")
    ap.add_argument("--plan", help="Reuse an existing plan.json (skips all LLM binding)")
    ap.add_argument(
        "--out-dir", help="Write outputs here instead of deck_outputs/<timestamp>"
    )
    ap.add_argument(
        "--publish",
        action="store_true",
        help="After building, upload the assets and the deck to your Google "
        "Drive (deck converted to Google Slides) and print the link. Requires "
        "a one-time sign-in: publish_cli.py login",
    )
    ap.add_argument(
        "--brand-audit",
        action="store_true",
        help="After building, write brand_report.json: deterministic brand lint "
        "over every slide plus a vision review of the slides this pipeline "
        "generated. Report-only; off by default.",
    )
    ap.add_argument(
        "--adaptive-structure",
        action="store_true",
        help="Let the engineer's notes adjust the slide structure: the variant "
        "spec is regenerated by the model (strong copy-through bias, validated, "
        "retries with a ceiling, falls back to the default). Off by default — "
        "the fixed variant spec always applies.",
    )
    ap.add_argument(
        "--llm-backend",
        choices=["api", "claude-code", "agent-sdk"],
        default=os.environ.get("SG_LLM_BACKEND", "agent-sdk"),
        help="Where LLM calls run: 'agent-sdk' (default) and 'claude-code' both "
        "use your Claude Code login (no API key), via the managed Agent SDK or "
        "per-call CLI spawns respectively; 'api' uses the Anthropic API and "
        "needs ANTHROPIC_API_KEY.",
    )
    args = ap.parse_args(argv)

    llm.select_backend(args.llm_backend)
    if args.llm_backend == "claude-code":
        print("LLM backend: claude-code (all deck LLM calls use your Claude Code login)")

    t0 = time.monotonic()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else paths.output_base() / "deck_outputs" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.plan:
        plan = json.loads(Path(args.plan).read_text())
    else:
        spec = yaml.safe_load((ROOT / "decks" / f"{args.variant}.yaml").read_text())
        pool = load_run(Path(args.run))
        load_engineer_inputs(pool, args.context, args.images)
        if args.adaptive_structure:
            if pool.get("engineer_notes"):
                from deck.spec_adapter import adapt_spec

                spec, record = adapt_spec(spec, pool)
                (out_dir / "diff.json").write_text(json.dumps(record, indent=2))
            else:
                print("  --adaptive-structure: no engineer notes given; default spec applies")
        plan = build_plan(spec, pool, verify_images=args.verify_images, work_dir=out_dir)

    (out_dir / "plan.json").write_text(json.dumps(plan, indent=2))
    included = [s for s in plan["slides"] if not s.get("skipped")]
    for s in plan["slides"]:
        mark = f"SKIP ({s['skipped']})" if s.get("skipped") else "ok"
        print(f"  slide {s['id']}: {mark}")
    for m in plan.get("match_report", []):
        verified = (
            ""
            if "verified" not in m
            else (" [verified]" if m["verified"] else " [VERIFY FAILED]")
        )
        print(f"  match {m['slot']}: {m['stage']} -> {m['asset']}{verified}")
    if args.plan_only:
        print(f"\nplan only -> {out_dir / 'plan.json'}")
        return 0

    deck_path = out_dir / "deck.pptx"
    build_deck(included, deck_path)
    # Brand enforcement that matters happens where it can still change the
    # output: brand-styled construction (freeform) and the agent-slide
    # acceptance gate. The post-assembly audit is opt-in (--brand-audit) and
    # report-only; its vision tier is scoped to slides this pipeline
    # generated, since judging the company's own templates against a few
    # reference renders produces false positives.
    if args.brand_audit:
        try:
            from deck.brand import audit_deck

            audit_deck(
                deck_path,
                out_dir / "brand_report.json",
                vision=True,
                included_slides=included,
            )
        except Exception as e:
            print(f"  brand audit failed (deck unaffected): {e}", file=sys.stderr)
    print(f"\ndeck: {deck_path} ({len(included)} slides, {time.monotonic() - t0:.0f}s)")

    if args.publish:
        # Additive and non-fatal: a publishing problem must never invalidate
        # a deck that is already on disk.
        from publish import gdrive

        try:
            report = gdrive.publish(Path(args.run), deck_path)
            print("\nDrive folder: " + (report["folder_link"] or "?"))
            if report.get("slides_link"):
                print("Google Slides: " + report["slides_link"])
        except gdrive.AuthError as e:
            print(f"\nnot published — Google sign-in needed: {e}", file=sys.stderr)
        except Exception as e:
            print(f"\nnot published: {e}", file=sys.stderr)
            print(
                f"the deck is still at {deck_path}; retry with: "
                f"publish_cli.py --run {args.run} --deck {deck_path}",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
