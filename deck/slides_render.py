"""Render pptx through Google Slides — the engine that will actually
display the deck.

Upload the pptx to Drive (auto-converted to a native Slides file), export
it back as PDF, rasterise the pages locally with PyMuPDF, delete the
temporary Drive file. The point is fidelity: Slides renders with the real
fonts and its own text metrics, so a preview produced this way matches what
an engineer sees when they open the deck — LibreOffice substitutes any font
that is not installed locally, which changes text extents and can make a
layout look fine here and overflow there.

Costs a network round-trip and a Drive write per render, so callers select
it by purpose (see deck/render.py) rather than using it everywhere. Batch
rendering amortises the round-trip across a whole deck: one upload, one PDF,
pages split locally.

Every path deletes the temporary Drive file, including on failure — a
rasteriser must never leave litter in the engineer's Drive.
"""

from __future__ import annotations

from pathlib import Path

SLIDES_MIME = "application/vnd.google-apps.presentation"
PPTX_MIME = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
# 2x of 96dpi: crisp enough to judge layout, small enough to stay cheap as
# vision input (the API caps images near 1568px anyway).
DEFAULT_DPI = 144


class SlidesRenderError(RuntimeError):
    pass


def available() -> bool:
    """True when a Slides render could plausibly work: credentials
    configured and a cached token present. Never triggers a browser."""
    try:
        from publish.gdrive import auth_state

        return bool(auth_state().get("ready"))
    except Exception:
        return False


def _client(log=print):
    from publish.gdrive import DriveClient

    return DriveClient(log=log)


def _upload_convert(client, src: Path) -> str:
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(src), mimetype=PPTX_MIME, resumable=True)
    body = {"name": f"_sg_render_{src.stem}", "mimeType": SLIDES_MIME}
    request = client.service.files().create(
        body=body, media_body=media, fields="id", supportsAllDrives=True
    )
    response = None
    while response is None:
        _status, response = request.next_chunk(num_retries=5)
    file_id = response.get("id")
    if not file_id:
        raise SlidesRenderError("Drive upload returned no file id")
    return file_id


def _export_pdf(client, file_id: str) -> bytes:
    pdf = (
        client.service.files()
        .export(fileId=file_id, mimeType="application/pdf")
        .execute(num_retries=5)
    )
    if not pdf:
        raise SlidesRenderError("Drive PDF export returned no content")
    return pdf


def _delete_quietly(client, file_id: str) -> None:
    try:
        client.service.files().delete(
            fileId=file_id, supportsAllDrives=True
        ).execute(num_retries=3)
    except Exception:
        pass


def _pdf_to_pngs(pdf: bytes, out_paths: list[Path], dpi: int) -> list[Path]:
    import fitz

    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        if len(doc) == 0:
            raise SlidesRenderError("PDF export contained zero pages")
        written = []
        zoom = dpi / 72.0
        for i, out in enumerate(out_paths):
            if i >= len(doc):
                break
            out.parent.mkdir(parents=True, exist_ok=True)
            doc[i].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).save(str(out))
            written.append(out)
        return written
    finally:
        doc.close()


def convert(
    src: Path, fmt: str, outdir: Path | None = None, dpi: int = DEFAULT_DPI, log=print
) -> Path | None:
    """Render `src` (a .pptx) through Google Slides. `fmt` is "png" (first
    page) or "pdf" (the whole deck). Returns the produced file, or None if
    Slides rendering is unavailable — callers fall back."""
    src = Path(src)
    if fmt not in ("png", "pdf") or not src.exists() or not available():
        return None
    outdir = Path(outdir or src.parent)
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"{src.stem}.{fmt}"

    client = _client(log=log)
    file_id = None
    try:
        file_id = _upload_convert(client, src)
        pdf = _export_pdf(client, file_id)
    except Exception as e:
        log(f"  slides render unavailable ({str(e)[:90]}); falling back")
        return None
    finally:
        if file_id:
            _delete_quietly(client, file_id)

    if fmt == "pdf":
        out.write_bytes(pdf)
        return out
    written = _pdf_to_pngs(pdf, [out], dpi)
    return written[0] if written else None


def convert_pages(
    src: Path, out_paths: list[Path], dpi: int = DEFAULT_DPI, log=print
) -> list[Path] | None:
    """Render every page of a multi-slide pptx in ONE round-trip. Returns
    the written PNGs (in page order), or None when unavailable."""
    src = Path(src)
    if not src.exists() or not out_paths or not available():
        return None
    client = _client(log=log)
    file_id = None
    try:
        file_id = _upload_convert(client, src)
        pdf = _export_pdf(client, file_id)
    except Exception as e:
        log(f"  slides batch render unavailable ({str(e)[:90]}); falling back")
        return None
    finally:
        if file_id:
            _delete_quietly(client, file_id)
    return _pdf_to_pngs(pdf, list(out_paths), dpi)


def export_pdf_bytes(src: Path, log=print) -> bytes | None:
    """PDF bytes for a pptx as Slides would render it — used by the brand
    audit, which wants pages in memory rather than files."""
    src = Path(src)
    if not src.exists() or not available():
        return None
    client = _client(log=log)
    file_id = None
    try:
        file_id = _upload_convert(client, src)
        return _export_pdf(client, file_id)
    except Exception as e:
        log(f"  slides render unavailable ({str(e)[:90]}); falling back")
        return None
    finally:
        if file_id:
            _delete_quietly(client, file_id)
