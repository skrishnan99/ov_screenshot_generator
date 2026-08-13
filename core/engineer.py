"""The sales engineer's contact profile: who signs the report's contact slide.

Stored once per user at data_dir()/engineer.json — the same persistent home
as the Google token and trace cache, so it survives plugin updates. The
/ov-test-report command collects it in its single up-front question the
first time; every later run reads it silently.

Missing or unusable data NEVER fails a build: absent fields resolve
field-wise to visibly generic placeholders ("SE Name", ...) — obviously a
gap to fix before sending, rather than silently attributing the report to
the wrong person. SG_ENGINEER_NAME/EMAIL/PHONE override per run.
"""

from __future__ import annotations

import json
import os

from core.paths import data_dir

FIELDS = ("name", "email", "phone")

PLACEHOLDERS = {
    "name": "SE Name",
    "email": "SE Email",
    "phone": "SE Contact Number",
}

_ENV = {
    "name": "SG_ENGINEER_NAME",
    "email": "SG_ENGINEER_EMAIL",
    "phone": "SG_ENGINEER_PHONE",
}


def profile_path():
    return data_dir() / "engineer.json"


def format_phone(raw: str) -> str:
    """US display format, applied on the way OUT (stored values stay as
    typed, so existing profiles need no migration): 10 digits render as
    (909) 615-6153; 11 digits with a leading 1 drop the 1 first. Anything
    else — international, short, malformed — passes through exactly as
    entered rather than being mangled. Idempotent on formatted input."""
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return raw
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def load_profile() -> tuple[dict, str]:
    """(contact, source) — contact always carries every field, phone in US
    display format (see format_phone).

    source: "profile" when every field came from the file/env, "partial"
    when some did, "placeholder" when none. Callers surface non-"profile"
    sources in their summaries so a generic contact never ships unnoticed.
    """
    stored: dict = {}
    p = profile_path()
    try:
        if p.exists():
            raw = json.loads(p.read_text())
            if isinstance(raw, dict):
                stored = raw
    except Exception:
        stored = {}

    contact: dict = {}
    real = 0
    for f in FIELDS:
        val = os.environ.get(_ENV[f], "").strip() or str(stored.get(f, "") or "").strip()
        if val:
            contact[f] = format_phone(val) if f == "phone" else val
            real += 1
        else:
            contact[f] = PLACEHOLDERS[f]
    source = "profile" if real == len(FIELDS) else "partial" if real else "placeholder"
    return contact, source


def save_profile(name: str, email: str, phone: str) -> None:
    """Write the profile (used by the one-time collection at preflight)."""
    p = profile_path()
    p.write_text(json.dumps(
        {"name": name.strip(), "email": email.strip(), "phone": phone.strip()},
        indent=2,
    ) + "\n")
