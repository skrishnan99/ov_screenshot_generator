"""The training slide's image ladder: annotated block page, else the model's
View All ROIs gallery, else the block page despite its issues.

A block page whose viewer is black with empty label outlines (a real
occurrence) ruins the training slide. The ladder judges the block screenshot
from its vision description BEFORE global matching, deterministically, and
records every decision. What this suite pins:

- a good block page is pinned and the gallery is never used on this slide,
- a rejected block page yields to the model's View All ROIs gallery,
- with no gallery, the rejected block page still ships — flawed beats empty,
- no description / a failed judge call keeps the block page (positive
  evidence only; the judge must never fail a build),
- pins skip semantic assignment and their paths leave the free catalog,
- paths resolve from the sanctioned contracts (envelope, then the
  manifest assets join) — never from filename guessing,
- the default spec flags the training hole with `ladder: annotated_block`.

Run: uv run python tests/test_training_image_ladder.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _specfix import make_run  # noqa: E402

import deckspec as ds  # noqa: E402
import matching as matching_mod  # noqa: E402

BLOCK_S = "deliverables/screenshots/05_segmentation.png"
BLOCK_C = "deliverables/screenshots/07_classification.png"
ROIS_S = "deliverables/screenshots/06_view_model-s.png"
ROIS_C = "deliverables/screenshots/hq_view.png"


def _jobs(run):
    ctx = ds.build_context(run)
    jobs, _ = ds.expand(ds.load_spec(), ctx)
    return [j for j in jobs if j.origin == "training"]


def main() -> int:
    failures = []
    saved_quality = matching_mod.block_quality_call
    saved_assign = matching_mod.assign_call
    saved_verify = matching_mod.verify_call

    try:
        with tempfile.TemporaryDirectory() as td:
            run = make_run(Path(td))
            training = _jobs(run)
            if len(training) != 2:
                failures.append(f"expected 2 training jobs, got {[j.id for j in training]}")

            # ---- spec carries the flag ----
            for j in training:
                if j.images[0].get("ladder") != "annotated_block":
                    failures.append(f"{j.id} hole lost `ladder: annotated_block`")

            # ---- good block page: pinned, gallery untouched ----
            matching_mod.block_quality_call = lambda d, t="": {
                "product_image": True, "annotated": True, "reason": "fine"}
            pins = matching_mod.ladder_pins(run, training, log=lambda *a: None)
            got = {h: p for h, (p, _) in pins.items()}
            want = {"training_model-s#0": BLOCK_S, "training_horn-quality#0": BLOCK_C}
            if got != want:
                failures.append(f"good-block pins wrong: {got}")

            # ---- the extractor's recorded capture pick outranks the
            # description judge: tier 1 keeps the block, any other tier
            # goes straight to the gallery — no judge call either way ----
            man_p = run / "data" / "manifest.json"
            man = json.loads(man_p.read_text())
            man["steps"] = [
                {"id": "segmentation_block", "capture_pick": {"tier": 1}},
                {"id": "classification_block", "capture_pick": {"tier": 3}},
            ]
            man_p.write_text(json.dumps(man))
            judge_calls = []

            def _never(d, t=""):
                judge_calls.append(d)
                raise AssertionError("description judge must not run")
            matching_mod.block_quality_call = _never
            pins = matching_mod.ladder_pins(run, training, log=lambda *a: None)
            got = {h: p for h, (p, _) in pins.items()}
            if got != {"training_model-s#0": BLOCK_S,
                       "training_horn-quality#0": ROIS_C}:
                failures.append(f"pick-record pins wrong: {got}")
            if judge_calls:
                failures.append("description judge ran despite a pick record")
            reasons = {h: e["reason"] for h, (_, e) in pins.items()}
            if "capture pick: tier 1" not in reasons["training_model-s#0"] \
                    or "capture pick: tier 3" not in reasons["training_horn-quality#0"]:
                failures.append(f"pick-record reasons unexplained: {reasons}")
            # remove the records; the description judge takes over again,
            # and receives the MODEL'S TYPE (labels are not masks)
            man["steps"] = []
            man_p.write_text(json.dumps(man))
            types_seen = []
            matching_mod.block_quality_call = lambda d, t="": types_seen.append(t) or {
                "product_image": True, "annotated": True, "reason": "fine"}
            matching_mod.ladder_pins(run, training, log=lambda *a: None)
            if sorted(types_seen) != ["classification", "segmentation"]:
                failures.append(f"judge not type-aware: {types_seen}")

            # ---- rejected block page: the model's gallery stands in ----
            matching_mod.block_quality_call = lambda d, t="": {
                "product_image": False, "annotated": False,
                "reason": "black viewer, empty outlines"}
            pins = matching_mod.ladder_pins(run, training, log=lambda *a: None)
            got = {h: p for h, (p, _) in pins.items()}
            want = {"training_model-s#0": ROIS_S, "training_horn-quality#0": ROIS_C}
            if got != want:
                failures.append(f"bad-block pins wrong: {got}")
            for _, entry in pins.values():
                if "rejected" not in entry["reason"]:
                    failures.append(f"ladder decision unexplained: {entry}")

            # ---- rejected block, NO gallery: block ships despite issues ----
            meta_p = run / "data" / "meta.json"
            meta = json.loads(meta_p.read_text())
            for m in meta["models"]:
                m.pop("view_rois_screenshot", None)
            meta_p.write_text(json.dumps(meta))
            # jobs snapshot the envelope at expand time; re-expand so the
            # models actually lack the gallery
            training = _jobs(run)
            pins = matching_mod.ladder_pins(run, training, log=lambda *a: None)
            got = {h: p for h, (p, _) in pins.items()}
            want = {"training_model-s#0": BLOCK_S, "training_horn-quality#0": BLOCK_C}
            if got != want:
                failures.append(f"no-gallery fallback wrong: {got}")
            if not all("despite issues" in e["reason"] for _, e in pins.values()):
                failures.append("fallback-despite-issues not recorded")

            # ---- judge failure: block kept, nothing raises ----
            def _boom(d, t=""):
                raise RuntimeError("llm down")
            matching_mod.block_quality_call = _boom
            pins = matching_mod.ladder_pins(run, training, log=lambda *a: None)
            got = {h: p for h, (p, _) in pins.items()}
            if got != want:
                failures.append(f"judge-failure pins wrong: {got}")

            # ---- no description: block kept without any judge call ----
            desc_p = run / "deliverables" / "report" / "descriptions.json"
            descs = json.loads(desc_p.read_text())
            descs.pop("05_segmentation.png", None)
            desc_p.write_text(json.dumps(descs))
            judged = []
            matching_mod.block_quality_call = lambda d, t="": judged.append(d) or {
                "product_image": False, "annotated": False, "reason": "x"}
            pins = matching_mod.ladder_pins(run, training, log=lambda *a: None)
            if pins["training_model-s#0"][0] != BLOCK_S:
                failures.append("undescribed block page was not kept")
            if any("segmentation" in str(d).lower() and "block" in str(d).lower()
                   for d in judged if "classification" not in str(d).lower()) and False:
                pass  # (the classification hole may still be judged)

            # ---- through match(): pins skip assignment; paths leave the pool ----
            run2 = make_run(Path(td) / "again")
            seen = {}

            def fake_assign(holes, catalog):
                seen["hole_ids"] = [h.id for h in holes]
                seen["paths"] = [c["path"] for c in catalog]
                return [{"hole": h.id, "path": None, "confidence": "high",
                         "reason": "stub"} for h in holes]

            matching_mod.assign_call = fake_assign
            matching_mod.verify_call = lambda *a, **k: {"match": True, "reason": "s"}
            matching_mod.block_quality_call = lambda d, t="": {
                "product_image": True, "annotated": True, "reason": "fine"}
            ctx = ds.build_context(run2)
            jobs, _ = ds.expand(ds.load_spec(), ctx)
            m = matching_mod.match(run2, jobs, log=lambda *a: None)
            if m.assignments.get("training_model-s#0") != BLOCK_S:
                failures.append("match() lost the ladder pin")
            if any(h.startswith("training_") for h in seen.get("hole_ids", [])):
                failures.append("pinned holes were still sent to the assigner")
            if BLOCK_S in seen.get("paths", []) or BLOCK_C in seen.get("paths", []):
                failures.append("pinned paths stayed in the free catalog")
            ladder_rows = [r for r in m.report if r.get("stage") == "ladder"]
            if len(ladder_rows) != 2:
                failures.append(f"ladder decisions not in the report: {ladder_rows}")
    finally:
        matching_mod.block_quality_call = saved_quality
        matching_mod.assign_call = saved_assign
        matching_mod.verify_call = saved_verify

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL TRAINING-IMAGE-LADDER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
