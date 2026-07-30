#!/usr/bin/env python3
"""Upload a finished deck (and optionally its source assets) to Google Drive.

    python scripts/publish.py out/report.pptx
    python scripts/publish.py out/report.pptx --assets runs/20260730_005318 --dry-run

The deck is converted to Google Slides on upload and collected in the
'OV Test Reports' Drive library.

Drive credentials are not re-implemented here. This delegates to the
ov-test-reports plugin's publish_cli.py, which already owns the OAuth flow and
is the only sanctioned upload path. The first upload on a machine opens a
browser for a one-time Google consent; after that it is silent.
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
from pathlib import Path

# This skill ships inside the ov-test-reports plugin, so the publisher is
# normally three levels up (scripts/ -> overview-deck/ -> skills/ -> root).
# That path is preferred: it is the plugin this copy of the skill belongs to,
# and it is the only one that resolves when running from a source checkout
# rather than an installed plugin.
BUNDLED_PUBLISHER = Path(__file__).resolve().parents[3] / "publish_cli.py"

PLUGIN_GLOBS = [
    os.path.expanduser("~/.claude/plugins/cache/*/ov-test-reports/*/publish_cli.py"),
    os.path.expanduser("~/.claude/plugins/*/ov-test-reports/*/publish_cli.py"),
]


def find_publisher() -> Path | None:
    if BUNDLED_PUBLISHER.exists():
        return BUNDLED_PUBLISHER
    hits: list[str] = []
    for g in PLUGIN_GLOBS:
        hits.extend(glob.glob(g))
    if not hits:
        return None
    # newest version wins
    return Path(sorted(hits)[-1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx")
    ap.add_argument("--assets", default=None,
                    help="extractor run dir whose deliverables/ and data/ to upload alongside")
    ap.add_argument("--name", default=None, help="override the Drive folder name")
    ap.add_argument("--dry-run", action="store_true",
                    help="print exactly what would be uploaded, touch nothing")
    a = ap.parse_args()

    pptx = Path(a.pptx).resolve()
    if not pptx.exists():
        print(f"ERROR: {pptx} does not exist")
        return 1

    pub = find_publisher()
    if not pub:
        print(
            "ERROR: could not find the ov-test-reports plugin's publish_cli.py.\n"
            "  Drive upload runs through that plugin. Either install it, or upload\n"
            f"  {pptx.name} to Drive manually and convert it to Google Slides."
        )
        return 1

    project = pub.parent
    cmd = ["uv", "run", "--project", str(project), "python", str(pub)]
    if not a.dry_run:
        cmd.append("publish")
    cmd += ["--deck", str(pptx)]
    if a.assets:
        # --run alone means "deck only" when a deck is also given: the shared
        # drive holds finished decks, not asset dumps. Asking for assets here
        # must say so explicitly, or they are silently dropped.
        cmd += ["--run", str(Path(a.assets).resolve()), "--assets"]
    if a.name:
        cmd += ["--name", a.name]
    if a.dry_run:
        cmd.append("--dry-run")

    print("$", " ".join(cmd), "\n")
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())
