from deck_builder.run_bundle import parse_model_label


def test_recipe_name_and_variant(bundle):
    assert bundle.recipe_name == "#4 Camera 56959 Tail"
    assert bundle.camera_variant == "ov80i"


def test_model_discovery_dedupes_across_steps(bundle):
    """The three manifest steps case the type differently; models must
    still merge into one entry each, in inspection_rois order."""
    assert [(m.name, m.block_type) for m in bundle.models] == [
        ("Horn Quality", "classification"),
        ("Cracks", "segmentation"),
        ("Hole Presence", "classification"),
    ]


def test_models_carry_their_screenshots(bundle):
    by_name = {m.name: m for m in bundle.models}
    horn = by_name["Horn Quality"]
    assert horn.roi_screenshot.name == "04_roi_horn-quality-classification.png"
    assert horn.report_screenshot.name == "horn-quality_classification.png"
    assert horn.settings_screenshot.name == "horn-quality_classification_settings.png"
    # Cracks has no training report in this run.
    assert by_name["Cracks"].report_screenshot is None


def test_step_screenshots(bundle):
    assert bundle.screenshot("imaging_setup").name == "02_imaging_setup.png"
    assert bundle.screenshot("library").name == "11_library.png"
    assert bundle.screenshot("nonexistent_step") is None


def test_descriptions_indexed_by_filename(bundle):
    shot = bundle.screenshot("imaging_setup")
    assert "Imaging Setup" in bundle.description_for(shot)
    assert bundle.description_for(None) == ""


def test_parse_model_label():
    assert parse_model_label("Horn Quality (Classification)") == (
        "Horn Quality", "classification",
    )
    assert parse_model_label("Cracks (segmentation)") == ("Cracks", "segmentation")
    assert parse_model_label("Weird (OCR)") is None
    assert parse_model_label("no type at all") is None
