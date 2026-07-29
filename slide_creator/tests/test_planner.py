from deck_builder.planner import plan_deck


def test_deck_order_and_step_numbering(bundle):
    manifest = plan_deck(bundle)
    templates = [s.template_id for s in manifest.slides]

    assert templates == [
        "recipe_title",
        "problem_solution",
        "results_image", "results_image", "results_image",   # one per model
        "configuring_ov80i",
        "imaging_setup",
        "aligner_setup",
        "roi_setup",
        # classifier group ×2 (classification models first, recipe order)
        "classifier_setup", "cls_rois_setup", "training_stats",
        "concise_results_classifier",
        "classifier_setup", "cls_rois_setup", "training_stats",
        "concise_results_classifier",
        # segmenter group ×1
        "segmenter_setup", "training_stats", "concise_results_segmenter",
        "nodered_setup",
        "library",
        "results",
        "basic_camera_info",
        "advanced_camera_info",
        "unique_factors",
        "defect_generator_info",
        "integration_info",
        "team_and_locations",
        "contact",
    ]

    steps = {
        s.id: s.holes["step_no"].text
        for s in manifest.slides
        if "step_no" in s.holes
    }
    # imaging=1, aligner=2, roi=3, then per-group increments, nodered last.
    by_template = [
        (s.template_id, steps[s.id])
        for s in manifest.slides
        if s.id in steps
    ]
    assert by_template == [
        ("imaging_setup", "1"),
        ("aligner_setup", "2"),
        ("roi_setup", "3"),
        ("classifier_setup", "4"),
        ("cls_rois_setup", "5"),
        ("classifier_setup", "6"),
        ("cls_rois_setup", "7"),
        ("segmenter_setup", "8"),
        ("nodered_setup", "9"),
    ]


def test_per_model_slides_are_tagged(bundle):
    manifest = plan_deck(bundle)
    classifier_slides = [
        s for s in manifest.slides if s.template_id == "classifier_setup"
    ]
    assert [s.model_name for s in classifier_slides] == ["Horn Quality", "Hole Presence"]


def test_hardcoded_slides_have_no_holes(bundle):
    manifest = plan_deck(bundle)
    for slide in manifest.slides:
        if slide.kind == "hardcoded":
            assert slide.holes == {}


def test_user_notes_and_llm_cache_are_stored(bundle):
    cache = {"problem": "P", "solution": "S"}
    manifest = plan_deck(bundle, llm_cache=cache, user_notes="  hi  ")
    assert manifest.user_notes == "hi"
    assert manifest.llm_cache == cache
    ps = next(s for s in manifest.slides if s.template_id == "problem_solution")
    assert ps.holes["problem"].text == "P"
    assert ps.holes["solution"].text == "S"
