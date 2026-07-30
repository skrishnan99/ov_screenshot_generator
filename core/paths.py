"""Filesystem layout for both dev checkouts and installed-plugin use.

Three location classes:

- PACKAGE_ROOT: the code and its read-only resources (task specs, deck
  specs, skeletons, bundled seed traces). When installed as a Claude Code
  plugin this directory should never be written to.
- data_dir(): per-user writable state (trace cache, .env with the team API
  key). Defaults to ~/.ov-report-generator; override with OV_REPORT_DATA_DIR.
- output_base(): where run artifacts (runs/, deck_outputs/) are created.
  Defaults to the current working directory — an engineer using the skill
  gets outputs where they invoked it; override with OV_REPORT_OUTPUT_DIR.
  A dev working inside the checkout sees the historical layout unchanged.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    d = Path(os.environ.get("OV_REPORT_DATA_DIR", "~/.ov-report-generator")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def output_base() -> Path:
    return Path(os.environ.get("OV_REPORT_OUTPUT_DIR", os.getcwd()))


def traces_dir() -> Path:
    """Writable trace cache, seeded once from the traces bundled with the
    package so a fresh install replays known UI versions instead of paying
    for agent discovery."""
    d = data_dir() / "traces"
    bundled = PACKAGE_ROOT / "traces"
    if not d.exists():
        if bundled.is_dir():
            shutil.copytree(bundled, d)
        else:
            d.mkdir(parents=True)
    return d
