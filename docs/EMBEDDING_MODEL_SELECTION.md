
# Embedding Model Selection

## Summary

**Selected model: `sentence-transformers/multi-qa-MiniLM-L6-cos-v1`**

Evaluated 10 embedding models across 3 production-scale repos (sympy, scikit-learn, django) with 305 test cases total. MiniLM won on combined accuracy + speed.

---

## Why Embedding?

JIDRA uses a hybrid BM25 + dense vector search. BM25 (FTS5) handles exact/keyword matches; dense embeddings handle semantic similarity. The embedding model determines the quality of the dense retrieval leg.

Without embeddings, JIDRA falls back to BM25-only. With embeddings, search and explore pass rates improve across all repos tested.

---

## Eval Setup

**Repos tested:**
| Repo | Test cases | Methods indexed |
|---|---|---|
| sympy/sympy | 77 | ~57,000 |
| scikit-learn/scikit-learn | 23 | ~16,000 |
| django/django | 114 | ~52,000 |

**Baseline:** JIDRA without embeddings (BM25-only, each repo's v2 baseline JSON)

**Metrics tracked:**
- JIDRA search pass rate (recall ≥ 0.5 threshold)
- JIDRA explore pass rate
- Mean MRR (mean reciprocal rank)
- Embedding throughput (methods/second)
- Search latency (mean, p50, p95)

**Eval scripts:** `evals/run_eval.sh`, `evals/dataset/compare_four.py`, `evals/dataset/swebench_retrieval_eval.py`

---

## Models Tested

| Model | Size | Notes |
|---|---|---|
| `nomic-ai/nomic-embed-text-v2-moe` | Large MoE | Requires trust_remote_code |
| `sentence-transformers/all-mpnet-base-v2` | Medium | General purpose |
| `sentence-transformers/multi-qa-MiniLM-L6-cos-v1` | Small | Multi-QA trained |
| `sentence-transformers/multi-qa-distilbert-cos-v1` | Medium | Multi-QA trained |
| `huggingface/CodeBERTa-small-v1` | Small | Code-specific |
| `microsoft/graphcodebert-base` | Medium | Code-specific |
| `sentence-transformers/all-DistilROBERTa-v1` | Medium | General purpose |
| `sentence-transformers/multi-qa-distilbert-dot-v1` | Medium | Multi-QA, dot product |
| `microsoft/unixcoder-base` | Medium | Code-specific |
| `BAAI/bge-small-en-v1.5` | Small | General purpose |

---

## Results

### Combined wins gained vs BM25 baseline (all 3 repos)

"Wins gained" = net new cases where JIDRA passes that it didn't before (Jidra-Only wins + Both-Pass gains).

| Model | Sympy | scikit-learn | Django | **Total** | Embed speed |
|---|---|---|---|---|---|
| **multi-qa-MiniLM-L6-cos-v1** | +4 | +2 | +3 | **+9** | 464–670/s |
| all-DistilROBERTa-v1 | +4 | +2 | +3 | **+9** | 163–259/s |
| bge-small-en-v1.5 | +3 | +2 | +3 | **+8** | 262–370/s |
| multi-qa-distilbert-dot-v1 | +3 | +2 | +2 | **+7** | 171–261/s |
| multi-qa-distilbert-cos-v1 | +3 | +2 | +1 | **+6** | 187–246/s |
| all-mpnet-base-v2 | +2 | +2 | +2 | **+6** | 86–112/s |
| nomic-embed-text-v2-moe | +2 | +1 | +1 | **+4** | 67–79/s |
| graphcodebert-base | +1 | +2 | 0 | **+3** | 94–127/s |
| CodeBERTa-small-v1 | +1 | +1 | 0 | **+2** | 190–266/s |
| unixcoder-base | 0 | +1 | 0 | **+1** | 67–78/s |

### Pass rates — sympy (77 cases, BM25 baseline: search 84.4%, explore 74.0%)

| Model | Search | Explore |
|---|---|---|
| multi-qa-MiniLM-L6-cos-v1 | **89.6%** (+5.2%) | **76.6%** (+2.6%) |
| all-DistilROBERTa-v1 | **89.6%** (+5.2%) | 75.3% (+1.3%) |
| multi-qa-distilbert-cos-v1 | 88.3% (+3.9%) | 76.6% (+2.6%) |
| bge-small-en-v1.5 | 88.3% (+3.9%) | 74.0% (=) |
| unixcoder-base | 84.4% (=) | 72.7% (-1.3%) |

### Pass rates — scikit-learn (23 cases, BM25 baseline: search 47.8%, explore 43.5%)

| Model | Search | Explore |
|---|---|---|
| all-mpnet-base-v2 | **56.5%** (+8.7%) | **47.8%** (+4.3%) |
| multi-qa-MiniLM-L6-cos-v1 | **56.5%** (+8.7%) | **47.8%** (+4.3%) |
| multi-qa-distilbert-cos-v1 | **56.5%** (+8.7%) | **47.8%** (+4.3%) |
| all-DistilROBERTa-v1 | **56.5%** (+8.7%) | **47.8%** (+4.3%) |
| nomic-embed-text-v2-moe | 52.2% (+4.3%) | 47.8% (+4.3%) |

### Pass rates — django (114 cases, BM25 baseline: search 91.2%, explore 84.2%)

| Model | Search | Explore |
|---|---|---|
| multi-qa-MiniLM-L6-cos-v1 | **93.9%** (+2.6%) | **87.7%** (+3.5%) |
| all-DistilROBERTa-v1 | **93.9%** (+2.6%) | **87.7%** (+3.5%) |
| bge-small-en-v1.5 | **93.9%** (+2.6%) | **87.7%** (+3.5%) |
| CodeBERTa-small-v1 | 91.2% (=) | 85.9% (+1.7%) |
| unixcoder-base | 91.2% (=) | 85.1% (+0.9%) |

---

## Why MiniLM Won

**MiniLM and DistilROBERTa tied on accuracy (+9 wins total).** Tiebreaker: speed.

| | MiniLM | DistilROBERTa |
|---|---|---|
| Total wins | +9 | +9 |
| Embed speed | **464–670 methods/s** | 163–259 methods/s |
| Speed advantage | **2.6–3x faster** | — |

Speed matters because:
1. Initial embed-index on a large repo (scikit-learn 16K methods) takes 35s with MiniLM vs 99s with DistilROBERTa
2. Re-embed on incremental reindex happens automatically — users feel this latency
3. Smaller model = smaller memory footprint at runtime

Code-specific models (CodeBERTa, GraphCodeBERT, UniXcoder) underperformed general-purpose multi-QA models. JIDRA queries are natural language ("find the method that does X"), not code-to-code retrieval — general-purpose multi-QA training is a better fit.

nomic-embed-text-v2-moe is the slowest (67–79 methods/s) with only +4 combined wins — worst accuracy/speed ratio of all models tested.

---

## Latency Impact

All embedding models add ~250–400ms to search latency (hybrid reranking cost). This is the price of the dense leg.

| Model | Search latency mean | vs BM25 baseline |
|---|---|---|
| MiniLM | ~880–1150ms | +246–295ms |
| DistilROBERTa | ~894–1158ms | +260–338ms |
| nomic | ~949–1204ms | +315–337ms |

MiniLM has the lowest latency overhead of the top-performing models.

---

## What Was Implemented

After selecting MiniLM, the model was made the default throughout JIDRA:

1. **`DEFAULT_EMBED_MODEL`** constant in `src/jidra/indexing/method_embeddings.py`
2. **`jidra init`** auto-runs embed-index with MiniLM after full index (section 2 of 4)
3. **UI pipeline** auto-runs embed-index in the SSE stream after indexing completes
4. **Incremental reindexer** calls `auto_embed_after_reindex()` after every successful reindex
5. **"Rebuild from scratch"** button in UI now correctly passes `force=true`, deleting the existing graph before re-indexing

No manual `jidra embed-index` step required after this change.

---

## Eliminated Models

| Model | Reason |
|---|---|
| `unixcoder-base` | Only model that regressed on sympy explore; +1 total wins; slow |
| `nomic-embed-text-v2-moe` | Slowest (67–79/s); worst accuracy/speed ratio; MoE architecture mismatch for this task |
| `CodeBERTa-small-v1` | +2 wins only; code-to-code training not suited for natural language queries |
| `graphcodebert-base` | Same wins as CodeBERTa; slower |
