from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _fingerprint(java_files: list[Path]) -> str:
    h = hashlib.sha1()
    for path in java_files:
        stat = path.stat()
        h.update(str(path).encode("utf-8"))
        h.update(str(stat.st_mtime_ns).encode("utf-8"))
        h.update(str(stat.st_size).encode("utf-8"))
    return h.hexdigest()


def cache_path(root: Path) -> Path:
    return root / ".java_code_intel_cache.json"


def load_cache(root: Path) -> dict | None:
    path = cache_path(root)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_cache(root: Path, payload: dict) -> None:
    cache_path(root).write_text(json.dumps(payload), encoding="utf-8")


def compute_fingerprint(java_files: list[Path]) -> str:
    return _fingerprint(java_files)
