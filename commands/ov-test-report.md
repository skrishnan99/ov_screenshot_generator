---
description: Build and publish a full OV camera test report — extract recipe assets, build the branded deck, upload it to the team Drive as Google Slides. Fire-and-forget; ~25-30 minutes.
argument-hint: <camera-url> <recipe name> [extra instructions]
---

Build a complete OV camera test report, end to end, from this request:

$ARGUMENTS

## How to read the arguments

- The token starting with `http://` or `https://` is the **camera URL**.
- Everything else up to any extra instruction is the **recipe name** —
  approximate is fine (the pipeline resolves it against the camera's recipe
  list), and recipe names routinely contain spaces, `#`, `~`, `(`, `)`.
- Anything that reads as an instruction ("don't upload", "skip node-red",
  "audience is internal", a notes file path) is part of the initial request
  and carries the same force it would in a typed prompt.

If the camera URL or the recipe name is genuinely absent, ask for the
missing piece once, then start. The engineer-profile collection below is
MANDATORY and shares that same single up-front interaction — one moment of
questions at minute zero, never a second round and never a mid-run one.

## What to do

This command is the fire-and-forget flow the plugin's skills define — follow
them, do not improvise around them:

1. **Front-load the one interactive moment.** Run the plugin's preflight
   with `--ensure-engineer-profile` (always) and `--ensure-google-auth`
   (unless the request opted out of uploading), plus `--fix` and the camera
   `--url`, BEFORE anything else. If Google sign-in is missing, a browser
   opens once, now — tell the user that this moment is the only interaction
   the whole run needs. Never let anything surface at the publish step,
   ~25-30 unattended minutes in. If the request said to keep the deck
   local, drop only the Google flag.

   **The engineer contact profile is mandatory.** It signs the report's
   contact slide and title byline, and preflight FAILS while any of name /
   email / phone is missing, naming the missing fields. When it does, you
   MUST ask for exactly those fields — a direct, explicit question naming
   each one ("What is your phone number?"), never an aside folded invisibly
   into another question. It may share the single up-front interaction with
   a missing-URL/recipe ask, but every missing field is spelled out. Then
   save (empty arguments keep existing values, so pass just what was
   given) and re-run preflight to confirm it now passes:

   ```bash
   uv run --project "$PLUGIN_ROOT" python -c \
     "from core.engineer import save_profile; save_profile('<name>', '<email>', '<phone>')"
   ```

   If the user explicitly refuses or ignores the question, do not block the
   run: save whatever they gave, re-run preflight WITHOUT
   `--ensure-engineer-profile`, and continue — the contact slide degrades
   to visibly generic placeholders ("SE Name", "SE Email", "SE Contact
   Number"), and the final summary MUST say so prominently. Once the
   profile is complete, no run ever asks again.
2. Invoke the **`ov-test-reports:extract-recipe-assets`** skill with the URL
   and recipe. State once that the whole job takes ~25-30 minutes and that the
   run will activate the recipe on the camera if it is inactive, then start.
3. When extraction completes, continue straight into the
   **`ov-test-reports:overview-deck`** skill with the run directory. Build
   the default deck exactly as `references/default-deck.md` specifies.
4. Publish the deck — the deck only, as Google Slides, to the team shared
   drive — automatically, per the skill's step 7. Sign-in already happened in
   step 1, so this must not prompt for anything.

Both skills' operating mode applies in full: no mid-run questions, fill gaps
with the documented defaults, retry a failed extraction step once
automatically, and stop only for the skills' named stop conditions (ambiguous
recipe, unreachable camera, a camera the request says is on a live production
line, or an instruction that would destroy existing work).

## What to report at the end

One summary: the Google Slides link first, then slide count, which models
were found and which assets each slide used, every assumption or default you
applied, anything omitted and why (e.g. a segmentation model with no training
report), and any warnings from the run. If publishing failed, say so plainly
and give the local `.pptx` path — a failed upload never invalidates the deck.
