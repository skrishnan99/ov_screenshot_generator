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
            ("models.count", 2),
            ("models.segmentation", 1),
            ("recipe.name", "Widget Inspection"),
        ]:
            got, ok = ctx.get(key)
            if not ok or got != want:
                failures.append(f"context[{key}] = {got!r} (resolved={ok}), want {want!r}")

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
        # repeats fanned out per model, closing in template order at the end
        for expected in ("rois_model-s", "rois_horn-quality",
                         "model_evidence_model-s", "closing_capabilities"):
            if expected not in ids:
                failures.append(f"base spec expansion missing {expected} (have {ids})")
        closing = [i for i in ids if i.startswith("closing_")]
        if closing != ["closing_capabilities", "closing_defect_generator",
                       "closing_integration", "closing_team", "closing_thank_you"]:
            failures.append(f"closing run out of template order: {closing}")

        # determinism: identical expansions
        jobs2, _ = ds.expand(spec, ctx)
        if [(j.id, j.title, j.tokens) for j in jobs] != [(j.id, j.title, j.tokens) for j in jobs2]:
            failures.append("expansion is not deterministic")

        # ---- conditions ----
        cond = _minimal([
            {"id": "a", "layout": "statement", "when": {"aligner.skipped": True}},
            {"id": "b", "layout": "statement", "when": {"aligner.skipped": False}},
            {"id": "c", "layout": "statement", "when": {"models.count": {"gte": 3}}},
            {"id": "d", "layout": "statement",
             "when": {"any": [{"models.count": {"gte": 3}}, {"trigger.manual": True}]}},
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
        if [j.title for j in jobs5] != ["Model S"]:
            failures.append(f"where filter gave {[j.title for j in jobs5]}")
        if jobs5 and "Model S" not in jobs5[0].images[0]["expects"]:
            failures.append("model interpolation missing in image expects")

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
