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

    # ---- anchored prompts name THIS part; unanchored fall back soft ----
    block_anchored = cli._block_capture_prompt("segmentation", "R", part_desc=DESC)
    if DESC not in block_anchored or "THIS part" not in block_anchored:
        failures.append("block prompt not anchored to the part description")
    if "different angle, zoom, exposure or lighting" not in block_anchored:
        failures.append("block anchor lost the variance allowance")
    if "any manufactured part" in block_anchored.lower() \
            or "still counts as a product image" in block_anchored:
        failures.append("the any-part softener survived into an anchored prompt")
    block_plain = cli._block_capture_prompt("segmentation", "R")
    if "inspection recipe 'R'" not in block_plain:
        failures.append("unanchored block prompt lost the soft recipe line")

    anchor = cli._anchor_line("R", DESC)
    for judge_prompt in (
        cli.LIBRARY_VIEWER_PROMPT.format(
            recipe_line=anchor, product_criterion=cc.PRODUCT_CRITERION,
            overlay_criterion=cc.INSPECTION_OVERLAY_CRITERION),
        cli.LIBRARY_THUMBS_PROMPT.format(
            recipe_line=anchor, ids="#1", product_criterion=cc.PRODUCT_CRITERION),
    ):
        if DESC not in judge_prompt:
            failures.append("a library prompt did not carry the anchor")

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
