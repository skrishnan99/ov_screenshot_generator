"""deck_builder: screenshot-run → Google Slides case-study deck.

Public API:

    from deck_builder import build_deck

    result = build_deck(
        "runs/20260728_163942",
        user_dir="my_context/",          # optional: notes.md + images
        folder_id="1-dkrv...",           # optional Drive folder
    )
    print(result.drive_link)

Pipeline (each stage degrades gracefully — see the stage modules):

    RunBundle + UserContext
        → llm.build_llm_cache        (one Claude call; {} on failure)
        → planner.plan_deck          (skeleton order from recipe_decryption)
        → asset_matching.auto_assign (user images replace system defaults)
        → render.render_deck         (slide_creator fills each skeleton)
        → drive_export               (merge + upload as native Google Slides)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from deck_builder.manifest import DeckManifest

__version__ = "0.1.0"


@dataclass(frozen=True)
class BuildResult:
    """Everything a caller needs to report on a completed build."""

    manifest: DeckManifest
    out_dir: Path
    merged_pptx: Optional[Path]
    drive_link: str = ""
    warnings: list[str] = field(default_factory=list)


def build_deck(
    run_dir: str | Path,
    *,
    user_dir: str | Path | None = None,
    out_dir: str | Path | None = None,
    folder_id: Optional[str] = None,
    skip_llm: bool = False,
    skip_drive: bool = False,
    new_drive_file: bool = False,
    llm_model: Optional[str] = None,
    force: bool = False,
) -> BuildResult:
    """Run the full pipeline for one run directory.

    Args:
        run_dir: A screenshot_generator run directory (contains
            ``manifest.json``, ``descriptions.json``, screenshots).
        user_dir: Optional user-context directory (``notes.md`` +
            images + optional ``captions.json``). User content always
            outranks system-generated content.
        out_dir: Where deck artifacts go. Defaults to
            ``out/<run_id>`` under the current working directory.
        folder_id: Drive folder / shared-drive id for the export.
            Defaults to ``$DRIVE_EXPORT_FOLDER_ID``. None targets
            My Drive root.
        skip_llm: Plan with deterministic fills only (no Claude calls,
            including asset matching).
        skip_drive: Stop after the local merged ``.pptx``.
        new_drive_file: Always create a fresh Drive file instead of
            updating a previous export of this run in place.
        llm_model: Override the Anthropic model id for both LLM passes.
        force: Re-render slides even when fingerprints match.
    """
    import os

    from deck_builder.asset_matching import auto_assign
    from deck_builder.drive_export import export_deck_to_drive, merge_deck
    from deck_builder.llm import DEFAULT_MODEL, build_llm_cache, load_env
    from deck_builder.persistence import load_manifest, save_manifest
    from deck_builder.planner import plan_deck
    from deck_builder.render import RunContext, render_deck
    from deck_builder.run_bundle import load_run_bundle
    from deck_builder.user_context import load_user_context

    load_env()
    model = llm_model or DEFAULT_MODEL

    bundle = load_run_bundle(run_dir)
    user_context = load_user_context(user_dir)
    out_dir = Path(out_dir) if out_dir else Path("out") / bundle.run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Carry the previous export's Drive identity forward so re-building
    # the same run updates the same Drive file (stable link).
    previous_export = None
    try:
        previous_export = load_manifest(out_dir).drive_export
    except Exception:
        pass

    llm_cache = {} if skip_llm else build_llm_cache(
        bundle, user_notes=user_context.notes, model=model,
    )

    manifest = plan_deck(bundle, llm_cache=llm_cache, user_notes=user_context.notes)
    manifest.drive_export = previous_export

    if user_context.images and not skip_llm:
        auto_assign(manifest, user_context, model=model)

    ctx = RunContext(manifest=manifest, out_dir=out_dir)
    render_deck(ctx, force=force)
    save_manifest(manifest, out_dir)

    warnings: list[str] = []
    merged_path, skipped = merge_deck(manifest, out_dir)
    if skipped:
        warnings.append(f"{len(skipped)} slide(s) skipped: {', '.join(skipped)}")

    drive_link = ""
    if not skip_drive:
        result = export_deck_to_drive(
            manifest,
            out_dir,
            folder_id=folder_id or os.environ.get("DRIVE_EXPORT_FOLDER_ID") or None,
            update_existing=not new_drive_file,
        )
        save_manifest(manifest, out_dir)
        drive_link = result.info.web_view_link
        warnings.extend(result.warnings)

    return BuildResult(
        manifest=manifest,
        out_dir=out_dir,
        merged_pptx=merged_path,
        drive_link=drive_link,
        warnings=warnings,
    )


__all__ = ["BuildResult", "build_deck", "__version__"]
