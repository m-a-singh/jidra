"""Method embedding storage and retrieval for dense reranking.

Phase 0: schema migration + payload builder.
Phase 1: offline indexer (build_method_embeddings).
Phase 2: rerank_by_embedding (called from engine after BM25).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from typing import TYPE_CHECKING

# Suppress HuggingFace noise before any HF import
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("TQDM_DISABLE", "1")
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)

if TYPE_CHECKING:
    import numpy as np

_MAX_SOURCE_CHARS = 2000
_WHITESPACE_RE = re.compile(r"\s+")

# --- Schema ---

_DDL = """
CREATE TABLE IF NOT EXISTS method_embeddings (
    method_id   TEXT NOT NULL,
    variant     TEXT NOT NULL,
    module_id   TEXT,
    model       TEXT NOT NULL,
    embedding   BLOB NOT NULL,
    text_hash   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (method_id, variant, module_id, model)
);
"""


def ensure_embeddings_table(conn: sqlite3.Connection) -> None:
    conn.execute(_DDL)
    conn.commit()


# --- Payload builder ---


def _norm(text: str | None) -> str:
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", text).strip()


def _parse_class_context(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        ctx = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    parts: list[str] = []
    for field in (
        "name",
        "full_name",
        "imports",
        "stereotypes",
        "extends",
        "implements",
    ):
        val = ctx.get(field)
        if not val:
            continue
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val if v)
        if val:
            parts.append(f"{field}: {_norm(str(val))}")
    return "; ".join(parts)


def _parse_annotations(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        anns = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return str(raw)
    if isinstance(anns, list):
        return ", ".join(str(a) for a in anns if a)
    return _norm(str(anns))


def build_payload(row: dict) -> str:
    """Build the embedding text for one methods row."""
    lines: list[str] = []

    if row.get("language"):
        lines.append(f"lang: {row['language']}")
    if row.get("file_path"):
        lines.append(f"path: {row['file_path']}")
    if row.get("class_full_name"):
        lines.append(f"class: {row['class_full_name']}")
    if row.get("method_name"):
        lines.append(f"method: {row['method_name']}")
    if row.get("signature"):
        lines.append(f"signature: {_norm(row['signature'])}")

    fw = _norm(row.get("framework_role") or "")
    if fw:
        lines.append(f"framework_role: {fw}")

    if row.get("is_endpoint"):
        ep_parts = [
            p for p in [row.get("http_method"), row.get("full_route") or row.get("route")] if p
        ]
        if ep_parts:
            lines.append(f"endpoint: {' '.join(ep_parts)}")

    ann = _parse_annotations(row.get("annotations_json"))
    if ann:
        lines.append(f"annotations: {ann}")

    ctx = _parse_class_context(row.get("class_context_json"))
    if ctx:
        lines.append(f"class_context: {ctx}")

    source = _norm(row.get("source") or "")
    if len(source) > _MAX_SOURCE_CHARS:
        source = source[:_MAX_SOURCE_CHARS]
    if source:
        lines.append(f"source:\n{source}")

    return "\n".join(lines)


def payload_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# --- Embedding client ---

_model_cache: dict[str, object] = {}

# Models that require trust_remote_code=True
_TRUST_REMOTE_CODE_MODELS = {
    "nomic-ai/nomic-embed-text-v1",
    "nomic-ai/nomic-embed-text-v1.5",
}


def is_model_cached(model_name: str) -> bool:
    """Return True if model files already exist in the HF cache."""
    try:
        from huggingface_hub import try_to_load_from_cache

        # Any single file present means the model was downloaded
        result = try_to_load_from_cache(model_name, "config.json")
        return result is not None and result != "not_in_cache"
    except Exception:
        return False


def ensure_model_downloaded(model_name: str, on_status=None) -> None:
    """Download model if not cached. on_status(msg) called with progress updates."""
    if is_model_cached(model_name):
        if on_status:
            on_status(f"model cached ({model_name})")
        return

    if on_status:
        on_status(f"downloading {model_name} (~90 MB)...")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise ImportError(
            "sentence-transformers not installed. Run: pip install sentence-transformers"
        ) from e
    trust = model_name in _TRUST_REMOTE_CODE_MODELS
    model = SentenceTransformer(model_name, trust_remote_code=trust)
    _model_cache[model_name] = model
    if on_status:
        on_status("download complete")


def _get_model(model_name: str):
    if model_name in _model_cache:
        return _model_cache[model_name]
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise ImportError(
            "sentence-transformers not installed. Run: pip install sentence-transformers"
        ) from e
    trust = model_name in _TRUST_REMOTE_CODE_MODELS
    model = SentenceTransformer(model_name, trust_remote_code=trust)
    _model_cache[model_name] = model
    return model


# --- Offline indexer ---

DEFAULT_EMBED_MODEL = "sentence-transformers/multi-qa-MiniLM-L6-cos-v1"


def build_method_embeddings(
    conn: sqlite3.Connection,
    *,
    model_name: str = DEFAULT_EMBED_MODEL,
    batch_size: int = 64,
    language: str | None = None,
    force: bool = False,
) -> dict:
    """Embed all non-generated methods and store in method_embeddings.

    Returns stats dict: {total, embedded, skipped, failed, elapsed_s, methods_per_s}.
    """
    import time

    t_start = time.perf_counter()

    ensure_embeddings_table(conn)

    t0 = time.perf_counter()
    model = _get_model(model_name)
    print(f"[embed] model loaded in {time.perf_counter() - t0:.2f}s")

    lang_clause = "AND language = ?" if language else ""
    params: tuple = (language,) if language else ()

    t0 = time.perf_counter()
    rows = conn.execute(
        f"SELECT id, variant, module_id, method_name, signature, file_path, "
        f"source, class_full_name, class_context_json, annotations_json, "
        f"is_endpoint, http_method, route, full_route, language, framework_role "
        f"FROM methods WHERE (generated IS NULL OR generated = 0) {lang_clause}",
        params,
    ).fetchall()
    print(f"[embed] fetched {len(rows)} methods in {time.perf_counter() - t0:.2f}s")

    col_names = [
        "id",
        "variant",
        "module_id",
        "method_name",
        "signature",
        "file_path",
        "source",
        "class_full_name",
        "class_context_json",
        "annotations_json",
        "is_endpoint",
        "http_method",
        "route",
        "full_route",
        "language",
        "framework_role",
    ]

    total = len(rows)
    embedded = skipped = failed = 0

    t0 = time.perf_counter()
    triples = []
    for raw in rows:
        row = dict(zip(col_names, raw, strict=False))
        payload = build_payload(row)
        h = payload_hash(payload)
        triples.append((row, payload, h))
    print(f"[embed] built payloads in {time.perf_counter() - t0:.2f}s")

    existing: dict[tuple, str] = {}
    if not force:
        t0 = time.perf_counter()
        pk_rows = conn.execute(
            "SELECT method_id, variant, module_id, text_hash FROM method_embeddings WHERE model = ?",
            (model_name,),
        ).fetchall()
        existing = {(r[0], r[1], r[2]): r[3] for r in pk_rows}
        print(f"[embed] loaded {len(existing)} existing hashes in {time.perf_counter() - t0:.2f}s")

    to_embed: list[tuple[dict, str, str]] = []
    for row, payload, h in triples:
        pk = (row["id"], row["variant"], row["module_id"])
        if not force and existing.get(pk) == h:
            skipped += 1
            continue
        to_embed.append((row, payload, h))

    print(f"[embed] {len(to_embed)} to embed, {skipped} unchanged (skipping)")

    t_encode_total = 0.0
    t_write_total = 0.0

    for i in range(0, len(to_embed), batch_size):
        batch = to_embed[i : i + batch_size]
        payloads = [t[1] for t in batch]
        try:
            t0 = time.perf_counter()
            vectors = model.encode(payloads, show_progress_bar=False, normalize_embeddings=True)
            t_encode_total += time.perf_counter() - t0
        except Exception as exc:
            print(f"[embed] batch {i // batch_size} encode failed: {exc}")
            failed += len(batch)
            continue

        t0 = time.perf_counter()
        for (row, _, h), vec in zip(batch, vectors, strict=False):
            blob = vec.astype("float32").tobytes()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO method_embeddings "
                    "(method_id, variant, module_id, model, embedding, text_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (row["id"], row["variant"], row["module_id"], model_name, blob, h),
                )
                embedded += 1
            except Exception as exc:
                print(f"[embed] insert failed for {row['id']}: {exc}")
                failed += 1
        conn.commit()
        t_write_total += time.perf_counter() - t0

        done = min(i + batch_size, len(to_embed))
        elapsed = time.perf_counter() - t_start
        rate = embedded / elapsed if elapsed > 0 else 0
        print(
            f"[embed] {done}/{len(to_embed)}  {rate:.0f} methods/s",
            end="\r",
            flush=True,
        )

    if to_embed:
        print()

    elapsed_s = time.perf_counter() - t_start
    rate = embedded / elapsed_s if elapsed_s > 0 else 0
    if to_embed:
        print(
            f"[embed] encode {t_encode_total:.2f}s  write {t_write_total:.2f}s  "
            f"total {elapsed_s:.2f}s  ({rate:.0f} methods/s)"
        )

    return {
        "total": total,
        "embedded": embedded,
        "skipped": skipped,
        "failed": failed,
        "elapsed_s": round(elapsed_s, 3),
        "methods_per_s": round(rate, 1),
    }


# --- In-memory index ---


def load_embedding_index(
    conn: sqlite3.Connection,
    model_name: str,
) -> tuple[np.ndarray, list[str]]:
    """Load all embeddings for model_name into a (N, D) float32 matrix.

    Returns (matrix, method_ids). Matrix rows are L2-normalised so
    dot-product == cosine similarity.
    """
    import numpy as np

    rows = conn.execute(
        "SELECT method_id, embedding FROM method_embeddings WHERE model = ?",
        (model_name,),
    ).fetchall()
    if not rows:
        return np.empty((0, 0), dtype="float32"), []

    ids = [r[0] for r in rows]
    vecs = [np.frombuffer(r[1], dtype="float32") for r in rows]
    matrix = np.stack(vecs)  # (N, D)
    return matrix, ids


# --- Reranker ---


def detect_indexed_model(conn: sqlite3.Connection) -> str | None:
    """Return the model name that has the most rows in method_embeddings, or None."""
    try:
        row = conn.execute(
            "SELECT model, COUNT(*) as n FROM method_embeddings GROUP BY model ORDER BY n DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def rerank_by_embedding(
    conn: sqlite3.Connection,
    query: str,
    candidates: list[dict],
    *,
    model_name: str = "nomic-ai/nomic-embed-text-v1.5",
    top_k: int = 100,
    bm25_weight: float = 0.60,
    embed_weight: float = 0.20,
    heuristic_weight: float = 0.20,
    min_sim: float = 0.0,
) -> list[dict]:
    """Rerank BM25 candidates using cosine similarity against query embedding.

    Falls back to original order if embeddings unavailable.
    bm25_score in each candidate must be the normalized BM25 score [0,1].
    heuristic_score is optional; defaults to 0 if absent.
    """
    import numpy as np

    if not candidates:
        return candidates

    try:
        model = _get_model(model_name)
    except ImportError:
        return candidates

    try:
        ensure_embeddings_table(conn)
    except Exception:
        return candidates

    # Embed query
    try:
        q_vec = model.encode([query], normalize_embeddings=True)[0].astype("float32")
    except Exception:
        return candidates

    # Load stored vectors for candidates.
    # Query by method_id only — tuple IN with NULL module_id never matches in SQLite
    # (NULL = NULL is NULL, not TRUE), which would silently empty vec_map.
    method_ids = [c["id"] for c in candidates[:top_k]]
    placeholders = ",".join("?" for _ in method_ids)
    params_q = [*method_ids, model_name]
    try:
        rows = conn.execute(
            f"SELECT method_id, embedding FROM method_embeddings "
            f"WHERE method_id IN ({placeholders}) AND model = ?",
            params_q,
        ).fetchall()
    except Exception:
        return candidates

    vec_map: dict[str, np.ndarray] = {}
    for r in rows:
        vec_map[r[0]] = np.frombuffer(r[1], dtype="float32")

    if not vec_map:
        return candidates

    scored: list[tuple[float, dict]] = []
    for c in candidates:
        key = c["id"]
        bm25 = float(c.get("bm25_norm") or 0.0)
        heuristic = float(c.get("heuristic_score") or 0.0)
        if key in vec_map:
            sim = float(np.dot(q_vec, vec_map[key]))
            if min_sim > 0.0 and sim < min_sim:
                continue
            blend = bm25_weight * bm25 + embed_weight * sim + heuristic_weight * heuristic
        else:
            # No vector: use bm25 + heuristic only, scaled to preserve relative order
            blend = (bm25_weight + embed_weight) * bm25 + heuristic_weight * heuristic
        scored.append((blend, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored]
