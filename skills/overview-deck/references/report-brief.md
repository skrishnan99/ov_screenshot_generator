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

## The narrative arc

The slide order is an argument, not a tour of the UI. Each phase answers the
reader's next question:

1. **Title** — whose part, whose line, who ran it, when.
2. **Problem / solution** — *what are we inspecting and why is it hard?*
   Then: *how does the system solve it?* This is the only place that may be
   two paragraphs of prose. Everything after it is evidence for these claims.
3. **What the recipe does** — one sentence a reader can repeat to a colleague.
4. **How it was set up** — imaging, alignment, inspection regions. *Where does
   the camera look, and under what conditions?*
5. **Per AI model** — what it decides, the labelled data it learned from, how
   it was trained, how it scored. This is the heart of the report: it is where
   a sceptical quality manager decides whether to believe you.
6. **IO logic** — *how does a decision become an action on the line?*
7. **Image library** — the volume of real data behind it.
8. **Observations** — what you would tell them candidly.
9. **Closing** — what happens next.

A recipe with one model is a shorter argument, not a padded one.

---

## Per-slide content briefs

Lengths are deliberate. These come from the deck generator this skill
replaced, where they were tuned over many real reports.

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
