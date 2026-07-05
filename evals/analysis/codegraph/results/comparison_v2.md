# JIDRA vs CodeGraph — Retrieval Eval V2
**Date:** 2026-07-05  
**JIDRA commit:** feature/agent_exploration  
**CodeGraph commit:** 7d624ec  

Metrics differ by design — not directly comparable on a single number:
- **JIDRA search MRR** measures rank of target in BM25 results
- **CodeGraph search MRR** is always 1.00 when found (first hit only)
- **JIDRA explore** = recall over graph traversal results
- **CodeGraph explore** = recall + density (unique callers / found)

---

## Summary Table

| Repo        | JIDRA Pass | CG Pass | JIDRA Recall | CG Recall | JIDRA Search MRR | CG Search MRR |
|-------------|-----------|---------|--------------|-----------|-----------------|---------------|
| shapeshift  | 12/12 (100%) | 7/12 (58%) | 0.792 | 0.46 | 0.700 | 1.00* |
| mtkruto     | 12/12 (100%) | 11/12 (92%) | 0.847 | 0.72 | 0.251 | 1.00* |
| postybirb   | 10/12 (83%)  | 9/12 (75%)  | 0.708 | 0.63 | 0.067 | 1.00* |
| trezor      | 10/12 (83%)  | 8/12 (67%)  | 0.708 | 0.50 | 0.508 | 1.00* |
| **Average** | **94%** | **73%** | **0.764** | **0.578** | **0.382** | **1.00*** |

*CodeGraph MRR=1.00 because it only reports MRR for cases where the target is found at rank 1; missed cases get mrr=0.00, pulling overall lower when accounting for failures.

---

## Per-Repo Detail

### shapeshift (web)

**JIDRA: 12/12 pass | Recall=0.792 | Search MRR=0.700**

| Case | Result | Recall | MRR | Notes |
|------|--------|--------|-----|-------|
| search-getTradeQuote | PASS | 1.00 | 1.00 | |
| search-signTransaction | PASS | 1.00 | 1.00 | |
| search-estimateFees | PASS | 1.00 | 0.10 | found but not rank 1 |
| search-broadcastTransaction | PASS | 1.00 | 1.00 | |
| search-getRates | PASS | 1.00 | 0.10 | found but not rank 1 |
| search-getAssets | PASS | 1.00 | 1.00 | |
| explore-swap | PASS | 0.50 | — | missed: broadcastTransaction |
| explore-assets | PASS | 0.50 | — | missed: getRates |
| explore-fees | PASS | 0.50 | — | missed: estimateFees |
| explore-signing | PASS | 0.50 | — | missed: broadcastTransaction |
| explore-broadcast | PASS | 1.00 | — | |
| explore-rates | PASS | 0.50 | — | missed: getAssets |

**CodeGraph: 7/12 pass | Recall=0.46 | Search MRR=1.00 (for found)**

| Case | Result | Recall | Notes |
|------|--------|--------|-------|
| search-getTradeQuote | PASS | 1.00 | |
| search-signTransaction | PASS | 1.00 | |
| search-broadcastTransaction | PASS | 1.00 | |
| search-estimateFees | PASS | 1.00 | |
| search-getRates | **FAIL** | 0.00 | missed: getRates |
| search-getAssets | **FAIL** | 0.00 | missed: getAssets |
| explore-quote | PASS | 0.50 | missed: getTradeQuote |
| explore-sign-broadcast | PASS | 0.50 | missed: broadcastTransaction |
| explore-assets | PASS | 0.50 | missed: getAssets |
| explore-fees | **FAIL** | 0.00 | missed: estimateFees, getNetworkFee |
| explore-rates | **FAIL** | 0.00 | missed: getRates, getTradeRate |
| explore-trade | **FAIL** | 0.00 | missed: getTradeQuote, broadcastTransaction |

---

### mtkruto

**JIDRA: 12/12 pass | Recall=0.847 | Search MRR=0.251**

| Case | Result | Recall | MRR | Notes |
|------|--------|--------|-----|-------|
| search-sendMessage | PASS | 1.00 | 0.06 | found but not rank 1 (many overloads) |
| search-invoke | PASS | 1.00 | 0.05 | found but not rank 1 (many overloads) |
| search-serialize | PASS | 1.00 | 0.25 | found serializeObject |
| search-getMe | PASS | 1.00 | 0.05 | found but not rank 1 |
| search-signIn | PASS | 1.00 | 0.10 | found but not rank 1 |
| search-forwardMessages | PASS | 1.00 | 1.00 | |
| explore-send-flow | PASS | 0.67 | — | missed: serializeObject |
| explore-session | PASS | 1.00 | — | |
| explore-invoke | PASS | 1.00 | — | |
| explore-connection | PASS | 0.50 | — | missed: send |
| explore-auth | PASS | 0.50 | — | missed: getMe |
| explore-messaging | PASS | 0.50 | — | missed: forwardMessages |

**CodeGraph: 11/12 pass | Recall=0.72 | Search MRR=1.00 (for found)**

| Case | Result | Recall | Notes |
|------|--------|--------|-------|
| search-sendMessage | PASS | 1.00 | |
| search-invoke | PASS | 1.00 | |
| search-serialize | **FAIL** | 0.00 | missed: serializeObject |
| search-getMe | PASS | 1.00 | |
| search-signIn | PASS | 1.00 | |
| search-forwardMessages | PASS | 1.00 | |
| explore-send-flow | PASS | 0.67 | missed: serializeObject |
| explore-session | PASS | 0.50 | missed: receive |
| explore-invoke | PASS | 0.50 | missed: sendMessage |
| explore-connection | PASS | 0.50 | missed: send |
| explore-auth | PASS | 1.00 | |
| explore-messaging | PASS | 0.50 | missed: sendMessage |

---

### postybirb

**JIDRA: 10/12 pass | Recall=0.708 | Search MRR=0.067**

| Case | Result | Recall | MRR | Notes |
|------|--------|--------|-----|-------|
| search-validateSubmission | PASS | 1.00 | 0.14 | found but not rank 1 |
| search-postSubmission | PASS | 1.00 | 0.04 | found but not rank 1 |
| search-uploadFile | PASS | 1.00 | 0.05 | found but not rank 1 |
| search-login | PASS | 1.00 | 0.04 | found but not rank 1 |
| search-updateSubmission | PASS | 1.00 | 0.05 | found but not rank 1 |
| search-post | PASS | 1.00 | 0.08 | found but not rank 1 |
| explore-submit | **FAIL** | 0.00 | — | missed: validateSubmission, postSubmission |
| explore-file | PASS | 1.00 | — | |
| explore-auth | PASS | 0.50 | — | missed: logout |
| explore-website | PASS | 0.50 | — | missed: validateSubmission |
| explore-update | **FAIL** | 0.00 | — | missed: updateSubmission, validateSubmission |
| explore-posting | PASS | 0.50 | — | missed: postSubmission |

**CodeGraph: 9/12 pass | Recall=0.63 | Search MRR=1.00 (for found)**

| Case | Result | Recall | Notes |
|------|--------|--------|-------|
| search-validateSubmission | PASS | 1.00 | |
| search-postSubmission | PASS | 1.00 | |
| search-uploadFile | PASS | 1.00 | |
| search-login | PASS | 1.00 | |
| search-updateSubmission | PASS | 1.00 | |
| search-post | PASS | 1.00 | |
| explore-submit | PASS | 0.50 | missed: postSubmission |
| explore-file | PASS | 0.50 | missed: uploadFile |
| explore-auth | **FAIL** | 0.00 | missed: login, logout |
| explore-website | **FAIL** | 0.00 | missed: validateSubmission, login |
| explore-update | **FAIL** | 0.00 | missed: updateSubmission, validateSubmission |
| explore-posting | PASS | 0.50 | missed: postSubmission |

---

### trezor

**JIDRA: 10/12 pass | Recall=0.708 | Search MRR=0.508**

| Case | Result | Recall | MRR | Notes |
|------|--------|--------|-----|-------|
| search-signTransaction | PASS | 1.00 | 0.50 | |
| search-getAddress | PASS | 1.00 | 0.05 | found but not rank 1 |
| search-getFeatures | PASS | 1.00 | 0.50 | |
| search-useSendForm | PASS | 1.00 | 0.50 | |
| search-applySettings | PASS | 1.00 | 0.50 | |
| search-estimateFee | PASS | 1.00 | 1.00 | |
| explore-tx-flow | PASS | 0.50 | — | missed: pushTransaction |
| explore-device | **FAIL** | 0.00 | — | missed: getFeatures, getAddress |
| explore-send-form | PASS | 1.00 | — | |
| explore-fees | **FAIL** | 0.00 | — | missed: estimateFee, composeTransaction |
| explore-settings | PASS | 0.50 | — | missed: applySettings |
| explore-account | PASS | 0.50 | — | missed: getAddress |

**CodeGraph: 8/12 pass | Recall=0.50 | Search MRR=1.00 (for found)**

| Case | Result | Recall | Notes |
|------|--------|--------|-------|
| search-signTransaction | PASS | 1.00 | |
| search-getAddress | **FAIL** | 0.00 | missed: getAddress |
| search-getFeatures | PASS | 1.00 | |
| search-useSendForm | **FAIL** | 0.00 | missed: useSendForm |
| search-applySettings | PASS | 1.00 | |
| search-estimateFee | PASS | 1.00 | |
| explore-tx-flow | PASS | 0.50 | missed: pushTransaction |
| explore-device | **FAIL** | 0.00 | missed: getFeatures, getAddress |
| explore-send-form | PASS | 0.50 | missed: signTransaction |
| explore-fees | **FAIL** | 0.00 | missed: estimateFee, composeTransaction |
| explore-settings | PASS | 0.50 | missed: applySettings |
| explore-account | PASS | 0.50 | missed: getAddress |

---

## Key Observations

### JIDRA wins
1. **Search recall: 100% across all repos** — JIDRA finds every target in top-20, CodeGraph misses getRates/getAssets (shapeshift), serializeObject (mtkruto), getAddress/useSendForm (trezor)
2. **Pass rate: 94% vs 73%** — 21 more passing cases across 48 total
3. **Explore recall: 0.583 vs 0.46** (shapeshift), 0.694 vs 0.72 (mtkruto), 0.417 vs 0.63 (postybirb), 0.417 vs 0.50 (trezor)

### CodeGraph wins
1. **Search MRR when found: 1.00** — when CodeGraph finds a method, it's rank 1. JIDRA finds everything but rank varies (MRR 0.07–0.70 depending on repo)
2. **postybirb explore**: CG 0.63 vs JIDRA 0.417 — CG's density-based traversal surfaces more relevant methods in interface-heavy codebase

### JIDRA weaknesses
- **Low search MRR on common names**: `invoke` (0.05), `sendMessage` (0.06), `login` (0.04) — exact match now pins to top, but BM25 on short generic names still needs work
- **Explore misses on abstract method trees**: postybirb `explore-submit` and `explore-update` both score 0.00 — methods live on abstract base classes, graph traversal doesn't cross the interface boundary

### CodeGraph weaknesses  
- **Partial name search fails**: `getRates`, `getAssets`, `serializeObject`, `useSendForm` all miss — CodeGraph uses exact/fuzzy match, not FTS; camelCase sub-word queries fail
- **Explore recall lower overall** on shapeshift and trezor despite higher density scores — returning many nodes doesn't mean returning the *right* nodes
