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

## 2. Report what is missing, plainly

Extraction runs fail partially all the time. When an asset is absent, say so
rather than papering over it:

- A step that produced no screenshot → omit the slide, and note the gap in the
  closing summary or an `rows` entry.
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

## 3. The observations slide

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
