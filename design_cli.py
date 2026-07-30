"""Derive the deck's design guide from the template corpus (maintainer tool).

An agent session reads every slide template, its render, its sidecar
purpose, and the deck spec's ordering, then writes deck/brand/design_guide.md
— the prose conventions every generated slide is held to. Run it when the
templates or the slide sequence change; the result is cached and versioned,
and can be edited by hand afterwards.

Usage:
  uv run python design_cli.py [--variant ov80i] [--llm-backend agent-sdk] [--show]
"""

from __future__ import annotations

import argparse
import os
import sys

from core import llm
from deck.design import GUIDE_PATH, derive_design_guide, load_design_guide


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="ov80i", help="Variant whose corpus to analyse")
    ap.add_argument(
        "--llm-backend",
        choices=["api", "claude-code", "agent-sdk"],
        default=os.environ.get("SG_LLM_BACKEND", "agent-sdk"),
        help="Backend for the agent session (default: agent-sdk, no API key)",
    )
    ap.add_argument("--show", action="store_true", help="Print the current guide and exit")
    args = ap.parse_args()

    if args.show:
        guide = load_design_guide()
        print(guide or f"no design guide yet ({GUIDE_PATH})")
        return 0 if guide else 1

    llm.select_backend(args.llm_backend)
    try:
        path = derive_design_guide(args.variant)
    except Exception as e:
        print(f"failed to derive design guide: {e}", file=sys.stderr)
        return 1
    print(f"\nreview and edit by hand as needed: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
