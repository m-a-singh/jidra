#!/usr/bin/env python3
"""Pre-eval sanity check. Run before embed-index + eval on any repo.

Usage:
    python evals/pre_eval_check.py <repo_jidra_db_path>

Example:
    python evals/pre_eval_check.py ~/jidra_analysis_repos/sympy/.jidra/graph.db

Exit codes:
    0 = all checks passed, safe to proceed
    1 = one or more checks failed, do not run eval
"""

import sqlite3
import sys
from pathlib import Path


def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"  OK    {msg}")


def warn(msg: str) -> None:
    print(f"  WARN  {msg}")


def check(db_path: Path) -> bool:
    passed = True

    print(f"\n=== Pre-eval check: {db_path} ===\n")

    # 1. DB file exists
    if not db_path.exists():
        fail(f"graph.db not found: {db_path}")
        return False
    ok(f"graph.db exists ({db_path.stat().st_size // (1024 * 1024)}MB)")

    # 2. Orphaned reindexer WAL — means previous index replace was not clean
    build_wal = db_path.with_name("graph.building.db-wal")
    build_shm = db_path.with_name("graph.building.db-shm")
    build_db = db_path.with_name("graph.building.db")
    orphans = [p for p in (build_wal, build_shm, build_db) if p.exists()]
    if orphans:
        real_orphans = [p for p in orphans if p.stat().st_size > 0]
        empty_orphans = [p for p in orphans if p.stat().st_size == 0]
        for p in empty_orphans:
            warn(f"empty orphaned build file (harmless): {p.name}")
        for p in real_orphans:
            fail(
                f"orphaned reindexer file: {p.name} ({p.stat().st_size // (1024 * 1024)}MB)"
            )
        if real_orphans:
            fail(
                "DB was replaced without WAL checkpoint — FTS5 may be corrupt. Re-index required."
            )
            passed = False
    else:
        ok("no orphaned reindexer WAL/SHM files")

    # 3. Connect
    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
    except Exception as e:
        fail(f"cannot open DB: {e}")
        return False

    # 4. Method count
    try:
        total = conn.execute("SELECT COUNT(*) FROM methods").fetchone()[0]
        if total == 0:
            fail("methods table is empty — re-index required")
            passed = False
        else:
            ok(f"methods: {total}")
    except Exception as e:
        fail(f"methods table query failed: {e}")
        passed = False

    # 5. FTS5 integrity
    for fts in ("methods_fts", "classes_fts"):
        try:
            conn.execute(f"INSERT INTO {fts}({fts}) VALUES('integrity-check')")
            ok(f"{fts} integrity: OK")
        except sqlite3.DatabaseError as e:
            if "locked" in str(e).lower():
                warn(
                    f"{fts} integrity check skipped: DB locked by JIDRA server (not corruption)"
                )
            else:
                fail(f"{fts} integrity FAILED: {e}")
                fail(f"  → re-index required (do not run eval)")
                passed = False
        except Exception as e:
            warn(f"{fts} integrity check error (non-corruption): {e}")

    # 6. Embeddings table should be empty before embed-index
    try:
        emb_count = conn.execute("SELECT COUNT(*) FROM method_embeddings").fetchone()[0]
        if emb_count > 0:
            models = conn.execute(
                "SELECT model, COUNT(*) FROM method_embeddings GROUP BY model"
            ).fetchall()
            warn(
                f"method_embeddings not empty ({emb_count} rows) — leftover from previous run?"
            )
            for model, n in models:
                warn(f"  model={model!r}  rows={n}")
            warn(
                '  Run: sqlite3 graph.db "DELETE FROM method_embeddings" before embed-index'
            )
        else:
            ok("method_embeddings: empty (clean for embed-index)")
    except Exception as e:
        warn(f"method_embeddings check skipped: {e}")

    # 7. Variant breakdown
    try:
        variants = conn.execute(
            "SELECT variant, COUNT(*) FROM methods GROUP BY variant ORDER BY COUNT(*) DESC"
        ).fetchall()
        for v, n in variants:
            ok(f"  variant={v!r}  methods={n}")
    except Exception as e:
        warn(f"variant breakdown skipped: {e}")

    conn.close()

    print()
    if passed:
        print("PASSED — safe to run embed-index + eval")
    else:
        print("FAILED — fix issues above before running eval")
    print()

    return passed


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    db_path = Path(sys.argv[1]).expanduser().resolve()
    ok = check(db_path)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
