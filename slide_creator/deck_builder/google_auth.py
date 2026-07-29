"""Shared Google API authentication for the ``deck_builder`` package.

Both the Google Slides preview rasterizer (``preview/google_slides.py``)
and the Drive deck export (``drive_export.py``) talk to Google APIs with
the same OAuth identity. This module is the single owner of that
identity: scope list, credential discovery, token cache, and client
construction all live here so the two features can never drift apart.

Credential discovery order (same locations as ``slide_agent``):

1. Env var ``GOOGLE_CREDENTIALS`` (or ``SLIDE_AGENT_GOOGLE_CREDENTIALS``)
   → path to a ``credentials.json`` (OAuth client-secret, type
   "Desktop app").
2. ``~/.config/slide-agent/credentials.json`` (XDG default).
3. Token cached at ``~/.config/slide-agent/token.json`` — auto-refreshed.

Scopes
------

``drive`` (full) rather than ``drive.file``: the deck export uploads
into a shared-drive folder that this app did not create, and under
``drive.file`` the Drive API cannot even resolve that folder id (it
404s on any file the app didn't create or open). The preview
rasterizer only needs ``drive.file``-level access, but a single token
must cover both features, so the wider scope wins.

A cached token granted under the old, narrower scope list is detected
by :func:`_token_scopes_ok` and discarded, which forces one
re-consent in the browser. First run (or first run after a scope
change) therefore needs a browser; every run after that is silent.

Run ``python -m deck_builder.google_auth`` to perform that consent step
ahead of time (e.g. before deploying somewhere headless) and verify
connectivity.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deck_builder.errors import DriveAuthError

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/presentations",
]

_DEFAULT_CONFIG_DIR = Path(
    os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
) / "slide-agent"

DEFAULT_CREDENTIALS_PATH = _DEFAULT_CONFIG_DIR / "credentials.json"
DEFAULT_TOKEN_PATH = _DEFAULT_CONFIG_DIR / "token.json"


@dataclass
class GoogleClients:
    """Authorized Google API service clients."""

    drive: Any
    slides: Any


def resolve_credentials_path() -> Path:
    """Find a ``credentials.json`` file or raise with setup instructions."""
    for env_var in ("GOOGLE_CREDENTIALS", "SLIDE_AGENT_GOOGLE_CREDENTIALS"):
        val = os.environ.get(env_var)
        if val:
            p = Path(val).expanduser()
            if p.exists():
                return p

    if DEFAULT_CREDENTIALS_PATH.exists():
        return DEFAULT_CREDENTIALS_PATH

    raise DriveAuthError(
        "No Google OAuth credentials found. Google Drive/Slides features "
        "need a 'credentials.json' (OAuth 2.0 Client ID, type 'Desktop app').\n\n"
        "Setup:\n"
        "  1. Enable Drive API and Slides API in Google Cloud Console.\n"
        "  2. Create an OAuth 2.0 Client ID (type 'Desktop app').\n"
        f"  3. Save it as {DEFAULT_CREDENTIALS_PATH}\n"
        "     OR set GOOGLE_CREDENTIALS=/path/to/credentials.json"
    )


def build_google_clients(
    credentials_path: str | None = None,
    token_path: str | None = None,
) -> GoogleClients:
    """Build authorized Drive + Slides API clients.

    Loads the cached token (refreshing if expired), or runs the
    interactive browser consent flow when there is no usable token.

    Raises:
        DriveAuthError: when credentials are missing or the consent /
            refresh flow fails.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise DriveAuthError(
            "Google API client libraries are not installed. Install with:\n"
            "  pip install google-api-python-client google-auth google-auth-oauthlib"
        ) from exc

    creds_path = Path(credentials_path) if credentials_path else resolve_credentials_path()
    tok_path = Path(token_path) if token_path else DEFAULT_TOKEN_PATH

    try:
        creds = _load_or_obtain(creds_path, tok_path, Request, Credentials, InstalledAppFlow)
    except DriveAuthError:
        raise
    except Exception as exc:
        raise DriveAuthError(f"Google OAuth flow failed: {exc}") from exc

    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    slides = build("slides", "v1", credentials=creds, cache_discovery=False)
    return GoogleClients(drive=drive, slides=slides)


def cached_token_ok(token_path: Path | None = None) -> bool:
    """True when a cached token exists and carries every required scope.

    A cheap, network-free readiness check (used by the webapp's
    ``/api/health``). A ``True`` here doesn't guarantee the token is
    unrevoked — that's only known at request time — but a ``False``
    reliably predicts the next Google call will need re-consent.
    """
    p = Path(token_path) if token_path else DEFAULT_TOKEN_PATH
    return p.exists() and _token_scopes_ok(p)


def _token_scopes_ok(tok_path: Path) -> bool:
    """True if the cached token was granted every scope in ``SCOPES``.

    A token cached under an older, narrower scope list (e.g. the
    original ``drive.file``) refreshes fine but then 403s/404s at
    request time — far away from the actual cause. Rejecting it here
    forces a clean re-consent instead.
    """
    try:
        granted = set(json.loads(tok_path.read_text()).get("scopes") or [])
    except Exception:
        return False
    return set(SCOPES).issubset(granted)


def _load_or_obtain(creds_path, tok_path, Request, Credentials, InstalledAppFlow):
    """Load cached token, refresh if expired, or run the browser flow."""
    creds = None
    if tok_path.exists() and _token_scopes_ok(tok_path):
        try:
            creds = Credentials.from_authorized_user_file(str(tok_path), SCOPES)
        except Exception:
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            tok_path.parent.mkdir(parents=True, exist_ok=True)
            tok_path.write_text(creds.to_json())
            return creds
        except Exception:
            creds = None

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=0)
    tok_path.parent.mkdir(parents=True, exist_ok=True)
    tok_path.write_text(creds.to_json())
    return creds


def main() -> int:
    """Prime and verify Google auth from the command line.

    Runs the consent flow if needed (opens a browser), then makes one
    read-only API call to prove the token works. Intended for first-time
    setup and for pre-authorizing a machine before headless use.
    """
    try:
        clients = build_google_clients()
    except DriveAuthError as exc:
        print(f"Auth failed:\n{exc}")
        return 1

    try:
        about = clients.drive.about().get(fields="user(emailAddress,displayName)").execute()
        user = about.get("user", {})
        print(
            f"Google auth OK — authorized as "
            f"{user.get('displayName', '?')} <{user.get('emailAddress', '?')}>"
        )
    except Exception as exc:
        print(f"Token obtained, but a test API call failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
