"""Feature-promo modals are dismissed before capture — Escape first, never
the walkthrough CTA.

The alignment screen greets every fresh browser session with a promo modal
("Try out the New Aligner"); nothing is persisted on dismissal, so every
run sees it. What this suite pins:

- fired only when a dialog is actually VISIBLE (no blind keypresses),
- Escape is the primary close; a dismiss-flavoured button is the fallback,
- the primary CTA ("See What's New") is NEVER clicked — it starts the
  walkthrough — even when it is the only button that would close the modal,
- the whole routine is cosmetic: no failure may stop the capture,
- the spec activates it on template_image, before the vision wait, and the
  agent goal forbids following the walkthrough.

Run: uv run python tests/test_promo_modal.py
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

import cli  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class _FakeBtn:
    def __init__(self, page, label, closes):
        self._page, self.label, self.closes = page, label, closes

    def inner_text(self):
        return self.label

    def click(self):
        self._page.clicked.append(self.label)
        if self.closes:
            self._page.dialog_open = False


class _FakeDialog:
    def __init__(self, page):
        self._page = page

    def is_visible(self):
        return self._page.dialog_open

    def query_selector_all(self, sel):
        return [_FakeBtn(self._page, lbl, closes)
                for lbl, closes in self._page.buttons]


class _FakeKeyboard:
    def __init__(self, page):
        self._page = page

    def press(self, combo):
        self._page.pressed.append(combo)
        if combo == "Escape" and self._page.escape_works:
            self._page.dialog_open = False


class _FakePage:
    def __init__(self, dialog_open, escape_works=True, buttons=(), broken=False):
        self.dialog_open = dialog_open
        self.escape_works = escape_works
        self.buttons = list(buttons)
        self.broken = broken
        self.pressed: list = []
        self.clicked: list = []
        self.keyboard = _FakeKeyboard(self)

    def query_selector_all(self, sel):
        if self.broken:
            raise RuntimeError("page crashed")
        return [_FakeDialog(self)] if self.dialog_open else []

    def wait_for_timeout(self, ms):
        pass


class _FakeBrowser:
    def __init__(self, page):
        self.page = page


def main() -> int:
    failures = []

    # ---- no visible dialog: nothing is pressed or clicked ----
    page = _FakePage(dialog_open=False)
    cli.dismiss_promo_modal(_FakeBrowser(page))
    if page.pressed or page.clicked:
        failures.append(f"acted with no dialog visible: {page.pressed} {page.clicked}")

    # ---- Escape closes it: one keypress, no button ever touched ----
    page = _FakePage(dialog_open=True, escape_works=True,
                     buttons=[("See What's New", True),
                              ("I'll Explore on my own", True)])
    cli.dismiss_promo_modal(_FakeBrowser(page))
    if page.pressed != ["Escape"]:
        failures.append(f"Escape not the primary close: {page.pressed}")
    if page.clicked:
        failures.append(f"buttons clicked though Escape worked: {page.clicked}")
    if page.dialog_open:
        failures.append("dialog still open after working Escape")

    # ---- Escape ineffective: the DISMISS button is clicked, never the
    # walkthrough CTA, regardless of button order ----
    page = _FakePage(dialog_open=True, escape_works=False,
                     buttons=[("See What's New", True),
                              ("I'll Explore on my own", True)])
    cli.dismiss_promo_modal(_FakeBrowser(page))
    if page.clicked != ["I'll Explore on my own"]:
        failures.append(f"wrong button path: {page.clicked}")
    if page.dialog_open:
        failures.append("dialog still open after dismiss-button fallback")

    # ---- only the CTA would close it: still never clicked; warn instead ----
    page = _FakePage(dialog_open=True, escape_works=False,
                     buttons=[("See What's New", True)])
    cli.dismiss_promo_modal(_FakeBrowser(page))
    if page.clicked:
        failures.append(f"walkthrough CTA clicked: {page.clicked}")
    if not page.dialog_open:
        failures.append("dialog closed by something other than sanctioned paths")

    # ---- cosmetic guarantee: a crashing page never raises ----
    try:
        cli.dismiss_promo_modal(_FakeBrowser(_FakePage(dialog_open=True, broken=True)))
    except Exception as e:
        failures.append(f"dismissal raised through a page failure: {e}")

    # ---- the dismiss vocabulary stays dismissive ----
    for good in ("I'll Explore on my own", "Maybe later", "Not now", "Skip",
                 "Got it", "No thanks"):
        if not cli.PROMO_DISMISS_RE.search(good):
            failures.append(f"dismiss vocabulary lost {good!r}")
    for bad in ("See What's New", "Start tour", "Next", "Show me"):
        if cli.PROMO_DISMISS_RE.search(bad):
            failures.append(f"dismiss vocabulary matches a CTA: {bad!r}")

    # ---- spec: activated on template_image, and the goal forbids the tour ----
    spec = yaml.safe_load((REPO / "tasks" / "ov80i.yaml").read_text())
    tmpl = next(s for s in spec["steps"] if s["id"] == "template_image")
    if tmpl.get("dismiss_promo_modal") is not True:
        failures.append("template_image lost dismiss_promo_modal: true")
    if "never start the walkthrough" not in tmpl["goal"].lower():
        failures.append("template_image goal lost the never-start-walkthrough rule")

    # ---- order: dismissal runs before the vision wait would stall on the
    # modal covering the image viewer ----
    src = inspect.getsource(cli.main)
    call = src.index("dismiss_promo_modal(browser)")
    wait = src.index('step.get("wait_image_loaded")')
    if not call < wait:
        failures.append("dismiss_promo_modal must run before the image wait")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL PROMO-MODAL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
