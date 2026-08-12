"""Node-RED's palette and sidebar are closed before the flow capture.

Both panel controls are TOGGLES (ctrl/cmd-p, ctrl/cmd-space), so firing one
blind can OPEN a closed panel — the visibility check before each toggle is
the guarantee tested here. Closing goes through the editor's own action API
first (no keyboard focus needed), the shortcut as fallback, and the whole
routine is cosmetic: no failure may stop the capture.

Run: uv run python tests/test_node_red_panels.py
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

import cli  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class _FakeEl:
    def __init__(self, frame, sel):
        self._frame, self._sel = frame, sel

    def is_visible(self):
        return self._frame.open[self._sel]


class _FakeFrame:
    """Node-RED editor frame: panels toggle via RED.actions or key press."""

    def __init__(self, palette_open, sidebar_open, red_ok=True, workspace=True):
        self.open = {"#red-ui-palette": palette_open, "#red-ui-sidebar": sidebar_open}
        self.red_ok = red_ok
        self.workspace = workspace
        self.invoked: list = []
        self.pressed: list = []

    def query_selector(self, sel):
        if sel in ("#red-ui-workspace", "#workspace"):
            return object() if self.workspace else None
        if sel in self.open:
            return _FakeEl(self, sel)
        return None

    def _toggle(self, key):
        self.open[key] = not self.open[key]

    def evaluate(self, js):
        if not self.red_ok:
            raise RuntimeError("RED is not defined")
        self.invoked.append(js)
        if "toggle-palette" in js:
            self._toggle("#red-ui-palette")
        elif "toggle-sidebar" in js:
            self._toggle("#red-ui-sidebar")

    def press(self, sel, combo):
        self.pressed.append(combo)
        if combo.endswith("+p"):
            self._toggle("#red-ui-palette")
        else:
            self._toggle("#red-ui-sidebar")


class _FakePage:
    def __init__(self, frames):
        self.main_frame = object()
        self.frames = [self.main_frame] + frames
        self.waits: list = []

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


class _FakeBrowser:
    def __init__(self, frames):
        self.page = _FakePage(frames)


def main() -> int:
    failures = []

    # --- both panels open: both closed, via the action API, no key presses ---
    f = _FakeFrame(palette_open=True, sidebar_open=True)
    cli.close_node_red_panels(_FakeBrowser([f]))
    if f.open["#red-ui-palette"] or f.open["#red-ui-sidebar"]:
        failures.append(f"panels left open: {f.open}")
    if len(f.invoked) != 2 or f.pressed:
        failures.append(f"expected 2 action invokes, no presses: {f.invoked} {f.pressed}")

    # --- already closed: toggles MUST NOT fire (they would re-open) ---
    f = _FakeFrame(palette_open=False, sidebar_open=False)
    cli.close_node_red_panels(_FakeBrowser([f]))
    if f.invoked or f.pressed:
        failures.append(f"toggled a closed panel: {f.invoked} {f.pressed}")
    if f.open["#red-ui-palette"] or f.open["#red-ui-sidebar"]:
        failures.append(f"closed panels re-opened: {f.open}")

    # --- one open, one closed: only the open one is touched ---
    f = _FakeFrame(palette_open=True, sidebar_open=False)
    cli.close_node_red_panels(_FakeBrowser([f]))
    if f.open["#red-ui-palette"] or f.open["#red-ui-sidebar"]:
        failures.append(f"mixed state mishandled: {f.open}")
    if len(f.invoked) != 1:
        failures.append(f"expected exactly one toggle: {f.invoked}")

    # --- RED not exposed: falls back to the keyboard shortcut ---
    f = _FakeFrame(palette_open=True, sidebar_open=True, red_ok=False)
    cli.close_node_red_panels(_FakeBrowser([f]))
    if f.open["#red-ui-palette"] or f.open["#red-ui-sidebar"]:
        failures.append(f"fallback left panels open: {f.open}")
    if len(f.pressed) != 2 or f.invoked:
        failures.append(f"expected 2 key presses on fallback: {f.pressed}")

    # --- no node-red frame at all: warns, never raises ---
    try:
        cli.close_node_red_panels(_FakeBrowser([]))
        cli.close_node_red_panels(
            _FakeBrowser([_FakeFrame(True, True, workspace=False)])
        )
    except Exception as e:
        failures.append(f"missing frame raised instead of degrading: {e!r}")

    # --- wiring: runs in the capture branch BEFORE the screenshot ---
    src = inspect.getsource(cli.main)
    if "close_node_red_panels" not in src:
        failures.append("capture branch never closes node-red panels")
    elif src.index("close_node_red_panels") > src.index('name = f"{step[\'screenshot\']}.png"'):
        failures.append("panels must close BEFORE the capture, not after")

    # --- and the node-red view step opts in ---
    spec = yaml.safe_load((REPO / "tasks" / "ov80i.yaml").read_text())
    step = next(s for s in spec["steps"] if s["id"] == "io_node_red_view")
    if not step.get("close_node_red_panels"):
        failures.append("io_node_red_view does not close the node-red panels")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL NODE-RED-PANEL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
