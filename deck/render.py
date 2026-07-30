"""Which engine rasterises a pptx, and when.

Two backends, deliberately not interchangeable:

- **LibreOffice** (`deck/soffice.py`) — local, instant, offline, free. It
  substitutes any font that is not installed on the machine, which changes
  text metrics.
- **Google Slides** (`deck/slides_render.py`) — the engine that will
  actually display the deck, so fonts and text extents are the real ones.
  Costs a network round-trip and a temporary Drive file per render.

Because the costs differ by orders of magnitude, selection is by PURPOSE
rather than a global switch:

- ``FIDELITY`` — the render feeds a decision (the agent-slide acceptance
  gate, the brand audit, the corpus/neighbour renders a generated slide is
  measured against). Prefer Slides when it is available.
- ``FAST`` — the render is a convenience artifact (the bundled deck.pdf).
  LibreOffice.

``SG_RENDERER`` overrides everything: ``slides``, ``libreoffice`` or
``auto`` (default).

Reliability rule: rendering is never allowed to fail a build. Slides
failures fall through to LibreOffice; if neither can render, callers get
None and degrade to gate-only checks, exactly as they did before this
module existed.
"""

from __future__ import annotations

import os
from pathlib import Path

from deck import soffice

FIDELITY = "fidelity"
FAST = "fast"


def mode() -> str:
    m = (os.environ.get("SG_RENDERER") or "auto").lower()
    return m if m in ("slides", "libreoffice", "auto") else "auto"


def _use_slides(purpose: str) -> bool:
    m = mode()
    if m == "libreoffice":
        return False
    if m == "slides":
        return True
    return purpose == FIDELITY


def active_backend(purpose: str = FIDELITY) -> str:
    """What would actually be used — for logging and preflight."""
    from deck import slides_render

    if _use_slides(purpose) and slides_render.available():
        return "google-slides"
    return "libreoffice" if soffice.available() else "none"


def convert(
    src: Path, fmt: str, outdir: Path | None = None, purpose: str = FAST, log=print
) -> Path | None:
    """Render a pptx to "png" (first page) or "pdf". Returns the produced
    file, or None when no renderer is available."""
    if _use_slides(purpose):
        from deck import slides_render

        produced = slides_render.convert(src, fmt, outdir, log=log)
        if produced is not None:
            return produced
    return soffice.convert(src, fmt, outdir)


def convert_pages(
    src: Path, out_paths: list[Path], purpose: str = FIDELITY, log=print
) -> list[Path]:
    """Render every page of a multi-slide pptx. One round-trip on the
    Slides backend; LibreOffice cannot do this (``--convert-to png`` only
    emits the first slide), so it goes via PDF and splits locally."""
    out_paths = [Path(p) for p in out_paths]
    if _use_slides(purpose):
        from deck import slides_render

        produced = slides_render.convert_pages(src, out_paths, log=log)
        if produced is not None:
            return produced
    return _pages_via_libreoffice(src, out_paths)


def _pages_via_libreoffice(src: Path, out_paths: list[Path]) -> list[Path]:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="sg-pages-") as td:
        pdf = soffice.convert(Path(src), "pdf", Path(td))
        if pdf is None:
            return []
        import fitz

        doc = fitz.open(str(pdf))
        try:
            written = []
            for i, out in enumerate(out_paths):
                if i >= len(doc):
                    break
                out.parent.mkdir(parents=True, exist_ok=True)
                doc[i].get_pixmap(dpi=72).save(str(out))
                written.append(out)
            return written
        finally:
            doc.close()


def pdf_bytes(src: Path, purpose: str = FIDELITY, log=print) -> bytes | None:
    """A pptx rendered to PDF bytes, in memory."""
    if _use_slides(purpose):
        from deck import slides_render

        data = slides_render.export_pdf_bytes(src, log=log)
        if data is not None:
            return data
    import tempfile

    with tempfile.TemporaryDirectory(prefix="sg-pdf-") as td:
        produced = soffice.convert(Path(src), "pdf", Path(td))
        return produced.read_bytes() if produced else None
