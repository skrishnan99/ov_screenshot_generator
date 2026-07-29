"""Camera variant + UI version detection.

The cache key for traces is `<ui-version>_<asset-hash>`: the human-readable
version string the UI renders (e.g. "v2026.6.0-OV80i") plus a hash of the
app's content-hashed bundle URLs, which discriminates dev builds whose version
string doesn't change (e.g. "local_develop"). Wrong keys are safe — replay
validates postconditions and falls back to the agent — so this only needs to
be a good cache key, not a guarantee.
"""

from __future__ import annotations

import hashlib
import re


def detect_variant(title: str, page_text: str) -> str:
    m = re.search(r"\bOV\d+\w*\b", title + " " + page_text, re.IGNORECASE)
    return m.group(0).lower() if m else "unknown-variant"


def detect_ui_version(page_text: str, html: str) -> str:
    m = re.search(r"Version:\s*(\S+)", page_text)
    version = m.group(1) if m else "noversion"

    assets = sorted(set(re.findall(r'(?:src|href)="(/[^"]+\.(?:js|css))"', html)))
    asset_hash = (
        hashlib.sha256("\n".join(assets).encode()).hexdigest()[:10] if assets else "noassets"
    )
    return f"{version}_{asset_hash}"
