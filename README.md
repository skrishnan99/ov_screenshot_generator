# OV Test Reports

Generate Overview camera inspection assets and customer-facing test-report
decks from natural language, inside Claude Code.

## For sales engineers: install as a plugin

One-time setup:

1. Install [Claude Code](https://claude.com/claude-code) and sign in.
2. In Claude Code, add the team marketplace and install the plugin:
   ```
   /plugin marketplace add <marketplace-repo-url>
   /plugin install ov-test-reports@overview-tools
   ```
3. Make sure you're on the Tailscale/VPN network that reaches the cameras.

No API key is needed — everything runs on your Claude Code login (a plan
tier with Opus access is required; runs draw from your plan's usage limits).

Then just ask, in any Claude Code session:

- *"Get me the assets for recipe `<name>` on `http://<camera-url>`"*
  → runs the **extract-recipe-assets** skill; you get a `runs/<ts>/` folder
  of screenshots, images, descriptions, and metadata.
- *"Generate a test report for recipe `<name>` on `http://<camera-url>` —
  here are my notes and photos from the visit"*
  → runs the **generate-test-report** skill; you get one folder with the
  deck (`report/deck.pptx`) and every extracted asset (`assets/`).

First use auto-installs the browser it needs (~2 min). Asset extraction takes
~20 minutes; a full test report (extract + deck) ~45. Both scale with how many
AI models the recipe has. A run will activate the recipe on the camera if it's
inactive — confirm that's okay on production lines. Installing LibreOffice and the brand fonts
(Proxima Nova, Montserrat) is optional but recommended: they enable PDF
previews, visual quality checks, and correct font rendering.

## What's inside

- `cli.py` — asset extractor: agentic, self-healing navigation of the camera
  UI ("agent discovers, script replays"), adaptive vision waits, native-image
  downloads, vision descriptions + structured facts, Node-RED export.
- `deck_cli.py` — deck generator: skeleton-based slides with content holes,
  semantic image matching with vision verification, freeform slides,
  engineer notes/photos as first-class inputs.
- `pipeline.py` — end-to-end: URL + recipe → bundled deliverable folder.
- Agent-built slides (`deck/agent_slide.py`, opt-in per slide): all of a
  run's agent slides are authored in ONE autonomous session, so they are
  consistent with each other by construction (it renders them and compares
  them side by side). Each slide is then gated and vision-verified
  independently; a single retry re-prompts only the failures, and anything
  still failing falls back to the deterministic freeform layout. Neighbour
  context resolves to the nearest FIXED slides, so it never depends on build
  order. `SG_AGENT_MODEL` picks the session model; `SG_AGENT_TRANSPORT`
  forces the `cli` or `sdk` session transport.
- Two rendering backends behind `deck/render.py`, selected by PURPOSE
  rather than a global switch (they differ by orders of magnitude in cost):
  **Google Slides** (`deck/slides_render.py` — upload, convert, export PDF,
  rasterise locally, always delete the temp Drive file) for renders that
  feed a decision, since it uses the real fonts and text metrics; and
  **LibreOffice** (`deck/soffice.py`, a private user profile per conversion
  so renders never collide) for convenience artifacts. `SG_RENDERER`
  overrides. A Slides failure falls back to LibreOffice, then to no render —
  rendering can never fail a build.
- Theme baking (`deck/assemble.bake_theme_colors`): each slide's
  `schemeClr` references are resolved against its OWN theme into literal
  colours before transplant, so a deck carrying 8 masters cannot be
  re-coloured by an importer. Verified pixel-identical across all 30 pages.
- Adaptive structure (`--adaptive-structure` + `deck/spec_adapter.py`,
  opt-in): the engineer's notes can adjust the slide lineup — the variant
  spec is regenerated with a strong copy-through bias, validated with
  retries, recorded in `diff.json`, and falls back to the default.
- Design guide (`deck/brand/design_guide.md` + `deck/design.py`): the deck's
  *conventions* in prose — slide families, title treatments, numbering —
  derived from the real template corpus by an agent session
  (`design_cli.py`) and cached. Every generated slide gets it plus renders
  of the slides it will sit between, so new slides match their neighbours.
- Brand system (`deck/brand/` + `deck/brand.py`): single-source kit (rules,
  reference renders, real logo assets) feeding renderer defaults, agent
  prompts, and the per-slide acceptance gate. `audit_deck()` also provides a
  post-assembly lint + vision audit; it is available but not wired into the
  build (its vision tier mis-flags inherited canonical slides — scope it to
  generated slides via its `slides=` argument before enabling).
- Google Drive publishing (`publish/gdrive.py`, `publish_cli.py`, `--publish`):
  uploads the assets and the deck to the engineer's own Drive, converting the
  deck to editable **Google Slides**. Per-user OAuth2 with the `drive.file`
  scope — access only to files this tool creates — consented once and cached
  in the data dir. Every publish makes a NEW dated folder; nothing is ever
  overwritten, the deck uploads first, and a failed asset is reported rather
  than fatal. One-time setup: place the OAuth client JSON (Desktop app, from
  an **Internal** Google Cloud project) at
  `publish/google_client.json` in this repo (committed deliberately — for
  desktop apps Google treats it as a non-confidential app identifier, and an
  **Internal** consent screen restricts it to your Workspace). Engineers do
  nothing: their first `--publish` opens a browser for one consent, then
  refreshes silently forever. All
  reports collect in one `OV Test Reports` folder in their Drive (override
  with `--library`, or `--library ""` for the Drive root).
- `preflight.py` — environment checks with auto-fix.
- Model tiering (`core/llm.py`): Opus for quality-critical calls, Sonnet for
  navigation/enumeration, Haiku for image-load polling, Fable for
  agent-built slides; vision descriptions run in parallel.
- Three LLM backends (`--llm-backend`, default `agent-sdk`): `agent-sdk`
  runs EVERYTHING — navigation included, via in-process SDK browser tools —
  on your Claude Code login, no API key at all (usage draws from the
  subscription's rate limits); `claude-code` runs single-shot calls on that
  login via per-call CLI spawns but still needs a key for navigation; `api`
  uses the Anthropic API and needs `ANTHROPIC_API_KEY`.
- Model-tier fallback: every call names a preferred model and walks down
  Fable → Opus → Sonnet → Haiku when a tier is unavailable (quota, no
  access), rather than failing the run. Substitutions are recorded in the
  manifest / `plan.json`, never silent. Refusals and bad requests are not
  retried on weaker tiers.

## For developers

```bash
uv sync
uv run playwright install chromium
uv run python preflight.py --fix
uv run python pipeline.py --url http://<camera> --recipe "<name>" --out my_report
# add --llm-backend api to bill the Anthropic API instead of your Claude Code login
```

Writable state lives outside the checkout: trace cache and `.env` in
`~/.ov-report-generator/` (override `OV_REPORT_DATA_DIR`); outputs under the
current directory (override `OV_REPORT_OUTPUT_DIR`). Task specs per variant
in `tasks/<variant>.yaml`, deck specs in `decks/<variant>.yaml`, slide
skeletons + sidecar hole descriptions in `deck/skeletons/`.
