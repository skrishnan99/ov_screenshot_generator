"""Content pool: every deck input normalized into one structure.

Sources: an extractor run (consumed via its asset index + descriptions — the
downstream contract), optional engineer notes, and optional engineer photos
(described at intake so they join the pool as first-class citizens).
"""

from __future__ import annotations

import json
from pathlib import Path


import re


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unnamed"


def _models(meta: dict, manifest: dict) -> list[dict]:
    """Model roster: prefer the structured envelope written by the extractor
    (meta["models"] — the one sanctioned structured contract); fall back to
    parsing display labels from older runs' step records."""
    envelope = meta.get("models")
    if envelope:
        out = []
        for m in envelope:
            entry = dict(m)
            entry.setdefault("slug", _slugify(entry.get("name", "")))
            entry.setdefault(
                "label",
                f"{entry.get('name', '')} ({entry.get('type', '').title()})".strip(),
            )
            out.append(entry)
        return out
    return _parse_models(manifest)


def _parse_models(manifest: dict) -> list[dict]:
    """Fallback: model list parsed from display labels like
    "Horn Quality (Classification)" -> {name, type, label, slug}."""
    for s in manifest.get("steps", []):
        if s.get("models"):
            out = []
            for label in s["models"]:
                m = re.match(r"^(.*?)\s*\((\w+)\)\s*$", label)
                name, typ = (m.group(1), m.group(2).lower()) if m else (label, "")
                out.append({"label": label, "name": name, "type": typ, "slug": _slugify(name)})
            return out
    return []


def _find(run_dir: Path, *candidates: str) -> Path | None:
    for c in candidates:
        p = run_dir / c
        if p.exists():
            return p
    return None


def load_run(run_dir: Path) -> dict:
    mf_p = _find(run_dir, "data/manifest.json", "manifest.json")
    manifest = json.loads(mf_p.read_text()) if mf_p else {}
    desc_p = _find(run_dir, "deliverables/report/descriptions.json", "descriptions.json")
    descriptions = json.loads(desc_p.read_text()) if desc_p else {}
    meta_p = _find(run_dir, "data/meta.json", "meta.json")
    meta = json.loads(meta_p.read_text()) if meta_p else {}
    nr_p = _find(
        run_dir, "deliverables/report/node_red_description.md", "node_red_description.md"
    )
    recipe = next(
        (s.get("matched_recipe") for s in manifest.get("steps", []) if s.get("matched_recipe")),
        None,
    ) or manifest.get("recipe_input", "")
    return {
        "run_dir": run_dir,
        "manifest": manifest,
        "descriptions": descriptions,
        "meta": meta,
        "node_red": nr_p.read_text() if nr_p else "",
        "assets": manifest.get("assets") or _synthesize_assets(manifest, meta),
        "recipe": recipe,
        "variant": manifest.get("variant", ""),
        "models": _models(meta, manifest),
        "facts": meta.get("facts", []),
        "engineer_notes": "",
    }


def _synthesize_assets(manifest: dict, meta: dict) -> list[dict]:
    """Pseudo-index for pre-reorganization (flat-layout) runs."""
    assets = []
    for s in manifest.get("steps", []):
        names = ([s["screenshot"]] if s.get("screenshot") else []) + s.get("screenshots", [])
        for n in names:
            assets.append(
                {
                    "path": n,
                    "kind": "screenshot",
                    "role": "deliverable",
                    "step": s["id"],
                    "description_key": Path(n).name,
                }
            )
        if s.get("download"):
            assets.append(
                {"path": s["download"], "kind": "data", "role": "data", "step": s["id"]}
            )
    for key, entry in meta.items():
        if not isinstance(entry, dict):
            continue
        step = (
            key.replace("_main_image", "").replace("_img_bbox", "").replace("_with_template", "")
        )
        if key.endswith("_main_image"):
            if entry.get("file"):
                assets.append(
                    {
                        "path": entry["file"],
                        "kind": "image",
                        "role": "deliverable",
                        "step": step,
                        "item": "raw viewer image",
                    }
                )
            comp = (entry.get("composite") or {}).get("file")
            if comp:
                assets.append(
                    {
                        "path": comp,
                        "kind": "image",
                        "role": "deliverable",
                        "step": step,
                        "item": "viewer layers flattened (as shown in UI)",
                    }
                )
            for o in entry.get("overlays", []):
                if o.get("file"):
                    assets.append(
                        {
                            "path": o["file"],
                            "kind": "image",
                            "role": "archive",
                            "step": step,
                            "item": "viewer overlay layer",
                        }
                    )
        elif key.endswith("_with_template"):
            # Current runs composite IN PLACE: entry["file"] is the step's own
            # screenshot, already in the pool from the steps loop above, and
            # adding it again would give the matcher two identical candidates.
            # What is new is the preserved plain capture.
            if entry.get("plain"):
                assets.append(
                    {
                        "path": entry["plain"],
                        "kind": "image",
                        "role": "deliverable",
                        "step": "imaging_setup",
                        "item": "imaging screen as captured, before compositing",
                    }
                )
            elif entry.get("file") and entry.get("composited") is not False:
                # Older runs wrote the composite to a separate file and left
                # the plain capture as the step screenshot. Keep reading those.
                assets.append(
                    {
                        "path": entry["file"],
                        "kind": "image",
                        "role": "deliverable",
                        "step": "imaging_setup",
                        "item": "imaging screen with template image composited",
                    }
                )
    return assets


def asset_path(pool: dict, a: dict) -> Path | None:
    """Absolute, existing filesystem path for an asset, or None. Extractor
    assets are run_dir-relative; engineer assets carry absolute paths."""
    p = Path(a["path"])
    if not p.is_absolute():
        p = pool["run_dir"] / p
    return p if p.exists() else None


def filter_assets(pool: dict, sel: dict) -> list[dict]:
    """ALL assets matching the selector (each with 'abs_path' added), in pool
    order. Selectors are constraints that narrow the candidate set — they do
    not pick; callers decide what to do with 0, 1, or many survivors."""
    out = []
    for a in pool["assets"]:
        if sel.get("path") and a["path"] != sel["path"]:
            continue
        if sel.get("step") and a.get("step") != sel["step"]:
            continue
        if sel.get("kind") and a.get("kind") != sel["kind"]:
            continue
        if a.get("role", "deliverable") != sel.get("role", "deliverable"):
            continue
        if sel.get("source") and a.get("source", "extractor") != sel["source"]:
            continue
        if sel.get("path_contains") and sel["path_contains"] not in a["path"]:
            continue
        p = asset_path(pool, a)
        if p:
            out.append({**a, "abs_path": str(p)})
    return out


def resolve_asset(pool: dict, sel: dict) -> Path | None:
    """First asset matching the selector, as an existing filesystem path.
    Used for `when` conditions and other exists-style checks."""
    matches = filter_assets(pool, sel)
    return Path(matches[0]["abs_path"]) if matches else None


def load_engineer_inputs(pool: dict, context_file: str | None, images_dir: str | None):
    if context_file:
        pool["engineer_notes"] = Path(context_file).read_text()
    if images_dir:
        from core.describer import describe_screenshot

        for p in sorted(Path(images_dir).iterdir()):
            if p.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                continue
            try:
                result = describe_screenshot(
                    p.read_bytes(),
                    {
                        "variant": pool.get("variant"),
                        "recipe": pool.get("recipe"),
                        "step": "engineer photo",
                        "intent": "photo taken by the sales engineer during the site visit",
                        "item": p.name,
                    },
                )
                desc = result["description"]
                for fact in result.get("facts", []):
                    pool.setdefault("facts", []).append({**fact, "source": p.name})
            except Exception as e:
                desc = f"[description failed: {e}]"
            pool["assets"].append(
                {
                    "path": str(p.resolve()),
                    "kind": "image",
                    "role": "deliverable",
                    "step": "engineer",
                    "source": "engineer",
                    "description_key": p.name,
                }
            )
            pool["descriptions"][p.name] = desc
            print(f"  engineer image described: {p.name}")
