# Backlog

Open items, with enough context to pick up cold. Ordered roughly by value.
Everything here is deliberately deferred, not forgotten — the pipeline is
working end to end without any of it.

## 1. Slides-backed rendering for the acceptance gate

**Status:** the original worry (conversion fidelity) is ANSWERED — see below.
What remains is narrower and worth doing.

**Answered 2026-07-29.** The published deck was exported back from Drive as
PDF and inspected: Google rendered it with `ProximaNova-Bold`,
`ProximaNova-Regular`, `ProximaNova-RegularIt`, plus the Montserrat family —
the Workspace has Proxima Nova provisioned, and the 8-master deck converted
correctly, including the agent-built slides. Conversion fidelity is fine.
Do not re-litigate this without new evidence.

**What actually remains.** Proxima Nova is *not installed locally*, so
LibreOffice substitutes it in our own renders — and those renders are what
the agent judges against in the render-look-revise loop, the acceptance
gate's vision verdict, the design-guide corpus renders and neighbour
renders. Different font metrics mean text that looks fine locally could
overflow in Slides.

Fix, borrowed from `recipe_decryption/case_study/preview/google_slides.py`:
upload the pptx → Drive converts → export PDF via the Drive API → render
PNG with PyMuPDF → delete the temp file. That renders with the engine that
will actually display the deck. Their `rasterize_batch` merges N slides into
one upload and splits the multi-page PDF (~6–10× faster), with sequential
fallback.

Scope it tightly: **use it for the acceptance gate / final verdict, keep
LibreOffice for the agent's fast iteration**. A pluggable rasterizer seam
(their `preview/__init__.py`, ~50 lines, auto-detect Slides when credentials
exist) is the right delivery vehicle and mirrors our `--llm-backend`
selection.

Cheaper stopgap: install Proxima Nova and Montserrat locally, which fixes
the metrics mismatch without any network round-trips.

## 2. Drive publish: update-in-place for stable customer links

**Status:** designed, not built. Borrowed from
`recipe_decryption/case_study/drive_export.py`.

Today every publish creates a NEW dated folder and a new Slides file. That
is the safe default — an engineer may have edited the previous deck in
Slides, and silently overwriting their work is the worst thing this feature
could do.

But it loses a workflow the other pipeline supports deliberately: the first
export creates a Drive file and records its `file_id`; **subsequent exports
update that same file in place**, so a link already shared with a customer
keeps working *and keeps its sharing settings*. If the file has since been
trashed, it transparently falls back to creating a fresh one.

Proposal: add `--update` (opt-in, default stays new-folder). Requires
storing `file_id` in the manifest — we currently record links but not an id
we can target. Their `_try_update_file` is the reference implementation:
`files().get(fields="id, trashed")` first, treat 403/404 and `trashed` as
"create new instead", any other error as fatal.

Natural companion: **deck fingerprint** — hash the ordered
(slide id, content fingerprint) pairs so we can answer "is the Drive copy
behind the local deck?" They compute this on export and store it alongside
the file id.

## 3. Cheap wins from the case_study Drive exporter

All small, all verified as real by reading their code:

- **Token scope validation.** A cached token granted under a narrower scope
  refreshes fine and then 403s at request time, far from the cause. Their
  `_token_scopes_ok` discards such a token so the user gets one clean
  re-consent instead. **We have this latent bug** — it will bite the day we
  add a scope (e.g. `presentations` for a Slides-API path). ~10 lines.
- **90 MB size warning.** Google rejects pptx imports above ~100 MB. Our
  deck was 66 MB before embed-time downscaling and is 37 MB now; a larger
  recipe could cross it. Warn with headroom so the failure comes from us,
  not from Google.
- **`supportsAllDrives=True`** on every Drive call — one keyword, enables
  shared-drive destinations, no downside.
- **`_verify_folder_access`** — fail fast with an actionable message when a
  destination folder is unreachable, with a `drives().get` fallback because
  a shared drive's root id only resolves that way. Relevant if we ever
  accept a folder id instead of our own named library.

## 4. Theme baking — make slides theme-independent

**Status:** insurance, not a fix. Do it next time assembly is touched.

In pptx a colour can be literal (`<a:srgbClr val="532EE3"/>`) or a reference
(`<a:schemeClr val="accent1"/>`) resolved through slide → layout → master →
theme. Our decks carry **8 masters, 7 themes and 137 `schemeClr`
references**, because the OPC transplant gives every skeleton its own
master. If an importer ever flattens or re-associates masters, "accent1"
silently means a different colour and brand purple becomes something else.

Baking resolves each slide's references against *its own* theme and rewrites
the XML to literals, making the slide self-contained. Reference
implementation: `case_study/preview/merge.py` `_slide_theme_maps` /
`_bake_theme_references` / `_bake_placeholder_text_colors` /
`_effective_background`.

Two subtleties a naive version gets wrong: `bg1`/`tx1`/`bg2`/`tx2` are
indirected through the master's `clrMap` and can be flipped by a
`clrMapOvr` on the layout or slide (dark designs swap text/background
roles — ignoring the override renders light text as dark); and transform
children (`lumMod`, `alpha`) must be preserved, which works because
`srgbClr` accepts the same children.

**Only the colour half applies to us.** We have *zero* theme-font tokens
(`+mn-lt`/`+mj-lt`) — our typefaces are explicit, which is exactly why
Proxima Nova survived conversion. Skip the font baking.

Empirically our published deck's colours render correctly, so this guards
against a failure we have not observed.

## 5. Skeleton template media — one-time offline re-encode

**Status:** ready to do, ~1 hour, needs a visual review afterwards.

Deck size went 66 MB → 37 MB when we started downscaling images at embed
time. Of what remains, **31.5 MB is decorative media baked into the skeleton
templates** (2048×1152 RGBA PNGs), transplanted verbatim by the OPC surgery,
which never touches `sized_for_slot`. Our own matched assets are now only
4.3 MB.

Preferred approach: a one-time script that re-encodes the templates' media in
place (opaque photographic PNGs → JPEG, oversized art → slot-appropriate
dimensions), reviewed visually once and committed. Expected result: decks
around 10 MB, permanently, with no runtime risk added to the transplant path.

Rejected alternative: downscaling during transplant — it would re-encode
brand artwork on every build inside the most delicate code in the deck path,
for the least additional gain.

## 6. RESOLVED: `foreach_block_models` verified against a real camera

**Closed 2026-07-29** on camera ov80i-gsac177082, recipe "#4 Camera 56959
Tail" (1 segmentation + 2 classification models). Produced 6 per-model
captures, all 6 content-distinct, with a correct model envelope:

    Horn Quality   classification  roi + view_all_rois_classification
    Cracks         segmentation    roi + view_all_rois_segmentation
    Hole Presence  classification  roi + view_all_rois_classification

**Open perf note (not a defect).** `view_all_rois_classification` took
211.3s for 2 models vs 75.1s for 1 in the segmentation step. Switching to
the second model made the agent back out of the modal, re-open "View All
ROIs", open the selector and pick the model — the goal permits this, it is
just slow. Per-model goals interpolate the model name, so they are
data-conditional and always run agentically rather than replaying from a
trace; the cost recurs every run. Worth a targeted per_model_goal that
drives the selector directly without leaving the page.

## 7. Brand audit — available but not wired in

**Status:** intentional; re-enable when useful.

`deck.brand.audit_deck()` does deterministic lint over every slide plus a
vision review. It is reachable via `deck_cli.py --brand-audit` but off by
default. Its vision tier is now scoped to *generated* slides only
(`generated_slide_numbers`), which fixed the false positives it produced when
judging the company's own canonical templates against a few reference
renders. Lint is free and accurate; the vision tier costs ~15–20s scoped.

## 8. Agent-slide batching by family (at ~30+ slides)

**Status:** noted in `build_agent_slides`' docstring; not needed yet.

All of a run's agent slides are built in ONE session so they are consistent
with each other. At ~30+ agent slides that becomes unwieldy (turn budget,
context). The natural extension is batching **by family** — all numbered
steps in one session, all stat cards in another — which preserves the
consistency benefit where it actually matters. Current decks use 9.

## 9. RESOLVED (was: "SDK transport is rate-limited where the CLI is not")

**Closed 2026-07-29. It was our bug, not Anthropic metering.**

`rate_limit_note()` treated a RateLimitEvent as fatal when EITHER `status`
or `overage_status` read "rejected". But the SDK emits that event alongside
ordinary successful responses, and on a subscription with no pay-as-you-go
overage enabled `overage_status` is permanently "rejected":

    working Sonnet call -> status='allowed',  overage_status='rejected'
    exhausted Fable     -> status='rejected', overage_status='rejected'

So every agentic step aborted on a healthy account, reporting a rate limit
for a call that had actually succeeded. The CLI transport never parses these
events, which is exactly why it looked immune — hence the false "SDK is
throttled where the CLI isn't" conclusion. Fix: trust `status` only.
Regression test: `tests/test_rate_limit.py`.

Corollary: the navigator does NOT need a CLI transport. It requests Sonnet,
verified working through the SDK.

**Lessons.** (1) Compare like for like on model — limits are per-model, and
probes at different tiers are a confound. (2) When a vendor path looks
broken, check our own parsing of its signals before concluding the vendor
is at fault; two separate wrong diagnoses here both came from skipping that.

## 10. FIXED 2026-07-29: Acceptance gate false-rejected on a rendering artifact

**Found 2026-07-29** in the first full end-to-end build: attempt 1 rejected
6 of 8 agent slides. Five carried the SAME complaint — the top-left numeral
"reads 1 while the title says Step 5/7/15/16", and/or sits clipped against
the purple rail.

That numeral is the **slide-number placeholder**, assigned by position when
the deck is assembled. The gate renders each candidate as a standalone
one-slide pptx, where it necessarily reads "1". The mismatch is an artifact
of isolation and cannot occur in the delivered deck (verified: p08 -> "8",
p09 -> "9", p20 -> "20").

Proof it is a false signal: `model_training_settings_horn-quality` was
ACCEPTED on attempt 1, and its isolated render shows "1" against a "Step 6"
title — identical to what the other five were failed for. The gate is both
wrong and inconsistent.

Cost: a full extra agent session (6 slides regenerated on Opus) per build,
plus the risk that a genuinely good slide exhausts its retries and drops to
the freeform fallback — which is exactly what happened to
`model_view_rois_hole-presence` (see item 11).

**Fixed** in `VERIFY_PROMPT` (deck/agent_slide.py): the reviewer is now told
the top-left numeral is a positional placeholder assigned at assembly, that
this preview always shows "1", and that neither it nor its disagreement with
a "Step N" title is a defect — while still reporting a duplicate step number
the slide drew itself. Prose, not schema, matching the rest of the guide.
NOT yet re-run against a real build.

Note the reviewer is otherwise doing good work — the sixth rejection
(`model_view_rois_hole-presence`, screenshot at ~21% width, ~40% dead
canvas) was a REAL defect, correctly caught.

## 11. FIXED 2026-07-29: Freeform fallback produced a visibly off-brand slide

**Found 2026-07-29.** When an agent slide fails acceptance twice, it falls
back to the deterministic freeform layout. In the e2e deck, slide 20 came
out clearly broken against every convention the deck establishes:

- title reads "Model View Rois Hole-Presence" — the slide **id**
  titlecased — instead of "Step 14: All labeled regions — Hole Presence";
  the "Step N:" convention is lost entirely
- the step number leaks into the body as a bare "14" on its own line
- the intended title is dumped as the first body line
- layout is mirrored (text left / image right; every other slide is image
  left / text right) and body copy is plain black, not the purple bold
  treatment

Cause, `deck_cli.py` (~line 440):

    entry["tokens"].setdefault("_ff_title", entry["id"].replace("_"," ").title())
    body = "\n".join(v for k, v in texts.items() if not k.startswith("_ff"))

**Fixed** in two places, NOT yet re-run against a real build:

- `deck_cli.py` — `_ff_title` now comes from the real `title` token with the
  `Step N: ` prefix reapplied, and the body join excludes `title`/`step_no`
  instead of only `_ff*`.
- `deck/assemble.py` `fill_freeform()` — adopts the numbered-step geometry
  from the design guide verbatim (title `0.74, 0.42, 8.63 x 0.50` Bold 30 pt
  black; image `0.70, 1.73, 5.48 x 3.14`; body `6.30, 2.85, 3.60` Bold 17 pt
  `#532EE3`), so the fallback is image-left/text-right like its neighbours
  rather than the mirrored 22 pt / grey 13 pt layout it used before.
  `_freeform_body()` gained `size_pt` / `bold` / `color`, defaulted to the
  old values so the text-only and image-only branches are unchanged.

## 12. Smaller things

- **Fonts for local rendering:** Proxima Nova and Montserrat are NOT
  installed on the dev machine, so LibreOffice substitutes them in
  `deck.pdf` and in every local render. Google Slides renders them
  correctly (verified), so this only affects local previews and the visual
  feedback the agent gets — see item 1. Installing the two fonts is the
  cheapest fix.
- **Text overflow in skeleton boxes:** the brand linter deliberately skips
  auto-grow frames (too noisy), so LLM copy that shrinks-to-fit inside a
  fixed skeleton box is not detected. Only the vision tier would catch it.
- **Deck build time:** the last full build was 833s, of which the agent
  session was ~4 min. Most of the rest is ~20 vision verifications plus the
  repair round. Verification is already parallelised at 4 workers; raising
  that is the easy lever if it starts to hurt.

- **Dead-tier re-probing.** The fallback ladder re-attempts a tier it has
  already found unavailable in this run — the e2e deck build burned 5+
  Fable requests, each rejected, before falling back to Opus every time.
  Rejections return fast so the cost is small, but a per-run
  "known unavailable" set would remove the waste and the log noise.
