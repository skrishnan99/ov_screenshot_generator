"""The Library capture is filtered to the run's recipe — reliably.

Unfiltered, the Library grid shows every recipe's captures newest-first.
A field run proved three weaknesses in sequence: a one-shot #recipe query
raced the SPA (blank at 0s, rendered at 14s), the filter silently
no-op'd, and ANOTHER recipe's captures shipped as the library pair with
nothing downstream told. What this suite pins:

- the hook WAITS for the filter panel (a page rendering seconds late is
  filtered normally), bounded and retried once before degrading,
- the click target is the stable #recipe input; the option click is an
  EXACT text match — a prefix sibling is never accepted,
- a first attempt degrading (e.g. options not yet populated) is retried
  and can succeed on the second pass,
- zero filtered results is the recipe's true state: success, with a note,
- the outcome is VERIFIED against the cards' recipe names: our cards ->
  "ok", empty grid -> "zero", a foreign recipe's cards -> "foreign" with
  the name recorded and loudly warned — never silent contamination,
- the hook returns a record and the step stores it in the manifest
  (step_record["library_filter"]),
- every failure path warns and proceeds — the hook never raises,
- the spec activates it on the library step; the hook precedes the
  vision wait and the main-image download.

Run: uv run python tests/test_library_filter.py
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

import cli  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
RECIPE = "sanmina SJ - second pin inspection"
SIBLING = "sanmina SJ - pin inspection"
FOREIGN = "Traton Bushing Wear"


def _cards(name, n=3, start=2600):
    return "\n".join(f"#{start + i}\nPASS\n{name}\n2026-08-03" for i in range(n))


class _FakeEl:
    def __init__(self, page, text="", visible=True, kind=""):
        self._page, self.text, self.visible, self.kind = page, text, visible, kind

    def is_visible(self):
        return self.visible

    def inner_text(self):
        return self.text

    def click(self):
        self._page.clicked.append(self.text or self.kind)
        if self.kind == "option":
            self._page.selected = self.text
        if self.kind == "search":
            self._page.searched = True

    def fill(self, value):
        self._page.filled.append((self.kind, value))


class _FakeKeyboard:
    def __init__(self, page):
        self._page = page

    def press(self, combo):
        self._page.pressed.append(combo)


class _FakePage:
    """Library page with a clock: the panel exists only once the fake
    clock (advanced by wait_for_timeout) reaches ready_at_ms. Body text is
    blank before readiness, foreign-card grid before search, and the
    filtered grid (or zero state) after."""

    def __init__(self, has_recipe_box=True, options=(RECIPE, SIBLING),
                 has_search=True, count_before="2,243", count_after="9",
                 ready_at_ms=0, options_ready_at_ms=0, broken=False):
        self.has_recipe_box = has_recipe_box
        self.options = list(options)
        self.has_search = has_search
        self.count_before, self.count_after = count_before, count_after
        self.ready_at_ms = ready_at_ms
        self.options_ready_at_ms = options_ready_at_ms
        self.broken = broken
        self.clock_ms = 0
        self.searched = False
        self.selected = None
        self.clicked: list = []
        self.filled: list = []
        self.pressed: list = []
        self.keyboard = _FakeKeyboard(self)

    @property
    def ready(self):
        return self.clock_ms >= self.ready_at_ms

    def query_selector(self, sel):
        if self.broken:
            raise RuntimeError("page crashed")
        if sel == "#recipe" and self.has_recipe_box and self.ready:
            return _FakeEl(self, kind="recipe-box")
        return None

    def query_selector_all(self, sel):
        if sel == ".ant-select-item-option":
            if self.clock_ms < self.options_ready_at_ms:
                return []
            return [_FakeEl(self, text=o, kind="option") for o in self.options]
        if sel == "button":
            btns = [_FakeEl(self, text="Reset", kind="button")]
            if self.has_search:
                btns.append(_FakeEl(self, text="Search", kind="search"))
            return btns
        return []

    def evaluate(self, js):
        if not self.ready:
            return ""
        if self.searched:
            if self.count_after == "0":
                return "Library\n0 Total Captures\nSort By"
            return (f"Library\n{self.count_after} Total Captures\n"
                    + _cards(self.selected or ""))
        return (f"Library\n{self.count_before} Total Captures\n"
                + _cards(FOREIGN))

    def wait_for_timeout(self, ms):
        self.clock_ms += ms


class _FakeBrowser:
    def __init__(self, page):
        self.page = page


def _run(page, recipe=RECIPE):
    saved = (cli.LIBRARY_READY_WAIT_S, cli.LIBRARY_READY_RETRY_WAIT_S,
             cli.LIBRARY_FILTER_WAIT_S)
    try:
        # tiny REAL deadlines: the fake clock advances instantly, so the
        # polls spin through their iterations in microseconds
        cli.LIBRARY_READY_WAIT_S = 0.4
        cli.LIBRARY_READY_RETRY_WAIT_S = 0.2
        cli.LIBRARY_FILTER_WAIT_S = 0.4
        return cli.filter_library_by_recipe(_FakeBrowser(page), recipe)
    finally:
        (cli.LIBRARY_READY_WAIT_S, cli.LIBRARY_READY_RETRY_WAIT_S,
         cli.LIBRARY_FILTER_WAIT_S) = saved


def main() -> int:
    failures = []

    # ---- happy path: filter, search, verified against our cards ----
    page = _FakePage()
    rec = _run(page)
    if not rec["filtered"] or rec["verified"] != "ok":
        failures.append(f"happy path: {rec}")
    if ("recipe-box", RECIPE) not in page.filled or page.selected != RECIPE:
        failures.append(f"filter mechanics: {page.filled} {page.selected!r}")

    # ---- the panel rendering LATE is waited for, then filtered (the
    # field failure: blank at 0s, rendered at 14s) ----
    page = _FakePage(ready_at_ms=14000)
    rec = _run(page)
    if not rec["filtered"] or rec["verified"] != "ok":
        failures.append(f"late-render not absorbed: {rec}")
    if page.clock_ms < 14000:
        failures.append("readiness wait never advanced to the render point")

    # ---- degraded first attempt (options not yet populated) recovers on
    # the retry ----
    page = _FakePage(options_ready_at_ms=7000)
    rec = _run(page)
    if not rec["filtered"] or len(rec["attempts"]) != 2 \
            or "no-recipe-option" not in rec["attempts"][0]:
        failures.append(f"retry recovery: {rec}")

    # ---- exact match: the prefix sibling never wins ----
    page = _FakePage(options=(SIBLING,))
    rec = _run(page)
    if page.selected is not None or page.searched:
        failures.append(f"prefix sibling accepted: {page.selected!r}")
    if "Escape" not in page.pressed:
        failures.append("dropdown left open after a failed option match")
    # the unfiltered grid shows a foreign recipe — verified + recorded
    if rec["verified"] != "foreign" or FOREIGN not in rec.get("note", ""):
        failures.append(f"foreign contamination not flagged: {rec}")

    # ---- zero filtered results: success, verified zero ----
    page = _FakePage(count_after="0")
    rec = _run(page)
    if not rec["filtered"] or rec["verified"] != "zero":
        failures.append(f"zero-results: {rec}")

    # ---- failure paths never raise; records carry the attempts ----
    for label, page in (
        ("no #recipe box", _FakePage(has_recipe_box=False)),
        ("no Search button", _FakePage(has_search=False)),
        ("page crash", _FakePage(broken=True)),
    ):
        try:
            rec = _run(page)
            if rec["filtered"]:
                failures.append(f"{label}: claimed filtered")
        except Exception as e:
            failures.append(f"{label}: raised {e}")
    rec = _run(_FakePage(), recipe="")
    if rec["filtered"] or rec.get("note") != "no recipe name":
        failures.append(f"empty recipe name: {rec}")

    # ---- the record lands in the manifest ----
    src = inspect.getsource(cli.main)
    if 'step_record["library_filter"] = filter_library_by_recipe' not in src:
        failures.append("filter record not stored in the step record")

    # ---- spec + ordering (unchanged contracts) ----
    spec = yaml.safe_load((REPO / "tasks" / "ov80i.yaml").read_text())
    lib = next(s for s in spec["steps"] if s["id"] == "library")
    if lib.get("filter_library_recipe") is not True:
        failures.append("library step lost filter_library_recipe: true")
    filt = src.index("filter_library_by_recipe(")
    wait = src.index('step.get("wait_image_loaded")')
    dl = src.index('step.get("download_main_image")')
    if not (filt < wait and filt < dl):
        failures.append("filter must precede the wait and the download")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL LIBRARY-FILTER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
