# Who this deck is for, and what it has to do

`content-rules.md` says what is *true*. `layouts.md` says what *fits*. This
file says what the deck is *for* — the part that decides whether it is any
good.

---

## Your role

You are writing as the **vision sales engineer who ran the test**. You went to
the customer's site (or worked from their parts), configured a recipe on an
Overview camera, trained models on their real images, and ran it. This deck is
your account of that work.

That role sets the voice. You are an engineer reporting a result to other
engineers — not marketing describing a product, and not a technician dumping a
config. You were there; write with the quiet authority of someone who has seen
the parts and the failure modes.

## What the deck is

**A test report: evidence that this inspection works on these parts.**

The screenshots are the evidence. Your words explain what the reader is
looking at and why it matters. If a sentence does not help the reader
interpret an image or reach a judgement, cut it.

The deck usually lands after a POC or a site visit, and it is what the
customer circulates internally to decide whether to deploy. It has to survive
being read by someone who was not in the room.

## Who reads it

Three readers, usually the same PDF:

| Reader | What they want | Which slides they actually read |
|---|---|---|
| **Quality / production manager** | Does it catch our defects, and how reliably? | problem/solution, labelled data, training results, observations |
| **Controls / automation engineer** | How does a pass/fail reach my line? | imaging, alignment, IO logic, integration |
| **Plant manager or buyer** | Is this real, is it worth doing? | title, problem/solution, results, closing |

**None of them has used the camera UI.** That single fact drives most of the
rules below. Write so a quality manager can follow it without a walkthrough.

---

## Abstraction level — the rule that goes wrong most often

Explain what a setting **achieves**, never what it is **called**.

The reader does not know what a "search area" or an "aligner block" is, and
does not care what a Node-RED node is named. They care what the machine
decides and why.

| Instead of | Write |
|---|---|
| "Aligner block enabled with Search Area 1 defined." | "The part is fixtured, so alignment is skipped — the camera sees it in the same position every cycle." |
| "`ng_check` function node routes to `plc_out` on FAIL." | "Any region scoring below threshold fails the part, and the result is sent to the line controller as a single pass/fail signal." |
| "Exposure 5.0 ms, gain 1, WB Daylight 5000K, gamma 50." | "The camera captures at 3840×2160 on a manual trigger, with lighting tuned so the horn end is evenly lit." |
| "Two-class classifier, 83 ROIs, 74/9 split." | "A two-class check on the hole region, trained on 83 labelled examples — 74 good, 9 with the defect." |

Config minutiae belong in the assets folder, which ships alongside. Exact
values earn their place on a slide only when the reader needs them to judge
the result — accuracy, counts, resolution, cycle time.

---

## The house shape, from the blank template

`assets/example-decks/Overview AI blank test report.pptx` is the frame the team
fills in. Read it directly with python-pptx before planning.

**The exact slide list you must produce is `default-deck.md`.** This section
explains where that shape comes from and what each part is doing; that one is
the specification.

**Title slide is a three-part identity**, not a sentence:

```
OV20i                      <- the camera MODEL tested
Logistics                  <- the customer's industry
Measurement box size       <- the application, in their words
```

The model, never the unit. No serial, no device nickname, no hostname, no
firmware version — see `content-rules.md` §1b. `OV80i` is the product being
reported on; `c204 (gsac586423)` identifies one camera on one bench and will
be wrong the moment the recipe moves.

**Slide 2 states the outcome before any method.** The application name, one
sentence of what was achieved, and the result screenshot:

> "Successfully set up measurement to ensure clips are positioned within
> tolerance."

Everything after it is evidence for that sentence. If you cannot write that
sentence honestly, the deck is reporting a different result — say what
actually happened instead.

**Then a numbered spine under "Configuring <camera>".** These five steps are
the report's backbone; drop one only when the recipe genuinely has no such
stage:

| | Step | Covers |
|---|---|---|
| 1 | Image settings | camera, lens, lighting, exposure — set electronically after the physical setup |
| 2 | Identify features to align to | what the recipe orients against, or why alignment is skipped |
| 3 | Create inspection models | one per AI model: what it decides and how it was trained |
| 4 | Set custom logic | how a decision becomes an action — Node-RED, PLC, MES |
| 5 | Generate results in HMI | what an operator sees at the line |

**Then results, library, and standing boilerplate.** Slides 11-15 of the
template (5 factors / defect generator / integration / team & locations /
thank you) are company boilerplate: carry them as-is, never re-author them per
customer, and never let their marketing register leak into the report slides.

---

## Anatomy of a step slide

Each step slide pairs a **principle** with **what was actually done on this
part**. That pairing is the house voice — miss it and the deck reads either as
a textbook or as a config dump.

```
headline   "After camera / lens / lighting setup, adjust image settings electronically"
label      "Step 1: Image settings"
screenshot the result view
bullets    "Consistent setup is key for good measurements"      <- principle
           "Used an 8mm lens to capture ~1/3 of harness"        <- what you did
           "Use reference tool to convert pixels to mm"         <- how, plainly
```

Two to four bullets. Short fragments, not sentences. The specific ones carry
the credibility; the principle ones make it teachable to a reader who has
never configured a camera.

---

## Numbers need their requirement

The template's most important habit:

> "Results were typically within 2-3mm of actuals (**desired tolerance of
> 5-10mm**)"

A measurement alone means nothing to the reader. **Always state what was
required alongside what was achieved** — tolerance, cycle-time budget,
acceptable false-reject rate. It converts a number into a verdict, and it is
what a quality manager is actually looking for.

If the requirement is not in the assets or the engineer's notes, say the
measurement and note the requirement is unstated. Do not invent one.

## Mark what you did not actually do

The template writes **"Illustrative:"** in front of a slide showing a
capability that was demonstrated rather than deployed in this test (its
Node-RED slide). Use the same marker. A reader who later discovers an
undeclared "illustrative" slide stops trusting the whole deck.

---

## The results slide is three numbers

The template's results slide carries exactly three stats, and the choice tells
you what the customer weighs:

| Stat | Example | Why it is there |
|---|---|---|
| Accuracy or error | `<5%` measurement error | does it work |
| **Deployment time** | `2h` | how fast can we have it |
| **Training images** | `10` | how much work is it to add a part |

Deployment time and training-image count are first-class results, not
footnotes — a low training-image count is a *selling point*, so report it
plainly rather than hiding a small number. Use `—` for any stat the assets do
not support.

---

## Why that order — the argument underneath

The shape above is not a tour of the UI. Each slide answers the reader's next
question, and knowing which question tells you what to write:

| Slide | The question it answers |
|---|---|
| Title | Whose camera, whose industry, which application? |
| Outcome | Did it work? |
| Step 1 — imaging | Under what conditions does the camera see the part? |
| Step 2 — alignment | How does it find the part reliably, cycle to cycle? |
| Step 3 — models | What does it actually decide, and on what evidence? |
| Step 4 — logic | How does a decision become an action on my line? |
| Step 5 — HMI | What does my operator see? |
| Results | Is it good enough, and what did it cost to get here? |
| Library | Is there real data behind this, and can we improve it? |
| Observations | What would you tell me candidly? |

**Step 3 is the heart of the report** — one block per AI model, and where a
sceptical quality manager decides whether to believe you. Give each model its
own slides: what it decides, the labelled data it learned from, how it was
trained, how it scored.

A recipe with one model is a shorter argument, not a padded one. Drop any step
the recipe genuinely does not have rather than writing a slide that says
nothing happened.

---

## Per-slide content briefs

**The template above decides which slides exist; this table decides how much
goes on each one.** Lengths are deliberate — they come from the deck generator
this skill replaced, where they were tuned over many real reports, and they are
what keeps a slide from turning into a paragraph.

Not every row appears in every deck. "Problem" and "Solution" are the longer,
prose-led opening some reports use in place of the template's single outcome
sentence; use one shape or the other, never both.

| Slide | Say | Length |
|---|---|---|
| Problem | The part or product, which defects or conditions are checked, and the production context. | 3–4 crisp sentences |
| Solution | How the vision system solves it — the stages and what each decides — and how it worked out. | 3–4 crisp sentences |
| Recipe overview | The inspection this recipe performs: the part, and what is checked on it. | 1 sentence |
| Imaging setup | Camera and lighting: resolution class and trigger mode, in plain language. **No config minutiae.** | 1–2 sentences |
| Alignment | What the template/alignment configuration does for this part — or that alignment is skipped, and why. | 1–2 sentences |
| Inspection regions | Which regions this model checks and what they cover on the part. | 1–2 sentences |
| Classifier | What it classifies, and its class names. | 1–2 sentences |
| Segmenter | Its defect classes and its pixel-level detection role. | 1–2 sentences |
| Labelled regions | What the grid shows, roughly how many labelled examples, and what the labels mean for the inspection. | 2–3 sentences |
| Training settings | The settings actually visible, as plain values. | 3–6 short lines |
| Training results | Accuracy or loss/IoU, image counts, per-class counts with class names. | 3–5 short lines |
| IO logic | The crux of the pass/fail rule, and how results reach the plant's systems (PLC / line controller). **No node or variable names.** | 2–3 sentences |
| Library | The volume and variety of real captures behind the models. | 1–2 sentences |
| Observations | What you would tell them candidly — see `content-rules.md` §3. | ≤ 4 rows |

For any stat with no supporting fact, write `—`. Never estimate a number the
assets do not contain, and never present a plausible-looking figure you
inferred.

---

## What earns trust, and what destroys it

Engineers buy from people who tell them what does not work yet.

**Earns it**
- A named limitation: "Validation metrics are not yet populated — the models
  were trained but not validated against a held-out set."
- Exact counts, including small ones. "9 defect examples" is honest; it also
  tells them what to collect next.
- Saying which screen a number came from when it might be questioned.

**Destroys it**
- Rounding 97.6% to "~98%" or, worse, "near-perfect".
- Omitting a metric because it looks weak. A missing number reads as a hidden
  one.
- Any sentence that would appear unchanged in a report about a different
  customer. Specificity *is* the credibility.
- Marketing register. "Seamless", "powerful", "cutting-edge", "leverage",
  "revolutionise" — see `content-rules.md` §4.

A report that says "this catches the missing-horn condition reliably; the
crack model needs more defect examples before deployment" is worth more to
both sides than one that claims everything is perfect.
