"""Shared fixture for the v2 spec-generator tests: a miniature but complete
extractor run written into a tempdir — two models, facts, descriptions, real
(tiny) images so ovdeck can measure them. No live run required."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# Isolate every suite from the developer's real ~/.ov-report-generator: the
# engineer profile there would otherwise leak into built decks and make
# assertions machine-dependent.
os.environ["OV_REPORT_DATA_DIR"] = tempfile.mkdtemp(prefix="sg-testdata-")

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "overview-deck" / "scripts"
for p in (str(REPO), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

MODELS = [
    {"name": "Model S", "type": "segmentation", "slug": "model-s",
     "view_rois_screenshot": "deliverables/screenshots/06_view_model-s.png"},
    {"name": "Horn Quality", "type": "classification", "slug": "horn-quality",
     "view_rois_screenshot": "deliverables/screenshots/hq_view.png"},
    # Never trained: ROIs exist, but the trained filter must exclude it from
    # the combined-ROI slide and the per-model blocks.
    {"name": "Edge Check", "type": "segmentation", "slug": "edge-check"},
]

# Real runs identify the per-TYPE block screenshot through the assets index
# (models envelopes carry no block_screenshot); the fixture mirrors that.
MANIFEST_ASSETS = [
    {"path": "deliverables/screenshots/05_segmentation.png",
     "kind": "screenshot", "role": "deliverable", "step": "segmentation_block"},
    {"path": "deliverables/screenshots/07_classification.png",
     "kind": "screenshot", "role": "deliverable", "step": "classification_block"},
    {"path": "deliverables/screenshots/12_library.png",
     "kind": "screenshot", "role": "deliverable", "step": "library"},
]

SCREENSHOTS = {
    "02_imaging_setup.png": "The Imaging Setup screen: camera settings panel with exposure, "
                            "white balance and trigger mode beside the main viewer.",
    "03_template_image.png": "The Template Image and Alignment screen with Skip Aligner enabled "
                             "and one search area drawn; the template viewer is blank.",
    "04_roi_model-s.png": "The Inspection Setup screen with Model S selected; 26 regions of "
                          "interest drawn across the part surface.",
    "04_roi_horn-quality.png": "The Inspection Setup screen with Horn Quality selected; regions "
                               "over the horn end of the part.",
    "04_roi_edge-check.png": "The Inspection Setup screen with Edge Check selected; two regions "
                             "along the edge of the part.",
    "05_segmentation.png": "The Segmentation block page showing Model S with a capture loaded.",
    "07_classification.png": "The Classification block page showing Horn Quality with a capture.",
    "06_view_model-s.png": "Model S's labelled-regions view: a grid of labelled ROI crops with "
                           "class labels Dent, Discolor, Scratch.",
    "model-s_settings.png": "Model S's training settings dialog with augmentation controls.",
    "hq_report.png": "Horn Quality's training report: accuracy and image counts after training.",
    "hq_view.png": "Horn Quality's labelled-regions grid: Pass and Fail labelled crops.",
    "hq_settings.png": "Horn Quality's training settings dialog.",
    "12_library.png": "The camera's Library page: capture browser with thumbnails of stored "
                      "captures and a selected capture's details panel.",
    "10_io_node_red.png": "The IO Logic screen: the embedded Node-RED flow editor with wired "
                          "nodes from the inspection input to the pass/fail outputs.",
}

FACTS = [
    {"subject": "camera", "property": "product_name", "value": "OV80i AI Vision System", "source": "01"},
    {"subject": "recipe", "property": "name", "value": "Widget Inspection", "source": "01"},
    {"subject": "recipe", "property": "skip_aligner", "value": "on", "source": "03"},
    {"subject": "recipe", "property": "trigger_mode", "value": "Manual HMI Trigger", "source": "02"},
    {"subject": "recipe", "property": "resolution", "value": "3840x2160", "source": "02"},
    {"subject": "model: Model S", "property": "class_count", "value": "3", "source": "05"},
    # The Train screen's per-model trained signal: a date, or "Never trained".
    {"subject": "model: Model S", "property": "last_trained", "value": "8/3/2026 12:32:22 PM", "source": "05"},
    {"subject": "model: Horn Quality", "property": "last_trained", "value": "7/28/2026 9:14:03 AM", "source": "07"},
    {"subject": "model: Edge Check", "property": "last_trained", "value": "Never trained", "source": "05"},
    # legacy Train-screen numbers: the results-card fallback when no
    # model_stats harvest exists (runs predating it)
    {"subject": "model: Model S", "property": "training_images", "value": "83", "source": "05"},
    {"subject": "model: Horn Quality", "property": "training_images", "value": "40", "source": "07"},
]

# The block-page labelling harvest: per model, the capture-navigator total
# and every class bar. final_train_images = min(total, max bar);
# Model S -> min(6, 18) = 6, Horn Quality -> min(16, 6) = 6, Edge Check's
# all-zero bars contribute nothing. Recipe card = max = 6.
MODEL_STATS = {
    "Model S": {
        "type": "segmentation", "total_captures": 6, "source": "seg block",
        "classes": [
            {"roi": "", "label": "Defect", "class_token": "", "labelled_images": 18},
        ],
    },
    "Horn Quality": {
        "type": "classification", "total_captures": 16, "source": "cls block",
        "classes": [
            {"roi": "Horn", "label": "Pass", "class_token": "pass_horn", "labelled_images": 6},
            {"roi": "Horn", "label": "Fail", "class_token": "fail_horn", "labelled_images": 4},
        ],
    },
    "Edge Check": {
        "type": "segmentation", "total_captures": 6, "source": "seg block",
        "classes": [
            {"roi": "", "label": "Chip", "class_token": "", "labelled_images": 0},
        ],
    },
}


def _png(path: Path, size=(64, 40), colour=(60, 60, 90)):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path)


def make_run(tmp: Path) -> Path:
    run = Path(tmp) / "run"
    (run / "data").mkdir(parents=True)
    (run / "deliverables" / "report").mkdir(parents=True)
    (run / "data" / "manifest.json").write_text(json.dumps({
        "variant": "ov80i", "ui_version": "v1-test", "recipe_input": "Widget Inspection",
        "steps": [],
        "assets": MANIFEST_ASSETS,
    }))
    (run / "data" / "meta.json").write_text(json.dumps({
        "models": MODELS,
        "facts": FACTS,
        "model_stats": MODEL_STATS,
        "library_main_image": {
            "file": "deliverables/images/12_library_raw.jpg",
            "composite": {"file": "deliverables/images/12_library_composite.png"},
        },
    }))
    (run / "deliverables" / "report" / "descriptions.json").write_text(
        json.dumps(SCREENSHOTS))
    (run / "deliverables" / "report" / "node_red_description.md").write_text(
        "# flow\nPass only when every area's defect pixels stay under 20; the "
        "verdict feeds the camera's pass/fail result.")
    for name in SCREENSHOTS:
        _png(run / "deliverables" / "screenshots" / name)
    _png(run / "deliverables" / "images" / "12_library_raw.jpg", colour=(20, 20, 20))
    _png(run / "deliverables" / "images" / "12_library_composite.png", colour=(90, 20, 20))
    return run


def keyword_assign(holes, catalog):
    """Deterministic stand-in for the assigner: pick the first catalog entry
    whose description shares the most informative words with the hole's
    expects. Returns the ASSIGN_SCHEMA shape."""
    out = []
    used = set()
    for h in holes:
        want = set(w for w in h.expects.lower().replace("'s", " ").split() if len(w) > 3)
        best, score = None, 0
        for c in catalog:
            if c["path"] in used:
                continue
            have = set(c["description"].lower().replace("'s", " ").split())
            s = len(want & have)
            if s > score:
                best, score = c["path"], s
        if best and score >= 3:
            used.add(best)
            out.append({"hole": h.id, "path": best, "confidence": "high", "reason": "keyword"})
        else:
            out.append({"hole": h.id, "path": None, "confidence": "high", "reason": "no fit"})
    return out


LINT_CLEAN = {
    "text": "The camera checks the part surface for dents and scratches across "
            "26 regions.",
    # metric-free: this value is served into EVERY lines token, including
    # training-slide tokens that carry `ban: metrics`
    "lines": "Decides: dent or scratch per region\nClasses: Dent, Discolor, Scratch",
    "pairs": "Inspection result | from the AI models\nDecision rule | 20-pixel budget",
}


def canned_resolver(reqs, material):
    """Stand-in for content.resolve_call: shape-correct, lint-clean values
    that respect each token's max_chars (text is sliced; the structured
    shapes are already short)."""
    out = {}
    for r in reqs:
        v = LINT_CLEAN[r.shape]
        if r.shape == "text" and len(v) > r.max_chars:
            v = v[: r.max_chars].rstrip()
        out[r.id] = v
    return out
