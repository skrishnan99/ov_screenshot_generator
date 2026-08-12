"""Per-model labelling stats are harvested from the block page, robustly.

The block page's class panel shows, per ROI group, one colored bar per class
with its labelled-image count, plus a "Source Capture: n of TOTAL" readout.
The panel overflows the viewport on real recipes, so the harvest reads
innerText (which includes rows scrolled out of view) and only re-reads after
scrolling when scrolling actually changed the text — the virtualized-list
case. Stats are enrichment: they must never fail a step, and they must land
both in meta["model_stats"] (keyed by model name) and as roster-subject
facts so deck grounding can use them with no further wiring.

Run: uv run python tests/test_model_stats.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

import cli  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class _FakeBrowser:
    """Serves a scripted sequence of page texts; scroll_panels advances
    through them and reports whether anything moved."""

    def __init__(self, texts: list[str], moves: list[bool]):
        self.texts = texts
        self.moves = moves
        self.pos = 0
        self.resets = 0

    def page_text(self, limit: int = 6000) -> str:
        return self.texts[min(self.pos, len(self.texts) - 1)]

    def scroll_panels(self) -> bool:
        moved = self.moves[min(self.pos, len(self.moves) - 1)]
        if moved:
            self.pos += 1
        return moved

    def reset_panel_scroll(self) -> None:
        self.resets += 1
        self.pos = 0


def _stub_extract(responses: dict, calls: list):
    def _extract(page_text, name, mtype):
        calls.append(page_text)
        return responses[page_text]

    return _extract


def main() -> int:
    failures = []
    real_extract = cli.extract_model_stats
    try:
        # --- merge: dedupe by (roi, token), keep the larger count, drop junk ---
        agg = {"total_captures": None, "classes": {}}
        cli._merge_stats(agg, {
            "total_captures": 15,
            "classes": [
                {"roi": "Trumpet Front", "label": "Two",
                 "class_token": "two_trumpet_front", "labelled_images": 5},
                {"roi": "Trumpet Front", "label": "Two",
                 "class_token": "two_trumpet_front", "labelled_images": 3},
                {"roi": "", "label": "", "class_token": "", "labelled_images": 9},
                {"roi": "X", "label": "Bad", "class_token": "", "labelled_images": -1},
                {"roi": "X", "label": "NaN", "class_token": "", "labelled_images": "n/a"},
            ],
        })
        cli._merge_stats(agg, {"total_captures": -1, "classes": [
            {"roi": "trumpet front", "label": "Two",
             "class_token": "TWO_TRUMPET_FRONT", "labelled_images": 4},
        ]})
        if agg["total_captures"] != 15:
            failures.append(f"total_captures {agg['total_captures']}, want 15")
        if len(agg["classes"]) != 1:
            failures.append(f"merge kept {len(agg['classes'])} classes, want 1 "
                            f"(dedupe by case-insensitive roi+token)")
        else:
            only = next(iter(agg["classes"].values()))
            if only["labelled_images"] != 5:
                failures.append(f"dedupe kept {only['labelled_images']}, want the larger 5")

        # --- static panel: scroll moves but the text is unchanged -> ONE read ---
        text = "Source Capture: 5 of 5\nInspection Types\nTwo two_trumpet_front 5"
        calls: list = []
        cli.extract_model_stats = _stub_extract({text: {
            "total_captures": 5,
            "classes": [{"roi": "Trumpet Front", "label": "Two",
                         "class_token": "two_trumpet_front", "labelled_images": 5}],
        }}, calls)
        b = _FakeBrowser([text], [True, True, False])
        meta: dict = {}
        cli.harvest_model_stats(b, {"name": "Model 2", "type": "classification"},
                                meta, "view_all_rois_classification page")
        if len(calls) != 1:
            failures.append(f"static panel read {len(calls)} times, want 1")
        entry = meta.get("model_stats", {}).get("Model 2")
        if entry is None:
            failures.append("stats not keyed by model name in meta['model_stats']")
        else:
            if entry["type"] != "classification" or entry["total_captures"] != 5:
                failures.append(f"entry wrong: {entry}")
            if len(entry["classes"]) != 1:
                failures.append(f"entry classes: {entry['classes']}")
        facts = meta.get("facts", [])
        want_facts = {
            ("model: Model 2", "total_captures", "5"),
            ("model: Model 2", "labelled_images two_trumpet_front", "5"),
        }
        got_facts = {(f["subject"], f["property"], f["value"]) for f in facts}
        if got_facts != want_facts:
            failures.append(f"facts mirrored wrong: {sorted(got_facts)}")
        if any(f["source"] != "view_all_rois_classification page" for f in facts):
            failures.append("facts lost their source")
        if b.resets < 2:
            failures.append("panel scroll not reset before AND after harvest")

        # --- virtualized panel: text changes per scroll -> passes are merged ---
        t1 = "Source Capture: 15 of 15\nZero zero_a 2"
        t2 = "One one_a 3\nTwo two_a 4"
        calls = []
        cli.extract_model_stats = _stub_extract({
            t1: {"total_captures": 15, "classes": [
                {"roi": "A", "label": "Zero", "class_token": "zero_a",
                 "labelled_images": 2}]},
            t2: {"total_captures": -1, "classes": [
                {"roi": "A", "label": "One", "class_token": "one_a",
                 "labelled_images": 3},
                {"roi": "A", "label": "Two", "class_token": "two_a",
                 "labelled_images": 4}]},
        }, calls)
        b = _FakeBrowser([t1, t2], [True, False])
        meta = {}
        cli.harvest_model_stats(b, {"name": "Model", "type": "segmentation"},
                                meta, "view_all_rois_segmentation page")
        if len(calls) != 2:
            failures.append(f"virtualized panel read {len(calls)} times, want 2")
        entry = meta.get("model_stats", {}).get("Model", {})
        if entry.get("total_captures") != 15 or len(entry.get("classes", [])) != 3:
            failures.append(f"virtualized merge wrong: {entry}")

        # --- nothing found: recorded honestly, never invented ---
        calls = []
        cli.extract_model_stats = _stub_extract(
            {"bare page": {"total_captures": -1, "classes": [], "notes": ""}}, calls)
        b = _FakeBrowser(["bare page"], [False])
        meta = {}
        cli.harvest_model_stats(b, {"name": "Model 3", "type": "segmentation"},
                                meta, "src")
        entry = meta.get("model_stats", {}).get("Model 3", {})
        if entry.get("total_captures") is not None or entry.get("classes"):
            failures.append(f"empty page produced data: {entry}")
        if "note" not in entry:
            failures.append("empty result carries no note")
        if meta.get("facts"):
            failures.append(f"empty result minted facts: {meta['facts']}")

        # --- an extraction crash warns and returns; it must not raise ---
        def boom(page_text, name, mtype):
            raise RuntimeError("llm down")

        cli.extract_model_stats = boom
        try:
            cli.harvest_model_stats(_FakeBrowser(["x"], [False]),
                                    {"name": "M", "type": "classification"}, {}, "s")
        except Exception as e:
            failures.append(f"harvest raised instead of degrading: {e!r}")
    finally:
        cli.extract_model_stats = real_extract

    # --- totals: read post-Previous, merged into the same entries ---
    meta = {
        "models": [
            {"name": "Model", "type": "segmentation"},
            {"name": "Model 3", "type": "segmentation"},
            {"name": "Model 2", "type": "classification"},
        ],
        "model_stats": {
            "Model": {"type": "segmentation", "total_captures": None,
                      "classes": [{"roi": "", "label": "Defect",
                                   "class_token": "", "labelled_images": 18}],
                      "source": "segmentation_block_stats page"},
        },
    }
    b = _FakeBrowser(["Navigation\nSource Capture:\n6\nof 6\nGo\nView All ROIs"], [False])
    cli.harvest_block_total(b, "segmentation", meta, "segmentation_block page")
    got = meta["model_stats"]
    if got["Model"]["total_captures"] != 6:
        failures.append(f"existing entry not updated with total: {got['Model']}")
    if got["Model"]["classes"][0]["labelled_images"] != 18:
        failures.append("totals merge clobbered the class bars")
    if got.get("Model 3", {}).get("total_captures") != 6:
        failures.append(f"missing-entry model not given a total: {got.get('Model 3')}")
    if "Model 2" in got:
        failures.append("classification model updated by a segmentation total")
    tot_facts = [(f["subject"], f["value"]) for f in meta.get("facts", [])
                 if f["property"] == "total_captures"]
    if sorted(tot_facts) != [("model: Model", "6"), ("model: Model 3", "6")]:
        failures.append(f"total facts wrong: {tot_facts}")

    # --- no readout on the page: warns, records nothing, never raises ---
    meta = {"models": [{"name": "M", "type": "segmentation"}]}
    try:
        cli.harvest_block_total(_FakeBrowser(["no navigator here"], [False]),
                                "segmentation", meta, "s")
    except Exception as e:
        failures.append(f"missing readout raised: {e!r}")
    if meta.get("model_stats") or meta.get("facts"):
        failures.append(f"missing readout still recorded data: {meta}")

    # --- wiring: stats are harvested from each block page's INITIAL view,
    # BEFORE the screenshot step clicks "Previous" (which swaps the class
    # panel to the annotation state and loses the bars) ---
    import inspect  # noqa: E402
    src = inspect.getsource(cli.main)
    if "collect_block_stats" not in src or "harvest_model_stats" not in src:
        failures.append("cli.main does not harvest stats on collect_block_stats steps")
    spec = yaml.safe_load((REPO / "tasks" / "ov80i.yaml").read_text())
    ids = [s["id"] for s in spec["steps"]]
    for want in ("segmentation", "classification"):
        sid, bid = f"{want}_block_stats", f"{want}_block"
        step = next((s for s in spec["steps"] if s["id"] == sid), None)
        if step is None:
            failures.append(f"{sid} step missing")
            continue
        if step.get("collect_block_stats") != want:
            failures.append(f"{sid} collect_block_stats != {want!r}")
        if not step.get("always_agent"):
            failures.append(f"{sid} must always run the agent (branches on live data)")
        goal = step.get("goal", "").lower()
        if '"previous"' not in goal or "not" not in goal.split('"previous"')[0][-60:]:
            failures.append(f"{sid} goal does not forbid clicking Previous")
        if bid not in ids or ids.index(sid) != ids.index(bid) - 1:
            failures.append(f"{sid} must run immediately before {bid}: {ids}")
        block = next(s for s in spec["steps"] if s["id"] == bid)
        if "Previous" not in block["goal"]:
            failures.append(f"{bid} no longer clicks Previous — stats ordering moot")
        if block.get("collect_block_total") != want:
            failures.append(f"{bid} does not collect the post-Previous capture total")
    if "collect_block_total" not in src:
        failures.append("cli.main does not handle collect_block_total")

    # --- the prompt keeps the guards the design relies on ---
    from core import resolver  # noqa: E402
    p = resolver.STATS_PROMPT.lower()
    if "verbatim" not in p or "never estimate" not in p:
        failures.append("stats prompt lost the copy-verbatim guard")
    if "different model" not in p:
        failures.append("stats prompt lost the other-model exclusion")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL MODEL-STATS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
