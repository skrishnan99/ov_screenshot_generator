"""Camera-variant skeleton resolution (OV10i / OV20i / OV80i).

Copied from ``recipe_decryption/recipe_core/camera_variant.py`` (the
two functions this package needs). Variant skeletons are pure fallback
overlays: ``skeletons/<variant>/<name>.pptx`` overrides
``skeletons/<name>.pptx`` when it exists; anything without an override
serves the default asset. Adding a variant-specific slide is a file
drop, never a code change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

CAMERA_VARIANTS: tuple[str, ...] = ("ov10i", "ov20i", "ov80i")

_DISPLAY_NAMES = {"ov10i": "OV10i", "ov20i": "OV20i", "ov80i": "OV80i"}


def display_name(variant: Optional[str]) -> str:
    """Human/product-facing form ("ov80i" → "OV80i"). OV20i when unset."""
    return _DISPLAY_NAMES.get((variant or "").lower(), "OV20i")


def resolve_variant_skeleton(base_path: Path, variant: Optional[str]) -> Path:
    """Pick a skeleton file for the variant, falling back to the base."""
    base_path = Path(base_path)
    if variant:
        candidate = base_path.parent / variant / base_path.name
        if candidate.exists():
            return candidate
    return base_path


__all__ = ["CAMERA_VARIANTS", "display_name", "resolve_variant_skeleton"]
