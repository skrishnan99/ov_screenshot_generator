"""The library capture is SEARCHED for, not taken on faith.

The filtered grid auto-selects the newest capture, with no guarantee its
viewer shows a real product or the AI inspection overlays (a real deck's
overview slide shipped a black raw/composite pair this way).
pick_library_capture batch-judges each page's thumbnails, clicks every
product-looking card (exhausting the page before moving on), judges each
viewer for product + overlay, and short-circuits on both. Ladder on
exhaustion: product-no-overlay > overlay-no-product > reset to page 1's
newest. Two caps: pages scanned (5) and total candidates clicked (10).

What this suite pins (DOM/vision access is behind patchable helpers, so
the search runs as a pure state machine):

- short-circuit on the first product+overlay viewer,
- every product-looking thumbnail on a page is clicked before paging,
- both caps: click cap counts across pages and stops the search; page cap
  stops paging even when next pages exist,
- best partial: tier 2 beats tier 3, first-seen ties, jump-back navigates
  to the winner's page and re-selects it,
- nothing qualifies -> page 1 reset + first (newest) capture selected,
- a page with no product-looking thumbnails costs zero clicks,
- thumbnail-judge failure and click failure both degrade, never raise,
- the spec activates the hook on the library step, after the filter,
- consistency by construction: the hook precedes the screenshot and the
  main-image download in the capture dispatch.

Run: uv run python tests/test_library_pick.py
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

import cli  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class _Rig:
    """Stateful stand-in: pages of product-thumb ids, per-capture viewer
    verdicts (product, overlay), a click/page log."""

    def __init__(self, page_thumbs, viewers, total_pages=None,
                 thumb_crash=False, click_fails=()):
        self.page_thumbs = page_thumbs          # page -> [capture ids]
        self.viewers = viewers                  # id -> (product, overlay)
        self.total_pages = total_pages or max(page_thumbs, default=1)
        self.thumb_crash = thumb_crash
        self.click_fails = set(click_fails)
        self.page = 1
        self.selected = None
        self.clicked: list = []
        self.gotos: list = []
        self.first_by_page = {p: (ids[0] if ids else 900 + p)
                              for p, ids in page_thumbs.items()}

    def product_thumbs(self, browser, recipe, part_desc=""):
        if self.thumb_crash:
            raise RuntimeError("vision down")
        return list(self.page_thumbs.get(self.page, []))

    def click(self, browser, cid):
        if cid in self.click_fails:
            return False
        self.clicked.append((self.page, cid))
        self.selected = cid
        return True

    def viewer(self, browser, recipe="", part_desc=""):
        product, overlay = self.viewers.get(self.selected, (False, False))
        return {"product_image": product, "overlay": overlay, "reason": "rig"}

    def next_page(self, browser):
        if self.page >= self.total_pages:
            return False
        self.page += 1
        return True

    def goto_page(self, browser, n):
        self.gotos.append(n)
        self.page = n
        return True

    def first_capture(self, browser):
        return self.first_by_page.get(self.page, 999)


def _run(rig, page_cap=cli.LIBRARY_PAGE_SCAN_CAP, click_cap=cli.LIBRARY_CLICK_CAP):
    saved = (cli._library_product_thumbs, cli._click_library_capture,
             cli.judge_library_viewer, cli._library_next_page,
             cli._library_goto_page, cli._library_first_capture)
    try:
        cli._library_product_thumbs = rig.product_thumbs
        cli._click_library_capture = rig.click
        cli.judge_library_viewer = rig.viewer
        cli._library_next_page = rig.next_page
        cli._library_goto_page = rig.goto_page
        cli._library_first_capture = rig.first_capture
        return cli.pick_library_capture(object(), "R", page_cap=page_cap,
                                        click_cap=click_cap)
    finally:
        (cli._library_product_thumbs, cli._click_library_capture,
         cli.judge_library_viewer, cli._library_next_page,
         cli._library_goto_page, cli._library_first_capture) = saved


def main() -> int:
    failures = []

    # ---- short-circuit on the first product+overlay viewer ----
    rig = _Rig({1: [10, 11, 12]}, {10: (True, False), 11: (True, True)})
    rec = _run(rig)
    if rec["chosen"] != {"page": 1, "id": 11} or rec["tier"] != 1:
        failures.append(f"short-circuit: {rec}")
    if rig.clicked != [(1, 10), (1, 11)]:
        failures.append(f"click order: {rig.clicked}")

    # ---- the page is exhausted before paging ----
    rig = _Rig({1: [10, 11], 2: [20]}, {20: (True, True)})
    rec = _run(rig)
    if [c for c in rig.clicked if c[0] == 1] != [(1, 10), (1, 11)]:
        failures.append(f"page 1 not exhausted first: {rig.clicked}")
    if rec["chosen"] != {"page": 2, "id": 20}:
        failures.append(f"page-2 winner missed: {rec}")

    # ---- click cap counts ACROSS pages and stops the search ----
    rig = _Rig({1: [1, 2, 3, 4, 5, 6], 2: [7, 8, 9, 10, 11, 12]},
               {12: (True, True)})
    rec = _run(rig, click_cap=10)
    # rec["clicked"] is the SEARCH log; the tier-4 reset's re-selection
    # afterwards is not a candidate and doesn't count against the cap
    if len(rec["clicked"]) != 10:
        failures.append(f"click cap: {len(rec['clicked'])} candidates judged")
    if rec["tier"] == 1:
        failures.append("found a winner past the click cap")

    # ---- page cap stops even when more pages exist ----
    rig = _Rig({p: [p * 100] for p in range(1, 9)}, {800: (True, True)},
               total_pages=8)
    rec = _run(rig, page_cap=5)
    if rec["pages_scanned"] != 5 or any(c[0] > 5 for c in rig.clicked):
        failures.append(f"page cap: scanned {rec['pages_scanned']}, {rig.clicked}")

    # ---- best partial: tier 2 beats tier 3; jump back to its page+card ----
    rig = _Rig({1: [10], 2: [20], 3: [30]},
               {10: (False, True), 20: (True, False), 30: (False, True)},
               total_pages=3)
    rec = _run(rig)
    if rec["chosen"] != {"page": 2, "id": 20} or rec["tier"] != 2:
        failures.append(f"best partial: {rec}")
    if rig.gotos != [2] or rig.clicked[-1] != (2, 20):
        failures.append(f"jump-back wrong: gotos={rig.gotos} clicked={rig.clicked}")

    # ---- nothing qualifies: page 1 reset, newest selected, tier 4 ----
    rig = _Rig({1: [10], 2: [20]}, {}, total_pages=2)
    rec = _run(rig)
    if rec["tier"] != 4 or rec["chosen"] is not None:
        failures.append(f"tier-4 fallback: {rec}")
    if rig.gotos[-1] != 1 or rig.selected != 10:
        failures.append(f"tier-4 reset: gotos={rig.gotos} selected={rig.selected}")

    # ---- a page with no product thumbs costs zero clicks ----
    rig = _Rig({1: [], 2: [20]}, {20: (True, True)}, total_pages=2)
    rec = _run(rig)
    if [c for c in rig.clicked if c[0] == 1]:
        failures.append(f"clicked on a product-less page: {rig.clicked}")
    if rec["tier"] != 1:
        failures.append(f"page-2 winner after empty page 1: {rec}")

    # ---- degradation: thumb-judge crash and click failures never raise ----
    try:
        rec = _run(_Rig({1: [10]}, {}, thumb_crash=True))
    except Exception as e:
        failures.append(f"thumb crash escaped: {e}")
    rig = _Rig({1: [10, 11]}, {11: (True, True)}, click_fails={10})
    rec = _run(rig)
    if rec["chosen"] != {"page": 1, "id": 11}:
        failures.append(f"click failure not skipped: {rec}")

    # ---- spec: activated on the library step, after the filter ----
    spec = yaml.safe_load((REPO / "tasks" / "ov80i.yaml").read_text())
    lib = next(s for s in spec["steps"] if s["id"] == "library")
    if lib.get("pick_library_capture") is not True:
        failures.append("library step lost pick_library_capture: true")
    if lib.get("filter_library_recipe") is not True:
        failures.append("library step lost the recipe filter")

    # ---- ordering: filter -> pick -> (wait/screenshot/download) ----
    src = inspect.getsource(cli.main)
    filt = src.index("filter_library_by_recipe(browser")
    pick = src.index("pick_library_capture(")
    wait = src.index('step.get("wait_image_loaded")')
    dl = src.index('step.get("download_main_image")')
    if not (filt < pick < wait and pick < dl):
        failures.append("pick must run after the filter, before wait/download")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL LIBRARY-PICK CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
