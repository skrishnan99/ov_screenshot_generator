"""Agent SDK navigator: same contract as core.navigator.run_step, but the
agentic loop is run by Claude Code (via claude-agent-sdk) on the user's
subscription — no API key. The browser tools are exposed as in-process SDK
MCP tools; tool semantics, trace recording, and the StepResult contract are
shared with the API navigator (core.navigator).

Threading: Playwright's sync API is bound to the thread that created the
browser (the main thread) and cannot run inside a live asyncio loop. The SDK
loop therefore runs in a worker thread, and each tool call is marshaled to
the main thread over a queue pair: the async handler enqueues the request
and blocks (off-loop) until the main thread has executed the browser action
and posted the result. Tool calls are sequential, so one in-flight request
at a time is an invariant, not a limitation.
"""

from __future__ import annotations

import queue
import threading

from core import llm
from core.navigator import MAX_MODEL_CALLS, SYSTEM_PROMPT, TOOLS, StepResult, _execute

_DONE = object()


def _to_mcp_content(content) -> list[dict]:
    """Anthropic-style tool output -> MCP content blocks."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    out = []
    for block in content:
        if block.get("type") == "image":
            src = block["source"]
            out.append(
                {"type": "image", "data": src["data"], "mimeType": src["media_type"]}
            )
        else:
            out.append(block)
    return out


def run_step_sdk(
    browser,
    goal: str,
    postcondition: str,
    log=print,
    max_model_calls: int = MAX_MODEL_CALLS,
    model: str = llm.SONNET,
) -> StepResult:
    """Preferred model first; an unavailable tier retries the whole step on
    the next one (a rejected session does nothing, so nothing is lost)."""
    chain = llm.fallback_chain(model)
    for i, candidate in enumerate(chain):
        result = _run_step_sdk_once(
            browser, goal, postcondition, log, max_model_calls, candidate
        )
        if (
            result.status != "success"
            and llm.is_availability_issue(result.evidence)
            and i < len(chain) - 1
        ):
            log(f"  {candidate} unavailable; falling back to {chain[i + 1]}")
            continue
        if candidate != model and result.status == "success":
            llm.record_substitution(model, candidate, "unavailable", log)
        return result
    return result


def _run_step_sdk_once(
    browser,
    goal: str,
    postcondition: str,
    log,
    max_model_calls: int,
    model: str,
) -> StepResult:
    import asyncio

    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        create_sdk_mcp_server,
        query,
        tool,
    )

    actions: list[dict] = []
    outcome: dict = {}
    requests: queue.Queue = queue.Queue()
    responses: queue.Queue = queue.Queue()
    state = {"calls": 0, "error": None}

    def make_tool(spec: dict):
        name = spec["name"]

        @tool(name, spec["description"], spec["input_schema"])
        async def handler(args, _name=name):
            requests.put((_name, args))
            content = await asyncio.to_thread(responses.get)
            return {"content": _to_mcp_content(content)}

        return handler

    server = create_sdk_mcp_server(
        name="nav", tools=[make_tool(spec) for spec in TOOLS]
    )
    options = ClaudeAgentOptions(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"nav": server},
        allowed_tools=[f"mcp__nav__{spec['name']}" for spec in TOOLS],
        tools=[],  # no built-in tools; the page is the only world
        setting_sources=[],
        permission_mode="bypassPermissions",
        max_turns=max_model_calls,
        # Page snapshots and tool results travel back through the SDK's stdio
        # stream, whose 1MB default raises a FATAL reader error rather than
        # degrading. A large DOM snapshot must not kill a navigation step.
        max_buffer_size=llm.SDK_BUFFER_BYTES,
    )
    user = (
        f"Task:\n{goal}\n\n"
        f"Postcondition that must be true before you report success:\n{postcondition}\n\n"
        f"Current URL: {browser.url()}"
    )

    async def run():
        async for msg in query(prompt=user, options=options):
            if isinstance(msg, AssistantMessage):
                state["calls"] += 1
            if isinstance(msg, ResultMessage) and msg.is_error:
                state["error"] = str(msg.result)[:300]

    def worker():
        try:
            asyncio.run(run())
        except Exception as e:  # surfaced as a step failure, never a hang
            state["error"] = f"{type(e).__name__}: {e}"
        finally:
            requests.put(_DONE)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    # Main thread: execute browser actions as the SDK loop requests them.
    while True:
        item = requests.get()
        if item is _DONE:
            break
        name, args = item
        if name == "report_outcome":
            outcome.update(args)
            responses.put("acknowledged")
            continue
        try:
            content, record = _execute(browser, name, args, log)
        except Exception as e:
            content, record = f"Error: tool {name} raised {e}", None
        if record:
            actions.append(record)
        responses.put(content)
    thread.join(timeout=60)

    if outcome:
        return StepResult(
            status=outcome.get("status", "failure"),
            matched_recipe=outcome.get("matched_recipe", ""),
            evidence=outcome.get("evidence", ""),
            notes=outcome.get("notes", ""),
            actions=actions,
            model_calls=state["calls"],
        )
    reason = state["error"] or f"agent finished without report_outcome"
    return StepResult(
        "failure",
        "",
        f"{reason} (turns={state['calls']}, budget={max_model_calls})",
        "",
        actions,
        state["calls"],
    )
