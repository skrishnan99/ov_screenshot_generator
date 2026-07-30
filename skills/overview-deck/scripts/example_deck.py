#!/usr/bin/env python3
"""Worked example: a full OV camera test report built with ovdeck.

    python scripts/example_deck.py --run runs/20260730_005318 \
                                   --out out/tail-report.pptx

Copy this file, change the CONTENT block, keep the structure. Every string here
was lifted from the run's meta.json / descriptions.json — that is the standard:
no number appears on a slide unless it appears in the extracted facts.

Structure (the house shape for a camera test report):
    title -> value cards -> contents
    01 Introduction   : what the recipe is
    02 Recipe Setup   : one slide per configuration step, one per AI model
    03 Logic & Results: IO logic, library, honest observations
    closing
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ovdeck import Deck  # noqa: E402


def build(run: Path, out: Path) -> Path:
    shots = run / "deliverables" / "screenshots"
    imgs = run / "deliverables" / "images"

    d = Deck(out)

    d.title_slide(
        "OV80i AI Vision Inspection",
        "Camera 56959 Tail – Multi-Model Rail Inspection",
        meta=["Recipe #4 — OV80i, serial gsac177082", "Date: 2026.07.30"],
        image=str(imgs / "03_template_image_raw.jpg"),
    )

    d.cards("What This Recipe Demonstrates", [
        ("Three AI Models, One Recipe",
         "Classification, segmentation and a second classifier all run on a single capture"),
        ("4K Native Capture",
         "3840x2160 sensor at 20 ms exposure — every ROI is cropped from full resolution"),
        ("Trained On the Camera",
         "All three models trained on-device — no external PC, no software license"),
        ("Pixel-Level Defect Detection",
         "Segmentation measures crack area in pixels, not just a pass/fail verdict"),
        ("PLC-Native Integration",
         "EtherNet/IP byte to the PLC plus JSON results POSTed to a master controller"),
        ("23,051 Captures On Device",
         "Full image library with per-ROI results, searchable and traceable to source"),
    ])

    d.contents([
        ("01", "Introduction", "The Camera 56959 Tail recipe running on the OV80i"),
        ("02", "Recipe Setup Process", "Imaging, ROIs and three AI models trained on-camera"),
        ("03", "Inspection Logic & Results", "Node-RED pass/fail, PLC output and the image library"),
    ])

    # ---------------- 01 ----------------
    d.section("01", "Introduction", "The Camera 56959 Tail recipe running on the OV80i")

    d.statement(
        "Camera 56959 Tail — Rail Inspection",
        "Recipe #4 “Camera 56959 Tail” runs on an OV80i AI Vision System (serial gsac177082, "
        "firmware v2026.6.0-OV80i). The camera inspects a long stamped aluminium rail held in a "
        "machining fixture — checking the horn end, the punched holes along the flange, and the "
        "surface for cracks.",
        card_title="Three AI Models on One Capture",
        badge="12 ROIs total",
        bullets=[
            "Horn Quality (Classification) — 1 ROI, Pass/Fail on the horn end, type “Missing End”",
            "Cracks (Segmentation) — 5 ROIs (C1–C5), pixel-level crack area along the flange",
            "Hole Presence (Classification) — 6 ROIs (H1–H6), Pass/Fail on each punched hole",
            "PLC Trigger Mode — the line PLC triggers each capture over EtherNet/IP",
            "Operates as “Slave Camera 3”, reporting results to a master controller",
        ],
    )

    # ---------------- 02 ----------------
    d.section("02", "Recipe Setup Process", "Imaging, ROIs and three AI models trained on-camera")

    d.figure(
        "Step 1: Imaging Setup", str(shots / "02_imaging_setup.png"),
        caption="Exposure, gain, gamma and white balance were tuned for the wet, oily machining "
                "cell. The camera captures the full sensor on every PLC trigger.",
        chips=["3840x2160", "Exposure 20 ms", "Gain 1", "Gamma 30", "Daylight 6500K",
               "PLC Trigger Mode"],
        note="Screenshot composited: the aligner’s template image is rendered into the viewer "
             "area, which this settings screen leaves empty.",
    )

    d.split(
        "Step 2: Template Image and Alignment", str(shots / "03_template_image.png"),
        card_title="Alignment Skipped by Design",
        para="The rail is held in a fixed machining fixture, so the aligner is switched off and "
             "ROIs are placed directly on the captured image.",
        bullets=[
            "Skip Aligner toggle is ON — no part tracking runs",
            "One Search Area defined on the capture",
            "Trade-off: ROI positions depend on fixture repeatability",
        ],
    )

    d.figure(
        "Step 3: Inspection Setup — Define ROIs", str(shots / "04_roi_horn-quality.png"),
        caption="Twelve ROIs were drawn on the rail and assigned across the three models — Horn "
                "for the horn end, C1–C5 for cracks, H1–H6 for the punched holes.",
        chips=["Horn x 1", "C1–C5 cracks", "H1–H6 holes", "Bounding Boxes ON", "ROI Labels ON"],
    )

    d.split(
        "Model 1: Horn Quality — Classification", str(shots / "07_classification.png"),
        card_title="Missing End Check",
        para="A single ROI on the horn at the end of the rail is classified Pass or Fail.",
        chips=["Train acc 100%", "Loss 0.240"],
        bullets=["1 ROI named “Horn”, inspection type “Missing End”",
                 "2 classes — Pass (23 labels) / Fail (7 labels)",
                 "30 training images, 100 iterations",
                 "Trained 21 May 2026 in Accurate mode"],
    )

    d.split(
        "Model 2: Cracks — Segmentation", str(shots / "05_segmentation.png"),
        card_title="Pixel-Level Crack Area",
        para="Segmentation marks cracks at pixel level across five ROIs, so the IO logic can "
             "threshold on defect area rather than a bare verdict.",
        chips=["Training loss 0.028", "IoU not populated"],
        bullets=["5 ROIs — C1 through C5",
                 "1 defect class, yellow annotation mask",
                 "24 annotated crop tiles, filterable by ROI",
                 "Trained 21 May 2026 in Accurate mode"],
    )

    d.split(
        "Model 3: Hole Presence — Classification", str(shots / "04_roi_hole-presence.png"),
        card_title="Six Punched Holes",
        para="Each punched hole has its own ROI and its own Pass/Fail decision, so a single "
             "missing hole fails the part.",
        chips=["Train acc 100%", "Loss 0.154"],
        bullets=["6 ROIs — H1 through H6",
                 "2 classes — pass (74 labels) / fail (9 labels)",
                 "83 training images, 100 iterations",
                 "Trained 19 May 2026 in Accurate mode"],
    )

    d.two_up(
        "Labelled Training Crops — View All ROIs",
        str(shots / "08_view_all_rois_classification_horn-quality.png"),
        str(shots / "08_view_all_rois_classification_hole-presence.png"),
        caption="Every ROI produces its own labelled crop feeding the classification models, "
                "making labels easy to verify before training.",
        left_caption="Horn Quality — 30 of 30 ROIs",
        right_caption="Hole Presence — 83 of 83 ROIs",
    )

    d.figure(
        "Segmentation Annotations — Cracks",
        str(shots / "06_view_all_rois_segmentation_cracks.png"),
        caption="Each crack ROI is shown with its annotation mask overlaid — 24 crop tiles "
                "across C1–C5, filterable by ROI.",
    )

    d.figure(
        "Step 4: Train the AI Models on the Camera", str(shots / "09_train_models.png"),
        caption="All three models train directly on the camera — no external PC and no software "
                "license. Each model can run in Fast or Accurate mode.",
        chips=["Horn Quality 100%", "Hole Presence 100%", "Cracks loss 0.028"],
        note="Validation metrics read “---” for all three models, and two models carry an amber "
             "warning icon in the Actions column.",
    )

    d.two_up(
        "Training Reports — Per-Crop Results",
        str(shots / "horn-quality_classification.png"),
        str(shots / "hole-presence_classification.png"),
        caption="Each training run reports per-crop loss, predicted class and correctness for "
                "every image in the dataset.",
        left_caption="Horn Quality — 30 images",
        right_caption="Hole Presence — 83 images",
    )

    d.figure(
        "Model Settings — Augmentations",
        str(shots / "horn-quality_classification_settings.png"),
        caption="Augmentation is deliberately conservative: brightness and contrast only, at "
                "±0.1 with 0.50 probability. Rotation, flip, noise and blur are disabled — "
                "consistent with a fixtured part under stable lighting.",
        chips=["Brightness ±0.1", "Contrast ±0.1", "Probability 0.50", "Rotation off", "Flip off"],
    )

    # ---------------- 03 ----------------
    d.section("03", "Inspection Logic & Results",
              "Node-RED pass/fail, PLC output and the on-device image library")

    d.flow(
        "IO Logic — Node-RED Pass/Fail Flow",
        nodes=[("Slave 3 Local Input", "AI pipeline result"),
               ("Process & Prepare", "function — 3 outputs")],
        caption="All decision logic lives in one JavaScript function node. Every inspection "
                "fires three outputs in parallel.",
        fan_out=[("Final Pass/Fail", "camera verdict + IO"),
                 ("POST to Master", "192.168.1.80:1880"),
                 ("Write PLC Data", "EtherNet/IP, 1 byte")],
        cards=[("Pass requires all three",
                ["Alignment found — alignmentFound must be true",
                 "Every classification ROI must predict its Pass class",
                 "Segmentation blob area must stay under 250 px"]),
               ("Outputs",
                ["PLC: single byte — 1 = pass, 0 = fail",
                 "Master: JSON verdict, per-ROI detail, image URL",
                 "No MQTT, no timing nodes — immediate and parallel"])],
    )

    d.figure(
        "Image Library & Result History", str(shots / "12_library.png"),
        caption="Every capture is stored on the device with full metadata — filter by recipe, "
                "trigger ID, pass/fail or date, and trace any inspection back to its source image.",
        chips=["23,051 captures", "1,153 pages", "Process time 2,421 ms", "Newest first"],
    )

    d.rows(
        "Engineering Observations",
        intro="Points worth confirming with the line engineer before this configuration is signed off.",
        entries=[
            ("Validation metrics are blank",
             "All three models report “---” for validation accuracy, IoU and loss. Only training "
             "metrics exist, so generalisation to unseen parts is unverified."),
            ("Cracks model has no IoU",
             "The segmentation model shows training loss 0.028 but no mean IoU and no compiled "
             "IoU, and no training report is available for it on the camera."),
            ("Skip Aligner is enabled",
             "Alignment is disabled, yet the Node-RED pass rule still requires alignmentFound to "
             "be true — worth confirming this is intended."),
            ("Flow name does not match the recipe",
             "The Node-RED tab is titled “Slave Camera 3 V5” with no reference to 56959 or Tail."),
        ],
    )

    d.closing(
        para="Full asset set — every configuration screen, native-resolution capture and the "
             "Node-RED logic summary — was extracted from the camera on 2026.07.30.",
        summary=["OV80i AI Vision System — serial gsac177082",
                 "Firmware v2026.6.0-OV80i — 3840x2160",
                 "3 AI models — 12 ROIs — 23,051 captures"],
    )

    return d.save()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="extractor run directory")
    ap.add_argument("--out", required=True, help="output .pptx path")
    a = ap.parse_args()
    build(Path(a.run).resolve(), Path(a.out).resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
