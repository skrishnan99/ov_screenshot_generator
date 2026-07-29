"""Every template's declared HOLE_SCHEMA must match its skeleton's
actual holes — names AND kinds. This is the regression net for anyone
editing a skeleton in Google Slides and re-exporting it."""

import pytest
from pptx import Presentation

from deck_builder.templates import ALL_TEMPLATES, get_template, register_all
from deck_builder.errors import UnknownTemplateError
from deck_builder.variant import resolve_variant_skeleton


@pytest.mark.parametrize("template", ALL_TEMPLATES, ids=lambda t: t.TEMPLATE_ID)
def test_schema_matches_skeleton(template):
    from slide_creator import list_holes

    prs = Presentation(str(template.SKELETON_PATH))
    holes = {h.name: h.type.value for h in list_holes(prs)}
    schema = {spec.name: spec.kind for spec in template.HOLE_SCHEMA}
    assert schema == holes, (
        f"{template.TEMPLATE_ID}: schema {schema} != skeleton holes {holes}"
    )


def test_variant_skeleton_resolution():
    base = get_template("recipe_title").SKELETON_PATH
    # ov20i has an override on disk; ov80i does not (falls back to base).
    assert resolve_variant_skeleton(base, "ov20i").parent.name == "ov20i"
    assert resolve_variant_skeleton(base, "ov80i") == base
    assert resolve_variant_skeleton(base, None) == base


def test_registry_contract():
    register_all()  # raises on any broken template
    with pytest.raises(UnknownTemplateError):
        get_template("not_a_template")
