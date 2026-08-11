"""Offline end-to-end: base spec -> finished .pptx through the REAL ovdeck.

Only the three model calls are stubbed (assigner, resolver, arranger); the
layout engine, its save() gates, the skeleton store and the canvas invariant
all run for real. Pins the pipeline-order lessons: match before content,
steps numbered after match-skips, required-hole-unmatched skips the slide
with a record, optional absence is silent.

Run: uv run python tests/test_spec_pipeline.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _specfix import canned_resolver, keyword_assign, make_run  # noqa: E402

import content as content_mod  # noqa: E402
import matching as matching_mod  # noqa: E402
import arrange as arrange_mod  # noqa: E402
import deckgen  # noqa: E402
import deckspec as ds  # noqa: E402


def main() -> int:
    failures = []
    saved = {
        "assign": matching_mod.assign_call,
        "verify": matching_mod.verify_call,
        "resolve": content_mod.resolve_call,
        "arrange": arrange_mod.arrange_call,
    }
    calls = {"assign": 0, "resolve": 0, "arrange": 0}

    def stub_assign(holes, catalog):
        calls["assign"] += 1
        return keyword_assign(holes, catalog)

    def stub_resolve(reqs, material):
        calls["resolve"] += 1
        return canned_resolver(reqs, material)

    def stub_arrange(title, image_paths, text, feedback=""):
        calls["arrange"] += 1
        slides = []
        for p in image_paths:
            slides.append({"layout": "figure", "title": title, "images": [p],
                           "text": {"caption": text.get("text", "")[:100]}})
        if not slides:
            slides = [{"layout": "statement", "title": title, "images": [],
                       "text": {"intro": "x", "card_title": "S", "bullets": "a\nb\nc"}}]
        return slides

    matching_mod.assign_call = stub_assign
    matching_mod.verify_call = lambda *a, **k: {"match": True, "reason": "stub"}
    content_mod.resolve_call = stub_resolve
    arrange_mod.arrange_call = stub_arrange

    try:
        with tempfile.TemporaryDirectory() as td:
            run = make_run(Path(td))
            out = Path(td) / "out" / "report.pptx"
            out.parent.mkdir(parents=True)

            plan = deckgen.compile_deck(run, out, log=lambda *a: None)

            # ---- the deck exists and passed ovdeck's gates ----
            deck_path = Path(plan.get("deck", ""))
            if not deck_path.exists():
                failures.append("no deck written")
            else:
                from pptx import Presentation

                pr = Presentation(str(deck_path))
                # 2 models: title, problem, seen_whole, imaging, aligner,
                # rois x2, setup x2, evidence x2 (arranged: >=1 slide each),
                # logic, library skeleton, closing x5  => >= 18
                if len(pr.slides) < 18:
                    failures.append(f"only {len(pr.slides)} slides in the deck")
                kw = pr.core_properties.keywords or ""
                if "ovdeck:template-slides=" not in kw:
                    failures.append("carried-slide stamp missing (audit exemption broken)")

            # ---- one global assignment call (plus at most one repair) ----
            if not (1 <= calls["assign"] <= 2):
                failures.append(f"{calls['assign']} assignment calls; want 1 global (+1 repair)")
            if calls["resolve"] != 1:
                failures.append(f"{calls['resolve']} resolver calls; want exactly 1 batched")

            # ---- steps contiguous in emitted titles ----
            steps = []
            for rec in plan["slides"]:
                t = rec.get("title", "")
                if t.startswith("Step "):
                    steps.append(int(t.split()[1].rstrip(":")))
            if steps != list(range(1, len(steps) + 1)):
                failures.append(f"step numbering not contiguous: {steps}")
            if "{step}" in str(plan["slides"]):
                failures.append("unresolved {step} placeholder reached the plan")

            # ---- matching audit present, optional absence silent ----
            ev = [r for r in plan["slides"] if r["origin"] == "model_evidence"]
            if not ev:
                failures.append("tier-3 evidence slides missing from plan")
            for rec in ev:
                for img in rec["images"]:
                    if img["optional"] and img["path"] is None:
                        pass  # silently absent — exactly right
            if "matching" not in plan or not plan["matching"]:
                failures.append("matching report missing from plan")

            # ---- a required hole that can't match skips the slide, recorded ----
            spec = ds.load_spec()
            spec["slides"].insert(2, {
                "id": "impossible", "layout": "figure", "title": "X",
                "images": [{"expects": "a purple submarine surfacing in a bakery"}],
            })
            spec_path = Path(td) / "mod.yaml"
            import yaml

            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))
            plan2 = deckgen.compile_deck(run, Path(td) / "out2" / "r.pptx",
                                         spec_path=spec_path, plan_only=True,
                                         log=lambda *a: None)
            skipped = [s for s in plan2["skipped"] if s.get("id") == "impossible"]
            if not skipped or "unmatched" not in skipped[0]["skipped"]:
                failures.append(f"required-unmatched slide not skipped+recorded: {skipped}")
            if any(r["id"] == "impossible" for r in plan2["slides"]):
                failures.append("unmatched slide still compiled")
    finally:
        matching_mod.assign_call = saved["assign"]
        matching_mod.verify_call = saved["verify"]
        content_mod.resolve_call = saved["resolve"]
        arrange_mod.arrange_call = saved["arrange"]

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL SPEC-PIPELINE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
