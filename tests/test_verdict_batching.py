"""Acceptance verdicts: one batched render, concurrent judging.

The fidelity renderer is Google Slides, and one conversion costs an upload,
convert, PDF export, download and delete. The acceptance loop used to pay
that per slide, serially — 14 round trips in a measured build, the single
largest cost in the deck. `convert_pages` already renders a whole multi-slide
file in one round trip, so candidates are combined into one temp deck and
split locally, and the vision calls then run concurrently.

Batching is an OPTIMISATION: every failure path here must degrade to the
per-slide render, never fail a build. These tests pin that.

Run: uv run python tests/test_verdict_batching.py
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deck import agent_slide  # noqa: E402


def _fake_paths(tmp: Path, n: int) -> list[Path]:
    out = []
    for i in range(n):
        p = tmp / f"s{i}.pptx"
        p.write_bytes(b"not really a pptx")
        out.append(p)
    return out


def main() -> int:
    import tempfile

    failures = []
    calls = {"convert_pages": 0, "build_deck": 0, "render_png": 0}

    def stub_build_deck(jobs, out_path):
        calls["build_deck"] += 1
        Path(out_path).write_bytes(b"combined")

    def stub_convert_pages(src, out_paths, purpose=None, log=None):
        calls["convert_pages"] += 1
        produced = []
        for i, o in enumerate(out_paths):
            Path(o).write_bytes(f"png{i}".encode())
            produced.append(Path(o))
        return produced

    import deck.assemble as assemble_mod
    import deck.render as render_mod

    real_build, real_convert = assemble_mod.build_deck, render_mod.convert_pages
    assemble_mod.build_deck = stub_build_deck
    render_mod.convert_pages = stub_convert_pages

    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)

            # ---- ONE round trip for N slides, mapped in order ----
            paths = _fake_paths(tmp, 4)
            got = agent_slide.batch_render(paths, log=lambda *a: None)
            if calls["convert_pages"] != 1:
                failures.append(
                    f"4 slides caused {calls['convert_pages']} render round trips, want 1"
                )
            if len(got) != 4:
                failures.append(f"batch_render returned {len(got)} entries, want 4")
            for i, p in enumerate(paths):
                if got.get(p) != f"png{i}".encode():
                    failures.append(f"page {i} mapped to the wrong slide: {got.get(p)!r}")

            # ---- a short page list must be refused wholesale ----
            # Verifying a slide against another slide's image is worse than
            # paying for individual renders.
            def short_convert(src, out_paths, purpose=None, log=None):
                return [Path(out_paths[0])][:1]

            render_mod.convert_pages = short_convert
            if agent_slide.batch_render(_fake_paths(tmp, 3), log=lambda *a: None) != {}:
                failures.append("short page list was not refused")

            # ---- never raises, whatever goes wrong ----
            def boom(*a, **k):
                raise RuntimeError("renderer exploded")

            render_mod.convert_pages = boom
            try:
                if agent_slide.batch_render(_fake_paths(tmp, 3), log=lambda *a: None) != {}:
                    failures.append("renderer failure did not degrade to {}")
            except Exception as e:
                failures.append(f"batch_render raised instead of degrading: {e!r}")

            assemble_mod.build_deck = boom
            render_mod.convert_pages = stub_convert_pages
            try:
                if agent_slide.batch_render(_fake_paths(tmp, 3), log=lambda *a: None) != {}:
                    failures.append("build_deck failure did not degrade to {}")
            except Exception as e:
                failures.append(f"batch_render raised on build_deck failure: {e!r}")
            assemble_mod.build_deck = stub_build_deck

            # ---- a single slide is not worth batching ----
            calls["convert_pages"] = 0
            if agent_slide.batch_render(_fake_paths(tmp, 1), log=lambda *a: None) != {}:
                failures.append("single slide should skip batching")
            if calls["convert_pages"]:
                failures.append("single slide still hit the renderer")

            # ---- a supplied png is used instead of re-rendering ----
            def counting_render(_p):
                calls["render_png"] += 1
                return b"solo"

            real_render_png = agent_slide._render_png
            agent_slide._render_png = counting_render
            try:
                agent_slide._vision_verdict(tmp / "x.pptx", "brief", png=b"batched")
                if calls["render_png"]:
                    failures.append("supplied png ignored; slide was re-rendered")
                # ...and omitting it still renders (the fallback path).
                agent_slide._vision_verdict(tmp / "x.pptx", "brief")
                if calls["render_png"] != 1:
                    failures.append("omitted png did not fall back to rendering")
            finally:
                agent_slide._render_png = real_render_png
    finally:
        assemble_mod.build_deck, render_mod.convert_pages = real_build, real_convert

    # ---- structural: gate -> render -> concurrent verdicts, stable order ----
    src = inspect.getsource(agent_slide.build_agent_slides)
    order = [src.find(x) for x in ("issues = gate(", "batch_render(", "ThreadPoolExecutor")]
    if not all(i > 0 for i in order) or order != sorted(order):
        failures.append(f"acceptance phases out of order: {order}")
    if "for sid in pending if sid in failed" not in src:
        failures.append("still_failing is no longer rebuilt in pending order")
    if agent_slide.VERDICT_WORKERS < 1:
        failures.append("VERDICT_WORKERS must be >= 1")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL VERDICT-BATCHING CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
