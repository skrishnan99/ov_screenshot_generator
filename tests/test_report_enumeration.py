"""Training-report capture must not chase a model that does not exist.

The Train Models table stacks a model's name above its type in one cell:

    Model S
    segmentation

`list_training_reports` read that and returned "segmentation" — the TYPE — as
the model name. Nothing checked it, so the agent hunted a nonexistent model
for 50 turns (against a nominal budget of 30), `capture_reports` raised, and a
13-step run died at step 10, losing model settings, Node-RED and the library.

Two guards, both tested here:
  1. enumeration is filtered against meta["models"], which inspection_rois
     already populated correctly — anything not in the roster is a misread,
     and the roster's spelling wins.
  2. a report that will not open warns and continues. One model's report
     screenshot is worth less than the three steps after it, and a report
     that cannot be opened is usually a model that has none.

Run: uv run python tests/test_report_enumeration.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli  # noqa: E402

ROSTER = [
    {"name": "Model S", "type": "segmentation", "slug": "model-s"},
    {"name": "Horn Quality", "type": "classification", "slug": "horn-quality"},
]


class FakeOut:
    def __init__(self, tmp):
        self.run_dir = tmp

    def save(self, name, content, **kw):
        p = self.run_dir / name
        p.write_bytes(content if isinstance(content, bytes) else content.encode())
        return p

    def rel(self, p):
        return str(Path(p).relative_to(self.run_dir))


class FakeBrowser:
    def screenshot_bytes(self, full_page=False):
        return b"png"


def run_capture(monkey, enumerated, open_ok=True, tmp=None):
    """Drive capture_reports with a stubbed page. Returns (visited, record)."""
    visited = []

    monkey(cli, "poll_table_loaded", lambda b, **k: (True, "loaded"))
    monkey(cli, "_stable_snapshot", lambda b: "<snapshot>")
    monkey(cli, "list_training_reports", lambda s: list(enumerated))
    monkey(cli, "_click_scoped", lambda b, t, n: False)  # force the agent path
    monkey(cli, "poll_image_loaded", lambda b, **k: (True, "loaded"))
    monkey(cli, "_close_report", lambda b: None)

    class R:
        def __init__(self, ok):
            self.status = "success" if ok else "failure"
            self.evidence = "no such model on the page"

    def fake_run_step(browser, goal, post, **kw):
        visited.append(goal)
        return R(open_ok)

    monkey(cli, "run_step", fake_run_step)

    record = {}
    cli.capture_reports(
        FakeBrowser(),
        {"id": "training_reports", "screenshot": "09_train"},
        FakeOut(tmp), record, [], {"variant": "ov80i"},
        {"models": [dict(m) for m in ROSTER]},
    )
    return visited, record


def main() -> int:
    import tempfile

    failures = []
    saved = {}

    def monkey(mod, name, val):
        saved.setdefault((mod, name), getattr(mod, name, None))
        setattr(mod, name, val)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        try:
            # --- the real bug: the TYPE enumerated as a name ---
            visited, rec = run_capture(
                monkey,
                [{"name": "segmentation", "type": "Segmentation",
                  "entry_text": "View"}],
                tmp=tmp,
            )
            if visited:
                failures.append(
                    f"chased a model absent from the roster: {visited[0][:60]!r}"
                )
            if rec.get("report_models"):
                failures.append(f"recorded a phantom model: {rec['report_models']}")

            # --- a real model is kept, and normalised to the roster spelling ---
            visited, rec = run_capture(
                monkey,
                [{"name": "Model S", "type": "Segmentation", "entry_text": "View"}],
                tmp=tmp,
            )
            if not visited:
                failures.append("a model that IS in the roster was discarded")
            if rec.get("report_models") != ["Model S (segmentation)"]:
                failures.append(f"roster spelling not applied: {rec.get('report_models')}")

            # --- a differently-formatted but real name still matches ---
            visited, _ = run_capture(
                monkey,
                [{"name": "Model S Segmentation", "type": "Segmentation",
                  "entry_text": "View"}],
                tmp=tmp,
            )
            if not visited:
                failures.append("a real model with a decorated name was discarded")

            # --- an unopenable report must NOT raise ---
            try:
                visited, _ = run_capture(
                    monkey,
                    [{"name": "Model S", "type": "Segmentation", "entry_text": "View"}],
                    open_ok=False, tmp=tmp,
                )
            except Exception as e:
                failures.append(f"an unopenable report still kills the run: {e!r}")

            # --- with no roster at all, fall back to trusting enumeration ---
            monkey(cli, "poll_table_loaded", lambda b, **k: (True, "loaded"))
            monkey(cli, "list_training_reports",
                   lambda s: [{"name": "Whatever", "type": "x", "entry_text": "View"}])
            rec2 = {}
            cli.capture_reports(
                FakeBrowser(), {"id": "training_reports", "screenshot": "09"},
                FakeOut(tmp), rec2, [], {}, {},
            )
            if not rec2.get("report_models"):
                failures.append("with no roster, enumeration should be trusted as-is")
        finally:
            for (mod, name), val in saved.items():
                if val is not None:
                    setattr(mod, name, val)

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL REPORT-ENUMERATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
