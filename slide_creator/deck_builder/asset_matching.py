"""Match user-uploaded images to deck image holes with a vision model.

Adapted from ``recipe_decryption/case_study/asset_matching.py``. Same
design rules:

* **Failure is a no-op.** ``auto_assign`` never raises — any error
  yields zero assignments and the deck proceeds fully system-generated.
* **Leaving images unused is a correct outcome**, stated explicitly in
  the prompt; the matcher must prefer "unused" over a forced guess.
* **Only high-confidence matches are applied** (user data replaces the
  system screenshot); medium/low decisions are recorded as audit only.
* **Every decision is auditable** on ``manifest.user_assignments``.
"""

from __future__ import annotations

import base64
import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

from deck_builder.llm import DEFAULT_MODEL, load_env
from deck_builder.manifest import DeckManifest, ImageValue, UserAssignment
from deck_builder.user_context import UserContext, UserImage
from deck_builder.variant import display_name

_MAX_IMAGE_DIM = 1024  # px — plenty for "which screen is this?"
_MAX_IMAGES_PER_CALL = 20

_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SlideTarget:
    """One image hole a user image could be assigned to."""

    slide_id: str
    hole_name: str
    label: str
    hint: str


def build_slide_roster(manifest: DeckManifest) -> list[SlideTarget]:
    """Every image hole in the deck, in deck order."""
    roster: list[SlideTarget] = []
    for slide in manifest.slides:
        for name, spec in slide.hole_specs.items():
            if spec.kind != "image":
                continue
            hint = spec.match_hint or (
                f"The '{name}' image on the '{slide.label}' slide"
            )
            if slide.model_name:
                hint = f"{hint} (model: {slide.model_name})"
            roster.append(SlideTarget(
                slide_id=slide.id, hole_name=name, label=slide.label, hint=hint,
            ))
    return roster


# ---------------------------------------------------------------------------
# Matching + application
# ---------------------------------------------------------------------------

@dataclass
class MatchDecision:
    image: UserImage
    slide_id: Optional[str]
    hole_name: Optional[str]
    confidence: str
    reason: str


def auto_assign(
    manifest: DeckManifest,
    user_context: UserContext,
    *,
    model: str = DEFAULT_MODEL,
    min_confidence: Literal["high", "medium", "low"] = "high",
) -> list[UserAssignment]:
    """Full pass: roster → vision match → apply confident matches.

    Mutates the manifest (hole values, render caches, audit list) and
    returns the audit list. Never raises.
    """
    if not user_context.images:
        return []
    roster = build_slide_roster(manifest)
    if not roster:
        manifest.user_assignments = [
            UserAssignment(image_path=str(img.path), target="unused",
                           reason="Deck has no image holes.")
            for img in user_context.images
        ]
        return manifest.user_assignments

    load_env()
    try:
        decisions = _match_images(
            user_context.images[:_MAX_IMAGES_PER_CALL],
            roster,
            user_notes=user_context.notes,
            deck_summary=_deck_summary(manifest),
            device_name=display_name(manifest.camera_variant),
            model=model,
        )
    except Exception as exc:
        print(
            f"asset_matching: failed ({exc}) — deck continues fully system-generated",
            file=sys.stderr,
        )
        manifest.user_assignments = [
            UserAssignment(image_path=str(img.path), target="unused",
                           reason=f"Matching failed: {exc}")
            for img in user_context.images
        ]
        return manifest.user_assignments

    return _apply(manifest, decisions, min_confidence=min_confidence)


def _apply(
    manifest: DeckManifest,
    decisions: list[MatchDecision],
    *,
    min_confidence: str,
) -> list[UserAssignment]:
    """Write confident decisions into slide holes; record everything.

    Per hole, the best-confidence decision wins (dedupe when two images
    target the same hole). User images always REPLACE the system
    default — that is the prioritization contract of this pipeline.
    """
    valid = {(t.slide_id, t.hole_name) for t in build_slide_roster(manifest)}
    threshold = _CONFIDENCE_RANK.get(min_confidence, 3)

    best_for_hole: dict[tuple[str, str], MatchDecision] = {}
    for d in decisions:
        if not d.slide_id or not d.hole_name:
            continue
        key = (d.slide_id, d.hole_name)
        current = best_for_hole.get(key)
        if current is None or (
            _CONFIDENCE_RANK.get(d.confidence, 0) > _CONFIDENCE_RANK.get(current.confidence, 0)
        ):
            best_for_hole[key] = d

    audit: list[UserAssignment] = []
    for d in decisions:
        key = (d.slide_id, d.hole_name) if d.slide_id and d.hole_name else None
        applied = (
            key is not None
            and key in valid
            and best_for_hole.get(key) is d
            and _CONFIDENCE_RANK.get(d.confidence, 0) >= threshold
        )
        if applied:
            slide = manifest.get_slide(d.slide_id)  # type: ignore[arg-type]
            updated = slide.model_copy(update={
                "holes": {
                    **slide.holes,
                    d.hole_name: ImageValue(
                        path=str(d.image.path), source="user",
                        label=d.image.caption or d.image.path.name,
                    ),
                },
                "render_cache": None,
            })
            manifest.replace_slide(updated)
        audit.append(UserAssignment(
            image_path=str(d.image.path),
            target=f"{d.slide_id}/{d.hole_name}" if key else "unused",
            confidence=d.confidence,
            reason=d.reason,
            applied=applied,
        ))

    manifest.user_assignments = audit
    return audit


# ---------------------------------------------------------------------------
# The vision call
# ---------------------------------------------------------------------------

def _match_images(
    images: list[UserImage],
    roster: list[SlideTarget],
    *,
    user_notes: str,
    deck_summary: str,
    device_name: str,
    model: str,
    max_tokens: int = 2048,
) -> list[MatchDecision]:
    import anthropic

    content: list[dict[str, Any]] = []
    ids = [f"u{i + 1}" for i in range(len(images))]
    for image_id, img in zip(ids, images):
        caption = f' User\'s caption: "{img.caption}"' if img.caption else ""
        content.append({
            "type": "text",
            "text": f"Image [{image_id}] — original filename: {img.path.name}.{caption}",
        })
        media_type, data = _encode_image(img.path)
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        })

    targets_lines = "\n".join(
        f"- slide_id={t.slide_id!r} hole_name={t.hole_name!r}: {t.hint}"
        for t in roster
    )
    notes_block = (
        f"\n<user_context>\n{user_notes.strip()}\n</user_context>\n"
        if user_notes.strip() else ""
    )
    content.append({
        "type": "text",
        "text": f"""You are placing a user's uploaded images into a case-study slide deck about an Overview {device_name} vision-inspection recipe.

<deck_overview>
{deck_summary}
</deck_overview>
{notes_block}
The deck has these image slots (each currently holds a system-generated product screenshot; a matched user image REPLACES it):
{targets_lines}

For EACH image above, decide which slot (if any) it belongs in. Rules:
- An image fits a slot only when its content clearly corresponds to what the slot shows (e.g. a photo of the physical part fits the example-image slots; a screenshot of the OV imaging screen fits the imaging slot).
- Leaving an image unused (slide_id=null) is a correct and common outcome. Prefer null over a forced guess.
- Assign at most one image per slot; pick the best fit.
- confidence "high" means you would bet the user intended exactly this placement; it is applied automatically and replaces the system screenshot. Use "medium"/"low" for plausible-but-uncertain placements — those are recorded as suggestions only.

Call the `submit_matches` tool with one entry per image, using the exact image ids in brackets and the exact slide_id/hole_name strings listed above.""",
    })

    schema = {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "image_id": {"type": "string"},
                        "slide_id": {"type": ["string", "null"]},
                        "hole_name": {"type": ["string", "null"]},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "reason": {"type": "string", "description": "One short sentence."},
                    },
                    "required": ["image_id", "slide_id", "hole_name", "confidence", "reason"],
                },
            },
        },
        "required": ["matches"],
    }

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        tools=[{
            "name": "submit_matches",
            "description": "Submit the image-to-slide placement decisions.",
            "input_schema": schema,
        }],
        tool_choice={"type": "tool", "name": "submit_matches"},
        messages=[{"role": "user", "content": content}],
    )
    tool_use = next(b for b in response.content if b.type == "tool_use")

    by_id = dict(zip(ids, images))
    decisions: list[MatchDecision] = []
    mentioned: set[str] = set()
    for m in tool_use.input.get("matches", []):
        image_id = m.get("image_id")
        img = by_id.get(image_id)
        if img is None:
            continue  # hallucinated id — drop
        mentioned.add(image_id)
        decisions.append(MatchDecision(
            image=img,
            slide_id=m.get("slide_id"),
            hole_name=m.get("hole_name"),
            confidence=m.get("confidence", "low"),
            reason=m.get("reason", ""),
        ))
    for image_id, img in by_id.items():
        if image_id not in mentioned:
            decisions.append(MatchDecision(
                image=img, slide_id=None, hole_name=None,
                confidence="low", reason="Not matched by the model.",
            ))
    return decisions


def _encode_image(path: Path) -> tuple[str, str]:
    """Downscale to ≤1024px and return (media_type, base64 data)."""
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((_MAX_IMAGE_DIM, _MAX_IMAGE_DIM))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
    return "image/jpeg", base64.b64encode(buf.getvalue()).decode("ascii")


def _deck_summary(manifest: DeckManifest) -> str:
    models = sorted({
        f"{s.model_name}" for s in manifest.slides if s.model_name
    })
    return (
        f"Recipe: {manifest.recipe_name}. "
        f"Inspection models: {', '.join(models) or 'n/a'}. "
        f"Deck of {len(manifest.slides)} slides."
    )


__all__ = ["SlideTarget", "auto_assign", "build_slide_roster"]
