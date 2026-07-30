# Layout catalogue

Ten layouts. They are the whole vocabulary — a deck built from these looks like
one deck. If content will not fit a layout, **change the content or split the
slide**; never add a one-off layout or nudge coordinates, because nothing
outside `ovdeck.py` is validated.

## Two styles, one set of layouts

```python
Deck(out)                          # style="report"       (default)
Deck(out, style="presentation")
```

| Style | Chrome | From | Use for |
|---|---|---|---|
| `report` | navy title bar, tinted page, white cards | STADLER connector deck | test reports, case studies from an extraction run |
| `presentation` | white page, `#532EE3` left spine with slide number, navy title | Hot Bar Soldering deck | capability/application/intro decks |

Every layout below works identically in both — the engine shifts the left
margin to clear the spine and recomputes each layout's internal geometry from
it. Title, contents, section and closing slides use the navy ground in both
styles.

`presentation` also accepts a `subtitle=` argument on `cards`, `figure`,
`split`, `two_up`, `flow` and `rows` — it renders as a purple sub-headline
under the title. In `report` style there is no room for it and passing one
raises a warning.

All measurements are inches on a 13.333 × 7.5 slide. Every capacity figure
below is enforced at `save()`.

---

## `title_slide(title, subtitle, *, meta, image, footer)`

Opening slide. Navy ground, white horizontal logo top-left, headline, accent
rule, optional circular part photo in a purple halo on the right.

| Arg | Notes |
|---|---|
| `title` | ≤ 2 lines at 40 pt ≈ 34 chars/line with an image, 52 without |
| `subtitle` | one line, the engagement/recipe name |
| `meta` | up to 3 short lines (author, date, serial) |
| `image` | optional; auto centre-cropped to a circle. Use the part, not a screenshot |

Use the most legible photo of the actual part. A UI screenshot inside the
circle reads as noise.

---

## `cards(title, cards, *, columns=3)`

Value/benefit grid. **2–6 cards.** Each card: bold title (≤ 34 chars, 2 lines
max) and a description of ≤ 110 chars.

Ground every card in something observable. "23,051 captures on device" is a
card; "industry-leading performance" is not.

---

## `contents(items, *, heading)`

Numbered table of contents on navy. Each item `(number, title, subtitle)`.
**2–4 items** — this maps to the section dividers, so it should mirror them
exactly.

---

## `section(number, title, subtitle="")`

Full-bleed navy divider: big purple number, white title, optional one-line
subtitle. One per section, and the numbers must match `contents`.

---

## `statement(title, intro, *, card_title, bullets, badge="", rule=True)`

Intro paragraph plus one emphasised white card with an accent rule on top.
The "what this is" slide.

| Arg | Capacity |
|---|---|
| `intro` | ≤ 300 chars (3 lines) |
| `card_title` | ≤ 45 chars |
| `badge` | ≤ 18 chars, optional; renders in the yellow highlight |
| `bullets` | 4–6 items, ≤ 105 chars each |

---

## `figure(title, image, *, caption, chips, note)`

One screenshot at full content width. The default for any configuration screen.

| Arg | Capacity |
|---|---|
| `caption` | ≤ 240 chars (2 lines) |
| `chips` | 0–8 short spec pills; they wrap to a second row automatically |
| `note` | optional footnote in muted type — provenance, caveats, "this screen is composited" |

Screenshots are roughly 16:10, so at full width the image is height-limited and
sits centred with side margins. That is correct and matches the reference deck.

---

## `split(title, image, *, card_title, para, bullets, chips)`

Screenshot left (7.05 wide), white explanation card right (4.66 wide). The
workhorse for "here is a thing, here is what it means".

| Arg | Capacity |
|---|---|
| `card_title` | ≤ 32 chars (2 lines at 16 pt) |
| `para` | ≤ 170 chars |
| `chips` | 0–2 — the card is narrow, a third chip wraps and eats bullet room |
| `bullets` | 3–5 items, ≤ 60 chars each |

Overrun any of these and `save()` reports `text-overflow` for the card. The fix
is fewer words, or move detail to a following `figure`.

---

## `two_up(title, left, right, *, caption, left_caption, right_caption)`

Two images side by side with captions. For before/after, or two models' views
of the same step.

Images with very different aspect ratios will render at visibly different
sizes — that is honest, not a bug, but if it looks unbalanced prefer two
`figure` slides.

---

## `flow(title, nodes, *, caption, fan_out, cards)`

Node diagram: a left-to-right chain, an optional fan-out column, and up to two
explanation cards beneath.

| Arg | Capacity |
|---|---|
| `nodes` | 2–3 `(label, sub)` pairs — the in-line chain |
| `fan_out` | 0–3 `(label, sub)` boxes the last node branches to |
| `cards` | 0–2 `(title, [bullets])`; 3 bullets each, ≤ 55 chars |

Use this when there is **no screenshot** of the logic — a Node-RED flow, an
integration path, a decision rule. Do not draw a diagram of something you have
a screenshot of.

---

## `rows(title, entries, *, intro)`

Label/detail rows with an accent spine. Findings, observations, spec tables.

**Maximum 5 rows.** Label ≤ 34 chars, detail ≤ 210 chars. More than five rows
means two slides.

---

## `closing(*, title, para, summary, contact, footer)`

Closing slide. Navy, white logo, "Thank You", one paragraph, and up to two
panels (`summary`, `contact`) of ≤ 4 lines each.

---

# Deck shape

> **For a camera test report, `default-deck.md` is authoritative** — it is the
> required structure and it names the asset for every slide. What follows is
> the generic shape those layouts assemble into, useful for decks that are not
> test reports.

The house structure, mirroring the reference decks:

```
title
cards            value/what-this-shows
contents         2-4 sections
section 01       Introduction
  statement      what the recipe is
section 02       Recipe Setup Process
  figure         imaging setup
  split          alignment / template
  figure         ROIs
  split          one per AI model
  two_up         labelled crops
  figure         training
  two_up         training reports
  figure         settings
section 03       Logic & Results
  flow           IO logic
  figure         image library
  rows           engineering observations
closing
```

Scale it to the recipe: one `split` per model, drop steps that produced no
asset. A recipe with one model yields ~15 slides; three models ~22. Do not pad
a thin recipe to hit a slide count.
