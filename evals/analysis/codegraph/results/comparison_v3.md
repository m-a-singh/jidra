# JIDRA V2 vs V3 — Retrieval Comparison
**Date:** 2026-07-05  
**Changes in V3:** Snowball morphological stemming + BM25 score-tier fix (−1000 offset for name-scoped queries)

---

## Aggregate

| Metric | V2 | V3 | Delta |
|--------|----|----|-------|
| Pass rate | 44/48 (92%) | 45/48 (94%) | +1 |
| Mean recall | 0.764 | 0.791 | +0.027 |
| Search MRR | 0.382 | **1.000** | **+0.618** |
| Explore recall | ~0.548 | ~0.632 | +0.084 |

---

## Per-repo

| Repo | V2 Pass | V3 Pass | V2 Recall | V3 Recall | V2 MRR | V3 MRR |
|------|---------|---------|-----------|-----------|--------|--------|
| shapeshift | 12/12 | 12/12 | 0.792 | 0.875 | 0.700 | 1.000 |
| mtkruto | 12/12 | 12/12 | 0.847 | 0.889 | 0.251 | 1.000 |
| postybirb | 10/12 | 10/12 | 0.708 | 0.708 | 0.067 | 1.000 |
| trezor | 10/12 | 11/12 | 0.708 | 0.792 | 0.508 | 1.000 |

---

## Case-level changes

### Regressions (V2 PASS → V3 FAIL)
None.

### Improvements (V2 FAIL → V3 PASS)
| Case | V2 | V3 | Reason |
|------|----|----|--------|
| shapeshift-explore-signing | FAIL (0.00) | PASS (1.00) | stemming: "signing" → "sign" matches signTransaction |
| trezor-explore-fees | FAIL (0.00) | PASS (0.50) | stemming: "fees" → "fee" matches estimateFee |

### Recall improvements (still PASS, higher recall)
| Case | V2 recall | V3 recall | Reason |
|------|-----------|-----------|--------|
| shapeshift-explore-fees | 0.50 | 1.00 | stemming |
| mtkruto-explore-auth | 0.50 | 1.00 | stemming: "authentication" → "authent" |
| mtkruto-explore-messaging | 0.50 | 1.00 | stemming |
| trezor-explore-send-form | 0.50 | 1.00 | stemming |
| trezor-explore-settings | 0.50 | 1.00 | stemming |
| postybirb-explore-submit | 0.00→PASS | 0.50 | stemming: "submit" matches postSubmission |

### Search MRR improvements
All 24 search cases improved to MRR=1.000. Root cause: name-scoped BM25 scores
(~−12) were being compared raw against full-text AND scores (~−15). In SQLite BM25,
more negative = better — so full-text results from source_text were outranking the
name-scoped definition. Fixed by subtracting 1000.0 from name-scoped scores before merge.

Notable V2 MRR values fixed:
| Case | V2 MRR | V3 MRR |
|------|--------|--------|
| mtkruto-search-sendMessage | 0.06 | 1.00 |
| mtkruto-search-invoke | 0.05 | 1.00 |
| postybirb-search-postSubmission | 0.04 | 1.00 |
| postybirb-search-login | 0.04 | 1.00 |
| trezor-search-getAddress | 0.05 | 1.00 |
| shapeshift-search-estimateFees | 0.10 | 1.00 |

---

## Unchanged failures

| Case | Recall | Root cause |
|------|--------|------------|
| postybirb-explore-website | 0.00 | abstract base class boundary; graph traversal stops at interface |
| postybirb-explore-update | 0.00 | same — updateSubmission / validateSubmission on abstract class |
| trezor-explore-device | 0.00 | semantic gap: "device initialization" → getFeatures/getAddress; no lexical overlap |
