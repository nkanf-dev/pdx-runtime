"""Separate small, synchronized evidence from host-local databases and raw dumps."""
from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path

SYNC_LIMIT = 256 * 1024


def atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


@dataclass(frozen=True)
class Workspace:
    local: Path
    docs: Path

    def __post_init__(self):
        local, docs = self.local.resolve(), self.docs.resolve()
        if local == docs or docs in local.parents or local in docs.parents:
            raise ValueError("Local artifact storage and synchronized docs must be separate trees")
        object.__setattr__(self, "local", local)
        object.__setattr__(self, "docs", docs)

    def write_doc(self, relative: str, content: str) -> Path:
        path = (self.docs/relative).resolve()
        if self.docs.resolve() not in path.parents:
            raise ValueError("Document path escapes workspace")
        if path.suffix not in {".md", ".json", ".py", ".toml"}:
            raise ValueError("Only small text artifacts are synchronized")
        if len(content.encode("utf-8")) > SYNC_LIMIT:
            raise ValueError("Artifact exceeds sync size budget; store locally and index its hash")
        atomic_write(path, content)
        return path

    def cache_path(self, sha256: str, name: str) -> Path:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError("Invalid build fingerprint")
        if Path(name).name != name:
            raise ValueError("Cache filename must be a basename")
        return self.local/"builds"/sha256/name

    def write_json(self, path: Path, value: object) -> None:
        if self.local.resolve() not in path.resolve().parents:
            raise ValueError("Raw JSON must stay in local artifact storage")
        atomic_write(path, json.dumps(value,ensure_ascii=False,separators=(",", ":")))
