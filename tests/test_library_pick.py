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
- STRICT THUMBNAIL FILTER + RESERVE: the ranker's single call returns a
  RECOGNIZABLE pool (the part's features can be made out) and a
  PLAUSIBLE reserve; pass 1 clicks only the recognizable pool (a page
  with nothing recognizable costs zero clicks); the reserve is clicked
  only after the whole scan found no product+overlay, revisiting pages
  with NO new thumbnail calls, under the shared click cap; best partial
  spans both passes; each click records its pool,
- the spec activates the hook on the library step, after the filter,
- consistency by construction: the hook precedes the screenshot and the
  main-image download in the capture dispatch,
- PAGE-TURN SETTLE GATE (a field pick judged a stale/half-painted grid
  after pagination and shipped a valid-looking wrong capture): every
  navigation waits until the card ids CHANGE, every thumbnail has
  PAINTED, and the view is stable across two reads — bounded; a page
  that never settles is judged as-is and noted in the pick record
  (nav_notes); the blind 2s sleep is gone from both nav helpers; page 1
  settles before the first thumbnail ranking; the walk JS carries no
  raw newline inside quoted literals,
- BATCHED POPULATION (measured live: after Next the grid goes stale ->
  empty -> a 5-card batch -> 20 cards a second later; a stability-only
  gate released on the batch and the ranker judged a page whose real
  part captures were not yet in the DOM): the gate now demands the
  EXPECTED card count — min(page size, total - size*(N-1)) from the
  page's static "N Total Captures" / "N / page" texts — plus painted +
  one confirming read; without readable texts it falls back to three
  identical reads and notes the degradation; page numbers are threaded
  from the pick loop and goto hops so every gate knows its count,
- GRID VISIBILITY (the grid scrolls inside a viewport-derived inner
  panel, so full_page screenshots clipped the bottom ~11 of 20 cards —
  the ranker could only see half of every page; and the grid is only
  ~625px of a 1600px page, so the rest of the frame cost resolution):
  the grid screenshot grows the viewport to the card containers' bottom
  edge (bounded), re-measures the bbox after the re-layout, CROPS to it
  (labels included — the model maps thumbnails to ids by the "#N" text)
  so thumbnails reach the model undownscaled, always restores the
  viewport — even when the screenshot itself fails — never resizes when
  the grid already fits, and degrades stage by stage to the plain
  full-page shot.

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
                 thumb_crash=False, click_fails=(), page_plausible=None):
        self.page_thumbs = page_thumbs          # page -> [recognizable ids]
        self.page_plausible = page_plausible or {}   # page -> [reserve ids]
        self.viewers = viewers                  # id -> (product, overlay)
        self.total_pages = total_pages or max(
            list(page_thumbs) + list(self.page_plausible), default=1)
        self.thumb_crash = thumb_crash
        self.click_fails = set(click_fails)
        self.page = 1
        self.selected = None
        self.clicked: list = []
        self.gotos: list = []
        self.thumb_calls: list = []
        self.first_by_page = {p: (ids[0] if ids else 900 + p)
                              for p, ids in page_thumbs.items()}

    def product_thumbs(self, browser, recipe, part_desc="", top_n=3,
                       trace=None):
        self.thumb_calls.append(self.page)
        if self.thumb_crash:
            raise RuntimeError("vision down")
        return {"recognizable": [
                    {"id": i, "badge": "pass"}
                    for i in list(self.page_thumbs.get(self.page, []))[:top_n]],
                "plausible": [
                    {"id": i, "badge": "pass"}
                    for i in list(self.page_plausible.get(self.page, []))[:top_n]]}

    def click(self, browser, cid):
        if cid in self.click_fails:
            return False
        self.clicked.append((self.page, cid))
        self.selected = cid
        return True

    def viewer(self, browser, recipe="", part_desc=""):
        product, overlay = self.viewers.get(self.selected, (False, False))
        return {"product_image": product, "overlay": overlay, "reason": "rig"}

    def next_page(self, browser, notes=None, page_no=None):
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


def _settled(browser, prev, max_wait_s=None, require_change=True,
             expected=None):
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

    # ---- TWO PASSES: the plausible reserve is held during the scan and
    # clicked only after the recognizable pools found no product+overlay;
    # no thumbnail call is repeated; the click cap is shared ----
    # page 1 has nothing recognizable (dark frames = reserve), page 2 has
    # the part: pass 1 must cost ZERO clicks on page 1 and win on page 2
    rig = _Rig({1: [], 2: [20]}, {20: (True, True)},
               page_plausible={1: [11, 12, 13]})
    rec = _run(rig)
    if rig.clicked != [(2, 20)] or rec["tier"] != 1:
        failures.append(f"reserve clicked during pass 1: {rig.clicked} {rec}")
    if rec.get("reserve_pass"):
        failures.append("reserve pass ran although pass 1 won")
    # nothing recognizable anywhere, reserve holds the part: pass 2 finds
    # it — after returning to the page — with no new thumbnail calls
    rig = _Rig({1: [], 2: []}, {12: (True, True)}, total_pages=2,
               page_plausible={1: [11, 12], 2: [21]})
    rec = _run(rig)
    if rec["chosen"] != {"page": 1, "id": 12} or rec["tier"] != 1:
        failures.append(f"reserve pass missed the winner: {rec}")
    if not rec.get("reserve_pass") or rig.gotos != [1]:
        failures.append(f"reserve pass navigation wrong: gotos={rig.gotos} {rec}")
    if rig.thumb_calls != [1, 2]:
        failures.append(f"thumbnails re-judged in pass 2: {rig.thumb_calls}")
    if [c["pool"] for c in rec["clicked"]] != ["plausible", "plausible"]:
        failures.append(f"pool not recorded per click: {rec['clicked']}")
    # recognizable found only tier 2 (product, no overlay): the reserve is
    # still tried for a tier 1, and best partial spans both passes
    rig = _Rig({1: [10]}, {10: (True, False), 11: (False, True)},
               page_plausible={1: [11]})
    rec = _run(rig)
    # search order 10 (tier 2) then reserve 11 (tier 3); the winner is 10,
    # re-selected by the jump-back since the reserve click moved off it
    if rig.clicked != [(1, 10), (1, 11), (1, 10)] \
            or rec["chosen"] != {"page": 1, "id": 10} or rec["tier"] != 2 \
            or rig.selected != 10:
        failures.append(f"best partial across passes wrong: {rig.clicked} {rec}")
    # the click cap is shared: a pass-1 cap-out means no reserve pass
    rig = _Rig({p: [p * 10 + i for i in range(3)] for p in range(1, 5)}, {},
               total_pages=4, page_plausible={1: [99]})
    rec = _run(rig, click_cap=6)
    if len(rec["clicked"]) != 6 or rec.get("reserve_pass") \
            or any(c["id"] == 99 for c in rec["clicked"]):
        failures.append(f"cap not shared with the reserve pass: {rec}")
    # a page whose reserve can't be reached again is skipped, noted
    rig = _Rig({1: [], 2: []}, {}, total_pages=2, page_plausible={1: [11]})
    rig.goto_page = lambda browser, n, notes=None: False
    rec = _run(rig)
    if rec["clicked"] or not any("could not return" in n
                                 for n in rec.get("nav_notes", [])):
        failures.append(f"unreachable reserve page not noted: {rec}")

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

    # ---- the BADGE classifier: pure text -> group. Training first (a
    # trainset card with a verdict is still last), verdict must be its
    # own line, recipe name stripped so "...Training..." recipes can't
    # mark every card, case/whitespace tolerant ----
    R = "Traton Bushing Wear"
    for text, want in (
        (f"#2609\nPASS\n{R}\n2026-08-14 12:52:11.471\n3 days ago", "pass"),
        (f"#2603\nFAIL\n{R}\n2026-08-03", "fail"),
        (f"#2607\n{R}\n2026-08-11\n6 days ago\nUsed for training\n"
         f"(Classification, Segmentation)", "training"),
        (f"#2607\nPASS\n{R}\nused  For\nTRAINING", "training"),
        (f"#2601\n{R}\n2026-08-03", "none"),
        ("#2601\nPASSED inspection notes\nR", "none"),   # not a bare tag line
        ("", "none"),
    ):
        got = cli._card_badge(text, R)[1]
        if got != want:
            failures.append(f"badge {want!r} expected, got {got!r} for {text[:30]!r}")
    if cli._card_badge("#5\nPASS\nTraining Line Check\n2026", "Training Line Check")[1] != "pass":
        failures.append("recipe name containing 'training' marked the card trainset")
    if not (cli.BADGE_VERDICT < cli.BADGE_NONE < cli.BADGE_TRAINING):
        failures.append("badge group order broken")

    # ---- the ORDERING: badge group first, the MODEL'S RANK within a
    # group, grid position (recency) only as the final tiebreak;
    # hallucinated ids dropped, duplicates collapsed; sort happens BEFORE
    # the per-page cap ----
    def _c(cid, *lines):
        return {"id": str(cid), "painted": True,
                "text": "\n".join([f"#{cid}", *lines, R]), "box": None}
    grid = [_c(105, "PASS"),                 # idx0 verdict, newest
            _c(104, "Used for training"),    # idx1 training
            _c(103),                         # idx2 none
            _c(102, "FAIL"),                 # idx3 verdict
            _c(101, "PASS")]                 # idx4 verdict, oldest
    # the model ranks the trainset card FIRST, then the OLDEST verdict
    # card above the newest one; a hallucination and a duplicate thrown in
    model_answer = [104, 999, 104, 101, 102, 103, 105]
    ordered = cli._order_candidates(model_answer, grid, R)
    # verdict group in MODEL order (101, 102, 105), then none, then training
    if [c["id"] for c in ordered] != [101, 102, 105, 103, 104]:
        failures.append(f"badge->model-rank order wrong: {ordered}")
    if [c["badge"] for c in ordered] != ["pass", "fail", "pass", "none", "training"]:
        failures.append(f"badge labels wrong: {ordered}")
    # cap AFTER sort: the top 3 are the three verdict cards, never 104
    if [c["id"] for c in ordered[:3]] != [101, 102, 105]:
        failures.append("cap must apply after the badge sort")
    if any(c["id"] == 999 for c in ordered):
        failures.append("hallucinated id survived")
    if cli._order_candidates([103], grid, R) != [{"id": 103, "badge": "none"}]:
        failures.append("single-candidate ordering wrong")
    if cli._order_candidates([], grid, R) != []:
        failures.append("empty model answer must yield no candidates")

    # the live page-2 shape that motivated model-rank-within-group: every
    # card PASS, the model ranks the clearly-lit part first and the dark
    # newest frames last — recency-within-group had inverted this
    page2 = [_c(i, "PASS") for i in (2590, 2589, 2588, 2587, 2582, 2577, 2576)]
    ranked = [2582, 2577, 2576, 2590, 2589, 2588, 2587]
    got = [c["id"] for c in cli._order_candidates(ranked, page2, R)]
    if got != ranked:
        failures.append(f"model rank not preserved within one group: {got}")
    # recency is only the FINAL tiebreak: with a total model order it
    # never reorders — grid position must not beat rank
    grid_rev = list(reversed(page2))
    got = [c["id"] for c in cli._order_candidates(ranked, grid_rev, R)]
    if got != ranked:
        failures.append(f"grid position beat the model's rank: {got}")

    # ---- the RANKER end-to-end on a fake page: two pools from one call
    # (recognizable = click pool, plausible = reserve), each badge-sorted
    # and capped; a card in BOTH lists is recognizable only; the prompt
    # asks for the RECOGNIZABLE standard and both lists ----
    from PIL import Image
    import io as _io

    from core import llm as _llm

    class _ThumbPage:
        viewport_size = {"width": 1600, "height": 1000}

        def evaluate(self, js):
            return grid

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
            self.schema = schema
            # 103 appears in BOTH lists; 777 is a hallucination in the reserve
            return {"reason": "r", "product_captures": model_answer,
                    "plausible_captures": [103, 777, 104]}

    _stub = _ThumbBackend()
    _llm.set_backend(_stub)
    trace: dict = {}
    try:
        got = cli._library_product_thumbs(_ThumbBrowser(), R, "desc", top_n=3,
                                          trace=trace)
    finally:
        _llm.set_backend(None)
    if [c["id"] for c in got["recognizable"]] != [101, 102, 105]:
        failures.append(f"ranker order/cap wrong: {got}")
    # 103 was recognizable -> not in the reserve; 104 was ALSO in the
    # recognizable answer -> not in the reserve; 777 dropped => empty
    if got["plausible"] != []:
        failures.append(f"reserve must exclude recognizable ids + hallucinations: {got}")
    if trace.get("cards") != 5 or trace.get("model_ranked") != model_answer \
            or trace.get("ordered") != [101, 102, 105, 103, 104] \
            or trace.get("model_plausible") != [103, 777, 104] \
            or trace.get("plausible_ordered") != []:
        failures.append(f"ranker trace incomplete: {trace}")
    # a genuinely separate reserve survives, badge-sorted and capped
    _stub2 = _ThumbBackend()
    _stub2.complete = lambda prompt, schema=None, images=None, max_tokens=0, \
        model=None: {"reason": "r", "product_captures": [],
                     "plausible_captures": [104, 101, 105, 103]}
    _llm.set_backend(_stub2)
    try:
        got = cli._library_product_thumbs(_ThumbBrowser(), R, "desc", top_n=3)
    finally:
        _llm.set_backend(None)
    if got["recognizable"] != [] \
            or [c["id"] for c in got["plausible"]] != [101, 105, 103]:
        failures.append(f"reserve ordering/cap wrong: {got}")
    prompt_flat = " ".join(getattr(_stub, "prompt", "").split())
    for must in ("RECOGNIZABLE", "product_captures", "plausible_captures",
                 "too dark, blurred or featureless to recognize",
                 "even if it could conceivably be the part",
                 "RANK these most clearly recognizable first",
                 "the part described above"):
        if must not in prompt_flat:
            failures.append(f"thumbnail prompt lost: {must!r}")
    for gone in ("plausibly shows the part", "UP TO", "exposure or lighting"):
        if gone in prompt_flat:
            failures.append(f"thumbnail prompt kept the soft standard: {gone!r}")
    if "#105, #104, #103, #102, #101" not in prompt_flat:
        failures.append("prompt ids not in grid order")
    if "plausible_captures" not in _stub.schema.get("required", []):
        failures.append("schema does not require the reserve list")
    # without an anchor the recognizability test is about a manufactured part
    if "physical manufactured part" not in cli._recognizable_what(""):
        failures.append("no-anchor recognizability subject wrong")

    # ---- the pick record carries each click's badge; the per-page
    # candidate order is logged ----
    ps = inspect.getsource(cli.pick_library_capture)
    if '"badge": badge' not in ps or "candidates (badge, then" not in ps \
            or '"model rank): "' not in ps:
        failures.append("pick loop lost the badge record/log")

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

    def _settle_run(page, prev, require_change=True, expected=None):
        saved = cli.LIBRARY_PAGE_TURN_WAIT_S
        try:
            cli.LIBRARY_PAGE_TURN_WAIT_S = 0.6
            return cli._library_page_settled(_TurnBrowser(page), prev,
                                             require_change=require_change,
                                             expected=expected)
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
                           require_change=False, expected=2)
    if not ok:
        failures.append("require_change=False rejected a settled grid")

    # ---- BATCHED POPULATION (measured live: 20 stale -> 0 -> 5 cards ->
    # 20 cards a second later): a partial batch that holds across reads
    # must NOT be believed when the expected count is known ----
    def _cards(*ids):
        return [{"id": str(i), "painted": True} for i in ids]
    stale = _cards(*range(2610, 2590, -1))
    batch = _cards(2590, 2589, 2588, 2587, 2586)
    full = _cards(*range(2590, 2570, -1))
    # the partial batch holds for THREE reads (2s..4s) before the rest lands
    timeline = [(0, stale), (1000, []), (2000, batch), (5000, full)]
    page = _TurnPage(timeline)
    ok, ids = _settle_run(page, [c["id"] for c in stale], expected=20)
    if not ok or len(ids) != 20 or ids[0] != 2590:
        failures.append(f"expected-count gate released on the batch: {ok} "
                        f"{len(ids)} cards")
    if page.clock_ms < 6000:
        failures.append(f"settled before the full grid + confirm read: "
                        f"{page.clock_ms}ms")
    # the same timeline with NO expected count: stability alone must
    # demand LIBRARY_SETTLE_STABLE_READS_FALLBACK identical reads — three
    # here — so a batch held for two reads is still not enough ...
    page = _TurnPage([(0, stale), (1000, []), (2000, batch), (4000, full)])
    ok, ids = _settle_run(page, [c["id"] for c in stale])
    if not ok or len(ids) != 20:
        failures.append(f"fallback gate released on a 2-read batch: {ok} "
                        f"{len(ids)} cards")
    # ... and the two-read gate the fallback replaces would have released
    # on it (documenting the fixed weakness, not asserting it)
    if cli.LIBRARY_SETTLE_STABLE_READS_FALLBACK < 3:
        failures.append("fallback stability window too short for batches")

    # a wrong count never settles (bounded), ids still reported
    ok, ids = _settle_run(_TurnPage([(0, batch)]), [], expected=20)
    if ok or len(ids) != 5:
        failures.append(f"short grid claimed settled under expected=20: {ok}")
    # the last page: fewer cards is exactly right when expected says so
    ok, ids = _settle_run(_TurnPage([(0, batch)]), [], expected=5)
    if not ok:
        failures.append("last-page short grid rejected under expected=5")
    # a zero-capture recipe: expected 0 settles on an empty grid
    ok, ids = _settle_run(_TurnPage([(0, [])]), [], require_change=False,
                          expected=0)
    if not ok or ids:
        failures.append("expected=0 did not settle on the empty grid")

    # ---- the expected count comes from two static page texts ----
    class _CountPage:
        def __init__(self, text):
            self.text = text

        def evaluate(self, js):
            return self.text

    def _exp(text, page_no):
        return cli._library_expected_cards(_TurnBrowser(_CountPage(text)),
                                           page_no)
    body = "Library\n77 Total Captures\nSort By\n20 / page\nGo to"
    if [_exp(body, n) for n in (1, 2, 3, 4, 5)] != [20, 20, 20, 17, 0]:
        failures.append(f"expected counts wrong: "
                        f"{[_exp(body, n) for n in (1, 2, 3, 4, 5)]}")
    if _exp("Library\n2,243 Total Captures\n20 / page", 1) != 20:
        failures.append("comma total not parsed")
    if _exp("Library\n0 Total Captures\n20 / page", 1) != 0:
        failures.append("zero total must expect zero cards")
    for text in ("Library\n77 Total Captures", "Library\n20 / page", ""):
        if _exp(text, 2) is not None:
            failures.append(f"unreadable texts must yield None: {text!r}")

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
        def spy(browser, prev, max_wait_s=None, require_change=True,
                expected=None):
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

    # ---- page numbers reach the gate as expected counts: the pick loop
    # settles page N+1 on Next, goto hops 2..N, entry gate page 1 ----
    exp_calls = []
    saved_exp = cli._library_settle_expectation

    def _spy_exp(browser, page_no, notes):
        exp_calls.append(page_no)
        return None
    cli._library_settle_expectation = _spy_exp
    cli._library_page_settled = _spy_settle(True)
    try:
        cli._library_goto_page(_TurnBrowser(_NavPage(has_page_one=True)), 4, [])
        if exp_calls != [1, 2, 3, 4]:
            failures.append(f"goto_page hop numbering wrong: {exp_calls}")
    finally:
        cli._library_settle_expectation = saved_exp
        cli._library_page_settled = saved_settle
    ps_src = inspect.getsource(cli.pick_library_capture)
    if "page_no=page + 1" not in ps_src \
            or "_library_settle_expectation(browser, 1, nav_notes)" not in ps_src:
        failures.append("pick loop does not hand page numbers to the gate")

    # ---- an unsettled page 1 lands in the pick record; a settled run
    # stays clean ----
    rig = _Rig({1: [10]}, {10: (True, True)})
    rec = _run(rig, settle=lambda *a, **k: (False, []))
    if not rec.get("nav_notes") or rec["tier"] != 1:
        failures.append(f"unsettled page not recorded (or pick broken): {rec}")
    rig = _Rig({1: [10]}, {10: (True, True)})
    rec = _run(rig)
    if any("did not settle" in n for n in rec.get("nav_notes", [])):
        failures.append(f"settled run polluted with settle notes: {rec}")

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
    # grid screenshot grows the viewport to the containers' bottom edge,
    # crops to their bbox (labels included) and ALWAYS restores the
    # viewport ----
    import io as _gio

    from PIL import Image as _GImage

    BOX = {"left": 265, "top": 419, "right": 890, "bottom": 1677}

    class _GridPage:
        """The walk returns two cards whose container boxes union to
        `box` (None -> empty grid)."""

        def __init__(self, box=BOX, fail_resize=False, fail_probe=False):
            self.box = box
            self.fail_resize = fail_resize
            self.fail_probe = fail_probe
            self.viewport_size = {"width": 1600, "height": 1000}
            self.sets: list = []
            self.probes = 0

        def evaluate(self, js):
            if self.fail_probe:
                raise RuntimeError("no dom")
            self.probes += 1
            if not self.box:
                return []
            b = self.box
            return [{"id": "1", "painted": True, "text": "#1",
                     "box": {"left": b["left"], "top": b["top"],
                             "right": b["left"] + 10, "bottom": b["top"] + 10}},
                    {"id": "2", "painted": True, "text": "#2",
                     "box": {"left": b["right"] - 10, "top": b["bottom"] - 10,
                             "right": b["right"], "bottom": b["bottom"]}}]

        def set_viewport_size(self, vp):
            if self.fail_resize:
                raise RuntimeError("resize refused")
            self.sets.append(dict(vp))
            self.viewport_size = dict(vp)

        def wait_for_timeout(self, ms):
            pass

    class _GridBrowser:
        def __init__(self, page, boom=False, real_png=True):
            self.page = page
            self.boom = boom
            self.real_png = real_png
            self.shot_heights: list = []

        def screenshot_bytes(self, full_page=True):
            h = self.page.viewport_size["height"]
            self.shot_heights.append(h)
            if self.boom:
                raise RuntimeError("shot failed")
            if not self.real_png:
                return b"png"
            buf = _gio.BytesIO()
            _GImage.new("RGB", (1600, h), (30, 30, 30)).save(buf, format="PNG")
            return buf.getvalue()

    # a clipped grid: grown to fit its bottom edge (+margin), shot tall,
    # CROPPED to the container bbox (+pad), viewport restored, bbox
    # re-measured after the re-layout
    gb = _GridBrowser(_GridPage())
    out = cli._library_grid_screenshot(gb)
    if gb.shot_heights != [1717]:
        failures.append(f"grid shot not taken tall: {gb.shot_heights}")
    pad = cli.LIBRARY_GRID_CROP_PAD
    with _GImage.open(_gio.BytesIO(out)) as im:
        want = (890 - 265 + 2 * pad, min(1717, 1677 + pad) - (419 - pad))
        if im.size != want:
            failures.append(f"crop wrong: {im.size} != {want}")
    if gb.page.viewport_size["height"] != 1000 \
            or [s["height"] for s in gb.page.sets] != [1717, 1000]:
        failures.append(f"viewport not restored: {gb.page.sets}")
    if gb.page.probes != 2:
        failures.append(f"bbox not re-measured after growth: {gb.page.probes}")

    # a grid that already fits: no viewport churn, one probe, still cropped
    gb = _GridBrowser(_GridPage({"left": 265, "top": 119, "right": 890,
                                 "bottom": 877}))
    out = cli._library_grid_screenshot(gb)
    if gb.page.sets or gb.shot_heights != [1000] or gb.page.probes != 1:
        failures.append(f"needless resize/probe: {gb.page.sets} "
                        f"{gb.page.probes}")
    with _GImage.open(_gio.BytesIO(out)) as im:
        if im.size != (890 - 265 + 2 * pad, 877 - 119 + 2 * pad):
            failures.append(f"fits-crop wrong: {im.size}")

    # a monster grid: clamped to the cap
    gb = _GridBrowser(_GridPage(dict(BOX, bottom=9000)))
    cli._library_grid_screenshot(gb)
    if gb.page.sets[0]["height"] != cli.LIBRARY_GRID_MAX_VIEWPORT_H:
        failures.append(f"viewport growth not capped: {gb.page.sets}")

    # degradations: empty grid -> plain uncropped shot; probe crash /
    # resize refusal / undecodable screenshot -> a shot always comes back
    gb = _GridBrowser(_GridPage(None))
    out = cli._library_grid_screenshot(gb)
    with _GImage.open(_gio.BytesIO(out)) as im:
        if im.size != (1600, 1000) or gb.page.sets:
            failures.append(f"empty grid mishandled: {im.size} {gb.page.sets}")
    for label, gb in (
        ("probe fail", _GridBrowser(_GridPage(fail_probe=True),
                                    real_png=False)),
        ("resize fail", _GridBrowser(_GridPage(fail_resize=True),
                                     real_png=False)),
        ("bad png", _GridBrowser(_GridPage(), real_png=False)),
    ):
        try:
            if cli._library_grid_screenshot(gb) != b"png":
                failures.append(f"{label}: no screenshot returned")
        except Exception as e:
            failures.append(f"{label}: raised {e}")

    # the screenshot itself failing STILL restores the viewport
    gb = _GridBrowser(_GridPage(), boom=True)
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

    # ---- 0.25.7 bug class: no raw newline in the walk JS's literals ----
    for q in ('"', "'"):
        for lit in re.findall(q + r"[^" + q + r"]*" + q,
                              cli._LIBRARY_CARD_WALK_JS):
            if "\n" in lit.replace("\\n", ""):
                failures.append(f"raw newline in walk-JS literal {lit[:30]!r}")

    # ---- ONE card walk: every consumer derives from it (the three
    # keep-in-sync siblings it replaced are gone) ----
    for gone in ("_LIBRARY_CARDS_JS", "_LIBRARY_CARD_STATE_JS",
                 "_LIBRARY_GRID_BBOX_JS"):
        if hasattr(cli, gone):
            failures.append(f"{gone} still exists — the card walk must be one")
    for fn in (cli._library_card_ids, cli._library_grid_bbox,
               cli._library_page_settled, cli._library_product_thumbs,
               cli._library_first_capture):
        src_fn = inspect.getsource(fn)
        if "_library_cards(" not in src_fn and "_library_card_ids(" not in src_fn:
            failures.append(f"{fn.__name__} does not derive from the card walk")
    if 'page.evaluate(' in inspect.getsource(cli._library_page_settled):
        failures.append("settle gate bypasses the card walk")

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
