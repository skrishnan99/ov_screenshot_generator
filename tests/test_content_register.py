"""The content resolver: register rules, shapes, scoping, retry ladder.

The three register principles (no minutiae, no absence-commentary, no
production narration) are enforced in exactly one place — the resolver's
prompt plus its code lint — so this suite pins both halves: the rules are in
the prompt, and the lint actually rejects violations.

Run: uv run python tests/test_content_register.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _specfix import make_run  # noqa: E402

import content as content_mod  # noqa: E402
import deckspec as ds  # noqa: E402
import matching as matching_mod  # noqa: E402
from _specfix import keyword_assign  # noqa: E402


def main() -> int:
    failures = []

    # ---- the rules are in the one prompt that writes copy ----
    rules = " ".join(content_mod.SYSTEM_RULES.lower().split())
    for phrase in ("never mention what was not run", "production narration",
                   "what a setting achieves", "em dash", "serial",
                   "quality manager"):
        if phrase not in rules:
            failures.append(f"register prompt lost: {phrase!r}")

    # ---- lint: banned vocabulary, shapes, lengths, the sanctioned dash ----
    req = content_mod.TokenReq(id="t", brief="b", shape="text", max_chars=100, scope="")
    cases = [
        ("The screenshot shows the part.", True),          # production narration
        ("Validation was not run for this model.", True),  # absence commentary
        ("Composited view of the template.", True),
        ("Results: n/a for two classes.", True),
        ("x" * 150, True),                                  # over length
        ("—", False),                                       # sanctioned no-data
        ("The camera checks 26 regions on the part.", False),
    ]
    for value, should_fail in cases:
        got = bool(content_mod.lint(req, value))
        if got != should_fail:
            failures.append(f"lint({value[:40]!r}) -> {got}, want {should_fail}")

    lines_req = content_mod.TokenReq(id="l", brief="b", shape="lines", max_chars=300, scope="")
    # one line is VALID: honesty-stripping may legitimately leave a single
    # data-bearing line, and a build must not fail for having less to say
    if content_mod.lint(lines_req, "Training accuracy: 100%"):
        failures.append("lines shape rejected a single data-bearing line")
    if content_mod.lint(lines_req, "a: 1\nb: 2\nc: 3"):
        failures.append("lines shape rejected a valid 3-line value")

    # the normalizer silences per-line absence mechanically (a real build
    # emitted "Training accuracy: —" before this existed)
    n = content_mod.normalize(lines_req, "Classes: Dent, Discolor\nTraining accuracy: —\nLoss: -")
    if "—" in n or "Loss" in n:
        failures.append(f"dash lines survived normalize: {n!r}")
    if "Classes: Dent, Discolor" not in n:
        failures.append(f"normalize dropped a data-bearing line: {n!r}")
    if content_mod.normalize(lines_req, "a: —\nb: —") != "—":
        failures.append("all-dash value did not collapse to the bare dash")
    pairs_req = content_mod.TokenReq(id="p", brief="b", shape="pairs", max_chars=300, scope="")
    if content_mod.lint(pairs_req, "no separators here\nagain"):
        pass
    else:
        failures.append("pairs shape accepted lines without '|'")

    # ---- scoping: a model slide's tokens see that model's slice only ----
    with tempfile.TemporaryDirectory() as td:
        run = make_run(Path(td))
        ctx = ds.build_context(run)
        jobs, _ = ds.expand(ds.load_spec(), ctx)
        holes = matching_mod.collect_holes(jobs)
        catalog = matching_mod.build_catalog(run)
        assignments = {a["hole"]: a["path"] for a in keyword_assign(holes, catalog)}
        material = content_mod.build_material(run)
        reqs = content_mod.collect(jobs, material, assignments)

        by_id = {r.id: r for r in reqs}
        cap = by_id.get("training_model-s.text")
        if cap is None:
            failures.append("no request collected for training_model-s.text")
        else:
            if "Model S" not in cap.scope:
                failures.append("model slide scope lacks its model")
            if "Horn Quality" in cap.scope:
                failures.append("Model S scope leaked Horn Quality material")
            if "Segmentation block page" not in cap.scope:
                failures.append("scope lacks the MATCHED block page's description")
        # the combined-ROI slide is model-neutral: its text sees BOTH trained
        # models' matched region screens, and not the untrained model's
        rois = by_id.get("rois.text")
        if rois is None:
            failures.append("no request collected for rois.text")
        else:
            for want in ("Inspection Setup screen with Model S",
                         "Inspection Setup screen with Horn Quality"):
                if want not in rois.scope:
                    failures.append(f"rois scope lacks {want!r}")
            if "Edge Check" in rois.scope:
                failures.append("rois scope leaked the never-trained model")
        logic = [r for r in reqs if r.id.startswith("logic.")]
        if not logic or all("defect pixels" not in r.scope for r in logic):
            failures.append("logic tokens did not receive the IO analysis")

        # ---- model slice: exact-name scoping and result-fact priority ----
        # A real roster was "Model", "Model 2", ... — substring matching gave
        # "Model" every other model's facts. And train_accuracy sat below the
        # per-subject cap behind ~100 slider facts: dashes with data present.
        slider_facts = [f"aug_slider_{i}: 0.5  [from 05]" for i in range(60)]
        tricky_facts = {
            "model: Model": slider_facts + ["train_accuracy: 100%  [from 07]"],
            "model: Model 2": ["training_images: 40  [from 07]"],
            "class: Model 2/Zero": ["label_count: 11  [from 08]"],
        }
        s1 = content_mod._model_slice({"name": "Model", "type": "segmentation"},
                                      tricky_facts, {}, [])
        if "train_accuracy: 100%" not in s1:
            failures.append("result fact buried below the model-slice cap")
        if "training_images: 40" in s1 or "label_count" in s1:
            failures.append("prefix-named model received another model's facts")
        s2 = content_mod._model_slice({"name": "Model 2", "type": "classification"},
                                      tricky_facts, {}, [])
        if "training_images: 40" not in s2 or "label_count: 11" not in s2:
            failures.append("exact-name scoping dropped the model's own facts")

        # ---- retry ladder: bad then good resolves; bad twice raises ----
        real = content_mod.resolve_call
        seq = {"n": 0}

        def flaky(reqs_, material_):
            seq["n"] += 1
            if seq["n"] == 1:
                return {r.id: "The screenshot shows things." for r in reqs_}
            return {r.id: "The camera checks the part." for r in reqs_}

        content_mod.resolve_call = flaky
        try:
            small = [content_mod.TokenReq(id="a.b", brief="x", shape="text",
                                          max_chars=100, scope="s")]
            vals = content_mod.resolve(_FakeJobs(small), material, {}, log=lambda *a: None)
            if vals.get("a.b") != "The camera checks the part.":
                failures.append(f"retry ladder did not recover: {vals}")
        finally:
            content_mod.resolve_call = real

        content_mod.resolve_call = lambda r, m: {x.id: "n/a" for x in r}
        try:
            content_mod.resolve(_FakeJobs(small), material, {}, log=lambda *a: None)
            failures.append("twice-bad token did not raise ContentError")
        except content_mod.ContentError:
            pass
        finally:
            content_mod.resolve_call = real

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL CONTENT-REGISTER CHECKS PASSED")
    return 0


class _FakeJobs(list):
    """collect() walks jobs; hand resolve() pre-built requests instead by
    monkeypatching collect for this list."""

    def __init__(self, reqs):
        super().__init__()
        self._reqs = reqs
        real_collect = content_mod.collect
        content_mod.collect = lambda jobs, material, assignments: (
            jobs._reqs if isinstance(jobs, _FakeJobs) else real_collect(jobs, material, assignments))


if __name__ == "__main__":
    raise SystemExit(main())
