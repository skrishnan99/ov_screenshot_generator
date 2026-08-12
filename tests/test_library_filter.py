"""The Library capture is filtered to the run's recipe before screenshot
and main-image download.

Unfiltered, the Library grid shows every recipe's captures newest-first, so
both the deliverable screenshot and the downloaded main image can belong to
another recipe entirely. The filter is applied fresh every run — the page
does not persist it. What this suite pins (all proven against a live
camera first):

- the click target is the stable #recipe input, never the placeholder text
  (the placeholder intercepts pointer events and the click times out),
- the option click is an EXACT text match — a prefix sibling
  ("X - pin inspection" vs "X - second pin inspection") is never accepted;
  no exact match closes the dropdown and captures unfiltered,
- zero filtered results is the recipe's true state: captured, with a note,
- every failure path (no #recipe, no Search button, page crash) warns and
  proceeds unfiltered — the hook never fails the step,
- the spec activates it on the library step and the hook runs before the
  vision wait and the download dispatch.

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
    """Library page: #recipe combobox, options, a Search button, a body
    whose text flips to the filtered state once Search has been clicked."""

    def __init__(self, has_recipe_box=True, options=(RECIPE, SIBLING),
                 has_search=True, count_before="2,243", count_after="9",
                 broken=False):
        self.has_recipe_box = has_recipe_box
        self.options = list(options)
        self.has_search = has_search
        self.count_before, self.count_after = count_before, count_after
        self.broken = broken
        self.searched = False
        self.selected = None
        self.clicked: list = []
        self.filled: list = []
        self.pressed: list = []
        self.keyboard = _FakeKeyboard(self)

    def query_selector(self, sel):
        if self.broken:
            raise RuntimeError("page crashed")
        if sel == "#recipe" and self.has_recipe_box:
            return _FakeEl(self, kind="recipe-box")
        return None

    def query_selector_all(self, sel):
        if sel == ".ant-select-item-option":
            return [_FakeEl(self, text=o, kind="option") for o in self.options]
        if sel == "button":
            btns = [_FakeEl(self, text="Reset", kind="button")]
            if self.has_search:
                btns.append(_FakeEl(self, text="Search", kind="search"))
            return btns
        return []

    def evaluate(self, js):
        if self.searched:
            if self.count_after == "0":
                return "Library\n0 Total Captures\nSort By"
            return (f"Library\n{self.count_after} Total Captures\n"
                    f"#1342 PASS {self.selected or ''}")
        return f"Library\n{self.count_before} Total Captures\n#2607 Traton Bushing Wear"

    def wait_for_timeout(self, ms):
        pass


class _FakeBrowser:
    def __init__(self, page):
        self.page = page


def main() -> int:
    failures = []

    # ---- happy path: #recipe -> fill -> EXACT option -> Search ----
    page = _FakePage()
    cli.filter_library_by_recipe(_FakeBrowser(page), RECIPE)
    if ("recipe-box", RECIPE) not in page.filled:
        failures.append(f"recipe name not typed into #recipe: {page.filled}")
    if page.selected != RECIPE:
        failures.append(f"selected option: {page.selected!r}")
    if not page.searched:
        failures.append("Search was never clicked")

    # ---- exact match: the prefix sibling never wins ----
    page = _FakePage(options=(SIBLING,))
    cli.filter_library_by_recipe(_FakeBrowser(page), RECIPE)
    if page.selected is not None:
        failures.append(f"prefix sibling accepted: {page.selected!r}")
    if page.searched:
        failures.append("searched despite no exact recipe option")
    if "Escape" not in page.pressed:
        failures.append("dropdown left open after a failed option match")

    # ---- zero filtered results: still a success (true state) ----
    page = _FakePage(count_after="0")
    cli.filter_library_by_recipe(_FakeBrowser(page), RECIPE)
    if not page.searched:
        failures.append("zero-results path never searched")

    # ---- failure paths never raise, never search blind ----
    for label, p in (
        ("no #recipe box", _FakePage(has_recipe_box=False)),
        ("no Search button", _FakePage(has_search=False)),
        ("page crash", _FakePage(broken=True)),
    ):
        try:
            cli.filter_library_by_recipe(_FakeBrowser(p), RECIPE)
        except Exception as e:
            failures.append(f"{label}: raised {e}")
    page = _FakePage()
    try:
        cli.filter_library_by_recipe(_FakeBrowser(page), "")
    except Exception as e:
        failures.append(f"empty recipe name raised: {e}")
    if page.searched or page.filled:
        failures.append("empty recipe name still drove the filter")

    # ---- the spec activates it on the library step ----
    spec = yaml.safe_load((REPO / "tasks" / "ov80i.yaml").read_text())
    lib = next(s for s in spec["steps"] if s["id"] == "library")
    if lib.get("filter_library_recipe") is not True:
        failures.append("library step lost filter_library_recipe: true")
    if "filter by recipe" not in lib["goal"].lower():
        failures.append("library goal lost the filter instruction")
    if "exact" not in lib["goal"].lower():
        failures.append("library goal lost the exact-match warning")

    # ---- ordering: the hook precedes the vision wait / capture dispatch ----
    src = inspect.getsource(cli.main)
    call = src.index("filter_library_by_recipe(browser")
    wait = src.index('step.get("wait_image_loaded")')
    if not call < wait:
        failures.append("filter hook must run before the image wait")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL LIBRARY-FILTER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
