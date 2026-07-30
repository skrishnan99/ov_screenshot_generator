"""Publish a run's assets and deck to Google Drive (deck as Google Slides).

Usage:
  uv run python publish_cli.py login          # one-time Google sign-in
  uv run python publish_cli.py status         # is this machine signed in?
  uv run python publish_cli.py logout
  uv run python publish_cli.py --run runs/<ts> [--deck path/to/deck.pptx] [--all]

Publishing is additive and never overwrites a previous upload: every publish
creates a new timestamped folder in the engineer's own Drive.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from publish import gdrive


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "action",
        nargs="?",
        default="publish",
        choices=["publish", "login", "logout", "status"],
    )
    ap.add_argument("--run", help="Extractor run directory whose assets to publish")
    ap.add_argument("--deck", help="Deck .pptx to upload and convert to Google Slides")
    ap.add_argument(
        "--all",
        action="store_true",
        help="Also publish archive/ and debug/ (default: deliverables/ and data/)",
    )
    ap.add_argument("--name", help="Override this report's Drive folder name")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Show exactly what would be uploaded (tree, file count, total size) "
        "without touching Drive or needing credentials",
    )
    ap.add_argument(
        "--library",
        default=gdrive.DEFAULT_LIBRARY,
        help="Drive folder that collects every report (default: "
        f"{gdrive.DEFAULT_LIBRARY!r}); pass '' to publish to your Drive root",
    )
    args = ap.parse_args()

    if args.action == "login":
        try:
            gdrive.credentials()
        except gdrive.AuthError as e:
            print(f"cannot sign in: {e}", file=sys.stderr)
            return 1
        print("signed in to Google Drive")
        return 0
    if args.action == "logout":
        print("signed out" if gdrive.logout() else "was not signed in")
        return 0
    if args.action == "status":
        state = gdrive.auth_state()
        print(("ready: " if state["ready"] else "not ready: ") + state["reason"])
        return 0 if state["ready"] else 1

    if not args.run and not args.deck:
        print("nothing to publish: pass --run and/or --deck", file=sys.stderr)
        return 2
    for label, value in (("run", args.run), ("deck", args.deck)):
        if value and not Path(value).exists():
            print(f"{label} not found: {value}", file=sys.stderr)
            return 2
    if args.dry_run:
        plan = gdrive.plan_publish(
            Path(args.run) if args.run else None,
            Path(args.deck) if args.deck else None,
            include=gdrive.ALL_INCLUDE if args.all else gdrive.DEFAULT_INCLUDE,
            folder_name=args.name,
            library=args.library or None,
        )
        for line in plan["tree"]:
            print(line)
        print(
            f"\n{plan['file_count']} file(s), {plan['total_bytes'] / 1e6:.1f} MB "
            f"— nothing uploaded (dry run)"
        )
        return 0

    try:
        report = gdrive.publish(
            Path(args.run) if args.run else None,
            Path(args.deck) if args.deck else None,
            include=gdrive.ALL_INCLUDE if args.all else gdrive.DEFAULT_INCLUDE,
            folder_name=args.name,
            library=args.library or None,
        )
    except gdrive.AuthError as e:
        print(f"\nGoogle sign-in needed: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\npublish failed: {e}", file=sys.stderr)
        return 1

    if report.get("library_link"):
        print("\nReport library: " + report["library_link"])
    print("This report: " + (report["folder_link"] or "?"))
    if report.get("slides_link"):
        print("Google Slides: " + report["slides_link"])
    if report["failed"]:
        print(f"\n{len(report['failed'])} file(s) did not upload:", file=sys.stderr)
        for f in report["failed"][:10]:
            print(f"  {f['file']}: {f['error'][:120]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
