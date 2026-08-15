"""Shared definitions of the block-capture quality criteria.

Two judges answer the same two questions about a Block-page capture: the
extractor's judge over the live viewer PIXELS at capture time
(cli.judge_block_capture) and the deck's judge over the recorded vision
DESCRIPTION at build time (matching.block_quality_call). The criteria live
here ONCE so the two prompts cannot drift apart — the register rules solved
this same duplication hazard the same way. tests/test_capture_pick.py pins
that both prompts actually embed these.
"""

from __future__ import annotations

# What counts as a product image — the judgment that separates a real part
# photograph from the black/blank frames that once shipped on decks.
PRODUCT_CRITERION = (
    "does it show a real photograph of a physical part/product? A dark or "
    "dim photograph of a real part is TRUE. A black, blank, grey or "
    "featureless frame, or content clearly not a manufactured part, is "
    "FALSE."
)

# The carve-out BOTH judges need on the annotation side. Two facts about
# these UIs: the black frames that motivated all of this carried empty
# labelled outlines; and ROI outline rectangles — coloured or not — are
# STANDING CHROME drawn on every capture, each carrying its region's NAME
# label (e.g. "Trumpet Front"), annotated or not, over real images too.
EMPTY_OUTLINES_NOTE = (
    "ROI outline rectangles are standing chrome drawn on EVERY capture — "
    "coloured or not, and even over a real product image — and each "
    "carries its region's NAME label (e.g. \"Trumpet Front\"): neither the "
    "outlines nor these region-name labels count as annotations. "
    "Annotations are content ADDED to a region: class-verdict chips or "
    "painted masks. Empty outlines over a blank/black canvas count as "
    "nothing at all."
)

# What counts as an annotation is BLOCK-TYPE-SPECIFIC — labels are not
# masks: a segmentation frame whose ROIs carry name labels but no painted
# masks is NOT annotated (proven live: the same black frame is tier 4 on a
# segmentation block and tier 3 on a classification block).
_ANNOTATION_KIND = {
    "classification": (
        "ROI boxes carrying class labels — coloured boxes and/or label "
        "chips (e.g. red/green/yellow class tags) attached to them"
    ),
    "segmentation": (
        "painted pixel masks or brush strokes marking defect areas inside "
        "the ROIs (region outlines alone are not enough)"
    ),
}


def annotation_criterion(block_type: str) -> str:
    return _ANNOTATION_KIND.get(
        str(block_type or "").strip().lower(),
        "class labels, coloured boxes or painted masks on the ROIs",
    )


# The Library viewer's overlay is the INSPECTION overlay (regions, marks,
# result labels the inspection draws over the photograph) — a different
# thing from training annotations, so it gets its own definition. The
# search-area / alignment rectangle (e.g. a cyan "Search Area" box) is
# standing chrome drawn on essentially every capture and must not satisfy
# this criterion, or overlay degenerates to always-true.
INSPECTION_OVERLAY_CRITERION = (
    "are AI inspection RESULT overlays drawn ON the image — per-region "
    "result boxes, masks, marks or verdict labels rendered over the "
    "photograph by the inspection? The search-area/alignment rectangle "
    "(e.g. a box labelled \"Search Area\") is standing chrome on every "
    "capture and does NOT count. A photograph with nothing beyond that "
    "standing chrome is FALSE."
)

# The extractor's capture-pick preference ladder, shared so the deck can
# read a recorded pick and both sides describe tiers in the same words.
PICK_TIER_MEANING = {
    1: "product + annotated",
    2: "product image, unannotated",
    3: "annotated, but no real product image",
    4: "no product image and no annotations",
}

# The library pick's ladder, same shape, overlay flavour.
LIBRARY_TIER_MEANING = {
    1: "product + inspection overlay",
    2: "product image, no overlay",
    3: "overlay, but no real product image",
    4: "no product image and no overlay",
}
