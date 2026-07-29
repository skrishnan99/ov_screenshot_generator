"""Pydantic schema for the deck document.

Simplified from ``recipe_decryption/case_study/manifest.py``: the
recipe pipeline needed six ``HoleValue`` kinds because holes were
re-resolved through HTML screen populators and overlay drawers. Here
every hole lowers directly to what ``slide_creator.render`` consumes,
so two kinds suffice:

* ``text``  — a plain string for a ``{{token}}`` text hole.
* ``image`` — a path to a PNG/JPEG on disk for a picture hole, tagged
  with its provenance (``system`` screenshot vs ``user`` upload) so
  prioritization decisions stay auditable.

The manifest is the only stateful object in the pipeline and lives in
a single JSON file per deck run (see ``persistence.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Hole values
# ---------------------------------------------------------------------------

class TextValue(BaseModel):
    """Plain text for a ``{{token}}`` hole."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["text"] = "text"
    text: str = ""


class ImageValue(BaseModel):
    """Path to an image file for a picture hole.

    ``source`` records where the image came from:
      * ``system`` — a screenshot captured by the screenshot_generator run.
      * ``user``   — an upload from the user-context directory (always
        wins over a system default when the asset matcher is confident).
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["image"] = "image"
    path: str
    source: Literal["system", "user"] = "system"
    label: str = ""


HoleValue = Union[TextValue, ImageValue]


# ---------------------------------------------------------------------------
# Hole specs (per-template declarations)
# ---------------------------------------------------------------------------

class HoleSpec(BaseModel):
    """Declaration of one hole in a template's schema.

    ``kind`` is the slide_creator target ("text"/"image") directly —
    there is no separate kind registry in this package. ``match_hint``
    feeds the user-asset matcher: a one-line description of what a
    good image for this hole looks like.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    kind: Literal["text", "image"]
    label: str = ""
    editor_config: dict[str, Any] = Field(default_factory=dict)
    match_hint: Optional[str] = None


# ---------------------------------------------------------------------------
# Slides
# ---------------------------------------------------------------------------

class RenderCache(BaseModel):
    """On-disk artifacts of the last successful render of a slide."""

    pptx_path: str
    fingerprint: str
    rendered_at: str


class SlideSpec(BaseModel):
    """One slide of the deck: template + hole values."""

    id: str
    order: int
    kind: Literal["templated", "hardcoded"]
    template_id: str
    label: str
    # For per-model slides: the model name this slide belongs to
    # (e.g. "Horn Quality"). None for recipe-global slides.
    model_name: Optional[str] = None
    holes: dict[str, HoleValue] = Field(default_factory=dict)
    hole_specs: dict[str, HoleSpec] = Field(default_factory=dict)
    render_cache: Optional[RenderCache] = None


# ---------------------------------------------------------------------------
# User context audit + Drive export
# ---------------------------------------------------------------------------

class UserAssignment(BaseModel):
    """One decision of the user-asset matcher, kept for auditing.

    ``target`` is ``"<slide_id>/<hole_name>"`` for applied matches or
    ``"unused"`` when the matcher declined to place the image.
    """

    image_path: str
    target: str
    confidence: str = ""
    reason: str = ""
    applied: bool = False


class DriveExportInfo(BaseModel):
    """Identity of the exported Google Slides file."""

    file_id: str
    web_view_link: str
    name: str
    folder_id: Optional[str] = None
    exported_at: str


# ---------------------------------------------------------------------------
# Deck manifest
# ---------------------------------------------------------------------------

class DeckManifest(BaseModel):
    """The whole deck document for one run."""

    run_id: str
    run_dir: str
    camera_variant: Optional[str] = None
    recipe_name: str = ""
    user_notes: str = ""
    slides: list[SlideSpec] = Field(default_factory=list)
    llm_cache: dict[str, Any] = Field(default_factory=dict)
    user_assignments: list[UserAssignment] = Field(default_factory=list)
    drive_export: Optional[DriveExportInfo] = None

    def get_slide(self, slide_id: str) -> SlideSpec:
        for slide in self.slides:
            if slide.id == slide_id:
                return slide
        raise KeyError(f"No slide with id {slide_id!r}")

    def replace_slide(self, updated: SlideSpec) -> None:
        for i, slide in enumerate(self.slides):
            if slide.id == updated.id:
                self.slides[i] = updated
                return
        raise KeyError(f"No slide with id {updated.id!r}")


__all__ = [
    "DeckManifest",
    "DriveExportInfo",
    "HoleSpec",
    "HoleValue",
    "ImageValue",
    "RenderCache",
    "SlideSpec",
    "TextValue",
    "UserAssignment",
]
