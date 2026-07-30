---
name: extract-recipe-assets
description: Capture a complete folder of inspection assets from an Overview AI camera for one recipe — screenshots of every configuration screen, native-resolution images, vision descriptions, structured metadata, and the Node-RED IO logic summary. Use when a sales engineer wants recipe assets or screenshots from an OV camera, given a camera URL and recipe name.
---

# Extract recipe assets from an OV camera

You orchestrate a battle-tested extraction pipeline. **Never navigate the
camera UI yourself, never re-implement any pipeline logic, and never modify
the plugin's code** — your job is intake, running the bundled commands,
monitoring, and explaining results.

The plugin root (referenced as `$PLUGIN_ROOT` below) is the directory two
levels above this SKILL.md file; when the environment variable
`CLAUDE_PLUGIN_ROOT` is set, use that.

## 1. Collect inputs

From the conversation (ask only for what's missing):
- **Camera URL** — any page on the camera works; the origin is what matters.
- **Recipe name** — approximate is fine; the pipeline resolves it against the
  recipe list with an LLM. If resolution turns out ambiguous, the run fails
  with the candidate names — relay them and ask the engineer to pick.
- **Variant** (optional, e.g. `ov80i`) — used only for a preflight support
  check; the pipeline auto-detects the real variant.

Before running, tell the engineer: the run takes **~20 minutes** for a recipe
with two or three AI models, and it will **activate the recipe on the camera**
if it is currently inactive (that is how the editor opens). If the camera is
on a production line, they should confirm that's acceptable.

Time scales with the number of models, not the number of steps: each model
adds its own ROI, view-all-ROIs, settings and training-report captures, and
those are driven live every run (their goals name the model, so they cannot
replay from the navigation cache). Steps that do replay are much faster on a
camera whose UI version has been seen before.

## 2. Preflight

```bash
uv run --project "$PLUGIN_ROOT" python "$PLUGIN_ROOT/preflight.py" --fix --url "<URL>" --variant "<variant if given>"
```

If it fails, relay the printed fix instructions verbatim and stop. The usual
one-time fix is connecting Tailscale/VPN (camera unreachable). No API key is
needed — everything runs on the engineer's Claude Code login. A "LibreOffice
not found" line is informational, not a failure.

## 3. Run the extraction

```bash
PYTHONUNBUFFERED=1 uv run --project "$PLUGIN_ROOT" python "$PLUGIN_ROOT/cli.py" \
  --url "<URL>" --recipe "<RECIPE>"
```

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

If the engineer wants the assets in their Google Drive, publish them. The
first publish on a machine opens a browser for a one-time Google consent —
after that it is silent, so no separate sign-in step is needed:

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
plain language what the camera UI showed. Offer exactly one retry (a rerun
is safe and often succeeds; transient UI slowness is the usual cause). If it
fails twice at the same step, collect the run directory path and the failure
screenshot for the engineer to send to the plugin maintainer — do not try to
fix the pipeline yourself.

Two failures have specific answers rather than a retry:
- **Subscription limit reached** — relay the reset time; retrying before it
  will fail again.
- **Recipe ambiguous / not found** — the output lists the candidate recipe
  names it saw on the camera. Show them and ask the engineer which one, then
  rerun with that exact name.
