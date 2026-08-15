"""The View All ROIs per-model captures are staged deterministically.

That step was the flow's most turn-hungry agent task and hit the turn
budget often: close the previous model's modal, fight the portal-rendered
Ant model selector (which only exists in the capture-review state, and
whose option names can be strict prefixes of each other), reopen the
modal. prepare_model_view does the mechanical parts in code before each
per-model agent run. What this suite pins:

- an already-correct selector is confirmed without touching anything,
- a wrong selector is opened and the option clicked by EXACT equality —
  "Model" never matches "Model 3" and vice versa,
- an open modal is Escaped before anything else,
- no selector on the live view -> Previous is clicked to enter the
  review state, then the selector is driven,
- every failure path (no selector anywhere, option missing, page crash)
  returns False and never raises — the agent's full fallback remains,
- the spec: both view_all_rois steps carry prepare_model_selector: true
  and max_model_calls: 60, and their goals lead with the pre-staged path,
- capture_block_per_model invokes the hook before the per-model agent.

Run: uv run python tests/test_model_view_prep.py
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

import cli  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class _Rig:
    def __init__(self, current="Model", options=("Model", "Model 3"),
                 selector_present=True, selector_after_previous=False,
                 modal_open=False, stubborn_modal=False, crash=False):
        self.current = current
        self.options = list(options)
        self.selector_present = selector_present
        self.selector_after_previous = selector_after_previous
        self.modal_open = modal_open
        self.stubborn_modal = stubborn_modal
        self.crash = crash
        self.escapes = 0
        self.prev_clicks = 0
        self.opened = 0
        self.clicked_option = None

    # ---- patched helpers ----
    def escape_dialogs(self, browser):
        if self.stubborn_modal:
            self.escapes += 1
            return False
        if self.modal_open:
            self.escapes += 1
            self.modal_open = False
        return True

    def selector_state(self, browser, block_type):
        if self.crash:
            raise RuntimeError("page crashed")
        if self.selector_present:
            return "#sel", self.current
        return None, None

    def nav_click(self, browser, label):
        if label == "Previous":
            self.prev_clicks += 1
            if self.selector_after_previous:
                self.selector_present = True
            return True
        return False

    def click_option(self, browser, name):
        if name in self.options:
            self.clicked_option = name
            self.current = name
            return True
        return False


class _FakeKeyboard:
    def press(self, k):
        pass


class _FakePage:
    keyboard = _FakeKeyboard()

    def click(self, sel, **kw):
        pass

    def wait_for_timeout(self, ms):
        pass


class _FakeBrowser:
    page = _FakePage()


def _run(rig, block_type="segmentation", model="Model 3"):
    saved = (cli._escape_dialogs, cli._model_selector_state,
             cli._click_nav_button, cli._click_model_option)
    try:
        cli._escape_dialogs = rig.escape_dialogs
        cli._model_selector_state = rig.selector_state
        cli._click_nav_button = rig.nav_click
        cli._click_model_option = rig.click_option
        return cli.prepare_model_view(_FakeBrowser(), block_type, model)
    finally:
        (cli._escape_dialogs, cli._model_selector_state,
         cli._click_nav_button, cli._click_model_option) = saved


def main() -> int:
    failures = []

    # ---- already correct: confirmed, nothing touched ----
    rig = _Rig(current="Model 3")
    if _run(rig) is not True or rig.clicked_option is not None:
        failures.append("already-correct selector was disturbed")

    # ---- wrong selection: option clicked by exact equality ----
    rig = _Rig(current="Model")
    if _run(rig, model="Model 3") is not True or rig.clicked_option != "Model 3":
        failures.append(f"selector switch failed: {rig.clicked_option}")
    # the prefix trap in reverse: selecting "Model" while "Model 3" exists
    rig = _Rig(current="Model 3")
    if _run(rig, model="Model") is not True or rig.clicked_option != "Model":
        failures.append(f"prefix-safe selection failed: {rig.clicked_option}")

    # ---- open modal is escaped first ----
    rig = _Rig(current="Model 3", modal_open=True)
    _run(rig)
    if rig.escapes != 1:
        failures.append("open modal was not escaped")

    # ---- live view: Previous enters the review state ----
    rig = _Rig(selector_present=False, selector_after_previous=True,
               current="Model 3")
    if _run(rig) is not True or rig.prev_clicks != 1:
        failures.append(f"live-view path: prev={rig.prev_clicks}")

    # ---- no selector anywhere / missing option / crash: False, no raise ----
    rig = _Rig(selector_present=False, selector_after_previous=False)
    if _run(rig) is not False:
        failures.append("missing selector did not return False")
    # a modal that will not close: bail fast, never fight the selector
    rig = _Rig(current="Model", stubborn_modal=True)
    if _run(rig, model="Model 3") is not False or rig.clicked_option is not None:
        failures.append("stubborn modal did not bail before the selector")
    rig = _Rig(current="Model", options=("Model",))
    if _run(rig, model="Model 3") is not False:
        failures.append("missing option did not return False")
    try:
        if _run(_Rig(crash=True)) is not False:
            failures.append("crash did not return False")
    except Exception as e:
        failures.append(f"crash escaped the hook: {e}")

    # ---- spec: flags, budgets, and goals lead with the staged path ----
    spec = yaml.safe_load((REPO / "tasks" / "ov80i.yaml").read_text())
    for sid in ("view_all_rois_segmentation", "view_all_rois_classification"):
        s = next(x for x in spec["steps"] if x["id"] == sid)
        if s.get("prepare_model_selector") is not True:
            failures.append(f"{sid} lost prepare_model_selector: true")
        if s.get("max_model_calls") != 60:
            failures.append(f"{sid} max_model_calls: {s.get('max_model_calls')}")
        goal = s.get("per_model_goal", "")
        if "deterministic helper" not in goal or "match the whole name" not in goal:
            failures.append(f"{sid} goal lost the staged-path lead / prefix warning")

    # ---- the hook runs before the per-model agent ----
    src = inspect.getsource(cli.capture_block_per_model)
    if not src.index("prepare_model_view(") < src.index("run_step("):
        failures.append("hook must run before the per-model agent")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL MODEL-VIEW-PREP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
