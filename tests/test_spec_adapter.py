"""Governed mutation: the spec changes iff the user asked, exactly as asked.

Enforcement is mechanical: the diff is recomputed in code and every changed
slide needs a justification whose quote appears verbatim in the request.
ANY violation rejects the whole adaptation and the base spec compiles
unmodified — there is no partial acceptance.

Run: uv run python tests/test_spec_adapter.py
"""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _specfix  # noqa: F401,E402  (sys.path setup)

import yaml  # noqa: E402

import adapt as adapt_mod  # noqa: E402
from deckspec import load_spec  # noqa: E402


def _edited(base, mutate):
    spec = copy.deepcopy(base)
    mutate(spec)
    return yaml.safe_dump(spec, sort_keys=False)


def main() -> int:
    failures = []
    base = load_spec()
    real_call = adapt_mod.adapt_call

    def run(request, spec_yaml, justifications):
        adapt_mod.adapt_call = lambda s, r: {
            "spec_yaml": spec_yaml, "justifications": justifications}
        try:
            return adapt_mod.adapt(base, request, log=lambda *a: None)
        finally:
            adapt_mod.adapt_call = real_call

    # ---- a justified removal applies, and the diff is exactly that ----
    req = "please skip the node-red slide this time"
    spec, diff = run(
        req,
        _edited(base, lambda s: s["slides"].__setitem__(
            slice(None), [x for x in s["slides"] if x.get("id") != "logic"])),
        [{"slide_id": "logic", "op": "remove_slide", "quote": "skip the node-red slide"}],
    )
    if not diff["applied"]:
        failures.append(f"justified removal rejected: {diff}")
    elif [c["op"] for c in diff["changes"]] != ["remove_slide"]:
        failures.append(f"diff not exactly one removal: {diff['changes']}")
    if any(s.get("id") == "logic" for s in spec["slides"]):
        failures.append("logic slide survived an applied removal")

    # ---- an UNJUSTIFIED extra change rejects the whole adaptation ----
    def sneaky(s):
        s["slides"] = [x for x in s["slides"] if x.get("id") != "logic"]
        for x in s["slides"]:
            if x.get("id") == "imaging":
                x["title"] = "Step {step}: Imaging (improved)"
    spec, diff = run(
        req, _edited(base, sneaky),
        [{"slide_id": "logic", "op": "remove_slide", "quote": "skip the node-red slide"}],
    )
    if diff["applied"]:
        failures.append("sneaky extra modification was accepted")
    if spec != base:
        failures.append("rejection did not return the base spec unmodified")

    # ---- a quote that is not in the request rejects ----
    spec, diff = run(
        "add a slide about lighting",
        _edited(base, lambda s: s["slides"].__setitem__(
            slice(None), [x for x in s["slides"] if x.get("id") != "logic"])),
        [{"slide_id": "logic", "op": "remove_slide", "quote": "skip the node-red slide"}],
    )
    if diff["applied"]:
        failures.append("fabricated quote was accepted")

    # ---- a mutation that breaks the schema rejects ----
    def breaks(s):
        s["slides"].append({"id": "new", "layout": "hero_banner"})
    spec, diff = run(
        "add a hero banner slide",
        _edited(base, breaks),
        [{"slide_id": "new", "op": "add_slide", "quote": "add a hero banner slide"}],
    )
    if diff["applied"]:
        failures.append("schema-breaking mutation was accepted")

    # ---- a no-op edit rejects (nothing to adapt) ----
    spec, diff = run("skip the node-red slide",
                     yaml.safe_dump(base, sort_keys=False), [])
    if diff["applied"]:
        failures.append("no-change adaptation was accepted")

    # ---- permitted_ops narrows (the future flag's mechanism) ----
    adapt_mod.adapt_call = lambda s, r: {
        "spec_yaml": _edited(base, lambda sp: sp["slides"].__setitem__(
            slice(None), [x for x in sp["slides"] if x.get("id") != "logic"])),
        "justifications": [{"slide_id": "logic", "op": "remove_slide",
                            "quote": "skip the node-red slide"}]}
    try:
        spec, diff = adapt_mod.adapt(base, "skip the node-red slide",
                                     permitted_ops=("modify_slide",),
                                     log=lambda *a: None)
        if diff["applied"]:
            failures.append("permitted_ops did not block a disallowed op")
    finally:
        adapt_mod.adapt_call = real_call

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL SPEC-ADAPTER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
