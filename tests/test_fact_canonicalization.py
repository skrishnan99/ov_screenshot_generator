"""Fact-subject canonicalization against the model roster.

The describer transcribes whatever name a screen displays; the roster
(meta["models"], from the Inspection Setup DOM) is the authority. Two real
failures drove this: a sanmina run filed its only model's training facts
under "model: Model" while the roster said "Model C" (the 98% accuracy
rendered as an em dash), and the same run's meta carried subjects from other
recipes entirely ("class: Traton Bushing Wear/Center"). The rules must fill
the first hole WITHOUT reopening the Traton naming hazard — a roster with
both "Model" and "Model 2" must keep them strictly apart.

Run: uv run python tests/test_fact_canonicalization.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.resolver import canonicalize_fact_subject  # noqa: E402


def main() -> int:
    failures = []

    cases = [
        # (subject, roster, expected subject, expected action)
        # non-model subjects pass through untouched
        ("recipe", ["Model C"], "recipe", None),
        ("camera", ["Model C"], "camera", None),
        ("io_logic", [], "io_logic", None),
        ("global_config", ["Model C"], "global_config", None),
        # exact match keeps the roster's spelling, records no action
        ("model: Model C", ["Model C"], "model: Model C", None),
        ("model: model c", ["Model C"], "model: Model C", "rewritten"),
        # the sanmina case: truncated display name -> the one roster name
        ("model: Model", ["Model C"], "model: Model C", "rewritten"),
        ("class: Model/pass_pin", ["Model C"], "class: Model C/pass_pin", "rewritten"),
        # extension with a clean boundary is also rewritten
        ("model: Model C - Classification", ["Model C"], "model: Model C", "rewritten"),
        # alphanumeric continuation is NOT a boundary: "Mode" is not "Model"
        ("model: Mode", ["Model"], "unattributed: model: Mode", "quarantined"),
        # cross-recipe content is quarantined, kept for audit
        ("class: Traton Bushing Wear/Center", ["Model C"],
         "unattributed: class: Traton Bushing Wear/Center", "quarantined"),
        ("model: Inspection Type 1", ["Model C"],
         "unattributed: model: Inspection Type 1", "quarantined"),
        # the Traton roster: exact names never cross, ambiguity quarantines
        ("model: Model", ["Model", "Model 2"], "model: Model", None),
        ("model: Model 2", ["Model", "Model 2"], "model: Model 2", None),
        ("model: Mod", ["Model", "Model 2"], "unattributed: model: Mod", "quarantined"),
        # "Model 2" extends "Model" with an alnum-free boundary AND matches
        # "Model 2" exactly — exactness must win before prefix logic runs
        ("class: Model 2/Zero", ["Model", "Model 2"], "class: Model 2/Zero", None),
        # empty roster: every model subject is unattributable
        ("model: Model", [], "unattributed: model: Model", "quarantined"),
    ]
    for subject, roster, want_subject, want_action in cases:
        got_subject, got_action = canonicalize_fact_subject(subject, roster)
        if (got_subject, got_action) != (want_subject, want_action):
            failures.append(
                f"canonicalize({subject!r}, {roster}) -> "
                f"({got_subject!r}, {got_action!r}), want ({want_subject!r}, {want_action!r})"
            )

    # a quarantined subject must be invisible to the deck's model slices
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                           / "skills" / "overview-deck" / "scripts"))
    import content as content_mod

    quarantined, _ = canonicalize_fact_subject(
        "class: Traton Bushing Wear/Center", ["Model C"])
    facts = {quarantined: ["class_color: red  [from 05]"],
             "model: Model C": ["train_accuracy: 98%  [from 09]"]}
    s = content_mod._model_slice({"name": "Model C", "type": "classification"},
                                 facts, {}, [])
    if "train_accuracy: 98%" not in s:
        failures.append("canonicalized subject missed by the model slice")
    if "Traton" in s:
        failures.append("quarantined subject leaked into the model slice")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL FACT-CANONICALIZATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
