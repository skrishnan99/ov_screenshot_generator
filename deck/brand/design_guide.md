# Overview test-report deck — design guide

Everything below is measured from `corpus/`. Numbers are inches from the top-left
corner of the slide unless stated. Point sizes are literal.

## The canvas

10.00 × 5.62 in (16:9), every slide, no exceptions. Renders are 960 × 540 px, so
**96 px = 1 in** if you are measuring off a PNG.

Fonts: Proxima Nova (headlines and body), Montserrat SemiBold (display numbers
and the near-white/branded headlines), Arial and Calibri only where a Google
export already uses them. Nothing else.

Two chrome systems, and a slide uses exactly one of them:

- **Sidebar chrome** — the brand sidebar image at `L 0, T 0, W 0.36, H 5.62`,
  full-bleed: a `#532EE3` bar with letterspaced white "OVERVIEW" rotated 90°
  (reading bottom-to-top) near its lower end. It is a baked asset — reuse it,
  never redraw it. Slides with the sidebar carry **no logo**. They also carry a
  slide-number placeholder at `L 0.06, T 0.24, W 0.39, H 0.21`, right-aligned
  with 0.09 insets, so the digit sits against the bar's inner edge.
- **Logo chrome** — the wordmark from `brand/logos/`, at one of two sizes the
  corpus actually uses: small `1.22 × 0.23` (title and stat slides) or large
  `3.70 × 0.69` (dark openers and the closer). Its slide-number placeholder,
  when present, is bottom-right at `L 9.36, T 5.19, W 0.60, H 0.43`.

Sidebar and logo never appear on the same slide.

---

## Recognising the family

Ask what the slide's content *is*:

| If the content is… | It is a… |
|---|---|
| A configuration action the engineer performed in the camera UI, evidenced by a screenshot of that screen | **Numbered configuration step** |
| The deck's opening identity (product + recipe), or a section break announcing what follows | **Title / section opener** |
| A headline claim plus screenshot evidence of an outcome, on a near-white ground | **Light headline + evidence** |
| Two or three bare numbers with labels | **Stat card** |
| Product capability, benefits, company facts — the same on every deck | **Static information** |

Numbered steps and stat cards are the two you will actually build. Get the
numbered step exactly right; it is the deck's spine.

---

## Family 1 — Numbered configuration step

Five templates (`imaging_setup`, `aligner_setup`, `roi_setup`,
`classifier_setup`, `segmenter_setup`) are **byte-for-byte identical in
geometry**. That is the standard. Match it exactly.

Slide background: explicit `#FFFFFF`. Sidebar chrome as above.

**Title.** Rect text shape at `L 0.74, T 0.42, W 8.63, H 0.50`, zero insets, no
autofit, top-anchored, left-aligned, line spacing 1.20. Proxima Nova **Bold
30 pt**, `#000000`.

Wording is `Step {{ step_no }}: ` — the literal token, a colon, one space —
followed by a short imperative or noun phrase naming the screen:
"Setup Camera and Lighting", "Setup Inspection Regions", "Setup Template Image
and Aligner", "Setup classifier inspection", "Check classification dataset".
Agent-built steps use sentence case with an em dash and the model name:
"All labeled regions — Solder Classifier", "Training settings — …",
"Training report — …". Sentence case is the newer, preferred convention.
(`nodered_setup` omits the colon; that is a defect in that one file, not a
pattern — use the colon.)

Two lines is fine: at 30 pt / 1.20 the second line bottoms out around 1.42",
clearing the image. Three lines collides with the image — cut the words instead.

**Body copy.** Text box at `L 6.30, T 2.85, W 3.60`, height 0.34 growing
downward (shape-to-fit-text), insets 0.05 left/right and 0.02 top/bottom, line
spacing 1.0. Proxima Nova **Bold 17 pt**, `#532EE3`.

It is a right-hand column, not a caption: 1–2 short sentences, or 3–6 short
newline-separated lines for settings and report slides. The fixed 2.85" top
optically centres a short block against the image band (1.73–4.87, centre 3.30).
Let it grow down; never move its top or its left.

**Image.** `L 0.70, T 1.73, W 5.48, H 3.14` — aspect ratio 1.743. Fit the
screenshot inside this box preserving its own aspect ratio (the corpus images
match their box to within 0.1%; the hard limit is 4% distortion). Never stretch,
never crop to fill. If the source is a different shape, keep the box's centre and
shrink the dimension that doesn't fit.

Note the deliberate 0.04" offset: **text rail is 0.74, image left is 0.70.**
Keep both.

**Composition.** Left two-thirds evidence, right third explanation, 0.12"
gutter between the image's right edge (6.18) and the body box. The bottom 0.75"
of the slide and the top-right quadrant are empty. That emptiness is the design —
do not fill it.

### Two sanctioned variants

- **Wide image, no body** (`cls_rois_setup`): title box widens to `W 8.81`,
  image goes to `L 0.74, T 1.08, W 7.84, H 4.50`, body column is deleted. Use
  this when the screenshot is a dense grid of thumbnails or a settings dialog
  that would be unreadable at 5.48".
- **Text only, no image** (`nodered_setup`, `training_stats`): body moves to
  `L 0.74, T 1.19, W 7.45, H 3.18`, insets 0.10, **Arial 18 pt** regular in the
  inherited near-black, line spacing 1.0, fixed height. Use when there is no
  screen to show.

---

## Family 2 — Title and section openers

**Recipe title** (`recipe_title`, the deck's first slide). Flat `#493691` ground
on the left; from `x 5.01` to the right edge a full-height panel with a
`#241465 → #493691` vertical gradient, horizontal hairlines every ~0.535" and a
faint grid; the camera product shot sits on a light-purple disc at
`L 4.51, T 0.64, W 5.13, H 4.19`.

- Small wordmark: `L 0.31, T 0.51, W 1.22, H 0.23`.
- Product name ("OV80i"): `L 0.31, T 0.85, W 4.29, H 0.76`, middle-anchored,
  Montserrat SemiBold **Bold 40 pt** white, line spacing 0.90.
- Recipe title: `L 0.12, T 3.75, W 4.70, H 1.33`, top-anchored, Proxima Nova
  **Bold 36 pt** `#FFFFFF`, line spacing 1.218. This is the one place in the
  deck where text starts at 0.12 rather than the 0.30 rail — it is intentional
  optical alignment with the 40 pt name above.

Whitespace: the whole band between 1.6" and 3.7" on the left half is empty.

**Dark section opener** (`configuring_ov80i`, `results`). Ground `#101025`
(near-black); a large, barely-there gear outline bottom-right; the camera photo
bleeding off the right and bottom at `L 4.80, T 0.56, W 5.20, H 5.06`. Large
wordmark at `L 0.27, T 0.26, W 3.70, H 0.69`. Slide number bottom-right.

Headline Proxima Nova **Bold 39 pt** `#FFFFFF`, line spacing 1.218, left rail
0.54–0.61, vertically around the middle (`T 2.46` for a one-liner, `T 1.78` when
it runs to three lines). Directly beneath it, in the same text box and same
spacing, an optional kicker at **Bold 20 pt** `#7F60F9`
("Power of a vision system, ease-of-use of a vision sensor.").

Headlines here are full sentences ending in a period, and may carry the number
that matters ("Results: 1-2 hours to configure, setup, label, train and deploy
each inspection model.").

**Closer** (`contact`). Near-black ground, large wordmark top-left,
"Contact information" Proxima Nova **Bold 30 pt** white, centred in a box at
`L 0.82, T 2.52, W 4.39, H 0.59`; the three contact lines Arial 16 pt at
`L 6.10, T 2.05, W 2.62` with 0.48"-wide glyphs at `x 5.66`.

---

## Family 3 — Light headline + evidence

`results_image` (the inspection overview) and `library`. Background `#FAF9FE`,
inherited from the layout — do not draw a rectangle for it. **No sidebar, no
logo, no slide number.** These slides are chrome-free on purpose.

- **Headline**: `L 0.30, T 0.46, W 9.39, H 0.67`, middle-anchored, Montserrat
  SemiBold **Bold 30 pt** `#241465`, line spacing 1.0. On static information
  slides it is a claim ending in a period ("Library: Store >20,000 images on
  device."). On the overview slide it is just the recipe name, no period.
- **Sub-line**: `L 0.32, T 1.14, W 9.37`, height 0.24 auto-growing, Proxima Nova
  **Bold 14 pt** `#241465`, line spacing 1.4286. Exactly one sentence.
- **Evidence placeholders**: solid `#190E42` rectangles with a 1 pt `#A896F0`
  border, holding centred Arial 14 pt white placeholder text. Two layouts:
  - *Two-up*: `0.18, 1.90, 4.62 × 3.06` and `5.20, 1.90, 4.62 × 3.06` — 0.40
    gutter, 0.18 outer margins. Used for raw part vs. overlaid part.
  - *One-up plus bullets*: image `0.79, 1.66, 5.65 × 3.60`; bullet text box
    `6.92, 1.66, 2.85 × 1.35`, Proxima Nova **Bold 14 pt** `#241465`, line
    spacing 1.0, 0.167" space before each bullet after the first. Two bullets,
    two lines each — that is the whole budget.

---

## Family 4 — Stat card

`concise_results_classifier` / `concise_results_segmenter`. Ground `#493691`;
on top, one rounded rectangle at `L 0.31, T 0.30, W 9.39, H 5.03` (corner
radius adjustment 0.025) filled with a vertical gradient, `#493691` at the top
to `#241465` at the bottom. Small wordmark at `L 0.67, T 0.45, W 1.22 × 0.23`.

Title "Results": `L 0.66, T 0.49, W 6.55, H 1.09`, middle-anchored, Montserrat
SemiBold **Bold 40 pt** white, line spacing 0.80.

Three columns on a 3.105" pitch at `x = 0.75, 3.86, 6.96`:

- value: `T 2.74, W 2.28, H 1.09`, middle-anchored, autofit text-to-shape,
  0.10 insets, Montserrat SemiBold **Bold 52.25 pt** white, line spacing 0.90
- label: `T 3.76, W 2.70, H 0.50`, Montserrat SemiBold **Bold 17.46 pt**
  `#A896F0`, line spacing 0.90

Values are short — "100%", "83", "3 days" — and a missing value is exactly one
em dash, `—`. Labels are Title Case: "Training Accuracy" (or "Mean IoU" for a
segmenter), "Training Images", "Deployment Time". The card leaves a full inch
empty below the labels; leave it empty.

Stat cards are **not** part of the numbered run and carry no step prefix.

---

## Family 5 — Static information slides

Two dialects, both fixed content shipped with every deck.

**Sidebar dialect** (`basic_camera_info`, `advanced_camera_info`) — white
ground, sidebar chrome, text rail 0.74. Title Proxima Nova **Bold 30 pt** at
`T 0.21–0.36`; an optional deck line under it in **Bold 17 pt** `#532EE3` at
`0.74, 0.80, W 9.13`. Icon chips are 0.56 × 0.56 rounded squares filled
`#4316CE` with a ~0.24" white glyph centred and a Montserrat 14 pt `#333333`
label starting 0.71" right of the chip's left edge. Numbered lists are Proxima
Nova **Bold 15 pt** `#532EE3` at line spacing 1.50, auto-numbered decimal.
Closing paragraphs are Proxima Nova 14 pt `#000000` at line spacing 1.4286, with
a final one-line kicker at Bold 17 pt.

**Imported marketing dialect** (`defect_generator_info`, `integration_info`,
`team_and_locations`, `unique_factors`) — Google exports in Calibri at 8.6–13.5 pt
with `#F5F5F7` cards, `#735CFF` rules and numerals, a `#532EE3` stat block, the
compact wordmark top-right at `8.70, 0.26, 1.05 × 0.22`, and a
"09  /  15" page counter in Calibri 8.63 pt `#666666` bottom-right.
`team_and_locations` and `unique_factors` split the canvas with a full-height
panel (`#1A1A2E` at `0, 0, 2.70 × 5.62`, or a purple panel to `x 5.78`).

Do not author new slides in the marketing dialect. Its type sizes are below the
brand's body range and its font is a fallback. If you must extend one of these
slides, copy its immediate neighbour's numbers rather than the step family's.

---

## Sequence and the "Step N:" run

Deck order for the `ov80i` variant: title → problem/solution → inspection
overview → "Configuring OV80i" opener → imaging → aligner → **one block per AI
model** → Node-RED → library → results → basic camera info → advanced camera
info → unique factors → defect generator → integration → team → contact.

Each model block is: ROI setup, model setup (classifier *or* segmenter), all
labeled regions, training settings, training report, concise results.

**Which slides carry the prefix.** Exactly these, and in this document order:
imaging, aligner, then per model — ROI setup, model setup, labeled regions,
training settings, training report — then Node-RED. Everything else carries no
prefix: title, problem/solution, overview, the opener, every stat card, library,
and the entire static tail.

**How numbering behaves.** `step_no` is positional and resolved at render time:
the *n*-th prefixed slide in document order is Step *n*. With one model that
gives 1 imaging, 2 aligner, 3 ROIs, 4 model setup, 5 labeled regions, 6 training
settings, 7 training report, 8 Node-RED. With two models the second block runs
8–12 and Node-RED becomes 13. Note that the concise results card sits *inside*
the model block but takes no number, so the run's numbers are not contiguous
with the slide index.

Insert a new prefixed slide into the middle of the run and it takes the number
of its position; every following step increments. Nothing else needs editing —
**because no slide ever hard-codes a digit.** Write the token
`Step {{ step_no }}: ` literally. For the same reason, body copy must never
cross-reference by number ("as shown in step 4"): the reference will silently
go stale.

A new slide belongs *inside* the run only if it documents a configuration action
performed in the camera UI. Evidence and outcome slides sit outside it.

If your slide repeats per model, its title must name the model (em dash + model
name) so the repeats are distinguishable in a multi-model deck.

---

## How the purple actually works

Purple is the accent and the ground, never the wall.

- **`#532EE3` primary** — the 0.36" sidebar, and accent *ink* on white slides:
  step body copy, deck lines, numbered lists, the one solid stat block on the
  defect-generator slide. On a numbered step slide purple covers about 4% of the
  canvas plus a few lines of type. It is never a full-slide background.
- **`#493691` and `#241465`** — the dark grounds and gradients on branded slides
  (title panel, stat card). `#241465` doubles as the headline and body ink on
  the near-white slides.
- **`#735CFF` / `#7F60F9`** — kickers on dark grounds, small rules, numerals.
- **`#A896F0` light purple** — stat labels on dark, and the 1 pt border around
  screenshot placeholders.
- **`#101025` / `#1A1A2E` / `#1E1B3A` near-blacks** — full-bleed grounds for
  openers and the closer.

Whole-slide colour is reserved for title, stat, opener and closer slides. A step
slide or an information slide is white or `#FAF9FE`, full stop. Match any palette
colour to within 40/255 per channel.

---

## Building a new numbered step slide

1. Start from `corpus/imaging_setup.pptx`. Do not start from a blank slide.
2. Leave the sidebar image and the slide-number placeholder untouched.
3. Retitle: `Step {{ step_no }}: <sentence-case phrase>`, and if the slide is
   per-model append ` — {model_name}`. Keep it to two lines at 30 pt.
4. Drop the screenshot into `0.70, 1.73, 5.48 × 3.14`, scaled to fit its own
   aspect ratio. If it is a dense grid or dialog, switch to the wide variant
   (`0.74, 1.08, 7.84 × 4.50`) and delete the body column.
5. Write the body into `6.30, 2.85, 3.60` at Bold 17 pt `#532EE3`. One or two
   sentences, or up to six short lines. Let the box grow downward.
6. Leave the bottom 0.75" and the top-right quadrant empty.
7. Check the copy against the brand voice: no exposure/gain/gamma values, no IP
   addresses, no node or variable names, no UI or JSON field names, no UUIDs.
   Numbers only where they tell the story — class names, example counts,
   accuracies, region counts — and only facts present in the source material.

---

## Deviating

**Legitimate, because the corpus already does it:**

- Swapping to the wide-image or text-only step variant.
- Two-line titles (the box overflows its 0.50" height and that is fine).
- Dropping the right-column body from 17 pt to 14 pt when you have 4–6 lines —
  14 pt is the corpus's other body size. Shrink the type, never the image.
- Growing the body box downward; it is set to auto-fit.
- Full-bleed backgrounds, sidebars and photos that run off the edge;
  decorative shapes may overhang the canvas by up to 0.35".
- Title-case vs. sentence case after the colon (both are in the corpus; prefer
  sentence case for new slides).

**Never:**

- Change the 10.00 × 5.62 canvas.
- Redraw, recolour, resize or reposition the sidebar; put a logo on a sidebar
  slide, or a sidebar on a logo slide; fabricate a logo instead of using
  `brand/logos/`.
- Hard-code a step number, or cross-reference a step by number in body copy.
- Move the numbered step's title off `0.74, 0.42` or its 30 pt Bold black
  setting; move the body off `6.30, 2.85`; move the image off
  `0.70, 1.73, 5.48 × 3.14` (standard variant).
- Stretch, distort beyond 4%, or crop-to-fill a screenshot.
- Introduce a font outside Proxima Nova / Montserrat / Arial / Calibri, or set
  body copy below 11 pt (7 pt is the absolute floor, for chrome only).
- Flood a step or information slide with purple, or invent a palette colour.
- Invent a metric, part name or outcome. Where the engineer's notes conflict
  with anything inferred from the screenshots, the notes win.
