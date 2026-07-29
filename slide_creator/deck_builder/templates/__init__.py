"""Template registry — same pipeline structure as recipe_decryption.

Templates are modules; this file lists them and provides lookup
helpers. The three-phase deck order (GLOBAL_HEAD → per-model
BLOCK_GROUPS in fixed type order → GLOBAL_TAIL) is copied verbatim
from ``recipe_decryption/case_study/templates/__init__.py``.
"""

from __future__ import annotations

from deck_builder.errors import UnknownTemplateError
from deck_builder.templates.base import (
    SlideTemplateModule,
    TemplateContext,
    validate_template_module,
)

from deck_builder.templates import (
    recipe_title,
    problem_solution,
    results_image,
    configuring_ov80i,
    basic_camera_info,
    advanced_camera_info,
    unique_factors,
    defect_generator_info,
    integration_info,
    team_and_locations,
    imaging_setup,
    aligner_setup,
    roi_setup,
    classifier_setup,
    cls_rois_setup,
    segmenter_setup,
    training_stats,
    nodered_setup,
    library,
    results,
    concise_results_classifier,
    concise_results_segmenter,
    contact,
)

# ---------------------------------------------------------------------------
# Pipeline structure (identical slide order to recipe_decryption)
# ---------------------------------------------------------------------------
#
#   1. Recipe title
#   2. Problem & Solution
#   3. Results with image (one per model)
#   4. Configuring OV80i (hardcoded)
#   5. Imaging Settings
#   6. Aligner setup
#   7. ROI setup
#   8. (Classifier + ROIs + Training Stats + Concise Results) per
#      classification model | (Segmenter + Training Stats + Concise
#      Results) per segmentation model — classification models first
#   9. Node-RED description (conditional)
#  10. Library screen
#  11. Results (hardcoded) … Contact (hardcoded)

GLOBAL_HEAD: list[SlideTemplateModule] = [
    recipe_title,
    problem_solution,
    results_image,
    configuring_ov80i,
    imaging_setup,
]

# Per-type template groups. The planner walks block types in
# BLOCK_TYPE_ORDER; for each context of that type (an alignment/ROI
# singleton or a ModelInfo) it emits every template in the group whose
# ``applies()`` includes the context.
BLOCK_GROUPS: dict[str, list[SlideTemplateModule]] = {
    "alignment": [aligner_setup],
    "roi": [roi_setup],
    "classification": [
        classifier_setup, cls_rois_setup, training_stats,
        concise_results_classifier,
    ],
    "segmentation": [segmenter_setup, training_stats, concise_results_segmenter],
}

BLOCK_TYPE_ORDER = ["alignment", "roi", "classification", "segmentation"]

GLOBAL_TAIL: list[SlideTemplateModule] = [
    nodered_setup,
    library,
    results,
    basic_camera_info,
    advanced_camera_info,
    unique_factors,
    defect_generator_info,
    integration_info,
    team_and_locations,
    contact,
]

ALL_TEMPLATES: list[SlideTemplateModule] = [
    recipe_title,
    problem_solution,
    results_image,
    configuring_ov80i,
    basic_camera_info,
    advanced_camera_info,
    unique_factors,
    defect_generator_info,
    integration_info,
    team_and_locations,
    imaging_setup,
    aligner_setup,
    roi_setup,
    classifier_setup,
    cls_rois_setup,
    segmenter_setup,
    training_stats,
    nodered_setup,
    library,
    results,
    concise_results_classifier,
    concise_results_segmenter,
    contact,
]


def register_all() -> None:
    """Validate every listed template at import time (fail loudly)."""
    for module in ALL_TEMPLATES:
        validate_template_module(module)


def get_template(template_id: str) -> SlideTemplateModule:
    for module in ALL_TEMPLATES:
        if module.TEMPLATE_ID == template_id:
            return module
    known = [m.TEMPLATE_ID for m in ALL_TEMPLATES]
    raise UnknownTemplateError(
        f"No template registered with TEMPLATE_ID={template_id!r}. Known: {known}"
    )


register_all()


__all__ = [
    "ALL_TEMPLATES",
    "BLOCK_GROUPS",
    "BLOCK_TYPE_ORDER",
    "GLOBAL_HEAD",
    "GLOBAL_TAIL",
    "SlideTemplateModule",
    "TemplateContext",
    "get_template",
    "register_all",
    "validate_template_module",
]
