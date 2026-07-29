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

The agentic navigator (core/navigator.py) intentionally stays on the direct
API regardless of backend: its loop executes browser tools mid-conversation,
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
import uuid
from pathlib import Path

import jsonschema

# Model tiers. Assignment policy: OPUS for quality-critical work (the
# authoritative descriptions, customer-facing copy, recipe resolution, and
# navigator escalation); SONNET for capable-but-cheaper agentic/extraction
# work (first-attempt navigation, enumerations, image verification); HAIKU
# for high-volume binary judgments (image-load polling) whose failure modes
# are self-healing.
OPUS = "claude-opus-5"
SONNET = "claude-sonnet-5"
HAIKU = "claude-haiku-4-5-20251001"
DEFAULT_MODEL = OPUS


def _load_env_file() -> None:
    """Load KEY=VALUE lines from the project .env into os.environ (never
    overriding values already set), so runs launched outside the user's
    interactive shell still find ANTHROPIC_API_KEY etc."""
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env_file()


class LLMError(RuntimeError):
    pass


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
        img_files = []
        for img in images or []:
            ext = "jpg" if _media_type(img) == "image/jpeg" else "png"
            p = self.workdir / f"input_{uuid.uuid4().hex[:10]}.{ext}"
            p.write_bytes(img)
            img_files.append(p)
        parts = []
        if img_files:
            names = ", ".join(f.name for f in img_files)
            parts.append(
                f"First use the Read tool to view the image file(s) in the current "
                f"directory: {names}. Then answer based on what you see."
            )
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
    else:
        raise ValueError(f"unknown LLM backend: {name!r} (expected 'api' or 'claude-code')")
    return _backend


def set_backend(obj) -> None:
    """Test seam: inject a stub backend object exposing complete()."""
    global _backend
    _backend = obj


def complete(prompt, schema=None, images=None, max_tokens=4000, model=DEFAULT_MODEL):
    """schema -> validated dict; no schema -> reply text.
    images: list of raw PNG/JPEG bytes shown to the model before the prompt.
    Raises LLMRefusal when the model declines, LLMError on transport failure."""
    return backend().complete(
        prompt, schema=schema, images=images, max_tokens=max_tokens, model=model
    )
