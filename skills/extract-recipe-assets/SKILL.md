---
name: extract-recipe-assets
description: "Capture a complete folder of inspection assets from an Overview AI camera for one recipe — screenshots of every configuration screen, native-resolution images, vision descriptions, structured metadata, and the Node-RED IO logic summary. Use when a sales engineer wants recipe assets or screenshots from an OV camera, given a camera URL and recipe name. Runs unattended — states what it is doing and proceeds, rather than asking for confirmation. This is step 1 of the two-step report flow: if the same request also asks for a deck, report or case study, run this first and then continue into the overview-deck skill without stopping to ask."
user-invocable: false
---

# Extract recipe assets from an OV camera

You orchestrate a battle-tested extraction pipeline. **Never navigate the
camera UI yourself, never re-implement any pipeline logic, and never modify
the plugin's code** — your job is intake, running the bundled commands,
monitoring, and explaining results.

The plugin root (referenced as `$PLUGIN_ROOT` below) is the directory two
levels above this SKILL.md file; when the environment variable
`CLAUDE_PLUGIN_ROOT` is set, use that.

## Operating mode: fire and forget

A sales engineer gives one instruction — a camera, an approximate recipe name,
maybe some notes — and expects to come back to a finished deck. The whole job
takes ~25-30 minutes, and **every question you ask blocks it for as long as they
take to notice.** They are not watching. A run that stalls on "which audience
is this for?" wastes the entire window and is the worst outcome this skill has.

So: **take the request, fill the gaps with the documented defaults, and run the
whole thing to completion** — extract, then hand straight to the deck skill — without
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

## 1. Collect inputs

From the conversation (ask only for what's missing):
- **Camera URL** — any page on the camera works; the origin is what matters.
- **Recipe name** — approximate is fine; the pipeline resolves it against the
  recipe list with an LLM. If resolution turns out ambiguous, the run fails
  with the candidate names — relay them and ask the engineer to pick.
- **Variant** (optional, e.g. `ov80i`) — used only for a preflight support
  check; the pipeline auto-detects the real variant.

Before running, state — do not ask — that the run takes **~20 minutes** for a
recipe with two or three AI models, and that it will **activate the recipe on
the camera** if it is currently inactive (that is how the editor opens). Then
start.

The engineer chose this camera, so treat activation as expected. The one
exception: if their own request says the camera is on a live or production
line, confirm before touching it — that is the "extremely dangerous" case, and
the only one here worth blocking for.

Time scales with the number of models, not the number of steps: each model
adds its own ROI, view-all-ROIs, settings and training-report captures, and
those are driven live every run (their goals name the model, so they cannot
replay from the navigation cache). Steps that do replay are much faster on a
camera whose UI version has been seen before.

## 2. Preflight

```bash
uv run --project "$PLUGIN_ROOT" python "$PLUGIN_ROOT/preflight.py" --fix --url "<URL>" --variant "<variant if given>"
```

**When the request also asks for a deck or report** (the chained flow), add
`--ensure-google-auth`: publishing will need Google sign-in, and if it is
missing the one-time browser consent then happens HERE, at minute zero — the
only interaction of the whole run — instead of interrupting the publish step
~25-30 minutes later. Tell the user that is what the browser window is.

If it fails, relay the printed fix instructions verbatim and stop. The usual
one-time fix is connecting Tailscale/VPN (camera unreachable). No API key is
needed — everything runs on the engineer's Claude Code login. A "LibreOffice
not found" line is informational, not a failure.

## 3. Run the extraction

```bash
PYTHONUNBUFFERED=1 uv run --project "$PLUGIN_ROOT" python "$PLUGIN_ROOT/cli.py" \
  --url "<URL>" --recipe "<RECIPE>"
```

**When the request describes the part or application in the engineer's own
words** — "we're inspecting the terminal-block cover on dryer rear
panels", or they gave a notes file — pass those words through with
`--context "<their words>"` (literal text or a file path). It grounds the
run's part identification, which every capture-pick judgment anchors to.
Pass ONLY engineer-authored content, verbatim: never write your own
summary of the camera or recipe, and omit the flag when they said nothing
about the part — wrong context is worse than none.

Run it in the background and report step progress (`== step:` lines) as it
goes. Outputs land in `runs/<timestamp>/` under the current working
directory. Do not start a second run in parallel against the same camera.

Watch for two lines in the output and relay them when they appear:
- `model fallback: <X> unavailable -> using <Y>` — a preferred model was
  rate-limited or unavailable, so a weaker one answered. The run continues
  and stays usable; mention which parts were affected.
- `Claude Code subscription limit reached ... resets <time>` — the account's
  quota is exhausted for every tier. Relay the reset time and stop; the
  engineer can retry after it, or the maintainer can raise the limits.

## 4. Deliver

On success, read `runs/<ts>/data/manifest.json` and summarize for the
engineer: recipe matched, models found (from `meta.json`'s `models`), steps
completed with timings, and where things live:
- `deliverables/screenshots/` — every configuration screen
- `deliverables/images/` — native-resolution captures, overlays, composites,
  and `*_plain.png` originals of any screenshot that was composited
- `deliverables/report/` — vision descriptions of every screenshot
  (`descriptions.json`) + the Node-RED IO logic summary
- `data/` — manifest, structured metadata (`meta.json`: model roster with
  per-model screenshot links, plus extracted facts), raw Node-RED flow

Two things about the assets are worth explaining if the engineer asks:

**The imaging-setup screenshot is composited.** That screen is a settings
page whose own viewer is usually empty, so the aligner's template image is
rendered into the viewer's exact pixel area — `02_imaging_setup.png` shows
the screen as you would expect it to look. The untouched capture is kept as
`images/02_imaging_setup_plain.png`. `meta.json`'s
`imaging_setup_with_template` records which happened; when it says
`composited: false` it also gives the reason, and the plain capture stays as
the deliverable.

**An empty image area is often correct, not a failure.** A recipe with "Skip
Aligner" enabled genuinely has no template image, and a manually-triggered
camera has no live preview until someone captures one. Those screens are
reported truthfully rather than retried or dropped. Say so plainly instead of
presenting them as missing data.

**Do not publish the assets by default.** Only when the engineer explicitly
asked for the assets in Drive — "upload the assets", "put the screenshots in
my Drive". A request for a report is *not* such a request: the deck skill
publishes the finished deck and nothing else, which is the intended outcome.

When they did ask, assets go to their OWN Drive library — the team shared
drive is for finished decks only. The first publish on a machine opens a
browser for a one-time Google consent; after that it is silent:

```bash
uv run --project "$PLUGIN_ROOT" python "$PLUGIN_ROOT/publish_cli.py" --run runs/<ts>
```

Also relay any `warning:` lines from the output (e.g. an image that never
finished loading before capture) so the engineer knows which screenshots to
double-check. If the manifest has `model_substitutions`, say plainly which
calls ran on a weaker model than intended — descriptions written by a lower
tier are still useful but less exhaustive.

## 5. If it fails

The pipeline stops at the failing step and names it. Diagnose from
`data/manifest.json` (per-step status/notes) and `debug/failure.png` +
`debug/failure_snapshot.txt` — view the failure screenshot and explain in
plain language what the camera UI showed.

**Retry once automatically.** A rerun is safe, transient UI slowness is the
usual cause, and it often succeeds. Do not ask permission — say you are
retrying and retry. If it fails twice at the same step, stop and report: the
run directory path, the failing step, and the failure screenshot for the
engineer to send to the plugin maintainer. Do not try to fix the pipeline
yourself.

Two failures have specific answers rather than a retry:
- **Subscription limit reached** — relay the reset time; retrying before it
  will fail again.
- **Recipe ambiguous / not found** — the output lists the candidate recipe
  names it saw on the camera. Show them and ask the engineer which one, then
  rerun with that exact name.

## 6. Hand off to the deck skill

Extraction produces the assets; it does not produce a deck. The deck is built
by **`ov-test-reports:overview-deck`**, which owns the layout engine, the brand
pack and the verification loop.

**If the request also asked for a deck, report, slides or a case study**
— "get the assets and build a test report", "make me a report for recipe X" —
then extraction was only the first half. Continue straight into
`overview-deck` with the run directory as its source assets. Do **not** stop to
ask whether to build the deck; they already said so, and after a 20-minute
extraction an unnecessary question is the last thing they want. Say what you
are doing and keep going:

> "Assets are in `runs/<ts>/` — 13 steps, 3 models. Building the deck now."

**If they asked for assets only**, stop here and offer:

> "The assets are in `runs/<ts>/`. Want me to build the report deck from
> them?"

Either way: do not build a deck yourself, and do not route to
`generate-test-report` — that skill is deprecated.
