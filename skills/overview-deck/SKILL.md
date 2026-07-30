---
name: overview-deck
description: Build a brand-compliant Overview.ai PowerPoint deck (customer test report, case study, demo summary) from inspection assets, then verify it and upload it to Google Drive as Google Slides. Use whenever someone asks for an Overview deck, report, slides or case study — including from an OV camera extraction run. Enforces the Overview brand pack (palette, logo, type) and refuses to emit a deck with overflowing text, colliding shapes or off-brand colour.
---

# Overview.ai deck builder

You produce decks that go in front of customers under the Overview brand. Two
things make that safe, and neither is optional:

1. **Every slide is built through `scripts/ovdeck.py`.** It owns the layouts,
   the palette, the logo and the geometry, and it *measures every string with
   real font metrics* before saving. Never place a shape or pick a colour
   outside it.
2. **You look at the rendered deck before you hand it over.** A deck can pass
   every programmatic check and still be wrong.

The brand pack lives in `assets/brand/` and is the authority on colour and
logo. The example decks are the authority on *structure and tone* — never on
colour (see the warning in step 5).

---

## 0. Paths and environment

This skill ships inside the `ov-test-reports` plugin, which also provides the
Python environment it needs.

- `$PLUGIN_ROOT` — the plugin root, three levels above this SKILL.md. When
  `CLAUDE_PLUGIN_ROOT` is set, use that instead.
- `$SKILL_DIR` — `$PLUGIN_ROOT/skills/overview-deck`, the directory holding
  this file, `scripts/`, `references/` and `assets/`.

Run every script through the plugin's environment, by absolute path. The user's
working directory is wherever they started; never assume it is `$SKILL_DIR`:

```bash
uv run --project "$PLUGIN_ROOT" python "$SKILL_DIR/scripts/<script>.py" ...
```

The scripts locate their own `assets/` and `references/` relative to
themselves, so they work from any working directory. Write build scripts and
output into the **user's** working directory, not into `$SKILL_DIR`.

`python-pptx`, `Pillow` and `PyMuPDF` come from the plugin environment.
Rendering additionally needs LibreOffice installed on the machine.

`assets/brand/derived/` ships prebuilt, so no setup step is normally required.
Regenerate it only if the brand pack changes — `ovdeck.py` fails the build if
it is missing:

```bash
uv run --project "$PLUGIN_ROOT" python "$SKILL_DIR/scripts/make_logo_variants.py"
```

---

## 1. Collect inputs

Ask only for what you cannot determine:

- **Source assets** — usually an OV extraction run directory (`runs/<ts>/`)
  containing `deliverables/` and `data/`. If the user has not run one, the
  `ov-test-reports:extract-recipe-assets` skill produces it.
- **Audience** — customer-facing or internal. This decides whether the
  engineering-observations slide stays (see `references/content-rules.md`).
- **Author and date** for the title slide.
- **Site-visit notes or photos**, if they have any — a photo of the real part
  makes a far better title image than a screenshot.

Tell the user up front if the run is degraded (missing screenshots, error
modals captured, `model_substitutions` in the manifest). A deck built from a
broken run will have visible gaps, and they should decide whether to re-extract
first.

---

## 2. Read the facts before writing any slide

**Read `references/content-rules.md` now.** The rule that matters most: no
number, name or setting goes on a slide unless it appears in the source assets.

```bash
python3 - <<'PY'
import json; run="runs/20260730_005318"
m=json.load(open(f"{run}/data/meta.json"))
for f in m["facts"]: print(f"{f['subject']:20s} {f['property']:30s} {f['value']}")
print(json.dumps(m["models"], indent=2))
PY
```

Also read `data/manifest.json` for warnings and substitutions, and
`deliverables/report/node_red_description.md` for the integration story.

---

## 3. Plan the deck, then check the plan against the assets

Pick a style first — read `references/example-decks.md`, which describes the two
bundled reference decks and what each is for:

| Ask | Style |
|---|---|
| Test report / case study from an extraction run | `Deck(out)` — `style="report"` |
| Capability, application or intro deck | `Deck(out, style="presentation")` |

Then read `references/layouts.md` for the ten layouts and their capacities, and
use the house structure at the bottom of that file. Scale it to the recipe — one
`split` per AI model, drop steps that produced no asset. Never pad.

Write the slide list out before coding, and for each slide name the asset it
uses. Any slide whose asset does not exist gets cut at this stage, not after a
failed build.

---

## 4. Write the build script

Copy `$SKILL_DIR/scripts/example_deck.py` into the user's working directory and
replace the content. It is a complete, working 22-slide report — keep its
shape.

Its `sys.path.insert(...)` line only finds `ovdeck` while the file sits in
`scripts/`. Once copied elsewhere, put the engine on the path when you run it
(step 5) rather than editing the copy.

```python
from ovdeck import Deck
d = Deck("out/report.pptx")
d.title_slide("OV80i AI Vision Inspection", "Acme — Line 3",
              meta=["Report by: …", "Date: 2026.07.30"], image=part_photo)
d.cards("What This Recipe Demonstrates", [(t, desc), …])   # 2–6, grounded
d.contents([("01", "Introduction", "…"), …])
d.section("01", "Introduction")
d.statement(…); d.figure(…); d.split(…); d.two_up(…); d.flow(…); d.rows(…)
d.closing(para="…", summary=[…])
d.save()
```

Hard rules while writing content:

- **Never** compute your own x/y coordinates, add a `pptx` shape directly, or
  import `RGBColor` to pick a colour. If a layout cannot hold the content,
  shorten the content or split the slide.
- **Never** copy marketing claims out of the example decks. Build the `cards`
  slide from what this recipe demonstrably does.
- Respect the per-argument capacities in `references/layouts.md`. They are
  enforced, so guessing wastes a build cycle.

---

## 5. Build until it is clean

```bash
PYTHONPATH="$SKILL_DIR/scripts" \
  uv run --project "$PLUGIN_ROOT" python your_build.py \
  --run runs/<ts> --out out/report.pptx
```

`save()` prints every issue and **refuses to write the file** if any are
errors. What you will see and what it means:

| Issue | Fix |
|---|---|
| `text-overflow` | too many words for that slot — cut them; do not shrink type |
| `off-canvas` | content pushed a box off the slide, almost always downstream of an overflow |
| `collision` | two elements overlap — usually a caption that grew into an image |
| `card-count` / `row-count` | over the layout's capacity — split the slide |
| `missing-image` | the asset path is wrong or the run never produced it |
| `missing-logo` | opening/closing slide lost its logo — run `make_logo_variants.py` |
| `off-brand-colour` | you bypassed the token set |

Iterate on the *content* until the build is clean. Passing `strict=False` to
`Deck()` writes the file anyway and is for inspecting a broken layout during
development only — never for a deck you hand over.

Then audit the saved file:

```bash
uv run --project "$PLUGIN_ROOT" python "$SKILL_DIR/scripts/brandcheck.py" out/report.pptx
```

This reads the OOXML directly, so it also catches hand-edited decks. It must
report **clean**.

> **Do not eyedrop colours from the example decks.** The STADLER deck and
> others in circulation use a near-miss palette (`#201553`, `#2C1B69`,
> `#7B5CFF`, `#EFEBFA`) that is *not* the brand. `brandcheck.py` will flag
> every one. Borrow their layout and narrative; take colour only from
> `assets/tokens.json`.

---

## 6. Render it and actually look

Not optional. Every deck, every time.

```bash
uv run --project "$PLUGIN_ROOT" python "$SKILL_DIR/scripts/render.py" out/report.pptx --dpi 80
```

Then read the PNGs — at minimum the title, one of each layout you used, and the
closing. You are looking for what no checker can see:

- an image that is mostly empty, black, or shows an error dialog
- a screenshot too small to read at presentation size
- a caption that says something the screenshot does not show
- a slide that is technically fine but says nothing
- awkward rag or a lone word on its own line in a headline

Fix by changing content or swapping the layout, then rebuild and re-render.

---

## 7. Deliver

Report to the user: output path, slide count, which assets were used, anything
cut and why, and any caveat carried on a slide.

Upload only when asked:

```bash
uv run --project "$PLUGIN_ROOT" python "$SKILL_DIR/scripts/publish.py" out/report.pptx --assets runs/<ts> --dry-run
uv run --project "$PLUGIN_ROOT" python "$SKILL_DIR/scripts/publish.py" out/report.pptx --assets runs/<ts>
```

The deck converts to Google Slides and lands **flat in the team-wide shared
drive**, where the whole team can find every report. Always dry-run first and
show the user where it will go. The first upload on a machine opens a browser
for a one-time Google consent.

`--assets` also uploads the source `deliverables/` and `data/` — those go to
the engineer's OWN Drive library instead, inside a dated folder, because raw
assets would clutter a space the team reads. `--personal` sends the deck there
too.

Google Slides re-flows PowerPoint text with its own metrics. After uploading,
tell the user to spot-check the tight layouts — chip rows and `flow` diagrams —
and note that the local `.pptx` remains the reference copy.

---

## Files

| Path | What |
|---|---|
| `scripts/ovdeck.py` | the layout engine — read its docstrings before using it |
| `scripts/example_deck.py` | complete worked report; copy this |
| `scripts/brandcheck.py` | post-build brand audit of the `.pptx` |
| `scripts/render.py` | `.pptx` → per-slide PNGs |
| `scripts/publish.py` | Drive upload via the ov-test-reports plugin |
| `scripts/make_logo_variants.py` | one-time logo derivation |
| `references/brand.md` | palette, logo rules, type — sampled from the brand pack |
| `references/layouts.md` | the ten layouts, both styles, and their capacities |
| `references/content-rules.md` | grounding, honesty and voice rules |
| `references/example-decks.md` | the two reference decks: what to borrow, what to ignore |
| `assets/tokens.json` | machine-readable brand tokens |
| `assets/brand/` | the brand pack; `derived/` holds generated logo variants |
| `assets/example-decks/` | the STADLER and Hot Bar Soldering reference decks |

## The three rules that matter most

1. **Ground every claim** in the source assets — no number reaches a slide
   unless it is in `meta.json`, the descriptions, or the manifest.
2. **Never hand-position or hand-colour anything.** If it does not fit a
   layout, the content is too long.
3. **Look at the render before you deliver.** Every deck, every time.
