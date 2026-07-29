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

from core import llm
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


def build_plan(spec: dict, pool: dict, verify_images: bool = False) -> dict:
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
        if freeform:
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

    # Pass 2: step numbering over the slides that actually survived.
    step_no = 0
    for entry in slides:
        if entry.pop("step_counter", None) and not entry.get("skipped"):
            step_no += 1
            entry["tokens"]["step_no"] = str(step_no)

    return {
        "variant": variant,
        "recipe": pool["recipe"],
        "run_dir": str(pool["run_dir"]),
        "slides": slides,
        "match_report": det_report + match_report,
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
        "--llm-backend",
        choices=["api", "claude-code"],
        default=os.environ.get("SG_LLM_BACKEND", "api"),
        help="Where LLM calls run: 'api' = Anthropic API (default); 'claude-code' = "
        "through the local claude CLI using your Claude Code login.",
    )
    args = ap.parse_args(argv)

    llm.select_backend(args.llm_backend)
    if args.llm_backend == "claude-code":
        print("LLM backend: claude-code (all deck LLM calls use your Claude Code login)")

    t0 = time.monotonic()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "deck_outputs" / ts
    out_dir.mkdir(parents=True)

    if args.plan:
        plan = json.loads(Path(args.plan).read_text())
    else:
        spec = yaml.safe_load((ROOT / "decks" / f"{args.variant}.yaml").read_text())
        pool = load_run(Path(args.run))
        load_engineer_inputs(pool, args.context, args.images)
        plan = build_plan(spec, pool, verify_images=args.verify_images)

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
    print(f"\ndeck: {deck_path} ({len(included)} slides, {time.monotonic() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
