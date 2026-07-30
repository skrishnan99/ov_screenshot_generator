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
from core.describer import describe_node_red, describe_screenshot, poll_image_loaded
from core.navigator import run_step_auto as run_step
from core.output import RunOutput
from core.resolver import (
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
DESCRIBE_WORKERS = 4


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
                resp = browser.page.request.get(urljoin(browser.page.url, src))
                if not resp.ok:
                    out["error"] = f"fetch of img src returned {resp.status}"
                    return out
                out["content"] = resp.body()
                ctype = resp.headers.get("content-type", "")
                out["ext"] = ".png" if "png" in ctype else ".jpg" if "jpe" in ctype or "jpg" in ctype else ".png"
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
    """If we know the imaging screen's viewer bbox and have the aligner's raw
    image, render the raw image into that bbox on the imaging screenshot — a
    synthesized view of the imaging screen showing the template capture."""
    bbox = meta.get("imaging_setup_img_bbox")
    raw_name = (meta.get("template_image_main_image") or {}).get("file")
    if not bbox or not raw_name:
        return None
    step = next(
        (s for s in manifest.get("steps", []) if s.get("id") == "imaging_setup"), None
    )
    shot_name = (step or {}).get("screenshot")
    if not shot_name:
        return None
    base_path = out.run_dir / shot_name
    raw_path = out.run_dir / raw_name
    if not base_path.exists() or not raw_path.exists():
        return None
    import io

    from PIL import Image

    base = Image.open(base_path).convert("RGBA")
    raw = Image.open(raw_path).convert("RGBA")
    raw = raw.resize((bbox["width"], bbox["height"]), Image.LANCZOS)
    # alpha_composite (not paste): transparent raw regions show the screenshot
    # beneath instead of rendering black.
    base.alpha_composite(raw, (bbox["x"], bbox["y"]))
    buf = io.BytesIO()
    base.convert("RGB").save(buf, format="PNG")
    dest = out.save(
        f"{base_path.stem}_with_template.png", buf.getvalue(),
        kind="image", role="deliverable", step="imaging_setup",
        item="imaging screen with template image composited into viewer bbox",
    )
    return {
        "file": out.rel(dest),
        "base": shot_name,
        "source": raw_name,
        "bbox": {k: bbox[k] for k in ("x", "y", "width", "height")},
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
    models = list_training_reports(_stable_snapshot(browser))
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
                raise RuntimeError(
                    f"could not open training report for {m['name']}: {result.evidence}"
                )
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
                    trace_store.save(
                        variant, version_key, step_id,
                        result.actions, result.matched_recipe,
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
            if step.get("expect_download") and browser.downloads:
                dl_name = step.get(
                    "download_as", browser.downloads[-1].suggested_filename
                )
                dest = out.folder_for("data", "data") / dl_name
                browser.downloads[-1].save_as(dest)
                out.register(dest, kind="data", role="data", step=step_id)
                step_record["download"] = out.rel(dest)
                print(f"  saved download -> {out.rel(dest)}")
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
                else:
                    browser.page.wait_for_timeout(1500)
                name = f"{step['screenshot']}.png"
                png = browser.screenshot_bytes(full_page=True)
                shot = out.save(
                    name, png, kind="screenshot",
                    role=step.get("screenshot_role", "deliverable"),
                    step=step_id, description_key=name,
                )
                step_record["screenshot"] = out.rel(shot)
                desc_queue.append((shot, base_ctx))
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
        if desc_queue and not args.skip_descriptions:
            desc_t0 = time.monotonic()
            print(f"\ndescribing {len(desc_queue)} screenshot(s) ({DESCRIBE_WORKERS} in parallel)...")
            # Descriptions are independent of each other; run them concurrently.
            # Results (descriptions dict, facts) are merged in queue order so
            # the output files stay deterministic regardless of finish order.
            from concurrent.futures import ThreadPoolExecutor

            def _describe(item):
                shot_path, ctx = item
                return describe_screenshot(shot_path.read_bytes(), ctx)

            descriptions = {}
            with ThreadPoolExecutor(max_workers=DESCRIBE_WORKERS) as pool:
                futures = [pool.submit(_describe, item) for item in desc_queue]
                for (shot_path, _ctx), fut in zip(desc_queue, futures):
                    try:
                        result = fut.result()
                        descriptions[shot_path.name] = result["description"]
                        for fact in result.get("facts", []):
                            meta.setdefault("facts", []).append(
                                {**fact, "source": shot_path.name}
                            )
                        print(
                            f"  described {shot_path.name} "
                            f"(+{len(result.get('facts', []))} facts)"
                        )
                    except Exception as e:
                        descriptions[shot_path.name] = f"[description failed: {e}]"
                        print(f"  FAILED to describe {shot_path.name}: {e}", file=sys.stderr)
            out.save(
                "descriptions.json", json.dumps(descriptions, indent=2),
                kind="report", role="deliverable",
            )
            manifest["descriptions"] = "deliverables/report/descriptions.json"
            manifest["descriptions_duration_s"] = round(time.monotonic() - desc_t0, 1)
        flow_path = run_dir / "data" / "node_red_flow.json"
        if flow_path.exists() and not args.skip_descriptions:
            nr_t0 = time.monotonic()
            print("describing node-red flow...")
            try:
                nr = describe_node_red(
                    flow_path.read_text(),
                    {"variant": manifest.get("variant"), "recipe": manifest.get("recipe_input")},
                )
                out.save(
                    "node_red_description.md", nr["markdown"],
                    kind="report", role="deliverable",
                )
                for fact in nr.get("facts", []):
                    meta.setdefault("facts", []).append(
                        {**fact, "source": "node_red_flow.json"}
                    )
                manifest["node_red_description"] = "deliverables/report/node_red_description.md"
                manifest["node_red_duration_s"] = round(time.monotonic() - nr_t0, 1)
                print("  node_red_description.md written")
            except Exception as e:
                print(f"  FAILED to describe node-red flow: {e}", file=sys.stderr)
        try:
            composed = compose_imaging_with_template(out, meta, manifest)
            if composed:
                meta["imaging_setup_with_template"] = composed
                print(f"composed imaging+template -> {composed['file']}")
        except Exception as e:
            print(f"FAILED to compose imaging+template: {e}", file=sys.stderr)
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
