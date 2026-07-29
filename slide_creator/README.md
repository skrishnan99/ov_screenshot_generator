# slide_creator (deck_builder)

Builds a customer-facing case-study **Google Slides deck** from a
`screenshot_generator` run: system-captured product screenshots +
system-generated descriptions, blended with optional **user-provided
screenshots and notes** (which always take priority). Outputs a Google
Drive link to a native Google Slides file.

The slide skeletons, deck order, step numbering, and camera-variant
overlay convention are copied verbatim from
`recipe_decryption/case_study` — this package is that pipeline with the
recipe-export input swapped for a screenshot run.

## Quick start

```bash
cd slide_creator
uv sync

# Full build: LLM copy pass + user assets + Drive export
uv run python -m deck_builder ../runs/20260728_163942 \
    --user-dir examples/user_context

# Local-only, no Claude / no Google
uv run python -m deck_builder ../runs/20260728_163942 --skip-llm --skip-drive
```

The CLI prints the Google Slides link on success. Re-running the same
run **updates the same Drive file in place** (stable share link); pass
`--new` for a frozen snapshot.

## Inputs

| Input | Source | Contents |
|---|---|---|
| run dir (required) | `screenshot_generator` | `manifest.json`, `descriptions.json`, screenshots, optional `node_red_description.md` |
| `--user-dir` (optional) | the user | `notes.md`, image files, optional `captions.json` (`{"file.png": "caption"}`) |

**Prioritization contract:** user notes ground the LLM-written copy
(problem/solution/deployment time use the user's framing), and user
images replace system screenshots wherever a one-shot multimodal
matching call is highly confident. Every placement decision is audited
in `out/<run_id>/deck_manifest.json` → `user_assignments`.

## Pipeline

```
RunBundle (run dir) + UserContext (--user-dir)
    → llm.build_llm_cache        one structured Claude call → slide copy ({} on failure)
    → planner.plan_deck          GLOBAL_HEAD → per-model groups → GLOBAL_TAIL
    → asset_matching.auto_assign user images replace system defaults (no-op on failure)
    → render.render_deck         slide_creator fills each skeleton → per-slide .pptx
    → drive_export               merge (theme-baking) → upload as native Google Slides
```

Every stage degrades gracefully: no API key → deterministic copy from
the descriptions; failed capture step → that slide is skipped; failed
matching → deck proceeds fully system-generated.

## Deck structure (same as recipe_decryption)

recipe_title → problem_solution → results_image (×model) →
configuring_ov80i → imaging_setup¹ → aligner_setup² → roi_setup³ →
per classification model (classifier_setup → cls_rois_setup →
training_stats → concise_results_classifier) → per segmentation model
(segmenter_setup → training_stats → concise_results_segmenter) →
nodered_setup (conditional) → library → results → basic/advanced camera
info → unique_factors → defect_generator_info → integration_info →
team_and_locations → contact. (¹²³ = numbered "Step N" slides.)

Camera variants: `deck_builder/skeletons/<variant>/<name>.pptx`
overrides the base skeleton when present (file drop, no code change);
the variant comes from the run manifest (`ov10i`/`ov20i`/`ov80i`).

## Layout

| Module | Owns |
|---|---|
| `run_bundle.py` | Parsing the run dir; model discovery from the manifest's per-model steps |
| `user_context.py` | `notes.md` + images + `captions.json` loading |
| `manifest.py` | Pydantic deck document (two hole kinds: `text`, `image`) |
| `templates/` | One module per slide type: skeleton path, hole schema, `applies()`, `build()` |
| `planner.py` | Deck order + step numbering (copied from recipe_decryption) |
| `llm.py` | The single copy-extraction Claude call |
| `asset_matching.py` | The single multimodal user-image placement call |
| `render.py` | Hole resolution → `slide_creator.render` (autofit off; Google re-fits) |
| `merge.py` | Single-slide pptx → one deck, with theme/background baking |
| `google_auth.py`, `drive_export.py` | OAuth + upload-as-Google-Slides, stable links |

## Setup

- **Anthropic**: `ANTHROPIC_API_KEY` in the environment or a nearby
  `.env` (this dir or `screenshot_generator/`). Model override:
  `DECK_LLM_MODEL` or `--model` (default `claude-sonnet-5`).
- **Google**: OAuth Desktop-app credentials at
  `~/.config/slide-agent/credentials.json` (shared with
  recipe_decryption / slide_agent). One-time consent:
  `uv run python -m deck_builder.google_auth`.
- **Drive folder**: `--folder-id` or `DRIVE_EXPORT_FOLDER_ID`; unset
  targets My Drive root.

## Tests

```bash
uv run pytest tests/ -q
```

Covers: run parsing/model dedup, deck order + step numbers against the
example run, every template schema vs. its skeleton's actual holes,
render + cache + failure modes, user-context loading, stats fallbacks.

## Extending

**New slide type**: author the skeleton in Google Slides, export
`.pptx` into `deck_builder/skeletons/`, write
`deck_builder/templates/<name>.py` (five attributes — see
`templates/base.py`), add it to `ALL_TEMPLATES` and the right phase
list in `templates/__init__.py`. The schema-vs-skeleton test will hold
you honest.
