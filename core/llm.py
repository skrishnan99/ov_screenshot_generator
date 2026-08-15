"""Single choke point for every single-shot model call.

All resolvers, describers, and binders call complete() here; the backend is
selected once at startup (select_backend). Two backends:

- "api" (default): the Anthropic API directly — native structured outputs
  via output_config, images as base64 content blocks.
- "claude-code": each call runs through the local `claude` CLI in headless
  print mode, so the tool uses the user's existing Claude Code login instead
  of a separate API key. The CLI has no output_config, so schema enforcement
  happens at this layer: the schema is stated in the prompt, the reply is
  validated locally, and invalid replies get bounded retries. Images are
  written into the backend's private working directory and viewed by the
  CLI's Read tool (its only enabled tool; text-only calls disable all
  tools). Settings sources are disabled and cwd is isolated so the user's
  CLAUDE.md, hooks, and MCP servers never leak into these calls.

- "agent-sdk": same Claude Code subscription auth, but transported through
  the Claude Agent SDK (which manages the Claude Code process) with NATIVE
  structured output — and it is the one backend that also carries the
  agentic navigator (see core/navigator_sdk.py), eliminating the API key
  entirely.

Under "api" and "claude-code", the agentic navigator (core/navigator.py)
stays on the direct API: its loop executes browser tools mid-conversation,
which a one-shot headless call cannot do.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

import jsonschema

# Model tiers. Assignment policy: OPUS for quality-critical work (the
# authoritative descriptions, customer-facing copy, recipe resolution, and
# navigator escalation); SONNET for everything else — navigation,
# enumerations, image verification, the pick judges, and the load polls.
#
# HAIKU holds no preferred call sites any more; it survives as the
# fallback ladder's last rung. The old "Haiku for volume" policy died of
# two facts learned the hard way: (1) it never applied to judgments whose
# positive verdict is terminal (the capture pick judges — a false tier-1
# short-circuits and ships an image with no second opinion), and (2) on
# the agent-sdk transport Haiku is not even faster — session overhead
# dominates and Sonnet clears the image-read turn in fewer steps
# (measured 6.5s vs 14.6s per identical vision call). Do not "optimize"
# any judge back down without re-measuring.
OPUS = "claude-opus-5"
SONNET = "claude-sonnet-5"
HAIKU = "claude-haiku-4-5-20251001"
# Fable drives the agent-built slide sessions (deck/agent_slide.py).
FABLE = "claude-fable-5"
DEFAULT_MODEL = OPUS

# Capability order, strongest first. A call names its PREFERRED model; if
# that model is unavailable (subscription quota exhausted, no access, the
# API rate-limiting us), the request walks DOWN this ladder rather than
# failing the run. Substitutions are recorded, never silent.
TIER_ORDER = [FABLE, OPUS, SONNET, HAIKU]


def fallback_chain(model: str) -> list[str]:
    """The preferred model followed by every weaker tier."""
    if model in TIER_ORDER:
        return TIER_ORDER[TIER_ORDER.index(model) :]
    return [model]  # an explicitly pinned model we don't rank: no substitutes


_AVAILABILITY_MARKERS = (
    "rate limit", "rate_limit", "limit reached", "reached your", "quota",
    "usage limit", "subscription limit", "overloaded", "not_found_error",
    "does not exist", "model not found", "unavailable", "credit balance",
    "insufficient", "429", "529",
)
_AVAILABILITY_TYPES = {
    "RateLimitError", "NotFoundError", "OverloadedError", "InternalServerError",
}


def is_availability_issue(problem) -> bool:
    """True when a failure means 'this model can't serve us right now' —
    as opposed to a bad request or a refusal, which substitution won't fix."""
    if problem is None:
        return False
    if type(problem).__name__ in _AVAILABILITY_TYPES:
        return True
    text = str(problem).lower()
    return any(m in text for m in _AVAILABILITY_MARKERS)


_substitutions: list[dict] = []


def substitutions() -> list[dict]:
    """Every model downgrade this process made — surfaced in run records so
    a degraded result is always traceable."""
    return list(_substitutions)


def record_substitution(requested: str, used: str, reason: str, log=print) -> None:
    _substitutions.append(
        {"requested": requested, "used": used, "reason": str(reason)[:200]}
    )
    log(f"  model fallback: {requested} unavailable -> using {used}")


def _load_env_files() -> None:
    """Load KEY=VALUE lines into os.environ (never overriding values already
    set) from the package .env (dev checkouts) and the per-user data dir's
    .env (installed-plugin use), so runs launched outside the user's
    interactive shell still find ANTHROPIC_API_KEY etc."""
    candidates = [Path(__file__).resolve().parent.parent / ".env"]
    try:
        from core.paths import data_dir

        candidates.append(data_dir() / ".env")
    except Exception:
        pass
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env_files()


class LLMError(RuntimeError):
    pass


def rate_limit_note(event) -> str | None:
    """Turn an SDK RateLimitEvent into an actionable message, or None when
    the event is not a rejection. Subscription limits are per-model, so the
    fix is usually another model or the API backend — say so plainly.

    Only `status` is authoritative. `overage_status` reports whether
    pay-as-you-go overage is available, and it reads "rejected" on a normal
    account with no overage enabled — including on calls that SUCCEED:

        working Sonnet call -> status='allowed',  overage_status='rejected'
        exhausted Fable     -> status='rejected', overage_status='rejected'

    Treating either field as a rejection (as this once did) aborts every
    step on a healthy account, which looks exactly like a transport-level
    rate limit and was misdiagnosed as one for a long time. Check `status`.
    """
    info = getattr(event, "rate_limit_info", None)
    if info is None:
        return None
    if getattr(info, "status", "") != "rejected":
        return None
    import datetime

    resets = getattr(info, "resets_at", None)
    when = (
        datetime.datetime.fromtimestamp(resets).strftime("%Y-%m-%d %H:%M")
        if resets
        else "an unknown time"
    )
    return (
        f"Claude Code subscription limit reached for this model "
        f"({getattr(info, 'rate_limit_type', 'unknown')}); resets {when}. "
        f"Use another model (e.g. SG_AGENT_MODEL=claude-opus-5 for agent "
        f"sessions) or run with --llm-backend api."
    )


class LLMRefusal(RuntimeError):
    pass


def _media_type(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/png"


class ApiBackend:
    name = "api"

    def complete(self, prompt, schema=None, images=None, max_tokens=4000, model=DEFAULT_MODEL):
        import anthropic

        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": _media_type(img),
                    "data": base64.standard_b64encode(img).decode(),
                },
            }
            for img in images or []
        ]
        content.append({"type": "text", "text": prompt})
        kwargs = {}
        if schema:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        client = anthropic.Anthropic()
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
            **kwargs,
        ) as stream:
            response = stream.get_final_message()
        if response.stop_reason == "refusal":
            raise LLMRefusal("model refused the request")
        text = "".join(b.text for b in response.content if b.type == "text")
        return json.loads(text) if schema else text


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of a CLI reply that may carry prose/fences."""
    stripped = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object found in reply: {stripped[:120]!r}")
    return json.loads(stripped[start : end + 1])


# Anthropic resizes any image so its long edge is <= ~1568px before the model
# sees it, so pixels above this are discarded server-side regardless. Sending
# them costs nothing in quality and everything in payload: the CLI backends
# hand images to the model via the Read tool, whose result travels back
# through the SDK's stdio stream, and a full-resolution 1440p screenshot
# overflows that stream's buffer (see SDK_BUFFER_BYTES). The symptom is ugly
# and misleading — "Failed to decode JSON: JSON message exceeded maximum
# buffer size", surfacing as a FALSE "image not loaded" verdict that polls to
# its ceiling and then captures a possibly-unrendered page anyway.
VISION_MAX_EDGE = 1568

# The SDK's stdio reader defaults to a 1MB cap and raises a *fatal* reader
# error when a single message exceeds it, killing the session rather than
# degrading. Downscaling above keeps images well clear of that, but any large
# tool result can trip it, so raise the ceiling too — the two fixes are
# independent on purpose.
SDK_BUFFER_BYTES = 32 * 1024 * 1024


def downscale_for_vision(data: bytes, max_edge: int = VISION_MAX_EDGE) -> bytes:
    """Cap an image's long edge at what the API would resize it to anyway.

    Returns the input untouched when it is already small enough, or when
    Pillow is unavailable / the bytes will not decode — a vision call with an
    oversized image is still better than no vision call.
    """
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            if max(im.size) <= max_edge:
                return data
            scale = max_edge / max(im.size)
            resized = im.resize(
                (max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                Image.LANCZOS,
            )
            buf = io.BytesIO()
            if resized.mode in ("RGBA", "LA", "P"):
                resized.save(buf, format="PNG", optimize=True)
            else:
                resized.save(buf, format="JPEG", quality=88, optimize=True)
            out = buf.getvalue()
            # Only barely over the cap (1600 -> 1568) on flat UI chrome, the
            # re-encode can cost more bytes than the few pixels it drops. The
            # model sees the same thing either way — the API resizes on its
            # own — so keep whichever is cheaper to ship.
            return out if len(out) < len(data) else data
    except Exception:
        return data


def _write_image_files(images, workdir: Path) -> list[Path]:
    files = []
    for img in images or []:
        img = downscale_for_vision(img)
        ext = "jpg" if _media_type(img) == "image/jpeg" else "png"
        p = workdir / f"input_{uuid.uuid4().hex[:10]}.{ext}"
        p.write_bytes(img)
        files.append(p)
    return files


def _image_note(files: list[Path]) -> str:
    names = ", ".join(f.name for f in files)
    return (
        f"First use the Read tool to view the image file(s) in the current "
        f"directory: {names}. Then answer based on what you see."
    )


class ClaudeCodeBackend:
    name = "claude-code"
    ATTEMPTS = 3
    TIMEOUT_S = 600

    def __init__(self, run=subprocess.run):
        self._run = run
        self.exe = shutil.which("claude")
        if not self.exe:
            raise LLMError(
                "claude-code backend selected but the `claude` CLI was not found on PATH"
            )
        self.workdir = Path(tempfile.mkdtemp(prefix="sg-llm-"))

    def complete(self, prompt, schema=None, images=None, max_tokens=4000, model=DEFAULT_MODEL):
        img_files = _write_image_files(images, self.workdir)
        parts = []
        if img_files:
            parts.append(_image_note(img_files))
        parts.append(prompt)
        if schema:
            parts.append(
                "Respond with ONLY a JSON object (no prose, no code fences) that "
                f"validates against this JSON Schema:\n{json.dumps(schema)}"
            )
        full = "\n\n".join(parts)
        try:
            errors: list[str] = []
            for _ in range(self.ATTEMPTS):
                ask = full if not errors else (
                    f"{full}\n\nYour previous reply was invalid ({errors[-1]}). "
                    f"Reply again with ONLY a valid JSON object."
                )
                text = self._invoke(ask, model, tools="Read" if img_files else "")
                if not schema:
                    return text
                try:
                    data = _extract_json(text)
                    jsonschema.validate(data, schema)
                    return data
                except (ValueError, json.JSONDecodeError, jsonschema.ValidationError) as e:
                    errors.append(str(e)[:300])
            raise LLMError(
                f"claude-code backend: no valid structured output after "
                f"{self.ATTEMPTS} attempts: {errors[-1]}"
            )
        finally:
            for p in img_files:
                p.unlink(missing_ok=True)

    def _invoke(self, prompt: str, model: str, tools: str) -> str:
        cmd = [
            self.exe,
            "-p",
            "--output-format", "json",
            "--model", model,
            "--setting-sources", "",
            "--tools", tools,
        ]
        proc = self._run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            cwd=self.workdir,
            timeout=self.TIMEOUT_S,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            raise LLMError(f"claude CLI failed (exit {proc.returncode}): {detail}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise LLMError(f"claude CLI returned non-JSON output: {proc.stdout[:200]!r}")
        if payload.get("is_error"):
            raise LLMError(f"claude CLI error: {str(payload.get('result'))[:500]}")
        return payload.get("result") or ""


def run_coro_in_thread(factory):
    """Run an async callable to completion in a dedicated thread with its own
    event loop. Required because Playwright's sync API keeps an event loop
    registered on the main thread, which makes a bare asyncio.run() there
    fail with 'cannot be called from a running event loop'."""
    import asyncio

    result: dict = {}

    def runner():
        try:
            result["value"] = asyncio.run(factory())
        except BaseException as e:
            result["error"] = e

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


class AgentSdkBackend:
    """Routes calls through the Claude Agent SDK — the same Claude Code
    subscription auth as ClaudeCodeBackend, but the SDK manages the Claude
    Code process (no per-call spawn) and supports NATIVE structured output
    (output_format -> ResultMessage.structured_output). Native output is
    still validated locally and retried with the validation error fed back
    (ATTEMPTS, like ClaudeCodeBackend): the original assumption that native
    output needs no retries failed in the field — a library-page description
    came back invalid once, had no second chance, and cost a deck slide.
    Images use the same workdir + Read approach."""

    name = "agent-sdk"
    ATTEMPTS = 3

    def __init__(self, query_fn=None):
        self._query = query_fn  # test seam; resolved lazily from the SDK
        self.workdir = Path(tempfile.mkdtemp(prefix="sg-llm-"))

    def complete(self, prompt, schema=None, images=None, max_tokens=4000, model=DEFAULT_MODEL):
        img_files = _write_image_files(images, self.workdir)
        full = (_image_note(img_files) + "\n\n" + prompt) if img_files else prompt
        try:
            errors: list[str] = []
            for _ in range(self.ATTEMPTS):
                ask = full if not errors else (
                    f"{full}\n\nYour previous reply was invalid ({errors[-1]}). "
                    f"Respond again with output matching the required schema exactly."
                )
                # Availability failures, refusals and transport errors raise
                # straight through — only structured-output validation retries.
                result = self._invoke_once(ask, schema, img_files, model)
                if result.stop_reason == "refusal":
                    raise LLMRefusal("model refused the request")
                if result.is_error:
                    raise LLMError(f"agent-sdk backend error: {str(result.result)[:500]}")
                if not schema:
                    return result.result or ""
                try:
                    data = result.structured_output
                    if data is None:
                        data = _extract_json(result.result or "")
                    jsonschema.validate(data, schema)
                    return data
                except (ValueError, json.JSONDecodeError, jsonschema.ValidationError) as e:
                    errors.append(str(e)[:300])
            raise LLMError(
                f"agent-sdk backend: no valid structured output after "
                f"{self.ATTEMPTS} attempts: {errors[-1]}"
            )
        finally:
            for p in img_files:
                p.unlink(missing_ok=True)

    def _invoke_once(self, full, schema, img_files, model):
        """One SDK session; returns the ResultMessage. Raises LLMError on
        rate-limit rejection or a missing result. Overridable test seam."""
        from claude_agent_sdk import ClaudeAgentOptions, RateLimitEvent, ResultMessage

        query = self._query
        if query is None:
            from claude_agent_sdk import query

        options = ClaudeAgentOptions(
            model=model,
            cwd=str(self.workdir),
            setting_sources=[],
            tools=["Read"] if img_files else [],
            permission_mode="bypassPermissions",
            max_turns=8 if img_files else 2,
            max_buffer_size=SDK_BUFFER_BYTES,
            output_format=(
                {"type": "json_schema", "schema": schema} if schema else None
            ),
        )

        limited: list[str] = []

        async def run():
            final = None
            async for msg in query(prompt=full, options=options):
                if isinstance(msg, RateLimitEvent):
                    note = rate_limit_note(msg)
                    if note and not limited:
                        # Record and keep draining: raising mid-iteration
                        # closes the SDK's async generator while it runs.
                        limited.append(note)
                if isinstance(msg, ResultMessage):
                    final = msg
            return final

        try:
            result = run_coro_in_thread(run)
        except Exception:
            if limited:
                raise LLMError(limited[0]) from None
            raise
        if limited:
            raise LLMError(limited[0])
        if result is None:
            raise LLMError("agent-sdk backend: no result message from Claude Code")
        return result


_backend = None


def backend():
    global _backend
    if _backend is None:
        _backend = ApiBackend()
    return _backend


def select_backend(name: str | None):
    """Called once at startup. Unknown names fail loudly."""
    global _backend
    if name in (None, "", "api"):
        _backend = ApiBackend()
    elif name == "claude-code":
        _backend = ClaudeCodeBackend()
    elif name == "agent-sdk":
        _backend = AgentSdkBackend()
    else:
        raise ValueError(
            f"unknown LLM backend: {name!r} (expected 'api', 'claude-code', or 'agent-sdk')"
        )
    return _backend


def set_backend(obj) -> None:
    """Test seam: inject a stub backend object exposing complete()."""
    global _backend
    _backend = obj


def complete(prompt, schema=None, images=None, max_tokens=4000, model=DEFAULT_MODEL):
    """schema -> validated dict; no schema -> reply text.
    images: list of raw PNG/JPEG bytes shown to the model before the prompt.

    `model` is the PREFERRED tier: if it is unavailable the call walks down
    fallback_chain() and records the substitution. Refusals and malformed
    requests are NOT retried on a weaker model — substitution only fixes
    availability. Raises LLMRefusal when the model declines, LLMError when
    every tier is exhausted."""
    chain = fallback_chain(model)
    last_error: Exception | None = None
    for i, candidate in enumerate(chain):
        try:
            result = backend().complete(
                prompt, schema=schema, images=images, max_tokens=max_tokens,
                model=candidate,
            )
        except LLMRefusal:
            raise
        except Exception as e:
            if not is_availability_issue(e) or i == len(chain) - 1:
                raise
            last_error = e
            continue
        if candidate != model:
            record_substitution(model, candidate, last_error or "unavailable")
        return result
    raise LLMError(f"no model tier available for this call: {last_error}")
