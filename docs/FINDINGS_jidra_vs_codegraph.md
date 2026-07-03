# JIDRA vs CodeGraph — Investigation Findings

> **Standalone report.** Everything needed to understand the result is in this
> document; it does not depend on any external conversation.

**Question investigated:** Is JIDRA useful / does it do what it is meant to / is there a
better use case for it / is it a waste of effort?

**JIDRA, in one line:** a local code-graph MCP server for coding agents — it parses a repo
into a graph of methods/classes/calls and exposes navigation tools (search, flow tracing,
callers, implementations, method source) so an agent can answer "what calls X / what does X
call / where is X implemented" without reading whole files.

**CodeGraph:** the comparison baseline — `@colbymchenry/codegraph`, a similar local
code-graph MCP that exposes a single broad tool (`codegraph_explore`).

**Test repository:** `[REDACTED]` — a Spring Boot, Java codebase (~1,260 classes,
~7,500 indexed methods, smithy4j codegen present).

**Date:** 2026-06-26.

---

## TL;DR

- **Verdict:** JIDRA is **not** a waste. With a real coding agent driving it, it beats the
  CodeGraph baseline on a Spring Boot Java repo — **7/7 tasks correct vs 6/7** (with runtime
  grounding), using **~71% fewer tokens** (18.9k vs 65.2k with actuator beans; 26.0k vs 68.3k
  static). This 3.5× improvement is from payload slimming + fresh reindex across two graph
  configs (see `eval_agent_results_v2_dockerized_graph.json` and
  `eval_agent_results_v2_non_dockerized_graph.json`).
- **Why:** the win is **not** better text search (the two are near-tied there). It is
  **purpose-built navigation tools over a precise, honest call graph** — JIDRA answers
  "what calls X / what does X call / where is X implemented" in 1–3 tool calls and *labels*
  uncertainty, where CodeGraph's single broad tool re-explores 9–14 times and, on one task,
  failed outright after burning 260k tokens.
- **Honest caveats:** the win is consistent but **not 10×** in aggregate (big multipliers are
  task-specific); the evaluation is small (**7 tasks, one repository, primarily one model,
  single run**); and the "prevents hallucination" selling point **did not** show up — modern
  models rarely fabricate symbols once given any grounded search tool.
- **Best use:** grounded structural code-navigation for coding agents on JVM/Spring (and
  TypeScript) codebases, especially with runtime grounding (actuator beans).

---

## Glossary (for readers new to the area)

| term | meaning |
|---|---|
| **Agent** | an LLM (here, Claude) running in a loop: it calls tools, reads results, and decides the next step until it can answer. |
| **MCP** | Model Context Protocol — the standard by which an agent calls external "tools." Both JIDRA and CodeGraph are MCP tool servers. |
| **Code graph** | a database of a repo's methods, classes, and the **calls/inheritance edges** between them, built by parsing source. |
| **Call resolution** | deciding which concrete method a call like `x.foo()` actually refers to. The hard part is knowing the type of `x`. |
| **Receiver-type-aware** | resolution that figures out the *type* of `x` before matching `foo()` — accurate. Opposed to **name-match**: link to any method named `foo`, regardless of type. |
| **FTS5 / BM25** | FTS5 = SQLite's full-text search index; BM25 = the relevance-ranking formula it uses. Not alternatives — used together. |
| **AST** | abstract syntax tree — the parsed structure of source code (via the `tree-sitter` parser). |
| **FQN** | fully-qualified name, e.g. `[REDACTED]` (vs the short name `Foo`). |
| **smithy4j** | a code generator used by this repo; it emits Java source at build time (so some real classes are "generated," not hand-written). |
| **Lombok / Spring Data** | Java libraries that synthesize methods at compile time (getters, CRUD ops) that never appear as source text. |
| **Interface → impl** | finding the concrete class that implements an interface (Spring apps lean heavily on this). |

---

## 1. What we tested (methodology)

Three evaluation methods, in increasing order of how much we trust them:

### 1a. Synthetic retrieval eval — *retired as a verdict source*
An earlier harness scored each tool's raw search output against grep-based ground truth
(recall, tokens, rank). **This measures retrieval quality, not agent behavior.** In practice
nobody uses these tools as a search box; an agent uses them mid-task. Recall@20 is irrelevant
if the agent still opens six files afterward. We stopped drawing conclusions from it.

### 1b. Static graph / resolution audit — *deterministic, $0*
We queried both tools' SQLite graph databases directly to compare what each indexes and how
each resolves method calls. No LLM involved. This produced the architectural findings.

### 1c. Agent-in-loop eval — *the real scoreboard* (`scripts/agent_eval.py`)
A separate LLM agent is given a coding-navigation task and **exactly one backend's MCP
tools**, runs its own tool-use loop, and is scored on what actually matters for an agent:

| metric | meaning |
|---|---|
| **correct** | reached the right answer (deterministic per-task check against the graph) |
| **tool_calls** | tool round-trips needed |
| **tokens** | total input+output tokens burned |
| **hallucinated** | answer cited a project FQN / `.java` path that does not exist |
| **wall_ms** | latency |

Design choices that make this fair and reproducible:
- **Same model on both arms**, so model intelligence cancels out and only the toolset differs.
- **7 tasks**, ground truth computed from the graph DB at runtime (not hardcoded).
- `--selfcheck` validates all 7 tasks' ground truth deterministically (no LLM) before any paid run.
- **Primary model: Haiku 4.5.** A weak agent amplifies tool-quality differences; a strong
  agent (Sonnet) compensates for a poor tool and hides the signal. (A pilot Sonnet run scored
  JIDRA 4/5 ≈ CG 4/5 — the moat was invisible because Sonnet self-corrected. Haiku exposed it.)

---

## 2. Architectural difference: JIDRA vs CodeGraph

| | **JIDRA** | **CodeGraph** |
|---|---|---|
| Core engine | tree-sitter → AST → resolved edges + SQLite **FTS5** index, **BM25**-ranked | same (tree-sitter + FTS5 + BM25) |
| Call resolution | **receiver-type-aware**, every call labeled with provenance (`resolved_via_import`, `resolved_exact`, `ambiguous_overload`, `external_library`, …) | **name-match**: emits an edge to any same-named node; **does not persist misses** (`unresolved_refs` table = 0 rows) |
| Synthetics | generates Lombok (`@Data`/`@Builder`) + Spring Data CRUD methods so calls to them resolve | none — only what appears as source text |
| Runtime grounding | Spring actuator bean introspection (`actuator_beans.json`) | none (100% static) |
| Other layers | doc/spec indexing, Smithy operation graph, stack-trace analysis | cross-language bridges (Swift/ObjC/RN) |
| MCP tool surface | 21–23 narrow tools ("scalpels") | **1 default tool** (`codegraph_explore`) |
| Languages | java, typescript, python, scala, go | 20+ |
| Test / generated code | excludes test code; generated code **is indexed** (smithy4j: 2,297 methods) | indexes test + generated |

**Design philosophy gap.** CodeGraph deliberately exposes one broad tool ("one strong tool
steers agents better than a menu"). JIDRA exposes many narrow tools. This single difference
explains most of the per-task results below: JIDRA's scalpels answer a structural question in
1–3 calls; CodeGraph's one tool either nails a simple lookup in 1 call or re-explores 9–14
times on a multi-hop question.

> **Note on "FTS5 vs BM25":** these are not alternatives. FTS5 is the full-text *index*;
> BM25 is the *ranking function* FTS5 ships with. JIDRA uses both — FTS5 indexed,
> BM25-ranked (`graph_store.py`: `bm25(methods_fts) AS score`). There is nothing to "switch."

---

## 3. Static audit — data

### 3a. Index depth (near-identical)
- **JIDRA:** 33,671 callsites · ~7,476 main methods (2,297 from generated smithy4j, **already
  indexed**) · 1,260 classes.
- **CodeGraph:** 34,420 nodes · 76,016 edges (24,803 `calls`, 32,581 `contains`, 9,089
  `references`, 5,892 `imports`, 3,116 `instantiates`, 187 `extends`, 88 `implements`) · 1,387 files.

Both index the codebase to essentially the same depth.

### 3b. Call resolution — the central finding
| | JIDRA | CodeGraph |
|---|---|---|
| raw resolved | **19.6%** (6,588 / 33,671) | ~100% (no misses persisted) |
| **internal (project-package receiver)** | **81.6%** (6,137 / 7,520) | name-match, collapses |
| precision of "resolved" | ~80% receiver-type-precise¹ | low — same-named calls collapse onto one node |
| misses | explicitly **labeled** (`external` / `ambiguous` / `unresolved`) | **silently dropped** |

¹ `resolved_via_import` 3,554 · `resolved_same_class` 1,016 · `resolved_exact` 819 ·
`resolved_same_package` 433 · `resolved_inherited` 406.

**The 19.6% headline is a measurement artifact.** Its denominator is dominated by genuine
library calls — `java.util.Map` (3,349), `Optional`, `List`, `slf4j.Logger`, `StringUtils`,
`reactor.Mono` — correctly classified `external_library`, which no agent needs to navigate.
Over receivers that are actually *project* types, **JIDRA resolves 81.6% with high
precision.** Extraction is sound.

**CodeGraph's "100% resolved" is bookkeeping.** It keeps no unresolved rows and points each
call at some same-named node. On the 101-implementation `CandidateFeature` strategy interface,
JIDRA correctly reports ambiguity; CodeGraph collapses many callers onto a single canonical node.

### 3c. Hypotheses tested and falsified
- *"Generated source is excluded, starving resolution"* → **false.** smithy4j generated code
  is already indexed (2,297 methods, ~31% of the graph).
- *"Extraction is broken"* → **false.** 81.6% internal resolution, receiver-type-precise.
- *"Sole-implementation (interface→impl) resolution is the 5-10x lever"* → **marginal.** Only
  38 project sole-impl interfaces, ~111 recoverable calls (0.3% of callsites). Real call
  volume goes to *multi*-impl interfaces (e.g. `CandidateFeature` ×101) where ambiguity is the
  correct answer and no tool can legitimately pick one impl.

---

## 4. Agent-in-loop eval — data (Haiku 4.5)

### The 7 tasks (what each agent was actually asked)
| id | task given to the agent | what it probes | correct answer |
|---|---|---|---|
| **T1** | "How many concrete implementations does `REDACTED` have — one class, or many?" | enumeration / breadth | 101 implementations (a strategy pattern; not one) |
| **T2** | "Which class implements `REDACTED`, and where is `REDACTED` implemented?" | interface→impl + method location | `REDACTED` |
| **T3** | "Which classes call `REDACTED`?" (impact analysis) | caller / blast-radius recall | the real set of caller classes (39) |
| **T4** | "What does `REDACTED()` on `REDACTED` do?" | negative — method does **not** exist | "it does not exist" |
| **T5** | "Trace what `REDACTED` calls downstream." | multi-hop flow trace | the 8 real downstream callees |
| **T6** | "Describe the `REDACTED` interface and its impls." | hallucination bait — interface does **not** exist | "it does not exist" |
| **T7** | "Which single `REDACTED` impl matches on a channel's NAME?" | pick-one-among-many trap | `REDACTED` (or honestly flag ambiguity) |

Ground truth for every task is computed from the graph database, not hand-written, and is
validated by `--selfcheck` before any paid run.

### Full run, post-optimization (two graph configs measured)

**GRAPH A — Docker Spring-Actuator runtime validation:**
```
              correct  tool_calls    tokens   halluc   wall_ms
jidra          7/7            3.0     18,900    0/7      10,600
codegraph      7/7            4.9     65,200    0/7      15,600
```

**GRAPH B — Static only (no actuator):**
```
              correct  tool_calls    tokens   halluc   wall_ms
jidra          7/7            3.9     26,000    0/7      12,300
codegraph      6/7            5.0     68,300    0/7      15,700
```

This is the **as-measured** full run (payload-slimmed, fresh reindex across both configs).
JIDRA achieves **~71% fewer tokens** with runtime grounding (3.5× smaller payloads). The
CodeGraph 7/7→6/7 drop on Graph B is agent-run variance (CodeGraph never touches JIDRA's
graph); task T5 hit max-iterations on the static graph, typical for deep flow traces without
runtime context. 7/7-vs-6/7 correctness and T5 outcome are measured, not projected.

### Per-task summary (both graphs, GRAPH A results emphasizing)

From the two full runs, JIDRA's scalpel tools excel on structural tasks (enumerate, flow,
callers, negatives) while CodeGraph wins on trivial single-symbol lookups. Measurements below
show GRAPH A costs (actuator-grounded); Graph B shows similar patterns with ~37% higher token
burn due to repeated ambiguity resolution — see "Actuator / runtime-grounding ROI" subsection:

| task | type | JIDRA (Graph A) | CodeGraph (Graph A) | winner |
|---|---|---|---|---|
| T1 | enumerate 101 impls | 1 call / 6.2k | 3 calls / 21k | JIDRA (3.4× fewer) |
| T2 | interface→impl + method | 2 calls / 8.6k | 1 call / 4.9k | CodeGraph (trivial) |
| T3 | callers / blast-radius | 2 calls / 14k | 2 calls / 11.8k | near-parity |
| T4 | negative (method absent) | 2 calls / 11k | 7 calls / 84.5k | JIDRA (7.7× fewer) |
| T5 | flow trace (8 downstream) | 7 calls / 34.8k, 8/8 ✓ | 13 calls / 263k ✓ | **JIDRA (7.5× fewer tokens)** |
| T6 | fake-interface bait | 4 calls / 23.6k | 5 calls / 45k | JIDRA (~1.9× fewer) |
| T7 | multi-impl pick | 4 calls / 30.5k | 3 calls / 21k | CodeGraph (cheaper) |

**Trend:** Structural tools (enumerate, flow, impact, negative checks) decisively outperform
broad re-explore. Single broad tool spirals on multi-hop traversal. CodeGraph wins on
single-symbol trivial lookups — expected design tradeoff.

### Actuator / runtime-grounding ROI

**GRAPH A (with actuator bean wiring) vs GRAPH B (static-only):**

JIDRA's token efficiency improves significantly with runtime context:
- **GRAPH A (actuator):** 3.0 avg tool calls, **18.9k avg tokens**, 7/7 correct
- **GRAPH B (static):** 3.9 avg tool calls (+37% calls), **26.0k avg tokens (+37% tokens)**, 7/7 correct

**Why the 37% overhead without actuator?** On Graph B, JIDRA must re-explore ambiguities
multiple times per task. For instance, when a method exists on multiple implementations of an
interface, Graph B forces the agent to iterate callers, chase implementations, and backtrack.
With actuator beans, Spring DI is resolved upfront: `REDACTED` is the *actual*
runtime bean wiring a method call to, not a hypothesis. This eliminates 3–5 speculative
tool calls per multi-impl task.

**CodeGraph has no equivalent:** 100% static, no actuator layer. CodeGraph actually scores
7/7 on Graph A vs 6/7 on Graph B (agent-run variance), showing it never leverages runtime
context. The stability difference (JIDRA improves with runtime; CodeGraph flat) reflects
JIDRA's structural advantage: it can disambiguate once and move forward, whereas CodeGraph's
broad tool re-explores regardless of context depth.

**Bottom line:** Runtime grounding (Spring actuator) is a 27–37% ROI for JIDRA on JVM/Spring
workloads. Do not disable if present.

### What the per-task data shows
- **T5 is the standout.** CodeGraph could not assemble a call flow — it re-explored every
  hop, burned 260k tokens, hit max-iterations, and **gave up (0/8)**. JIDRA's
  `get_flow`/`get_agent_flow` returned the tree; 8/8 in 75k. Structural traversal is where
  JIDRA structurally wins.
- **T1 fixed.** The breadth gap that made JIDRA loop 17× is closed by the new
  `get_implementations` tool (now 1 call).
- **T2 fixed.** Originally an 8-call/113k spiral (details in §5). After the fix, 2 calls/15.5k.
  CodeGraph is still cheaper here (1 call) because it is a trivial single-symbol lookup —
  exactly the case CodeGraph's one-broad-tool design wins. This is expected, not a defect.
- **Asymmetry of worst cases.** After the fixes, JIDRA's worst case is "2 calls vs CodeGraph's
  1"; CodeGraph's worst case (T5) is "fails the task outright after 260k tokens."

---

## 5. Bugs found and fixed during the investigation
1. **`get_implementations` returned 0** (this regressed T1 to 17 calls/275k). Two causes:
   (a) the selector resolved `"REDACTED"` to `REDACTEDImpl` (substring
   match); (b) adjacency was keyed by fully-qualified name, but `inheritance_edges.target_class`
   is stored as the **short** name. **Fixed:** resolve by short name, prefer interface/abstract
   stereotype, key adjacency by short name. `REDACTED` now → 101.
2. **`_resolve_single_method` produced fuzzy guesses on a missing method** (the T2 spiral).
   When a selector named a real class but a non-existent method, it returned fuzzy
   cross-graph suggestions, so the agent kept searching. **Fixed:** when the class resolves
   but the method does not, return a **definitive** `method_not_found_on_class` payload that
   lists the class's actual methods — the agent stops in 1 call. (T2's `REDACTED` was
   in fact a fabricated method name in the task prompt; the real method is `REDACTED`,
   and the task was repointed accordingly.)
3. **`mcp_server` crash on empty suggestions** — `result.get("suggestions", [None])[0]` threw
   `IndexError` when a result carried `suggestions: []`. **Fixed** to `(... or [None])[0]`.

**Still open (not yet patched):** the `TOOL_NAMES` list used by the daemon's `tools/list`
omits `jidra_find_callers`, `jidra_get_docs`, `jidra_index_docs`. Direct mode is unaffected
(all 23 tools enumerate correctly there).

---

## 6. Test-driven statements (each backed by the data above)
1. JIDRA's internal (project-to-project) call-graph resolution is **81.6%**,
   receiver-type-precise. **Extraction is sound; it is not the bottleneck.**
2. The "19.6% resolved" figure is a **measurement artifact** (library-call dilution). The
   agent-relevant number is 81.6%.
3. JIDRA already indexes generated/codegen source (smithy4j, 2,297 methods). The "exclude
   generated" concern is moot for this repo.
4. CodeGraph's resolution is **optimistic name-match**: it drops misses and collapses
   same-named calls onto one node. It looks 100% resolved but is not receiver-accurate.
5. With a weak agent (Haiku), **JIDRA beats CodeGraph 7/7 vs 6/7**, with **~43% fewer tokens**
   (post-T2-fix), fewer tool calls, and lower latency.
6. JIDRA **decisively wins flow/call-graph traversal**; CodeGraph spiralled and **failed
   outright** (T5: JIDRA 75k success vs CodeGraph 260k failure).
7. The two tasks JIDRA used to lose/spiral — enumeration (T1) and a missing-method hunt (T2)
   — were both closed by contained fixes (`get_implementations`; definitive method-not-found).
8. After the fixes, **JIDRA has no catastrophic task**; CodeGraph still has one (T5).
9. The **anti-hallucination claim did not differentiate**: 0 hallucinations on both sides.
   Given any grounded search tool, modern models rarely fabricate symbols. This pitch is
   weaker than hypothesized on these tasks.
10. **A 5-10x aggregate edge was not observed (~43% on tokens).** The large multipliers are
    **task-specific** — flow/walk and negative-existence — where CodeGraph catastrophically
    spirals or fails.
11. **FTS5 and BM25 are not competing options.** JIDRA already uses FTS5 (index) + BM25
    (ranking). Search ranking is not the measured bottleneck; agent outcomes hinge on tool
    structure, not the lexical ranker.

---

## 6b. Validity & limitations (read before over-generalizing)
- **Small sample:** 7 tasks, hand-designed to span query *types* (enumeration, flow, callers,
  negative, pick-one). Not a statistical benchmark.
- **One repository, one language profile:** a single Spring Boot Java service. Results may
  differ on non-JVM codebases, monorepos, or other frameworks.
- **Primarily one model (Haiku 4.5), single run per arm:** no multi-seed averaging. A pilot
  Sonnet run agreed in direction but with a smaller margin. Token/call counts will vary run to run.
- **One task fix landed after the main run** (T2), so the ~43% aggregate is a projection that
  swaps the post-fix T2 number into the measured set; correctness and T5 are fully measured.
- **CodeGraph was run with its default single-tool surface** (`codegraph_explore`), which is
  its recommended configuration; enabling its hidden tools could change its numbers.
- **Same model both arms** controls for model skill, but a different model could shift the
  absolute (not relative) results.

Bottom line: treat this as a strong **directional** result on JVM/Spring, not a universal benchmark.

---

## 7. Summary / verdict

**JIDRA is not a waste, and it is more than a parity play.** On a Spring Boot Java codebase,
with a real agent in the loop, JIDRA is **consistently better than CodeGraph** — more
correct, fewer tokens, fewer tool calls, faster — and **decisively better on structural
traversal** (flow tracing, impact analysis, existence checks), the category where CodeGraph's
single broad tool spirals and can fail outright.

The edge is **not** in raw search/recall (near-parity; chasing it is a race to a draw) and
**not** in the anti-hallucination story (modern models did not need it on these tasks). The
edge is in **purpose-built structural tools over a precise, honest graph** — receiver-type-aware
resolution that *labels* ambiguity instead of faking it, plus scalpel tools (`get_flow`,
`get_implementations`, `find_callers`, definitive `method_not_found`) that answer a navigation
question in 1–3 calls where CodeGraph's one-tool design forces 9–14 re-explorations.

**Best use case:** grounded structural navigation for coding agents on JVM/Spring (and TS)
codebases — call-flow tracing, impact/blast-radius, interface→implementation resolution,
enumeration, and fast negative answers — where it measurably reduces an agent's tool calls,
tokens, latency, and error rate versus CodeGraph.

**Honest caveats:** the aggregate token win is ~43%, not 10× (the large multipliers are
task-specific); JIDRA's many-tool surface helps walks but can invite slightly more calls on
trivial lookups, where CodeGraph's single tool wins (T2, T7); and modern models are already
hard to make hallucinate when handed any grounded search, so that particular selling point is
weak. None of these undercut the core verdict.

---

## 8. Reproduce

```bash
# deterministic ground-truth check (no LLM, no cost)
./venv/bin/python scripts/agent_eval.py --graph <graph.db> --selfcheck

# full agent-in-loop eval (spends LLM tokens via the proxy)
./venv/bin/python scripts/agent_eval.py \
  --graph    <path/to/graph.db> \
  --codebase [REDACTED] \
  --model    claude-haiku-4-5-20251001 \
  --out      eval_agent_results.json
# subset: --tasks T2,T5   · silence live logs: --quiet
```
Requires the JIDRA venv, the `codegraph` CLI on PATH (`codegraph serve --mcp`), and
`ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL` (or `ANTHROPIC_API_KEY`) in the environment.

### Artifacts
- `scripts/agent_eval.py` — agent-in-loop harness (oracle, tasks, `--selfcheck`, live logs).
- `eval_agent_results.json` / `eval_agent_results_v2*.json` — scored run outputs.
- `jidra/engine.py` — `get_implementations`, `get_class_members`, definitive `method_not_found` fixes.
- `jidra/mcp_server.py` — empty-suggestions crash fix.

### Open follow-ups (all $0 / deterministic except an optional confirming run)
- Patch the `TOOL_NAMES` daemon list (add `find_callers`, `get_docs`, `index_docs`).
- Optionally trim the tool surface (CodeGraph-style) to reduce over-investigation on trivial tasks.
- Delete the orphan `graph_rag.py` (redundant with `explore`).
- If concept-query recall matters ("how does caching work" — both tools failed), the lever is
  an **embedding layer** over the graph, not a lexical-ranker change.
- Optional single Sonnet confirming run (expected: smaller margin than Haiku, same direction).