"""End-to-end pipeline: URL + recipe in, slide deck out.

Runs the screenshot extractor and then the deck generator as one command:

  uv run python pipeline.py --url http://<camera> --recipe "70% real ng data" \
      [--context notes.md] [--images photos/] [--verify-images] \
      [--headed] [--llm-backend claude-code]

The extractor auto-detects the camera variant and records it in the run's
manifest; the deck spec (decks/<variant>.yaml) is chosen from that. If the
extractor fails, the pipeline stops and reports the partial run directory
instead of building a deck from incomplete assets.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from core import paths

ROOT = Path(__file__).resolve().parent


def _manifest(run_dir: Path) -> dict:
    for rel in ("data/manifest.json", "manifest.json"):
        p = run_dir / rel
        if p.exists():
            return json.loads(p.read_text())
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="Camera URL (any path; origin is used)")
    ap.add_argument("--recipe", required=True, help="Approximate recipe name")
    ap.add_argument("--headed", action="store_true", help="Run the browser headed")
    ap.add_argument("--context", help="Engineer's site-visit notes (text/markdown file)")
    ap.add_argument("--images", help="Directory of engineer photos to add to the pool")
    ap.add_argument(
        "--verify-images",
        action="store_true",
        help="Vision-verify every matched slot image in the deck",
    )
    ap.add_argument(
        "--publish",
        action="store_true",
        help="Upload the assets and deck to your Google Drive (deck as Google "
        "Slides) and print the link; needs publish_cli.py login once",
    )
    ap.add_argument(
        "--adaptive-structure",
        action="store_true",
        help="Let the engineer's notes adjust the deck's slide structure "
        "(see deck_cli.py); default is the fixed variant spec",
    )
    ap.add_argument(
        "--llm-backend",
        choices=["api", "claude-code", "agent-sdk"],
        default=os.environ.get("SG_LLM_BACKEND", "agent-sdk"),
        help="LLM backend for both phases (see cli.py / deck_cli.py). Default "
        "'agent-sdk' runs everything on your Claude Code login, no API key",
    )
    ap.add_argument(
        "--out",
        help="Bundle everything into this directory: <out>/assets (the full "
        "extractor run) + <out>/report (deck.pptx, plan.json, deck.pdf when "
        "LibreOffice is available)",
    )
    args = ap.parse_args()

    import cli
    import deck_cli

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = paths.output_base() / "runs" / ts

    print(f"=== Phase 1: asset extraction -> {run_dir} ===")
    extractor_argv = [
        "--url", args.url,
        "--recipe", args.recipe,
        "--run-dir", str(run_dir),
        "--llm-backend", args.llm_backend,
    ]
    if args.headed:
        extractor_argv.append("--headed")
    # The engineer's notes also ground the extractor's part description
    # (what the capture picks anchor to), not just the deck's copy.
    if args.context:
        extractor_argv += ["--context", args.context]
    code = cli.main(extractor_argv)
    if code != 0:
        print(
            f"\nextractor failed (exit {code}); not building a deck from a "
            f"partial run. Inspect {run_dir} — a deck can still be built "
            f"manually with: deck_cli.py --run {run_dir} --variant <variant>",
            file=sys.stderr,
        )
        return code

    variant = _manifest(run_dir).get("variant", "")
    if not variant:
        print(f"no variant recorded in {run_dir}'s manifest; cannot pick a deck spec",
              file=sys.stderr)
        return 1
    if not (ROOT / "decks" / f"{variant}.yaml").exists():
        print(f"no deck spec for variant {variant} (decks/{variant}.yaml)", file=sys.stderr)
        return 1

    print(f"\n=== Phase 2: deck generation (variant {variant}) ===")
    out = Path(args.out).resolve() if args.out else None
    deck_argv = [
        "--run", str(run_dir),
        "--variant", variant,
        "--llm-backend", args.llm_backend,
    ]
    if out:
        deck_argv += ["--out-dir", str(out / "report")]
    if args.context:
        deck_argv += ["--context", args.context]
    if args.images:
        deck_argv += ["--images", args.images]
    if args.verify_images:
        deck_argv.append("--verify-images")
    if args.adaptive_structure:
        deck_argv.append("--adaptive-structure")
    if args.publish:
        deck_argv.append("--publish")
    code = deck_cli.main(deck_argv)
    if code != 0 or not out:
        return code
    return _bundle(out, run_dir)


def _bundle(out: Path, run_dir: Path) -> int:
    """Assemble the single deliverable folder: assets/ + report/."""
    import shutil
    import subprocess

    print(f"\n=== Phase 3: bundling -> {out} ===")
    shutil.copytree(run_dir, out / "assets", dirs_exist_ok=True)
    deck = out / "report" / "deck.pptx"
    if deck.exists() and shutil.which("soffice"):
        try:
            subprocess.run(
                ["soffice", "--headless", "--convert-to", "pdf", str(deck),
                 "--outdir", str(out / "report")],
                capture_output=True, timeout=300, check=True,
            )
            print("  deck.pdf rendered")
        except Exception as e:
            print(f"  pdf render skipped: {e}")
    print(f"  assets: {out / 'assets'}")
    print(f"  report: {deck}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
