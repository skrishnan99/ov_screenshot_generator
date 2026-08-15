"""Environment preflight: verify everything a run needs, in seconds, with
actionable fix instructions — so failures happen here, not 6 minutes into a
pipeline run on a teammate's machine.

Usage:
  uv run python preflight.py [--url http://<camera>] [--variant ov80i] [--fix]

--fix auto-installs the Playwright Chromium browser when missing.
Exit code 0 = ready to run; 1 = at least one check failed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
from urllib.parse import urlparse

from core import llm, paths  # noqa: F401  (importing llm loads .env files)

OK, BAD = "  ok   ", "  FAIL "


def _check(label: str, passed: bool, fix: str = "") -> bool:
    print(f"{OK if passed else BAD}{label}")
    if not passed and fix:
        print(f"         fix: {fix}")
    return passed


def check_claude_cli() -> bool:
    exe = shutil.which("claude")
    if not exe:
        return _check(
            "claude CLI on PATH",
            False,
            "install Claude Code (https://claude.com/claude-code) and sign in",
        )
    try:
        proc = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=30
        )
        version = proc.stdout.strip() or "unknown version"
        return _check(f"claude CLI ({version})", proc.returncode == 0)
    except Exception as e:
        return _check("claude CLI runs", False, str(e))


def check_api_key(backend: str) -> bool:
    if backend == "agent-sdk":
        return _check("API key not needed (agent-sdk backend uses Claude Code login)", True)
    return _check(
        f"ANTHROPIC_API_KEY available (agentic navigation on '{backend}' backend)",
        bool(os.environ.get("ANTHROPIC_API_KEY")),
        f"put ANTHROPIC_API_KEY=<team key> in {paths.data_dir() / '.env'}, "
        f"or use --llm-backend agent-sdk (no key needed)",
    )


def check_browser(fix: bool) -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            path = p.chromium.executable_path
            if path and os.path.exists(path):
                return _check("Playwright Chromium installed", True)
    except Exception as e:
        return _check("Playwright importable", False, str(e))
    if fix:
        print("         installing Chromium (one-time, ~2 min)...")
        proc = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
        )
        return _check(
            "Playwright Chromium installed (auto)",
            proc.returncode == 0,
            (proc.stderr or proc.stdout)[-300:],
        )
    return _check(
        "Playwright Chromium installed",
        False,
        "run this preflight with --fix, or: uv run playwright install chromium",
    )


def check_camera(url: str) -> bool:
    origin = "{0.scheme}://{0.netloc}".format(urlparse(url))
    try:
        with urllib.request.urlopen(origin, timeout=10) as resp:
            return _check(f"camera reachable ({origin}, HTTP {resp.status})", True)
    except Exception as e:
        return _check(
            f"camera reachable ({origin})",
            False,
            f"{e} — is the camera on and your Tailscale/VPN connected?",
        )


def check_variant(variant: str) -> bool:
    ok = True
    task_spec = paths.PACKAGE_ROOT / "tasks" / f"{variant}.yaml"
    ok &= _check(
        f"extraction spec for variant {variant}",
        task_spec.exists(),
        f"variant not supported yet (no {task_spec.name}); supported: "
        + ", ".join(sorted(p.stem for p in (paths.PACKAGE_ROOT / 'tasks').glob('*.yaml'))),
    )
    deck_spec = paths.PACKAGE_ROOT / "decks" / f"{variant}.yaml"
    ok &= _check(
        f"deck spec for variant {variant}",
        deck_spec.exists(),
        f"deck generation not supported yet for {variant}; supported: "
        + ", ".join(sorted(p.stem for p in (paths.PACKAGE_ROOT / 'decks').glob('*.yaml'))),
    )
    return bool(ok)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="Camera URL to check reachability")
    ap.add_argument("--variant", help="Expected camera variant (e.g. ov80i)")
    ap.add_argument("--fix", action="store_true", help="Auto-install missing Chromium")
    ap.add_argument(
        "--ensure-google-auth",
        action="store_true",
        help="If Google Drive sign-in is missing, run the one-time browser "
        "consent NOW. For runs that will publish: the consent then happens "
        "up front instead of interrupting an unattended run ~25-30 minutes in.",
    )
    ap.add_argument(
        "--ensure-engineer-profile",
        action="store_true",
        help="Fail unless the engineer contact profile (name, email, phone) "
        "is complete. The /ov-test-report flow uses this to force the "
        "explicit up-front ask for exactly the missing fields.",
    )
    ap.add_argument(
        "--llm-backend",
        choices=["api", "claude-code", "agent-sdk"],
        default=os.environ.get("SG_LLM_BACKEND", "agent-sdk"),
        help="Backend the run will use — determines whether an API key is required",
    )
    args = ap.parse_args()

    print("preflight checks:")
    ok = True
    ok &= check_claude_cli()
    ok &= check_api_key(args.llm_backend)
    ok &= check_browser(args.fix)
    # Google Drive: informational for asset-only runs, but a run that will
    # publish should pass --ensure-google-auth so the ONE interactive moment
    # (the browser consent) happens here at minute zero, not at the publish
    # step after ~25-30 unattended minutes.
    try:
        from publish.gdrive import AuthError, auth_state, credentials

        state = auth_state()
        if not state["ready"] and args.ensure_google_auth:
            print("         Google sign-in needed — opening a browser now so the")
            print("         rest of the run needs no interaction at all...")
            try:
                credentials()  # interactive; caches the refresh token
                state = auth_state()
            except AuthError as e:
                _check(f"Google sign-in failed ({str(e)[:100]})", False,
                       "run: uv run python publish_cli.py login")
                ok = False
        if args.ensure_google_auth:
            ok &= _check(
                "Google Drive publishing ready"
                if state["ready"]
                else f"Google Drive publishing not set up ({state['reason'][:110]})",
                state["ready"],
                "run: uv run python publish_cli.py login",
            )
        else:
            _check(
                "Google Drive publishing ready"
                if state["ready"]
                else f"Google Drive publishing not set up ({state['reason'][:110]})",
                True,
            )
    except Exception as e:
        _check(f"Google Drive publishing unavailable ({str(e)[:80]})", True)
    # Informational: agent-built slides iterate with visual feedback when
    # LibreOffice is present; without it they still work, gate-only.
    _check(
        "LibreOffice (recommended: visual feedback for agent-built slides)"
        if shutil.which("soffice")
        else "LibreOffice not found (agent-built slides will iterate without "
        "visual feedback; brew install --cask libreoffice)",
        True,
    )
    # The deck's contact slide and title byline sign with the SE profile.
    # Informational by default (headless deckgen and asset-only runs never
    # block on it); a HARD check under --ensure-engineer-profile, naming
    # exactly the missing fields so the /ov-test-report flow asks for each
    # one explicitly, by name, before the unattended stretch begins.
    try:
        from core.engineer import missing_fields, profile_path

        missing = missing_fields()
        if missing:
            _check(
                f"engineer contact profile incomplete — missing: "
                f"{', '.join(missing)} (ask for exactly these, or set "
                f"{profile_path()})",
                not args.ensure_engineer_profile,
            )
            ok &= not args.ensure_engineer_profile
        else:
            _check("engineer contact profile complete (signs the report's "
                   "contact slide)", True)
    except Exception as e:
        _check(f"engineer profile unavailable ({str(e)[:80]})",
               not args.ensure_engineer_profile)
        ok &= not args.ensure_engineer_profile
    if args.url:
        ok &= check_camera(args.url)
    if args.variant:
        ok &= check_variant(args.variant)
    print("\nready to run" if ok else "\nNOT ready — fix the failures above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
