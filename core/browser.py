"""Playwright wrapper shared by the deterministic executor and the navigator agent.

All element interaction goes through numbered refs assigned by snapshot():
snapshot() enumerates visible interactive elements, tags each with a
data-sg-ref attribute, and returns a listing. Refs are reassigned on every
snapshot, so any action that changes the page requires a fresh snapshot.
"""

from __future__ import annotations

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

SNAPSHOT_JS = r"""
(offset) => {
  document.querySelectorAll('[data-sg-ref]').forEach(el => el.removeAttribute('data-sg-ref'));
  const items = [];
  let n = offset;
  const push = (el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') return;
    n += 1;
    el.setAttribute('data-sg-ref', String(n));
    const text = (el.innerText || el.value || el.getAttribute('aria-label')
      || el.getAttribute('placeholder') || '').trim().replace(/\s+/g, ' ').slice(0, 160);
    // Short labels (Edit / Activate / icon buttons) are ambiguous without the
    // enclosing row: climb ancestors until one carries enough text to identify it.
    let ctx = '';
    if (text.length < 20) {
      let node = el.parentElement;
      while (node && node !== document.body) {
        const t = (node.innerText || '').trim().replace(/\s+/g, ' ');
        if (t.length >= 30) { ctx = t.slice(0, 110); break; }
        node = node.parentElement;
      }
      if (ctx === text) ctx = '';
    }
    items.push({
      ref: n,
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      text,
      ctx,
      disabled: el.disabled === true,
    });
  };
  const interactive = new Set(document.querySelectorAll(
    'a, button, input, select, textarea, [role="button"], [role="link"], [role="menuitem"], ' +
    '[role="tab"], [role="checkbox"], [role="switch"], [role="option"], [onclick]'));
  for (const el of interactive) push(el);
  // Framework clickables (React et al.) attach listeners invisibly; cursor:pointer
  // is the only DOM-visible signal for those.
  for (const el of document.querySelectorAll('*')) {
    if (interactive.has(el) || el.hasAttribute('data-sg-ref')) continue;
    if (el.childElementCount > 5) continue;
    if (window.getComputedStyle(el).cursor === 'pointer' && !el.closest('[data-sg-ref]')) push(el);
  }
  return items;
}
"""

# Camera UIs poll/stream constantly, so "networkidle" may never fire; a short
# settle delay plus caller-side verification is the reliable pattern here.
SETTLE_MS = 800


class Browser:
    def __init__(self, headed: bool = False):
        self._headed = headed
        self._pw = None
        self.page = None
        self.last_items: dict[int, dict] = {}
        # Frame each ref lives in — Node-RED and similar embedded UIs render in
        # iframes, invisible to a main-frame-only snapshot.
        self._ref_frames: dict[int, object] = {}
        self.downloads: list = []

    def start(self):
        self._pw = sync_playwright().start()
        browser = self._pw.chromium.launch(headless=not self._headed)
        context = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1600, "height": 1000},
            accept_downloads=True,
        )
        context.set_default_timeout(10_000)
        self.page = context.new_page()
        self.page.on("download", lambda d: self.downloads.append(d))

    def close(self):
        if self._pw:
            self._pw.stop()
            self._pw = None

    def goto(self, url: str):
        # Embedded cameras can be slow to first byte; retry once on timeout.
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightTimeout:
            self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        self.page.wait_for_timeout(SETTLE_MS * 2)

    def url(self) -> str:
        return self.page.url

    def snapshot(self) -> str:
        self.last_items = {}
        self._ref_frames = {}
        lines = [f"URL: {self.page.url}", f"Title: {self.page.title()}", ""]
        offset = 0
        for frame in self.page.frames:
            try:
                items = frame.evaluate(SNAPSHOT_JS, offset)
            except Exception:
                continue  # frame may be detached or cross-origin
            if not items:
                continue
            in_iframe = frame is not self.page.main_frame
            frame_label = " [iframe]" if in_iframe else ""
            for it in items:
                self.last_items[it["ref"]] = it
                self._ref_frames[it["ref"]] = frame
                role = f" role={it['role']}" if it["role"] else ""
                disabled = " (disabled)" if it["disabled"] else ""
                ctx = f'  (in: "{it["ctx"]}")' if it.get("ctx") else ""
                lines.append(
                    f'[{it["ref"]}] {it["tag"]}{role}{disabled} "{it["text"]}"{ctx}{frame_label}'
                )
            offset = max(it["ref"] for it in items)
        return "\n".join(lines)

    def _frame_for(self, ref: int):
        return self._ref_frames.get(ref) or self.page

    def page_text(self, limit: int = 6000) -> str:
        text = self.page.inner_text("body")
        return text[:limit] + ("\n...[truncated]" if len(text) > limit else "")

    def screenshot_bytes(self, full_page: bool = False) -> bytes:
        return self.page.screenshot(full_page=full_page)

    def click(self, ref: int) -> str:
        item = self.last_items.get(ref)
        if item is None:
            return f"Error: ref {ref} is not in the latest snapshot. Call snapshot first."
        try:
            self._frame_for(ref).click(f'[data-sg-ref="{ref}"]')
        except PlaywrightTimeout:
            return f"Error: click on ref {ref} timed out (element may be covered or gone)."
        self.page.wait_for_timeout(SETTLE_MS)
        return f'Clicked [{ref}] {item["tag"]} "{item["text"]}".'

    def click_text(self, text: str) -> str:
        for frame in self.page.frames:
            try:
                loc = frame.get_by_text(text, exact=False)
                if loc.count() == 0:
                    continue
                loc.first.click(timeout=5_000)
                self.page.wait_for_timeout(SETTLE_MS)
                return f'Clicked element containing "{text}".'
            except Exception:
                continue
        return f'Error: no clickable element containing "{text}" found.'

    def type_text(self, ref: int, text: str) -> str:
        if ref not in self.last_items:
            return f"Error: ref {ref} is not in the latest snapshot. Call snapshot first."
        try:
            self._frame_for(ref).fill(f'[data-sg-ref="{ref}"]', text)
        except PlaywrightTimeout:
            return f"Error: could not type into ref {ref}."
        self.page.wait_for_timeout(SETTLE_MS)
        return f'Typed "{text}" into [{ref}].'

    def press_keys(self, combo: str) -> str:
        """Press a keyboard shortcut (Playwright syntax, e.g. "ControlOrMeta+e").
        Keys go to the currently focused element/frame — click the target area
        first when the shortcut belongs to an embedded editor."""
        try:
            self.page.keyboard.press(combo)
        except Exception as e:
            return f"Error: press {combo!r} failed: {e}"
        self.page.wait_for_timeout(SETTLE_MS)
        return f"Pressed {combo}."

    def wait_for_text(self, text: str, timeout_s: float = 5) -> str:
        try:
            self.page.get_by_text(text, exact=False).first.wait_for(
                state="visible", timeout=timeout_s * 1000
            )
            return f'"{text}" is visible.'
        except PlaywrightTimeout:
            return f'Error: "{text}" did not appear within {timeout_s}s.'
