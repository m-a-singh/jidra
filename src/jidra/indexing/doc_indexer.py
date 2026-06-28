from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from pathlib import Path
from typing import Iterator

# ── Chunking ──────────────────────────────────────────────────────────────────

_CHUNK_TARGET_CHARS = 1800   # ~450 tokens
_CHUNK_MAX_CHARS    = 3000


def _stable_id(source_path: str, chunk_index: int) -> str:
    return hashlib.sha1(f"{source_path}::{chunk_index}".encode()).hexdigest()[:16]


def _split_markdown(text: str) -> list[tuple[str | None, str]]:
    """
    Split markdown into (heading, body) chunks.
    Splits on H1/H2/H3 headings; falls back to paragraph splitting when a
    section is too long.
    Returns list of (title, content) tuples.
    """
    # Split by headings
    sections: list[tuple[str | None, str]] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        m = re.match(r"^(#{1,3})\s+(.+)$", line)
        if m:
            if current_lines:
                body = "\n".join(current_lines).strip()
                if body:
                    sections.append((current_heading, body))
            current_heading = m.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append((current_heading, body))

    # Sub-split sections that are too long
    result: list[tuple[str | None, str]] = []
    for heading, body in sections:
        if len(body) <= _CHUNK_MAX_CHARS:
            result.append((heading, body))
        else:
            paragraphs = re.split(r"\n{2,}", body)
            chunk_lines: list[str] = []
            for para in paragraphs:
                if sum(len(l) for l in chunk_lines) + len(para) > _CHUNK_TARGET_CHARS and chunk_lines:
                    result.append((heading, "\n\n".join(chunk_lines).strip()))
                    chunk_lines = []
                chunk_lines.append(para)
            if chunk_lines:
                result.append((heading, "\n\n".join(chunk_lines).strip()))

    return [(h, c) for h, c in result if c.strip()]


# ── Heuristic linking ─────────────────────────────────────────────────────────

_IDENTIFIER_RE = re.compile(r"\b([A-Z][a-zA-Z0-9]{2,})\b")


def _extract_identifiers(text: str) -> set[str]:
    """Extract CamelCase identifiers from text — likely class/method names."""
    return set(_IDENTIFIER_RE.findall(text))


def _link_chunks_to_graph(
    chunks: list[tuple[str | None, str]],
    graph_class_names: set[str],
    graph_method_names: set[str],
) -> list[str]:
    """
    For each chunk, return a comma-separated string of matched class names.
    Matches CamelCase tokens in the chunk against known graph class names.
    """
    linked: list[str] = []
    for heading, body in chunks:
        text = (heading or "") + " " + body
        identifiers = _extract_identifiers(text)
        matches = sorted(identifiers & graph_class_names)
        # Also match method names (less common in specs, but useful)
        method_matches = sorted(identifiers & graph_method_names)
        all_matches = list(dict.fromkeys(matches + method_matches))  # dedupe, preserve order
        linked.append(",".join(all_matches))
    return linked


# ── MarkItDown conversion ─────────────────────────────────────────────────────

def _to_markdown(source: str) -> tuple[str, str]:
    """
    Convert a local file path to markdown text. URL indexing is intentionally
    disabled — all processing is offline, no data leaves the machine.
    Returns (markdown_text, source_type).
    """
    if source.startswith(("http://", "https://")):
        raise ValueError(
            "URL indexing is disabled. Download the document locally first "
            "and pass the file path to keep all processing offline."
        )

    source_lower = source.lower()

    # Plain markdown/text — read directly, no external dependency needed
    if source_lower.endswith((".md", ".mdx", ".txt")):
        return Path(source).read_text(encoding="utf-8", errors="replace"), "markdown"

    try:
        from markitdown import MarkItDown
        # No llm_client passed — image conversion stays local (exiftool only)
        md = MarkItDown()
        result = md.convert(source)
        text = result.text_content or ""
        if source_lower.endswith(".pdf"):
            return text, "pdf"
        if source_lower.endswith((".docx", ".doc")):
            return text, "docx"
        if source_lower.endswith((".pptx", ".ppt")):
            return text, "pptx"
        return text, "file"
    except ImportError:
        raise RuntimeError(
            "markitdown is required for doc indexing. Install it with: pip install markitdown"
        )


# ── Public API ────────────────────────────────────────────────────────────────

def index_document(
    conn: sqlite3.Connection,
    source: str,
    graph_class_names: set[str] | None = None,
    graph_method_names: set[str] | None = None,
    on_progress=None,
) -> int:
    """
    Index a single document (file path or URL) into doc_chunks.
    Returns number of chunks written.
    graph_class_names / graph_method_names: known identifiers for heuristic linking.
    on_progress(current, total): optional progress callback.
    """
    from . import doc_store

    if on_progress:
        on_progress(0, 1)

    markdown, source_type = _to_markdown(source)
    chunks = _split_markdown(markdown)

    if not chunks:
        return 0

    class_names = graph_class_names or set()
    method_names = graph_method_names or set()
    linked = _link_chunks_to_graph(chunks, class_names, method_names)

    # Infer doc title from first heading or filename
    title: str | None = None
    for heading, _ in chunks:
        if heading:
            title = heading
            break
    if not title:
        title = Path(source).stem if not source.startswith("http") else source

    ts = int(time.time() * 1000)
    records = []
    for i, ((heading, body), linked_classes) in enumerate(zip(chunks, linked)):
        chunk_title = heading or title
        records.append({
            "id": _stable_id(source, i),
            "source_path": source,
            "source_type": source_type,
            "title": chunk_title,
            "content": body,
            "linked_classes": linked_classes,
            "chunk_index": i,
            "ts": ts,
        })
        if on_progress:
            on_progress(i + 1, len(chunks))

    # Remove old chunks for this source, then insert fresh
    doc_store.delete_source(conn, source)
    doc_store.upsert_chunks(conn, records)
    doc_store.upsert_source(conn, source, source_type, title, len(records))

    return len(records)


def index_directory(
    conn: sqlite3.Connection,
    directory: str,
    extensions: tuple[str, ...] = (".md", ".mdx", ".txt", ".pdf"),
    graph_class_names: set[str] | None = None,
    graph_method_names: set[str] | None = None,
    on_progress=None,
) -> dict[str, int]:
    """
    Recursively index all matching files in a directory.
    Returns {source_path: chunk_count}.
    """
    root = Path(directory)
    files = [f for f in root.rglob("*") if f.suffix.lower() in extensions and f.is_file()]
    results: dict[str, int] = {}
    for i, f in enumerate(files):
        if on_progress:
            on_progress(i, len(files), str(f))
        try:
            n = index_document(conn, str(f), graph_class_names, graph_method_names)
            results[str(f)] = n
        except Exception as e:
            results[str(f)] = -1  # -1 = failed
    return results


def extract_graph_names(graph) -> tuple[set[str], set[str]]:
    """Extract class and method name sets from a graph object for heuristic linking."""
    class_names: set[str] = set()
    method_names: set[str] = set()
    for cls in graph.classes:
        # Use short name (last segment) and full name
        full = getattr(cls, "full_name", "") or ""
        short = full.split(".")[-1] if "." in full else full
        if short:
            class_names.add(short)
        if full:
            class_names.add(full)
    for m in graph.methods:
        name = getattr(m, "method_name", "") or ""
        if name and len(name) > 2:
            method_names.add(name)
    return class_names, method_names
