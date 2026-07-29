"""Command-line entry point.

::

    python -m deck_builder RUN_DIR [--user-dir DIR] [--out-dir DIR]
        [--folder-id ID] [--skip-llm] [--skip-drive] [--new] [--force]
        [--model MODEL]

Prints progress per stage and, on success, the Google Drive link of
the exported native Google Slides deck (plus the local merged pptx
path).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from deck_builder.errors import DeckBuilderError


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deck_builder",
        description="Build a Google Slides case-study deck from a "
                    "screenshot_generator run directory.",
    )
    parser.add_argument(
        "run_dir",
        help="Run directory (contains manifest.json, descriptions.json, screenshots)",
    )
    parser.add_argument(
        "--user-dir", default=None,
        help="User-context directory: notes.md + images + optional captions.json. "
             "User content always outranks system-generated content.",
    )
    parser.add_argument(
        "--out-dir", default=None,
        help="Output directory for deck artifacts (default: out/<run_id>)",
    )
    parser.add_argument(
        "--folder-id", default=None,
        help="Destination Drive folder / shared-drive id "
             "(default: $DRIVE_EXPORT_FOLDER_ID; unset targets My Drive root)",
    )
    parser.add_argument(
        "--skip-llm", action="store_true",
        help="No Claude calls: deterministic slide copy, no user-image matching",
    )
    parser.add_argument(
        "--skip-drive", action="store_true",
        help="Stop after the local merged .pptx (no Google upload)",
    )
    parser.add_argument(
        "--new", action="store_true",
        help="Create a fresh Drive file instead of updating this run's previous export",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-render every slide even when nothing changed",
    )
    parser.add_argument(
        "--model", default=None,
        help="Anthropic model id for the copy + matching passes "
             "(default: $DECK_LLM_MODEL or claude-sonnet-5)",
    )
    args = parser.parse_args(argv)

    from deck_builder import build_deck

    try:
        result = build_deck(
            args.run_dir,
            user_dir=args.user_dir,
            out_dir=args.out_dir,
            folder_id=args.folder_id,
            skip_llm=args.skip_llm,
            skip_drive=args.skip_drive,
            new_drive_file=args.new,
            llm_model=args.model,
            force=args.force,
        )
    except DeckBuilderError as exc:
        print(f"deck build failed:\n{exc}", file=sys.stderr)
        return 1

    m = result.manifest
    print(f"Deck: {m.recipe_name} — {len(m.slides)} slides "
          f"(variant: {m.camera_variant or 'default'})")
    applied = [a for a in m.user_assignments if a.applied]
    if m.user_assignments:
        print(f"User images: {len(applied)} placed, "
              f"{len(m.user_assignments) - len(applied)} left unused")
        for a in m.user_assignments:
            flag = "APPLIED" if a.applied else (a.confidence or "unused")
            print(f"  {Path(a.image_path).name} -> {a.target} [{flag}] {a.reason}")
    if result.merged_pptx:
        print(f"Merged pptx: {result.merged_pptx}")
    for w in result.warnings:
        print(f"warning: {w}")
    if result.drive_link:
        print(f"Google Slides: {result.drive_link}")
    elif not args.skip_drive:
        print("warning: Drive export produced no link", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
