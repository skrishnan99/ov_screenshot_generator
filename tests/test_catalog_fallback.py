"""A failed vision description must not evict an extractor screenshot from
the matching catalog.

descriptions.json entries reading "[description failed: ...]" used to drop
the asset from build_catalog entirely — a one-call structured-output hiccup
on 12_library.png cost the deck its library slide, even though the manifest
asset index records exactly which extraction step captured the file
(identity by construction, the same sanctioned join the training-image
ladder uses). What this suite pins:

- a failed-description screenshot present in the asset index is cataloged
  with a deterministic stand-in naming its extraction step,
- a screenshot with NO descriptions.json entry at all is cataloged the same
  way,
- a failed-description screenshot absent from the index stays out (nothing
  known about it), as does any file missing on disk,
- healthy descriptions are untouched and never duplicated by the join,
- end to end through match(): the stand-in path reaches the assigner.

Run: uv run python tests/test_catalog_fallback.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _specfix import make_run  # noqa: E402

import deckspec as ds  # noqa: E402
import matching as matching_mod  # noqa: E402

LIB = "deliverables/screenshots/12_library.png"


def _by_path(catalog):
    return {c["path"]: c["description"] for c in catalog}


def main() -> int:
    failures = []

    with tempfile.TemporaryDirectory() as td:
        run = make_run(Path(td))
        desc_p = run / "deliverables" / "report" / "descriptions.json"

        # ---- baseline: healthy catalog, no stand-ins, no duplicates ----
        cat = _by_path(matching_mod.build_catalog(run))
        if LIB not in cat or "asset index" in cat[LIB]:
            failures.append(f"healthy description replaced or lost: {cat.get(LIB)!r}")
        paths = [c["path"] for c in matching_mod.build_catalog(run)]
        if len(paths) != len(set(paths)):
            failures.append("catalog contains duplicate paths")

        # ---- failed description + asset index -> deterministic stand-in ----
        descs = json.loads(desc_p.read_text())
        descs["12_library.png"] = "[description failed: structured output invalid]"
        desc_p.write_text(json.dumps(descs))
        cat = _by_path(matching_mod.build_catalog(run))
        if LIB not in cat:
            failures.append("failed-description screenshot evicted from the catalog")
        elif "'library'" not in cat[LIB]:
            failures.append(f"stand-in does not name the extraction step: {cat[LIB]!r}")

        # ---- missing description entry entirely -> same stand-in ----
        descs.pop("12_library.png")
        desc_p.write_text(json.dumps(descs))
        cat = _by_path(matching_mod.build_catalog(run))
        if LIB not in cat or "'library'" not in cat[LIB]:
            failures.append(f"undescribed screenshot not recovered via the index: {cat.get(LIB)!r}")

        # ---- failed description with NO index entry -> stays out ----
        descs["99_mystery.png"] = "[description failed: boom]"
        desc_p.write_text(json.dumps(descs))
        (run / "deliverables" / "screenshots" / "99_mystery.png").write_bytes(
            (run / "deliverables" / "screenshots" / "12_library.png").read_bytes())
        cat = _by_path(matching_mod.build_catalog(run))
        if "deliverables/screenshots/99_mystery.png" in cat:
            failures.append("unknown failed-description screenshot was cataloged")

        # ---- file missing on disk -> never cataloged, even via the index ----
        (run / "deliverables" / "screenshots" / "12_library.png").unlink()
        cat = _by_path(matching_mod.build_catalog(run))
        if LIB in cat:
            failures.append("catalog lists a file that does not exist")

        # ---- end to end: the stand-in reaches the assigner through match()
        run2 = make_run(Path(td) / "again")
        d2 = run2 / "deliverables" / "report" / "descriptions.json"
        descs2 = json.loads(d2.read_text())
        descs2["12_library.png"] = "[description failed: structured output invalid]"
        d2.write_text(json.dumps(descs2))

        saved = (matching_mod.assign_call, matching_mod.verify_call,
                 matching_mod.block_quality_call)
        seen_paths = []

        def fake_assign(holes, catalog):
            seen_paths.extend(c["path"] for c in catalog)
            return [{"hole": h.id, "path": None, "confidence": "high",
                     "reason": "stub"} for h in holes]

        try:
            matching_mod.assign_call = fake_assign
            matching_mod.verify_call = lambda *a, **k: {"match": True, "reason": "s"}
            matching_mod.block_quality_call = lambda d, t="": {
                "product_image": True, "annotated": True, "reason": "s"}
            ctx = ds.build_context(run2)
            jobs, _ = ds.expand(ds.load_spec(), ctx)
            matching_mod.match(run2, jobs, log=lambda *a: None)
        finally:
            (matching_mod.assign_call, matching_mod.verify_call,
             matching_mod.block_quality_call) = saved
        if LIB not in seen_paths:
            failures.append("stand-in path never reached the assigner via match()")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL CATALOG-FALLBACK CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
