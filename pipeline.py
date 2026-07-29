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
        "--llm-backend",
        choices=["api", "claude-code"],
        default=os.environ.get("SG_LLM_BACKEND", "api"),
        help="LLM backend for both phases (see cli.py / deck_cli.py)",
    )
    args = ap.parse_args()

    import cli
    import deck_cli

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = ROOT / "runs" / ts

    print(f"=== Phase 1: asset extraction -> {run_dir} ===")
    extractor_argv = [
        "--url", args.url,
        "--recipe", args.recipe,
        "--run-dir", str(run_dir),
        "--llm-backend", args.llm_backend,
    ]
    if args.headed:
        extractor_argv.append("--headed")
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
    deck_argv = [
        "--run", str(run_dir),
        "--variant", variant,
        "--llm-backend", args.llm_backend,
    ]
    if args.context:
        deck_argv += ["--context", args.context]
    if args.images:
        deck_argv += ["--images", args.images]
    if args.verify_images:
        deck_argv.append("--verify-images")
    return deck_cli.main(deck_argv)


if __name__ == "__main__":
    sys.exit(main())
