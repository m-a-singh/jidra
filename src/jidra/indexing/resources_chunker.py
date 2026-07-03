from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

_CHUNK_MAX_CHARS = 3000

_SKIP_PATTERNS = ("entity-match", "ms-list", "logback", "bigtable-schema")


def _truncate(text: str) -> str:
    if len(text) <= _CHUNK_MAX_CHARS:
        return text
    return text[:_CHUNK_MAX_CHARS] + "\n...(truncated)"


def chunk_yaml(path: Path) -> list[tuple[str | None, str]]:
    try:
        import yaml
    except ImportError:
        return []
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return [(_stem(path), _truncate(raw))]
    if not isinstance(data, dict):
        return [(_stem(path), _truncate(raw))]
    chunks = []
    for key, value in data.items():
        body = yaml.dump({key: value}, default_flow_style=False, allow_unicode=True)
        chunks.append((str(key), _truncate(body)))
    return chunks


def chunk_json(path: Path) -> list[tuple[str | None, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [(_stem(path), _truncate(raw))]
    if isinstance(data, dict):
        chunks = []
        for key, value in data.items():
            body = json.dumps({key: value}, indent=2)
            chunks.append((str(key), _truncate(body)))
        return chunks
    if isinstance(data, list):
        chunks = []
        for i, item in enumerate(data):
            body = json.dumps(item, indent=2)
            chunks.append((f"item_{i}", _truncate(body)))
        return chunks
    return [(_stem(path), _truncate(raw))]


def chunk_xml(path: Path) -> list[tuple[str | None, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return [(_stem(path), _truncate(raw))]
    chunks: list[tuple[str | None, str]] = []
    for child in root:
        tag = child.tag.rsplit("}", 1)[-1] if "}" in child.tag else child.tag
        body = ET.tostring(child, encoding="unicode")
        chunks.append((tag, _truncate(body)))
    if not chunks:
        chunks = [(_stem(path), _truncate(raw))]
    return chunks


def chunk_resource_file(path: Path) -> tuple[list[tuple[str | None, str]], str]:
    suffix = path.suffix.lower()
    for pat in _SKIP_PATTERNS:
        if pat in str(path).lower():
            raise ValueError(f"skip pattern matched: {pat}")
    if suffix in (".yml", ".yaml"):
        return chunk_yaml(path), "yaml"
    if suffix == ".json":
        return chunk_json(path), "json"
    if suffix == ".xml":
        return chunk_xml(path), "xml"
    raise ValueError(f"unsupported suffix: {suffix}")


def _stem(path: Path) -> str:
    return path.stem
