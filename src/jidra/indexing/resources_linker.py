from __future__ import annotations

import re

_FQN_RE = re.compile(r"\b([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\.[A-Z][A-Za-z0-9_]*)\b")
_CAMEL_RE = re.compile(r"\b([A-Z][a-zA-Z0-9]{2,})\b")


def extract_fqn_links(
    chunk_title: str | None,
    chunk_body: str,
    graph_class_names: set[str],
) -> list[str]:
    text = (chunk_title or "") + " " + chunk_body
    found = []
    for m in _FQN_RE.finditer(text):
        fqn = m.group(1)
        short = fqn.rsplit(".", 1)[-1]
        if fqn in graph_class_names or short in graph_class_names:
            found.append(short)
    return found


def _camel_links(
    chunk_title: str | None,
    chunk_body: str,
    graph_class_names: set[str],
    graph_method_names: set[str],
) -> list[str]:
    text = (chunk_title or "") + " " + chunk_body
    found = []
    for m in _CAMEL_RE.finditer(text):
        token = m.group(1)
        if token in graph_class_names or token in graph_method_names:
            found.append(token)
    return found


def link_resource_chunks(
    chunks: list[tuple[str | None, str]],
    graph_class_names: set[str],
    graph_method_names: set[str],
    _source_path: str = "",
) -> list[str]:
    results = []
    for title, body in chunks:
        fqn = extract_fqn_links(title, body, graph_class_names)
        camel = _camel_links(title, body, graph_class_names, graph_method_names)
        merged = sorted(set(fqn + camel))
        results.append(",".join(merged))
    return results
