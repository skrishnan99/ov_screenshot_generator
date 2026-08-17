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
  main-image download in the capture dispatch,
- PAGE-TURN SETTLE GATE (a field pick judged a stale/half-painted grid
  after pagination and shipped a valid-looking wrong capture): every
  navigation waits until the card ids CHANGE, every thumbnail has
  PAINTED, and the view is stable across two reads — bounded; a page
  that never settles is judged as-is and noted in the pick record
  (nav_notes); the blind 2s sleep is gone from both nav helpers; page 1
  settles before the first thumbnail ranking; the state JS carries no
  raw newline inside quoted literals and stays in sync with the card
  walk,
- GRID VISIBILITY (the grid scrolls inside a viewport-derived inner
  panel, so full_page screenshots clipped the bottom ~11 of 20 cards —
  the ranker could only see half of every page): the grid screenshot
  grows the viewport to the deepest card (bounded), always restores it
  — even when the screenshot itself fails — never resizes when the grid
  already fits, and degrades to the plain shot on any probe failure.

Run: uv run python tests/test_library_pick.py
"""

import inspect
import re
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

    def product_thumbs(self, browser, recipe, part_desc="", top_n=3):
        if self.thumb_crash:
            raise RuntimeError("vision down")
        return list(self.page_thumbs.get(self.page, []))[:top_n]

    def click(self, browser, cid):
        if cid in self.click_fails:
            return False
        self.clicked.append((self.page, cid))
        self.selected = cid
        return True

    def viewer(self, browser, recipe="", part_desc=""):
        product, overlay = self.viewers.get(self.selected, (False, False))
        return {"product_image": product, "overlay": overlay, "reason": "rig"}

    def next_page(self, browser, notes=None):
        if self.page >= self.total_pages:
            return False
        self.page += 1
        return True

    def goto_page(self, browser, n, notes=None):
        self.gotos.append(n)
        self.page = n
        return True

    def first_capture(self, browser):
        return self.first_by_page.get(self.page, 999)


def _settled(browser, prev, max_wait_s=None, require_change=True):
    return True, []


def _run(rig, page_cap=cli.LIBRARY_PAGE_SCAN_CAP, click_cap=cli.LIBRARY_CLICK_CAP,
         settle=_settled):
    saved = (cli._library_product_thumbs, cli._click_library_capture,
             cli.judge_library_viewer, cli._library_next_page,
             cli._library_goto_page, cli._library_first_capture,
             cli._library_page_settled)
    try:
        cli._library_product_thumbs = rig.product_thumbs
        cli._click_library_capture = rig.click
        cli.judge_library_viewer = rig.viewer
        cli._library_next_page = rig.next_page
        cli._library_goto_page = rig.goto_page
        cli._library_first_capture = rig.first_capture
        cli._library_page_settled = settle
        return cli.pick_library_capture(object(), "R", page_cap=page_cap,
                                        click_cap=click_cap)
    finally:
        (cli._library_product_thumbs, cli._click_library_capture,
         cli.judge_library_viewer, cli._library_next_page,
         cli._library_goto_page, cli._library_first_capture,
         cli._library_page_settled) = saved


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

    # ---- click cap counts ACROSS pages and stops the search (per-page
    # candidates are capped at 3, so 5 pages offer 15 > the 10 budget) ----
    rig = _Rig({p: [p * 10 + i for i in range(6)] for p in range(1, 6)},
               {}, total_pages=5)
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

    # ---- the thumbnail RANKER: model order preserved (that IS the
    # ranking), hallucinated ids dropped, duplicates collapsed, top-N cap —
    # DOM-order re-sorting here once defeated the whole ranking design ----
    from PIL import Image
    import io as _io

    from core import llm as _llm

    class _ThumbPage:
        viewport_size = {"width": 1600, "height": 1000}

        def evaluate(self, js):
            if js is cli._LIBRARY_GRID_EXTENT_JS:
                return 900   # grid fits: no viewport growth on this path
            return ["101", "102", "103", "104", "105"]

    class _ThumbBrowser:
        page = _ThumbPage()

        def screenshot_bytes(self, full_page=True):
            buf = _io.BytesIO()
            Image.new("RGB", (32, 20), (9, 9, 9)).save(buf, format="PNG")
            return buf.getvalue()

    class _ThumbBackend:
        def complete(self, prompt, schema=None, images=None, max_tokens=4000,
                     model=None):
            self.prompt = prompt
            # ranked: 104 first, a hallucination, a duplicate, then more
            return {"reason": "r",
                    "product_captures": [104, 999, 104, 101, 105, 102]}

    _stub = _ThumbBackend()
    _llm.set_backend(_stub)
    try:
        got = cli._library_product_thumbs(_ThumbBrowser(), "R", "desc", top_n=3)
    finally:
        _llm.set_backend(None)
    if got != [104, 101, 105]:
        failures.append(f"ranker order/cap wrong: {got}")
    if "RANKED most likely first" not in getattr(_stub, "prompt", ""):
        failures.append("thumbnail prompt lost the ranking instruction")
    if "UP TO 3" not in " ".join(_stub.prompt.split()):
        failures.append("thumbnail prompt lost the per-page candidate cap")

    # ---- the page-turn settle gate: after a pagination click the grid
    # must show NEW ids, fully painted, stable across two reads ----
    class _TurnPage:
        """Card state is a function of a fake clock advanced by
        wait_for_timeout — the settle deadline is a tiny REAL one, so the
        poll spins through its iterations in microseconds."""

        def __init__(self, timeline):
            self.timeline = sorted(timeline)     # (at_ms, cards)
            self.clock_ms = 0

        def evaluate(self, js):
            cards = []
            for at, c in self.timeline:
                if self.clock_ms >= at:
                    cards = c
            return cards

        def wait_for_timeout(self, ms):
            self.clock_ms += ms

    class _TurnBrowser:
        def __init__(self, page):
            self.page = page

    def _settle_run(page, prev, require_change=True):
        saved = cli.LIBRARY_PAGE_TURN_WAIT_S
        try:
            cli.LIBRARY_PAGE_TURN_WAIT_S = 0.4
            return cli._library_page_settled(_TurnBrowser(page), prev,
                                             require_change=require_change)
        finally:
            cli.LIBRARY_PAGE_TURN_WAIT_S = saved

    old = [{"id": "10", "painted": True}]
    new_grey = [{"id": "20", "painted": False}, {"id": "21", "painted": True}]
    new_ok = [{"id": "20", "painted": True}, {"id": "21", "painted": True}]

    # the field failure shape: stale grid, then unpainted tiles, then done
    page = _TurnPage([(0, old), (3000, new_grey), (6000, new_ok)])
    ok, ids = _settle_run(page, [10])
    if not ok or ids != [20, 21]:
        failures.append(f"late paint not absorbed: {ok} {ids}")
    if page.clock_ms < 7000:
        failures.append(f"stability tick skipped: settled at {page.clock_ms}ms")

    # a tile that never paints: bounded timeout, ids still reported
    page = _TurnPage([(0, new_grey)])
    ok, ids = _settle_run(page, [10])
    if ok or ids != [20, 21]:
        failures.append(f"unpainted grid claimed settled: {ok} {ids}")

    # ids never change (the click no-op'd): bounded timeout ...
    page = _TurnPage([(0, new_ok)])
    ok, _ids = _settle_run(page, [20, 21])
    if ok:
        failures.append("unchanged grid claimed settled")
    # ... but identical content is FINE when no change is required (the
    # page-1 jump's destination can equal the origin)
    ok, _ids = _settle_run(_TurnPage([(0, new_ok)]), [20, 21],
                           require_change=False)
    if not ok:
        failures.append("require_change=False rejected a settled grid")

    # ---- nav helpers: settle-gated, degradation noted, disabled Next
    # costs no wait ----
    class _NavEl:
        def __init__(self, cls=""):
            self._cls = cls

        def get_attribute(self, name):
            return self._cls

        def click(self):
            pass

    class _NavPage:
        def __init__(self, next_cls="", has_page_one=False):
            self.next_cls = next_cls
            self.has_page_one = has_page_one

        def query_selector(self, sel):
            if sel == "li.ant-pagination-next":
                return _NavEl(self.next_cls)
            if sel == "li.ant-pagination-item-1" and self.has_page_one:
                return _NavEl()
            return None

        def evaluate(self, js):
            return []

    settle_calls = []

    def _spy_settle(verdict):
        def spy(browser, prev, max_wait_s=None, require_change=True):
            settle_calls.append(require_change)
            return verdict, []
        return spy

    saved_settle = cli._library_page_settled
    try:
        cli._library_page_settled = _spy_settle(False)
        notes: list = []
        ok = cli._library_next_page(_TurnBrowser(_NavPage()), notes)
        if not ok or not notes or "did not settle" not in notes[0]:
            failures.append(f"next_page degrade note: ok={ok} notes={notes}")
        if settle_calls != [True]:
            failures.append(f"next_page settle must require an id change: "
                            f"{settle_calls}")
        ok = cli._library_next_page(_TurnBrowser(_NavPage("disabled")), [])
        if ok or len(settle_calls) != 1:
            failures.append("disabled Next clicked or settle-waited")
        settle_calls.clear()
        cli._library_page_settled = _spy_settle(True)
        ok = cli._library_goto_page(
            _TurnBrowser(_NavPage(has_page_one=True)), 3, [])
        if not ok or settle_calls != [False, True, True]:
            failures.append(f"goto_page hop gating wrong: {settle_calls}")
    finally:
        cli._library_page_settled = saved_settle

    # ---- an unsettled page 1 lands in the pick record; a settled run
    # stays clean ----
    rig = _Rig({1: [10]}, {10: (True, True)})
    rec = _run(rig, settle=lambda *a, **k: (False, []))
    if not rec.get("nav_notes") or rec["tier"] != 1:
        failures.append(f"unsettled page not recorded (or pick broken): {rec}")
    rig = _Rig({1: [10]}, {10: (True, True)})
    rec = _run(rig)
    if "nav_notes" in rec:
        failures.append(f"settled run polluted with nav_notes: {rec}")

    # ---- the blind 2s sleep is gone; page 1 settles before ranking ----
    for fn in (cli._library_next_page, cli._library_goto_page):
        s = inspect.getsource(fn)
        if "wait_for_timeout(2000)" in s:
            failures.append(f"{fn.__name__} kept the blind 2s sleep")
        if "_library_page_settled" not in s:
            failures.append(f"{fn.__name__} is not settle-gated")
    ps = inspect.getsource(cli.pick_library_capture)
    if ps.index("_library_page_settled") > ps.index("_library_product_thumbs"):
        failures.append("page 1 must settle before the first thumbnail ranking")

    # ---- GRID VISIBILITY: the grid scrolls inside an inner panel whose
    # height derives from the viewport, and full_page screenshots stop at
    # the DOCUMENT's height — at 1000px only the top ~9 of a 20-card page
    # reached the ranker's image while its prompt listed all 20 ids. The
    # grid screenshot grows the viewport to the deepest card and ALWAYS
    # restores it ----
    class _GridPage:
        def __init__(self, extent, fail_resize=False, fail_extent=False):
            self.extent = extent
            self.fail_resize = fail_resize
            self.fail_extent = fail_extent
            self.viewport_size = {"width": 1600, "height": 1000}
            self.sets: list = []

        def evaluate(self, js):
            if self.fail_extent:
                raise RuntimeError("no dom")
            return self.extent

        def set_viewport_size(self, vp):
            if self.fail_resize:
                raise RuntimeError("resize refused")
            self.sets.append(dict(vp))
            self.viewport_size = dict(vp)

        def wait_for_timeout(self, ms):
            pass

    class _GridBrowser:
        def __init__(self, page, boom=False):
            self.page = page
            self.boom = boom
            self.shot_heights: list = []

        def screenshot_bytes(self, full_page=True):
            self.shot_heights.append(self.page.viewport_size["height"])
            if self.boom:
                raise RuntimeError("shot failed")
            return b"png"

    # a clipped grid: grown to fit (+margin), shot tall, restored
    gb = _GridBrowser(_GridPage(1677))
    out = cli._library_grid_screenshot(gb)
    if out != b"png" or gb.shot_heights != [1717]:
        failures.append(f"grid shot not taken tall: {gb.shot_heights}")
    if gb.page.viewport_size["height"] != 1000 \
            or [s["height"] for s in gb.page.sets] != [1717, 1000]:
        failures.append(f"viewport not restored: {gb.page.sets}")

    # a grid that already fits: no viewport churn
    gb = _GridBrowser(_GridPage(900))
    cli._library_grid_screenshot(gb)
    if gb.page.sets or gb.shot_heights != [1000]:
        failures.append(f"needless resize: {gb.page.sets}")

    # a monster grid: clamped to the cap
    gb = _GridBrowser(_GridPage(9000))
    cli._library_grid_screenshot(gb)
    if gb.page.sets[0]["height"] != cli.LIBRARY_GRID_MAX_VIEWPORT_H:
        failures.append(f"viewport growth not capped: {gb.page.sets}")

    # extent probe failing / resize refused: plain screenshot, no raise
    for label, page in (("extent fail", _GridPage(0, fail_extent=True)),
                        ("resize fail", _GridPage(1677, fail_resize=True))):
        gb = _GridBrowser(page)
        try:
            if cli._library_grid_screenshot(gb) != b"png":
                failures.append(f"{label}: no screenshot returned")
        except Exception as e:
            failures.append(f"{label}: raised {e}")

    # the screenshot itself failing STILL restores the viewport
    gb = _GridBrowser(_GridPage(1677), boom=True)
    try:
        cli._library_grid_screenshot(gb)
    except Exception:
        pass
    if gb.page.viewport_size["height"] != 1000:
        failures.append("viewport leaked tall after a failed screenshot")

    # the ranker uses the grid screenshot, not the plain one
    ts = inspect.getsource(cli._library_product_thumbs)
    if "_library_grid_screenshot(" not in ts or "screenshot_bytes(" in ts:
        failures.append("ranker not wired to the grid screenshot")

    # ---- 0.25.7 bug class: no raw newline in the new JS's literals ----
    for js_name in ("_LIBRARY_CARD_STATE_JS", "_LIBRARY_GRID_EXTENT_JS"):
        for q in ('"', "'"):
            for lit in re.findall(q + r"[^" + q + r"]*" + q,
                                  getattr(cli, js_name)):
                if "\n" in lit.replace("\\n", ""):
                    failures.append(f"raw newline in {js_name} literal "
                                    f"{lit[:30]!r}")

    # ---- the three card walks stay in sync (same filter + detection) ----
    for frag in ("r.width < 40 || r.width > 400", "img.getBoundingClientRect()",
                 "/#\\d+/"):
        for js_name in ("_LIBRARY_CARDS_JS", "_LIBRARY_CARD_STATE_JS",
                        "_LIBRARY_GRID_EXTENT_JS"):
            if frag not in getattr(cli, js_name):
                failures.append(f"{js_name} diverged on {frag!r}")

    # ---- spec: activated on the library step, after the filter ----
    spec = yaml.safe_load((REPO / "tasks" / "ov80i.yaml").read_text())
    lib = next(s for s in spec["steps"] if s["id"] == "library")
    if lib.get("pick_library_capture") is not True:
        failures.append("library step lost pick_library_capture: true")
    if lib.get("filter_library_recipe") is not True:
        failures.append("library step lost the recipe filter")

    # ---- ordering: filter -> pick -> (wait/screenshot/download) ----
    src = inspect.getsource(cli.main)
    filt = src.index("filter_library_by_recipe(")
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
