"""Regression: a successful call must not be read as a rate-limit rejection.

The SDK emits a RateLimitEvent alongside ordinary, successful responses. On a
normal subscription with no pay-as-you-go overage enabled, that event carries
`overage_status='rejected'` while `status='allowed'` — the request was fine.

rate_limit_note() once treated EITHER field being "rejected" as fatal, so every
agentic step aborted on a healthy account. It presented as a transport-level
rate limit ("the SDK is throttled where the CLI isn't") and was misdiagnosed as
one for a long time, because the CLI transport never parses these events and so
was immune. Only `status` is authoritative.

Run: uv run python tests/test_rate_limit.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import llm  # noqa: E402


class _Info:
    def __init__(self, status, overage_status):
        self.status = status
        self.overage_status = overage_status
        self.rate_limit_type = "five_hour"
        self.resets_at = 1785376800


class _Event:
    def __init__(self, info):
        self.rate_limit_info = info


# (label, status, overage_status, should_be_fatal)
CASES = [
    # Observed on a real, working Sonnet call — must NOT abort the step.
    ("working call", "allowed", "rejected", False),
    # Observed on genuinely exhausted Fable — must abort.
    ("exhausted model", "rejected", "rejected", True),
    ("fully healthy", "allowed", "allowed", False),
    ("overage allowed, request rejected", "rejected", "allowed", True),
]


def main() -> int:
    failures = []
    for label, status, overage, want_fatal in CASES:
        note = llm.rate_limit_note(_Event(_Info(status, overage)))
        got_fatal = note is not None
        if got_fatal != want_fatal:
            failures.append(
                f"{label}: status={status} overage={overage} -> "
                f"{'fatal' if got_fatal else 'ignored'}, expected "
                f"{'fatal' if want_fatal else 'ignored'}"
            )

    # An event with no rate-limit payload is not a rejection.
    if llm.rate_limit_note(_Event(None)) is not None:
        failures.append("event without rate_limit_info treated as a rejection")

    # A genuine rejection must stay actionable.
    note = llm.rate_limit_note(_Event(_Info("rejected", "rejected")))
    if note and "--llm-backend api" not in note:
        failures.append("rejection message lost its actionable remedy")

    # A rejection is an availability issue, so the tier ladder can walk past it
    # instead of failing the run.
    if note and not llm.is_availability_issue(note):
        failures.append("rejection not classified as an availability issue")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL RATE-LIMIT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
