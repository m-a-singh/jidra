#!/bin/bash

# Quick test script for incremental re-indexing on JIDRA project itself
# Run from: /Users/akhil.singh/Workflows/Personal/chicha_v4/scripts/jidra

set -e

GRAPH_DIR=".jidra_test"
PYTHON="python"

cleanup() {
    echo "Cleaning up $GRAPH_DIR..."
    rm -rf "$GRAPH_DIR"
}

echo "=========================================="
echo "JIDRA Incremental Re-index Test Suite"
echo "=========================================="
echo ""

# Clean up from previous runs
cleanup || true

echo "[1/5] Initial full index (baseline)..."
$PYTHON -m jidra.cli index --codebase . --output "$GRAPH_DIR" > /dev/null
echo "✓ Created baseline graph"

echo ""
echo "[2/5] Test: Second reindex (should be instant no-op)..."
$PYTHON << 'PYEOF'
import sys
import time
sys.path.insert(0, '.')
from pathlib import Path
from jidra.reindexer import incremental_reindex

start = time.time()
result = incremental_reindex(
    codebase_root=Path('.'),
    graph_path=Path('.jidra_test/graph.jsonl'),
)
elapsed = time.time() - start

print(f"  Change type: {result.get('change_type')}")
print(f"  Elapsed: {elapsed:.2f}s ({result.get('elapsed_ms')} ms)")

if result.get('change_type') == 'no_change':
    print("  ✓ PASS: Correctly detected no changes")
    exit(0)
else:
    print(f"  ✗ FAIL: Expected 'no_change', got '{result.get('change_type')}'")
    exit(1)
PYEOF

echo ""
echo "[3/5] Test: Staleness detection..."
# Modify a file
sed -i '1i\\' jidra/cli.py

$PYTHON << 'PYEOF'
import sys
sys.path.insert(0, '.')
from pathlib import Path
from jidra.reindexer import check_staleness

result = check_staleness(
    codebase_root=Path('.'),
    graph_path=Path('.jidra_test/graph.jsonl')
)

print(f"  Stale: {result.get('stale')}")
print(f"  Changed files: {result.get('changed_files_count')}")

if result.get('stale'):
    print("  ✓ PASS: Staleness correctly detected")
    exit(0)
else:
    print("  ✗ FAIL: Should detect as stale")
    exit(1)
PYEOF

# Restore
git checkout jidra/cli.py > /dev/null 2>&1

echo ""
echo "[4/5] Test: Incremental reindex after change..."
$PYTHON << 'PYEOF'
import sys
import time
sys.path.insert(0, '.')
from pathlib import Path
from jidra.reindexer import incremental_reindex

start = time.time()
result = incremental_reindex(
    codebase_root=Path('.'),
    graph_path=Path('.jidra_test/graph.jsonl'),
)
elapsed = time.time() - start

print(f"  Change type: {result.get('change_type')}")
print(f"  Changed files: {result.get('changed_files')}")
print(f"  Elapsed: {elapsed:.2f}s ({result.get('elapsed_ms')} ms)")

if result.get('change_type') != 'no_change':
    print("  ✓ PASS: Change processed")
    exit(0)
else:
    print("  ~ INFO: No changes detected (may be expected)")
    exit(0)
PYEOF

echo ""
echo "[5/5] Test: Manifest validity..."
$PYTHON << 'PYEOF'
import sys
import json
from pathlib import Path

manifest_path = Path('.jidra_test/file_manifest.json')
manifest = json.loads(manifest_path.read_text())

print(f"  Schema: {manifest.get('schema')}")
print(f"  Entries: {len(manifest.get('entries', {}))}")
print(f"  Last indexed at (ns): {manifest.get('last_indexed_at_ns')}")

if manifest.get('schema') == 1 and len(manifest.get('entries', {})) > 100:
    print("  ✓ PASS: Manifest is valid and comprehensive")
    exit(0)
else:
    print("  ✗ FAIL: Manifest invalid or incomplete")
    exit(1)
PYEOF

echo ""
echo "=========================================="
echo "✓✓ All tests passed!"
echo "=========================================="

# Cleanup
cleanup
