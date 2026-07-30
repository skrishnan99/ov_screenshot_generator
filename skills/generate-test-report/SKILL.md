---
name: generate-test-report
description: "DEPRECATED — superseded by the overview-deck skill. Do NOT use for deck, report, slides or case-study requests; overview-deck handles those. Retained only as reference for the legacy skeleton-and-token deck builder (deck_cli.py / pipeline.py). Never select this skill unless the user explicitly names it or asks for the legacy pipeline by name."
---

# DEPRECATED — use `overview-deck` instead

> **This skill is no longer the deck generator.** Every request to build a
> deck, report, slides or case study — including from an OV camera extraction
> run — goes to **`ov-test-reports:overview-deck`**, which owns the layout
> engine, the brand pack and the verification loop.
>
> The current path is two skills:
> **`extract-recipe-assets`** to gather assets from the camera, then
> **`overview-deck`** to build the deck from them.
>
> Nothing here is deleted: `pipeline.py` and `deck_cli.py` still work, and
> this file documents them. Use it only when the user explicitly asks for the
> legacy skeleton-and-token builder by name, or when maintaining that code.

# Generate a full test report from an OV camera (legacy)

You orchestrate a battle-tested two-phase pipeline (asset extraction, then
deck generation). **Never navigate the camera UI yourself, never re-implement
pipeline logic, and never modify the plugin's code** — your job is intake,
running the bundled commands, monitoring, and explaining results.

The plugin root (referenced as `$PLUGIN_ROOT` below) is the directory two
levels above this SKILL.md file; when the environment variable
`CLAUDE_PLUGIN_ROOT` is set, use that.

## 1. Collect inputs

From the conversation (ask only for what's missing):
- **Camera URL** and **recipe name** (approximate name is fine — it is
  LLM-resolved against the camera's recipe list).
- **Engineer context** (optional but very valuable): any typed notes about
  the site visit, the customer, the part, deployment time, outcomes. Save
  everything they give you VERBATIM to a scratch file `notes.md` — do not
  summarize or rewrite; the pipeline treats these notes as authoritative.
- **Engineer photos** (optional): a folder path, attached files, or images
  pasted into the chat. Copy them all into one scratch directory `photos/`
  (keep original filenames when they exist). The pipeline describes each
  photo and lets it take precedence over extractor screenshots where it fits.

Before running, tell the engineer: the full run takes **~45 minutes** — very
roughly 20 minutes extracting assets from the camera and 25 building the deck
— and it will **activate the recipe on the camera** if currently inactive. On
a production line they should confirm that's acceptable.

Set that expectation before starting. The deck phase is quiet for long
stretches while an autonomous design session builds slides, and an engineer
who was told "15 minutes" will reasonably think it has hung. Time scales with
the number of AI models in the recipe.

## 2. Preflight

```bash
uv run --project "$PLUGIN_ROOT" python "$PLUGIN_ROOT/preflight.py" --fix --url "<URL>"
```

If it fails, relay the printed fix instructions verbatim and stop. The usual
one-time fix is connecting Tailscale/VPN (camera unreachable). No API key is
needed — everything runs on the engineer's Claude Code login. A "LibreOffice
not found" line is informational, not a failure — with LibreOffice installed
the engineer also gets a PDF preview and visual quality checks
(`brew install --cask libreoffice` if they want it).

## 3. Run the pipeline

Pick an output folder name like `test_report_<short-recipe>_<date>` in the
current working directory, then:

```bash
PYTHONUNBUFFERED=1 uv run --project "$PLUGIN_ROOT" python "$PLUGIN_ROOT/pipeline.py" \
  --url "<URL>" --recipe "<RECIPE>" --verify-images --publish \
  --out "<OUTPUT_FOLDER>" [--context notes.md] [--images photos/]
```

Pass `--publish` when the engineer wants the report in their Google Drive as
editable Google Slides — which is usually what they want next. It needs a
one-time consent: on the engineer's first publish a browser opens, they click
Allow, and every later run is silent (the tool only ever gets access to files
it creates). No separate sign-in step is needed — only if a run explicitly
reports one, have them run
`uv run --project "$PLUGIN_ROOT" python "$PLUGIN_ROOT/publish_cli.py" login`.
Drop `--publish` if they'd rather keep everything local. Publishing failures never
invalidate the deck — it is already on disk and can be published later.

Always pass `--verify-images` — every matched image is vision-checked against
the slot it fills, so a wrong screen or the wrong model's screenshot is caught
and re-matched rather than shipped. It adds a vision call per image slot.

It checks *identity*, not whether the picture is full: a screenshot whose
image area is legitimately blank (alignment disabled, no capture triggered)
is the correct asset and stays. Do not treat those slides as broken.

Run in the background and report progress (`== step:` and `=== Phase` lines).
Do not start a second run in parallel against the same camera.

Some slides are built by an autonomous design session (they show as
`agent slide <id>: attempt N` in the output) — those take a few minutes each,
so the deck phase is the slower half of the run. Relay
`model fallback: <X> -> <Y>` lines if they appear (a preferred model was
unavailable; the deck still completes on a weaker one), and if you see
`Claude Code subscription limit reached ... resets <time>`, relay the reset
time — every remaining slide will fall back to a plain layout until then.

If the engineer EXPLICITLY asks for a different slide lineup (skip a slide,
add a slide about something, reorder), make sure that request is written in
`notes.md` and add `--adaptive-structure` — the deck structure is then
adapted from the standard spec (strong bias to keep it; falls back to the
standard structure if the adaptation fails validation), and
`<OUTPUT_FOLDER>/report/diff.json` records exactly what changed and why.
Without such a request, never pass the flag: every report should follow the
standard structure. Note: adapted structures may include agent-built slides,
which add a few minutes each to the deck phase.

## 4. Deliver

On success, present the bundle — **lead with the Google Slides link when the
run published one**, since editing there is the engineer's next step:
- `Google Slides: <link>` — the editable report in their own Drive
- `Drive folder: <link>` — the Slides deck, the source .pptx, and `assets/`
- `<OUTPUT_FOLDER>/report/deck.pptx` — the local copy (plus `deck.pdf` for
  quick preview when available, and `plan.json` — the audit record)
- `<OUTPUT_FOLDER>/assets/` — every extracted asset, organized under
  `deliverables/`

To publish an existing run later (or retry a failed publish), no rebuild is
needed:

```bash
uv run --project "$PLUGIN_ROOT" python "$PLUGIN_ROOT/publish_cli.py" \
  --run "<OUTPUT_FOLDER>/assets" --deck "<OUTPUT_FOLDER>/report/deck.pptx"
```

Each publish creates a NEW dated Drive folder — it never overwrites a folder
the engineer may already have edited.

Then read `plan.json` and translate into plain language:
- `match_report` and any skipped slides — e.g. "the Cracks model was never
  trained, so its training-report slide was omitted" or "your photo of the
  mounted camera was used on the imaging slide". Flag any match marked
  `verified: false` for a manual look.
- `model_substitutions` (when non-empty) — say which parts ran on a weaker
  model than intended, so the engineer knows where to read more carefully.
- `agent_report` on any slide with `attempts: 2` or a populated `issues`
  list — that slide needed a retry or fell back to a plain layout; worth a
  glance before the deck goes to a customer.

Two viewing notes worth passing on: the deck's fonts are Proxima Nova and
Montserrat — on machines without them installed (including the bundled
`deck.pdf` render), text shows in substitute fonts; the .pptx itself is
correct. And every slide is native shapes, fully editable in Google Slides
or PowerPoint.

If the engineer wants changes (different image on a slide, tweaked wording,
or a structure change — add `--adaptive-structure` for the latter), collect
the correction into `notes.md` and re-run ONLY the deck phase — it reuses
the extracted assets and takes ~2 minutes:

```bash
uv run --project "$PLUGIN_ROOT" python "$PLUGIN_ROOT/deck_cli.py" \
  --run "<OUTPUT_FOLDER>/assets" --variant <variant from assets/data/manifest.json> \
  --verify-images --out-dir "<OUTPUT_FOLDER>/report" \
  [--context notes.md] [--images photos/] [--adaptive-structure]
```

(Rebuilds that touch agent-built slides re-run those design sessions, so
allow a few minutes each.)

## 5. If it fails

Phase 1 failures stop before any deck is built; the output names the failing
step and the partial run directory. Diagnose from its `data/manifest.json`
and `debug/failure.png` (view it and explain what the camera showed). Offer
exactly one retry — reruns are safe. If it fails twice at the same step,
collect the run directory path and failure screenshot for the plugin
maintainer — do not try to fix the pipeline yourself.

Two failures have specific answers rather than a retry:
- **Subscription limit reached** — relay the reset time; retrying before it
  will fail the same way.
- **Recipe ambiguous / not found** — the output lists the recipe names it
  saw; show them, ask which one, rerun with that exact name.

If phase 1 succeeded but phase 2 failed, do NOT re-extract: the assets are
already on disk. Rerun only the deck phase with the command in section 4,
pointing `--run` at the partial run directory the output named.
