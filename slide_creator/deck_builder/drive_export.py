"""Export a rendered deck to Google Drive as a native Google Slides file.

Adapted from ``recipe_decryption/case_study/drive_export.py``. Merges
every rendered slide's ``.pptx`` into one deck and uploads it to Drive
converted to a Google Slides file. Conversion fidelity is exact by
construction: the skeletons were authored in Google Slides.

Stable-link semantics: the first export creates a Drive file and
records its identity on ``manifest.drive_export``; later exports
update that file in place so a shared link keeps working. Pass
``update_existing=False`` for a frozen snapshot instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from deck_builder.errors import DriveExportError, NothingToExportError
from deck_builder.manifest import DeckManifest, DriveExportInfo, SlideSpec

_SLIDES_MIME = "application/vnd.google-apps.presentation"
_PPTX_MIME = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)

# Google rejects pptx imports above ~100 MB; warn with headroom so the
# user hears about it from us before they hear about it from Google.
_SIZE_WARN_BYTES = 90 * 1024 * 1024

_FALLBACK_DECK_NAME = "Inspection Case Study"


@dataclass(frozen=True)
class DriveExportResult:
    info: DriveExportInfo
    skipped_slide_ids: list[str]
    warnings: list[str] = field(default_factory=list)
    updated_existing: bool = False


# ---------------------------------------------------------------------------
# Pure helpers (no Google API access) — shared with the local pptx export
# ---------------------------------------------------------------------------

def collect_rendered_pptx(manifest: DeckManifest) -> tuple[list[Path], list[str]]:
    """Gather each slide's rendered ``.pptx`` in deck order.

    Returns ``(paths, skipped_slide_ids)`` — skipped lists slides with
    no render cache or whose cached file is missing on disk.
    """
    paths: list[Path] = []
    skipped: list[str] = []
    for slide in manifest.slides:
        cache = slide.render_cache
        if cache is None or not cache.pptx_path:
            skipped.append(slide.id)
            continue
        p = Path(cache.pptx_path)
        if p.exists():
            paths.append(p)
        else:
            skipped.append(slide.id)
    return paths, skipped


def deck_display_name(manifest: DeckManifest) -> str:
    """Drive file name: the recipe title, else a generic fallback."""
    name = (manifest.recipe_name or "").strip()
    return name or _FALLBACK_DECK_NAME


def deck_filename_stem(manifest: DeckManifest) -> str:
    """Filesystem-safe stem for the merged ``.pptx`` artifact."""
    stem = re.sub(r"[^A-Za-z0-9_\- ]+", "", deck_display_name(manifest)).strip()
    return stem.replace(" ", "_") or "case_study"


def merge_deck(manifest: DeckManifest, out_dir: Path) -> tuple[Path, list[str]]:
    """Merge all rendered slides into ``<out_dir>/export/<stem>.pptx``.

    The single merge implementation behind both the local pptx artifact
    and the Drive export — the two can never diverge.
    """
    paths, skipped = collect_rendered_pptx(manifest)
    if not paths:
        raise NothingToExportError(
            "No rendered slides to export — render the deck first."
        )

    export_dir = out_dir / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    output_path = export_dir / f"{deck_filename_stem(manifest)}.pptx"

    from deck_builder.merge import merge_pptx

    try:
        merge_pptx(paths, output_path)
    except Exception as exc:
        raise DriveExportError(f"Deck merge failed: {exc}") from exc
    return output_path, skipped


# ---------------------------------------------------------------------------
# Drive operations
# ---------------------------------------------------------------------------

def export_deck_to_drive(
    manifest: DeckManifest,
    out_dir: Path,
    *,
    folder_id: Optional[str] = None,
    update_existing: bool = True,
    credentials_path: Optional[str] = None,
    token_path: Optional[str] = None,
) -> DriveExportResult:
    """Merge the deck and upload it to Drive as a Google Slides file.

    On success, sets ``manifest.drive_export``; the caller persists the
    manifest.
    """
    from deck_builder.google_auth import build_google_clients

    # Auth first: the common failure (no/expired credentials) should
    # cost nothing and produce an actionable message.
    clients = build_google_clients(
        credentials_path=credentials_path, token_path=token_path
    )

    previous = manifest.drive_export
    creating_new = previous is None or not update_existing
    if creating_new and folder_id:
        _verify_folder_access(clients, folder_id)

    merged_path, skipped = merge_deck(manifest, out_dir)

    warnings: list[str] = []
    size = merged_path.stat().st_size
    if size > _SIZE_WARN_BYTES:
        warnings.append(
            f"Merged deck is {size / 1024 / 1024:.0f} MB — Google rejects "
            f"pptx imports above ~100 MB. Consider reducing screenshot "
            f"resolution if the export fails."
        )
    if skipped:
        warnings.append(
            f"{len(skipped)} slide(s) skipped (not rendered): {', '.join(skipped)}"
        )

    name = deck_display_name(manifest)
    uploaded: Optional[dict] = None
    updated = False

    if previous is not None and update_existing:
        uploaded = _try_update_file(clients, previous.file_id, name, merged_path)
        updated = uploaded is not None

    if uploaded is None:
        uploaded = _create_file(clients, name, merged_path, folder_id)

    info = DriveExportInfo(
        file_id=uploaded["id"],
        web_view_link=uploaded.get("webViewLink", ""),
        name=uploaded.get("name", name),
        folder_id=(previous.folder_id if updated and previous else folder_id),
        exported_at=datetime.now(timezone.utc).isoformat(),
    )
    manifest.drive_export = info
    return DriveExportResult(
        info=info,
        skipped_slide_ids=skipped,
        warnings=warnings,
        updated_existing=updated,
    )


def _media(merged_path: Path) -> Any:
    """Resumable upload body — a deck of screenshots is tens of MB."""
    from googleapiclient.http import MediaFileUpload

    return MediaFileUpload(str(merged_path), mimetype=_PPTX_MIME, resumable=True)


def _verify_folder_access(clients: Any, folder_id: str) -> None:
    """Fail fast, with a clear message, if the destination is unreachable."""
    from googleapiclient.errors import HttpError

    try:
        clients.drive.files().get(
            fileId=folder_id, fields="id", supportsAllDrives=True
        ).execute()
        return
    except HttpError as exc:
        if exc.resp.status not in (403, 404):
            raise DriveExportError(f"Drive folder check failed: {exc}") from exc

    try:
        clients.drive.drives().get(driveId=folder_id).execute()
        return
    except HttpError as exc:
        raise DriveExportError(
            f"Cannot access the Drive export folder {folder_id!r}. "
            f"Check that the id is correct and that the authorized Google "
            f"account is a member of that folder / shared drive with "
            f"contributor access. ({exc.resp.status})"
        ) from exc


def _try_update_file(
    clients: Any, file_id: str, name: str, merged_path: Path
) -> Optional[dict]:
    """Update the previously exported file in place.

    Returns None when the file no longer exists (deleted/trashed) so
    the caller falls back to creating a new one.
    """
    from googleapiclient.errors import HttpError

    try:
        meta = (
            clients.drive.files()
            .get(fileId=file_id, fields="id, trashed", supportsAllDrives=True)
            .execute()
        )
    except HttpError as exc:
        if exc.resp.status in (403, 404):
            return None
        raise DriveExportError(f"Drive file lookup failed: {exc}") from exc
    if meta.get("trashed"):
        return None

    try:
        return (
            clients.drive.files()
            .update(
                fileId=file_id,
                body={"name": name},
                media_body=_media(merged_path),
                supportsAllDrives=True,
                fields="id, webViewLink, name",
            )
            .execute()
        )
    except HttpError as exc:
        raise DriveExportError(
            f"Updating the existing Drive file failed: {exc}"
        ) from exc


def _create_file(
    clients: Any, name: str, merged_path: Path, folder_id: Optional[str]
) -> dict:
    """Create a new Google Slides file (converting the pptx on upload)."""
    from googleapiclient.errors import HttpError

    body: dict[str, Any] = {"name": name, "mimeType": _SLIDES_MIME}
    if folder_id:
        body["parents"] = [folder_id]

    try:
        result = (
            clients.drive.files()
            .create(
                body=body,
                media_body=_media(merged_path),
                supportsAllDrives=True,
                fields="id, webViewLink, name",
            )
            .execute()
        )
    except HttpError as exc:
        raise DriveExportError(f"Drive upload failed: {exc}") from exc

    if not result.get("id"):
        raise DriveExportError("Drive upload returned no file id")
    return result


__all__ = [
    "DriveExportResult",
    "collect_rendered_pptx",
    "deck_display_name",
    "deck_filename_stem",
    "export_deck_to_drive",
    "merge_deck",
]
