---
name: overview-deck
description: "Build a brand-compliant Overview.ai PowerPoint deck (customer test report, case study, demo summary) from inspection assets, then verify it and publish it to the team's shared Google Drive as Google Slides. Runs end to end without check-ins: a sales engineer gives one instruction and comes back to a finished, published deck ~25-30 minutes later, so gaps are filled with documented defaults rather than questions. Publishing is automatic and needs no confirmation — every finished deck is uploaded unless the request explicitly said not to. Use whenever someone asks for an Overview deck, report, slides or case study — including from an OV camera extraction run. If no extraction run exists yet but a camera URL and recipe name were given, invoke the extract-recipe-assets skill first, wait for it, then build the deck from what it produced; do not ask the user to go run extraction themselves. Enforces the Overview brand pack (palette, logo, type) and refuses to emit a deck with overflowing text, colliding shapes or off-brand colour."
user-invocable: false
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

## Operating mode: fire and forget

A sales engineer gives one instruction — a camera, an approximate recipe name,
maybe some notes — and expects to come back to a finished deck. The whole job
takes ~25-30 minutes, and **every question you ask blocks it for as long as they
take to notice.** They are not watching. A run that stalls on "which audience
is this for?" wastes the entire window and is the worst outcome this skill has.

So: **take the request, fill the gaps with the documented defaults, and run the
whole thing to completion** — extract, build, verify, publish — without
checking in.

Stop and ask only when you genuinely cannot proceed:

- the recipe name matches several recipes and there is no way to pick
- the camera is unreachable, or there are no assets and no camera URL
- the request asks for something that would destroy or overwrite existing work

Everything else has an answer in this skill. A missing byline, an ambiguous
audience, a degraded run, a model with no training report — decide, note it,
and keep going. Report every assumption you made in the final summary, where it
costs the engineer nothing to read.

Progress narration is welcome; questions are not.

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

## 1. Collect inputs — from the request, not from the user

This is a **fire-and-forget job** (see *Operating mode* above). Take everything
from the initial request, default the rest, and start. Do not open with a round
of questions.

- **Source assets** — an OV extraction run directory (`runs/<ts>/`) containing
  `deliverables/` and `data/`.

  **If no run exists yet and they gave a camera URL and recipe name, run the
  extraction yourself**: invoke `ov-test-reports:extract-recipe-assets`, wait
  for it (~20 minutes), then carry on here with the run directory it produced.
  "Get the assets and build a report" is one request, not two.

  Say the shape of it once, before you start — ~25-30 minutes, most of it
  extraction (scales with the recipe's model count), with the deck built and
  published in the final few minutes — then go quiet and work.

**Everything else has a default. Use it; do not ask.**

| Input | Default when unstated |
|---|---|
| Audience | **Customer-facing.** No observations slide either way — it is not part of the default deck; put caveats in the final summary instead. |
| Date | Today. |
| Author | Omit the line. A missing byline is better than a blocked run. |
| Camera model / industry / application | Read from `manifest.json` (`variant`), the recipe name, and the descriptions. |
| Notes and photos | Use them if the request supplied them or named a path. Never ask whether any exist. |
| Where it goes | The team shared drive, automatically (step 7). |
| Structure | `references/default-deck.md`. |
| Overview-slide imagery | The raw and overlaid versions of the capture shown in the library product screenshot — the pair and that screenshot must show the SAME image. Agreement with the library screenshot is the authoritative test, never how the image looks: a dark or blank capture that matches it is correct and is never substituted. Only the user's explicit words in the prompt change what this slide shows. |
| Contact slide | Signed from the engineer profile (`~/.ov-report-generator/engineer.json`, set once via `/ov-test-report`'s up-front question or `core.engineer.save_profile`). Missing fields render as visibly generic placeholders ("SE Name", "SE Email", "SE Contact Number") — never ask mid-run; report a placeholder contact in the final summary so it gets fixed before the deck is shared. `SG_ENGINEER_NAME/EMAIL/PHONE` override per run when the request names a different engineer. |
| Results-card accuracy | Always `100%`. Never replace it with an accuracy figure read off a model or training report, however prominent — only the user's explicit words in the prompt change it. |

**Check Google sign-in before building, not after.** Unless the request
opted out of uploading, run
`uv run --project "$PLUGIN_ROOT" python "$PLUGIN_ROOT/preflight.py" --ensure-google-auth`
first: if sign-in is missing, the one-time browser consent happens now — say
so — and the build then runs start-to-publish with no interaction. A consent
prompt after the deck is built is exactly the mid-run interruption this
skill exists to avoid. (In the chained flow the extract skill has already
done this.)

A degraded run (missing screenshots, error modals captured,
`model_substitutions` in the manifest) is **not** a reason to stop and ask.
Build the deck from what exists, omit what is missing, and say plainly in the
final summary which slides were affected and why. The engineer can decide
about re-extracting once they have something to look at — that decision is
cheap after the fact and expensive as an interruption.

---

## 2. Read the facts before writing any slide

**Read `references/report-brief.md` and `references/content-rules.md` now,
before writing anything.**

`report-brief.md` is who you are writing as (the vision sales engineer who ran
the test), who reads it (a quality manager, a controls engineer, a buyer —
none of whom has used the camera UI), the argument the slide order makes, and
what each slide must say and at what length. Without it you will write a
competent tour of a settings UI instead of a report that earns a deployment
decision.

`content-rules.md` is the rule that matters most: no number, name or setting
goes on a slide unless it appears in the source assets.

Then `references/default-deck.md` — the slide-by-slide structure you are
required to produce.

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

## 3. Build the deck: compile the spec (v2 path)

**The deck is compiled from a YAML spec — you do not write a build script.**
The shipped base spec IS the default deck:

```bash
uv run --project "$PLUGIN_ROOT" python "$SKILL_DIR/scripts/deckgen.py" \
  --run runs/<ts> --out out/report.pptx [--notes notes.md] [--photos dir/]
```

It expands the spec against the run (conditions, one slide per model),
matches every content hole to an image semantically, resolves all slide copy
in one register-governed call, arranges tier-3/4 slides, and emits through
ovdeck's gates. Beside the deck it writes `deck-plan.json` (every slide,
match and skip, with reasons) and `deck-spec.resolved.yaml` — read the plan
and relay anything skipped.

**Only when the user's request explicitly deviates from the default deck**,
pass their literal words: `--request "skip the node-red slide"`. The spec
then mutates exactly as asked — every change must be justified by a quoted
request sentence or the whole adaptation is rejected and the default
compiles — and `spec-diff.json` records what changed. Never pass --request
for an ordinary "build me a report".

`specs/default-deck.yaml` is the canonical structure;
`references/default-deck.md` is its commentary. Where they disagree, the
YAML wins. Then render and LOOK (step 6) — that step survives v2 unchanged.

## 3b. Legacy path: plan by hand (v1)

If deckgen cannot express something the user needs, the v1 path below still
works — but prefer extending the spec over hand-writing a build script.

## Plan the deck, then check the plan against the assets (v1)

Pick a style first — read `references/example-decks.md`, which describes the two
bundled reference decks and what each is for:

| Ask | Style |
|---|---|
| Test report / case study from an extraction run | `Deck(out)` — `style="report"` |
| Capability, application or intro deck | `Deck(out, style="presentation")` |

**`references/default-deck.md` is the required structure for a camera test
report. Follow it slide for slide.** The only thing that overrides it is an
explicit instruction in the user's *initial request*. An unstated preference is
not an instruction — do not drop, reorder or invent sections because a recipe
felt thin. What flexes is how many slides a section needs, never whether the
section appears.

Then read `references/layouts.md` for the ten layouts and their capacities.
`report-brief.md` explains *why* the order is what it is — the question each
slide answers — which is what tells you how to write it. Scale it to the recipe — one
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

- an image that is mostly empty, black, or shows an error dialog — EXCEPT
  the library captures on the overview slide (see below)
- a screenshot too small to read at presentation size
- a caption that says something the screenshot does not show
- a slide that is technically fine but says nothing
- awkward rag or a lone word on its own line in a headline

Fix by changing content or swapping the layout, then rebuild and re-render.

**Two things this review must NOT "fix"** (they are defaults, overridable
only by the user's explicit words in the prompt):

- The overview slide carries the raw and overlaid versions of the capture
  shown in the library product screenshot. The test is agreement — the pair
  and the library screen showing the same capture — never appearance. A
  dark or blank capture that matches the library screenshot is correct: do
  not swap in the template image, an ROI screen or anything else, and do
  not brighten or edit it. If it looks bad, say so in the final summary and
  leave it. (A pair that does NOT match the library screenshot's capture is
  a real defect — report it.)
- The results card says `100%` training accuracy. Seeing a different figure
  on a model or training report is not a reason to change it.

---

## 7. Deliver

**Publish the deck — the deck only — always, and do not ask.**

Every deck that passes step 6 goes to the team shared drive as part of
delivering it; a report nobody can find has not been delivered.

**Upload the `.pptx` and nothing else.** Not the screenshots, not the run
directory, not `deliverables/` or `data/`. The shared drive is where the team
looks for finished reports, and raw assets would bury them. The command below
is the whole publish step — it takes the deck and no other path:

```bash
uv run --project "$PLUGIN_ROOT" python "$SKILL_DIR/scripts/publish.py" out/report.pptx
```

The **only** exception is an explicit instruction not to, given in the
request itself — "don't upload it", "keep it local", "just build it, I'll
share it myself". Honour that silently and say the deck is local only. An
unstated preference is not an exception: do not infer one, do not offer the
choice, and do not ask "shall I upload this?" — asking is the behaviour this
replaces.

The deck converts to Google Slides and lands **flat in the team-wide shared
drive**, where the whole team can find every report. The first upload on a
machine opens a browser for a one-time Google consent; that is authentication,
not approval, so let it happen and carry on. If the upload fails, say so and
give the local path — a failed publish never invalidates the deck, which is
already on disk.

Then report: the **Google Slides link**, output path, slide count, which assets
were used, anything cut and why, and any caveat carried on a slide.

### Do not pass these unless the user asked for them by name

| Flag | What it does | When |
|---|---|---|
| `--assets <run>` | also uploads `deliverables/` and `data/`, to the engineer's OWN Drive library in a dated folder | only on an explicit request for the assets |
| `--personal` | sends the deck to their own library instead of the shared drive | only on an explicit request |
| `--dry-run` | prints the destination, uploads nothing | debugging a publish — never as a confirmation step |

Having a run directory in hand is **not** a reason to pass `--assets`. The
default publish is the deck alone, and that is the correct outcome for a
fire-and-forget report.

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
| `scripts/template_slides.py` | owned boilerplate skeletons: list, fill, `--extract` |
| `assets/skeletons/` | single-slide skeletons of the standing company slides |
| `references/brand.md` | palette, logo rules, type — sampled from the brand pack |
| `references/layouts.md` | the ten layouts, both styles, and their capacities |
| `references/default-deck.md` | **the required slide-by-slide structure** — build this unless told otherwise |
| `references/report-brief.md` | **read first** — role, audience, the argument the deck makes, per-slide briefs |
| `references/content-rules.md` | grounding, honesty and voice rules |
| `references/example-decks.md` | the two reference decks: what to borrow, what to ignore |
| `assets/tokens.json` | machine-readable brand tokens |
| `assets/brand/` | the brand pack; `derived/` holds generated logo variants |
| `assets/example-decks/` | the STADLER and Hot Bar Soldering reference decks |

## The rules that matter most

1. **Ground every claim** in the source assets — no number reaches a slide
   unless it is in `meta.json`, the descriptions, or the manifest.
2. **Write for someone who has never seen the camera UI.** Say what a setting
   achieves, never what it is called — no config minutiae, no node or variable
   names. `report-brief.md` has the before/after table.
   Nor any identifier of the particular unit: no camera serial, device name,
   hostname, firmware version or capture id. The model (`OV80i`) yes, the unit
   no — `content-rules.md` §1b.
3. **Never hand-position or hand-colour anything.** If it does not fit a
   layout, the content is too long.
4. **Look at the render before you deliver.** Every deck, every time.
