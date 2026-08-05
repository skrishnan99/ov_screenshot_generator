# What goes on the slides

The layout engine guarantees a deck *looks* right. These rules are what make it
*true* — and a customer-facing deck that is wrong is worse than one that is ugly.

---

## 1. Ground every claim

**Rule: no number, name or setting reaches a slide unless it appears in the
source assets.**

For OV camera reports the sources are, in order of authority:

1. `data/meta.json` → `facts[]` — structured `(subject, property, value, source)`
   tuples extracted from the screenshots. This is the best source; each fact
   names the screenshot it came from.
2. `data/meta.json` → `models[]` — the model roster with per-model screenshot links.
3. `deliverables/report/descriptions.json` — prose descriptions of each screenshot.
4. `deliverables/report/node_red_description.md` — the IO logic summary.
5. `data/manifest.json` — step status, timings, warnings, model substitutions.

Read the facts before writing a single slide. A useful first pass:

```python
import json
m = json.load(open(f"{run}/data/meta.json"))
for f in m["facts"]:
    print(f"{f['subject']:20s} {f['property']:28s} {f['value']}")
```

If a claim cannot be traced to one of those, it does not go on the slide. This
includes plausible-sounding product claims: cycle time, licence terms, sensor
specs, training duration. When you want to say something the assets do not
support, either leave it out or ask the engineer for the number.

### Marketing claims

The reference decks carry vendor lines like "5 Min Model Training" or "trains
with as few as 5 images". **Do not copy those forward.** They belong to a
specific product and campaign. Build the `cards` slide out of what this
recipe actually demonstrates — model count, resolution, on-device training,
protocol support, library size.

---

## 1b. Grounded is not the same as worth printing

Some facts are in the assets and must still **never reach a slide**. Being
extracted is not a licence to print.

**Never put on a slide:**

| Excluded | Example from a run |
|---|---|
| Camera serial | `gsac586423` |
| Camera nickname / device name | `c204` |
| Camera URL or hostname | `ov80i-gsac586423.tail48746.ts.net` |
| Firmware or build version | `v2026.6.0-OV80i` |
| Capture reference numbers, file IDs, internal record ids | `1785196184493.jpg` |

The test: **does it mean anything to the reader, and will it still be true in
six months?** A serial identifies one unit on one bench; the customer will be
running different hardware, the camera gets reimaged, the recipe is exported to
another device — and none of it changes what the inspection does. It is noise
that dates the document.

**The camera MODEL is not in this list and belongs on the title slide** —
`OV80i`, `OV20i`. That is the product being reported on. What is excluded is
the identity of the *particular unit* it was tested on.

Also fine, because they explain the result rather than identify the hardware:
resolution, exposure, trigger mode, lighting, defect classes, counts,
thresholds, accuracy.

**This governs the words you write, not the screenshots.** A camera UI header
showing a serial is evidence and stays as captured — never crop or edit a
screenshot to hide one (see §5). The rule is that *you* do not restate it in a
title, caption, bullet or stat.

---

## 2. Handle what is missing — silently on slides, plainly in the summary

Extraction runs fail partially all the time. The deck simply does not show
the gap; the **chat summary to the engineer** is where it gets named. Slide
copy never says "not available", "not run" or "not populated" — this is a
test report, and commentary belongs to the data that is present (see
`report-brief.md` → *Write to the data that is present*).

- A step that produced no screenshot → omit the slide, and note the gap in
  the chat summary.
- A screenshot showing an error state (a "camera not reachable" modal, an empty
  viewer) → **do not use it as a hero image**. Either omit it or caption what it
  shows.
- Empty image areas are often correct: "Skip Aligner" enabled means there is
  genuinely no template image; a manually triggered camera has no live preview.
  Say that in the caption — an unexplained black rectangle reads as a broken
  deck.
- `manifest.json` `model_substitutions` non-empty → the descriptions were
  written by a weaker model. Read them more sceptically before quoting.

---

## 3. The observations slide (on request only)

Not part of the default deck. Build it only when the user asks for candid
observations on a slide; otherwise this material belongs in the chat summary
to the engineer. When it IS requested:

Every report ends with an honest `rows` slide before the closing. Its job is to
surface what the engineer should check, from evidence:

- metrics that are blank, absent or implausible (validation columns reading `---`)
- warning icons visible in a screenshot
- settings that contradict the logic (alignment skipped but the pass rule still
  requires `alignmentFound`)
- naming that does not match (a Node-RED flow named after a different camera)

Keep it factual and short. If the deck is going to a customer rather than
staying internal, tell the engineer this slide exists so they can decide
whether to cut it — do not quietly drop it yourself.

---

## 4. Voice

- Plain declaratives. "The camera captures at 3840×2160 on every PLC trigger."
- Concrete over evaluative. "Training loss 0.028" not "excellent convergence".
- No exclamation marks, no "seamless", "powerful", "cutting-edge", "leverage".
- Address the reader's decision, not the product's glory: what was configured,
  what it measures, what happens on a fail.
- Captions describe what the screenshot *shows*; card bullets say what it
  *means*. Do not repeat the caption in the bullets.
- Sentence case for captions and bullets; title case for slide titles.
- Use the customer's own vocabulary for the part and defects, taken from the
  ROI and class names in the recipe.

### Numbers

- Thousands separators: `23,051 captures`.
- Resolution with `×`: `3840×2160`. In chips, ASCII `x` is safer for font
  fallback: `3840x2160`.
- Dates in prose as `21 May 2026`; in the title-slide meta as `2026.07.30`.
- Keep the units the UI used — do not convert ms to s.

---

## 5. Screenshot hygiene

- One screenshot per slide unless the layout is `two_up`.
- Never crop a screenshot to hide something inconvenient. Cropping to remove
  irrelevant chrome is fine; cropping out a warning icon is not.
- Never recolour a screenshot — the red/green in a product UI is evidence.
- Prefer the composited/overlay variants (`*_composite.png`) when the overlay
  is the point; prefer `*_plain.png` when the raw capture is the point. Say
  which one is on the slide if it matters.
- Native-resolution files (`*_raw.jpg`) are for the assets folder, not the
  slides — they are large and the deck will balloon past Drive's conversion
  limits.
