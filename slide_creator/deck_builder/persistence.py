"""JSON read/write for deck manifests.

One file per deck run: ``<out_dir>/deck_manifest.json``. Kept as a
module (not methods on ``DeckManifest``) so storage concerns stay off
the schema, same as recipe_decryption.
"""

from __future__ import annotations

import json
from pathlib import Path

from deck_builder.manifest import DeckManifest

_MANIFEST_NAME = "deck_manifest.json"


def save_manifest(manifest: DeckManifest, out_dir: str | Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _MANIFEST_NAME
    path.write_text(json.dumps(manifest.model_dump(mode="json"), indent=2))
    return path


def load_manifest(out_dir: str | Path) -> DeckManifest:
    path = Path(out_dir) / _MANIFEST_NAME
    return DeckManifest.model_validate(json.loads(path.read_text()))


__all__ = ["load_manifest", "save_manifest"]
