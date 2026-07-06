# JIDRA vs CodeGraph — Retrieval Eval V3
**Date:** 2026-07-05  
**JIDRA commit:** feature/agent_exploration  
**CodeGraph commit:** 7d624ec  

Changes from V2: morphological stemming (Snowball/English) + BM25 score-tier fix.

Metrics differ by design — not directly comparable on a single number:
- **JIDRA search MRR** measures rank of target in BM25 results
- **CodeGraph search MRR** is always 1.00 when found (first hit only)
- **JIDRA explore** = recall over graph traversal results
- **CodeGraph explore** = recall + density (unique callers / found)

---

## Summary Table

| Repo        | JIDRA Pass | CG Pass | JIDRA Recall | CG Recall | JIDRA Search MRR | CG Search MRR |
|-------------|-----------|---------|--------------|-----------|-----------------|---------------|
| shapeshift  | 12/12 (100%) | 7/12 (58%) | 0.875 | 0.46 | 1.000 | 1.00* |
| mtkruto     | 12/12 (100%) | 11/12 (92%) | 0.889 | 0.72 | 1.000 | 1.00* |
| postybirb   | 10/12 (83%)  | 9/12 (75%)  | 0.708 | 0.63 | 1.000 | 1.00* |
| trezor      | 11/12 (92%)  | 8/12 (67%)  | 0.792 | 0.50 | 1.000 | 1.00* |
| **Average** | **94%** | **73%** | **0.791** | **0.578** | **1.000** | **1.00*** |

*CodeGraph MRR=1.00 because it only reports MRR for cases where the target is found at rank 1; missed cases get mrr=0.00.

---

## Per-Repo Detail

### shapeshift (web)

**JIDRA V3: 12/12 pass | Recall=0.875 | Search MRR=1.000**

| Case | Result | Recall | MRR | Notes |
|------|--------|--------|-----|-------|
| search-getTradeQuote | PASS | 1.00 | 1.00 | |
| search-signTransaction | PASS | 1.00 | 1.00 | |
| search-estimateFees | PASS | 1.00 | 1.00 | fixed: was rank >1 in V2 |
| search-broadcastTransaction | PASS | 1.00 | 1.00 | |
| search-getRates | PASS | 1.00 | 1.00 | fixed: was rank >1 in V2 |
| search-getAssets | PASS | 1.00 | 1.00 | |
| explore-swap | PASS | 0.50 | — | missed: broadcastTransaction |
| explore-assets | PASS | 0.50 | — | missed: getRates |
| explore-fees | PASS | 1.00 | — | improved from 0.50 in V2 |
| explore-signing | PASS | 1.00 | — | **fixed from FAIL V2** (stemming) |
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

**JIDRA V3: 12/12 pass | Recall=0.889 | Search MRR=1.000**

| Case | Result | Recall | MRR | Notes |
|------|--------|--------|-----|-------|
| search-sendMessage | PASS | 1.00 | 1.00 | fixed: was 0.06 in V2 |
| search-invoke | PASS | 1.00 | 1.00 | fixed: was 0.05 in V2 |
| search-serialize | PASS | 1.00 | 1.00 | fixed: was 0.25 in V2 |
| search-getMe | PASS | 1.00 | 1.00 | fixed: was 0.05 in V2 |
| search-signIn | PASS | 1.00 | 1.00 | fixed: was 0.10 in V2 |
| search-forwardMessages | PASS | 1.00 | 1.00 | |
| explore-send-flow | PASS | 0.67 | — | missed: serializeObject |
| explore-session | PASS | 1.00 | — | |
| explore-invoke | PASS | 0.50 | — | missed: sendMessage |
| explore-connection | PASS | 0.50 | — | missed: send |
| explore-auth | PASS | 1.00 | — | improved from 0.50 in V2 |
| explore-messaging | PASS | 1.00 | — | improved from 0.50 in V2 |

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

**JIDRA V3: 10/12 pass | Recall=0.708 | Search MRR=1.000**

| Case | Result | Recall | MRR | Notes |
|------|--------|--------|-----|-------|
| search-validateSubmission | PASS | 1.00 | 1.00 | fixed: was 0.14 in V2 |
| search-postSubmission | PASS | 1.00 | 1.00 | fixed: was 0.04 in V2 |
| search-uploadFile | PASS | 1.00 | 1.00 | fixed: was 0.05 in V2 |
| search-login | PASS | 1.00 | 1.00 | fixed: was 0.04 in V2 |
| search-updateSubmission | PASS | 1.00 | 1.00 | fixed: was 0.05 in V2 |
| search-post | PASS | 1.00 | 1.00 | fixed: was 0.08 in V2 |
| explore-submit | PASS | 0.50 | — | improved from 0.00 in V2 (stemming) |
| explore-file | PASS | 1.00 | — | |
| explore-auth | PASS | 0.50 | — | missed: logout |
| explore-website | **FAIL** | 0.00 | — | missed: validateSubmission, login |
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

**JIDRA V3: 11/12 pass | Recall=0.792 | Search MRR=1.000**

| Case | Result | Recall | MRR | Notes |
|------|--------|--------|-----|-------|
| search-signTransaction | PASS | 1.00 | 1.00 | |
| search-getAddress | PASS | 1.00 | 1.00 | fixed: was 0.05 in V2 |
| search-getFeatures | PASS | 1.00 | 1.00 | fixed: was 0.50 in V2 |
| search-useSendForm | PASS | 1.00 | 1.00 | fixed: was 0.50 in V2 |
| search-applySettings | PASS | 1.00 | 1.00 | |
| search-estimateFee | PASS | 1.00 | 1.00 | |
| explore-tx-flow | PASS | 0.50 | — | missed: pushTransaction |
| explore-device | **FAIL** | 0.00 | — | missed: getFeatures, getAddress (semantic gap) |
| explore-send-form | PASS | 1.00 | — | improved from 0.50 in V2 |
| explore-fees | PASS | 0.50 | — | improved from 0.00 in V2 (stemming) |
| explore-settings | PASS | 1.00 | — | improved from 0.50 in V2 |
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

### JIDRA V3 wins vs V2
1. **Search MRR: 0.382 → 1.000** — BM25 score-tier fix ensures definition always ranks above callers. Previously generic names (`invoke`, `sendMessage`, `login`) ranked callers above definition.
2. **explore-signing PASS** (was FAIL) — stemming fixed `"signing"` → `"sign"` FTS5 match
3. **trezor: 10/12 → 11/12** — explore-fees improved from 0.00 to 0.50 via stemming
4. **postybirb explore-submit**: 0.00 → 0.50 — stemming `"submit"` now matches `postSubmission`

### JIDRA V3 wins vs CodeGraph
1. **Search recall: 100% across all repos** — CodeGraph misses getRates/getAssets (shapeshift), serializeObject (mtkruto), getAddress/useSendForm (trezor)
2. **Pass rate: 94% vs 73%** — 10 more passing cases
3. **Search MRR: 1.000 vs 1.000** — now tied on ranking precision
4. **Explore recall: higher on 3/4 repos** (shapeshift 0.750 vs 0.46, mtkruto 0.778 vs 0.72, trezor 0.583 vs 0.50)

### Remaining JIDRA weaknesses
- **postybirb explore-website / explore-update (0.00)** — abstract base class boundary; graph traversal doesn't cross interface hierarchy. Unchanged from V2.
- **trezor explore-device (0.00)** — `"device initialization"` has no lexical overlap with `getFeatures` / `getAddress`. Semantic gap; requires embeddings.
- **postybirb explore recall (0.417)** still below CodeGraph (0.63) — interface-heavy architecture limits graph traversal depth.

### CodeGraph weaknesses (unchanged from V2)
- **Partial name search fails**: `getRates`, `getAssets`, `serializeObject`, `useSendForm` all miss — exact/fuzzy match, not FTS; camelCase sub-word queries fail
- **Explore recall lower overall** on shapeshift and trezor — high density scores don't mean returning the *right* nodes
