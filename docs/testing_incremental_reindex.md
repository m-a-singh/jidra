# Testing Incremental Re-indexing

## Part 1: Testing on JIDRA Project Itself

### Prerequisites
```bash
cd /Users/akhil.singh/Workflows/Personal/chicha_v4/scripts/jidra
python -m jidra index --codebase . --output .jidra_test
```

This creates `.jidra_test/graph.jsonl` and `.jidra_test/file_manifest.json` for the JIDRA project itself.

---

## Test Suite A: Fingerprinting & Manifest

### Test A1: Manifest Creation
**Command:**
```bash
python -c "
from pathlib import Path
from jidra.reindexer import load_manifest

manifest = load_manifest(Path('.jidra_test'))
print('✓ Manifest loaded')
print(f'  Schema: {manifest.get(\"schema\")}')
print(f'  Entries: {len(manifest.get(\"entries\", {}))}')
print(f'  Last indexed: {manifest.get(\"last_indexed_at_ns\")}')
"
```

**Expected:** Manifest exists, has >10 entries (JIDRA has many .py files), `last_indexed_at_ns` is recent.

---

### Test A2: Fingerprint Diff After No Changes
**Command:**
```bash
python -c "
from pathlib import Path
from jidra.reindexer import compute_fingerprints, load_manifest, diff_fingerprints

manifest = load_manifest(Path('.jidra_test'))
current = compute_fingerprints(Path('.'))
changed, deleted = diff_fingerprints(current, manifest)

print(f'Changed files: {len(changed)}')
print(f'Deleted files: {len(deleted)}')
if changed:
    print('ERROR: Should have 0 changed files on fresh index')
else:
    print('✓ No changes detected (as expected)')
"
```

**Expected:** `changed: 0, deleted: 0`

---

### Test A3: Fingerprint Detects File Change
**Command:**
```bash
# Edit a Python file (just add a blank line)
echo "" >> jidra/cli.py

python -c "
from pathlib import Path
from jidra.reindexer import compute_fingerprints, load_manifest, diff_fingerprints

manifest = load_manifest(Path('.jidra_test'))
current = compute_fingerprints(Path('.'))
changed, deleted = diff_fingerprints(current, manifest)

print(f'Changed files: {len(changed)}')
if len(changed) > 0:
    print('✓ Change detected in cli.py')
    print(f'  Changed: {[p.split(\"/\")[-1] for p in list(changed)[:3]]}')
else:
    print('ERROR: Should have detected change in cli.py')
"

# Restore the file
git checkout jidra/cli.py
```

**Expected:** `changed: >= 1`, should include `cli.py`

---

### Test A4: Fingerprint Detects Deletion
**Command:**
```bash
# Create a temporary test file, index it, then delete it
touch /tmp/test_temp.py
cp /tmp/test_temp.py jidra/test_temp.py

# Re-index to pick up new file
python -c "
from pathlib import Path
from jidra.reindexer import compute_fingerprints, load_manifest, diff_fingerprints, save_manifest
import time

manifest = load_manifest(Path('.jidra_test'))
current = compute_fingerprints(Path('.'))
changed, deleted = diff_fingerprints(current, manifest)
save_manifest(Path('.jidra_test'), current, int(time.time_ns()))
print(f'Re-indexed, added temp file')
"

# Now delete it
rm jidra/test_temp.py

python -c "
from pathlib import Path
from jidra.reindexer import compute_fingerprints, load_manifest, diff_fingerprints

manifest = load_manifest(Path('.jidra_test'))
current = compute_fingerprints(Path('.'))
changed, deleted = diff_fingerprints(current, manifest)

if len(deleted) > 0:
    print('✓ Deletion detected')
    print(f'  Deleted: {[p.split(\"/\")[-1] for p in list(deleted)[:3]]}')
else:
    print('ERROR: Should have detected deletion')
"
```

**Expected:** `deleted: >= 1`, includes `test_temp.py`

---

## Test Suite B: Incremental Reindex on JIDRA

### Test B1: Full Initial Index (Baseline)
**Command:**
```bash
cd /Users/akhil.singh/Workflows/Personal/chicha_v4/scripts/jidra

time python -m jidra index --codebase . --output .jidra_test_baseline

# Verify output
python -c "
from jidra.graph_io import load_graph_jsonl
from pathlib import Path

graph = load_graph_jsonl(Path('.jidra_test_baseline/graph.jsonl'))
print(f'✓ Full index complete')
print(f'  Classes: {len(graph.classes)}')
print(f'  Methods: {len(graph.methods)}')
print(f'  Edges: {len(graph.resolved_call_edges)}')
"
```

**Expected:** Full index takes ~5-10 seconds on JIDRA itself. Outputs baseline numbers (e.g., 50+ classes, 500+ methods).

---

### Test B2: Incremental Reindex - No Changes
**Command:**
```bash
time python -c "
from pathlib import Path
from jidra.reindexer import incremental_reindex

result = incremental_reindex(
    codebase_root=Path('.'),
    graph_path=Path('.jidra_test_baseline/graph.jsonl'),
)
print('Result:', result)
"
```

**Expected:** 
- `change_type: "no_change"` 
- `elapsed_ms: < 100` 
- Much faster than full index

---

### Test B3: Incremental Reindex - Metadata Only (Line Shift)
**Command:**
```bash
# Add a blank line at top of a Python file
sed -i '1i\\' jidra/cli.py

time python -c "
from pathlib import Path
from jidra.reindexer import incremental_reindex

result = incremental_reindex(
    codebase_root=Path('.'),
    graph_path=Path('.jidra_test_baseline/graph.jsonl'),
    hint_changed_files=['jidra/cli.py'],
)
print('Change type:', result.get('change_type'))
print('Added methods:', result.get('added_methods'))
print('Removed methods:', result.get('removed_methods'))
print('Elapsed:', result.get('elapsed_ms'), 'ms')
"

# Restore file
git checkout jidra/cli.py
```

**Expected:**
- `change_type: "metadata_only"`
- `added_methods: 0, removed_methods: 0`
- `elapsed_ms: < 50` (very fast)
- Existing edges unchanged

---

### Test B4: Incremental Reindex - Callsite Change
**Command:**
```bash
# Edit a method body to add a new method call (simulated)
# For Python, add a new import or call

python << 'EOF'
import sys
sys.path.insert(0, '.')

# Read cli.py
with open('jidra/cli.py', 'r') as f:
    content = f.read()

# Find the main() function and add a new call inside it
# (Simple approach: add after first line of main())
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'def main()' in line:
        # Insert a new call after the def line
        lines.insert(i+2, '    print("test call")')  
        break

with open('jidra/cli.py', 'w') as f:
    f.write('\n'.join(lines))
print("Modified cli.py to add a call")
EOF

time python -c "
from pathlib import Path
from jidra.reindexer import incremental_reindex

result = incremental_reindex(
    codebase_root=Path('.'),
    graph_path=Path('.jidra_test_baseline/graph.jsonl'),
    hint_changed_files=['jidra/cli.py'],
)
print('Change type:', result.get('change_type'))
print('Elapsed:', result.get('elapsed_ms'), 'ms')
"

# Restore file
git checkout jidra/cli.py
```

**Expected:**
- `change_type: "callsite_change"` or `"structural"`
- `elapsed_ms: < 200`
- Method edges updated

---

### Test B5: Incremental Reindex - Structural Change
**Command:**
```bash
# Add a new Python file with a simple function
cat > jidra/test_new_module.py << 'EOF'
def test_function():
    """A test function."""
    return 42

def another_function():
    """Calls test_function."""
    return test_function() + 1
EOF

time python -c "
from pathlib import Path
from jidra.reindexer import incremental_reindex

result = incremental_reindex(
    codebase_root=Path('.'),
    graph_path=Path('.jidra_test_baseline/graph.jsonl'),
)
print('Change type:', result.get('change_type'))
print('Added methods:', result.get('added_methods'))
print('Elapsed:', result.get('elapsed_ms'), 'ms')
"

# Clean up
rm jidra/test_new_module.py
```

**Expected:**
- `change_type: "structural"`
- `added_methods: 2` (both functions)
- `elapsed_ms: < 300`
- New methods appear in subsequent graph queries

---

### Test B6: MCP Tool - jidra_check_staleness
**Command:**
```bash
# Create a stale condition: modify a file but don't reindex

sed -i '1i\\' jidra/cli.py

python << 'EOF'
import sys
sys.path.insert(0, '.')

from pathlib import Path
from jidra.mcp_server import analyze_stack_trace

# Import the check_staleness logic directly (or via MCP simulation)
from jidra.reindexer import check_staleness

result = check_staleness(
    codebase_root=Path('.'),
    graph_path=Path('.jidra_test_baseline/graph.jsonl')
)

print(f"Stale: {result.get('stale')}")
print(f"Changed files: {result.get('changed_files_count')}")
print(f"Deleted files: {result.get('deleted_files_count')}")
if result.get('stale'):
    print("✓ Correctly detected staleness")
else:
    print("ERROR: Should detect staleness")
EOF

# Restore
git checkout jidra/cli.py
```

**Expected:**
- `stale: True`
- `changed_files_count: >= 1`
- Includes `cli.py` in the changed list

---

### Test B7: Graph Correctness After Reindex
**Command:**
```bash
python << 'EOF'
import sys
sys.path.insert(0, '.')

from pathlib import Path
from jidra.graph_io import load_graph_jsonl

# Load baseline graph
baseline = load_graph_jsonl(Path('.jidra_test_baseline/graph.jsonl'))

# Run incremental reindex with no changes
from jidra.reindexer import incremental_reindex
incremental_reindex(
    codebase_root=Path('.'),
    graph_path=Path('.jidra_test_baseline/graph.jsonl'),
)

# Load reindexed graph
reindexed = load_graph_jsonl(Path('.jidra_test_baseline/graph.jsonl'))

print(f"Baseline classes: {len(baseline.classes)}")
print(f"Reindexed classes: {len(reindexed.classes)}")
print(f"Baseline methods: {len(baseline.methods)}")
print(f"Reindexed methods: {len(reindexed.methods)}")
print(f"Baseline edges: {len(baseline.resolved_call_edges)}")
print(f"Reindexed edges: {len(reindexed.resolved_call_edges)}")

if (len(baseline.classes) == len(reindexed.classes) and 
    len(baseline.methods) == len(reindexed.methods) and
    len(baseline.resolved_call_edges) == len(reindexed.resolved_call_edges)):
    print("✓ Graph unchanged after no-op reindex")
else:
    print("ERROR: Graph changed unexpectedly")
EOF
```

**Expected:** Counts match exactly after no-change reindex.

---

## Part 2: Testing on Java Repo (Using Claude CLI)

### Setup: Create a Test Java Repo

```bash
# Create a minimal Spring Boot test project
mkdir -p /tmp/test-java-repo/src/main/java/com/example/{service,controller,dao}
cd /tmp/test-java-repo

# Create pom.xml
cat > pom.xml << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0" 
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.example</groupId>
    <artifactId>test-api</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>
    
    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.1.0</version>
    </parent>
    
    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
        </dependency>
    </dependencies>
</project>
EOF

# Create test service classes
cat > src/main/java/com/example/service/UserService.java << 'EOF'
package com.example.service;

import org.springframework.stereotype.Service;

@Service
public class UserService {
    
    public String getUser(String id) {
        return "User: " + id;
    }
    
    public void saveUser(String id, String name) {
        System.out.println("Saving user: " + name);
    }
}
EOF

cat > src/main/java/com/example/controller/UserController.java << 'EOF'
package com.example.controller;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;
import com.example.service.UserService;

@RestController
@RequestMapping("/users")
public class UserController {
    
    @Autowired
    private UserService userService;
    
    @GetMapping("/{id}")
    public String getUser(@PathVariable String id) {
        return userService.getUser(id);
    }
    
    @PostMapping
    public void createUser(@RequestParam String id, @RequestParam String name) {
        userService.saveUser(id, name);
    }
}
EOF

cat > src/main/java/com/example/dao/UserDao.java << 'EOF'
package com.example.dao;

import org.springframework.stereotype.Repository;

@Repository
public class UserDao {
    
    public void save(String data) {
        System.out.println("Saving to DB: " + data);
    }
}
EOF

mkdir -p src/test/java/com/example
cat > src/test/java/com/example/UserServiceTest.java << 'EOF'
package com.example;

import org.junit.jupiter.api.Test;
import com.example.service.UserService;

public class UserServiceTest {
    
    @Test
    public void testGetUser() {
        UserService service = new UserService();
        String result = service.getUser("123");
        assert result.contains("123");
    }
}
EOF
```

---

### Test J1: Index Java Test Repo (Claude CLI)
**Command:**
```bash
cd /tmp/test-java-repo
jidra index --codebase . --output .jidra

# Verify
python << 'EOF'
import sys
sys.path.insert(0, '/Users/akhil.singh/Workflows/Personal/chicha_v4/scripts/jidra')

from pathlib import Path
from jidra.graph_io import load_graph_jsonl

graph = load_graph_jsonl(Path('.jidra/graph.jsonl'))
print(f"✓ Java project indexed")
print(f"  Classes: {len(graph.classes)}")
print(f"  Methods: {len(graph.methods)}")
print(f"  Edges: {len(graph.resolved_call_edges)}")
print(f"  Main/Test split: {len([c for c in graph.classes if 'test' in c.file_path.lower()])} test")
EOF
```

**Expected:** 5 classes (3 main + 1 test + maybe helpers), 10+ methods, several resolved edges.

---

### Test J2: Incremental Reindex - Edit Main Service
**Command:**
```bash
cd /tmp/test-java-repo

# Add a new method to UserService
cat >> src/main/java/com/example/service/UserService.java << 'EOF'
    
    public String getUserEmail(String id) {
        return "user_" + id + "@example.com";
    }
EOF

python << 'EOF'
import sys
sys.path.insert(0, '/Users/akhil.singh/Workflows/Personal/chicha_v4/scripts/jidra')

from pathlib import Path
from jidra.reindexer import incremental_reindex

result = incremental_reindex(
    codebase_root=Path('.'),
    graph_path=Path('.jidra/graph.jsonl'),
    hint_changed_files=['src/main/java/com/example/service/UserService.java'],
)

print(f"Change type: {result.get('change_type')}")
print(f"Added methods: {result.get('added_methods')}")
print(f"Elapsed: {result.get('elapsed_ms')} ms")

if result.get('added_methods') >= 1:
    print("✓ New method detected")
else:
    print("ERROR: Should have added 1 method")
EOF

# Restore
git checkout src/main/java/com/example/service/UserService.java 2>/dev/null || true
```

**Expected:** `added_methods: 1`, `elapsed_ms < 150`

---

### Test J3: Actuator Cache (if applicable)
**Command:**
```bash
cd /tmp/test-java-repo

# Run full process to cache actuator (if Docker available)
# If not, just verify static bean detection works

python << 'EOF'
import sys
sys.path.insert(0, '/Users/akhil.singh/Workflows/Personal/chicha_v4/scripts/jidra')

from pathlib import Path
from jidra.graph_io import load_graph_jsonl
from jidra.graph_validator import detect_beans_from_graph

graph = load_graph_jsonl(Path('.jidra/graph.jsonl'))
beans = detect_beans_from_graph(graph)

print(f"Detected beans: {beans}")
if 'com.example.service.UserService' in beans:
    print("✓ @Service detected")
if 'com.example.controller.UserController' in beans:
    print("✓ @RestController detected")
if 'com.example.dao.UserDao' in beans:
    print("✓ @Repository detected")
EOF
```

**Expected:** All 3 classes detected as beans via annotations.

---

### Test J4: MCP Tool Staleness for Java Repo
**Command:**
```bash
cd /tmp/test-java-repo

# Make a stale change
echo "" >> src/main/java/com/example/service/UserService.java

python << 'EOF'
import sys
sys.path.insert(0, '/Users/akhil.singh/Workflows/Personal/chicha_v4/scripts/jidra')

from pathlib import Path
from jidra.reindexer import check_staleness

result = check_staleness(
    codebase_root=Path('.'),
    graph_path=Path('.jidra/graph.jsonl')
)

print(f"Stale: {result.get('stale')}")
print(f"Changed files: {result.get('changed_files_count')}")
if result.get('stale'):
    print("✓ Staleness detected for Java repo")
EOF

# Restore
git checkout src/main/java/com/example/service/UserService.java 2>/dev/null || true
```

**Expected:** `stale: True`, detects `UserService.java` change.

---

## Test Suite C: Edge Cases & Robustness

### Test C1: Large File Change
**On JIDRA project:**
```bash
# Add 100 lines to a file
python << 'EOF'
with open('jidra/cli.py', 'a') as f:
    for i in range(100):
        f.write(f"# Comment line {i}\n")
EOF

time python -c "
from pathlib import Path
from jidra.reindexer import incremental_reindex
result = incremental_reindex(Path('.'), Path('.jidra_test_baseline/graph.jsonl'))
print('Elapsed:', result.get('elapsed_ms'), 'ms (large file change)')
"

git checkout jidra/cli.py
```

**Expected:** `elapsed_ms < 200` (still fast despite large file)

---

### Test C2: Multiple Files Changed
**Command:**
```bash
# Simulate multiple file edits
python << 'EOF'
import sys
sys.path.insert(0, '.')
from pathlib import Path

files_to_touch = [
    'jidra/cli.py',
    'jidra/extractor.py',
    'jidra/mcp_server.py',
]

for f in files_to_touch:
    with open(f, 'a') as file:
        file.write("\n# temp edit\n")

from jidra.reindexer import incremental_reindex
import time
start = time.time()
result = incremental_reindex(Path('.'), Path('.jidra_test_baseline/graph.jsonl'))
elapsed = (time.time() - start) * 1000

print(f"Changed files: {result.get('changed_files_count', 'N/A')}")
print(f"Elapsed: {elapsed:.0f} ms")

# Restore
import subprocess
for f in files_to_touch:
    subprocess.run(['git', 'checkout', f])
EOF
```

**Expected:** Handles multiple files, still completes in < 500ms

---

### Test C3: Manifest Corruption
**Command:**
```bash
# Corrupt the manifest
echo "invalid json {{{" > .jidra_test_baseline/file_manifest.json

python -c "
from pathlib import Path
from jidra.reindexer import load_manifest

manifest = load_manifest(Path('.jidra_test_baseline'))
print(f'Manifest: {manifest}')
if manifest == {}:
    print('✓ Gracefully handles corrupted manifest (returns empty)')
"

# Restore by re-indexing
python -m jidra index --codebase . --output .jidra_test_baseline
```

**Expected:** Returns empty dict gracefully, falls back to full rebuild.

---

## Summary Checklist

- [ ] A1: Manifest creation
- [ ] A2: No changes detected
- [ ] A3: File change detected
- [ ] A4: File deletion detected
- [ ] B1: Full index baseline (JIDRA)
- [ ] B2: Incremental no-op reindex (< 100ms)
- [ ] B3: Line-shift metadata only (< 50ms)
- [ ] B4: Callsite change reindex
- [ ] B5: Structural change detection
- [ ] B6: Staleness detection tool
- [ ] B7: Graph correctness unchanged
- [ ] J1: Java repo indexing
- [ ] J2: Java incremental reindex
- [ ] J3: Bean detection from annotations
- [ ] J4: Java staleness detection
- [ ] C1: Large file performance
- [ ] C2: Multiple files performance
- [ ] C3: Corruption resilience

Run all tests and report results. If any fail, debug using git diff / git log on modified files to verify implementation.
