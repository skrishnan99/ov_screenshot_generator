"""The block-page capture is SEARCHED for, not taken on faith.

"Previous" from live view lands on the LAST source capture, which carries
no guarantee of showing the part or its annotations (a real deck shipped
black block pages this way). pick_annotated_capture judges the current
capture first, short-circuits on product+annotated, and otherwise cycles
capture 1, 2, ... maintaining a best-partial ladder:

    tier 1  product + annotated   (short-circuit)
    tier 2  product, unannotated
    tier 3  annotated, no product
    tier 4  neither               (fallback: last capture visited)

What this suite pins (DOM access is behind tiny patchable helpers, so the
search runs as a pure state machine):

- short-circuit on the starting capture: zero navigation,
- search order last -> 1 -> 2 -> ... with the starting capture never
  re-judged, stopping the moment a tier-1 capture appears,
- best-partial: tier 2 beats tier 3, first-seen wins ties, and the viewer
  is jumped BACK to the winner before the caller's screenshot,
- nothing qualifies -> the last capture visited stays up (tier 4),
- the scan cap truncates the search, logged, and the cap counts judged
  captures including the first,
- a missing navigator or a judge crash degrades to the current view —
  the hook never raises,
- the spec activates the hook on both block steps.

Run: uv run python tests/test_capture_pick.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

import cli  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class _Rig:
    """Stateful stand-in for the block page: capture index, total, and a
    tier per capture. Patches cli's DOM helpers and the judge."""

    def __init__(self, tiers: dict, total: int, start: int | None = None,
                 nav_present: bool = True, judge_crash_at: int | None = None):
        self.tiers = tiers                  # index -> (product, annotated)
        self.total = total
        self.cur = start if start is not None else total
        self.nav_present = nav_present
        self.judge_crash_at = judge_crash_at
        self.judged: list = []
        self.gotos: list = []
        self.nexts = 0
        self.waits = 0

    # ---- patched helpers ----
    def nav_state(self, browser):
        if not self.nav_present:
            return self.cur, self.total, None
        return self.cur, self.total, "#cap"

    def goto(self, browser, sel, idx):
        self.gotos.append(idx)
        self.cur = idx
        return True

    def next(self, browser):
        self.nexts += 1
        self.cur = self.cur + 1 if self.cur < self.total else 1
        return True

    def wait(self, browser):
        self.waits += 1

    def judge(self, browser, block_type):
        if self.judge_crash_at is not None and self.cur == self.judge_crash_at:
            raise RuntimeError("vision judge unavailable")
        self.judged.append(self.cur)
        product, annotated = self.tiers.get(self.cur, (False, False))
        return {"product_image": product, "annotated": annotated, "reason": "rig"}


def _run(rig, cap=cli.CAPTURE_SCAN_CAP):
    saved = (cli._capture_nav_state, cli._goto_capture, cli._next_capture,
             cli._wait_capture_loaded, cli.judge_block_capture)
    try:
        cli._capture_nav_state = rig.nav_state
        cli._goto_capture = rig.goto
        cli._next_capture = rig.next
        cli._wait_capture_loaded = rig.wait
        cli.judge_block_capture = rig.judge
        return cli.pick_annotated_capture(object(), "classification", cap=cap)
    finally:
        (cli._capture_nav_state, cli._goto_capture, cli._next_capture,
         cli._wait_capture_loaded, cli.judge_block_capture) = saved


def main() -> int:
    failures = []

    # ---- tier-1 starting capture: short-circuit, zero navigation ----
    rig = _Rig({16: (True, True)}, total=16)
    rec = _run(rig)
    if rec["chosen"] != 16 or rec["tier"] != 1:
        failures.append(f"short-circuit: {rec}")
    if rig.gotos or rig.nexts:
        failures.append("navigated despite a tier-1 starting capture")

    # ---- search order: last first, then 1, 2, ...; stop at tier 1 ----
    rig = _Rig({3: (True, True)}, total=16)
    rec = _run(rig)
    if rig.judged != [16, 1, 2, 3]:
        failures.append(f"search order: {rig.judged}")
    if rec["chosen"] != 3 or rec["tier"] != 1:
        failures.append(f"tier-1 stop: {rec}")
    if rig.cur != 3:
        failures.append(f"viewer not left on the winner: {rig.cur}")

    # ---- the starting capture is never re-judged mid-sequence ----
    rig = _Rig({5: (True, True)}, total=4, start=2)
    rec = _run(rig)
    if rig.judged.count(2) != 1:
        failures.append(f"starting capture re-judged: {rig.judged}")

    # ---- best partial: tier 2 beats tier 3; first-seen wins; jump back ----
    rig = _Rig({2: (False, True), 4: (True, False), 6: (True, False)}, total=8)
    rec = _run(rig)
    if rec["chosen"] != 4 or rec["tier"] != 2:
        failures.append(f"best partial: {rec}")
    if rig.cur != 4:
        failures.append(f"viewer not jumped back to best: {rig.cur}")
    if rig.judged != [8, 1, 2, 3, 4, 5, 6, 7]:
        failures.append(f"full-cycle order: {rig.judged}")

    # ---- tier 3 chosen only when no tier 2 exists ----
    rig = _Rig({5: (False, True)}, total=6)
    rec = _run(rig)
    if rec["chosen"] != 5 or rec["tier"] != 3:
        failures.append(f"tier-3 fallback: {rec}")

    # ---- nothing qualifies: the LAST capture visited stays up, tier 4 —
    # a tier-4 frame must never be "best" and never trigger a jump-back
    rig = _Rig({}, total=5)
    rec = _run(rig)
    if rec["tier"] != 4 or rec["chosen"] != 4 or rig.cur != 4:
        failures.append(f"tier-4 fallback: {rec} cur={rig.cur}")
    if 1 in rig.gotos[1:]:
        failures.append(f"jumped back to a tier-4 capture: {rig.gotos}")

    # ---- the cap truncates: judged count == cap, including the first ----
    rig = _Rig({}, total=100)
    rec = _run(rig, cap=10)
    if len(rig.judged) != 10:
        failures.append(f"cap not honoured: judged {len(rig.judged)}")
    if rec["tier"] != 4 or rec["chosen"] != rig.judged[-1]:
        failures.append(f"capped tier-4 fallback: {rec} judged={rig.judged[-4:]}")

    # ---- no navigator: keep the current view, still recorded ----
    rig = _Rig({}, total=16, nav_present=False)
    rec = _run(rig)
    if rec["chosen"] != 16 or rig.gotos or rig.nexts:
        failures.append(f"missing-navigator degrade: {rec}")

    # ---- a judge crash mid-cycle degrades, never raises ----
    rig = _Rig({4: (True, True)}, total=16, judge_crash_at=2)
    try:
        rec = _run(rig)
    except Exception as e:
        failures.append(f"judge crash escaped the hook: {e}")

    # ---- the spec activates the hook on both block SCREENSHOT steps,
    # the flag's value naming the block type for the vision judge ----
    spec = yaml.safe_load((REPO / "tasks" / "ov80i.yaml").read_text())
    for sid, want_type in (("segmentation_block", "segmentation"),
                           ("classification_block", "classification")):
        s = next(x for x in spec["steps"] if x["id"] == sid)
        if s.get("pick_annotated_capture") != want_type:
            failures.append(f"{sid} pick flag: {s.get('pick_annotated_capture')!r}")
        if not s.get("screenshot"):
            failures.append(f"{sid} is not a plain screenshot step any more")

    # ---- the hook fires in the plain-screenshot capture path, before the
    # screenshot is taken (a live test caught it wired to the wrong path) ----
    import inspect
    src = inspect.getsource(cli.main)
    call = src.index("pick_annotated_capture(")
    shot = src.index('name = f"{step[\'screenshot\']}.png"')
    if not call < shot:
        failures.append("pick hook does not precede the screenshot capture")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL CAPTURE-PICK CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
