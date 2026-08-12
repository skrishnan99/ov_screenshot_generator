"""The spec layer: schema, normalized context, conditions, expansion, steps.

Structure is data — everything here must be deterministic and must fail at
load time, never mid-build. Pins the gotchas: conditions may only reference
the normalized context (raw fact keys are a load error), unresolved keys
evaluate false AND are recorded, step numbers stay contiguous across skips
because numbering happens after them.

Run: uv run python tests/test_deckspec.py
"""

import copy
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _specfix import make_run  # noqa: E402

import deckspec as ds  # noqa: E402


def _minimal(slides):
    return {"spec_version": 1, "slides": slides}


def main() -> int:
    failures = []

    with tempfile.TemporaryDirectory() as td:
        run = make_run(Path(td))
        ctx = ds.build_context(run)

        # ---- context derivations ----
        for key, want in [
            ("camera.model", "OV80i"),
            ("camera.title", "OV80i AI Vision System"),
            ("aligner.skipped", True),
            ("trigger.manual", True),
            ("models.count", 3),
            ("models.segmentation", 2),
            ("models.classification", 1),
            ("models.max_train_images", "6"),
            ("recipe.name", "Widget Inspection"),
        ]:
            got, ok = ctx.get(key)
            if not ok or got != want:
                failures.append(f"context[{key}] = {got!r} (resolved={ok}), want {want!r}")

        # ---- final_train_images ladder: harvest first, stated fallback ----
        # min(total, max bar) per model, max across models: min(6,18)=6 and
        # min(16,6)=6 -> "6"; Edge Check's zero bars contribute nothing.
        # Without the harvest, the Train screen's stated numbers: max 83.
        meta_p0 = run / "data" / "meta.json"
        original0 = meta_p0.read_text()
        import json as _json0
        meta0 = _json0.loads(original0)
        del meta0["model_stats"]
        meta0["facts"] = [f for f in meta0["facts"]
                          if f["property"] != "total_captures"
                          and not f["property"].startswith("labelled_images")]
        meta_p0.write_text(_json0.dumps(meta0))
        got, _ = ds.build_context(run).get("models.max_train_images")
        if got != "83":
            failures.append(f"stated-images fallback gave {got!r}, want '83'")
        meta_p0.write_text(original0)

        # ---- trained derivation: last_trained is the primary signal ----
        trained = {m["name"]: m["trained"] for m in ctx.values["models"]}
        if trained != {"Model S": True, "Horn Quality": True, "Edge Check": False}:
            failures.append(f"trained derivation wrong: {trained}")
        if ctx.values.get("models.trained_signal") is not True:
            failures.append("trained_signal should be True with last_trained facts")

        # no signal at all -> information absent, not negative: include all
        import json as _json

        meta_p = run / "data" / "meta.json"
        original_meta = meta_p.read_text()
        meta = _json.loads(original_meta)
        meta["facts"] = [f for f in meta["facts"]
                         if f["property"] not in ("last_trained", "training_images")]
        meta_p.write_text(_json.dumps(meta))
        ctx_ns = ds.build_context(run)
        if not all(m["trained"] for m in ctx_ns.values["models"]):
            failures.append("no-signal fallback did not include every model")
        if ctx_ns.values.get("models.trained_signal") is not False:
            failures.append("no-signal run not recorded as trained_signal=False")
        meta_p.write_text(original_meta)  # restore for the tests below
        ctx = ds.build_context(run)

        # ---- toggle eval: fast path for clear tokens, Haiku for the rest ----
        real_eval = ds.eval_toggle_call
        calls = []

        def no_call(setting, obs):
            raise AssertionError("clear token must not reach the model")

        ds.eval_toggle_call = no_call
        try:
            got, ok = ds.build_context(run).get("aligner.skipped")
            if not ok or got is not True:
                failures.append("fast-path 'on' did not resolve aligner.skipped")
        finally:
            ds.eval_toggle_call = real_eval
        # the Traton phrasing: verbatim value needs the eval, verdict is used
        meta = _json.loads(original_meta)
        for f in meta["facts"]:
            if f["property"] == "skip_aligner":
                f["value"] = "enabled (toggle ON)"
        meta["facts"].append({"subject": "recipe", "property": "skip_aligner_banner",
                              "value": "Skip Aligner is Enabled", "source": "05"})
        meta_p.write_text(_json.dumps(meta))
        ds.eval_toggle_call = lambda s, o: (calls.append((s, list(o))), "on")[1]
        try:
            got, ok = ds.build_context(run).get("aligner.skipped")
            if not ok or got is not True:
                failures.append(f"eval verdict not used: {got!r} (resolved={ok})")
            if not calls or "Skip Aligner" not in calls[0][0] \
                    or not any("enabled (toggle ON)" in x for x in calls[0][1]):
                failures.append(f"eval not fed the observations: {calls}")
            # unknown -> the key stays unresolved, so conditions record it
            ds.eval_toggle_call = lambda s, o: "unknown"
            ctx_u = ds.build_context(run)
            if "aligner.skipped" in ctx_u.values or "aligner.skipped" not in ctx_u.unresolved:
                failures.append("unknown toggle state was not left unresolved")
            # an eval crash degrades to unknown, never to a guess
            def boom(s, o):
                raise RuntimeError("api down")
            ds.eval_toggle_call = boom
            if "aligner.skipped" in ds.build_context(run).values:
                failures.append("eval failure produced a guessed toggle state")
        finally:
            ds.eval_toggle_call = real_eval
            meta_p.write_text(original_meta)
        ctx = ds.build_context(run)

        # ---- schema rejections, each a load-time error ----
        bad_specs = [
            ("unknown layout", [{"id": "x", "layout": "hero"}]),
            ("two kinds", [{"id": "x", "layout": "figure", "tier": 3}]),
            ("dup id", [{"id": "x", "layout": "statement"}, {"id": "x", "layout": "statement"}]),
            ("unknown token", [{"id": "x", "layout": "figure", "tokens": {"body": "hi"},
                               "images": [{"expects": "e"}]}]),
            ("bad shape", [{"id": "x", "layout": "statement",
                            "tokens": {"intro": {"llm": "b", "shape": "table"}}}]),
            ("raw fact condition", [{"id": "x", "layout": "statement",
                                     "when": {"recipe.skip_aligner": "on"}}]),
            ("where without repeat", [{"id": "x", "layout": "statement", "where": {"type": "s"}}]),
            ("model interp no repeat", [{"id": "x", "layout": "statement",
                                         "title": "{model.name}"}]),
            ("tier4 no brief", [{"id": "x", "tier": 4}]),
            ("figure image count", [{"id": "x", "layout": "figure", "images": []}]),
            ("unknown skeleton", [{"id": "x", "skeleton": "hero"}]),
            # the new-flow constructs, each rejected at load
            ("foreach not models", [{"id": "x", "tier": 3,
                                     "images": [{"expects": "e", "foreach": "steps"}]}]),
            ("foreach inside repeat", [{"id": "x", "tier": 3, "repeat": "models",
                                        "images": [{"expects": "e", "foreach": "models"}]}]),
            ("hint on tier 4", [{"id": "x", "tier": 4, "brief": "b", "hint": "h"}]),
            ("when_model outside block", [{"id": "x", "layout": "statement",
                                           "when_model": {"type": "segmentation"}}]),
            ("block without repeat", [{"id": "x", "slides": [
                {"id": "y", "layout": "statement"}]}]),
            ("block inner with when", [{"id": "x", "repeat": "models", "slides": [
                {"id": "y", "layout": "statement", "when": {"models.count": {"gte": 1}}}]}]),
            # skeleton token completeness: surplus AND missing are both errors
            ("skeleton surplus token", [{"id": "x", "skeleton": "results_image",
                                         "tokens": {"recipe_name": "a",
                                                    "brief_description": "b",
                                                    "bogus": "c"}}]),
            ("skeleton missing token", [{"id": "x", "skeleton": "results_image",
                                         "tokens": {"recipe_name": "a"}}]),
            ("model interp outside foreach hole", [
                {"id": "x", "tier": 3, "tokens": {"t": "{model.name}"},
                 "images": [{"expects": "e", "foreach": "models"}]}]),
        ]
        for label, slides in bad_specs:
            try:
                ds.validate(_minimal(slides))
                failures.append(f"schema accepted: {label}")
            except ds.SpecError:
                pass

        # ---- the shipped base spec loads, validates, expands ----
        spec = ds.load_spec()
        jobs, skipped = ds.expand(spec, ctx)
        ids = [j.id for j in jobs]
        # the 8-part flow: variant title, results overview, imaging,
        # (aligner skipped here), combined ROIs, per-model blocks, IO
        # logic, THEN the results card — the whole pipeline before its
        # outcome — library, closing run
        want_order = [
            "title_ov80i", "results_overview", "imaging", "rois",
            "training_model-s", "training_horn-quality",
            "logic",
            "results",
            "library_ov80i",
            "closing_capabilities", "closing_defect_generator",
            "closing_integration", "closing_team", "closing_thank_you",
        ]
        if ids != want_order:
            failures.append(f"base spec expansion order: {ids}")
        if any("edge-check" in i for i in ids):
            failures.append("never-trained model leaked into the deck")
        skipped_ids = {s.get("id") for s in skipped if isinstance(s, dict)}
        for sid in ("title_ov20i", "aligner", "library_ov20i"):
            if sid not in skipped_ids:
                failures.append(f"{sid} should be skipped+recorded (skip_aligner=on, ov80i)")
        # the combined-ROI slide fans one hole per TRAINED model, interpolated
        rois = next(j for j in jobs if j.id == "rois")
        rois_expects = " || ".join(i["expects"] for i in rois.images)
        if len(rois.images) != 2 or "Model S" not in rois_expects \
                or "Horn Quality" not in rois_expects or "Edge Check" in rois_expects:
            failures.append(f"rois foreach fan-out wrong: {rois_expects}")
        if not all(i.get("optional") for i in rois.images):
            failures.append("rois foreach holes must be optional")
        # ONE recipe-level results card: accuracy pinned, images = the max
        res = next(j for j in jobs if j.id == "results")
        if res.skeleton != "concise_results_classifier":
            failures.append(f"results card skeleton wrong: {res.skeleton}")
        if res.tokens.get("train_acc") != "100%":
            failures.append(f"train_acc not pinned to 100%: {res.tokens.get('train_acc')!r}")
        if res.tokens.get("train_imgs") != "6":
            failures.append(f"train_imgs is not the roster max: {res.tokens.get('train_imgs')!r}")
        # the overview slide ALWAYS carries the raw + overlaid versions of
        # the capture shown in the library page screenshot: both holes
        # required (a missing pair should skip the slide, never fill it with
        # something else), and each hole states the authoritative criterion —
        # agreement with the library screenshot, never visual appeal — so a
        # dark capture can't be re-matched away
        ov = next(j for j in jobs if j.id == "results_overview")
        if len(ov.images) != 2 or any(i.get("optional") for i in ov.images):
            failures.append("results_overview must have exactly 2 required holes")
        for i in ov.images:
            expects = i["expects"].lower()
            if "library capture" not in expects \
                    or "library page screenshot" not in expects \
                    or "agreement" not in expects:
                failures.append(f"results_overview hole lost the same-capture-"
                                f"as-library-screenshot doctrine: {i['expects'][:60]!r}")
        # training titles carry the model's name AND its type
        tr = next(j for j in jobs if j.id == "training_model-s")
        if tr.title != "Step {step}: Training — Model S (Segmentation)":
            failures.append(f"training title wrong: {tr.title!r}")

        # determinism: identical expansions
        jobs2, _ = ds.expand(spec, ctx)
        if [(j.id, j.title, j.tokens) for j in jobs] != [(j.id, j.title, j.tokens) for j in jobs2]:
            failures.append("expansion is not deterministic")

        # ---- variant fallback: manifest silent -> majority vote over the
        # screenshot descriptions -> the ov20i title skeleton is chosen ----
        run_v = make_run(Path(td) / "variantless")
        man_p = run_v / "data" / "manifest.json"
        man = _json.loads(man_p.read_text())
        man["variant"] = ""
        man_p.write_text(_json.dumps(man))
        desc_p = run_v / "deliverables" / "report" / "descriptions.json"
        descs = _json.loads(desc_p.read_text())
        descs["01_home.png"] = ("The OV20i home screen; the OV20i badge is "
                                "visible top-left above the recipe list.")
        desc_p.write_text(_json.dumps(descs))
        ctx_v = ds.build_context(run_v)
        if ctx_v.values.get("camera.variant") != "ov20i":
            failures.append(f"variant fallback gave {ctx_v.values.get('camera.variant')!r}")
        jobs_v, _ = ds.expand(spec, ctx_v)
        titles_v = [j.id for j in jobs_v if j.id.startswith("title_")]
        if titles_v != ["title_ov20i"]:
            failures.append(f"variant-conditional title picked {titles_v}")

        # ---- conditions ----
        cond = _minimal([
            {"id": "a", "layout": "statement", "when": {"aligner.skipped": True}},
            {"id": "b", "layout": "statement", "when": {"aligner.skipped": False}},
            {"id": "c", "layout": "statement", "when": {"models.count": {"gte": 4}}},
            {"id": "d", "layout": "statement",
             "when": {"any": [{"models.count": {"gte": 4}}, {"trigger.manual": True}]}},
        ])
        ds.validate(cond)
        jobs3, skips3 = ds.expand(cond, ctx)
        got = [j.id for j in jobs3]
        if got != ["a", "d"]:
            failures.append(f"conditions picked {got}, want ['a', 'd']")
        if not any("when" in str(s.get("skipped", "")) for s in skips3 if isinstance(s, dict)):
            failures.append("condition skip was not recorded")

        # unresolved key -> false, recorded, never silent
        ctx2 = copy.deepcopy(ctx)
        ctx2.values.pop("aligner.skipped")
        _, skips4 = ds.expand(_minimal(
            [{"id": "a", "layout": "statement", "when": {"aligner.skipped": True}}]), ctx2)
        flat = str(skips4)
        if "unresolved" not in flat:
            failures.append(f"unresolved condition not recorded: {flat[:120]}")

        # ---- where-filtered repeat ----
        rep = _minimal([{
            "id": "seg", "repeat": "models", "where": {"type": "segmentation"},
            "layout": "figure", "title": "{model.name}",
            "images": [{"expects": "x for {model.name}"}],
        }])
        ds.validate(rep)
        jobs5, _ = ds.expand(rep, ctx)
        if [j.title for j in jobs5] != ["Model S", "Edge Check"]:
            failures.append(f"where filter gave {[j.title for j in jobs5]}")
        if jobs5 and "Model S" not in jobs5[0].images[0]["expects"]:
            failures.append("model interpolation missing in image expects")

        # where on two keys: type AND trained
        rep2 = _minimal([{
            "id": "seg", "repeat": "models",
            "where": {"type": "segmentation", "trained": True},
            "layout": "figure", "title": "{model.name}",
            "images": [{"expects": "x"}],
        }])
        ds.validate(rep2)
        jobs5b, _ = ds.expand(rep2, ctx)
        if [j.title for j in jobs5b] != ["Model S"]:
            failures.append(f"trained where-filter gave {[j.title for j in jobs5b]}")

        # ---- deferred steps stay contiguous across a skip ----
        steps = _minimal([
            {"id": "s1", "layout": "statement", "step": True, "title": "Step {step}: A"},
            {"id": "s2", "layout": "statement", "step": True, "title": "Step {step}: B"},
            {"id": "s3", "layout": "statement", "step": True, "title": "Step {step}: C"},
        ])
        ds.validate(steps)
        jobs6, _ = ds.expand(steps, ctx)
        if any("{step}" not in j.title for j in jobs6):
            failures.append("step interpolation happened before finalize_steps")
        jobs6 = [j for j in jobs6 if j.id != "s2"]  # simulate a match-skip
        ds.finalize_steps(jobs6)
        titles = [j.title for j in jobs6]
        if titles != ["Step 1: A", "Step 2: C"]:
            failures.append(f"steps not contiguous after skip: {titles}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL DECKSPEC CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
