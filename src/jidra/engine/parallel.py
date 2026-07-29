"""Shared process-pool helper for parallelizing per-file AST extraction.

Tree-sitter/AST parsing + walking is CPU-bound pure-Python/C-extension work, so
threads don't help (the GIL stays held for the Python-side tree walking). A
process pool gives real wall-clock speedup on multi-core machines; this module
centralizes the pool-vs-sequential decision so each language extractor doesn't
duplicate the threshold/worker-count logic.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

# Below this many items, pool startup (process fork + pickling) costs more
# than it saves.
MIN_ITEMS_FOR_POOL = 8


def worker_count() -> int:
    override = os.getenv("JIDRA_WORKERS")
    if override:
        try:
            n = int(override)
            if n > 0:
                return n
        except ValueError:
            pass
    return os.cpu_count() or 4


def parallel_map(
    fn: Callable[[T], R],
    items: Sequence[T],
    *,
    workers: int | None = None,
    min_items_for_pool: int = MIN_ITEMS_FOR_POOL,
    on_error: Callable[[T, Exception], None] | None = None,
) -> Iterable[R]:
    """Apply `fn` to each item, in process-pool parallel when it's worth it.

    `fn` must be a module-level function (picklable) and `items`/its return
    value must be picklable — this is the same constraint ProcessPoolExecutor
    always has. Falls back to a plain sequential map for small inputs or when
    `workers` resolves to 1, so callers don't need their own branch.

    `on_error`, if provided, is called with `(item, exc)` for every item that
    raises during `fn(item)`, in addition to the usual warning log. This lets
    callers track which items failed (e.g. to avoid treating a transient parse
    failure as "this file has no content anymore").
    """
    if not items:
        return []

    n_workers = workers if workers is not None else worker_count()
    if n_workers <= 1 or len(items) < min_items_for_pool:
        results: list[R] = []
        for item in items:
            try:
                results.append(fn(item))
            except Exception as exc:
                logger.warning("parallel_map: skipping item %r after error: %s", item, exc)
                if on_error is not None:
                    on_error(item, exc)
        return results

    results = []
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(fn, item): item for item in items}
        for fut, item in futures.items():
            try:
                results.append(fut.result())
            except Exception as exc:
                logger.warning("parallel_map: skipping item %r after error: %s", item, exc)
                if on_error is not None:
                    on_error(item, exc)
    return results
