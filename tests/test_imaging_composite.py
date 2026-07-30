"""The imaging-setup deliverable is the composited view, always.

The imaging screen is a settings page; its own viewer is frequently empty
because whether a live picture exists depends on trigger mode. The aligner
step downloads the template image at native resolution and the viewer's pixel
bbox is recorded, so the two compose into the screen an engineer expects.

That composite is written OVER the plain capture — same path, same manifest
entry — so the deck's `{step: imaging_setup, kind: screenshot}` selector, the
description queue and the matcher catalog all pick it up with nothing to keep
in sync. The plain capture is preserved beside it under images/.

A BLANK composite is still the correct deliverable: an empty viewer is the
recipe's true state. Only an inability to build one at all leaves the plain
capture primary, and that must be reported rather than silent.

Run: uv run python tests/test_imaging_composite.py
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli  # noqa: E402
from core.output import RunOutput  # noqa: E402
from deck.content import _synthesize_assets  # noqa: E402

BBOX = {"x": 10, "y": 10, "width": 40, "height": 30}


def _png(size, colour) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def _fixture(tmp: Path, *, with_template=True, with_bbox=True, template_colour=(200, 0, 0)):
    out = RunOutput(tmp)
    shot = out.save(
        "02_imaging_setup.png", _png((100, 80), (128, 128, 128)),
        kind="screenshot", role="deliverable", step="imaging_setup",
        description_key="02_imaging_setup.png",
    )
    manifest = {"steps": [{"id": "imaging_setup", "screenshot": out.rel(shot)}]}
    meta = {}
    if with_bbox:
        meta["imaging_setup_img_bbox"] = dict(BBOX)
    if with_template:
        raw = out.save(
            "03_template_image_raw.png", _png((400, 300), template_colour),
            kind="image", role="deliverable", step="template_image",
        )
        meta["template_image_main_image"] = {"file": out.rel(raw)}
    return out, meta, manifest, shot


def main() -> int:
    import tempfile

    failures = []

    with tempfile.TemporaryDirectory() as td:
        # ---- happy path: composite replaces the primary, plain preserved ----
        tmp = Path(td) / "ok"
        out, meta, manifest, shot = _fixture(tmp)
        before = shot.read_bytes()
        n_assets = len(out.assets)
        res = cli.compose_imaging_with_template(out, meta, manifest)

        if not res or not res.get("composited"):
            failures.append(f"happy path did not composite: {res}")
        else:
            if res["file"] != out.rel(shot):
                failures.append("primary deliverable is not the step screenshot path")
            if shot.read_bytes() == before:
                failures.append("primary file was not replaced with the composite")
            plain = tmp / res["plain"]
            if not plain.exists():
                failures.append("plain capture was not preserved")
            elif plain.read_bytes() != before:
                failures.append("preserved plain capture is not the original bytes")
            if "images/" not in res["plain"]:
                failures.append(f"plain capture not under images/: {res['plain']}")
            # exactly one new asset (the plain), primary keeps its record
            if len(out.assets) != n_assets + 1:
                failures.append(f"expected 1 new asset, got {len(out.assets) - n_assets}")
            paths = [a["path"] for a in out.assets]
            if len(paths) != len(set(paths)):
                failures.append(f"duplicate asset records: {paths}")
            primary = [a for a in out.assets if a["path"] == out.rel(shot)]
            if len(primary) != 1 or "composited" not in (primary[0].get("item") or ""):
                failures.append("primary asset record not updated to say it is composited")
            # the description still keys off the primary, which is now the composite
            if primary and primary[0].get("description_key") != "02_imaging_setup.png":
                failures.append("description key lost on the primary asset")

        # ---- a blank template still composites; emptiness is not a skip ----
        tmp = Path(td) / "blank"
        out, meta, manifest, shot = _fixture(tmp, template_colour=(0, 0, 0))
        res = cli.compose_imaging_with_template(out, meta, manifest)
        if not res or not res.get("composited"):
            failures.append(f"a blank template must still composite: {res}")

        # ---- cannot composite -> plain stays primary, reason reported ----
        for label, kw in (
            ("no template", {"with_template": False}),
            ("no bbox", {"with_bbox": False}),
        ):
            tmp = Path(td) / label.replace(" ", "_")
            out, meta, manifest, shot = _fixture(tmp, **kw)
            before = shot.read_bytes()
            n = len(out.assets)
            res = cli.compose_imaging_with_template(out, meta, manifest)
            if not res or res.get("composited") is not False:
                failures.append(f"{label}: expected composited=False, got {res}")
            elif not res.get("reason"):
                failures.append(f"{label}: fallback gave no reason")
            if shot.read_bytes() != before:
                failures.append(f"{label}: primary was modified despite no composite")
            if len(out.assets) != n:
                failures.append(f"{label}: assets changed despite no composite")
            if res and res.get("file") != out.rel(shot):
                failures.append(f"{label}: fallback must still name the plain capture")

        # ---- no imaging capture at all ----
        out = RunOutput(Path(td) / "none")
        if cli.compose_imaging_with_template(out, {}, {"steps": []}) is not None:
            failures.append("a run without an imaging capture should return None")

        # ---- the deck pool sees exactly one imaging screenshot ----
        tmp = Path(td) / "pool"
        out, meta, manifest, shot = _fixture(tmp)
        res = cli.compose_imaging_with_template(out, meta, manifest)
        meta["imaging_setup_with_template"] = res
        manifest["steps"][0]["screenshot"] = out.rel(shot)
        assets = _synthesize_assets(manifest, meta)
        imaging = [a for a in assets if a.get("step") == "imaging_setup"]
        shots = [a for a in imaging if a["kind"] == "screenshot"]
        paths = [a["path"] for a in imaging]
        if len(shots) != 1:
            failures.append(f"deck pool has {len(shots)} imaging screenshots, want 1")
        elif shots[0]["path"] != out.rel(shot):
            failures.append("deck would not select the composited primary")
        if len(paths) != len(set(paths)):
            failures.append(f"deck pool has duplicate imaging assets: {paths}")

        # ---- older runs, which composited to a separate file, still read ----
        legacy_meta = {
            "imaging_setup_with_template": {
                "file": "deliverables/images/02_imaging_setup_with_template.png"
            }
        }
        legacy = _synthesize_assets(
            {"steps": [{"id": "imaging_setup", "screenshot": "deliverables/screenshots/02_imaging_setup.png"}]},
            legacy_meta,
        )
        if not any("with_template" in a["path"] for a in legacy):
            failures.append("legacy runs' separate composite is no longer picked up")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL IMAGING-COMPOSITE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
