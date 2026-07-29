"""Typed exceptions for the deck_builder package.

Mirrors the error layering of ``recipe_decryption/case_study``: one
base class so callers can catch everything from this package with a
single ``except DeckBuilderError``, plus narrow subclasses that let
the CLI report the failing stage precisely.
"""

from __future__ import annotations


class DeckBuilderError(Exception):
    """Base class for every error raised by deck_builder."""


class RunBundleError(DeckBuilderError):
    """The run directory is missing, malformed, or unusable."""


class UserContextError(DeckBuilderError):
    """The user-context directory is malformed."""


class TemplateContractError(DeckBuilderError):
    """A template module does not conform to the module contract."""


class UnknownTemplateError(DeckBuilderError):
    """No template registered with the requested TEMPLATE_ID."""


class RenderError(DeckBuilderError):
    """A slide failed to render.

    Carries the slide id and template id so batch callers can report
    exactly which slide broke without parsing the message.
    """

    def __init__(self, message: str, *, slide_id: str = "?", template_id: str = "?"):
        super().__init__(f"[slide={slide_id} template={template_id}] {message}")
        self.slide_id = slide_id
        self.template_id = template_id


class DriveAuthError(DeckBuilderError):
    """Google OAuth credentials are missing or the consent flow failed."""


class DriveExportError(DeckBuilderError):
    """The Drive upload / merge step failed."""


class NothingToExportError(DriveExportError):
    """No rendered slides exist to merge or export."""
