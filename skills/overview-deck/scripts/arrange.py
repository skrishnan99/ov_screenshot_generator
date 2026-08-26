#!/usr/bin/env python3
"""Tier 3/4: adaptive layout as validated DATA, never generated code.

Tier 3 fixes WHAT (the spec's content: matched images + resolved text) and
adapts HOW: one image with text reads differently from six images with text,
and choosing the arrangement is genuinely a judgment call. Tier 4 also
generates the content, from a brief.

v1's main reliability leak was the model writing Python build scripts. Here
the arranger returns a small JSON plan — a list of slides, each naming an
ovdeck layout and its args — which code validates structurally and code
executes. The model never positions anything; ovdeck still owns geometry,
and its save() gates plus brandcheck remain the backstop.

Failure ladder (the v1 fallback pattern, kept because it works): validate ->
one retry with the validator's feedback -> deterministic fallback arrangement
that is plain but correct. A boring slide ships; a broken one never does.
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = SKILL.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# What an arrangement may use, and the structural limits code enforces.
# (Real overflow is measured by ovdeck; these catch shape errors early.)
ARRANGEABLE = {
    "figure": {"images": 1, "text": {"caption"}},
    "split": {"images": 1, "text": {"card_title", "intro", "para", "bullets", "footnote"}},
    "two_up": {"images": 2, "text": {"caption", "left_caption", "right_caption"}},
    "rows": {"images": 0, "text": {"entries", "intro"}},
    "statement": {"images": 0, "text": {"intro", "card_title", "bullets"}},
}

ARRANGE_SCHEMA = {
    "type": "object",
    "properties": {
        "slides": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "layout": {"type": "string", "enum": sorted(ARRANGEABLE)},
                    "title": {"type": "string"},
                    "images": {"type": "array", "items": {"type": "string"}},
                    "text": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                "required": ["layout", "title", "images", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["slides"],
    "additionalProperties": False,
}

ARRANGE_PROMPT = """Arrange fixed content onto 1-3 slides of a branded report deck.

The content is FIXED — every image listed must appear exactly once across
the slides, and the text must be carried (split across slides is fine, but
nothing dropped, nothing added). Your only decision is the arrangement:
which layout(s), what goes together, how it reads best.

Use the FEWEST slides that hold the content. Never spin a stray sentence
off into a continuation slide — an imageless slide needs enough text to
justify existing (several sentences or bullet lines); otherwise fold that
text into the other slide's fields (para, bullets, footnote).

Available layouts and their text fields:
- figure: one image + caption. For one strong image.
- split: one image + card_title/intro/para/bullets/footnote. Image left,
  explanation right, rendered top-to-bottom in that field order — intro is a
  purple framing section of its own ABOVE everything (standing copy labeled
  [intro] goes there, verbatim); prose that should READ AFTER the bullets (a
  rationale, a why) goes in footnote, never para.
- two_up: exactly two images + caption and/or left_caption/right_caption
  (those exact field names). For before/after or pairs.
- rows: no image; entries as "label | detail" lines (max 5) + intro.
- statement: no image; intro + card_title + bullets.

Slide title base: "{title}" — reuse or extend it; a second slide may append
"(continued)".

IMAGES (use each exactly once, refer by path):
{images}

TEXT (carry all of it):
{text}

Return JSON per the schema. Bullets/entries fields take newline-separated
lines; entries lines are "label | detail"."""


class ArrangeError(RuntimeError):
    pass


def arrange_call(title: str, image_paths: list[str], text: dict[str, str],
                 feedback: str = "", hint: str = ""):
    from core import llm

    prompt = ARRANGE_PROMPT.format(
        title=title,
        images="\n".join(f"- {p}" for p in image_paths) or "(none)",
        text="\n".join(f"[{k}]\n{v}" for k, v in text.items()) or "(none)",
    )
    if hint:
        prompt += (
            "\n\nDEFAULT ARRANGEMENT — follow this unless the content's shape "
            "makes it impossible, and deviate as little as needed: " + hint
        )
    if feedback:
        prompt += f"\n\nYour previous arrangement was rejected: {feedback}. Fix exactly that."
    return llm.complete(prompt, schema=ARRANGE_SCHEMA, max_tokens=2500)["slides"]


def _norm(s: str) -> str:
    return " ".join(str(s).split())


def validate_arrangement(slides: list[dict], image_paths: list[str],
                         must_carry: tuple | list = ()) -> list[str]:
    """`must_carry`: literal spec-token values — the author's exact words —
    each of which must appear verbatim (whitespace-normalized) somewhere in
    the arranged text. LLM-resolved text stays prompt-governed; only
    literals get the code guarantee."""
    problems = []
    used: list[str] = []
    if must_carry:
        all_text = _norm(" ".join(
            v for s in slides for v in (s.get("text") or {}).values() if v))
        for lit in must_carry:
            if lit and _norm(lit) not in all_text:
                problems.append(
                    f"literal text dropped (must appear verbatim): {str(lit)[:80]!r}")
    for i, s in enumerate(slides):
        spec = ARRANGEABLE.get(s.get("layout"))
        if spec is None:
            problems.append(f"slide {i}: unknown layout {s.get('layout')!r}")
            continue
        if len(s.get("images", [])) != spec["images"]:
            problems.append(
                f"slide {i}: {s['layout']} takes {spec['images']} image(s), got {len(s.get('images', []))}"
            )
        for p in s.get("images", []):
            if p not in image_paths:
                problems.append(f"slide {i}: image {p!r} is not part of this content")
            used.append(p)
        for k in s.get("text", {}):
            if k not in spec["text"]:
                problems.append(f"slide {i}: {s['layout']} has no text field {k!r}")
        if s.get("layout") == "rows":
            entries = [ln for ln in s["text"].get("entries", "").splitlines() if ln.strip()]
            if not entries or len(entries) > 5 or any("|" not in ln for ln in entries):
                problems.append(f"slide {i}: rows entries must be 1-5 'label | detail' lines")
    if sorted(used) != sorted(image_paths):
        problems.append(f"images used {sorted(used)} != content images {sorted(image_paths)}")
    # Anti-padding: in a multi-slide arrangement, an imageless slide carrying
    # only a scrap of text is a continuation that should not exist (a real
    # build shipped a lone sentence rattling in an empty statement card).
    if len(slides) > 1:
        for i, s in enumerate(slides):
            if not s.get("images"):
                total = sum(len(str(v)) for v in (s.get("text") or {}).values())
                if total < 180:
                    problems.append(
                        f"slide {i}: imageless continuation with only {total} chars "
                        f"of text — fold it into the other slide(s) instead")
    return problems


def fallback_arrangement(title: str, image_paths: list[str], text: dict[str, str],
                         must_carry: tuple | list = ()) -> list[dict]:
    """Plain but correct: pair images into two_ups, then figures; text rides
    the first slide as a caption or becomes a statement when there is no
    image. Deterministic — the same content always falls back the same way.

    Must-carry literals are guaranteed by construction: with one image they
    get a split's intro section (a truncated figure caption would cut
    them); otherwise they lead the caption/body untruncated."""
    carry = [v for v in must_carry if v]
    if carry and len(image_paths) == 1:
        rest = " ".join(v for v in text.values()
                        if v and v != "—" and v not in carry)
        return [{"layout": "split", "title": title, "images": [image_paths[0]],
                 "text": {"card_title": "", "intro": "\n".join(carry),
                          "para": " ".join(rest.split())[:400]}}]
    slides: list[dict] = []
    body = "\n\n".join(v for v in text.values() if v and v != "—")
    lead = " ".join(" ".join(carry).split())
    rest = " ".join("\n\n".join(v for v in text.values()
                                if v and v != "—" and v not in carry).split())
    caption = ((lead + " ") if lead else "") + rest[:200]
    imgs = list(image_paths)
    while len(imgs) >= 2:
        a, b = imgs[:2]
        imgs = imgs[2:]
        slides.append({"layout": "two_up", "title": title, "images": [a, b],
                       "text": {"caption": caption if not slides else ""}})
    if imgs:
        slides.append({"layout": "figure", "title": title, "images": [imgs[0]],
                       "text": {"caption": caption if not slides else ""}})
    if not slides:
        slides.append({"layout": "statement", "title": title, "images": [],
                       "text": {"intro": caption or "—", "card_title": "Summary",
                                "bullets": body or "—"}})
    return slides


def arrange(title: str, image_paths: list[str], text: dict[str, str],
            hint: str = "", feedback: str = "", log=print,
            must_carry: tuple | list = ()) -> list[dict]:
    """`feedback` carries build-level pressure into the FIRST call — e.g.
    the save gate's overflow issues from a rejected emit, so the retry
    build picks roomier arrangements instead of redrawing blind.
    `must_carry` lists literal spec-token values that must survive the
    arrangement verbatim (code-enforced; see validate_arrangement)."""
    plan = arrange_call(title, image_paths, text, feedback=feedback, hint=hint)
    problems = validate_arrangement(plan, image_paths, must_carry)
    if problems:
        log(f"  arrange: retrying ({'; '.join(problems[:3])})")
        plan = arrange_call(title, image_paths, text,
                            feedback="; ".join(problems[:5]), hint=hint)
        problems = validate_arrangement(plan, image_paths, must_carry)
    if problems:
        log(f"  arrange: fallback layout ({'; '.join(problems[:3])})")
        plan = fallback_arrangement(title, image_paths, text, must_carry)
    return plan
