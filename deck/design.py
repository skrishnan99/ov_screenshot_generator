"""Deck design conventions: derived from the corpus, expressed in prose.

The brand kit (deck/brand/) holds what code applies — font names, hex
colors, geometry limits. This module holds what only a model can apply: the
deck's *conventions*. What a numbered step slide looks like, that its title
carries a "Step N:" prefix, where supporting copy sits and in which colour,
how much air a slide gets, when deviating is legitimate.

Those conventions live in deck/brand/design_guide.md as PROSE, not schema —
a schema would pre-decide which aspects of "on-brand" exist, which is
exactly the thing we cannot enumerate. The guide is authored by an agent
session over the real corpus (every skeleton, its render, its sidecar
purpose, the deck spec's ordering), cached, versioned, and editable by hand
when it gets something wrong. Regenerate with design_cli.py.

Structured data is reserved for facts the code needs: neighbour identities,
step numbers, file paths. Those are assembled per slide by slide_brief() and
handed to the generator alongside the guide and the neighbours' renders —
so a slide being built between Step 4 and Step 6 can look at both.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from core.paths import PACKAGE_ROOT, data_dir
from deck.brand import BRAND_DIR

GUIDE_PATH = BRAND_DIR / "design_guide.md"


def load_design_guide() -> str:
    """The cached guide, or "" when it has never been derived."""
    return GUIDE_PATH.read_text() if GUIDE_PATH.exists() else ""


def skeleton_render(pptx_path: str | Path) -> Path | None:
    """Cached PNG render of a skeleton (or any one-slide pptx). Renders are
    keyed by file name + mtime so edited skeletons re-render automatically.
    Warm the cache with precompute_renders() before any fan-out: two callers
    racing on the same uncached file would both invoke LibreOffice and one
    could read a half-written PNG."""
    from deck import render

    src = Path(pptx_path)
    if not src.exists():
        return None
    cache = data_dir() / "skeleton_renders"
    cache.mkdir(parents=True, exist_ok=True)
    # The renderer belongs in the cache key: a LibreOffice render (with
    # substituted fonts) must not be served when the caller asked for
    # fidelity, or the Slides backend is silently bypassed for exactly the
    # images a generated slide is measured against.
    backend = render.active_backend(render.FIDELITY)
    out = cache / f"{src.stem}_{int(src.stat().st_mtime)}_{backend}.png"
    if out.exists():
        return out
    # FIDELITY: these renders are what a generated slide is measured
    # against, so they must show the real fonts. Cached by mtime, so the
    # network cost is paid once per template change.
    produced = render.convert(src, "png", cache, purpose=render.FIDELITY)
    if produced is None:
        return None
    produced.replace(out)
    return out


def precompute_renders(paths) -> int:
    """Render every distinct pptx once, serially. Returns how many were
    rendered fresh (cache hits cost nothing)."""
    fresh = 0
    for p in sorted({str(x) for x in paths if x}):
        before = Path(p).exists()
        if before and skeleton_render(p):
            fresh += 1
    return fresh


def _is_agent(entry: dict) -> bool:
    return "agent_slide" in entry


def group_brief(slides: list[dict]) -> dict:
    """Visual context for every agent slide in the deck, resolved to the
    nearest FIXED (non-agent) slide on each side.

    Agent slides are built together and don't exist yet, so using a sibling
    as a neighbour would make the result order-dependent. The nearest fixed
    slide is a known-good exemplar of the deck's language — and because
    agent slides sit inside the numbered-step run, that exemplar is
    naturally the step template they must match.

    Returns {"per_slide": {sid: {previous, next}}, "renders": [paths]}.
    """
    live = [s for s in slides if not s.get("skipped")]

    def describe(entry: dict | None) -> dict | None:
        if entry is None:
            return None
        tokens = entry.get("tokens") or {}
        title = next(
            (
                str(v)
                for k, v in tokens.items()
                if k in ("title", "_ff_title", "recipe_title") and str(v).strip()
            ),
            "",
        )
        render = skeleton_render(entry["skeleton"]) if entry.get("skeleton") else None
        return {
            "id": entry.get("id", ""),
            "title": title,
            "step_no": tokens.get("step_no"),
            "skeleton": entry.get("skeleton") or None,
            "render": str(render) if render else None,
        }

    def nearest_fixed(pos: int, step: int) -> dict | None:
        i = pos + step
        while 0 <= i < len(live):
            if not _is_agent(live[i]):
                return describe(live[i])
            i += step
        return None

    # Warm the render cache serially for every skeleton that can appear as a
    # neighbour, before anything fans out or a session starts.
    precompute_renders(
        s.get("skeleton") for s in live if not _is_agent(s) and s.get("skeleton")
    )

    per_slide, renders = {}, []
    for pos, entry in enumerate(live):
        if not _is_agent(entry):
            continue
        prev, nxt = nearest_fixed(pos, -1), nearest_fixed(pos, +1)
        per_slide[entry["id"]] = {"previous": prev, "next": nxt}
        for side in (prev, nxt):
            if side and side.get("render"):
                renders.append(side["render"])
    return {"per_slide": per_slide, "renders": sorted(set(renders))}


def slide_brief(slides: list[dict], index: int) -> dict:
    """Facts a generator needs about where this slide sits: the neighbours
    that survived, their titles/step numbers, and renders of their layouts."""

    def describe(entry: dict | None) -> dict | None:
        if entry is None:
            return None
        tokens = entry.get("tokens") or {}
        title = next(
            (
                str(v)
                for k, v in tokens.items()
                if k in ("title", "_ff_title", "recipe_title") and str(v).strip()
            ),
            "",
        )
        render = None
        if entry.get("agent_pptx"):
            render = skeleton_render(entry["agent_pptx"])
        elif entry.get("skeleton"):
            render = skeleton_render(entry["skeleton"])
        return {
            "id": entry.get("id", ""),
            "title": title,
            "step_no": tokens.get("step_no"),
            "skeleton": entry.get("skeleton") or None,
            "render": str(render) if render else None,
        }

    live = [e for e in slides if not e.get("skipped")]
    try:
        pos = live.index(slides[index])
    except ValueError:
        return {"previous": None, "next": None}
    return {
        "previous": describe(live[pos - 1]) if pos > 0 else None,
        "next": describe(live[pos + 1]) if pos + 1 < len(live) else None,
    }


DERIVE_PROMPT = """You are writing the design guide for an automatically generated,
customer-facing slide deck (Overview AI camera inspection test reports).

Everything you need is in this directory:
- `corpus/` — every slide template (.pptx) the deck is built from, each with
  a PNG render of the same name, and (where present) a `<name>.yaml` sidecar
  describing what that template's content holes are for.
- `deck_spec.yaml` — the slide sequence for a variant: the order templates
  appear in, which repeat per AI model, and which participate in the
  numbered "Step N" run (`step_counter: true`).
- `brand/` — the brand kit (fonts, palette, voice) and reference renders.

READ the renders — the visual conventions are in the pixels, not just the
XML. Then write `design_guide.md`: the instructions a designer needs to
produce a NEW slide that looks like it always belonged in this deck.

Cover, in plain prose (no schema, no invented rules — describe what the
corpus actually does):
- The slide families you can see (e.g. numbered configuration steps, title,
  results/stat slides, static information slides) and how to recognise which
  one a new slide belongs to from its content.
- For each family: title treatment (wording pattern, size, weight, colour,
  position), body copy treatment (size, colour, where it sits, how long),
  image treatment (placement, proportion, margins), and the slide's overall
  composition and use of whitespace.
- Sequence conventions: which slides carry a "Step N:" prefix and how the
  numbering behaves when a slide is inserted into the middle of a run.
- How the purple palette is actually used (accent vs. ground), and where the
  logo/sidebar chrome appears.
- Concrete numbers wherever the corpus shows them (point sizes, inches from
  the edge) — a designer should be able to match a step slide exactly.
- When deviating is legitimate, and what must never change.

Write it for someone who will read it and immediately build a slide. Be
specific and concise; no preamble, no meta-commentary. Save it as
`design_guide.md` in this directory."""


def derive_design_guide(variant: str = "ov80i", log=print) -> Path:
    """Author deck/brand/design_guide.md from the real corpus (maintainer
    operation; the result is cached and versioned with the templates)."""
    import tempfile

    from deck.agent_slide import run_agent_session

    workspace = Path(tempfile.mkdtemp(prefix="sg-design-"))
    corpus = workspace / "corpus"
    corpus.mkdir()
    folders = [
        PACKAGE_ROOT / "deck" / "skeletons" / variant,
        PACKAGE_ROOT / "deck" / "skeletons" / "_shared",
    ]
    n_rendered = 0
    for folder in folders:
        if not folder.is_dir():
            continue
        for pptx in sorted(folder.glob("*.pptx")):
            if (corpus / pptx.name).exists():
                continue  # variant-specific template wins over _shared
            shutil.copy(pptx, corpus / pptx.name)
            sidecar = pptx.with_suffix(".yaml")
            if sidecar.exists():
                shutil.copy(sidecar, corpus / sidecar.name)
            render = skeleton_render(pptx)
            if render:
                shutil.copy(render, corpus / f"{pptx.stem}.png")
                n_rendered += 1
    spec = PACKAGE_ROOT / "decks" / f"{variant}.yaml"
    if spec.exists():
        shutil.copy(spec, workspace / "deck_spec.yaml")
    shutil.copytree(BRAND_DIR, workspace / "brand", dirs_exist_ok=True)

    n_templates = len(list(corpus.glob("*.pptx")))
    log(f"deriving design guide from {n_templates} templates ({n_rendered} rendered)")
    turns, error = run_agent_session(workspace, DERIVE_PROMPT, log=log)
    produced = workspace / "design_guide.md"
    if not produced.exists():
        raise RuntimeError(
            f"design guide not produced after {turns} turns"
            + (f": {error}" if error else "")
        )
    GUIDE_PATH.write_text(produced.read_text())
    log(f"design guide written ({len(produced.read_text())} chars, {turns} turns) -> {GUIDE_PATH}")
    return GUIDE_PATH


def write_brief(workspace: Path, guide: str, brief: dict) -> None:
    """Materialise the design guide + neighbour context into a workspace:
    prose in design_guide.md, facts in neighbours.json, layouts as PNGs."""
    if guide:
        (workspace / "design_guide.md").write_text(guide)
    neighbours = {}
    folder = workspace / "neighbours"
    for side in ("previous", "next"):
        info = brief.get(side)
        if not info:
            continue
        entry = {k: v for k, v in info.items() if k in ("id", "title", "step_no")}
        folder.mkdir(exist_ok=True)
        if info.get("render"):
            dest = folder / f"{side}.png"
            shutil.copy(info["render"], dest)
            entry["render"] = f"neighbours/{dest.name}"
        if info.get("skeleton") and Path(info["skeleton"]).exists():
            dest = folder / f"{side}_template.pptx"
            shutil.copy(info["skeleton"], dest)
            entry["template"] = f"neighbours/{dest.name}"
        neighbours[side] = entry
    if neighbours:
        folder.mkdir(exist_ok=True)
        (folder / "neighbours.json").write_text(json.dumps(neighbours, indent=2))
