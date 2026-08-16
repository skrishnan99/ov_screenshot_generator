"""Basic-Mode IO pages are first-class: recognized, harvested, analyzed.

The IO Logic tab has two layouts — Advanced (the embedded Node-RED flow
editor) and Basic (the "Pass/Fail & IO Logic" rule builders). The pipeline
assumed Advanced everywhere: the view step's agent hunted for a canvas
that doesn't exist, and the export step's hard expect_download FATALLY
failed the run after burning its turn budget. What this suite pins:

- _is_basic_io_page: text-shaped detection (headline + rules markers,
  no Node-RED iframe); an Advanced page (iframe present) is never basic;
  a crash reads as not-basic (the step then runs normally),
- harvest_io_rules saves the page text VERBATIM as data/io_rules.txt,
  registers the asset, stamps meta["io_mode"], and never raises,
- describe_io_rules: same {"markdown","facts"} contract as
  describe_node_red, io_logic fact subject, rules text embedded in the
  prompt, refusal degrades to a stub — so the SAME analysis join writes
  node_red_description.md whatever the mode,
- the skip-gate sits in the step loop BEFORE trace replay/agent dispatch,
- the analysis phase falls back from node_red_flow.json to io_rules.txt,
- the spec: io_node_red carries skip_when_basic_io, the view step's goal
  and postcondition accept both layouts and forbid "Save & Deploy" and
  the mode toggle, and the deck's logic hole accepts both layouts.

Run: uv run python tests/test_basic_io.py
"""

import inspect
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

import cli  # noqa: E402
from core import describer, llm  # noqa: E402
from core.output import RunOutput  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

BASIC_TEXT = ("Traton Bushing Wear / IO Logic\nPass/Fail & IO Logic\n"
              "Configure rules to define the inspection pass/fail logic.\n"
              "Save & Deploy\nAdvanced Mode\nClassification Rules\n"
              "All ROIs match zero\nAdd rule\nSegmentation Rules\n"
              "Inspection Type 1\nDefect Pixel Count Lowest <= 50")


class _Frame:
    def __init__(self, workspace):
        self._ws = workspace

    def query_selector(self, sel):
        return object() if self._ws else None


class _Page:
    def __init__(self, text=BASIC_TEXT, nr_frame=False, crash=False):
        self.main_frame = _Frame(False)
        self.frames = [self.main_frame] + ([_Frame(True)] if nr_frame else [])
        self._text = text
        self._crash = crash

    def evaluate(self, js):
        if self._crash:
            raise RuntimeError("page gone")
        return self._text


class _Browser:
    def __init__(self, **kw):
        self.page = _Page(**kw)


class _StubBackend:
    def __init__(self, result=None, refuse=False):
        self.result, self.refuse = result, refuse

    def complete(self, prompt, schema=None, images=None, max_tokens=4000, model=None):
        self.prompt = prompt
        if self.refuse:
            raise llm.LLMRefusal("no")
        return self.result


def main() -> int:
    failures = []

    # ---- detection: text-shaped, iframe-aware, crash-safe ----
    if not cli._is_basic_io_page(_Browser()):
        failures.append("basic rules page not detected")
    if cli._is_basic_io_page(_Browser(nr_frame=True)):
        failures.append("advanced page (Node-RED iframe) misread as basic")
    if cli._is_basic_io_page(_Browser(text="All Recipes\nLibrary\nHaystack")):
        failures.append("unrelated page misread as basic")
    if cli._is_basic_io_page(_Browser(crash=True)):
        failures.append("crash did not read as not-basic")

    # ---- harvest: verbatim artifact + meta stamp, never raises ----
    with tempfile.TemporaryDirectory() as td:
        out = RunOutput(Path(td))
        meta: dict = {}
        cli.harvest_io_rules(_Browser(), out, meta)
        saved = Path(td) / "data" / "io_rules.txt"
        if not saved.exists() or saved.read_text() != BASIC_TEXT:
            failures.append("rules text not saved verbatim")
        if meta.get("io_mode") != "basic":
            failures.append(f"io_mode not stamped: {meta}")
        if not any(a.get("path", "").endswith("io_rules.txt") for a in out.assets):
            failures.append("io_rules.txt missing from the asset index")
        try:
            cli.harvest_io_rules(_Browser(crash=True), out, {})
        except Exception as e:
            failures.append(f"harvest raised: {e}")

    # ---- describe_io_rules: contract, prompt, refusal ----
    good = {"markdown": "All ROIs must classify as zero; defect pixels "
                        "bounded at 50.", "facts": [
                {"subject": "io_logic", "property": "classification_rule",
                 "value": "All ROIs match zero"}]}
    stub = _StubBackend(good)
    llm.set_backend(stub)
    try:
        got = describer.describe_io_rules(BASIC_TEXT, {"variant": "ov80i",
                                                       "recipe": "R"})
        if got != good:
            failures.append(f"describe_io_rules mangled the result: {got}")
        if "All ROIs match zero" not in stub.prompt or "BASIC" not in stub.prompt:
            failures.append("rules text / mode framing missing from the prompt")
        if "ignore the chrome" not in stub.prompt:
            failures.append("prompt lost the chrome carve-out")
        llm.set_backend(_StubBackend(refuse=True))
        got = describer.describe_io_rules(BASIC_TEXT, {})
        if "refused" not in got.get("markdown", "") or got.get("facts") != []:
            failures.append(f"refusal did not degrade: {got}")
    finally:
        llm.set_backend(None)

    # ---- the harvest captures INPUT values (innerText misses them; a
    # live harvest lost a "<= 50" threshold), and the analyst is told
    # where to find them ----
    if "VISIBLE INPUT VALUES" not in inspect.getsource(cli.harvest_io_rules):
        failures.append("harvest lost the input-value capture")
    if "VISIBLE INPUT VALUES" not in describer.IO_RULES_PROMPT:
        failures.append("analysis prompt lost the input-value guidance")

    # ---- wiring pins (source inspection) ----
    src = inspect.getsource(cli.main)
    gate = src.index('step.get("skip_when_basic_io")')
    replay = src.index("trace_store.replay(")
    if not gate < replay:
        failures.append("skip-gate must precede replay/agent dispatch")
    if "io_rules.txt" not in src or "describe_io_rules" not in src:
        failures.append("analysis phase lost the io_rules fallback source")

    # ---- spec pins ----
    spec = yaml.safe_load((REPO / "tasks" / "ov80i.yaml").read_text())
    exp = next(s for s in spec["steps"] if s["id"] == "io_node_red")
    if exp.get("skip_when_basic_io") is not True:
        failures.append("io_node_red lost skip_when_basic_io: true")
    if exp.get("expect_download") is not True:
        failures.append("io_node_red lost expect_download (Advanced mode)")
    view = next(s for s in spec["steps"] if s["id"] == "io_node_red_view")
    for phrase in ("BASIC", "Save & Deploy", "Advanced Mode"):
        if phrase not in view["goal"]:
            failures.append(f"view goal lost {phrase!r}")
    if "Basic Mode" not in view["postcondition"]:
        failures.append("view postcondition accepts only the Node-RED layout")

    deck = yaml.safe_load(
        (REPO / "skills/overview-deck/specs/default-deck.yaml").read_text())
    logic = next(s for s in deck["slides"] if s.get("id") == "logic")
    exp_text = logic["images"][0]["expects"]
    if "Node-RED" not in exp_text or "Basic Mode" not in exp_text:
        failures.append("deck logic hole does not accept both layouts")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL BASIC-IO CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
