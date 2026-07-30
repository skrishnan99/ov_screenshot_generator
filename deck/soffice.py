"""LibreOffice conversions with per-call profile isolation.

`soffice` is single-instance by default: concurrent invocations — or one
invocation while the engineer has LibreOffice open in the GUI — block on a
profile lock or fail outright. Every conversion here gets a private
UserInstallation directory, so renders are safe to run alongside anything
else. All three render sites (skeleton renders, agent-slide previews, the
brand audit) go through this module, and agent sessions are told to use the
same flag for their own renders.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

# The flag agent sessions must use for their own renders.
PROFILE_FLAG_HINT = "-env:UserInstallation=file:///tmp/sg-soffice-$$"


def available() -> bool:
    return shutil.which("soffice") is not None


def convert(
    src: Path, fmt: str, outdir: Path | None = None, timeout: int = 300
) -> Path | None:
    """Convert `src` to `fmt` ("png", "pdf"), returning the produced file.
    None when LibreOffice is absent or the conversion produced nothing."""
    exe = shutil.which("soffice")
    if not exe or not Path(src).exists():
        return None
    outdir = Path(outdir or Path(src).parent)
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sg-soffice-") as profile:
        cmd = [
            exe,
            f"-env:UserInstallation=file://{profile}",
            "--headless",
            "--convert-to",
            fmt,
            str(src),
            "--outdir",
            str(outdir),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
        except Exception:
            return None
    produced = outdir / f"{Path(src).stem}.{fmt}"
    return produced if produced.exists() else None
