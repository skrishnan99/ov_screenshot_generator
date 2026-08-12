"""The agent-sdk backend retries invalid structured output — 3 attempts,
with the validation error fed back — and retries NOTHING else.

The backend originally assumed native structured output needs no retries.
That failed in the field: one invalid library-page description had no
second chance, the entry became "[description failed: ...]", and the deck
lost its library slide. Now ATTEMPTS=3 like ClaudeCodeBackend. What this
suite pins:

- an invalid payload is retried with the validation error in the re-ask,
  and a later valid attempt wins (both the native structured_output path
  and the JSON-in-text fallback path),
- three invalid attempts raise LLMError naming the attempt count,
- refusals, backend errors and availability failures are NEVER retried —
  substitution/retry can't fix them and the tier ladder owns availability,
- schema-less calls return first-attempt text, no retry machinery.

Run: uv run python tests/test_sdk_structured_retry.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import llm  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {"description": {"type": "string"}},
    "required": ["description"],
    "additionalProperties": False,
}


class _Result:
    def __init__(self, structured=None, text="", stop_reason="end_turn",
                 is_error=False):
        self.structured_output = structured
        self.result = text
        self.stop_reason = stop_reason
        self.is_error = is_error


class _Backend(llm.AgentSdkBackend):
    """Overrides the single-shot seam; records every ask."""

    def __init__(self, script):
        super().__init__(query_fn=lambda **k: None)
        self.script = list(script)
        self.asks: list[str] = []

    def _invoke_once(self, full, schema, img_files, model):
        self.asks.append(full)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def main() -> int:
    failures = []

    # ---- invalid native payload, then valid: second attempt wins ----
    b = _Backend([
        _Result(structured={"wrong_key": 1}),
        _Result(structured={"description": "the Library page"}),
    ])
    out = b.complete("describe", schema=SCHEMA)
    if out != {"description": "the Library page"}:
        failures.append(f"retry did not recover a valid payload: {out}")
    if len(b.asks) != 2:
        failures.append(f"expected 2 attempts, saw {len(b.asks)}")
    if "previous reply was invalid" not in b.asks[1]:
        failures.append("validation error not fed back into the re-ask")

    # ---- no native payload, invalid text, then parseable text ----
    b = _Backend([
        _Result(structured=None, text="sorry, here is prose"),
        _Result(structured=None, text='{"description": "ok"}'),
    ])
    out = b.complete("describe", schema=SCHEMA)
    if out != {"description": "ok"}:
        failures.append(f"text-fallback retry failed: {out}")

    # ---- three invalid attempts -> LLMError naming the count ----
    b = _Backend([_Result(structured={"nope": i}) for i in range(3)])
    try:
        b.complete("describe", schema=SCHEMA)
        failures.append("three invalid attempts did not raise")
    except llm.LLMError as e:
        if "3 attempts" not in str(e):
            failures.append(f"error does not name the attempt count: {e}")
    if len(b.asks) != 3:
        failures.append(f"expected exactly 3 attempts, saw {len(b.asks)}")

    # ---- refusal: raised immediately, never retried ----
    b = _Backend([_Result(stop_reason="refusal"),
                  _Result(structured={"description": "x"})])
    try:
        b.complete("describe", schema=SCHEMA)
        failures.append("refusal did not raise")
    except llm.LLMRefusal:
        pass
    if len(b.asks) != 1:
        failures.append(f"refusal was retried: {len(b.asks)} attempts")

    # ---- backend error: raised immediately, never retried ----
    b = _Backend([_Result(is_error=True, text="exploded"),
                  _Result(structured={"description": "x"})])
    try:
        b.complete("describe", schema=SCHEMA)
        failures.append("backend error did not raise")
    except llm.LLMError:
        pass
    if len(b.asks) != 1:
        failures.append(f"backend error was retried: {len(b.asks)} attempts")

    # ---- availability failure: propagates for the tier ladder, no retry ----
    rate = llm.LLMError("usage limit reached for this model")
    b = _Backend([rate, _Result(structured={"description": "x"})])
    try:
        b.complete("describe", schema=SCHEMA)
        failures.append("availability failure did not raise")
    except llm.LLMError as e:
        if not llm.is_availability_issue(str(e)):
            failures.append(f"availability failure mangled: {e}")
    if len(b.asks) != 1:
        failures.append(f"availability failure was retried: {len(b.asks)} attempts")

    # ---- schema-less: first text back, one attempt ----
    b = _Backend([_Result(text="plain prose")])
    if b.complete("say hi") != "plain prose":
        failures.append("schema-less call mangled the text")
    if len(b.asks) != 1:
        failures.append("schema-less call used retry machinery")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL SDK-STRUCTURED-RETRY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
