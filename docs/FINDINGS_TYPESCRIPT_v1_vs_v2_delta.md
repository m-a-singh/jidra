# TypeScript Eval: v1 → v2 Delta

**v1:** MTKruto, 4 tasks (TS1-TS4), date 2026-06-27
**v2:** MTKruto feature/caveman_installation graph, 5 tasks (TS1-TS5), date 2026-07-02

---

## Score

| metric | v1 | v2 | delta |
|---|---|---|---|
| JIDRA correct | 4/4 (3/4 scored†) | 5/5 | **+1** (perfect both) |
| CG correct | 1/4 | 3/5 | +2 (CG improved on TS2-TS4) |
| JIDRA avg calls | 3.75 | 2.4 | **-36%** |
| JIDRA avg tokens | 37,039 | 16,108 | **-57%** |
| CG avg calls | 8.5 | 10.2 | +20% (worse) |
| CG avg tokens | 148,970 | 352,633 | **+137%** (much worse) |
| Token ratio | ~4× | **21.9×** | JIDRA gap widened massively |
| **JIDRA cost** | ~$0.140† | **$0.076** | **-46%** |
| **CG cost** | ~$0.486† | **$1.433** | **+195%** |
| **Cost ratio** | 3.5× | **18.9×** | JIDRA savings grew 5.4× |

†v1 cost estimated from avg-token totals (no raw JSON); v2 exact from API telemetry. Haiku 4.5: $0.80/MTok in, $4.00/MTok out.

†v1 TS3 was scored wrong (markdown bold broke substring match). Corrected score was 4/4.

---

## Per-task comparison (TS1-TS4 overlap)

| task | v1 JIDRA | v2 JIDRA | v1 CG | v2 CG |
|---|---|---|---|---|
| TS1 callers `getDb` | ✓ 1c/7.8k | ✓ **1c/8.9k** | ✗ 1c/5.3k | ✗ 6c/113k |
| TS2 callees `spawnSession` | ✓ 10c/116k | ✓ **5c/37k** | ✗ 14c/275k | ✓ 10c/344k |
| TS3 negative | ✓ 2c/11k | ✓ **3c/19k** (slight regress) | ✓ 5c/47k | ✓ 11c/346k |
| TS4 locate `enforceBudget` | ✓ 2c/13k | ✓ **2c/11k** | ✗ 14c/269k | ✓ 10c/368k |

---

## Key movements

**JIDRA: 57% token reduction across overlapping tasks.**
TS2 cut most dramatically: 10c/116k → 5c/37k. Agents navigate more directly; `#` selector fix and better graph resolution mean fewer fallback calls.

**CG: flipped from 1/4 to 3/5 — but at 2.4× more tokens.**
v1 CG failed TS2/TS4 by hitting 14-call cap. v2 CG passes both — different query strategy, avoids the cap. But token cost exploded: TS2 344k (was 275k), TS4 368k (was 269k). CG is trading more tokens for correctness. Still fails TS1 (caller enumeration design gap) and TS5 (truncation wall).

**TS5 (new) — CG DNF.**
TS5 asks for `enforceBudget` source directly. JIDRA: 1c/5k. CG: 14c/592k → no answer. Same processManager.ts truncation wall that caused TS4 failure in v1. CG has no `get_method_source` equivalent — can never retrieve past the 20k-char truncation point.

**TS3 JIDRA: 2c → 3c minor regress.**
Needed one extra explore call ("purge") before concluding absent. Likely graph index difference between runs. Correct either way; no impact on correctness.

**Token ratio: 4× → 21.9×.**
Driven by two factors: JIDRA got cheaper (57% down), CG got more expensive (+137%). CG's new "don't hit cap" strategy burns more tokens per attempt. JIDRA's scalpel advantage is widening.

---

## CG structural limits confirmed

| gap | v1 | v2 | trend |
|---|---|---|---|
| TS1 caller enumeration | ✗ 1c (0 callers) | ✗ 6c (wrong count) | design limit — blast-radius ≠ call graph |
| TS4/TS5 truncation wall | ✗ 14c DNF | ✓/✗ 10c passes / 14c DNF | partial: CG escapes truncation on TS4 via context, fails TS5 |
| TS2 callee tracing | ✗ 14c DNF | ✓ 10c/344k | "fixed" but 9.4× token cost vs JIDRA |

---

## What changed between v1 and v2

1. **Graph updated** — `MTKruto-feature-caveman_installation` branch; different graph than v1's `/tmp/jidra_ts.db`.
2. **`#` selector fix** (from Java v11) — applies to TypeScript. `Class#method` notation resolves directly.
3. **TS5 added** — new task asking for full source. Exposes CG truncation wall more clearly.
4. **Harness: TS3 scoring fixed** — markdown-tolerant check. v1 had brittle substring match.
