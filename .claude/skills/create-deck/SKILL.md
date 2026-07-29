---
name: create-deck
description: Build a Google Slides case-study deck from a screenshot_generator run directory (system screenshots + descriptions) plus optional user-provided screenshots and notes. Outputs a Google Drive link to a native Google Slides file. Use when the user asks to create a slide deck / ppt / case study from a screenshot run.
---

# Create a case-study slide deck from a screenshot run

The `slide_creator/` project in this repo turns one screenshot run
(`runs/<timestamp>/`) into a fully formatted case-study deck using the
same skeletons and slide order as `recipe_decryption`, and exports it
to Google Drive as a native Google Slides file.

## Inputs

1. **Run directory** (required): a `runs/<timestamp>/` folder containing
   `manifest.json`, `descriptions.json`, screenshots, and optionally
   `node_red_description.md`. Ask the user which run to use if several
   exist (default: the newest).
2. **User context** (optional, always outranks system content): a
   directory containing
   - `notes.md` — free-form text (problem, deployment details, framing)
   - image files (`.png`/`.jpg`/`.jpeg`/`.webp`) — user screenshots/photos
   - `captions.json` — optional `{"filename.png": "caption"}`

   If the user pastes text or images in chat, write them into a
   directory of this shape first (see `slide_creator/examples/user_context/`).

## Run it

```bash
cd slide_creator
uv run python -m deck_builder ../runs/<timestamp> \
    [--user-dir <dir>] \
    [--folder-id <drive folder id>]   # default: $DRIVE_EXPORT_FOLDER_ID, else My Drive root
```

Useful flags: `--skip-drive` (local pptx only), `--skip-llm`
(deterministic copy, no Claude calls), `--new` (fresh Drive file
instead of updating this run's previous export), `--force` (ignore
render cache), `--out-dir` (default `out/<run_id>`).

On success the last line prints the Google Slides link — give that to
the user. Re-running the same run updates the same Drive file, so the
link stays stable.

## Requirements

- `ANTHROPIC_API_KEY` (in `screenshot_generator/.env` or the
  environment) for the copy + image-matching passes; without it the
  deck still builds with deterministic text.
- Google OAuth for the Drive export: `~/.config/slide-agent/credentials.json`
  (+ cached token). Prime it with
  `uv run python -m deck_builder.google_auth` — it opens a browser once.

## Troubleshooting

- **Drive auth errors**: run `uv run python -m deck_builder.google_auth`
  and complete the browser consent (this cannot be done headlessly —
  ask the user to run it with `! cd slide_creator && uv run python -m
  deck_builder.google_auth` if a browser is needed).
- **A slide is missing**: its source screenshot failed in the run
  (check `manifest.json` step `status`); the planner skips slides whose
  screenshots are absent rather than rendering broken images.
- **Wrong image placement**: the audit of user-image placements is in
  `out/<run_id>/deck_manifest.json` under `user_assignments`, and is
  printed by the CLI. Adding captions in `captions.json` improves
  matching sharply.
