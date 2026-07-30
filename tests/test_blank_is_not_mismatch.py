"""An empty image area is not a matching failure.

A recipe legitimately has nothing to show on some screens: "Skip Aligner" is
on so there is no template image, or the trigger is manual and no capture has
been taken. The screenshot is then correct — a black or grey preview IS the
state of the camera, and the deck's own spec plans for it ("alignment skipped
for fixtured parts").

The matcher's verify prompt used to list "an obviously unloaded/blank view"
as a reason to answer match = false. Under `--verify-images` that rejected the
imaging and aligner screenshots, widened their slots, found no replacement,
and — because both are the slide's FIRST image — deleted two slides out of the
numbered sequence.

Identity is the matcher's question ("is this the screen the slot expects?").
Whether the camera had anything to display is the extractor's, and belongs in
the run's failure ledger, not in a drop decision. These are prompt contracts,
so they are asserted as text: the point is that nobody reintroduces the
conflation.

Run: uv run python tests/test_blank_is_not_mismatch.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deck.agent_slide import VERIFY_PROMPT as SLIDE_PROMPT  # noqa: E402
from deck.matcher import VERIFY_PROMPT as MATCH_PROMPT  # noqa: E402

# The exact clause that caused the deletions, plus close variants.
BANNED = [
    "or an obviously unloaded/blank view all mean",
    "unloaded/blank view all mean\nmatch = false",
]


def main() -> int:
    failures = []
    low = MATCH_PROMPT.lower()

    # --- the matcher must not treat blankness as a mismatch ---
    for clause in BANNED:
        if clause.lower() in " ".join(low.split()):
            failures.append(f"matcher prompt still fails blank views: {clause!r}")

    if "does not mean match = false" not in low:
        failures.append("matcher prompt no longer states that an empty area still matches")
    for cue in ("blank", "black", "grey", "placeholder"):
        if cue not in low:
            failures.append(f"matcher prompt lost the '{cue}' case")
    if "skip aligner" not in low:
        failures.append("matcher prompt lost the concrete disabled-step example")

    # --- but a genuinely wrong screen must still fail ---
    for cue in ("wrong screen", "mismatched model"):
        if cue not in low:
            failures.append(f"matcher prompt no longer rejects: {cue}")
    if "match = false" not in low:
        failures.append("matcher prompt can no longer reject anything at all")

    # --- the slide reviewer must judge craft, not screenshot content ---
    slide_low = SLIDE_PROMPT.lower()
    if "not the content of the camera screenshot" not in slide_low:
        failures.append("slide prompt does not separate layout from screenshot content")
    if "is not a defect" not in slide_low:
        failures.append("slide prompt no longer exempts blank screenshots")
    # ...while still catching real slide defects.
    for cue in ("overlapping elements", "text cut off", "match=false"):
        if cue not in slide_low:
            failures.append(f"slide prompt lost its defect criteria: {cue}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL BLANK-TOLERANCE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
