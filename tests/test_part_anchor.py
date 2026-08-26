"""The pick judges anchor to THE part being inspected, not any part.

The template image is the canonical reference view of the part; one
Sonnet call describes it into meta["part_description"], and every pick
judgment (block captures, library thumbnails, library viewer) embeds that
description — replacing the "any manufactured part counts" softener that
was a proven false-positive source. A blank or random template yields NO
anchor (part_visible=false is the escape hatch): the judges fall back to
the generic criterion, never block a run. What this suite pins:

- describe_part_from_image: usable description comes back normalized; the
  part_visible=false escape hatch, an LLM failure, and an unreadable file
  all yield None — never an exception,
- with a part description, every judge prompt names THIS part and allows
  angle/zoom/exposure variance; without one, the soft recipe line returns,
- the "any real manufactured part still counts" softener never appears in
  an anchored prompt,
- evidence-first schemas: `reason` is the FIRST property of all three
  extractor judge schemas, and the part-description schema leads with the
  description,
- the criteria clarifications: ROI outlines + region-name labels are
  standing chrome (not annotations) even over a real image, and the
  search-area box does not satisfy the overlay criterion,
- the spec turns describe_part on for the template step, and the describe
  call sits inside the download_main_image branch.

Run: uv run python tests/test_part_anchor.py
"""

import inspect
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

import cli  # noqa: E402
from core import capture_criteria as cc  # noqa: E402
from core import llm  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DESC = "A cylindrical bronze bushing viewed end-on, with a machined bore."


class _StubBackend:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error
        self.calls = 0

    def complete(self, prompt, schema=None, images=None, max_tokens=4000, model=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def main() -> int:
    failures = []

    # ---- describe_part_from_image: all outcomes, never an exception ----
    with tempfile.TemporaryDirectory() as td:
        img = Path(td) / "raw.png"
        from PIL import Image

        Image.new("RGB", (64, 48), (120, 90, 40)).save(img)

        llm.set_backend(_StubBackend({"description": f"  {DESC}\n", "part_visible": True}))
        got = cli.describe_part_from_image(img)
        if got != DESC:
            failures.append(f"usable description mangled: {got!r}")

        llm.set_backend(_StubBackend({"description": "blank grey frame", "part_visible": False}))
        if cli.describe_part_from_image(img) is not None:
            failures.append("part_visible=false did not yield None")

        llm.set_backend(_StubBackend(error=RuntimeError("llm down")))
        try:
            if cli.describe_part_from_image(img) is not None:
                failures.append("LLM failure did not yield None")
        except Exception as e:
            failures.append(f"LLM failure raised: {e}")

        llm.set_backend(_StubBackend({"description": "x", "part_visible": True}))
        try:
            if cli.describe_part_from_image(Path(td) / "missing.png") is not None:
                failures.append("unreadable file did not yield None")
        except Exception as e:
            failures.append(f"unreadable file raised: {e}")

    # ---- the description call is GROUNDED: the resolved recipe name and
    # the engineer's own context enter the prompt as interpretation aids,
    # and identity guessing is forbidden — a field run's "automotive
    # panel" guess for a dryer cover made the anchored judges reject every
    # page of the actual part's captures ----
    class _PromptSpy(_StubBackend):
        def complete(self, prompt, schema=None, images=None, max_tokens=4000,
                     model=None):
            self.prompt = prompt
            return super().complete(prompt, schema, images, max_tokens, model)

    with tempfile.TemporaryDirectory() as td:
        img = Path(td) / "raw.png"
        from PIL import Image

        Image.new("RGB", (64, 48), (30, 60, 90)).save(img)
        spy = _PromptSpy({"description": DESC, "part_visible": True})
        llm.set_backend(spy)
        got = cli.describe_part_from_image(
            img, recipe="Terminal Block Secadoras L8 Tapa",
            context="we're inspecting terminal-block covers on dryer rear panels")
        if got != DESC:
            failures.append(f"grounded describe call mangled the description: {got!r}")
        if "Terminal Block Secadoras L8 Tapa" not in spy.prompt \
                or "dryer rear panels" not in spy.prompt:
            failures.append("recipe name / engineer context missing from the part prompt")
        if "Do NOT guess" not in spy.prompt or "INTERPRET" not in spy.prompt:
            failures.append("identity-guess ban / interpret doctrine missing")
        spy2 = _PromptSpy({"description": DESC, "part_visible": True})
        llm.set_backend(spy2)
        cli.describe_part_from_image(img)
        if "Context (" in spy2.prompt:
            failures.append("empty recipe+context still rendered a context block")
        if "Do NOT guess" not in spy2.prompt:
            failures.append("identity-guess ban must not depend on context")
    src_main = inspect.getsource(cli.main)
    if 'context=meta.get("user_context"' not in src_main:
        failures.append("main does not pass user context to the part description")

    # ---- anchored prompts: the part description sits IN the product
    # criterion at the decision point, demanding positive identification —
    # a preamble anchor with a generic criterion at the numbered line lost
    # to the local text (a judge passed an unidentifiable frame as
    # "plausibly" the part) ----
    anchored_crit = cc.anchored_product_criterion(DESC)
    if DESC not in anchored_crit or "positively identifiable" not in anchored_crit:
        failures.append(f"anchored criterion malformed: {anchored_crit[:80]}")
    # features over labels — an anchor's wrong object-class guess must not
    # be grounds for rejection, in the criterion AND the thumbnail anchor
    if "FEATURES" not in anchored_crit or "never grounds" not in anchored_crit:
        failures.append("anchored criterion lost the features-over-labels rule")
    if "FEATURES" not in cli._anchor_line("R", DESC):
        failures.append("thumbnail anchor line lost the features-over-labels rule")
    block_anchored = cli._block_capture_prompt("segmentation", "R", part_desc=DESC)
    if f"1. product_image — {anchored_crit}" not in block_anchored:
        failures.append("block decision point does not carry the anchored criterion")
    if "plausibly" in block_anchored:
        failures.append("the unfalsifiable 'plausibly' bar survived")
    if "any manufactured part" in block_anchored.lower() \
            or "still counts as a product image" in block_anchored:
        failures.append("the any-part softener survived into an anchored prompt")
    block_plain = cli._block_capture_prompt("segmentation", "R")
    if "inspection recipe 'R'" not in block_plain \
            or cc.PRODUCT_CRITERION not in block_plain:
        failures.append("unanchored block prompt lost the soft-line/generic pairing")

    viewer_anchored = cli.LIBRARY_VIEWER_PROMPT.format(
        recipe_line="", product_criterion=anchored_crit,
        overlay_criterion=cc.INSPECTION_OVERLAY_CRITERION)
    if f"1. product_image — {anchored_crit}" not in viewer_anchored:
        failures.append("viewer decision point does not carry the anchored criterion")
    # the thumbnail prefilter is anchored to THE part and applies the
    # RECOGNIZABLE standard (the plausible reserve is what keeps a strict
    # filter from starving the search — see test_library_pick)
    thumbs = cli.LIBRARY_THUMBS_PROMPT.format(
        recipe_line=cli._anchor_line("R", DESC), ids="#1",
        recognizable_what=cli._recognizable_what(DESC))
    if DESC not in thumbs or "the part described above" not in thumbs \
            or "RECOGNIZABLE" not in thumbs:
        failures.append("thumbnail prefilter lost its anchor or its standard")
    if "plausibly show" in thumbs:
        failures.append("the thumbnail prefilter kept the 'plausibly' bar")

    # ---- evidence-first field order ----
    for name, schema in (("block", cli.BLOCK_CAPTURE_SCHEMA),
                         ("library viewer", cli.LIBRARY_VIEWER_SCHEMA),
                         ("library thumbs", cli.LIBRARY_THUMBS_SCHEMA)):
        if list(schema["properties"])[0] != "reason":
            failures.append(f"{name} schema is not evidence-first")
    if list(cli.PART_DESCRIPTION_SCHEMA["properties"])[0] != "description":
        failures.append("part-description schema does not describe first")

    # ---- criteria clarifications ----
    note = cc.EMPTY_OUTLINES_NOTE
    if "standing chrome" not in note or "NAME label" not in note:
        failures.append("outline note lost the standing-chrome/name-label clarification")
    if "even over a real product image" not in note:
        failures.append("outline note only covers the blank-canvas case again")
    if "Search Area" not in cc.INSPECTION_OVERLAY_CRITERION:
        failures.append("overlay criterion lost the search-area carve-out")

    # ---- the pick judges run on SONNET: their tier-1 verdict is terminal
    # (no second opinion), and on the agent-sdk transport Sonnet measured
    # FASTER than Haiku (6.5s vs 14.6s) — see the tier-policy caveats in
    # core/llm.py before "optimizing" this back down ----
    from core import describer

    sys.path.insert(0, str(REPO / "skills" / "overview-deck" / "scripts"))
    import matching as matching_mod

    import deckspec as ds

    for fn in (cli.judge_block_capture, cli._library_product_thumbs,
               cli.judge_library_viewer,
               # the deck's fallback judge: same terminal-verdict asymmetry
               matching_mod.block_quality_call,
               # the load polls: hottest inner loops, Sonnet measured
               # FASTER than Haiku on this transport
               describer.check_image_loaded,
               describer.check_table_loaded,
               # the toggle eval: a wrong answer changes deck structure
               ds.eval_toggle_call):
        src_fn = inspect.getsource(fn)
        if "llm.SONNET" not in src_fn or "model=llm.HAIKU" in src_fn:
            failures.append(f"{fn.__name__} is not pinned to Sonnet")
    # Haiku holds no preferred call sites anywhere any more — it is the
    # fallback ladder's last rung only
    import subprocess
    hits = subprocess.run(
        ["grep", "-rn", "model=llm.HAIKU", "cli.py", "core", "deck",
         "skills/overview-deck/scripts"],
        capture_output=True, text=True, cwd=REPO).stdout.strip()
    if hits:
        failures.append(f"Haiku call sites reappeared: {hits}")

    # ---- spec + call-site ordering ----
    spec = yaml.safe_load((REPO / "tasks" / "ov80i.yaml").read_text())
    tmpl = next(s for s in spec["steps"] if s["id"] == "template_image")
    if tmpl.get("describe_part") is not True or not tmpl.get("download_main_image"):
        failures.append("template step lost describe_part/download_main_image")
    src = inspect.getsource(cli.main)
    dl = src.index('step.get("download_main_image")')
    desc_call = src.index("describe_part_from_image(")
    if not dl < desc_call:
        failures.append("describe_part must run off the downloaded main image")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL PART-ANCHOR CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
