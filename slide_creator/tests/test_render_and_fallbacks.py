import pytest

from deck_builder.errors import RenderError
from deck_builder.manifest import ImageValue
from deck_builder.planner import plan_deck
from deck_builder.render import RunContext, render_slide
from deck_builder.templates._shared import (
    extract_mean_iou,
    extract_total_images,
    extract_train_accuracy,
    first_sentences,
)


def test_render_one_slide(bundle, tmp_path):
    manifest = plan_deck(bundle)
    ctx = RunContext(manifest=manifest, out_dir=tmp_path)
    slide = next(s for s in manifest.slides if s.template_id == "imaging_setup")
    cache = render_slide(slide, ctx)
    assert (tmp_path / "slides" / f"{slide.id}.pptx").exists()
    # Second render is a cache hit (same fingerprint object comes back).
    assert render_slide(manifest.get_slide(slide.id), ctx) is not None
    assert manifest.get_slide(slide.id).render_cache.fingerprint == cache.fingerprint


def test_render_missing_image_fails_clearly(bundle, tmp_path):
    manifest = plan_deck(bundle)
    slide = next(s for s in manifest.slides if s.template_id == "library")
    broken = slide.model_copy(update={
        "holes": {
            **slide.holes,
            "library_capture": ImageValue(path="/nonexistent/x.png"),
        },
    })
    manifest.replace_slide(broken)
    ctx = RunContext(manifest=manifest, out_dir=tmp_path)
    with pytest.raises(RenderError, match="missing on disk"):
        render_slide(manifest.get_slide(slide.id), ctx)


def test_user_image_can_fill_any_image_hole(bundle, tmp_path):
    """The prioritization contract: a user image is target-compatible
    with every image hole, no template changes needed."""
    manifest = plan_deck(bundle)
    slide = next(s for s in manifest.slides if s.template_id == "library")
    user_png = bundle.screenshot("library")  # any real png on disk
    swapped = slide.model_copy(update={
        "holes": {"library_capture": ImageValue(path=str(user_png), source="user")},
    })
    manifest.replace_slide(swapped)
    ctx = RunContext(manifest=manifest, out_dir=tmp_path)
    assert render_slide(manifest.get_slide(slide.id), ctx) is not None


def test_stats_extraction():
    desc = "Summary stats: Training Accuracy 100%, Total Images 30, Iterations 100."
    assert extract_train_accuracy(desc) == "100%"
    assert extract_total_images(desc) == "30"
    assert extract_mean_iou(desc) == "—"
    assert extract_train_accuracy("") == "—"


def test_first_sentences_respects_budget():
    text = "One. " * 200
    out = first_sentences(text, max_chars=50)
    assert len(out) <= 50
    long_sentence = "word " * 100
    out = first_sentences(long_sentence, max_chars=40)
    assert out.endswith("…") and len(out) <= 40
