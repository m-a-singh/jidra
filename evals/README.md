# Evals

## Scripts

### `run_eval.sh` — Full 10-model eval runner

Runs all 10 embedding models in sequence: pre-check → embed-index → eval → clear embeddings → repeat.

```bash
./evals/run_eval.sh <repo_root> <jidra_repo_arg> <cases_file>
```

**Arguments:**
| Argument | Description |
|---|---|
| `<repo_root>` | Absolute path to the indexed repo (e.g. `/path/to/scikit-learn`) |
| `<jidra_repo_arg>` | Repo slug for output naming (e.g. `scikit-learn/scikit-learn`) |
| `<cases_file>` | Path to test cases YAML (e.g. `evals/dataset/ts_python_test_cases.yaml`) |

**Environment variables:**
| Var | Default | Description |
|---|---|---|
| `JIDRA_BIN` | `.venv/bin/jidra` (hardcoded) | Override path to jidra binary |

**Prerequisites:** Repo must be indexed via `jidra ui` or `jidra init`. Run `pre_eval_check.py` first.

**Example:**
```bash
./evals/run_eval.sh \
  /path/to/jidra_analysis_repos/scikit-learn \
  scikit-learn/scikit-learn \
  evals/dataset/ts_python_test_cases.yaml
```

**Output:** Per-model JSON + markdown in `evals/dataset/results/` and `docs/evals/automatedEvals/`.

**Models tested (fixed list):**
- `nomic-ai/nomic-embed-text-v2-moe`
- `sentence-transformers/all-mpnet-base-v2`
- `sentence-transformers/multi-qa-MiniLM-L6-cos-v1` ← **default/winner**
- `sentence-transformers/multi-qa-distilbert-cos-v1`
- `huggingface/CodeBERTa-small-v1`
- `microsoft/graphcodebert-base`
- `sentence-transformers/all-DistilROBERTa-v1`
- `sentence-transformers/multi-qa-distilbert-dot-v1`
- `microsoft/unixcoder-base`
- `BAAI/bge-small-en-v1.5`

---

### `pre_eval_check.py` — Pre-eval sanity checker

Run before any eval to verify the DB is healthy.

```bash
python3 evals/pre_eval_check.py <path/to/.jidra/graph.db>
```

Exit 0 = pass. Exit 1 = fail (do not run eval).

**Checks:** DB exists, no orphaned WAL/SHM files, methods > 0, FTS5 integrity, embeddings empty, variant breakdown.

**Example:**
```bash
python3 evals/pre_eval_check.py /path/to/scikit-learn/.jidra/graph.db
```

---

### `dataset/compare_four.py` — 4-way comparison (JIDRA search/explore vs CodeGraph search/explore)

**Mode A — full pipeline (runs all 4 evals from DBs):**
```bash
PYTHONPATH=src python3 evals/dataset/compare_four.py \
    --jidra-db  /path/to/.jidra/graph.db \
    --cg-db     /path/to/.codegraph/codegraph.db \
    --cases     evals/dataset/ts_python_test_cases.yaml \
    --repo      django/django \
    --work-dir  evals/dataset/results \
    --report    docs/evals/django_four_way.md
```

**Mode B — from pre-computed JSONs:**
```bash
PYTHONPATH=src python3 evals/dataset/compare_four.py \
    --jidra-search  evals/dataset/results/results_jidra_search.json \
    --jidra-explore evals/dataset/results/results_jidra_explore.json \
    --cg-search     evals/dataset/results/results_cg_search.json \
    --cg-explore    evals/dataset/results/results_cg_explore.json \
    --report        docs/evals/django_four_way.md
```

**All flags:**
| Flag | Type | Default | Description |
|---|---|---|---|
| `--jidra-db` | PATH | — | JIDRA graph.db (Mode A) |
| `--cg-db` | PATH | — | CodeGraph db (Mode A) |
| `--cases` | YAML+ | — | Test case file(s) (Mode A) |
| `--repo` | slug | — | Repo slug for report title |
| `--work-dir` | DIR | `evals/dataset/results` | Intermediate JSON output dir |
| `--out` | JSON | — | Write summary JSON |
| `--report` | MD | — | Write markdown report |
| `--jidra-search` | JSON | — | Pre-computed JIDRA search results (Mode B) |
| `--jidra-explore` | JSON | — | Pre-computed JIDRA explore results (Mode B) |
| `--cg-search` | JSON | — | Pre-computed CG search results (Mode B) |
| `--cg-explore` | JSON | — | Pre-computed CG explore results (Mode B) |

---

### `dataset/swebench_retrieval_eval.py` — JIDRA retrieval eval

```bash
PYTHONPATH=src python3 evals/dataset/swebench_retrieval_eval.py \
    --cases evals/dataset/ts_python_test_cases.yaml \
    --db    /path/to/.jidra/graph.db \
    --repo  django/django \
    --out   evals/dataset/results/results_jidra_search.json
```

**All flags:**
| Flag | Type | Default | Description |
|---|---|---|---|
| `--cases` | YAML+ | required | Test case file(s) |
| `--db` | PATH | required | JIDRA graph.db |
| `--repo` | slug | — | Filter to one repo |
| `--limit` | INT | — | Max cases to run |
| `--out` | JSON | — | Results output file |
| `--explore-only` | flag | false | Skip search, run explore only |

---

### `dataset/codegraph_retrieval_eval.py` — CodeGraph retrieval eval

```bash
python3 evals/dataset/codegraph_retrieval_eval.py \
    --cases evals/dataset/ts_python_test_cases.yaml \
    --db    /path/to/.codegraph/codegraph.db \
    --repo  django/django \
    --out   evals/dataset/results/results_cg_search.json
```

**All flags:**
| Flag | Type | Default | Description |
|---|---|---|---|
| `--cases` | YAML+ | required | Test case file(s) |
| `--db` | PATH | required | CodeGraph db |
| `--repo` | slug | — | Filter to one repo |
| `--limit` | INT | — | Max cases to run |
| `--out` | JSON | — | Results output file |
| `--search-only` | flag | false | Skip explore |
| `--explore-only` | flag | false | Skip search |

---

### `dataset/compare_vs_baseline.py` — Compare result to baseline

```bash
python3 evals/dataset/compare_vs_baseline.py \
    --result  evals/dataset/results/compare_four_django_vN.json \
    --baseline evals/dataset/results/compare_four_sympy_v2.json
```

**Flags:**
| Flag | Type | Default | Description |
|---|---|---|---|
| `--result` | JSON | required | New result JSON |
| `--baseline` | JSON | `results/compare_four_sympy_v2.json` | Baseline to compare against |

---

## Typical workflow

```bash
# 1. Index repo (one time)
jidra init /path/to/repo

# 2. Pre-eval check
python3 evals/pre_eval_check.py /path/to/repo/.jidra/graph.db

# 3. Run all 10 models
./evals/run_eval.sh /path/to/repo org/repo evals/dataset/ts_python_test_cases.yaml

# 4. Compare a single result vs baseline
python3 evals/dataset/compare_vs_baseline.py \
    --result evals/dataset/results/compare_four_repo_modelname.json
```

## Test cases

| File | Language | Cases |
|---|---|---|
| `evals/dataset/ts_python_test_cases.yaml` | Python | SWE-bench Python repos |
