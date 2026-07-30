# The example decks — what to borrow, what to ignore

All three live in `assets/example-decks/`. Read them for **structure,
narrative and chrome**. Do not take colour or copy from any of them — see the
warnings below.

Read the **blank test report template first** (section 0): it is the frame the
team actually fills in. The two finished decks show what a completed one looks
like.

To read one without leaving the terminal:

```bash
python - <<'PY'
import fitz
d = fitz.open("assets/example-decks/<name>.pdf")
for i, p in enumerate(d, 1):
    print(f"--- p{i}: {p.get_text().strip()[:200]}")
    p.get_pixmap(dpi=70).save(f"/tmp/ref-p{i:02d}.png")   # then look at the PNGs
PY
```

---

## 1. STADLER St. Margrethen — OV20i Connector Inspection Demo (24 pp)

A **site-visit test report**: one recipe, built during a customer visit,
documented step by step. This is the model for OV camera test reports and maps
directly onto `style="report"`.

**Borrow:**

- The three-act structure: Introduction → Recipe Setup Process → the
  integration/dashboard story, each opened by a numbered divider that matches
  the contents slide.
- One slide per configuration step, titled `Step N: <what happened>`, each with
  a caption saying what was done and a full-width screenshot.
- The image-left / explanation-card-right treatment for anything that needs
  settings called out.
- A benefits grid early, a contact/closing slide at the end.
- Captions written as *what the engineer did*, not as UI narration.

**Chrome it establishes** (reproduced by `style="report"`): navy title bar with
white slide title, tinted page below, white cards, centred screenshots with
generous side margins.

> **Colour warning.** This deck's palette is a near-miss and is *not* the brand:
> `#201553` navy, `#2C1B69` bar, `#7B5CFF` purple, `#EFEBFA` page, plus a green
> `#0EB27E` that does not exist in the brand pack at all. `brandcheck.py` flags
> every one. The correct equivalents are `#180E42`, `#180E42`, `#735CFF`,
> `#F1F1FF`, and — for accents — `#532EE3` or the yellow `#FFC524`.

> **Copy warning.** Its benefits grid carries campaign claims — "5 Min Model
> Training", "trains with as few as 5 images", "No Software License". Those are
> product-marketing lines for a specific camera and campaign. Do not copy them
> into a new deck; build the `cards` slide from what the recipe in front of you
> demonstrably does.

---

## 2. Overview.ai — Hot Bar Soldering V4 (19 pp)

A **capability/application deck**: the inspection problem, how the OV80i was
configured, and what it caught. Denser and more technical, aimed at an
engineering audience. Maps onto `style="presentation"`.

**Borrow:**

- Opening on the dark ground with the logo top-left and a two-line headline
  where the second line is purple — the strongest brand moment in either deck.
- Section breaks on the same dark ground with a large watermark shape.
- Content slides on **white** with a purple left spine carrying the slide
  number, big navy title, purple sub-headline underneath.
- Photographs of the real cell and part, not only UI screenshots. A photo of
  the fixture beats another screenshot.
- Naming the constraint that made the job hard ("very hard to see raised wire
  from a top-down view") before showing the solution. That framing is worth
  copying wholesale.
- Ending on hardware specs and a named contact.

**Chrome it establishes** (reproduced by `style="presentation"`): white page,
`#532EE3` spine, navy titles, purple sub-headlines, logomark at the foot of the
spine.

> **Typography signal.** This deck is set in **Proxima Nova** (Bold, Semibold,
> Regular) — the closest thing to a stated house typeface anywhere in the
> material, since the brand pack has no typography page. It is a licensed font
> and is not bundled. `Deck(font="Proxima Nova")` will use it when installed and
> otherwise warn and fall back to Calibri. Do not set Proxima Nova blindly: an
> uninstalled font is silently re-substituted by PowerPoint on the customer's
> machine, which is worse than shipping Calibri.

> **Colour warning.** Its dark ground is `#101024`, not the brand navy
> `#180E42`, and its heading purple is `#7E60F9`, not `#735CFF`. Close, but
> `brandcheck.py` will reject both. The spine purple `#532EE3` *is* correct.

---

## 0. Overview AI blank test report — the canonical template (15 pp, .pptx)

**Start here.** The other two are finished decks; this is the empty frame the
team actually fills in, and it is the authority on the *shape* of a test
report. It is a `.pptx`, so read it directly:

```python
from pptx import Presentation
for i, s in enumerate(Presentation(path).slides, 1):
    for sh in s.shapes:
        if sh.has_text_frame and sh.text_frame.text.strip():
            print(i, sh.text_frame.text)
```

Its structure, which `report-brief.md` expands into rules:

```
1   title            OV20i / Logistics / Measurement box size
2   outcome          the application, one success sentence, result screenshot
3   section          "Configuring OV80i"
4   Step 1           Image settings
5   Step 2           Identify features to align to
6   Step 3           Create inspection models
7   Step 4           Set custom logic using node-red        ("Illustrative:")
8   Step 5           Generate results in HMI page
9   Results          3 stats: error / deployment time / training images
10  Library          on-device storage -> retraining loop
11-13 boilerplate    5 factors / defect generator / integration
14-15 boilerplate    team & locations / thank you
```

**Borrow:** the numbered Step 1-5 spine, the outcome-first slide 2, the
three-stat results slide, and the bullet voice — each step slide pairs a
general principle with what was actually done on *this* part.

**Note:** slides 11-15 are standing company boilerplate. Carry them as they
are; do not re-author them per customer and do not mine them for the report's
own voice — they are marketing register, the rest of the deck is not.

---

## Choosing a style

| Ask | Style |
|---|---|
| Test report / case study from an extraction run, dense with screenshots | `report` |
| Capability, application or intro deck; fewer slides, more photos and story | `presentation` |

Layouts, capacities and validation are identical between the two — only the
chrome changes, so you can switch a finished deck with one argument and rebuild.
