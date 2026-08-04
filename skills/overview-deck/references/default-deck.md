# The default deck — build this unless told otherwise

**This is the required structure for an OV camera test report.** Follow it
slide for slide. The only thing that overrides it is an explicit instruction
in the user's *initial request* ("skip the Node-RED slide", "just the models",
"we only need the results"). An unstated preference is not an instruction:
do not drop, reorder or invent slides because a recipe felt thin.

What flexes is *how many* slides a section needs, never *whether* the section
appears. Where a section can be one slide or several, the rule is the same:
**all of the information must be present** — choose the arrangement that reads
best for this recipe.

Where an asset genuinely does not exist, say so on the slide or omit that one
slide and note the gap. Never fabricate the content.

---

## The sequence

### Intro — as in the blank template

Title slide in the template's three-part form, then the framing slides it
uses. See `report-brief.md` → *The house shape*.

```python
d.title_slide("OV80i", "Automotive", meta=["Horn end inspection",
              "Report by: …", "Date: 2026.07.30"])
```

Camera **model** only. Never the serial, device name, hostname or firmware
version — on this slide or any other (`content-rules.md` §1b).

---

### 1. What the inspection was about — problem, then solution

Two sections on one slide, **3–4 sentences each, maximum**.

- **Problem** — the part or product being inspected, which defects or
  conditions are checked, and the production context.
- **Solution** — how the vision system solves it: the key stages and what each
  decides, and how it worked out (training outcomes, integration in plain
  terms).

Text only; no screenshot. This is the claim the rest of the deck evidences.

```python
d.statement("Horn End Inspection", problem_para,
            card_title="The solution", bullets=solution_lines)
```

---

### 2. The inspection, seen whole — raw beside overlay

The library capture **without** the overlay next to the **same capture with
it**, so the reader sees the part and then sees what the camera decided about
it. One or two lines of text describing what they are looking at.

| Purpose | Asset |
|---|---|
| left — the part as captured | `deliverables/images/12_library_raw.jpg` |
| right — with inspection overlay | `deliverables/images/12_library_composite.png` |

```python
d.two_up("What the Camera Sees", raw, composite,
         left_caption="Captured frame", right_caption="Inspection result",
         caption="…one or two lines…")
```

If a recipe's overlay adds nothing visible, or only one of the pair exists,
use `figure` with whichever is real and say which it is. Pick whatever shows
*this* recipe's inspection most clearly — that is the goal, not the layout.

---

### 3. Imaging setup

Screenshot `deliverables/screenshots/02_imaging_setup.png` (already composited
with the template image — see `report-brief.md`).

Say what the camera and lighting achieve, and **call out any notable setting
that was deliberately used** — photometric stereo, HDR, a specific trigger
mode, an unusual exposure or lens choice. Those are the interesting decisions;
a reader skims past "gamma 50".

Resolution class and trigger mode in plain language. **No config minutiae.**

```python
d.split("Step 1: Image Settings", shot, card_title="Setup", para=…, bullets=[…])
```

---

### 4. Aligner setup

Screenshot `deliverables/screenshots/03_template_image.png`.

How alignment was set up for this part — what it orients against and why. If
alignment is **skipped**, say so and give the reason (a fixtured part arrives
in the same position every cycle); that is a legitimate, informative answer,
not a gap.

---

### 5. ROI setup — every model in the recipe

The inspection regions for **all** models. One slide per model, or several
models combined — whichever suits the recipe. All of it must appear.

Per-model asset: `meta.json` → `models[].roi_screenshot`
(e.g. `04_roi_horn-quality.png`).

For each: which regions it checks and what they cover on the part, 1–2
sentences. Three or fewer models usually fits one `figure` each or a `two_up`
pair; more than three, group them.

---

### 6. Per model — setup, training report, training settings

For **each** model, show:

| Content | Asset (`meta.json` → `models[]`) |
|---|---|
| model setup, with its training image | `05_segmentation.png` / `07_classification.png`, or `view_rois_screenshot` |
| labelled regions grid | `view_rois_screenshot` |
| training report *(if available)* | `report_screenshot` |
| training settings *(if available)* | `settings_screenshot` |

Arrange as suits the recipe — one slide per model with a `two_up`, or a short
run of slides per model. **Both "if available" items are genuinely optional in
the assets**: a segmentation model often has no training report (no View
button on the Train page). Omit that piece and move on; do not leave a hole or
imply the model was untrained.

Content per model:
- **classifier** — what it classifies, and its class names.
- **segmenter** — its defect classes and its pixel-level detection role.
- **labelled regions** — what the grid shows, roughly how many labelled
  examples, and what the labels mean for the inspection.
- **training settings** — the settings actually visible, plain values, 3–6
  short lines.
- **training results** — accuracy or loss/IoU, image counts, per-class counts
  with class names. 3–5 short lines. Pair every figure with its requirement
  (`report-brief.md` → *Numbers need their requirement*).

---

### 7. Node-RED — how the logic was set up

Explain **how the flow actually works**, and use a diagram so the reader can
follow it. `flow()` exists for exactly this:

```python
d.flow("Step 4: Inspection Logic",
       nodes=[("Trigger", "PLC signal"), ("Inspect", "3 models"),
              ("Decide", "all must pass"), ("Output", "pass/fail to PLC")],
       caption="…")
```

Source: `deliverables/report/node_red_description.md` and
`data/node_red_flow.json`.

Give the crux of the pass/fail decision rule and how results reach the plant's
systems (PLC / line controller), in plain language, 2–3 sentences.
**No node names, no variable names.** Draw the decision path, not the graph
topology — see `layouts.md` on not diagramming something you have not
understood.

---

### 8. Library

**Place the skill's owned library skeleton** and swap in this run's
screenshot. Do not re-author it:

```python
d.skeleton_slide("library", image=run/"deliverables/screenshots/12_library.png")
```

The slide arrives verbatim — title, bullets, styling — with the screenshot
dropped into its "Insert screenshot here" hole, exactly at the hole's frame.

---

### 9. Standing closing slides

Carry the standing closing run **verbatim**. These are company content:
re-authoring them through the layout engine produces a different approximation
every time, which is why they used to be inconsistent or missing.

```python
from template_slides import DEFAULT_CLOSING
for name in DEFAULT_CLOSING:   # capabilities, defect_generator, integration,
    d.skeleton_slide(name)     # team, thank_you — the template's own order
```

**All five closing slides, in the template's own order** (its slides 11-15):
capabilities ("5 factors"), defect_generator, integration, team & locations,
thank you. Each appears exactly once — `skeleton_slide` raises on a repeat —
and none is optional unless the user's request says to drop it. Iterate
`DEFAULT_CLOSING` rather than writing the names out, so the order cannot be
retyped wrongly.

The slides live as owned single-slide skeletons in `assets/skeletons/`
(extracted once from the blank template; `template_slides.py --extract`
regenerates them and reapplies the recorded fixups when the company template
changes). Run `template_slides.py` to list them with their holes.

If a filled skeleton looks wrong in the render, the remedy is the skeleton
file (a committed maintainer fix that then holds for every deck) or shorter
content — never a patch to the built deck.

Transplanted slides are exempt from the layout checks and the brand audit by
design — this engine did not lay them out, they legitimately use the brand's
other faces and colours, and a build script cannot fix the corporate template.
`brandcheck` names which slides it skipped. Do not re-author one to silence a
finding.

---

## Checklist before building

- [ ] Problem and solution, ≤ 4 sentences each
- [ ] Raw and overlay library capture, side by side
- [ ] Imaging setup, with any notable setting called out
- [ ] Aligner setup — configured, or skipped with the reason
- [ ] ROI setup covering **every** model
- [ ] Every model: setup, labelled regions, plus report and settings where they exist
- [ ] Node-RED explained with a diagram, no node names
- [ ] Library slide with this run's screenshot
- [ ] Library + closing slides placed via skeleton_slide(), not re-authored
- [ ] Closing run is DEFAULT_CLOSING, complete and in template order
- [ ] No camera serial, device name, hostname, firmware version or capture id
      anywhere in the deck's text

If you are dropping any of these, you should be able to point at the sentence
in the user's request that told you to.
