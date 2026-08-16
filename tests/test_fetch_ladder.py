"""Native-image fetches climb a ladder of increasingly-alive paths.

The request context is a separate network stack from the browser page (a
Node-side client doing fresh DNS per call); on Tailscale links a cold
lookup can transiently fail while Chromium rides warm connections — a
field run lost the template download to getaddrinfo ENOTFOUND while the
page rendered the same URL fine, and the imaging composite silently
degraded with a misleading reason. What this suite pins:

- rung 1 retries ONLY network-level errors, with backoff, bounded,
- an HTTP status (404) is not transient: no retry, straight down-ladder,
- rung 2 (in-page fetch, Chromium's stack) serves when rung 1 dies, with
  the direct-fetch failure recorded as source_error — visible, not silent,
- rung 3 (canvas-export of the already-decoded <img>) is the last resort,
- all rungs failing records the joined errors and never raises,
- the data: URL path never touches the ladder,
- both JS rungs contain no raw newline inside quoted literals (the
  0.25.7 bug class),
- the imaging compositor's skip reason carries the template download's
  ACTUAL error when one exists — a failed download and a recipe with no
  template are different situations (a field misdiagnosis blamed Skip
  Aligner for a DNS failure).

Run: uv run python tests/test_fetch_ladder.py
"""

import base64
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli  # noqa: E402
from core.output import RunOutput  # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
    "AAAABQABh6FO1AAAAABJRU5ErkJggg==")


class _Resp:
    def __init__(self, ok=True, status=200, body=PNG, ctype="image/png"):
        self.ok, self.status = ok, status
        self._body, self._ctype = body, ctype
        self.headers = {"content-type": ctype}

    def body(self):
        return self._body


class _Request:
    def __init__(self, script):
        self.script = list(script)   # exceptions or _Resp per attempt
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        step = self.script.pop(0) if self.script else RuntimeError("exhausted")
        if isinstance(step, Exception):
            raise step
        return step


class _Page:
    def __init__(self, script, page_fetch=None, canvas=None):
        self.request = _Request(script)
        self.page_fetch = page_fetch      # dict or Exception or None
        self.canvas = canvas              # data URL or None
        self.url = "http://cam.local/x"
        self.waits: list = []
        self.evals: list = []

    def wait_for_timeout(self, ms):
        self.waits.append(ms)

    def evaluate(self, js, arg=None):
        self.evals.append(js)
        if js is cli._PAGE_FETCH_JS:
            if isinstance(self.page_fetch, Exception):
                raise self.page_fetch
            return self.page_fetch
        if js is cli._IMG_CANVAS_JS:
            return self.canvas
        return None


class _Browser:
    def __init__(self, **kw):
        self.page = _Page(**kw)


LAYER = {"tag": "img", "src": "/edge/files/t.jpg", "idx": 0}
DNS = RuntimeError("getaddrinfo ENOTFOUND cam.local")


def main() -> int:
    failures = []

    # ---- rung 1: network errors retried with backoff, then success ----
    b = _Browser(script=[DNS, DNS, _Resp()])
    out = cli._fetch_layer(b, dict(LAYER))
    if out.get("method") != "img_src" or out.get("content") != PNG:
        failures.append(f"retry-then-success: {out.get('method')}")
    if b.page.request.calls != 3 or b.page.waits != [2000, 5000]:
        failures.append(f"retry/backoff wrong: calls={b.page.request.calls} waits={b.page.waits}")

    # ---- non-network error: no retry, down-ladder ----
    b = _Browser(script=[ValueError("boom")],
                 page_fetch={"b64": base64.b64encode(PNG).decode(), "type": "image/png"})
    out = cli._fetch_layer(b, dict(LAYER))
    if b.page.request.calls != 1:
        failures.append(f"non-network error was retried: {b.page.request.calls}")
    if out.get("method") != "img_src_page_fetch" or out.get("content") != PNG:
        failures.append(f"page-fetch rung failed: {out.get('method')}")
    if "boom" not in out.get("source_error", ""):
        failures.append("direct-fetch failure not recorded as source_error")

    # ---- HTTP 404: not transient, no retry, down-ladder ----
    b = _Browser(script=[_Resp(ok=False, status=404)],
                 page_fetch={"error": "HTTP 404"}, canvas="data:image/png;base64,"
                 + base64.b64encode(PNG).decode())
    out = cli._fetch_layer(b, dict(LAYER))
    if b.page.request.calls != 1:
        failures.append(f"HTTP status was retried: {b.page.request.calls}")
    if out.get("method") != "img_canvas_export" or out.get("content") != PNG:
        failures.append(f"canvas rung failed: {out.get('method')}")

    # ---- everything fails: joined errors, no raise ----
    b = _Browser(script=[DNS, DNS, DNS], page_fetch=RuntimeError("cdp gone"),
                 canvas=None)
    try:
        out = cli._fetch_layer(b, dict(LAYER))
    except Exception as e:
        failures.append(f"ladder raised: {e}")
        out = {}
    if "content" in out or "ENOTFOUND" not in out.get("error", "") \
            or "canvas export" not in out.get("error", ""):
        failures.append(f"total failure not recorded: {out.get('error')!r}")

    # ---- data: URLs never touch the ladder ----
    b = _Browser(script=[DNS])
    out = cli._fetch_layer(b, {"tag": "img", "idx": 0,
                               "src": "data:image/png;base64,"
                                      + base64.b64encode(PNG).decode()})
    if out.get("content") != PNG or b.page.request.calls != 0:
        failures.append("data: URL went through the ladder")

    # ---- the 0.25.7 bug class: no raw newline in quoted JS literals ----
    for name in ("_PAGE_FETCH_JS", "_IMG_CANVAS_JS"):
        js = getattr(cli, name)
        for q in ('"', "'"):
            for lit in re.findall(q + r"[^" + q + r"]*" + q, js):
                if "\n" in lit.replace("\\n", ""):
                    failures.append(f"{name}: raw newline in literal {lit[:30]!r}")

    # ---- compositor reason carries the download's actual error ----
    with tempfile.TemporaryDirectory() as td:
        out_dir = RunOutput(Path(td))
        shot = Path(td) / "deliverables" / "screenshots" / "02.png"
        shot.parent.mkdir(parents=True)
        shot.write_bytes(PNG)
        manifest = {"steps": [{"id": "imaging_setup",
                               "screenshot": "deliverables/screenshots/02.png"}]}
        meta = {"imaging_setup_img_bbox": {"x": 0, "y": 0, "width": 1, "height": 1},
                "template_image_main_image": {
                    "error": "getaddrinfo ENOTFOUND cam.local"}}
        rec = cli.compose_imaging_with_template(out_dir, meta, manifest)
        if "download failed" not in rec.get("reason", "") \
                or "ENOTFOUND" not in rec.get("reason", ""):
            failures.append(f"reason hides the download error: {rec}")
        meta["template_image_main_image"] = {}
        rec = cli.compose_imaging_with_template(out_dir, meta, manifest)
        if "none may exist" not in rec.get("reason", ""):
            failures.append(f"no-template reason unqualified: {rec}")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL FETCH-LADDER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
