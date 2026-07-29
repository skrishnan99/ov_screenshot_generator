"""Run output organization: audience-tiered folders + a machine-readable
asset index.

Folders are for humans (deliverables vs data vs archive vs debug); the index
(`RunOutput.assets`, embedded in the manifest) is the contract downstream
pipelines consume — they filter by role/kind/step and never glob directories,
so steps can be added or removed without breaking consumers.
"""

from __future__ import annotations

from pathlib import Path

_KIND_FOLDERS = {
    ("screenshot", "deliverable"): "deliverables/screenshots",
    ("image", "deliverable"): "deliverables/images",
    ("report", "deliverable"): "deliverables/report",
}
_ROLE_FOLDERS = {
    "archive": "archive",
    "data": "data",
    "debug": "debug",
}


class RunOutput:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.assets: list[dict] = []

    def folder_for(self, kind: str, role: str) -> Path:
        rel = _KIND_FOLDERS.get((kind, role)) or _ROLE_FOLDERS.get(role)
        if rel is None:
            rel = "archive"  # unknown combos are kept, never lost
        folder = self.run_dir / rel
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def save(
        self,
        name: str,
        content: bytes | str,
        *,
        kind: str,
        role: str = "deliverable",
        step: str | None = None,
        item: str | None = None,
        description_key: str | None = None,
    ) -> Path:
        dest = self.folder_for(kind, role) / name
        if isinstance(content, str):
            dest.write_text(content)
        else:
            dest.write_bytes(content)
        self.register(
            dest, kind=kind, role=role, step=step, item=item,
            description_key=description_key,
        )
        return dest

    def register(
        self,
        path: Path,
        *,
        kind: str,
        role: str,
        step: str | None = None,
        item: str | None = None,
        description_key: str | None = None,
    ) -> None:
        """Index a file that was written by other means (e.g. a browser
        download saved directly to its destination)."""
        entry: dict = {
            "path": str(path.relative_to(self.run_dir)),
            "kind": kind,
            "role": role,
            "step": step,
        }
        if item:
            entry["item"] = item
        if description_key:
            entry["description_key"] = description_key
        self.assets.append(entry)

    def rel(self, path: Path) -> str:
        return str(path.relative_to(self.run_dir))
