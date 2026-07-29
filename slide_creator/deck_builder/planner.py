"""Deck planner.

Given a ``RunBundle`` (+ optional user notes and a pre-built LLM
cache), produce a ``DeckManifest``. Structure and step numbering are
copied from ``recipe_decryption/case_study/planner.py``:

1. GLOBAL_HEAD templates, in order, one slide per ``applies()`` context.
2. Per-model groups in fixed block-type order (alignment → roi →
   classification → segmentation). For each context of a type, every
   template in the type's group emits before moving to the next
   context — that gives the classifier-group/segmenter-group
   interleaving of the original deck.
3. GLOBAL_TAIL templates, in order.

A template whose ``build`` raises is skipped with a stderr note
rather than killing the plan — a partially captured run should still
produce the rest of the deck.
"""

from __future__ import annotations

import sys
from typing import Any, Optional

from deck_builder.manifest import DeckManifest, SlideSpec, TextValue
from deck_builder.run_bundle import ModelInfo, RunBundle
from deck_builder.templates import (
    BLOCK_GROUPS,
    BLOCK_TYPE_ORDER,
    GLOBAL_HEAD,
    GLOBAL_TAIL,
    validate_template_module,
)

# Templates that increment the deck's step counter when emitted
# (identical set to recipe_decryption).
_STEP_INCREMENT_TEMPLATES = frozenset({
    "imaging_setup",       # always step 1
    "aligner_setup",       # always step 2
    "roi_setup",           # always step 3
    "classifier_setup",    # step 4+
    "cls_rois_setup",      # increments (sub-step of classifier)
    "segmenter_setup",     # step N
    "nodered_setup",       # last step
})


def plan_deck(
    bundle: RunBundle,
    *,
    llm_cache: Optional[dict[str, Any]] = None,
    user_notes: str = "",
) -> DeckManifest:
    """Build a fresh ``DeckManifest`` for the given run bundle.

    ``llm_cache`` is built by ``deck_builder.llm.build_llm_cache``;
    passing ``None`` (or ``{}``) plans a fully deterministic deck from
    the run's own screenshots and descriptions.
    """
    llm_cache = llm_cache or {}
    slides: list[SlideSpec] = []
    order = 0
    step = 0

    def _emit(template, ctx) -> None:
        nonlocal order, step
        validate_template_module(template)
        order += 1
        stepped = template.TEMPLATE_ID in _STEP_INCREMENT_TEMPLATES
        if stepped:
            step += 1
        try:
            slide = _make_slide(template, bundle, ctx, llm_cache, order, step)
        except Exception as exc:
            print(
                f"planner: {template.TEMPLATE_ID}.build failed ({exc}); skipping slide",
                file=sys.stderr,
            )
            order -= 1
            if stepped:
                step -= 1
            return
        slides.append(slide)

    # --- Phase 1: global head ------------------------------------------------
    for template in GLOBAL_HEAD:
        for ctx in template.applies(bundle):
            _emit(template, ctx)

    # --- Phase 2: per-model groups in fixed type order ------------------------
    for block_type in BLOCK_TYPE_ORDER:
        group = BLOCK_GROUPS.get(block_type, [])
        if not group:
            continue
        for ctx in _group_contexts(bundle, block_type):
            for template in group:
                if _context_applies(template, bundle, ctx):
                    _emit(template, ctx)

    # --- Phase 3: global tail --------------------------------------------------
    for template in GLOBAL_TAIL:
        for ctx in template.applies(bundle):
            _emit(template, ctx)

    return DeckManifest(
        run_id=bundle.run_id,
        run_dir=str(bundle.run_dir),
        camera_variant=bundle.camera_variant,
        recipe_name=bundle.recipe_name,
        user_notes=user_notes.strip(),
        slides=slides,
        llm_cache=llm_cache,
    )


def _group_contexts(bundle: RunBundle, block_type: str) -> list[Any]:
    """The ordered contexts a block-type group iterates over.

    ``alignment``/``roi`` are recipe-global singletons in this pipeline
    (their single template decides emission via ``applies``);
    classification/segmentation iterate the run's models of that type.
    """
    if block_type in ("alignment", "roi"):
        return [None]
    return bundle.models_of_type(block_type)


def _context_applies(template, bundle: RunBundle, ctx: Any) -> bool:
    """Whether ``template`` wants to emit a slide for this context."""
    try:
        return ctx in template.applies(bundle)
    except Exception as exc:
        print(
            f"planner: {template.TEMPLATE_ID}.applies failed ({exc}); skipping",
            file=sys.stderr,
        )
        return False


def _make_slide(
    template, bundle: RunBundle, ctx: Any, llm_cache: dict, order: int, step: int
) -> SlideSpec:
    holes = template.build(bundle, ctx, llm_cache)

    # Inject the computed step number into the ``step_no`` hole if the
    # template declares one (templates set it to "" by default).
    if step > 0 and "step_no" in holes:
        holes["step_no"] = TextValue(text=str(step))

    hole_specs = {spec.name: spec for spec in template.HOLE_SCHEMA}
    model = ctx if isinstance(ctx, ModelInfo) else None

    return SlideSpec(
        id=_build_slide_id(template, ctx, order),
        order=order,
        kind="hardcoded" if not template.HOLE_SCHEMA else "templated",
        template_id=template.TEMPLATE_ID,
        label=_default_label(template, ctx),
        model_name=model.name if model else None,
        holes=holes,
        hole_specs=hole_specs,
    )


def _build_slide_id(template, ctx: Any, order: int) -> str:
    """``{order:02d}_{template_id}[_{model-slug}]`` — sortable and unambiguous."""
    parts = [f"{order:02d}", template.TEMPLATE_ID]
    if isinstance(ctx, ModelInfo):
        parts.append(ctx.slug)
    return "_".join(parts)


def _default_label(template, ctx: Any) -> str:
    base = template.TEMPLATE_ID.replace("_", " ").title()
    if isinstance(ctx, ModelInfo):
        return f"{base}: {ctx.name}"
    return base


__all__ = ["plan_deck"]
