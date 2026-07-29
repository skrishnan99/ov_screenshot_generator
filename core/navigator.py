"""Claude navigator agent: completes one task step by driving the browser.

Used when no trace exists for the camera's UI version, or when replay failed.
Every successful click/type is recorded so the run can be replayed
deterministically next time (see core.trace).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

import anthropic

MODEL = "claude-opus-5"
MAX_MODEL_CALLS = 30

SYSTEM_PROMPT = """You are a browser-navigation agent operating an industrial vision camera's web UI \
to complete one precisely-defined step. You interact with the page only through the provided tools.

Rules:
- Refs are reassigned on every snapshot. After any action that changes the page (click, type, \
navigation), call snapshot before acting again; stale refs are invalid.
- Buttons with short labels include their enclosing row text as (in: "..."). Use it to pick the \
button belonging to the right item.
- The recipe name given in the task is approximate, not exact. Fuzzy-match it against the recipe \
names actually visible in the UI and choose the single best match. If nothing is a confident match, \
or two candidates are equally plausible, report failure and list the candidate names you saw.
- Verify the stated postcondition with snapshot/page evidence (URL, visible text) before reporting \
success. Never assume an action worked.
- If the page shows an unexpected state (error, permission message, unrelated dialog), handle it \
only if it is obviously part of the flow; otherwise report failure describing exactly what you saw.
- Prefer text snapshots; take a screenshot only when text is insufficient (e.g. purely visual state).
- When finished — success or failure — call report_outcome exactly once, then stop."""

TOOLS = [
    {
        "name": "snapshot",
        "description": "List the interactive elements on the page with numbered refs. Call after every page-changing action.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "page_text",
        "description": "Visible text of the whole page, for reading state or labels that are not interactive.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "screenshot",
        "description": "Screenshot of the current viewport. Use only when text snapshots are insufficient.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "click",
        "description": "Click the element with this ref from the latest snapshot.",
        "input_schema": {
            "type": "object",
            "properties": {"ref": {"type": "integer"}},
            "required": ["ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "click_text",
        "description": "Fallback: click the first element containing this visible text, when no ref matches.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "type_text",
        "description": "Clear the input with this ref and type the given text into it.",
        "input_schema": {
            "type": "object",
            "properties": {"ref": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["ref", "text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "wait_for_text",
        "description": "Wait up to timeout_s seconds for the given text to become visible.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "timeout_s": {"type": "number"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "wait_seconds",
        "description": "Wait a fixed number of seconds (max 60). Use when the task instructs you to wait for slow data/image loading.",
        "input_schema": {
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
            "required": ["seconds"],
            "additionalProperties": False,
        },
    },
    {
        "name": "wait_for_button_enabled",
        "description": "Wait until the button/element with this exact text becomes enabled, polling the page. Returns one of: enabled (act on it); still disabled but the page has settled (its data finished loading — treat as genuinely unavailable); or still disabled at the ceiling with the page still changing (judge the state yourself).",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "max_wait_s": {"type": "number"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "wait_for_image_load",
        "description": "Wait until the page's main capture/inspection image has fully rendered, by polling with a vision model. Use after an action that loads a new image (e.g. clicking Previous). Returns as soon as the image is judged fully loaded, or after max_wait_s (default 90) with the last verdict.",
        "input_schema": {
            "type": "object",
            "properties": {"max_wait_s": {"type": "number"}},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "report_outcome",
        "description": "Report the final outcome of the step. Call exactly once, when done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["success", "failure"]},
                "matched_recipe": {
                    "type": "string",
                    "description": "The exact recipe name as shown in the UI that you matched, if the task involved a recipe.",
                },
                "evidence": {
                    "type": "string",
                    "description": "Concrete evidence the postcondition holds (or what failed): URL, visible text.",
                },
                "notes": {"type": "string"},
            },
            "required": ["status", "evidence"],
            "additionalProperties": False,
        },
    },
]


@dataclass
class StepResult:
    status: str
    matched_recipe: str
    evidence: str
    notes: str
    actions: list = field(default_factory=list)
    model_calls: int = 0


def run_step(browser, goal: str, postcondition: str, log=print) -> StepResult:
    client = anthropic.Anthropic()
    user = (
        f"Task:\n{goal}\n\n"
        f"Postcondition that must be true before you report success:\n{postcondition}\n\n"
        f"Current URL: {browser.url()}"
    )
    messages = [{"role": "user", "content": user}]
    actions: list[dict] = []

    for call_n in range(1, MAX_MODEL_CALLS + 1):
        response = client.messages.create(
            model=MODEL, max_tokens=4096, system=SYSTEM_PROMPT, tools=TOOLS, messages=messages
        )
        if response.stop_reason == "refusal":
            return StepResult("failure", "", "model refused the request", "", actions, call_n)

        messages.append({"role": "assistant", "content": response.content})
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            messages.append(
                {
                    "role": "user",
                    "content": "Continue using tools; when finished call report_outcome.",
                }
            )
            continue

        results = []
        outcome = None
        for tu in tool_uses:
            content, record = _execute(browser, tu.name, tu.input, log)
            if tu.name == "report_outcome":
                outcome = tu.input
                content = "acknowledged"
            results.append(
                {"type": "tool_result", "tool_use_id": tu.id, "content": content}
            )
            if record:
                actions.append(record)
        messages.append({"role": "user", "content": results})

        if outcome:
            return StepResult(
                status=outcome.get("status", "failure"),
                matched_recipe=outcome.get("matched_recipe", ""),
                evidence=outcome.get("evidence", ""),
                notes=outcome.get("notes", ""),
                actions=actions,
                model_calls=call_n,
            )

    return StepResult(
        "failure", "", f"action budget exhausted ({MAX_MODEL_CALLS} model calls)", "", actions,
        MAX_MODEL_CALLS,
    )


def _execute(browser, name: str, args: dict, log):
    """Returns (tool_result_content, trace_record_or_None)."""
    if name == "snapshot":
        return browser.snapshot(), None
    if name == "page_text":
        return browser.page_text(), None
    if name == "screenshot":
        data = base64.standard_b64encode(browser.screenshot_bytes()).decode()
        return (
            [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": data},
                }
            ],
            None,
        )
    if name == "click":
        item = dict(browser.last_items.get(args["ref"]) or {})
        result = browser.click(args["ref"])
        log(f"  agent: click {item.get('tag')} \"{item.get('text')}\" -> {result[:80]}")
        record = None
        if not result.startswith("Error") and item:
            record = {
                "action": "click",
                "tag": item["tag"],
                "text": item["text"],
                "ctx": item.get("ctx", ""),
            }
        return result, record
    if name == "click_text":
        result = browser.click_text(args["text"])
        log(f"  agent: click_text \"{args['text']}\" -> {result[:80]}")
        record = None
        if not result.startswith("Error"):
            record = {"action": "click_text", "text": args["text"]}
        return result, record
    if name == "type_text":
        item = dict(browser.last_items.get(args["ref"]) or {})
        result = browser.type_text(args["ref"], args["text"])
        record = None
        if not result.startswith("Error") and item:
            record = {
                "action": "type_text",
                "tag": item["tag"],
                "text": item["text"],
                "ctx": item.get("ctx", ""),
                "value": args["text"],
            }
        return result, record
    if name == "wait_for_text":
        return browser.wait_for_text(args["text"], args.get("timeout_s", 5)), None
    if name == "wait_seconds":
        seconds = min(float(args.get("seconds", 5)), 60.0)
        log(f"  agent: waiting {seconds:.0f}s")
        browser.page.wait_for_timeout(int(seconds * 1000))
        return f"Waited {seconds:.0f} seconds.", None
    if name == "wait_for_button_enabled":
        # The target page renders "no data yet" and "no data at all" identically,
        # so a single look can't distinguish them. Disambiguate over time:
        # enabled -> done; page text quiet for QUIET_S with the button still
        # disabled -> data settled, genuinely unavailable; ceiling -> undecided.
        text = args["text"]
        max_wait = min(float(args.get("max_wait_s", 90)), 180.0)
        poll_s, quiet_s = 5.0, 30.0
        elapsed = quiet = 0.0
        prev_snap = None
        while True:
            snap = browser.snapshot()
            matches = [it for it in browser.last_items.values() if it["text"] == text] or [
                it for it in browser.last_items.values() if text in it["text"]
            ]
            if matches and any(not it["disabled"] for it in matches):
                log(f'  agent: "{text}" enabled after {elapsed:.0f}s')
                return f'"{text}" is now enabled (after {elapsed:.0f}s).', None
            quiet = quiet + poll_s if snap == prev_snap else 0.0
            prev_snap = snap
            if quiet >= quiet_s:
                log(f'  agent: page settled; "{text}" still disabled after {elapsed:.0f}s')
                if not matches:
                    return (
                        f'No element with text "{text}" exists and the page has been '
                        f"stable for {quiet:.0f}s — it will not appear.",
                        None,
                    )
                return (
                    f'"{text}" is still disabled and the page has been stable for '
                    f"{quiet:.0f}s — its data has finished loading, so it is genuinely "
                    "unavailable (e.g. no captures).",
                    None,
                )
            if elapsed >= max_wait:
                log(f'  agent: ceiling; "{text}" still disabled after {max_wait:.0f}s')
                return (
                    f'"{text}" is still disabled after the {max_wait:.0f}s ceiling and '
                    "the page is still changing. Judge the state yourself before deciding.",
                    None,
                )
            browser.page.wait_for_timeout(int(poll_s * 1000))
            elapsed += poll_s
    if name == "wait_for_image_load":
        # Polls with independent stateless vision calls so the agent's own
        # context doesn't accumulate a screenshot per check.
        from core.describer import poll_image_loaded

        max_wait = min(float(args.get("max_wait_s", 90)), 180.0)
        ok, msg = poll_image_loaded(browser, max_wait_s=max_wait, log=log)
        if ok:
            return f"Image fully {msg}", None
        return f"Image {msg}. Verify the state yourself before finishing.", None
    if name == "report_outcome":
        return "acknowledged", None
    return f"Error: unknown tool {name}", None
