"""Loader for the optional user-context directory.

User-provided context always outranks system-generated content. The
directory convention is deliberately simple:

* ``notes.md`` (or ``notes.txt``) — free-form text. Fed to the LLM
  copy pass as grounding context; anything the user states here about
  the problem, the solution, deployment time, etc. takes precedence
  over what the system infers from screenshots.
* image files (``.png``/``.jpg``/``.jpeg``/``.webp``) — user
  screenshots/photos. The asset matcher places them into image holes,
  replacing system screenshots wherever it is confident.
* ``captions.json`` (optional) — ``{"filename.png": "caption"}``.
  Captions sharpen matching considerably; encourage users to provide
  them.

Any other files are ignored. A missing directory yields an empty
context — the deck then builds fully system-generated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from deck_builder.errors import UserContextError

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_NOTES_NAMES = ("notes.md", "notes.txt", "context.md", "context.txt")


@dataclass(frozen=True)
class UserImage:
    path: Path
    caption: str = ""


@dataclass(frozen=True)
class UserContext:
    notes: str = ""
    images: list[UserImage] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.notes and not self.images


def load_user_context(user_dir: str | Path | None) -> UserContext:
    """Load a user-context directory. ``None`` → empty context.

    Raises ``UserContextError`` only when the path was given but is not
    a directory — malformed optional pieces (bad captions.json) degrade
    silently to "no captions" rather than blocking the deck.
    """
    if user_dir is None:
        return UserContext()
    user_dir = Path(user_dir).resolve()
    if not user_dir.is_dir():
        raise UserContextError(f"User context directory not found: {user_dir}")

    notes = ""
    for name in _NOTES_NAMES:
        p = user_dir / name
        if p.exists():
            try:
                notes = p.read_text().strip()
            except Exception:
                notes = ""
            break

    captions = _load_captions(user_dir)

    images: list[UserImage] = []
    for p in sorted(user_dir.iterdir()):
        if p.suffix.lower() in _IMAGE_SUFFIXES and p.is_file():
            images.append(UserImage(path=p, caption=captions.get(p.name, "")))

    return UserContext(notes=notes, images=images)


def _load_captions(user_dir: Path) -> dict[str, str]:
    p = user_dir / "captions.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


__all__ = ["UserContext", "UserImage", "load_user_context"]
