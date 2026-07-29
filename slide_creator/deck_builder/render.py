"""Per-slide render pipeline.

Lowers each slide's ``HoleValue``s to the ``str``/``Path`` content
``slide_creator.render`` consumes, validates them against the
template's declared schema, and fills the (variant-resolved) skeleton
into a per-slide ``.pptx``. Same shape as
``recipe_decryption/case_study/render.py`` minus the parts this
pipeline doesn't need: no HTML screen populators (both hole kinds
lower directly), and no preview rasterization (the deck is only ever
viewed as the exported Google Slides file).

Autofit note (inherited from recipe_decryption): the skeletons are
authored in Google Slides with spAutoFit, and Google Slides re-fits
text natively on import, so ``slide_creator``'s autofit pass is
disabled — it would replace spAutoFit with the opposite semantic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from deck_builder.errors import RenderError
from deck_builder.manifest import DeckManifest, RenderCache, SlideSpec
from deck_builder.templates import get_template
from deck_builder.variant import resolve_variant_skeleton


@dataclass(frozen=True)
class RunContext:
    """Paths + manifest for one render run.

    ``out_dir`` is the deck's output root; per-slide artifacts live at
    ``<out_dir>/slides/<slide_id>.pptx`` and the merged deck at
    ``<out_dir>/export/<stem>.pptx``.
    """

    manifest: DeckManifest
    out_dir: Path

    def slide_pptx_path(self, slide_id: str) -> Path:
        return self.out_dir / "slides" / f"{slide_id}.pptx"


def render_slide(slide: SlideSpec, ctx: RunContext, *, force: bool = False) -> RenderCache:
    """Rebuild one slide; skip when its fingerprint matches the cache."""
    fingerprint = _fingerprint_slide(slide, ctx.manifest.camera_variant)

    if (
        not force
        and slide.render_cache is not None
        and slide.render_cache.fingerprint == fingerprint
        and Path(slide.render_cache.pptx_path).exists()
    ):
        return slide.render_cache

    try:
        template = get_template(slide.template_id)
    except Exception as exc:
        raise RenderError(
            f"template lookup failed: {exc}",
            slide_id=slide.id, template_id=slide.template_id,
        ) from exc

    content = _resolve_holes(slide)
    _validate_content_against_schema(slide=slide, template=template, content=content)

    out_pptx = ctx.slide_pptx_path(slide.id)
    try:
        from slide_creator import render as slide_creator_render

        slide_creator_render(
            resolve_variant_skeleton(template.SKELETON_PATH, ctx.manifest.camera_variant),
            content,
            out_pptx,
            strict=True,
            autofit=False,
        )
    except Exception as exc:
        raise RenderError(
            f"slide_creator.render failed: {exc}",
            slide_id=slide.id, template_id=slide.template_id,
        ) from exc

    cache = RenderCache(
        pptx_path=str(out_pptx),
        fingerprint=fingerprint,
        rendered_at=datetime.now(timezone.utc).isoformat(),
    )
    updated = slide.model_copy(update={"render_cache": cache})
    ctx.manifest.replace_slide(updated)
    return cache


def render_deck(ctx: RunContext, *, force: bool = False) -> list[RenderCache]:
    """Render every slide in the manifest, in order."""
    return [render_slide(slide, ctx, force=force) for slide in list(ctx.manifest.slides)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_holes(slide: SlideSpec) -> dict[str, str | Path]:
    """Lower HoleValues to slide_creator content (str for text, Path for image)."""
    content: dict[str, str | Path] = {}
    for hole_name, value in slide.holes.items():
        if value.kind == "text":
            content[hole_name] = value.text
        elif value.kind == "image":
            p = Path(value.path)
            if not p.exists():
                raise RenderError(
                    f"image for hole {hole_name!r} missing on disk: {p}",
                    slide_id=slide.id, template_id=slide.template_id,
                )
            content[hole_name] = p
        else:  # pragma: no cover — pydantic union makes this unreachable
            raise RenderError(
                f"hole {hole_name!r} has unknown kind {value.kind!r}",
                slide_id=slide.id, template_id=slide.template_id,
            )
    return content


def _validate_content_against_schema(*, slide: SlideSpec, template, content: dict) -> None:
    """Check content keys and targets line up with the template schema.

    Values need only match the schema's *target* (text/image) — the
    exact provenance (system screenshot vs user upload) is irrelevant,
    which is what lets a user image replace any system default.
    """
    schema_by_name = {spec.name: spec for spec in template.HOLE_SCHEMA}

    missing = set(schema_by_name) - set(content)
    extra = set(content) - set(schema_by_name)
    if missing or extra:
        raise RenderError(
            f"hole schema mismatch: missing={sorted(missing)}, extra={sorted(extra)}",
            slide_id=slide.id, template_id=slide.template_id,
        )

    for hole_name, value in slide.holes.items():
        spec = schema_by_name.get(hole_name)
        if spec is not None and spec.kind != value.kind:
            raise RenderError(
                f"hole {hole_name!r} target mismatch: schema wants {spec.kind!r}, "
                f"value is {value.kind!r}",
                slide_id=slide.id, template_id=slide.template_id,
            )


def _fingerprint_slide(slide: SlideSpec, camera_variant: str | None = None) -> str:
    """Stable hash of the inputs that determine a slide's render output."""
    payload = {
        "template_id": slide.template_id,
        "holes": {
            name: value.model_dump(mode="json") for name, value in slide.holes.items()
        },
    }
    if camera_variant:
        payload["camera_variant"] = camera_variant
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["RunContext", "render_deck", "render_slide"]
