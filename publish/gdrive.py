"""Publish a deck (and optionally a run's assets) to Google Drive.

The deck is converted to native Google Slides on upload, because editing in
Slides is what engineers actually do next.

Two destinations, by design:
- The finished DECK goes to the team-wide shared drive (TEAM_DRIVE_ID), flat,
  so the whole team can find every report in one place.
- Raw ASSETS are working material and go to the engineer's OWN Drive library,
  inside a dated per-run folder. A shared space the team reads should not
  accumulate 28-file asset dumps.

`--personal` sends the deck to the engineer's library too.

Auth: per-user OAuth2 (installed-app loopback flow), consented once and
cached as a refresh token in the writable data dir. The scope is
`drive.file` — access ONLY to files this tool creates, never the rest of
their Drive. Register the OAuth client as an INTERNAL app in your Google
Cloud project: internal apps skip verification, and (importantly) an
External/Testing app expires refresh tokens after 7 days.

Reliability rules baked in here:
- Every publish creates a NEW timestamped folder. We never update or
  overwrite a previous upload — the engineer may have edited it, and
  silently clobbering their work is the worst thing this could do.
- The deck uploads FIRST: the Slides link is the valuable artifact, and one
  failed screenshot must never cost it.
- Uploads are resumable with retry/backoff; a per-file failure is recorded
  and reported, never fatal.
- Local artifacts stay canonical. Publishing is purely additive.
- Sharing permissions are never touched. In the shared drive the team's own
  access applies; in a personal Drive it stays the engineer's file.
"""

from __future__ import annotations

import datetime
import json
import mimetypes
import os
from pathlib import Path

from core.paths import PACKAGE_ROOT, data_dir

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_MIME = "application/vnd.google-apps.folder"
SLIDES_MIME = "application/vnd.google-apps.presentation"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
# Subtrees of a run worth publishing by default: what a human would want.
DEFAULT_INCLUDE = ("deliverables", "data")
ALL_INCLUDE = ("deliverables", "data", "archive", "debug")
RETRIES = 5
# Google rejects pptx imports above ~100 MB. Warn with headroom so the
# engineer hears it from us before they hear it from Google.
SIZE_WARN_BYTES = 90 * 1024 * 1024
# How long to wait for a browser consent callback before giving up.
AUTH_TIMEOUT_S = 180
# One stable library folder collects every report, so an engineer's Drive
# root doesn't accumulate loose folders. Each run still gets its own dated
# subfolder inside it — the library is a container, never a shared target.
# Used only when publishing to a personal Drive (see TEAM_DRIVE_ID).
DEFAULT_LIBRARY = "OV Test Reports"

# The team-wide shared drive every report lands in by default. Finished decks
# belong somewhere the whole team can find them, not scattered across personal
# Drives. Override with SG_TEAM_DRIVE_ID; set it to "" to fall back to the
# per-user library above.
#
# IMPORTANT — what our `drive.file` scope permits here, verified against this
# drive rather than assumed:
#
#   drives.get(driveId)              -> 403
#   files.get(fileId=driveId)        -> 404
#   files.create(parents=[driveId])  -> OK
#   files.delete(<file we created>)  -> OK
#
# So we can WRITE into the shared drive but cannot READ it: we cannot verify
# the destination exists, list what is in it, or find a folder by name inside
# it. Every code path here must therefore write blind and let the create call
# be the check — never gate an upload on folder_exists()/find_folder(), which
# will always fail for this target. The upside is that no broader scope is
# needed, so the tool still only ever touches files it created itself.
TEAM_DRIVE_ID = os.environ.get("SG_TEAM_DRIVE_ID", "0AEQ6bdfOEbU_Uk9PVA").strip()


class AuthError(RuntimeError):
    """Raised with an actionable message when credentials are unusable."""


def token_path() -> Path:
    return data_dir() / "google_token.json"


def library_state_path() -> Path:
    return data_dir() / "google_library.json"


def client_config_path() -> Path | None:
    """OAuth client JSON (Desktop app). Looked up in order: env override,
    the user's data dir, then a copy bundled with the plugin."""
    env = os.environ.get("SG_GOOGLE_CLIENT_JSON")
    candidates = [Path(env).expanduser()] if env else []
    candidates += [
        data_dir() / "google_client.json",
        PACKAGE_ROOT / "publish" / "google_client.json",
    ]
    return next((p for p in candidates if p.exists()), None)


def _client_config_hint() -> str:
    return (
        "no Google OAuth client configuration found. Ask your team lead for the "
        f"client JSON (Desktop app type) and save it as {data_dir() / 'google_client.json'}, "
        "or set SG_GOOGLE_CLIENT_JSON to its path."
    )


def _token_scopes_ok(path: Path) -> bool:
    """True when the cached token carries every scope we now require.

    A token granted under a narrower scope list refreshes perfectly well
    and then fails with 403/404 at request time — far from the cause, and
    baffling to debug. Rejecting it here converts that into one clean
    re-consent. This matters the day SCOPES changes, not today.
    """
    try:
        granted = set(json.loads(path.read_text()).get("scopes") or [])
    except Exception:
        return False
    return set(SCOPES).issubset(granted)


def _save_token(creds) -> None:
    path = token_path()
    path.write_text(creds.to_json())
    os.chmod(path, 0o600)


def credentials(interactive: bool = True, log=print):
    """Cached credentials, refreshed or freshly consented as needed."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token_path().exists():
        if not _token_scopes_ok(token_path()):
            log("  cached Google token predates a scope change; signing in again")
        else:
            try:
                creds = Credentials.from_authorized_user_file(str(token_path()), SCOPES)
            except Exception:
                creds = None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except RefreshError:
            log("  stored Google credentials are no longer valid; signing in again")
            creds = None
    # SG_NO_BROWSER_AUTH is for contexts that can never complete a browser
    # flow (CI, cron): fail fast with instructions instead of waiting.
    if not interactive or os.environ.get("SG_NO_BROWSER_AUTH") == "1":
        raise AuthError(
            "not signed in to Google. Run: uv run python publish_cli.py login"
        )
    config = client_config_path()
    if config is None:
        raise AuthError(_client_config_hint())
    flow = InstalledAppFlow.from_client_secrets_file(str(config), SCOPES)
    log("  opening a browser to sign in to Google (one time only)...")
    try:
        # Bounded: an unattended run must fail with a usable message rather
        # than block forever on a callback that will never arrive.
        creds = flow.run_local_server(
            port=0, prompt="consent", timeout_seconds=AUTH_TIMEOUT_S
        )
    except Exception as e:
        raise AuthError(
            f"Google sign-in did not complete within {AUTH_TIMEOUT_S}s ({type(e).__name__}). "
            f"Run `uv run python publish_cli.py login` from a machine with a browser, "
            f"or set SG_NO_BROWSER_AUTH=1 to skip the prompt entirely."
        ) from None
    _save_token(creds)
    log(f"  signed in; credentials cached at {token_path()}")
    return creds


def logout() -> bool:
    if token_path().exists():
        token_path().unlink()
        return True
    return False


def auth_state() -> dict:
    """Non-interactive report for preflight/status."""
    if client_config_path() is None:
        return {"ready": False, "reason": _client_config_hint()}
    if not token_path().exists():
        return {
            "ready": False,
            "reason": "not signed in — run: uv run python publish_cli.py login",
        }
    if not _token_scopes_ok(token_path()):
        return {
            "ready": False,
            "reason": "cached token predates a scope change — run: "
            "uv run python publish_cli.py login",
        }
    try:
        creds = credentials(interactive=False, log=lambda *a: None)
    except Exception as e:
        return {"ready": False, "reason": str(e)}
    return {"ready": bool(creds and creds.valid), "reason": "signed in"}


class DriveClient:
    """Thin wrapper over the Drive API — the only place Google is called.
    Keeping it small makes the publish logic testable with a fake."""

    def __init__(self, service=None, log=print):
        self.log = log
        if service is None:
            from googleapiclient.discovery import build

            service = build(
                "drive", "v3", credentials=credentials(log=log), cache_discovery=False
            )
        self.service = service

    def folder_exists(self, folder_id: str) -> bool:
        """True when the id still resolves to a live (untrashed) folder."""
        from googleapiclient.errors import HttpError

        try:
            meta = (
                self.service.files()
                .get(fileId=folder_id, fields="id,trashed,mimeType", supportsAllDrives=True)
                .execute(num_retries=RETRIES)
            )
        except HttpError:
            return False
        except Exception:
            return False
        return not meta.get("trashed") and meta.get("mimeType") == FOLDER_MIME

    def find_folder(self, name: str) -> str | None:
        """Look for an app-created folder by name. With the drive.file scope
        this can only ever see folders this tool made, which is exactly the
        set we want."""
        try:
            escaped = name.replace("\\", "\\\\").replace("'", "\\'")
            res = (
                self.service.files()
                .list(
                    q=(
                        f"name = '{escaped}' and mimeType = '{FOLDER_MIME}' "
                        f"and trashed = false"
                    ),
                    fields="files(id,name)",
                    pageSize=10,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute(num_retries=RETRIES)
            )
        except Exception:
            return None
        files = res.get("files") or []
        return files[0]["id"] if files else None

    def create_folder(self, name: str, parent: str | None = None) -> str:
        body = {"name": name, "mimeType": FOLDER_MIME}
        if parent:
            body["parents"] = [parent]
        created = (
            self.service.files()
            .create(body=body, fields="id", supportsAllDrives=True)
            .execute(num_retries=RETRIES)
        )
        return created["id"]

    def upload(
        self, path: Path, parent: str, convert_to: str | None = None, name: str | None = None
    ) -> dict:
        """Resumable upload; `convert_to` asks Drive to convert (e.g. pptx ->
        Google Slides). Returns {id, name, link}."""
        from googleapiclient.http import MediaFileUpload

        source_mime = (
            PPTX_MIME
            if path.suffix.lower() == ".pptx"
            else (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        )
        body = {"name": name or path.name, "parents": [parent]}
        if convert_to:
            body["mimeType"] = convert_to
        media = MediaFileUpload(str(path), mimetype=source_mime, resumable=True)
        request = self.service.files().create(
            body=body, media_body=media, fields="id,name,webViewLink",
            supportsAllDrives=True,
        )
        response = None
        while response is None:
            _status, response = request.next_chunk(num_retries=RETRIES)
        return {
            "id": response["id"],
            "name": response.get("name", body["name"]),
            "link": response.get("webViewLink", ""),
        }


def library_folder(client: "DriveClient", name: str = DEFAULT_LIBRARY, log=print) -> str:
    """The stable folder that collects every report, resolved in this order:

    1. the id remembered locally — still valid even if the engineer renames
       or moves the folder in Drive, which is why an id beats a name;
    2. an app-created folder with this name (covers a new machine, or a
       deleted local cache);
    3. a freshly created one.
    """
    remembered = None
    if library_state_path().exists():
        try:
            remembered = json.loads(library_state_path().read_text()).get(name)
        except Exception:
            remembered = None
    if remembered and client.folder_exists(remembered):
        return remembered
    found = client.find_folder(name)
    if found is None:
        found = client.create_folder(name)
        log(f"  created your Drive library folder: {name}")
    _remember_library(name, found, log)
    return found


def _remember_library(name: str, folder_id: str, log=print) -> None:
    try:
        state = {}
        if library_state_path().exists():
            state = json.loads(library_state_path().read_text())
        state[name] = folder_id
        library_state_path().write_text(json.dumps(state, indent=2))
    except Exception as e:
        # Losing the cache only costs a name lookup next time.
        log(f"  note: could not cache the library folder id: {e}")


def _folder_name(run_dir: Path, recipe: str | None) -> str:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    label = (recipe or run_dir.name).strip() or run_dir.name
    return f"OV Test Report — {label} — {stamp}"


def _manifest(run_dir: Path) -> tuple[Path | None, dict]:
    for rel in ("data/manifest.json", "manifest.json"):
        p = run_dir / rel
        if p.exists():
            try:
                return p, json.loads(p.read_text())
            except Exception:
                return p, {}
    return None, {}


def _recipe_of(manifest: dict) -> str | None:
    return next(
        (s.get("matched_recipe") for s in manifest.get("steps", []) if s.get("matched_recipe")),
        None,
    ) or manifest.get("recipe_input")


def _asset_files(run_dir: Path, include: tuple[str, ...]) -> list[Path]:
    return [
        f
        for sub in include
        for f in sorted((run_dir / sub).rglob("*"))
        if f.is_file() and not f.name.startswith(".")
    ]


def plan_publish(
    run_dir: Path | None,
    deck_path: Path | None = None,
    include: tuple[str, ...] = (),
    folder_name: str | None = None,
    library: str | None = DEFAULT_LIBRARY,
    team_drive: str | None = None,
) -> dict:
    """What a publish WOULD do — the destination, files and total size — with
    no credentials and no network. Lets an engineer sanity-check the payload
    before uploading. Defaults mirror publish() exactly, or the dry run would
    describe an upload that will not happen."""
    run_dir = Path(run_dir) if run_dir else None
    _, manifest = _manifest(run_dir) if run_dir else (None, {})
    name = folder_name or _folder_name(run_dir or Path("deck"), _recipe_of(manifest))
    team = TEAM_DRIVE_ID if team_drive is None else team_drive.strip()
    if include:
        team = ""  # assets go to the personal library, mirroring publish()
    flat = bool(team) and not include
    tree: list[str] = []
    total = 0
    count = 0
    if flat:
        tree.append(f"team shared drive ({team})/")
        indent = "  "
    else:
        if team:
            tree.append(f"team shared drive ({team})/")
        elif library:
            tree.append(f"{library}/")
        indent = "  " if (team or library) else ""
        tree.append(f"{indent}{name}/")
        indent += "  "
    if deck_path and Path(deck_path).exists():
        deck_path = Path(deck_path)
        size = deck_path.stat().st_size
        title = _recipe_of(manifest) or deck_path.stem
        label = name if flat else f"{title} — Test Report"
        tree.append(f"{indent}{label}   (Google Slides, {size / 1e6:.1f} MB)")
        total += size
        count += 1
    if run_dir and include:
        files = _asset_files(run_dir, include)
        tree.append(f"{indent}assets/   ({len(files)} files)")
        for f in files:
            size = f.stat().st_size
            total += size
            count += 1
            tree.append(f"{indent}  {f.relative_to(run_dir)}   ({size / 1e6:.2f} MB)")
    return {
        "folder_name": name,
        "tree": tree,
        "file_count": count,
        "total_bytes": total,
        "target": "team-drive" if team else "personal-drive",
        "flat": flat,
    }


def publish(
    run_dir: Path | None,
    deck_path: Path | None = None,
    client: DriveClient | None = None,
    include: tuple[str, ...] = (),
    folder_name: str | None = None,
    library: str | None = DEFAULT_LIBRARY,
    team_drive: str | None = None,
    log=print,
) -> dict:
    """Publish the deck as Google Slides, and optionally a run's assets.

    By default the deck lands FLAT in the team shared drive (TEAM_DRIVE_ID)
    with no surrounding folder and no assets — a finished report belongs
    somewhere the whole team can find it, and that is the only artifact worth
    sharing. Pass `include` to also upload assets, which reinstates the dated
    per-run folder; pass `team_drive=""` to publish to the engineer's own
    Drive library instead.

    Returns a report; never raises for a single failed file.
    """
    if run_dir is None and deck_path is None:
        raise ValueError("nothing to publish: pass a run directory and/or a deck")
    run_dir = Path(run_dir) if run_dir else None
    client = client or DriveClient(log=log)
    _, manifest = _manifest(run_dir) if run_dir else (None, {})
    name = folder_name or _folder_name(run_dir or Path("deck"), _recipe_of(manifest))
    team = TEAM_DRIVE_ID if team_drive is None else team_drive.strip()

    report: dict = {
        "folder_name": name,
        "uploaded": [],
        "failed": [],
        "slides_link": None,
        "folder_link": None,
        "warnings": [],
    }

    # The shared drive holds finished decks and nothing else. Raw assets are
    # working material, so a publish that includes them goes to the engineer's
    # own library instead of cluttering a space the whole team reads.
    if include:
        team = ""
        report["assets_note"] = "assets publish to your personal Drive library"

    # Deck-only into the shared drive: no folder to create, and — critically —
    # nothing to look up first. We cannot read this target at all (see
    # TEAM_DRIVE_ID), so the upload itself is the only check available.
    flat = bool(team) and not include
    if flat:
        root = team
        report["target"] = "team-drive"
        # There is no per-run folder here, but the report's shape must not
        # change with the destination — callers read folder_id/folder_link
        # unconditionally. The containing "folder" is the shared drive itself.
        report["folder_id"] = team
        report["folder_link"] = f"https://drive.google.com/drive/folders/{team}"
        report["flat"] = True
        log(f"  team shared drive: {report['folder_link']}")
    else:
        if team:
            parent = team  # dated folder created inside the shared drive
            report["target"] = "team-drive"
            report["library_id"] = team
            report["library_link"] = f"https://drive.google.com/drive/folders/{team}"
        else:
            parent = library_folder(client, library, log) if library else None
            report["target"] = "personal-drive"
            if parent:
                report["library_id"] = parent
                report["library_link"] = (
                    f"https://drive.google.com/drive/folders/{parent}"
                )
        root = client.create_folder(name, parent)
        report["folder_id"] = root
        report["folder_link"] = f"https://drive.google.com/drive/folders/{root}"
        log(f"  Drive folder: {(library + '/') if (library and not team) else ''}{name}")

    # The deck first — its link is the point of the exercise.
    if deck_path and Path(deck_path).exists():
        deck_path = Path(deck_path)
        title = _recipe_of(manifest) or deck_path.stem
        deck_bytes = deck_path.stat().st_size
        if deck_bytes > SIZE_WARN_BYTES:
            warn = (
                f"deck is {deck_bytes / 1e6:.0f} MB — Google rejects pptx "
                f"imports above ~100 MB; the Slides conversion may fail"
            )
            report.setdefault("warnings", []).append(warn)
            log(f"  warning: {warn}")
        # Flat in a shared space, the file name is the only context there is,
        # so it carries the full report name rather than just the recipe.
        slides_name = name if flat else f"{title} — Test Report"
        try:
            slides = client.upload(
                deck_path, root, convert_to=SLIDES_MIME, name=slides_name
            )
            report["slides_id"] = slides["id"]
            report["slides_link"] = (
                slides["link"]
                or f"https://docs.google.com/presentation/d/{slides['id']}/edit"
            )
            report["uploaded"].append(slides["name"])
            log(f"  Google Slides: {report['slides_link']}")
        except Exception as e:
            report["failed"].append({"file": deck_path.name, "error": str(e)[:200]})
            log(f"  FAILED to upload the deck: {e}")

    if run_dir:
        assets_root = client.create_folder("assets", root)
        folders: dict[str, str] = {".": assets_root}

        def folder_for(rel: Path) -> str:
            key = str(rel)
            if key in folders:
                return folders[key]
            parent = folder_for(rel.parent) if str(rel.parent) != "." else assets_root
            folders[key] = client.create_folder(rel.name, parent)
            return folders[key]

        files = _asset_files(run_dir, include)
        log(f"  uploading {len(files)} asset file(s)...")
        for f in files:
            rel = f.relative_to(run_dir).parent
            try:
                client.upload(f, folder_for(rel))
                report["uploaded"].append(str(f.relative_to(run_dir)))
            except Exception as e:
                report["failed"].append(
                    {"file": str(f.relative_to(run_dir)), "error": str(e)[:200]}
                )
                log(f"    failed: {f.name}: {str(e)[:100]}")

    if run_dir:
        _record(run_dir, report, log)
    log(
        f"  published {len(report['uploaded'])} file(s)"
        + (f", {len(report['failed'])} failed" if report["failed"] else "")
    )
    return report


def _record(run_dir: Path, report: dict, log=print) -> None:
    """Append this publish to the run manifest so the Drive copy is
    traceable later. Never let bookkeeping break a successful upload."""
    path, manifest = _manifest(run_dir)
    if path is None:
        return
    try:
        manifest.setdefault("google_drive_publishes", []).append(
            {
                "at": datetime.datetime.now().isoformat(timespec="seconds"),
                "folder_name": report["folder_name"],
                "folder_link": report["folder_link"],
                "slides_link": report.get("slides_link"),
                "files": len(report["uploaded"]),
                "failed": len(report["failed"]),
            }
        )
        path.write_text(json.dumps(manifest, indent=2))
    except Exception as e:
        log(f"  note: could not record the publish in the manifest: {e}")
