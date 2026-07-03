from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

from .doc_store import delete_source, upsert_chunks, upsert_source
from .resources_chunker import chunk_resource_file
from .resources_linker import link_resource_chunks

_RESOURCES_EXTENSIONS = (".yml", ".yaml", ".json", ".xml")

_RESOURCES_SKIP_PATTERNS = ("entity-match", "ms-list", "logback", "bigtable-schema")


def should_skip_resource(path: Path) -> bool:
    p = str(path).lower()
    return any(pat in p for pat in _RESOURCES_SKIP_PATTERNS)


def _stable_id(source: str, index: int) -> str:
    key = f"{source}::{index}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def index_resource_file(
    conn: sqlite3.Connection,
    source: str,
    graph_class_names: set[str] | None = None,
    graph_method_names: set[str] | None = None,
) -> int:
    path = Path(source)
    if should_skip_resource(path):
        return 0

    try:
        chunks, source_type = chunk_resource_file(path)
    except (ValueError, Exception):
        return 0

    if not chunks:
        return 0

    class_names = graph_class_names or set()
    method_names = graph_method_names or set()

    linked = link_resource_chunks(chunks, class_names, method_names, source)

    now = int(time.time() * 1000)
    records = []
    for i, (title, body) in enumerate(chunks):
        records.append(
            {
                "id": _stable_id(source, i),
                "source_path": source,
                "source_type": source_type,
                "title": title or path.stem,
                "content": body,
                "linked_classes": linked[i] if i < len(linked) else "",
                "chunk_index": i,
                "ts": now,
            }
        )

    delete_source(conn, source)
    upsert_chunks(conn, records)
    upsert_source(conn, source, source_type, path.stem, len(records))
    return len(records)


def discover_resource_files(
    repo: Path,
    skip_folders: set[str] | None = None,
) -> list[Path]:
    from ..filters.file_filters import apply_filters

    candidates: list[Path] = []
    for resources_dir in repo.rglob("resources"):
        if not resources_dir.is_dir():
            continue
        for f in resources_dir.rglob("*"):
            if f.is_file() and f.suffix.lower() in _RESOURCES_EXTENSIONS:
                candidates.append(f)

    if not candidates:
        return []

    return apply_filters(candidates, repo, skip_folders)
