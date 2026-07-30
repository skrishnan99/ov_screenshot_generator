"""The Train Models page must be polled until its table holds rows.

That page is a data grid with no imagery, so `poll_image_loaded` never applied
to it and nothing else waited. Its skeleton rows render instantly, so a real
run captured a page of grey placeholder bars, passed, and then enumerated
training reports against it — where the only readable text was the column
header, so "Model" became a model name. The agent spent its whole turn budget
hunting for that model's training report and the run died 9 steps in.

Two guards, both tested here: the declarative `wait_table_loaded` on the
capture step, and a poll inside capture_reports, which re-navigates to the
page itself and so cannot rely on the capture step's wait.

Run: uv run python tests/test_table_wait.py
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

import cli  # noqa: E402
from core import describer  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class _FakePage:
    def __init__(self, waits):
        self._waits = waits

    def title(self):
        return "Training AI Model"

    def wait_for_timeout(self, ms):
        self._waits.append(ms)


class _FakeBrowser:
    """Returns 'not loaded' for the first `blank` polls, then 'loaded'."""

    def __init__(self, blank: int):
        self.blank = blank
        self.calls = 0
        self.waits: list[int] = []
        self.page = _FakePage(self.waits)

    def screenshot_bytes(self, full_page=False):
        self.calls += 1
        return b"png"


def main() -> int:
    failures = []
    real_check = describer.check_table_loaded

    def stub(blank):
        state = {"n": 0}

        def _check(png, hint=""):
            state["n"] += 1
            if state["n"] <= blank:
                return {"loaded": False, "reason": "skeleton rows"}
            return {"loaded": True, "reason": "three models listed"}

        return _check

    try:
        # --- polls at the configured interval until the rows arrive ---
        describer.check_table_loaded = stub(2)
        b = _FakeBrowser(2)
        ok, msg = describer.poll_table_loaded(
            b, max_wait_s=60, interval_s=5, log=lambda *a: None
        )
        if not ok:
            failures.append(f"gave up on a table that loaded: {msg}")
        if b.calls != 3:
            failures.append(f"checked {b.calls} times, want 3 (2 blank + 1 loaded)")
        if b.waits != [5000, 5000]:
            failures.append(f"polled at {b.waits}ms, want 5000ms intervals")

        # --- gives up rather than hanging, and says so ---
        describer.check_table_loaded = stub(10_000)
        b = _FakeBrowser(10_000)
        ok, msg = describer.poll_table_loaded(
            b, max_wait_s=10, interval_s=5, log=lambda *a: None
        )
        if ok or "still NOT loaded" not in msg:
            failures.append(f"a never-loading table should time out, got: {ok} {msg}")

        # --- a check that raises must not kill the run ---
        def boom(png, hint=""):
            raise RuntimeError("vision down")

        describer.check_table_loaded = boom
        try:
            ok, msg = describer.poll_table_loaded(
                _FakeBrowser(0), max_wait_s=0, interval_s=5, log=lambda *a: None
            )
            if ok:
                failures.append("a failing check reported the table as loaded")
        except Exception as e:
            failures.append(f"poll_table_loaded raised instead of degrading: {e!r}")
    finally:
        describer.check_table_loaded = real_check

    # --- the prompt must not be fooled by headers, nor hang on empty tables ---
    p = describer.TABLE_LOADED_PROMPT.lower()
    if "header" not in p or "still loading" not in p:
        failures.append("prompt lost the header-row guard that caused the bug")
    if "nothing to wait for" not in p:
        failures.append("prompt would wait forever on a legitimately empty table")

    # --- wired on the capture step, at 5s ---
    spec = yaml.safe_load((REPO / "tasks" / "ov80i.yaml").read_text())
    step = next(s for s in spec["steps"] if s["id"] == "train_models")
    cfg = step.get("wait_table_loaded")
    if not isinstance(cfg, dict):
        failures.append("train_models has no wait_table_loaded block")
    elif cfg.get("interval_s") != 5:
        failures.append(f"train_models polls every {cfg.get('interval_s')}s, want 5")

    # --- and enumeration waits on its own, before reading the page ---
    src = inspect.getsource(cli.capture_reports)
    if "poll_table_loaded" not in src:
        failures.append("capture_reports enumerates without waiting for the table")
    elif src.index("poll_table_loaded") > src.index("list_training_reports"):
        failures.append("capture_reports waits AFTER enumerating")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL TABLE-WAIT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
