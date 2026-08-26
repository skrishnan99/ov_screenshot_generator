"""OV camera screenshot generator.

Usage:
  uv run python cli.py --url http://<camera-host>/recipes --recipe "<approximate name>" [--headed] [--force-agent]

Flow per step: replay the cached trace for this camera's UI version if one
exists; otherwise (or on any replay failure) run the Claude navigator agent and
record a fresh trace. Every step's postcondition is validated deterministically
before its screenshot is taken.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import yaml

from core import llm, paths
from core import trace as trace_store
from core.browser import Browser
from core.describer import (
    describe_io_rules,
    describe_node_red,
    describe_screenshot,
    poll_image_loaded,
    poll_table_loaded,
)
from core.navigator import run_step_auto as run_step
from core.output import RunOutput
from core.resolver import (
    canonicalize_fact_subject,
    extract_model_stats,
    list_model_settings,
    list_models,
    list_training_reports,
    resolve_recipe,
)
from core.trace import _stable_snapshot
from core.version import detect_ui_version, detect_variant

ROOT = Path(__file__).resolve().parent

# Vision descriptions are independent calls; this caps their concurrency
# (modest, to stay friendly to subscription rate limits).
# Vision descriptions are independent, I/O-bound API calls, so concurrency is
# bounded by the provider rather than the machine. 4 left most of the window
# idle on an 18-screenshot run; 8 roughly halves that phase. Tunable because
# the ceiling is really the account's rate limit, not anything we control.
DESCRIBE_WORKERS = max(1, int(os.environ.get("SG_DESCRIBE_WORKERS", "8")))


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unnamed"


BBOX_PROBE_JS = """
() => {
  const cands = [];
  for (const el of document.querySelectorAll('canvas, img, video')) {
    const r = el.getBoundingClientRect();
    const s = window.getComputedStyle(el);
    if (r.width < 50 || r.height < 50 || s.visibility === 'hidden' || s.display === 'none') continue;
    cands.push({
      x: r.x + window.scrollX, y: r.y + window.scrollY,
      width: r.width, height: r.height,
      tag: el.tagName.toLowerCase(),
    });
  }
  cands.sort((a, b) => b.width * b.height - a.width * a.height);
  return cands;
}
"""


def main_image_bbox(browser, png_bytes: bytes) -> dict | None:
    """Pixel-exact bbox of the page's main image area (largest visible
    canvas/img/video — a semantic invariant of these viewer screens, not a
    selector). Coordinates are page pixels, which map 1:1 onto our
    device-scale-factor-1 full-page screenshots."""
    import struct

    shot_w, shot_h = struct.unpack(">II", png_bytes[16:24])
    cands = browser.page.evaluate(BBOX_PROBE_JS)
    if not cands:
        return None
    b = cands[0]
    if b["width"] * b["height"] < 0.05 * shot_w * shot_h:
        return None  # nothing viewer-sized on this page
    return {
        "x": round(b["x"]),
        "y": round(b["y"]),
        "width": round(b["width"]),
        "height": round(b["height"]),
        "element": b["tag"],
        "screenshot": {"width": shot_w, "height": shot_h},
        "normalized": {
            "x": round(b["x"] / shot_w, 4),
            "y": round(b["y"] / shot_h, 4),
            "width": round(b["width"] / shot_w, 4),
            "height": round(b["height"] / shot_h, 4),
        },
    }


MAIN_IMAGE_PROBE_JS = """
() => {
  document.querySelectorAll('[data-sg-layer]').forEach(el => el.removeAttribute('data-sg-layer'));
  const cands = [];
  for (const el of document.querySelectorAll('canvas, img')) {
    const r = el.getBoundingClientRect();
    const s = window.getComputedStyle(el);
    if (r.width < 50 || r.height < 50 || s.visibility === 'hidden' || s.display === 'none') continue;
    cands.push({el, area: r.width * r.height});
  }
  if (!cands.length) return null;
  // Viewers stack same-sized layers (photo canvas under an annotation canvas):
  // consider every element at least half the largest area a candidate layer.
  const maxArea = Math.max(...cands.map(c => c.area));
  const scored = [];
  for (const c of cands.filter(c => c.area >= maxArea * 0.5)) {
    const tag = c.el.tagName.toLowerCase();
    let variance = null, readable = true;
    if (tag === 'canvas') {
      try {
        const off = document.createElement('canvas');
        off.width = 32; off.height = 32;
        const ctx = off.getContext('2d');
        ctx.drawImage(c.el, 0, 0, 32, 32);
        const d = ctx.getImageData(0, 0, 32, 32).data;
        let sum = 0, sum2 = 0;
        const n = d.length / 4;
        for (let i = 0; i < d.length; i += 4) {
          const v = (d[i] + d[i + 1] + d[i + 2]) / 3;
          sum += v; sum2 += v * v;
        }
        const mean = sum / n;
        variance = Math.round(sum2 / n - mean * mean);
      } catch (e) { readable = false; }
    }
    scored.push({
      c, tag, variance, readable,
      src: tag === 'img' ? (c.el.currentSrc || c.el.src || null) : null,
      nativeW: tag === 'img' ? c.el.naturalWidth : c.el.width,
      nativeH: tag === 'img' ? c.el.naturalHeight : c.el.height,
    });
  }
  // A real photograph has high pixel variance; a flat annotation overlay near zero.
  let best = scored.find(x => x.tag === 'img' && x.src);
  if (!best) {
    const canvases = scored.filter(x => x.tag === 'canvas' && x.variance !== null);
    canvases.sort((a, b) => b.variance - a.variance);
    best = canvases[0] || scored[0];
  }
  scored.forEach((x, i) => x.c.el.setAttribute('data-sg-layer', String(i)));
  return {
    best: scored.indexOf(best),
    layers: scored.map((x, i) => ({
      idx: i, tag: x.tag, variance: x.variance, readable: x.readable,
      src: x.src, nativeW: x.nativeW, nativeH: x.nativeH,
    })),
  };
}
"""

CANVAS_EXPORT_JS = """
(idx) => document.querySelector('[data-sg-layer="' + idx + '"]').toDataURL('image/png')
"""

COMPOSITE_JS = """
() => {
  const els = [...document.querySelectorAll('[data-sg-layer]')]
    .sort((a, b) => (+a.getAttribute('data-sg-layer')) - (+b.getAttribute('data-sg-layer')));
  if (!els.length) return null;
  // Native size of the largest layer; others are scaled onto it, in DOM
  // (stacking) order — the same compositing the UI itself performs.
  let W = 0, H = 0;
  for (const el of els) {
    const w = el.tagName === 'IMG' ? el.naturalWidth : el.width;
    const h = el.tagName === 'IMG' ? el.naturalHeight : el.height;
    if (w * h > W * H) { W = w; H = h; }
  }
  const off = document.createElement('canvas');
  off.width = W; off.height = H;
  const ctx = off.getContext('2d');
  for (const el of els) ctx.drawImage(el, 0, 0, W, H);
  return off.toDataURL('image/png');
}
"""


# Native-resolution viewer images run to several MB, and camera links
# (Tailscale/VPN) can be slow. The browser context's 10s default timeout
# ALSO governs page.request — a context-bound API request context inherits
# it — and a 6 MB template image timed out under it in the field. The
# fetch gets its own explicit budget instead.
IMAGE_FETCH_TIMEOUT_MS = 120_000


# Fetch resilience. The request context is a SEPARATE network stack from
# the browser page: a Node-side client doing a fresh getaddrinfo per call,
# while Chromium rides warm connections and its own DNS cache. On Tailscale
# links a cold lookup can transiently fail (a field run lost the template
# download to getaddrinfo ENOTFOUND while the page rendered the same URL
# fine). The ladder's rungs each use a MORE-ALIVE path:
#   1. request-context fetch, retried on network-level errors
#   2. in-page fetch (Chromium's stack — the one provably working)
#   3. for <img> layers: canvas-export the already-decoded element
_FETCH_RETRIES = 2
_FETCH_BACKOFF_MS = (2000, 5000)
_NETWORK_ERROR_MARKERS = (
    "enotfound", "econnrefused", "econnreset", "eai_again", "etimedout",
    "timeout", "timed out", "err_name_not_resolved", "socket hang up",
    "network", "getaddrinfo",
)


def _is_network_error(err) -> bool:
    return any(m in str(err).lower() for m in _NETWORK_ERROR_MARKERS)


# In-page fetch: same-origin, same session, Chromium's resolver. Base64 is
# chunked to stay under argument limits for multi-MB images.
_PAGE_FETCH_JS = r"""
async (src) => {
  const r = await fetch(src, {credentials: 'include'});
  if (!r.ok) return {error: 'HTTP ' + r.status};
  const bytes = new Uint8Array(await r.arrayBuffer());
  let bin = '';
  const chunk = 32768;
  for (let i = 0; i < bytes.length; i += chunk)
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  return {b64: btoa(bin), type: r.headers.get('content-type') || ''};
}
"""

# Last resort for <img> layers: the decoded pixels are already in the page.
# Pixel-identical to what the UI shows; re-encoded as PNG (bigger beats
# missing). Same-origin, so the canvas is not tainted.
_IMG_CANVAS_JS = r"""
(idx) => {
  const el = document.querySelector('[data-sg-layer="' + idx + '"]');
  if (!el || el.tagName !== 'IMG' || !el.naturalWidth) return null;
  const c = document.createElement('canvas');
  c.width = el.naturalWidth;
  c.height = el.naturalHeight;
  c.getContext('2d').drawImage(el, 0, 0);
  return c.toDataURL('image/png');
}
"""


def _fetch_layer(browser, layer: dict) -> dict:
    """Fetch one viewer layer's bytes (img source fetch or canvas bitmap
    export). Returns {content, ext, method, source_url?} or {error}."""
    import base64 as b64
    from urllib.parse import urljoin

    out: dict = {}
    try:
        if layer["tag"] == "img" and layer["src"]:
            src = layer["src"]
            out["source_url"] = src
            if src.startswith("data:"):
                header, data = src.split(",", 1)
                out["content"] = b64.standard_b64decode(data)
                out["ext"] = ".png" if "png" in header else ".jpg"
            else:
                url = urljoin(browser.page.url, src)
                errors: list[str] = []
                # rung 1: request context, retried on network-level errors
                for attempt in range(1 + _FETCH_RETRIES):
                    try:
                        resp = browser.page.request.get(
                            url, timeout=IMAGE_FETCH_TIMEOUT_MS)
                        if resp.ok:
                            out["content"] = resp.body()
                            ctype = resp.headers.get("content-type", "")
                            out["ext"] = (".png" if "png" in ctype
                                          else ".jpg" if "jpe" in ctype or "jpg" in ctype
                                          else ".png")
                            out["method"] = "img_src"
                            break
                        errors.append(f"HTTP {resp.status}")
                        break  # an HTTP status is not transient; next rung
                    except Exception as e:
                        errors.append(str(e)[:160])
                        if not _is_network_error(e) or attempt == _FETCH_RETRIES:
                            break
                        browser.page.wait_for_timeout(
                            _FETCH_BACKOFF_MS[min(attempt, len(_FETCH_BACKOFF_MS) - 1)])
                # rung 2: in-page fetch on Chromium's own network stack
                if "content" not in out:
                    try:
                        got = browser.page.evaluate(_PAGE_FETCH_JS, src)
                        if got and got.get("b64"):
                            out["content"] = b64.standard_b64decode(got["b64"])
                            ctype = got.get("type", "")
                            out["ext"] = (".png" if "png" in ctype
                                          else ".jpg" if "jpe" in ctype or "jpg" in ctype
                                          else ".png")
                            out["method"] = "img_src_page_fetch"
                        else:
                            errors.append(f"page fetch: {(got or {}).get('error', 'no data')}")
                    except Exception as e:
                        errors.append(f"page fetch: {str(e)[:160]}")
                # rung 3: export the already-decoded element's pixels
                if "content" not in out:
                    try:
                        data_url = browser.page.evaluate(_IMG_CANVAS_JS, layer["idx"])
                        if data_url:
                            out["content"] = b64.standard_b64decode(
                                data_url.split(",", 1)[1])
                            out["ext"] = ".png"
                            out["method"] = "img_canvas_export"
                        else:
                            errors.append("canvas export: element not exportable")
                    except Exception as e:
                        errors.append(f"canvas export: {str(e)[:160]}")
                if "content" not in out:
                    out["error"] = "; ".join(errors)[:400]
                    return out
                if out["method"] != "img_src":
                    # a fallback rung served this — visible, never silent
                    out["source_error"] = "; ".join(errors)[:200]
                    print(f"  note: layer fetched via {out['method']} "
                          f"(direct fetch failed: {errors[0][:80]})")
            if "method" not in out:
                out["method"] = "img_src"
        else:
            # Throws on a tainted canvas (cross-origin content) — reported, not guessed.
            data_url = browser.page.evaluate(CANVAS_EXPORT_JS, layer["idx"])
            out["content"] = b64.standard_b64decode(data_url.split(",", 1)[1])
            out["ext"] = ".png"
            out["method"] = "canvas_export"
    except Exception as e:
        out["error"] = str(e)
    return out


def download_main_image(browser, out: RunOutput, base_name: str, step_id: str) -> dict:
    """Save the images shown in the page's main viewer at native resolution:
    the photo layer as <base>_raw.* (deliverable), every other stacked layer
    as <base>_overlay*.png (archive), and — when layered — the flattened
    <base>_composite.png (deliverable). File paths in the returned metadata
    are run-dir-relative."""
    probe = browser.page.evaluate(MAIN_IMAGE_PROBE_JS)
    if not probe:
        return {"method": None, "error": "no canvas/img viewer element found"}
    layers = probe["layers"]
    best = layers[probe["best"]]

    info = {
        "element": best["tag"],
        "native_width": best["nativeW"],
        "native_height": best["nativeH"],
        "layer_variance": best["variance"],
        "layers": [
            {k: l[k] for k in ("tag", "variance", "readable")} for l in layers
        ],
    }
    raw = _fetch_layer(browser, best)
    info["method"] = raw.get("method")
    for k in ("source_url", "error"):
        if k in raw:
            info[k] = raw[k]
    if "content" in raw:
        dest = out.save(
            f"{base_name}_raw{raw['ext']}", raw["content"],
            kind="image", role="deliverable", step=step_id, item="raw viewer image",
        )
        info["file"] = out.rel(dest)

    overlays = []
    n = 0
    for layer in layers:
        if layer["idx"] == best["idx"]:
            continue
        n += 1
        suffix = "_overlay" if n == 1 else f"_overlay{n}"
        fetched = _fetch_layer(browser, layer)
        entry = {
            "tag": layer["tag"],
            "variance": layer["variance"],
            "native_width": layer["nativeW"],
            "native_height": layer["nativeH"],
        }
        for k in ("method", "source_url", "error"):
            if k in fetched:
                entry[k] = fetched[k]
        if "content" in fetched:
            dest = out.save(
                f"{base_name}{suffix}{fetched['ext']}", fetched["content"],
                kind="image", role="archive", step=step_id, item="viewer overlay layer",
            )
            entry["file"] = out.rel(dest)
        overlays.append(entry)
    if overlays:
        info["overlays"] = overlays
        # Also save the layers flattened in stacking order — what the UI shows.
        import base64 as b64

        try:
            data_url = browser.page.evaluate(COMPOSITE_JS)
            dest = out.save(
                f"{base_name}_composite.png",
                b64.standard_b64decode(data_url.split(",", 1)[1]),
                kind="image", role="deliverable", step=step_id,
                item="viewer layers flattened (as shown in UI)",
            )
            info["composite"] = {"file": out.rel(dest)}
        except Exception as e:
            info["composite"] = {"error": str(e)}
    return info


def compose_imaging_with_template(out: RunOutput, meta: dict, manifest: dict) -> dict | None:
    """Make the composited view the imaging step's PRIMARY deliverable.

    The imaging screen is a settings page: its own viewer is often empty,
    because whether a live picture is present depends on trigger mode. The
    aligner step downloads the template image at native resolution and we know
    the imaging viewer's exact pixel bbox, so the two compose into the screen
    an engineer expects to see.

    The composite is written OVER the plain capture, at the same path and
    keeping the same manifest entry, so every downstream consumer — the deck's
    ``{step: imaging_setup, kind: screenshot}`` selector, the description
    queue, the matcher's catalog — picks it up with no extra wiring and no
    chance of the two drifting apart. The plain capture is preserved first,
    beside it under images/ as ``<stem>_plain.png``.

    A BLANK composite is still the right deliverable: an empty viewer is the
    recipe's true state (alignment disabled, or no capture triggered), and the
    report should show that. Emptiness is never a reason to skip. Only an
    inability to build the composite at all leaves the plain capture primary,
    and that is reported rather than silent.

    Returns a record that always says which happened via ``composited``.
    """
    step = next(
        (s for s in manifest.get("steps", []) if s.get("id") == "imaging_setup"), None
    )
    shot_name = (step or {}).get("screenshot")
    if not shot_name:
        return None  # the imaging step never captured; nothing to describe
    base_path = out.run_dir / shot_name

    def unchanged(reason: str) -> dict:
        return {"file": shot_name, "composited": False, "reason": reason}

    bbox = meta.get("imaging_setup_img_bbox")
    raw_name = (meta.get("template_image_main_image") or {}).get("file")
    if not base_path.exists():
        return unchanged(f"capture missing at {shot_name}")
    if not bbox:
        return unchanged("no viewer bbox recorded for the imaging screen")
    if not raw_name:
        # Say WHY there is no template: a failed download and a recipe that
        # genuinely has no template are entirely different situations (a
        # field misdiagnosis blamed Skip Aligner for a DNS failure).
        dl_err = (meta.get("template_image_main_image") or {}).get("error")
        if dl_err:
            return unchanged(
                f"template image download failed: {str(dl_err)[:160]}")
        return unchanged("no template image was downloaded by the aligner "
                         "step (none may exist for this recipe)")
    raw_path = out.run_dir / raw_name
    if not raw_path.exists():
        return unchanged(f"template image missing at {raw_name}")

    import io

    from PIL import Image

    with Image.open(base_path) as im:
        base = im.convert("RGBA")
    with Image.open(raw_path) as im:
        raw = im.convert("RGBA").resize((bbox["width"], bbox["height"]), Image.LANCZOS)
    # alpha_composite (not paste): transparent raw regions show the screenshot
    # beneath instead of rendering black.
    base.alpha_composite(raw, (bbox["x"], bbox["y"]))
    buf = io.BytesIO()
    base.convert("RGB").save(buf, format="PNG")
    composite = buf.getvalue()

    # Preserve the plain capture BEFORE overwriting, so a failure here can
    # never leave the run without one of the two versions.
    plain = out.save(
        f"{base_path.stem}_plain.png", base_path.read_bytes(),
        kind="image", role="deliverable", step="imaging_setup",
        item="imaging screen as captured, before the template image was composited in",
    )
    # Written in place: the existing manifest entry keeps pointing at it, so no
    # duplicate asset appears in the pool for the matcher to choose between.
    base_path.write_bytes(composite)
    for asset in out.assets:
        if asset.get("path") == shot_name:
            asset["item"] = "imaging screen with the template image composited into its viewer"

    return {
        "file": shot_name,
        "plain": out.rel(plain),
        "source": raw_name,
        "bbox": {k: bbox[k] for k in ("x", "y", "width", "height")},
        "composited": True,
    }


def goto_checked(browser, origin: str, url: str):
    """Navigate, retrying once via the origin if the app's transient
    permission-error page shows up (its session can be slow to establish)."""
    browser.goto(url)
    for _ in range(2):
        browser.page.wait_for_timeout(1500)
        if "You do not have permission" not in browser.page_text(4000):
            return
        print("  permission page shown; re-establishing session via origin")
        browser.goto(origin)
        browser.goto(url)


def _click_model(browser, entry_text: str) -> bool:
    for _ in range(3):
        browser.snapshot()
        cands = [it for it in browser.last_items.values() if it["text"] == entry_text]
        if len(cands) == 1:
            return not browser.click(cands[0]["ref"]).startswith("Error")
        browser.page.wait_for_timeout(1500)
    return False


def _click_scoped(browser, entry_text: str, scope_hint: str) -> bool:
    """Click the element with this exact text; when several match (e.g. one
    'View' per model row), pick the one whose row context mentions scope_hint."""
    for _ in range(3):
        browser.snapshot()
        cands = [it for it in browser.last_items.values() if it["text"] == entry_text]
        if len(cands) > 1 and scope_hint:
            scoped = [
                it for it in cands if scope_hint.lower() in it.get("ctx", "").lower()
            ]
            if scoped:
                cands = scoped
        if len(cands) == 1:
            return not browser.click(cands[0]["ref"]).startswith("Error")
        browser.page.wait_for_timeout(1500)
    return False




def _envelope_entry(meta: dict, name: str, model_type: str) -> dict | None:
    """Match an enumerated model to its meta["models"] entry. Different
    enumerators sometimes format the same model's name differently (e.g. with
    the type appended), so after an exact slug match, fall back to slug
    containment either way — accepted only when unambiguous and type-compatible."""
    slug, typ = slugify(name), slugify(model_type or "")

    def type_ok(e):
        et = slugify(e.get("type") or "")
        return not et or not typ or et in typ or typ in et

    entries = [e for e in meta.get("models", []) if type_ok(e)]
    for e in entries:
        if e.get("slug") == slug:
            return e
    hits = [e for e in entries if e.get("slug") and (e["slug"] in slug or slug in e["slug"])]
    return hits[0] if len(hits) == 1 else None


# A block page's class panel only needs re-reading while scrolling actually
# changes its text (virtualized lists); each pass costs one structured call.
MAX_STATS_SCROLLS = 6


def _merge_stats(agg: dict, parsed: dict) -> None:
    """Fold one extraction pass into the accumulator. Classes key on
    (roi, token-or-label) so overlapping scroll windows dedupe; a key seen
    twice keeps the larger count, since a partially mounted row can only
    under-read. Entries without a usable identity or count are dropped."""
    total = parsed.get("total_captures")
    if isinstance(total, int) and total >= 0:
        if agg["total_captures"] is None or total > agg["total_captures"]:
            agg["total_captures"] = total
    for c in parsed.get("classes", []) or []:
        try:
            n = int(c.get("labelled_images"))
        except (TypeError, ValueError):
            continue
        if n < 0:
            continue
        roi = str(c.get("roi", "")).strip()
        label = str(c.get("label", "")).strip()
        token = str(c.get("class_token", "")).strip()
        if not (label or token):
            continue
        key = (roi.lower(), (token or label).lower())
        prev = agg["classes"].get(key)
        if prev is None or n > prev["labelled_images"]:
            agg["classes"][key] = {
                "roi": roi,
                "label": label,
                "class_token": token,
                "labelled_images": n,
            }


def harvest_model_stats(browser, model: dict, meta: dict, source: str) -> None:
    """Read the block page's labelling stats for ONE model: the total capture
    count ("Source Capture: n of TOTAL") and every class bar's labelled-image
    count from the class panel. The panel's initial view (BEFORE any
    "Previous" click swaps it to the annotation state) lists groups for all
    of the block's models, attributed by heading — extraction filters to
    this model.

    DOM text first: innerText includes rows scrolled out of view inside a
    scrollable panel, so the common case is one read and no scrolling. The
    scroll loop exists for virtualized lists, and only pays for another
    extraction when scrolling actually changed the text.

    Results land in meta["model_stats"][<model name>] and are mirrored into
    meta["facts"] under the roster subject, so the numbers are available to
    deck grounding with no further wiring. Enrichment only: any failure warns
    and returns — it never fails the step.
    """
    name, mtype = model.get("name", ""), model.get("type", "")
    try:
        browser.reset_panel_scroll()
        agg: dict = {"total_captures": None, "classes": {}}
        seen_texts = set()
        text = browser.page_text(20000)
        seen_texts.add(text)
        _merge_stats(agg, extract_model_stats(text, name, mtype))
        for _ in range(MAX_STATS_SCROLLS):
            if not browser.scroll_panels():
                break
            text = browser.page_text(20000)
            if text in seen_texts:
                break
            seen_texts.add(text)
            _merge_stats(agg, extract_model_stats(text, name, mtype))
        browser.reset_panel_scroll()

        classes = sorted(
            agg["classes"].values(),
            key=lambda c: (c["roi"], c["label"], c["class_token"]),
        )
        entry = {
            "type": mtype,
            "total_captures": agg["total_captures"],
            "classes": classes,
            "source": source,
        }
        if not classes:
            entry["note"] = "no class bars found on the block page"
        meta.setdefault("model_stats", {})[name] = entry

        facts = meta.setdefault("facts", [])
        if agg["total_captures"] is not None:
            facts.append({
                "subject": f"model: {name}",
                "property": "total_captures",
                "value": str(agg["total_captures"]),
                "source": source,
            })
        for c in classes:
            ident = c["class_token"] or " ".join(
                x for x in (c["roi"], c["label"]) if x
            )
            facts.append({
                "subject": f"model: {name}",
                "property": f"labelled_images {ident}",
                "value": str(c["labelled_images"]),
                "source": source,
            })
        total_str = (
            str(agg["total_captures"]) if agg["total_captures"] is not None else "?"
        )
        print(
            f"  stats for \"{name}\": {total_str} captures, "
            f"{len(classes)} class bar(s), {len(seen_texts)} read(s)"
        )
    except Exception as e:
        print(f"  warning: stats for \"{name}\" not captured: {e}")


def _node_red_frame(browser):
    """The iframe hosting the Node-RED editor, identified by its workspace
    element; None when no frame carries one."""
    for frame in browser.page.frames:
        if frame is browser.page.main_frame:
            continue
        try:
            if frame.query_selector("#red-ui-workspace") or frame.query_selector(
                "#workspace"
            ):
                return frame
        except Exception:
            continue
    return None


# Node-RED's panels cover parts of the flow in a capture. Each entry:
# (label, selectors old+new Node-RED, editor action, keyboard fallback).
# Both controls are TOGGLES — the visibility check before firing is what
# guarantees a closed panel is never accidentally opened.
NODE_RED_PANELS = (
    ("palette", ("#red-ui-palette", "#palette"),
     "core:toggle-palette", "ControlOrMeta+p"),
    ("sidebar", ("#red-ui-sidebar", "#sidebar"),
     "core:toggle-sidebar", "ControlOrMeta+Space"),
)


def close_node_red_panels(browser) -> None:
    """Close Node-RED's palette (left) and sidebar (right) inside the flow
    iframe, so a capture shows the whole flow rather than the editor chrome.

    Each panel is toggled ONLY when currently visible, preferably via the
    editor's own action API (deterministic, needs no keyboard focus), with
    the documented keyboard shortcut as fallback. Cosmetic only: any failure
    warns and the capture proceeds with the page as it is.
    """
    frame = _node_red_frame(browser)
    if frame is None:
        print("  warning: node-red frame not found; capturing panels as-is")
        return
    for label, selectors, action, combo in NODE_RED_PANELS:
        try:
            el = None
            for sel in selectors:
                el = frame.query_selector(sel)
                if el is not None:
                    break
            if el is None or not el.is_visible():
                continue
            try:
                frame.evaluate(f"() => RED.actions.invoke('{action}')")
            except Exception:
                # Older editor or RED not exposed: fall back to the shortcut.
                frame.press("body", combo)
            browser.page.wait_for_timeout(600)
            if el.is_visible():
                print(f"  warning: node-red {label} still open after toggle")
            else:
                print(f"  node-red {label} closed for capture")
        except Exception as e:
            print(f"  warning: node-red {label} check failed: {e}")


# The IO Logic tab has two modes. BASIC mode shows the "Pass/Fail & IO
# Logic" rules layout (Classification/Segmentation rule builders beside a
# capture preview) instead of the embedded Node-RED editor — a fully valid
# IO page with no flow to export. Detection is text-shaped, never
# selector-shaped: the flow iframe's absence plus the page's own headings.


def _is_basic_io_page(browser) -> bool:
    try:
        if _node_red_frame(browser) is not None:
            return False
        text = browser.page.evaluate("document.body.innerText") or ""
        # OV80i basic layout
        if "Pass/Fail & IO Logic" in text and (
            "Classification Rules" in text
            or "Segmentation Rules" in text
            or "Advanced Mode" in text
        ):
            return True
        # OV20i basic layout: "Basic IO Block" heading with the numbered
        # Rules / Overall result / Digital Outputs sections.
        return "Basic IO Block" in text and (
            "Overall result" in text
            or "Digital Outputs" in text
            or "Advanced Mode (Node-RED)" in text
        )
    except Exception:
        return False


# innerText misses INPUT values — numeric rule thresholds live in input
# boxes (a live harvest lost a "<= 50" threshold this way) — so visible
# input values are appended in page order. RAW string, deliberately: a
# non-raw "\n" here becomes a REAL newline inside the JS double-quoted
# literal, which is a JS SyntaxError — a field run hit exactly that
# ("Invalid or unexpected token") and lost the whole transcript, masked
# by the warn-and-continue contract.
_IO_RULES_TEXT_JS = r"""
() => {
  const vals = [...document.querySelectorAll('input, textarea')]
    .filter(e => e.getBoundingClientRect().width && (e.value || '').trim())
    .map(e => e.value.trim());
  return document.body.innerText +
    (vals.length ? "\n\nVISIBLE INPUT VALUES (in page order): "
                   + vals.join(", ") : "");
}
"""


def harvest_io_rules(browser, out: RunOutput, meta: dict) -> None:
    """Save the Basic-Mode rules page's innerText VERBATIM as
    data/io_rules.txt — the auditable stand-in for the exported flow JSON.
    Reading is text-to-model in this codebase; selectors are for clicking.
    The analysis phase later turns this into node_red_description.md +
    io_logic facts via describe_io_rules, so everything downstream of the
    Advanced-Mode path works unchanged. Enrichment: warns and continues,
    never fails a step."""
    try:
        text = browser.page.evaluate(_IO_RULES_TEXT_JS) or ""
        if not text.strip():
            print("  warning: io rules harvest found no page text")
            return
        dest = out.save(
            "io_rules.txt", text, kind="data", role="data",
            step="io_node_red", item="basic-mode IO rules page text",
        )
        meta["io_mode"] = "basic"
        print(f"  io rules harvested -> {out.rel(dest)}")
    except Exception as e:
        print(f"  warning: io rules harvest failed: {e}")


# Feature-promo / walkthrough modals pop over config screens on first visit
# in a fresh browser session (e.g. "Try out the New Aligner" on Template
# Image and Alignment). The UI persists nothing on dismissal, and every run
# starts a fresh browser, so the modal greets EVERY run. Detection keys on
# the dialog SHAPE ([role=dialog]), not its copy, so the next feature's
# promo is caught too. Buttons are only ever matched against this
# dismiss-flavoured vocabulary — the primary CTA ("See What's New") starts
# the walkthrough and must never be clicked.
PROMO_DISMISS_RE = re.compile(
    r"explore on my own|maybe later|not now|no thanks|skip|dismiss|got it",
    re.I,
)


def _visible_dialog(browser):
    """The overlaying dialog element if one is visible, else None."""
    for el in browser.page.query_selector_all("[role=dialog]"):
        try:
            if el.is_visible():
                return el
        except Exception:
            continue
    return None


def dismiss_promo_modal(browser) -> None:
    """Close a feature-promo / walkthrough modal overlaying the page.

    Fired only when a dialog is actually visible. Escape is the primary
    close — text-independent, and it cannot accidentally start the
    walkthrough — with a dismiss-flavoured button from the modal itself as
    fallback. Cosmetic: any failure warns and the capture proceeds with the
    page as it is; this must never fail a step.
    """
    try:
        modal = _visible_dialog(browser)
        if modal is None:
            return
        browser.page.keyboard.press("Escape")
        browser.page.wait_for_timeout(600)
        modal = _visible_dialog(browser)
        if modal is None:
            print("  promo modal closed (Escape)")
            return
        for btn in modal.query_selector_all("button"):
            label = (btn.inner_text() or "").strip()
            if label and PROMO_DISMISS_RE.search(label):
                btn.click()
                browser.page.wait_for_timeout(600)
                if _visible_dialog(browser) is None:
                    print(f"  promo modal closed ({label!r})")
                    return
                break
        print("  warning: promo modal still visible after dismiss attempts")
    except Exception as e:
        print(f"  warning: promo modal dismissal failed: {e}")


# --------------------------------------------------------------------------
# Block-page capture picking. "Previous" from live view lands on the LAST
# source capture, which is frequently a dark/blank frame (a real deck once
# shipped black block pages) — so the capture that feeds the training slide
# is SEARCHED for, not taken on faith. Preference ladder, per the spec:
#   tier 1  product photograph AND annotated  -> short-circuit, capture now
#   tier 2  product photograph, unannotated   -> best-partial candidate
#   tier 3  annotated but no real product     -> weaker candidate
#   tier 4  neither                           -> qualifies for nothing
# The judged-capture budget is capped; hitting the cap is logged, never
# silent. DOM access lives in tiny helpers so the search itself is testable
# as a pure state machine.

CAPTURE_SCAN_CAP = 25

_NAV_INPUT_JS = """
() => {
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walk.nextNode()) {
    const t = walk.currentNode.textContent || '';
    if (t.includes('Source Capture')) {
      const root = walk.currentNode.parentElement.closest('div');
      const i = root ? root.querySelector('input') : null;
      if (i) { if (!i.id) i.id = 'sg-capture-index'; return '#' + CSS.escape(i.id); }
    }
  }
  return null;
}
"""

# Evidence-first field order: `reason` leads, so the model describes what
# it sees before committing to the booleans (decide-then-justify was a
# false-positive source).
BLOCK_CAPTURE_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "product_image": {"type": "boolean"},
        "annotated": {"type": "boolean"},
    },
    "required": ["reason", "product_image", "annotated"],
    "additionalProperties": False,
}

BLOCK_CAPTURE_PROMPT = """This is the image viewer of a camera's {block_type} training Block page.{recipe_line}

Judge the CAPTURE shown in the viewer (ignore the surrounding UI panels):

1. product_image — {product_criterion}
2. annotated — are annotations drawn ON the capture: {annotation_kind}?
   {empty_note}

Answer with a one-sentence reason FIRST — describe what is actually drawn
and shown — then product_image and annotated."""


def _block_capture_prompt(block_type: str, recipe: str = "",
                          part_desc: str = "") -> str:
    """Criteria come from core.capture_criteria — shared verbatim with the
    deck's description judge so the two can never drift. With a part
    description, the ANCHORED product criterion replaces the generic one
    AT THE DECISION POINT (a preamble anchor lost to the local text: a
    judge passed an unidentifiable frame as 'plausibly' the part)."""
    from core import capture_criteria as cc

    return BLOCK_CAPTURE_PROMPT.format(
        block_type=block_type,
        recipe_line="" if part_desc else _recipe_line(recipe),
        product_criterion=(cc.anchored_product_criterion(part_desc)
                           if part_desc else cc.PRODUCT_CRITERION),
        annotation_kind=cc.annotation_criterion(block_type),
        empty_note=cc.EMPTY_OUTLINES_NOTE,
    )


def judge_block_capture(browser, block_type: str, recipe: str = "",
                        part_desc: str = "") -> dict:
    """One Haiku vision verdict on the CURRENT viewer image. Cropped to the
    viewer when the bbox probe finds it, so the judgment sees the capture
    rather than the whole page."""
    import io

    from PIL import Image

    from core import llm
    from core.llm import downscale_for_vision

    png = browser.screenshot_bytes(full_page=True)
    bbox = main_image_bbox(browser, png)
    if bbox:
        with Image.open(io.BytesIO(png)) as im:
            crop = im.crop((bbox["x"], bbox["y"],
                            bbox["x"] + bbox["width"], bbox["y"] + bbox["height"]))
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            png = buf.getvalue()
    # SONNET, not Haiku — measured 6.5s vs 14.6s per call on the
    # agent-sdk transport (session overhead dominates; Sonnet navigates
    # the image-read turn in fewer steps) AND it is the terminal verdict
    # the tier ladder ships. Availability still walks down to Haiku.
    return llm.complete(_block_capture_prompt(block_type, recipe, part_desc),
                        schema=BLOCK_CAPTURE_SCHEMA,
                        images=[downscale_for_vision(png)], max_tokens=500,
                        model=llm.SONNET)


def _capture_nav_state(browser):
    """(current_index, total, input_selector) — Nones when the navigator is
    not on the page (it only exists after leaving live view)."""
    try:
        sel = browser.page.evaluate(_NAV_INPUT_JS)
        if not sel:
            return None, None, None
        val = browser.page.evaluate(
            f"() => document.querySelector({sel!r})?.value ?? ''")
        cur = int(val) if str(val).strip().isdigit() else None
        m = _CAPTURE_TOTAL_RE.search(
            browser.page.evaluate("document.body.innerText") or "")
        total = int(m.group(1)) if m else None
        return cur, total, sel
    except Exception:
        return None, None, None


def _click_nav_button(browser, label: str) -> bool:
    for el in browser.page.query_selector_all("button"):
        try:
            if el.is_visible() and (el.inner_text() or "").strip() == label:
                el.click()
                return True
        except Exception:
            continue
    return False


def _goto_capture(browser, sel: str, idx: int) -> bool:
    """Jump straight to capture `idx` via the number input + Go."""
    try:
        browser.page.fill(sel, str(idx))
        return _click_nav_button(browser, "Go")
    except Exception:
        return False


def _next_capture(browser) -> bool:
    return _click_nav_button(browser, "Next")


def _wait_capture_loaded(browser) -> None:
    ok, msg = poll_image_loaded(browser, max_wait_s=35, interval_s=5)
    if not ok:
        print(f"  warning: capture image {msg}")


def pick_annotated_capture(browser, block_type: str,
                           cap: int = CAPTURE_SCAN_CAP,
                           recipe: str = "", part_desc: str = "") -> dict:
    """Position the block page's viewer on the best available capture and
    say how it was chosen. The caller screenshots afterwards.

    Judges the current capture (the last one, where Previous lands) first;
    a product+annotated verdict short-circuits immediately. Otherwise
    cycles capture 1, 2, ... maintaining the best-tier index seen
    (first-seen wins ties), and jumps back to it at the end. Best-effort
    like every capture hook: any failure leaves the viewer where it is and
    the capture proceeds — never fails the step.
    """
    rec: dict = {"judged": [], "chosen": None, "tier": None,
                 "anchored": bool(part_desc)}
    if part_desc:
        rec["part_anchor"] = part_desc[:160]
    else:
        # Same loud degradation note as the library pick: the generic
        # criterion has passed featureless frames before.
        print("  capture pick: NO part description — judging with the "
              "generic product criterion (run the template step for "
              "anchored picks)")
    try:
        cur, total, sel = _capture_nav_state(browser)

        def judge(idx):
            v = judge_block_capture(browser, block_type, recipe=recipe,
                                    part_desc=part_desc)
            tier = (1 if v.get("product_image") and v.get("annotated")
                    else 2 if v.get("product_image")
                    else 3 if v.get("annotated") else 4)
            rec["judged"].append({"index": idx, "tier": tier,
                                  "reason": str(v.get("reason", ""))[:300]})
            return tier

        tier = judge(cur)
        if tier == 1:
            rec["chosen"], rec["tier"] = cur, 1
            print(f"  capture pick: current capture ({cur}) is product+annotated")
            return rec
        best = (tier, cur) if tier in (2, 3) else (5, None)

        if sel is None:
            rec["chosen"], rec["tier"] = cur, tier
            print("  warning: capture navigator not found; keeping current capture")
            return rec

        bound = min(total, cap + 1) if total else cap
        judged = 1
        idx = 0
        while idx < bound:
            idx += 1
            if idx == cur:
                continue  # the starting capture is already judged
            if judged >= cap:
                print(f"  capture pick: scan cap ({cap}) reached with "
                      f"{(total or '?')} captures total; stopping the search")
                break
            moved = (_goto_capture(browser, sel, idx) if idx == 1 or judged == 0
                     else _next_capture(browser))
            if not moved:
                print("  warning: capture navigation failed; stopping the search")
                break
            _wait_capture_loaded(browser)
            now, _, _ = _capture_nav_state(browser)
            if now is not None and now != idx:
                # Next drifted (e.g. wrapped); correct deterministically.
                if not _goto_capture(browser, sel, idx):
                    break
                _wait_capture_loaded(browser)
            tier = judge(idx)
            judged += 1
            if tier == 1:
                rec["chosen"], rec["tier"] = idx, 1
                print(f"  capture pick: capture {idx} is product+annotated")
                return rec
            # only tiers 2/3 are candidates — tier 4 qualifies for nothing
            if tier in (2, 3) and tier < best[0]:
                best = (tier, idx)

        if best[1] is not None:
            if best[1] != (rec["judged"][-1]["index"] if rec["judged"] else None):
                if _goto_capture(browser, sel, best[1]):
                    _wait_capture_loaded(browser)
            rec["chosen"], rec["tier"] = best[1], best[0]
            from core.capture_criteria import PICK_TIER_MEANING

            print(f"  capture pick: best partial is capture {best[1]} "
                  f"(tier {best[0]}: {PICK_TIER_MEANING[best[0]]})")
        else:
            rec["chosen"], rec["tier"] = (rec["judged"][-1]["index"]
                                          if rec["judged"] else cur), 4
            print("  capture pick: no capture met any criterion; keeping the "
                  "last one visited")
    except Exception as e:
        print(f"  warning: capture pick failed: {e}; capturing the current view")
    return rec


# --------------------------------------------------------------------------
# Block-page model-view preparation. The View All ROIs captures were the
# flow's most turn-hungry agent task: close the previous model's modal,
# fight the portal-rendered Ant model selector, reopen the modal — and the
# selector fight alone regularly blew the turn budget (the selector only
# exists in the capture-review state, options render in a portal, and
# "Model" is a strict PREFIX of "Model 3", so text matching betrays
# agents). This hook does the mechanical parts deterministically before
# the per-model agent runs, leaving it only "click View All ROIs and
# wait". Best-effort: any failure leaves the page as-is and the agent's
# goal still carries the full manual instructions.

# NB: the click target is the .ant-select CONTAINER, not its inner input —
# this non-searchable select's input sits UNDER the selection-item span and
# never receives pointer events (a 5s click timeout, live-observed; the
# mirror image of the library filter's placeholder-interception lesson).
_MODEL_SELECT_JS = """
(labelPrefix) => {
  for (const sel of document.querySelectorAll('.ant-select')) {
    if (!sel.getBoundingClientRect().width) continue;
    let node = sel;
    for (let i = 0; i < 5 && node; i++) {
      node = node.parentElement;
      const t = ((node && node.innerText) || '').trim();
      if (t.toLowerCase().startsWith(labelPrefix.toLowerCase())) {
        if (!sel.id) sel.id = 'sg-model-select';
        const cur = sel.querySelector('.ant-select-selection-item');
        return {input: '#' + CSS.escape(sel.id),
                current: cur ? cur.innerText.trim() : ''};
      }
    }
  }
  return null;
}
"""


def _escape_dialogs(browser) -> bool:
    """Close any open modal (e.g. the previous model's View All ROIs):
    Escape first (cannot misclick), then the modal's own close controls —
    the X (ant-modal-close) and an Ok/Close button — because Escape has
    been seen not to land when focus sits inside the modal. Returns True
    when no dialog remains."""
    try:
        for attempt in range(3):
            dlg = _visible_dialog(browser)
            if dlg is None:
                return True
            if attempt == 0:
                browser.page.keyboard.press("Escape")
            else:
                closed = False
                try:
                    x = dlg.query_selector("button.ant-modal-close")
                    if x is not None and x.is_visible():
                        x.click()
                        closed = True
                except Exception:
                    pass
                if not closed:
                    for btn in dlg.query_selector_all("button"):
                        if (btn.inner_text() or "").strip().lower() in ("ok", "close"):
                            btn.click()
                            break
            browser.page.wait_for_timeout(800)
        return _visible_dialog(browser) is None
    except Exception:
        return False


def _model_selector_state(browser, block_type: str):
    """(input_selector, current_value) of the page's model selector, or
    (None, None). The selector only exists in the capture-review state."""
    try:
        got = browser.page.evaluate(_MODEL_SELECT_JS, f"{block_type} model")
        if got:
            return got["input"], got["current"]
    except Exception:
        pass
    return None, None


def _click_model_option(browser, name: str) -> bool:
    """Click the portal-rendered option whose text EXACTLY equals `name` —
    equality, never substring: "Model" is a prefix of "Model 3"."""
    try:
        for el in browser.page.query_selector_all(".ant-select-item-option"):
            if el.is_visible() and (el.inner_text() or "").strip() == name:
                el.click()
                return True
    except Exception:
        pass
    return False


def prepare_model_view(browser, block_type: str, model_name: str) -> bool:
    """Deterministically stage the block page for a per-model View All
    ROIs capture: close any open modal and set the model selector to
    `model_name`. Returns True when the selector provably shows the model;
    False leaves the agent's full manual fallback to do the work."""
    try:
        if not _escape_dialogs(browser):
            # A dialog still overlays the page — a selector click would
            # only time out against it. Bail fast; the agent knows how.
            print("  model-view prep: a modal would not close; agent will "
                  "drive the page")
            return False
        sel, current = _model_selector_state(browser, block_type)
        if sel is None:
            # Live view has no selector; Previous enters the review state.
            if _click_nav_button(browser, "Previous"):
                browser.page.wait_for_timeout(2500)
                sel, current = _model_selector_state(browser, block_type)
        if sel is None:
            print(f"  model-view prep: no {block_type} model selector found; "
                  f"agent will drive the page")
            return False
        if current == model_name:
            print(f"  model-view prep: {model_name!r} already selected")
            return True
        browser.page.click(sel, timeout=5000)
        browser.page.wait_for_timeout(800)
        if not _click_model_option(browser, model_name):
            browser.page.keyboard.press("Escape")
            print(f"  model-view prep: selector lists no option {model_name!r}; "
                  f"agent will drive the page")
            return False
        browser.page.wait_for_timeout(1500)
        _, now = _model_selector_state(browser, block_type)
        if now == model_name:
            print(f"  model-view prep: selector set to {model_name!r}")
            return True
        print(f"  model-view prep: selector shows {now!r} after selecting "
              f"{model_name!r}; agent will verify")
        return False
    except Exception as e:
        print(f"  warning: model-view prep failed: {e}; agent will drive the page")
        return False


# --------------------------------------------------------------------------
# Library capture picking. The filtered grid auto-selects the NEWEST
# capture, with no guarantee its viewer shows a real product or the AI
# inspection overlays (a real deck's overview slide shipped a black
# raw/composite pair this way). The pick runs after the recipe filter and
# BEFORE the screenshot and the main-image download, so all three describe
# the same chosen capture — which also preserves the deck's "overview pair
# agrees with the library screenshot" doctrine by construction.
#
# Search shape, per the spec: judge every thumbnail on the page in ONE
# batched vision call (thumbnails never render overlays, so they can only
# answer "real product?"); click each product-looking card and judge the
# VIEWER for product + overlay; exhaust the page before moving on. Two
# caps: pages scanned and total candidates clicked. Preference ladder on
# exhaustion mirrors the block pick: product+overlay short-circuits,
# product-no-overlay beats overlay-no-product, nothing qualifying resets
# to page 1's newest (today's exact behavior).

LIBRARY_PAGE_SCAN_CAP = 5
LIBRARY_CLICK_CAP = 10
# Per-page candidate budget: the thumbnail call FILTERS to product-bearing
# cards, the badge sort orders them (verdict-tagged > untagged > trainset,
# the model's rank within each), and only the top N get clicked, so the global
# click budget spans pages instead of drowning on page 1 (newest-first
# sorting fronts dim production triggers; the genuine part captures often
# sit pages deep).
LIBRARY_PAGE_CANDIDATES = 3

# THE card walk — the single source of card detection for the whole
# library pick. A card is a thumbnail-sized <img> whose nearest ancestor
# carries exactly one "#N" label; each card reports its id, whether the
# thumbnail has PAINTED (settle gate), its container's document-coordinate
# box (grid crop) and its container's innerText (badge ordering: PASS/FAIL
# tag, "Used for training"). Document order == grid order == recency
# (the library sorts newest first). Every consumer derives from this one
# walk — there is nothing to keep in sync.
_LIBRARY_CARD_WALK_JS = """
() => {
  const out = [];
  document.querySelectorAll('img').forEach(img => {
    const r = img.getBoundingClientRect();
    if (r.width < 40 || r.width > 400) return;
    let node = img;
    for (let i = 0; i < 6 && node; i++) {
      node = node.parentElement;
      if (node && /#\\d+/.test(node.innerText || '') &&
          (node.innerText.match(/#\\d+/g) || []).length === 1) {
        const c = node.getBoundingClientRect();
        out.push({id: (node.innerText.match(/#(\\d+)/) || [])[1],
                  painted: !!(img.complete && img.naturalWidth > 0),
                  text: node.innerText || '',
                  box: {left: c.left, top: c.top + window.scrollY,
                        right: c.right, bottom: c.bottom + window.scrollY}});
        return;
      }
    }
  });
  return out;
}
"""


def _library_cards(browser) -> list[dict]:
    """The grid's cards in grid (= recency) order; [] on any failure."""
    try:
        cards = browser.page.evaluate(_LIBRARY_CARD_WALK_JS) or []
        return [c for c in cards if str(c.get("id", "")).isdigit()]
    except Exception:
        return []


def _library_card_ids(browser) -> list[int]:
    return [int(c["id"]) for c in _library_cards(browser)]


def _library_grid_bbox(browser) -> dict | None:
    """Union of the card CONTAINERS' boxes — labels included, so a crop
    keeps the "#N" text the model needs to map thumbnails to the prompt's
    ids. None when the grid is empty."""
    boxes = [c["box"] for c in _library_cards(browser) if c.get("box")]
    if not boxes:
        return None
    box = {"left": min(b["left"] for b in boxes),
           "top": min(b["top"] for b in boxes),
           "right": max(b["right"] for b in boxes),
           "bottom": max(b["bottom"] for b in boxes)}
    if box["right"] > box["left"] and box["bottom"] > box["top"]:
        return box
    return None


# Badge groups — the SECONDARY ordering among a page's product-bearing
# thumbnails. Thumbnails never show overlays, so once the model has kept
# only cards showing THE part, the card's own text is the best available
# predictor of a full AI overlay behind it: a PASS/FAIL tag means an
# inspection ran (overlays likely); "Used for training" means a trainset
# capture (least likely). Within a group the model's rank orders; recency
# (grid position) is the final tiebreak.
BADGE_VERDICT, BADGE_NONE, BADGE_TRAINING = 0, 1, 2
_BADGE_NAMES = {BADGE_VERDICT: "verdict", BADGE_NONE: "none",
                BADGE_TRAINING: "training"}
_TRAINING_RE = re.compile(r"used\s+for\s+training", re.I)
_VERDICT_LINE_RE = re.compile(r"^\s*(PASS|FAIL)\s*$", re.M)


def _card_badge(text: str, recipe: str = "") -> tuple[int, str]:
    """(group, label) for a card's innerText. Training is checked FIRST —
    a trainset capture that also shows a verdict is still last. The recipe
    name is stripped before matching so a recipe called "...Training..."
    cannot mark every card as trainset."""
    body = text or ""
    if recipe:
        body = body.replace(recipe, " ")
    if _TRAINING_RE.search(body):
        return BADGE_TRAINING, "training"
    m = _VERDICT_LINE_RE.search(body)
    if m:
        return BADGE_VERDICT, m.group(1).lower()
    return BADGE_NONE, "none"


def _order_candidates(product_ids, cards, recipe: str = "") -> list[dict]:
    """The click order for a page: the model's product-bearing ids, sorted
    by badge group, then the MODEL'S RANK within the group, then grid
    position (recency) as the final tiebreak. The model's rank encodes
    how clearly THE part shows in the thumbnail — the signal that predicts
    a product viewer — so it must survive inside a group: a live page had
    every card PASS-tagged and recency-within-group inverted a correct
    ranking, clicking the three newest dark frames while the clearly-lit
    part sat 9th. Ids the DOM does not show are dropped, duplicates
    collapsed. Returns [{id, badge}] — the sort is stable and total, so
    the order is reproducible run to run."""
    by_id: dict[int, tuple[int, int, str]] = {}
    for idx, c in enumerate(cards):
        cid = int(c["id"])
        if cid not in by_id:
            group, label = _card_badge(c.get("text", ""), recipe)
            by_id[cid] = (group, idx, label)
    seen: set[int] = set()
    picked = []   # in model rank order
    for i in product_ids:
        try:
            i = int(i)
        except (TypeError, ValueError):
            continue
        if i in by_id and i not in seen:
            seen.add(i)
            picked.append(i)
    rank = {i: r for r, i in enumerate(picked)}
    picked.sort(key=lambda i: (by_id[i][0], rank[i], by_id[i][1]))
    return [{"id": i, "badge": by_id[i][2]} for i in picked]


LIBRARY_GRID_MAX_VIEWPORT_H = 3000
LIBRARY_GRID_CROP_PAD = 16


def _crop_to_grid(png: bytes, box: dict) -> bytes:
    """Crop the shot to the grid's bounding box (plus padding). The grid
    occupies ~625px of a 1600px-wide page — cropping away the sidebar and
    the viewer panel puts the whole grid under the vision cap in BOTH
    dimensions, so the ranker sees thumbnails at native resolution instead
    of downscaled. Degrades to the full shot on any failure."""
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(png)) as im:
            pad = LIBRARY_GRID_CROP_PAD
            left = max(0, int(box["left"]) - pad)
            top = max(0, int(box["top"]) - pad)
            right = min(im.width, int(box["right"]) + pad)
            bottom = min(im.height, int(box["bottom"]) + pad)
            if right - left < 100 or bottom - top < 100:
                return png
            crop = im.crop((left, top, right, bottom))
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            return buf.getvalue()
    except Exception as e:
        print(f"  warning: grid crop failed: {e}; sending the full shot")
        return png


def _library_grid_screenshot(browser) -> bytes:
    """Screenshot with the WHOLE capture grid in frame at native
    resolution. Two field-measured facts drive this: (1) the grid scrolls
    inside an inner panel whose height derives from the viewport, and a
    full-page screenshot stops at the DOCUMENT's height — at the default
    1000px viewport only the top ~9 of a 20-card page were in the image
    while the ranker's prompt listed all 20 ids; (2) the grid itself is
    only ~625px wide, so cropped to its bounding box the shot fits the
    vision cap in BOTH dimensions and the thumbnails reach the model
    undownscaled. So: probe the card containers' bbox, grow the viewport
    to fit its bottom edge (the panel is viewport-derived; all thumbnails
    are in the DOM and painted without scrolling — no lazy-load),
    re-measure after the re-layout, screenshot, crop. The viewport is
    restored no matter what, so the deliverable screenshot and everything
    downstream keep their normal geometry; every stage degrades toward
    the plain full-page screenshot, never fails."""
    page = browser.page
    box = _library_grid_bbox(browser)
    need = (int(box["bottom"]) + 40) if box else 0
    vp = page.viewport_size or {"width": 1600, "height": 1000}
    grown = False
    try:
        if need > vp["height"]:
            try:
                page.set_viewport_size(
                    {"width": vp["width"],
                     "height": min(need, LIBRARY_GRID_MAX_VIEWPORT_H)})
                page.wait_for_timeout(1000)  # re-layout at the taller viewport
                grown = True
                # the re-layout moves the cards: re-measure for the crop
                box = _library_grid_bbox(browser) or box
            except Exception as e:
                print(f"  warning: could not grow the viewport for the grid "
                      f"screenshot: {e}; judging the visible part")
        png = browser.screenshot_bytes(full_page=True)
        return _crop_to_grid(png, box) if box else png
    finally:
        if grown:
            try:
                page.set_viewport_size(vp)
                page.wait_for_timeout(500)
            except Exception:
                pass


_SELECTED_CAPTURE_RE = re.compile(r"Capture\s+#(\d+)\s+from", re.I)

# Two lists from one call: product_captures is the CLICK POOL — thumbnails
# in which the part is RECOGNIZABLE; plausible_captures is the reserve —
# could be the part but not recognizable at thumbnail scale (too dark,
# blurred, featureless). The search clicks the recognizable pool first
# and falls back to the reserve only when the whole scan found no
# product+overlay capture (see pick_library_capture), so a strict filter
# can never starve a genuinely dark recipe.
LIBRARY_THUMBS_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "product_captures": {"type": "array", "items": {"type": "integer"}},
        "plausible_captures": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["reason", "product_captures", "plausible_captures"],
    "additionalProperties": False,
}

LIBRARY_THUMBS_PROMPT = """This is the Library page of a camera's inspection UI: a grid of capture
cards, each labelled "#<number>" with a THUMBNAIL of that capture.{recipe_line}

The capture numbers visible on this page are: {ids}.

Sort these captures' thumbnails into two lists. Thumbnails never render
inspection overlays — judge ONLY the photograph.

product_captures — thumbnails in which {recognizable_what} is
RECOGNIZABLE: you can actually make out its features in the thumbnail
itself. A dim thumbnail in which the features are still recognizable
counts. A thumbnail too dark, blurred or featureless to recognize
anything does NOT count here, even if it could conceivably be the part
at a bad exposure. RANK these most clearly recognizable first.

plausible_captures — thumbnails that could be the part but in which it is
NOT recognizable (dark, blurred, indistinct). RANK likeliest first.

Omit thumbnails that show nothing (black, blank, grey, uniform) or
clearly show something else. Give the reason FIRST — say what the
recognizable thumbnails actually show — then the two lists."""

LIBRARY_VIEWER_PROMPT = """This is the main capture viewer of a camera's Library page.{recipe_line}

Judge the IMAGE shown in the viewer (ignore the surrounding UI panels):

1. product_image — {product_criterion}
2. overlay — {overlay_criterion}

Answer with a one-sentence reason FIRST — describe what is actually drawn
and shown — then product_image and overlay."""

LIBRARY_VIEWER_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "product_image": {"type": "boolean"},
        "overlay": {"type": "boolean"},
    },
    "required": ["reason", "product_image", "overlay"],
    "additionalProperties": False,
}


def _recipe_line(recipe: str) -> str:
    if not recipe:
        return ""
    return (f'\nThe captures belong to the inspection recipe {recipe!r} — a '
            f"photograph of the part this recipe inspects is expected. Any "
            f"real manufactured part still counts as a product image.")


def _anchor_line(recipe: str, part_desc: str = "") -> str:
    """The thumbnail prefilter's part anchor: names THE part so the
    recognizable/plausible split is about this part, not any object. The
    terminal viewer/block judges get the anchored product criterion AT
    THE DECISION POINT (capture_criteria.anchored_product_criterion).
    FEATURES over labels, same as that criterion: a template misread as an
    "automotive panel" once made this judge reject all three pages of the
    actual part's captures over the wrong class label alone."""
    if part_desc:
        return (f"\nThe part being inspected, as seen in the recipe's "
                f"template image: {part_desc}\nThe question is whether "
                f"THIS part is recognizable — possibly at a different angle "
                f"or zoom — not just any manufactured object. Match on the "
                f"description's FEATURES (shape, material, colour, "
                f"geometry); any object-class or industry guess in it is "
                f"secondary, and a mismatched class label alone is never "
                f"grounds to reject a thumbnail whose features match.")
    return _recipe_line(recipe)


def _recognizable_what(part_desc: str = "") -> str:
    """The subject of the thumbnail prompt's recognizability test."""
    return ("the part described above" if part_desc
            else "a real photograph of a physical manufactured part")


# One vision call per run, over the template step's native-res download —
# the canonical reference view of the part (what alignment anchors to).
# The description then anchors every pick judgment. part_visible is the
# escape hatch: a blank or random template (possible under Skip Aligner)
# yields NO anchor rather than a hallucinated one — the judges fall back
# to the generic criterion, never block.
PART_DESCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "part_visible": {"type": "boolean"},
    },
    "required": ["description", "part_visible"],
    "additionalProperties": False,
}

PART_DESCRIPTION_PROMPT = """This is the template image of a camera inspection recipe — the canonical
reference view of the part being inspected.{context_block}

Describe the part in 2-3 short sentences of INVARIANT features — overall
shape, material, colour, distinctive geometry (bores, holes, edges,
markings) — so a later judgment can recognise the same part at a different
angle, zoom or exposure. Describe the part itself, never image quality and
never UI elements.

Describe only what is VISIBLE. Do NOT guess what product, machine or
industry the part belongs to: an object-identity hypothesis the image alone
cannot establish poisons every judgment made against this description. When
the context above names the part, prefer its words for identity; never
invent an identity beyond it.

If the image does not clearly show a physical part (blank, uniform, a test
pattern, or unrelated content), say so and set part_visible=false."""


def _part_context_block(recipe: str, context: str) -> str:
    """The part prompt's context section: the resolved recipe name (names
    usually name the part or the inspection) and the engineer's own words
    from the run request. Interpretation aids only — the prompt keeps the
    image authoritative, and the block is absent when neither exists."""
    lines = []
    if recipe:
        lines.append(f'The recipe is named "{recipe}" — recipe names '
                     f"usually name the part or the inspection.")
    if context:
        lines.append("The engineer running this test said: "
                     + " ".join(str(context).split())[:600])
    if not lines:
        return ""
    return ("\n\nContext (use it to INTERPRET what you see; if the image "
            "contradicts it, describe what is actually visible):\n"
            + "\n".join(f"- {ln}" for ln in lines))


def describe_part_from_image(path, recipe: str = "",
                             context: str = "") -> str | None:
    """The part description for meta["part_description"], or None when the
    template shows no usable part. `recipe` and `context` (the engineer's
    own words from the run request) ground the read — a field run described
    a dryer cover panel as an "automotive panel", and the wrong identity
    then poisoned every downstream pick judgment. Never raises."""
    from core import llm
    from core.llm import downscale_for_vision

    try:
        data = Path(path).read_bytes()
        prompt = PART_DESCRIPTION_PROMPT.format(
            context_block=_part_context_block(recipe, context))
        out = llm.complete(prompt, schema=PART_DESCRIPTION_SCHEMA,
                           images=[downscale_for_vision(data)], max_tokens=600,
                           model=llm.SONNET)
        if out.get("part_visible") and str(out.get("description", "")).strip():
            return " ".join(str(out["description"]).split())
        print("  part description: template shows no clear part "
              f"({str(out.get('description', ''))[:80]})")
    except Exception as e:
        print(f"  warning: part description failed: {e}")
    return None


def _library_product_thumbs(browser, recipe: str, part_desc: str = "",
                            top_n: int = LIBRARY_PAGE_CANDIDATES,
                            trace: dict | None = None) -> dict:
    """The page's click candidates: one batched vision call over the grid
    sorts thumbnails into a RECOGNIZABLE pool (the part's features can be
    made out in the thumbnail) and a PLAUSIBLE reserve (could be the part,
    not recognizable), then the deterministic badge sort orders each pool
    (verdict-tagged > untagged > "Used for training", the model's rank
    within each group, recency as the final tiebreak — see
    _order_candidates) and only the top_n of each survive. The sort runs
    BEFORE the cap: a PASS-tagged product card must be clicked ahead of a
    trainset one whatever the model's own confidence order. Answers are
    validated against the DOM's card list (hallucinated ids dropped,
    duplicates collapsed), so a wild verdict can never click a nonexistent
    card. Returns {"recognizable": [{id, badge}], "plausible": [...]};
    both empty on any failure. `trace`, when given, receives the page's
    card count, both raw model lists + the reason and both full ordered
    lists — the manifest's record of WHY these candidates, in this order."""
    from core import llm
    from core.llm import downscale_for_vision

    empty = {"recognizable": [], "plausible": []}
    try:
        cards = _library_cards(browser)
        if not cards:
            return dict(empty)
        out = llm.complete(
            LIBRARY_THUMBS_PROMPT.format(
                recipe_line=_anchor_line(recipe, part_desc),
                ids=", ".join(f"#{c['id']}" for c in cards),
                recognizable_what=_recognizable_what(part_desc),
            ),
            schema=LIBRARY_THUMBS_SCHEMA,
            images=[downscale_for_vision(_library_grid_screenshot(browser))],
            max_tokens=900, model=llm.SONNET,
        )

        def _ints(key):
            return [int(i) for i in out.get(key, []) or []
                    if str(i).lstrip("-").isdigit()]

        model_ranked = _ints("product_captures")
        model_plausible = _ints("plausible_captures")
        recognizable = _order_candidates(model_ranked, cards, recipe)
        seen = {c["id"] for c in recognizable}
        # a card the model put in BOTH lists is recognizable — never a
        # reserve entry too
        plausible = [c for c in _order_candidates(model_plausible, cards, recipe)
                     if c["id"] not in seen]
        if trace is not None:
            # 600, not 200: a field investigation needed the ranker's full
            # reasoning and the manifest had only the first 200 chars.
            trace.update({"cards": len(cards), "model_ranked": model_ranked,
                          "model_plausible": model_plausible,
                          "model_reason": str(out.get("reason", ""))[:600],
                          "ordered": [c["id"] for c in recognizable],
                          "plausible_ordered": [c["id"] for c in plausible]})
        return {"recognizable": recognizable[:top_n],
                "plausible": plausible[:top_n]}
    except Exception as e:
        print(f"  warning: thumbnail judgement failed: {e}")
        return dict(empty)


def judge_library_viewer(browser, recipe: str = "", part_desc: str = "") -> dict:
    """One Haiku vision verdict on the viewer: product + inspection
    overlay. Cropped to the viewer when the bbox probe finds it."""
    import io

    from PIL import Image

    from core import capture_criteria as cc, llm
    from core.llm import downscale_for_vision

    png = browser.screenshot_bytes(full_page=True)
    bbox = main_image_bbox(browser, png)
    if bbox:
        with Image.open(io.BytesIO(png)) as im:
            crop = im.crop((bbox["x"], bbox["y"],
                            bbox["x"] + bbox["width"], bbox["y"] + bbox["height"]))
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            png = buf.getvalue()
    prompt = LIBRARY_VIEWER_PROMPT.format(
        recipe_line="" if part_desc else _recipe_line(recipe),
        product_criterion=(cc.anchored_product_criterion(part_desc)
                           if part_desc else cc.PRODUCT_CRITERION),
        overlay_criterion=cc.INSPECTION_OVERLAY_CRITERION,
    )
    return llm.complete(prompt, schema=LIBRARY_VIEWER_SCHEMA,
                        images=[downscale_for_vision(png)], max_tokens=500,
                        model=llm.SONNET)


def _library_selected_id(browser):
    try:
        m = _SELECTED_CAPTURE_RE.search(
            browser.page.evaluate("document.body.innerText") or "")
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _click_library_capture(browser, cid: int) -> bool:
    """Select capture #cid and wait for its viewer to load: the heading
    must name the capture, then the image poll guards the lazy viewer."""
    try:
        browser.page.get_by_text(f"#{cid}", exact=True).first.click()
    except Exception:
        return False
    for _ in range(8):
        browser.page.wait_for_timeout(1000)
        if _library_selected_id(browser) == cid:
            break
    else:
        return False
    ok, msg = poll_image_loaded(browser, max_wait_s=35, interval_s=5)
    if not ok:
        print(f"  warning: capture #{cid} viewer {msg}")
    return True


LIBRARY_PAGE_TURN_WAIT_S = 20


# "20 / page" — the pagination's page-size control, plain text on the page.
_LIBRARY_PAGE_SIZE_RE = re.compile(r"(\d+)\s*/\s*page", re.I)
# Without an expected count the gate can only watch for stability; the
# grid populates in BATCHES (5 cards, then 20 a second later — measured
# live), so a partial batch must hold this many consecutive reads before
# it is believed. With an expected count, one confirming read suffices.
LIBRARY_SETTLE_STABLE_READS_FALLBACK = 3


def _library_expected_cards(browser, page_no: int) -> int | None:
    """How many cards page_no MUST show, from two static texts on the page:
    the filtered total ("77 Total Captures") and the page size ("20 /
    page"). None when either is unreadable — the gate then falls back to
    stability alone. Read BEFORE the pagination click: both are constant
    across page turns."""
    try:
        total_s = _library_count(browser.page)
        body = browser.page.evaluate("document.body.innerText") or ""
        m = _LIBRARY_PAGE_SIZE_RE.search(body)
        if total_s is None or m is None:
            return None
        total = int(str(total_s).replace(",", ""))
        size = int(m.group(1))
        if size <= 0 or page_no < 1:
            return None
        return max(0, min(size, total - size * (page_no - 1)))
    except Exception:
        return None


def _library_page_settled(browser, prev_ids, max_wait_s: float | None = None,
                          require_change: bool = True,
                          expected: int | None = None) -> tuple[bool, list[int]]:
    """After a pagination click: poll until the grid shows the DESTINATION
    page — card ids differ from prev_ids (skipped via require_change=False
    for hops whose destination can equal the origin, e.g. clicking "1"
    while already on page 1), every card's thumbnail has PAINTED, the card
    COUNT equals `expected` when known, and the view holds identical
    across consecutive reads (one confirming read with an expected count;
    LIBRARY_SETTLE_STABLE_READS_FALLBACK without). The grid populates in
    batches — a field page went 5 cards, then 20 a second later — and a
    stability-only gate released on the 5-card batch: the ranker judged
    a page whose real part captures were not yet in the DOM. Bounded, 1s
    cadence; returns (settled, ids) — on timeout the caller degrades to
    judging whatever is rendered, never fails."""
    if max_wait_s is None:
        max_wait_s = LIBRARY_PAGE_TURN_WAIT_S
    deadline = time.monotonic() + max_wait_s
    prev = list(prev_ids)
    need_reads = 2 if expected is not None else LIBRARY_SETTLE_STABLE_READS_FALLBACK
    last_good: list[int] | None = None
    streak = 0
    while True:
        ids: list[int] = []
        try:
            cards = _library_cards(browser)
            ids = [int(c["id"]) for c in cards]
            painted = all(c.get("painted") for c in cards)
            if expected is not None:
                good = (len(ids) == expected and painted
                        and (expected == 0 or not require_change or ids != prev))
            else:
                good = (bool(ids) and (not require_change or ids != prev)
                        and painted)
        except Exception:
            good = False
        if good and ids == last_good:
            streak += 1
            if streak >= need_reads:
                return True, ids
        else:
            streak = 1 if good else 0
        last_good = ids if good else None
        if time.monotonic() > deadline:
            return False, ids
        browser.page.wait_for_timeout(1000)


def _library_nav_note(notes: list | None, msg: str) -> None:
    print(f"  warning: library {msg}")
    if notes is not None:
        notes.append(msg)


def _library_settle_expectation(browser, page_no: int | None,
                                notes: list | None) -> int | None:
    """The expected card count for a hop, or None (noted once) when the
    page's total / page-size texts can't be read."""
    if page_no is None:
        return None
    expected = _library_expected_cards(browser, page_no)
    if expected is None:
        msg = ("page total/page-size unreadable; settling page "
               f"{page_no} on stability alone")
        if notes is None or msg not in notes:
            _library_nav_note(notes, msg)
    return expected


def _library_next_page(browser, notes: list | None = None,
                       page_no: int | None = None) -> bool:
    """Click Next and settle on the destination page (page_no, when the
    caller knows it, gives the gate its exact expected card count).

    Returns False when no page turn happened. When the destination SHOULD
    exist — the page's own total/page-size texts say page_no holds cards —
    the reason is recorded via `notes`: a scan that ends early must never
    end silently (a field run skipped page 3 of 3 and the pick record
    could not say why, or even that it had). Ending past the true last
    page (expected 0 cards) stays quiet — that is the scan's normal end.
    """
    try:
        prev = _library_card_ids(browser)
        expected = _library_settle_expectation(browser, page_no, notes)
        should_exist = expected is not None and expected > 0
        nxt = browser.page.query_selector("li.ant-pagination-next")
        if nxt is None or "disabled" in (nxt.get_attribute("class") or ""):
            if should_exist:
                state = "absent" if nxt is None else "disabled"
                _library_nav_note(notes, f"Next is {state} though page "
                                  f"{page_no} should hold {expected} "
                                  f"card(s); the scan ends early")
            return False
        nxt.click()
    except Exception as e:
        _library_nav_note(notes, f"page turn to page {page_no} failed "
                          f"({str(e).splitlines()[0][:120]}); the scan "
                          f"ends early")
        return False
    settled, ids = _library_page_settled(browser, prev, expected=expected)
    if not settled:
        _library_nav_note(notes, "page turn did not settle within "
                          f"{LIBRARY_PAGE_TURN_WAIT_S}s ({len(ids)} cards"
                          + (f", expected {expected}" if expected is not None
                             else "") + "); judging the grid as-is")
    return True


def _library_goto_page(browser, page_no: int, notes: list | None = None) -> bool:
    """Page numbers beyond 3 hide behind the pagination's '•••', so the
    deterministic route is: click page 1 (always visible), then Next
    (page_no - 1) times — every hop settle-gated like the forward scan."""
    try:
        expected = _library_settle_expectation(browser, 1, notes)
        one = browser.page.query_selector("li.ant-pagination-item-1")
        if one is None:
            return page_no == 1
        one.click()
    except Exception:
        return False
    settled, _ = _library_page_settled(browser, [], require_change=False,
                                       expected=expected)
    if not settled:
        _library_nav_note(notes, "jump to page 1 did not settle; "
                          "proceeding as-is")
    for hop in range(2, page_no + 1):
        if not _library_next_page(browser, notes, page_no=hop):
            return False
    return True


def _library_first_capture(browser):
    ids = _library_card_ids(browser)
    return ids[0] if ids else None


def pick_library_capture(browser, recipe: str = "",
                         page_cap: int = LIBRARY_PAGE_SCAN_CAP,
                         click_cap: int = LIBRARY_CLICK_CAP,
                         part_desc: str = "",
                         filter_verified: bool = False) -> dict:
    """Leave the library viewer on the best available capture and say how
    it was chosen; the caller screenshots and downloads afterwards, so all
    three artifacts describe the same capture.

    Pass 1 — per page: one batched thumbnail judgement sorts cards into a
    RECOGNIZABLE pool (the part's features can be made out in the
    thumbnail) and a PLAUSIBLE reserve; the badge sort puts the
    likeliest-overlaid first (PASS/FAIL tag > untagged > "Used for
    training", the model's rank within each); the recognizable pool is
    clicked and each viewer judged — the page is exhausted before moving
    on; the reserve is held. product+overlay short-circuits.
    Pass 2 — only if pass 1 found no product+overlay and click budget
    remains: revisit the pages that held a reserve and click it, in the
    same order, under the same shared cap — NO new thumbnail judgements
    (the lists were recorded in pass 1). A strict thumbnail filter thus
    costs nothing when it works and cannot starve a genuinely dark
    recipe. On exhaustion (all pages, page cap, or click cap) the best
    partial across both passes wins: product-no-overlay over
    overlay-no-product, first-seen ties; nothing qualifying resets to page
    1's newest capture. Every grid the ranker judges is settle-gated first
    (_library_page_settled) — a page that never settles is judged as-is
    and noted in the record. Best-effort like every capture hook:
    failures degrade, never fail the step.

    ANCHOR CONTRADICTION GUARD: when the ANCHORED scan yields ZERO
    candidates — recognizable or plausible — across every page of a grid
    the recipe filter VERIFIED (filter_verified: the cards provably carry
    this recipe's name), tier 4 is not accepted yet. The cards are ground
    truth that these captures belong to the recipe; a judge that rejected
    all of them contradicts that, and the anchor — the one unverified
    input in the loop — is the suspect (a field run's template misread as
    an "automotive panel" zeroed all three pages and shipped the blank
    newest capture). The scan re-runs ONCE from page 1 unanchored, under
    the same shared caps, before the tier-4 reset; recorded as
    anchor_fallback."""
    from core.capture_criteria import LIBRARY_TIER_MEANING

    rec: dict = {"clicked": [], "chosen": None, "tier": None,
                 "pages_scanned": 0, "anchored": bool(part_desc)}
    if part_desc:
        rec["part_anchor"] = part_desc[:160]
    else:
        # The generic criterion accepts any manufactured part — a weaker
        # judge that has passed featureless frames before. Loud, so a run
        # without a template step can't silently claim anchored quality.
        print("  library pick: NO part description — judging with the "
              "generic product criterion (run the template step for "
              "anchored picks)")
    nav_notes: list = []
    state = {"best": (5, None), "clicks": 0, "capped": False, "candidates": 0}

    def judge(page: int, candidates: list, pool: str, anchor: str) -> bool:
        """Click and judge candidates in order; True on a product+overlay
        capture (recorded as chosen). Updates the shared click count, cap
        flag and best partial."""
        for cand in candidates:
            cid, badge = cand["id"], cand["badge"]
            if state["clicks"] >= click_cap:
                print(f"  library pick: click cap ({click_cap}) reached; "
                      f"stopping the search")
                state["capped"] = True
                return False
            if not _click_library_capture(browser, cid):
                print(f"  warning: could not select capture #{cid}; skipping")
                continue
            v = judge_library_viewer(browser, recipe, part_desc=anchor)
            state["clicks"] += 1
            tier = (1 if v.get("product_image") and v.get("overlay")
                    else 2 if v.get("product_image")
                    else 3 if v.get("overlay") else 4)
            rec["clicked"].append({"page": page, "id": cid, "badge": badge,
                                   "pool": pool, "anchored": bool(anchor),
                                   "tier": tier,
                                   "reason": str(v.get("reason", ""))[:300]})
            if tier == 1:
                rec["chosen"], rec["tier"] = {"page": page, "id": cid}, 1
                print(f"  library pick: capture #{cid} (page {page}) is "
                      f"product + overlay")
                return True
            if tier in (2, 3) and tier < state["best"][0]:
                state["best"] = (tier, (page, cid))
        return False

    def run_scan(anchor: str) -> bool:
        """One full scan under `anchor`: pass 1 over the pages, then that
        scan's plausible-reserve pass. Shares the click budget, cap flag,
        candidate count and best partial with any other scan of this pick.
        True when a product+overlay capture was chosen. The caller settles
        or navigates the grid to page 1 before calling."""
        anchored = bool(anchor)
        reserve: list[tuple[int, list]] = []   # (page, plausible candidates)
        page = 1
        while page <= page_cap:
            rec["pages_scanned"] = max(rec["pages_scanned"], page)
            trace: dict = {"page": page, "anchored": anchored}
            pools = _library_product_thumbs(browser, recipe, anchor,
                                            trace=trace)
            rec.setdefault("pages", []).append(trace)
            recognizable = pools.get("recognizable", [])
            plausible = pools.get("plausible", [])
            state["candidates"] += len(recognizable) + len(plausible)
            if plausible:
                reserve.append((page, plausible))
            if recognizable:
                print(f"  library pick: page {page} candidates (badge, then "
                      "model rank): " + ", ".join(
                          f"#{c['id']} {c['badge']}" for c in recognizable)
                      + (f"; {len(plausible)} plausible held in reserve"
                         if plausible else ""))
            elif plausible:
                print(f"  library pick: page {page}: nothing recognizable; "
                      f"{len(plausible)} plausible held in reserve")
            if judge(page, recognizable, "recognizable", anchor):
                return True
            if state["capped"] or page == page_cap \
                    or not _library_next_page(browser, nav_notes,
                                              page_no=page + 1):
                if page == page_cap and not state["capped"]:
                    print(f"  library pick: page cap ({page_cap}) reached")
                break
            page += 1

        if reserve and not state["capped"]:
            total = sum(len(c) for _, c in reserve)
            print(f"  library pick: no product + overlay among recognizable "
                  f"thumbnails; trying {total} plausible reserve candidate(s)")
            rec["reserve_pass"] = True
            current = page
            for rpage, cands in reserve:
                if state["capped"]:
                    break
                if rpage != current:
                    if not _library_goto_page(browser, rpage, nav_notes):
                        _library_nav_note(nav_notes, f"could not return to page "
                                          f"{rpage} for its reserve; skipped")
                        continue
                    current = rpage
                if judge(rpage, cands, "plausible", anchor):
                    return True
        return False

    try:
        # The entry grid gets the same settle gate as every page turn: the
        # filter's verdict proves cards rendered, not that their thumbnails
        # painted — and the ranker must never judge grey tiles.
        settled, _ = _library_page_settled(
            browser, [], require_change=False,
            expected=_library_settle_expectation(browser, 1, nav_notes))
        if not settled:
            _library_nav_note(nav_notes, "page 1 grid did not settle; "
                              "judging it as-is")

        if run_scan(part_desc):
            return rec

        if (part_desc and filter_verified and state["candidates"] == 0
                and not state["capped"]):
            # See ANCHOR CONTRADICTION GUARD in the docstring.
            rec["anchor_fallback"] = True
            print("  library pick: the anchored judge rejected every card "
                  "of a filter-VERIFIED grid — the anchor is the suspect; "
                  "retrying once unanchored")
            if _library_goto_page(browser, 1, nav_notes):
                if run_scan(""):
                    return rec
            else:
                _library_nav_note(nav_notes, "could not return to page 1 "
                                  "for the unanchored retry")

        best = state["best"]
        if best[1] is not None:
            bp, bid = best[1]
            last = rec["clicked"][-1] if rec["clicked"] else None
            if not (last and last["page"] == bp and last["id"] == bid):
                if _library_goto_page(browser, bp, nav_notes):
                    _click_library_capture(browser, bid)
            rec["chosen"], rec["tier"] = {"page": bp, "id": bid}, best[0]
            print(f"  library pick: best partial is capture #{bid} (page {bp}, "
                  f"tier {best[0]}: {LIBRARY_TIER_MEANING[best[0]]})")
        else:
            _library_goto_page(browser, 1, nav_notes)
            first = _library_first_capture(browser)
            if first is not None:
                _click_library_capture(browser, first)
            rec["tier"] = 4
            print("  library pick: no capture met any criterion; keeping page "
                  "1's newest capture")
    except Exception as e:
        print(f"  warning: library pick failed: {e}; capturing the current view")
    finally:
        if nav_notes:
            rec["nav_notes"] = nav_notes
    return rec


# The Library grid is global — unfiltered it shows every recipe's captures,
# newest first, so both the screenshot and the main-image download can land
# on another recipe's capture entirely. The filter is applied fresh every
# run: the page does not persist it across visits. Sequence proven against
# a live camera: the combobox input has a stable id (#recipe) — its
# placeholder text intercepts nothing and must never be the click target —
# type the name, click the option whose text matches EXACTLY (cameras carry
# recipes whose names are prefixes of each other), then Search.
_LIBRARY_COUNT_RE = re.compile(r"([\d,]+)\s+Total\s+Captures", re.I)
LIBRARY_FILTER_WAIT_S = 20


def _library_count(page) -> str | None:
    try:
        m = _LIBRARY_COUNT_RE.search(page.evaluate("document.body.innerText") or "")
        return m.group(1) if m else None
    except Exception:
        return None


LIBRARY_READY_WAIT_S = 30
LIBRARY_READY_RETRY_WAIT_S = 10

_LIBRARY_CARD_NAME_RE = re.compile(r"#\d+\n(?:(?:PASS|FAIL)\n)?([^\n#][^\n]*)")


def _wait_for_library_ready(browser, max_wait_s: float = LIBRARY_READY_WAIT_S) -> bool:
    """Poll until the library's filter panel exists. A fast replay
    completes on URL/text postconditions while the SPA is still hydrating
    (a field page was blank at 0s and rendered at 14s), so a one-shot
    query here silently no-op'd the filter and shipped ANOTHER recipe's
    captures. Bounded, 1s cadence; the image-load poll downstream absorbs
    the same delay anyway, so this costs nothing overall."""
    deadline = time.monotonic() + max_wait_s
    while True:
        try:
            box = browser.page.query_selector("#recipe")
            if box is not None and box.is_visible():
                return True
            if "Total Captures" in (
                    browser.page.evaluate("document.body.innerText") or ""):
                return True
        except Exception:
            pass
        if time.monotonic() > deadline:
            return False
        browser.page.wait_for_timeout(1000)


def _attempt_library_filter(page, recipe: str, count_before) -> tuple[str, str]:
    """One filter attempt. Returns (status, detail); "filtered" is the only
    success status (a legitimate zero-capture result included)."""
    box = page.query_selector("#recipe")
    if box is None or not box.is_visible():
        return "no-filter-control", ""
    # Click the ant-select CONTAINER, not the inner input: on the OV20i the
    # combobox input sits UNDER the selection overlay and never receives
    # pointer events (a 10s click timeout, live-observed — the same lesson
    # _MODEL_SELECT_JS carries). fill() needs no pointer and still targets
    # the input itself. The OV80i container takes the click identically.
    container = box.evaluate_handle("el => el.closest('.ant-select') || el")
    try:
        container.as_element().click(timeout=5000)
    except Exception:
        box.click(timeout=5000)  # unexpected DOM: try the input directly
    box.fill(recipe)
    page.wait_for_timeout(1200)
    option = next(
        (el for el in page.query_selector_all(".ant-select-item-option")
         if el.is_visible() and el.inner_text().strip() == recipe),
        None,
    )
    if option is None:
        # A prefix sibling ("X - pin inspection" vs "X - second pin
        # inspection") must never be accepted; close the dropdown instead.
        page.keyboard.press("Escape")
        return "no-recipe-option", recipe
    option.click()
    page.wait_for_timeout(400)
    search = next(
        (el for el in page.query_selector_all("button")
         if el.is_visible() and el.inner_text().strip().lower() == "search"),
        None,
    )
    if search is None:
        return "no-search-button", ""
    search.click()
    page.wait_for_timeout(2500)  # grid re-render
    deadline = time.monotonic() + LIBRARY_FILTER_WAIT_S
    while True:
        count = _library_count(page)
        body = page.evaluate("document.body.innerText") or ""
        if count == "0":
            print(f"  library filtered to {recipe!r}: 0 captures — "
                  f"the recipe's true state")
            return "filtered", "0"
        if count is not None and (count != count_before or recipe in body):
            print(f"  library filtered to {recipe!r}: {count} captures")
            return "filtered", str(count)
        if time.monotonic() > deadline:
            return "did-not-settle", ""
        page.wait_for_timeout(1000)


def _library_filter_verdict(page, recipe: str) -> tuple[str, str]:
    """What the grid actually shows now: ("ok"|"zero"|"foreign"|"unknown",
    foreign recipe name when identifiable). Text-shaped — the capture
    cards carry their recipe names in plain text."""
    try:
        body = page.evaluate("document.body.innerText") or ""
    except Exception:
        return "unknown", ""
    if _library_count(page) == "0":
        return "zero", ""
    names = [n.strip() for n in _LIBRARY_CARD_NAME_RE.findall(body)]
    if not names:
        return "unknown", ""
    if any(n == recipe for n in names):
        return "ok", ""
    foreign = max(set(names), key=names.count)
    return "foreign", foreign


def filter_library_by_recipe(browser, recipe: str) -> dict:
    """Filter the Library page to the run's recipe and search, so the capture
    grid, the selected capture and the main-image download all belong to the
    recipe under test.

    Deterministic via the filter's stable #recipe input; the option click is
    an exact-text match against the resolved recipe name. Reliability: waits
    for the panel to render (a one-shot query once raced the SPA and shipped
    another recipe's captures), retries the whole attempt once on any
    degradation, and VERIFIES the outcome against the cards' recipe names —
    an unfiltered grid showing a foreign recipe is loudly flagged and
    recorded in the manifest, never silent. Still best-effort at the step
    level: every failure path warns and the capture proceeds; the step
    never fails."""
    page = browser.page
    rec: dict = {"recipe": recipe, "filtered": False, "verified": None,
                 "attempts": []}
    try:
        recipe = (recipe or "").strip()
        if not recipe:
            print("  warning: no recipe name to filter the library by; "
                  "capturing unfiltered")
            rec["note"] = "no recipe name"
            return rec
        for attempt in range(2):
            ready = _wait_for_library_ready(
                browser, LIBRARY_READY_WAIT_S if attempt == 0
                else LIBRARY_READY_RETRY_WAIT_S)
            if not ready:
                rec["attempts"].append("library page never became ready")
                continue
            count_before = _library_count(page)
            # An attempt that RAISES (a click timeout, a detached node) is a
            # degraded attempt, not a reason to skip the retry and the
            # verification below — the field case that motivated this was an
            # agent-filtered grid whose backstop crash also skipped the
            # verdict that would have reported it clean.
            try:
                status, detail = _attempt_library_filter(page, recipe, count_before)
            except Exception as e:
                status, detail = "attempt-error", str(e).splitlines()[0][:160]
            rec["attempts"].append(f"{status}: {detail}" if detail else status)
            if status == "filtered":
                rec["filtered"] = True
                break
            print(f"  library filter attempt {attempt + 1} degraded "
                  f"({status}); " + ("retrying once" if attempt == 0
                                     else "capturing the page as-is"))
            browser.page.wait_for_timeout(5000)
        verdict, foreign = _library_filter_verdict(page, recipe)
        rec["verified"] = verdict
        if verdict == "foreign":
            rec["note"] = f"grid shows another recipe's captures ({foreign})"
            print(f"  WARNING: library grid is showing {foreign!r} — NOT the "
                  f"recipe under test; the library capture pair may show the "
                  f"wrong recipe's part")
        return rec
    except Exception as e:
        print(f"  warning: library recipe filter failed: {e}; capturing unfiltered")
        rec["note"] = str(e)[:200]
        return rec


# "Source Capture: <n> of <TOTAL>" — innerText may put the input value and
# the "of N" on separate lines, hence the bounded any-character gap.
_CAPTURE_TOTAL_RE = re.compile(r"source\s+capture:?[\s\S]{0,40}?\bof\s+(\d+)", re.I)

# The OV20i block page states the total as "Total Captures: N" in its
# INITIAL view — the "Source Capture: n of N" readout only appears once
# Previous has entered the capture-review state. Colon required: the
# Library page's "131 Total Captures" (number first) must NOT match.
_TOTAL_CAPTURES_RE = re.compile(r"total\s+captures:\s*([\d,]+)", re.I)


def _block_capture_total(text: str) -> int | None:
    m = _CAPTURE_TOTAL_RE.search(text) or _TOTAL_CAPTURES_RE.search(text)
    return int(m.group(1).replace(",", "")) if m else None


def harvest_block_total(browser, want_type: str, meta: dict, source: str) -> None:
    """Read the block page's total capture count and record it for every
    model of the block's type. The "Source Capture: n of N" readout only
    renders once a capture is loaded — i.e. AFTER the screenshot step's
    "Previous" click — which is why this runs there and not with the
    class-bar harvest, which needs the pre-"Previous" view. Deterministic
    (regex over page text); enrichment only — never fails the step."""
    try:
        total = _block_capture_total(browser.page_text(20000))
        if total is None:
            print(
                "  warning: no 'Source Capture: n of N' / 'Total Captures: N' "
                "readout found; total captures not recorded"
            )
            return
        models = [mm for mm in meta.get("models", []) if mm.get("type") == want_type]
        for mm in models:
            entry = meta.setdefault("model_stats", {}).setdefault(
                mm["name"],
                {"type": want_type, "total_captures": None,
                 "classes": [], "source": source},
            )
            entry["total_captures"] = total
            meta.setdefault("facts", []).append({
                "subject": f"model: {mm['name']}",
                "property": "total_captures",
                "value": str(total),
                "source": source,
            })
        print(f"  total captures for {want_type}: {total} ({len(models)} model(s))")
    except Exception as e:
        print(f"  warning: total captures not recorded: {e}")


def detect_recipe_model(browser, meta: dict) -> None:
    """Single-model variants (OV20i): a recipe carries exactly ONE AI model,
    and the editor overview's waterfall names its type — step 4 reads
    "Classification" or "Segmentation"; there is no Models section to
    enumerate. Seed the sanctioned meta["models"] envelope from that text so
    every downstream consumer (stats harvest, pick judges, deck slices) keys
    on the same roster contract multi-model variants fill on the ROI page.
    Deterministic; warns and leaves the roster unset on any failure, so
    downstream hooks no-op loudly rather than guess."""
    try:
        text = browser.page.evaluate("document.body.innerText") or ""
        counts = {
            t: len(re.findall(rf"\b{t}\b", text))
            for t in ("Classification", "Segmentation")
        }
        best = max(counts, key=lambda t: counts[t])
        if counts[best] == 0:
            print("  warning: neither Classification nor Segmentation appears "
                  "on the editor overview; model roster not seeded")
            return
        if all(counts.values()):
            print(f"  note: both block types appear on the overview {counts}; "
                  f"taking the more frequent")
        meta["models"] = [
            {"name": best, "type": best.lower(), "slug": slugify(best)}
        ]
        print(f"  recipe model: {best} ({best.lower()})")
    except Exception as e:
        print(f"  warning: recipe model detection failed: {e}")


def _block_type(value, meta: dict) -> str:
    """A step flag's block type: the literal value, or — for "auto"
    (single-model variants, where the spec cannot know the type) — the
    seeded roster's one model type. Empty string when auto cannot resolve;
    every consumer already warns-and-continues on an unknown type."""
    value = str(value)
    if value != "auto":
        return value
    models = meta.get("models") or []
    if not models:
        print("  warning: block type is 'auto' but the model roster is empty")
        return ""
    return models[0].get("type", "")


def harvest_aligner_choice(browser, meta: dict) -> None:
    """OV20i alignment page: record whether the aligner is in use from the
    "Use the aligner?" button pair — the selected side carries the primary
    style. Recorded as a recipe skip_aligner fact with the single-token
    value the deck's toggle evaluation resolves without a model call.
    Enrichment only: warns and continues on any failure or ambiguity."""
    try:
        got = browser.page.evaluate("""() => {
          const out = {};
          for (const b of document.querySelectorAll('button')) {
            const t = (b.innerText || '').trim();
            if (t === 'Yes, use it' || t === 'No, skip it')
              out[t] = (b.className || '').includes('ant-btn-primary');
          }
          return out;
        }""") or {}
        if len(got) < 2:
            print("  warning: 'Use the aligner?' buttons not found; "
                  "aligner state not recorded")
            return
        if got.get("Yes, use it") == got.get("No, skip it"):
            print(f"  warning: aligner choice ambiguous ({got}); not recorded")
            return
        skipped = bool(got.get("No, skip it"))
        meta.setdefault("facts", []).append({
            "subject": "recipe",
            "property": "skip_aligner",
            "value": "on" if skipped else "off",
            "source": "alignment page toggle",
        })
        print(f"  aligner: {'skipped' if skipped else 'in use'}")
    except Exception as e:
        print(f"  warning: aligner choice harvest failed: {e}")


def capture_block_per_model(
    browser, step: dict, out: RunOutput, step_record: dict, desc_queue: list,
    base_ctx: dict, meta: dict,
):
    """For steps with foreach_block_models: <type> — one capture per model of
    that type on an AI-block page (Classification/Segmentation).

    A block page shows one model at a time via its model selector, so a
    single screenshot can only ever be correct for one model. Deck slides are
    per model, so each model gets its own capture, recorded on the model
    envelope for a structured join downstream.
    """
    want = step["foreach_block_models"]
    models = [m for m in meta.get("models", []) if m.get("type") == want]
    if not models:
        print(f"  no {want} models; nothing to capture per model")
        return
    shots = []
    for m in models:
        # Stage the mechanical parts deterministically (close modal, set
        # the model selector) so the agent's turns go to the capture, not
        # to fighting the portal dropdown.
        if step.get("prepare_model_selector"):
            prepare_model_view(browser, want, m["name"])
        goal = step["per_model_goal"].format(model=m["name"])
        result = run_step(
            browser,
            goal,
            step["per_model_postcondition"].format(model=m["name"]),
            max_model_calls=step.get("max_model_calls", 30),
        )
        if result.status != "success":
            print(
                f"  warning: could not capture {want} view for \"{m['name']}\": "
                f"{result.evidence[:120]}"
            )
            continue
        ok, msg = poll_image_loaded(browser)
        if not ok:
            print(f"  warning: view for \"{m['name']}\" {msg}")
        name = f"{step['screenshot']}_{slugify(m['name'])}.png"
        item = f"{step.get('item_label', want + ' view')} for model {m['name']}"
        shot = out.save(
            name, browser.screenshot_bytes(full_page=True),
            kind="screenshot", role="deliverable", step=step["id"],
            item=item, description_key=name,
        )
        shots.append(out.rel(shot))
        m[step.get("meta_key", "block_screenshot")] = out.rel(shot)
        desc_queue.append((shot, {**base_ctx, "item": item}))
        print(f"  {want} view \"{m['name']}\" -> {name}")
    step_record["screenshots"] = shots


def capture_reports(
    browser, step: dict, out: RunOutput, step_record: dict, desc_queue: list, base_ctx: dict,
    meta: dict,
):
    """For steps with foreach_reports: open each model's training report (where
    available), wait for it to load, and screenshot as <model-name>_<model-type>.png."""
    # Enumerate only once the table actually holds rows. This step re-navigates
    # to the Train Models page itself, so it cannot rely on the capture step's
    # wait. Read against skeleton rows, the only real text on the page is the
    # header, and "Model" gets enumerated as a model — which then costs a whole
    # turn budget hunting for a training report that cannot exist.
    ok, msg = poll_table_loaded(browser)
    if not ok:
        print(f"  warning: train models table {msg}")
    models = list_training_reports(_stable_snapshot(browser))

    # Cross-check against the roster we already enumerated on the ROI page.
    # The Train table stacks a model's name over its type in one cell, and the
    # reader has come back with the TYPE as the name ("segmentation" for a
    # model actually called "Model S"). Navigating to a model that does not
    # exist cannot succeed, and costs a whole turn budget discovering that.
    # meta["models"] is authoritative here; anything not in it is a misread.
    if meta.get("models"):
        keep = []
        for m in models:
            entry = _envelope_entry(meta, m["name"], m.get("type", ""))
            if entry is None:
                print(
                    f"  ignoring enumerated report for {m['name']!r}: no such model "
                    f"in this recipe ({[e['name'] for e in meta['models']]})"
                )
                continue
            # Trust the roster's spelling over the table reader's.
            m["name"] = entry["name"]
            m["type"] = entry.get("type") or m.get("type", "")
            keep.append(m)
        models = keep

    step_record["report_models"] = [f"{m['name']} ({m['type']})" for m in models]
    if not models:
        print("  no models with an available training report")
        return
    shots = []
    for m in models:
        if not _click_scoped(browser, m["entry_text"], m["name"]):
            goal = (
                "You are on (or near) the Train Models page of a recipe. If a training "
                "report or any modal is currently open, close it first. Then click the "
                f'"{m["entry_text"]}" control that opens the training report for the '
                f'model "{m["name"]}" ({m["type"]}) — it is shown below that model\'s '
                '"Last trained" information.'
            )
            result = run_step(
                browser, goal, f'The training report for "{m["name"]}" is displayed.'
            )
            if result.status != "success":
                # One unreachable report must not cost the whole run. The three
                # steps after this one (settings, Node-RED, library) are worth
                # more than a single model's report screenshot, and a report
                # that will not open is usually a model that has none.
                print(
                    f"  warning: could not open training report for "
                    f"\"{m['name']}\": {result.evidence}",
                    file=sys.stderr,
                )
                continue
        ok, msg = poll_image_loaded(browser, max_wait_s=60, interval_s=5)
        if not ok:
            print(f"  warning: training report for \"{m['name']}\" {msg}")
        name = f"{slugify(m['name'])}_{slugify(m['type'])}.png"
        shot = out.save(
            name, browser.screenshot_bytes(full_page=True),
            kind="screenshot", role="deliverable", step=step["id"],
            item=f"training report for model {m['name']} ({m['type']})",
            description_key=name,
        )
        shots.append(out.rel(shot))
        entry = _envelope_entry(meta, m["name"], m["type"])
        if entry is not None:
            entry["report_screenshot"] = out.rel(shot)
        desc_queue.append(
            (shot, {**base_ctx, "item": f"training report for model {m['name']} ({m['type']})"})
        )
        print(f"  report \"{m['name']}\" ({m['type']}) -> {name}")
        _close_report(browser)
    step_record["screenshots"] = shots


SETTINGS_LOAD_WAIT_MS = 1_000


def capture_settings(
    browser, step: dict, out: RunOutput, step_record: dict, desc_queue: list, base_ctx: dict,
    meta: dict,
):
    """For steps with foreach_settings: open each model's settings, screenshot
    as <model-name>_<model-type>_settings.png, close, repeat.

    Settings controls are usually icon-only, so the enumerator returns the ref
    of the control in the snapshot it saw; refs go stale once the page changes,
    so each iteration re-enumerates on a fresh snapshot.
    """
    models = list_model_settings(_stable_snapshot(browser))
    step_record["settings_models"] = [f"{m['name']} ({m['type']})" for m in models]
    if not models:
        print("  no models with an identifiable settings control")
        return
    shots = []
    for i, target in enumerate(models):
        clicked = False
        current = models if i == 0 else list_model_settings(_stable_snapshot(browser))
        match = next((m for m in current if m["name"] == target["name"]), None)
        if match and match["settings_ref"] in browser.last_items:
            clicked = not browser.click(match["settings_ref"]).startswith("Error")
        if not clicked:
            goal = (
                "You are on (or near) the Train Models page of a recipe. If a settings "
                "dialog, report, or any modal is currently open, close it first. Then "
                f'open the settings for the model "{target["name"]}" ({target["type"]}) '
                "— typically a gear/settings icon in that model's row."
            )
            result = run_step(
                browser, goal, f'The settings for model "{target["name"]}" are displayed.'
            )
            if result.status != "success":
                raise RuntimeError(
                    f"could not open settings for {target['name']}: {result.evidence}"
                )
        browser.page.wait_for_timeout(SETTINGS_LOAD_WAIT_MS)
        name = f"{slugify(target['name'])}_{slugify(target['type'])}_settings.png"
        shot = out.save(
            name, browser.screenshot_bytes(full_page=True),
            kind="screenshot", role="deliverable", step=step["id"],
            item=f"settings dialog for model {target['name']} ({target['type']})",
            description_key=name,
        )
        shots.append(out.rel(shot))
        entry = _envelope_entry(meta, target["name"], target["type"])
        if entry is not None:
            entry["settings_screenshot"] = out.rel(shot)
        desc_queue.append(
            (shot, {**base_ctx, "item": f"settings dialog for model {target['name']} ({target['type']})"})
        )
        print(f"  settings \"{target['name']}\" ({target['type']}) -> {name}")
        _close_report(browser)
    step_record["screenshots"] = shots


def _close_report(browser):
    """Best-effort return to the Train Models list; the next iteration's
    click has agent fallback if this doesn't land."""
    browser.snapshot()
    for label in ("Close", "Ok", "Back"):
        cands = [it for it in browser.last_items.values() if it["text"] == label]
        if len(cands) == 1:
            browser.click(cands[0]["ref"])
            return
    browser.page.keyboard.press("Escape")
    browser.page.wait_for_timeout(1000)


def capture_per_model(
    browser, step: dict, out: RunOutput, step_record: dict, desc_queue: list,
    base_ctx: dict, meta: dict,
):
    """For steps with foreach_models: one screenshot per model on the page,
    named after the model; a single default screenshot when there are none.

    Also writes the structured model envelope (meta["models"]) — the one
    sanctioned structured contract downstream consumers may key on."""
    models = list_models(_stable_snapshot(browser))
    step_record["models"] = [m["name"] for m in models]
    meta["models"] = [
        {
            "name": m["name"],
            "type": m.get("model_type", ""),
            "slug": slugify(m["name"]),
        }
        for m in models
    ]
    if not models:
        name = f"{step['screenshot']}.png"
        browser.page.wait_for_timeout(1500)
        shot = out.save(
            name, browser.screenshot_bytes(full_page=True),
            kind="screenshot", role="deliverable", step=step["id"],
            item="default view (no models configured)", description_key=name,
        )
        step_record["screenshot"] = out.rel(shot)
        desc_queue.append((shot, {**base_ctx, "item": "default view (no models configured)"}))
        print(f"  no models configured; default screenshot -> {name}")
        return
    shots = []
    for m in models:
        if not _click_model(browser, m["entry_text"]):
            goal = (
                "On the Inspection Setup page, in the Models section, click the model "
                f'entry shown as "{m["entry_text"]}" to select it so its ROI setup is displayed.'
            )
            result = run_step(
                browser, goal, f'The ROI setup for model "{m["name"]}" is displayed.'
            )
            if result.status != "success":
                raise RuntimeError(
                    f"could not select model {m['name']}: {result.evidence}"
                )
        ok, msg = poll_image_loaded(browser)
        if not ok:
            print(f"  warning: ROI view for \"{m['name']}\" {msg}")
        name = f"{step['screenshot']}_{slugify(m['name'])}.png"
        shot = out.save(
            name, browser.screenshot_bytes(full_page=True),
            kind="screenshot", role="deliverable", step=step["id"],
            item=f"ROI setup for model {m['name']}", description_key=name,
        )
        shots.append(out.rel(shot))
        for entry in meta["models"]:
            if entry["name"] == m["name"]:
                entry["roi_screenshot"] = out.rel(shot)
        desc_queue.append((shot, {**base_ctx, "item": f"ROI setup for model {m['name']}"}))
        print(f"  model \"{m['name']}\" -> {name}")
    step_record["screenshots"] = shots


def check_postcondition(
    browser, step: dict, recipe_name: str | None, attempts: int = 6
) -> tuple[bool, str]:
    why = "ok"
    for attempt in range(attempts):
        if attempt:
            # Pages render asynchronously; give them a moment before re-checking.
            browser.page.wait_for_timeout(1500)
        url_regex = step.get("success_url_regex")
        if url_regex and not re.search(url_regex, browser.url()):
            why = f"URL {browser.url()} does not match {url_regex}"
            continue
        if (
            recipe_name
            and step.get("check_recipe", True)
            and recipe_name not in browser.page_text(20000)
        ):
            why = f'matched recipe "{recipe_name}" not visible on page'
            continue
        missing = [
            t for t in step.get("success_text", []) if t not in browser.page_text(20000)
        ]
        if missing:
            why = f"expected text not visible: {missing}"
            continue
        if step.get("expect_download") and not browser.downloads:
            # The download event can lag the click; the retry loop covers it.
            why = "expected a file download but none was captured"
            continue
        return True, "ok"
    return False, why


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="Camera URL (any path; origin is used)")
    ap.add_argument("--recipe", required=True, help="Approximate recipe name")
    ap.add_argument(
        "--run-dir",
        help="Output directory for this run (default: runs/<timestamp>)",
    )
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--force-agent", action="store_true", help="Skip trace replay")
    ap.add_argument(
        "--skip-descriptions",
        action="store_true",
        help="Do not generate vision descriptions of the screenshots",
    )
    ap.add_argument(
        "--steps",
        help="Comma-separated step ids to run (in spec order); others are skipped. "
        "Note: later steps may depend on earlier steps' end state.",
    )
    ap.add_argument(
        "--context",
        help="Engineer-provided context for this run — a file path or literal "
        "text, in THEIR words (what part/application is being inspected). "
        "Recorded in meta.json as user_context and used to ground the part "
        "description that anchors the capture picks.",
    )
    ap.add_argument(
        "--llm-backend",
        choices=["api", "claude-code", "agent-sdk"],
        default=os.environ.get("SG_LLM_BACKEND", "agent-sdk"),
        help="Where LLM calls run: 'agent-sdk' (default) = EVERYTHING, "
        "navigation included, on your Claude Code login — no API key needed; "
        "'claude-code' = single-shot calls via the local claude CLI (navigation "
        "still uses the API); 'api' = the Anthropic API, needs ANTHROPIC_API_KEY.",
    )
    args = ap.parse_args(argv)

    llm.select_backend(args.llm_backend)
    if args.llm_backend == "claude-code":
        print(
            "LLM backend: claude-code (resolvers/describers use your Claude Code "
            "login; agentic navigation still uses the Anthropic API and needs "
            "ANTHROPIC_API_KEY)"
        )
    elif args.llm_backend == "agent-sdk":
        print(
            "LLM backend: agent-sdk (all calls, including agentic navigation, "
            "run on your Claude Code login)"
        )

    origin = "{0.scheme}://{0.netloc}".format(urlparse(args.url))
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.run_dir) if args.run_dir else paths.output_base() / "runs" / f"{ts}"
    run_dir.mkdir(parents=True)
    out = RunOutput(run_dir)
    manifest: dict = {
        "url": origin,
        "recipe_input": args.recipe,
        "started": ts,
        "steps": [],
    }

    browser = Browser(headed=args.headed)
    browser.start()
    exit_code = 0
    # (screenshot path, context) pairs, described after the steps finish.
    desc_queue: list[tuple[Path, dict]] = []
    # Extra per-run structured data (e.g. image-area bboxes) -> meta.json.
    meta: dict = {}
    # Engineer-provided context (a file path or literal text): recorded
    # verbatim, and injected into the part-description prompt — a wrong
    # part identity there once poisoned every pick judgment of a run.
    if args.context:
        ctx = args.context
        try:
            p = Path(ctx)
            if p.is_file():
                ctx = p.read_text()
        except OSError:
            pass  # literal text long enough to break Path(); use as-is
        ctx = ctx.strip()
        if ctx:
            meta["user_context"] = ctx
            print(f"user context: {ctx[:100]}{'...' if len(ctx) > 100 else ''}")
    try:
        browser.goto(origin)

        page_text = browser.page_text()
        variant = detect_variant(browser.page.title(), page_text)
        # Raw index.html, not the rendered DOM: lazily-loaded route chunks make
        # the DOM's script list vary run-to-run.
        raw_html = browser.page.request.get(origin).text()
        version_key = detect_ui_version(page_text, raw_html)
        manifest["variant"] = variant
        manifest["ui_version"] = version_key
        print(f"variant={variant} ui_version={version_key}")

        spec_path = ROOT / "tasks" / f"{variant}.yaml"
        if not spec_path.exists():
            print(f"ERROR: no task spec for variant {variant} ({spec_path})", file=sys.stderr)
            return 2
        spec = yaml.safe_load(spec_path.read_text())
        if args.steps:
            wanted = {s.strip() for s in args.steps.split(",")}
            unknown = wanted - {s["id"] for s in spec["steps"]}
            if unknown:
                print(f"ERROR: unknown step ids: {sorted(unknown)}", file=sys.stderr)
                return 2
            spec["steps"] = [s for s in spec["steps"] if s["id"] in wanted]

        traces = trace_store.load(variant, version_key) or {"steps": {}}

        # Exact on-screen recipe name, resolved once per run (LLM) and shared
        # by all subsequent steps.
        run_resolved: str | None = None
        # URL where the previous step ended — the clean starting state for a
        # path-less step, restored before an agent fallback so recorded traces
        # are always complete flows.
        checkpoint_url: str | None = None

        run_t0 = time.monotonic()
        for step in spec["steps"]:
            step_id = step["id"]
            step_t0 = time.monotonic()
            print(f"\n== step: {step_id}")
            browser.downloads.clear()
            # Steps without a path continue from the previous step's end state.
            if step.get("path"):
                goto_checked(browser, origin, origin + step["path"])
            step_record: dict = {"id": step_id}
            recipe_name = None

            # BASIC-mode IO: there is no Node-RED flow to export, so the
            # export step is skipped BEFORE any agent turns are spent — and
            # the rules page is harvested instead, feeding the same
            # analysis contract the flow JSON feeds in Advanced mode.
            if step.get("skip_when_basic_io") and _is_basic_io_page(browser):
                print("  IO page is in Basic Mode (rules layout): no flow to "
                      "export; harvesting the rules instead")
                harvest_io_rules(browser, out, meta)
                step_record["status"] = "skipped"
                step_record["notes"] = ("basic-mode IO: no Node-RED flow to "
                                        "export; rules harvested")
                step_record["duration_s"] = round(time.monotonic() - step_t0, 1)
                manifest["steps"].append(step_record)
                print(f"  SKIPPED ({step_record['duration_s']}s)")
                continue

            cached = traces["steps"].get(step_id)
            done = False
            # Steps whose flow depends on live data (conditional branches the
            # trace can't see) always run the agent.
            if step.get("always_agent"):
                cached = None
            if cached and not args.force_agent:
                ok, why, resolved = trace_store.replay(
                    browser, cached, args.recipe, resolve_recipe, resolved=run_resolved
                )
                if resolved:
                    run_resolved = resolved
                if ok:
                    recipe_name = resolved or run_resolved
                    ok, why = check_postcondition(browser, step, recipe_name)
                if ok:
                    step_record["layer"] = "replay"
                    done = True
                else:
                    print(f"  replay failed ({why}); falling back to agent")
                    if step.get("path"):
                        goto_checked(browser, origin, origin + step["path"])
                    elif checkpoint_url:
                        browser.goto(checkpoint_url)

            if not done:
                goal = step["goal"].format(recipe=args.recipe)
                step_record["layer"] = "agent"
                step_record["model_calls"] = 0
                # Sonnet navigates first; a failed step is retried once on
                # Opus (from a restored checkpoint) before giving up.
                for nav_model in (llm.SONNET, llm.OPUS):
                    result = run_step(
                        browser,
                        goal,
                        step["postcondition"],
                        max_model_calls=step.get("max_model_calls", 30),
                        model=nav_model,
                    )
                    step_record["model_calls"] += result.model_calls
                    step_record["agent_evidence"] = result.evidence
                    step_record["agent_model"] = nav_model
                    if result.status == "success":
                        break
                    if nav_model != llm.OPUS:
                        print(f"  agent ({nav_model}) failed; escalating to {llm.OPUS}")
                        if step.get("path"):
                            goto_checked(browser, origin, origin + step["path"])
                        elif checkpoint_url:
                            browser.goto(checkpoint_url)
                if result.status != "success":
                    step_record["status"] = "failure"
                    step_record["notes"] = result.notes
                    step_record["duration_s"] = round(time.monotonic() - step_t0, 1)
                    manifest["steps"].append(step_record)
                    raise RuntimeError(f"agent failed step {step_id}: {result.evidence}")
                recipe_name = run_resolved or result.matched_recipe or None
                # Only the first resolution (the recipe-list step) is trusted as
                # the canonical on-screen name; later steps sometimes report a
                # decorated variant that would poison subsequent postconditions.
                if result.matched_recipe and run_resolved is None:
                    run_resolved = result.matched_recipe
                    recipe_name = run_resolved

            ok, why = check_postcondition(browser, step, recipe_name)
            if not ok:
                step_record["status"] = "failure"
                step_record["duration_s"] = round(time.monotonic() - step_t0, 1)
                manifest["steps"].append(step_record)
                raise RuntimeError(f"postcondition failed for {step_id}: {why}")

            if step_record["layer"] == "agent" and not step.get("always_agent"):
                if result.actions:
                    # The CANONICAL resolved name, never the step's own
                    # matched_recipe: agents on non-recipe steps sometimes
                    # fill that field with whatever they matched ("Region of
                    # Interest (ROIs)"), and when that junk appears in a
                    # click's row context the action is marked recipe_scoped
                    # — which replay can then never satisfy (it demands the
                    # resolved recipe name in the row). Same trust rule as
                    # run_resolved above, extended to the trace store.
                    trace_store.save(
                        variant, version_key, step_id,
                        result.actions, run_resolved or "",
                    )
                    print(f"  trace saved for {variant}/{version_key}")
                else:
                    # An agent run with no actions means it started from an
                    # already-advanced state; an empty trace would poison replay.
                    print("  agent recorded no actions; trace not saved")

            checkpoint_url = browser.url()
            base_ctx = {
                "variant": variant,
                "recipe": recipe_name or args.recipe,
                "step": step_id,
                "intent": step.get("goal", ""),
            }
            # Single-model variants: seed the roster from the editor
            # overview's waterfall (there is no Models section to enumerate).
            if step.get("detect_recipe_model"):
                detect_recipe_model(browser, meta)
            # OV20i alignment page: record the "Use the aligner?" choice.
            if step.get("harvest_aligner_choice"):
                harvest_aligner_choice(browser, meta)
            # Stats steps run on a block page's INITIAL view — before the
            # screenshot step's "Previous" click swaps the class panel to the
            # annotation state and its bars disappear. One harvest per model
            # of the block's type, all read from the same shared panel.
            # (On the OV20i the class panel persists after Previous, so the
            # same step may carry stats, total, pick and screenshot at once.)
            if step.get("collect_block_stats"):
                want_type = _block_type(step["collect_block_stats"], meta)
                stat_models = [
                    m for m in meta.get("models", []) if m.get("type") == want_type
                ]
                if not stat_models:
                    print(f"  no {want_type} models; stats skipped")
                for m in stat_models:
                    harvest_model_stats(browser, m, meta, f"{step['id']} page")
            # Totals-only companion to the above: reads the post-"Previous"
            # capture navigator, so it hangs off the screenshot step whose
            # agent already clicked Previous and waited for the image.
            if step.get("collect_block_total"):
                harvest_block_total(
                    browser, _block_type(step["collect_block_total"], meta),
                    meta, f"{step['id']} page"
                )
            if step.get("expect_download") and browser.downloads:
                dl_name = step.get(
                    "download_as", browser.downloads[-1].suggested_filename
                )
                dest = out.folder_for("data", "data") / dl_name
                browser.downloads[-1].save_as(dest)
                out.register(dest, kind="data", role="data", step=step_id)
                step_record["download"] = out.rel(dest)
                print(f"  saved download -> {out.rel(dest)}")
            # Before any capture or vision wait: a promo modal overlaying the
            # page would cover the image viewer and stall poll_image_loaded
            # for its full budget before ruining the screenshot anyway.
            if step.get("dismiss_promo_modal"):
                dismiss_promo_modal(browser)
            # Also before the vision wait and the main-image download: both
            # must see the RECIPE'S captures, not the global newest.
            if step.get("filter_library_recipe"):
                step_record["library_filter"] = filter_library_by_recipe(
                    browser, recipe_name or args.recipe)
            # After the filter, before screenshot + download: leave the
            # viewer on a capture that actually shows the product with its
            # inspection overlays (searched, not taken on faith).
            if step.get("pick_library_capture"):
                # The filter verdict feeds the anchor contradiction guard:
                # a verified grid whose every card the anchored judge
                # rejects indicts the anchor, not the grid.
                _lf = step_record.get("library_filter") or {}
                step_record["library_pick"] = pick_library_capture(
                    browser, recipe_name or args.recipe,
                    part_desc=meta.get("part_description", ""),
                    filter_verified=_lf.get("verified") == "ok")
            if step.get("foreach_models"):
                capture_per_model(
                    browser, step, out, step_record, desc_queue, base_ctx, meta
                )
            elif step.get("foreach_reports"):
                capture_reports(browser, step, out, step_record, desc_queue, base_ctx, meta)
            elif step.get("foreach_settings"):
                capture_settings(browser, step, out, step_record, desc_queue, base_ctx, meta)
            elif step.get("foreach_block_models"):
                capture_block_per_model(
                    browser, step, out, step_record, desc_queue, base_ctx, meta
                )
            elif step.get("screenshot"):
                wait_cfg = step.get("wait_image_loaded")
                table_cfg = step.get("wait_table_loaded")
                if wait_cfg:
                    if not isinstance(wait_cfg, dict):
                        wait_cfg = {}
                    ok, msg = poll_image_loaded(
                        browser,
                        max_wait_s=wait_cfg.get("max_wait_s", 90),
                        interval_s=wait_cfg.get("interval_s", 7),
                    )
                    if not ok:
                        print(f"  warning: {step_id} image {msg}")
                elif table_cfg:
                    # Pages whose content is a data grid rather than imagery:
                    # skeleton rows render instantly, so without this the
                    # capture succeeds against a page that has no data yet.
                    if not isinstance(table_cfg, dict):
                        table_cfg = {}
                    ok, msg = poll_table_loaded(
                        browser,
                        max_wait_s=table_cfg.get("max_wait_s", 60),
                        interval_s=table_cfg.get("interval_s", 5),
                    )
                    if not ok:
                        print(f"  warning: {step_id} table {msg}")
                else:
                    browser.page.wait_for_timeout(1500)
                # Block pages: search the source captures for a
                # product+annotated frame before capturing — the agent's
                # "Previous" lands on the LAST capture, which carries no
                # guarantee of showing the part or its annotations. The
                # flag's value names the block type for the vision judge.
                if step.get("pick_annotated_capture"):
                    step_record["capture_pick"] = pick_annotated_capture(
                        browser, _block_type(step["pick_annotated_capture"], meta),
                        recipe=recipe_name or args.recipe,
                        part_desc=meta.get("part_description", ""))
                if step.get("close_node_red_panels"):
                    close_node_red_panels(browser)
                name = f"{step['screenshot']}.png"
                # screenshot_iframe: capture only the page's dominant embedded
                # iframe (e.g. the Node-RED editor), not the surrounding
                # chrome. A page without one degrades to the normal capture.
                png = None
                if step.get("screenshot_iframe"):
                    png = browser.iframe_screenshot_bytes()
                    if png is None:
                        print(
                            f"  warning: no dominant iframe found for {step_id}; "
                            f"capturing the full page instead"
                        )
                if png is None:
                    png = browser.screenshot_bytes(full_page=True)
                shot = out.save(
                    name, png, kind="screenshot",
                    role=step.get("screenshot_role", "deliverable"),
                    step=step_id, description_key=name,
                )
                step_record["screenshot"] = out.rel(shot)
                desc_queue.append((shot, base_ctx))
                # Single-model variants: also record this capture on the model
                # envelope — the sanctioned join the deck's ladders key on
                # (block_screenshot, view_rois_screenshot, roi_screenshot).
                if step.get("envelope_key") and meta.get("models"):
                    meta["models"][0][step["envelope_key"]] = out.rel(shot)
                if step.get("capture_image_bbox"):
                    bbox = main_image_bbox(browser, png)
                    if bbox:
                        meta[f"{step_id}_img_bbox"] = bbox
                        print(f"  image bbox: {bbox['x']},{bbox['y']} {bbox['width']}x{bbox['height']}")
                    else:
                        print(f"  warning: no main image area found for {step_id} bbox")
                if step.get("download_main_image"):
                    img_info = download_main_image(browser, out, step["screenshot"], step_id)
                    meta[f"{step_id}_main_image"] = img_info
                    if img_info.get("file"):
                        step_record["main_image"] = img_info["file"]
                        n_overlays = len(img_info.get("overlays", []))
                        composite = (img_info.get("composite") or {}).get("file")
                        print(
                            f"  main image saved -> {img_info['file']} "
                            f"({img_info['method']}, {img_info['native_width']}x{img_info['native_height']}"
                            f"{f', +{n_overlays} overlay(s)' if n_overlays else ''}"
                            f"{f', composite {composite}' if composite else ''})"
                        )
                        # The template image is the canonical view of the
                        # part; its description anchors every later pick
                        # judgment to THE part being inspected. A blank or
                        # random template (possible under Skip Aligner)
                        # yields no anchor — enrichment, never a gate.
                        if step.get("describe_part"):
                            desc = describe_part_from_image(
                                out.run_dir / img_info["file"],
                                recipe=recipe_name or args.recipe,
                                context=meta.get("user_context", ""))
                            if desc:
                                meta["part_description"] = desc
                                print(f"  part description: {desc[:110]}")
                            else:
                                print("  part description unavailable; pick "
                                      "judges will run unanchored")
                    else:
                        print(f"  warning: main image not saved: {img_info.get('error')}")
            step_record["status"] = "success"
            step_record["matched_recipe"] = recipe_name
            step_record["duration_s"] = round(time.monotonic() - step_t0, 1)
            manifest["steps"].append(step_record)
            print(f"  OK ({step_record['layer']}, {step_record['duration_s']}s)")

    except Exception as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        manifest["error"] = str(e)
        try:
            out.save(
                "failure.png", browser.screenshot_bytes(full_page=True),
                kind="debug", role="debug",
            )
            out.save("failure_snapshot.txt", browser.snapshot(), kind="debug", role="debug")
        except Exception:
            pass
        exit_code = 1
    finally:
        # Describe whatever was captured, even on a failed run; a description
        # failure must never mask the run's own outcome.
        manifest["steps_duration_s"] = round(time.monotonic() - run_t0, 1)

        # Compose BEFORE describing. The composite replaces the plain capture
        # at the same path, so the vision description then describes the asset
        # that actually ships rather than the one it superseded.
        try:
            composed = compose_imaging_with_template(out, meta, manifest)
            if composed:
                meta["imaging_setup_with_template"] = composed
                if composed.get("composited"):
                    print(f"composed imaging+template -> {composed['file']}")
                else:
                    print(f"imaging setup left as captured: {composed['reason']}")
        except Exception as e:
            print(f"FAILED to compose imaging+template: {e}", file=sys.stderr)

        from concurrent.futures import ThreadPoolExecutor

        # The Node-RED analysis and the screenshot descriptions share nothing:
        # one reads a downloaded JSON flow, the other reads PNGs. Run serially
        # they cost ~165s each. Start Node-RED first and let it work WHILE the
        # descriptions do, then join below — the phase now costs the slower of
        # the two rather than their sum.
        #
        # Only the model call is off-thread. Every file write, manifest key and
        # facts append stays on this thread, so results land in a deterministic
        # order and nothing races on RunOutput.
        analysis_t0 = time.monotonic()
        flow_path = run_dir / "data" / "node_red_flow.json"
        rules_path = run_dir / "data" / "io_rules.txt"
        nr_future = nr_pool = None
        nr_source = None
        # One IO-logic analysis per run, whatever the mode captured: the
        # Advanced-mode flow JSON, or the Basic-mode rules text — both feed
        # the SAME output contract (node_red_description.md + io_logic
        # facts), so the deck never knows the difference.
        if flow_path.exists() and not args.skip_descriptions:
            nr_source, io_text, io_fn = ("node_red_flow.json",
                                         flow_path.read_text(), describe_node_red)
        elif rules_path.exists() and not args.skip_descriptions:
            nr_source, io_text, io_fn = ("io_rules.txt",
                                         rules_path.read_text(), describe_io_rules)
        if nr_source is not None:

            def _describe_io(fn, text, ctx):
                t0 = time.monotonic()
                return fn(text, ctx), round(time.monotonic() - t0, 1)

            nr_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="node-red")
            nr_future = nr_pool.submit(
                _describe_io, io_fn, io_text,
                {
                    "variant": manifest.get("variant"),
                    "recipe": manifest.get("recipe_input"),
                },
            )
            print(f"\ndescribing IO logic from {nr_source} (running in background)...")

        try:
            if desc_queue and not args.skip_descriptions:
                desc_t0 = time.monotonic()
                print(
                    f"describing {len(desc_queue)} screenshot(s) "
                    f"({DESCRIBE_WORKERS} in parallel)..."
                )

                # The roster is final by now (capture is done); it steers fact
                # subjects in the prompt, and canonicalization below enforces
                # it in code. Both exist because screens display truncated
                # model names and other recipes' content (see
                # canonicalize_fact_subject).
                roster_names = [
                    m.get("name", "") for m in meta.get("models", []) if m.get("name")
                ]

                def _describe(item):
                    shot_path, ctx = item
                    return describe_screenshot(
                        shot_path.read_bytes(),
                        {**ctx, "models": meta.get("models", [])},
                    )

                # Results are merged in queue order so the output files stay
                # deterministic regardless of completion order.
                descriptions = {}
                canon_notes: dict[str, int] = {}
                with ThreadPoolExecutor(max_workers=DESCRIBE_WORKERS) as pool:
                    futures = [pool.submit(_describe, item) for item in desc_queue]
                    for (shot_path, _ctx), fut in zip(desc_queue, futures):
                        try:
                            result = fut.result()
                            descriptions[shot_path.name] = result["description"]
                            for fact in result.get("facts", []):
                                subj, action = canonicalize_fact_subject(
                                    str(fact.get("subject", "")), roster_names
                                )
                                if action:
                                    canon_notes[action] = canon_notes.get(action, 0) + 1
                                meta.setdefault("facts", []).append(
                                    {**fact, "subject": subj, "source": shot_path.name}
                                )
                            print(
                                f"  described {shot_path.name} "
                                f"(+{len(result.get('facts', []))} facts)"
                            )
                        except Exception as e:
                            descriptions[shot_path.name] = f"[description failed: {e}]"
                            print(
                                f"  FAILED to describe {shot_path.name}: {e}",
                                file=sys.stderr,
                            )
                if canon_notes:
                    print(
                        "  fact subjects canonicalized against the roster: "
                        + ", ".join(f"{n} {k}" for k, n in sorted(canon_notes.items()))
                    )
                out.save(
                    "descriptions.json", json.dumps(descriptions, indent=2),
                    kind="report", role="deliverable",
                )
                manifest["descriptions"] = "deliverables/report/descriptions.json"
                manifest["descriptions_duration_s"] = round(
                    time.monotonic() - desc_t0, 1
                )

            # Join the Node-RED analysis started above. By now it has usually
            # finished alongside the descriptions, so this rarely blocks.
            if nr_future is not None:
                try:
                    nr, nr_secs = nr_future.result()
                    out.save(
                        "node_red_description.md", nr["markdown"],
                        kind="report", role="deliverable",
                    )
                    for fact in nr.get("facts", []):
                        subj, _ = canonicalize_fact_subject(
                            str(fact.get("subject", "")),
                            [m.get("name", "") for m in meta.get("models", [])
                             if m.get("name")],
                        )
                        meta.setdefault("facts", []).append(
                            {**fact, "subject": subj, "source": nr_source}
                        )
                    manifest["node_red_description"] = (
                        "deliverables/report/node_red_description.md"
                    )
                    manifest["node_red_duration_s"] = nr_secs
                    print(f"  node_red_description.md written (from {nr_source})")
                except Exception as e:
                    print(f"  FAILED to describe IO logic ({nr_source}): {e}",
                          file=sys.stderr)
        finally:
            if nr_pool is not None:
                nr_pool.shutdown(wait=False)
        # Wall clock for the overlapped phase. The two durations above are each
        # task's own compute time and will now sum to MORE than this.
        manifest["analysis_duration_s"] = round(time.monotonic() - analysis_t0, 1)

        if meta:
            out.save("meta.json", json.dumps(meta, indent=2), kind="data", role="data")
            manifest["meta"] = "data/meta.json"
        manifest["assets"] = out.assets
        if llm.substitutions():
            manifest["model_substitutions"] = llm.substitutions()
            print(
                f"\nNOTE: {len(llm.substitutions())} call(s) ran on a weaker model "
                f"than preferred (see manifest model_substitutions)"
            )
        manifest["duration_s"] = round(time.monotonic() - run_t0, 1)
        manifest["finished"] = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        (out.folder_for("data", "data") / "manifest.json").write_text(
            json.dumps(manifest, indent=2)
        )
        browser.close()
        step_times = ", ".join(
            f"{s['id']}={s.get('duration_s', '?')}s" for s in manifest["steps"]
        )
        if step_times:
            print(f"\ntimings: total={manifest['duration_s']}s | {step_times}")
        print(f"run dir: {run_dir}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
