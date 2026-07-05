"""
Ranking framework for JIDRA explore/search results.

Each signal is a named, weighted feature. RankingConfig is the single source
of truth — pass a custom config to score_hit() to A/B test ranking changes
without touching engine.py.

Current signals
---------------
Phase-1 (FTS query construction, in graph_store.search_methods):
  name_phase_enabled      bool    Scope FTS to method_name column first for identifier queries
  name_phase_col_weight   float   BM25 weight on method_name column vs other columns
  nl_strip_stopwords      bool    Remove stop words from NL queries before FTS
  nl_use_or               bool    Force OR between tokens for NL queries (vs AND)

Phase-2 (re-ranking, in score_hit):
  bm25_weight             float   Multiplier on BM25 score (base signal)
  exact_name_boost        float   +boost when a query token exactly equals method name
  name_substr_boost       float   +boost when a query token is substring of method name
  sig_boost               float   +boost when a query token appears in signature
  path_file_boost         float   +boost when a query token appears in filename (no ext)
  path_dir_boost          float   +boost when a query token appears in parent directory
  stereotype_service_boost float  +boost for controller/service stereotypes
  stereotype_repo_boost   float   +boost for repository stereotype
  test_penalty            float   Penalty for test file paths (negative)
  generated_penalty       float   Penalty for generated file paths (negative)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields


@dataclass
class RankingConfig:
    # --- Phase 1: FTS query construction ---
    name_phase_enabled: bool = True
    name_phase_col_weight: float = 10.0
    nl_strip_stopwords: bool = True
    nl_use_or: bool = True

    # --- Phase 2: re-ranking signals ---
    bm25_weight: float = 1.0
    exact_name_boost: float = 2.0
    name_substr_boost: float = 1.0
    sig_boost: float = 0.5
    path_file_boost: float = 1.5
    path_dir_boost: float = 0.5
    stereotype_service_boost: float = 1.5
    stereotype_repo_boost: float = 0.5
    test_penalty: float = -1.0
    generated_penalty: float = -10.0

    def describe(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


# Singleton used by engine.py — swap this out to change ranking globally
DEFAULT_CONFIG = RankingConfig()

_GENERATED_MARKERS = (
    "/generated/", "/gen/", ".generated.", "_generated.",
    "build/generated", "target/generated",
)

_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def score_hit(row: dict, tokens: set[str], cfg: RankingConfig = DEFAULT_CONFIG) -> float:
    """Compute relevance score for a candidate row given query tokens.

    Higher = better. Designed to be called after BM25 retrieval as a re-ranker.
    Pass a custom RankingConfig to isolate signal impact.
    """
    bm25 = -float(row.get("score") or 0.0)  # BM25 is negative; invert
    score = bm25 * cfg.bm25_weight

    name = (row.get("method_name") or "").lower()
    sig = (row.get("signature") or "").lower()
    path = (row.get("file_path") or "").lower()

    path_dir = path.rsplit("/", 1)[0] if "/" in path else ""
    path_file = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    boost = 0.0
    for tok in tokens:
        if cfg.exact_name_boost and tok == name:
            boost += cfg.exact_name_boost
        elif cfg.name_substr_boost and tok in name:
            boost += cfg.name_substr_boost
        if cfg.sig_boost and tok in sig:
            boost += cfg.sig_boost
        if cfg.path_file_boost and tok in path_file:
            boost += cfg.path_file_boost
        elif cfg.path_dir_boost and tok in path_dir:
            boost += cfg.path_dir_boost

    stereotype = row.get("stereotypes") or ""
    if cfg.stereotype_service_boost and (
        "controller" in stereotype or "service" in stereotype
    ):
        boost += cfg.stereotype_service_boost
    elif cfg.stereotype_repo_boost and "repository" in stereotype:
        boost += cfg.stereotype_repo_boost

    if cfg.test_penalty and (
        "/test/" in path
        or "/tests/" in path
        or path.endswith(("test.java", "tests.java"))
    ):
        boost += cfg.test_penalty

    if cfg.generated_penalty and any(m in path for m in _GENERATED_MARKERS):
        boost += cfg.generated_penalty

    return score + boost
