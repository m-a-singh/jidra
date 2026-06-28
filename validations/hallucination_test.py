#!/usr/bin/env python3
"""
JIDRA Hallucination & Consistency Validation

Tests whether JIDRA context reduces hallucinations and drift vs traditional
raw-source context across 5 test dimensions.

Tests:
  1. Call graph accuracy   — does model correctly name the methods X calls?
  2. Caller tracing        — does model correctly name what calls X?
  3. Change impact         — does model correctly identify what breaks if X changes?
  4. Unit test generation  — do generated tests reference real symbols?
  5. Consistency/drift     — does the model give the same answer twice?

Scoring:
  precision  = true_positives / (true_positives + false_positives)
  recall     = true_positives / (true_positives + false_negatives)
  drift      = |run1_claims Δ run2_claims| / |run1_claims ∪ run2_claims|

Ground truth is derived directly from graph.db — no manual labelling.

Usage:
    # Pass methods inline
    ANTHROPIC_API_KEY=... python validations/hallucination_test.py \
        --graph /path/to/.jidra/graph.db \
        --codebase /path/to/repo \
        --methods "OrderController.createOrder,PaymentService.charge"

    # Pass methods via file (one per line, or JSON array)
    ANTHROPIC_API_KEY=... python validations/hallucination_test.py \
        --graph /path/to/.jidra/graph.db \
        --codebase /path/to/repo \
        --methods-file validations/my_methods.txt

    # Auto-discover endpoints from the graph (no --methods needed)
    ANTHROPIC_API_KEY=... python validations/hallucination_test.py \
        --graph /path/to/.jidra/graph.db \
        --codebase /path/to/repo \
        --auto-discover --discover-limit 5

    # Run only specific tests
    ANTHROPIC_API_KEY=... python validations/hallucination_test.py \
        --graph ... --codebase ... --methods "..." --tests 4,5

Methods file format (my_methods.txt) — either:
    ClassName.methodName          (one per line)
    ["ClassName.method1", ...]    (JSON array)

Method selectors support partial matching:
    "search"                  → matches any method named search
    "SearchController.search" → matches class+method
    "com.example.SearchController.search" → fully qualified
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jidra.cost_calculator import _build_jidra_context, _collect_naive_files
from jidra import graph_store
from jidra.selector import _resolve_method_selector


def _load_methods_from_file(path: str) -> list[str]:
    """Load method selectors from a file — one per line or JSON array."""
    text = Path(path).read_text().strip()
    if text.startswith("["):
        return [m.strip() for m in json.loads(text) if m.strip()]
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _auto_discover_methods(graph, node_by_id: dict, limit: int) -> list[str]:
    """
    Auto-discover methods to test from the graph.
    Prefers endpoints (REST controllers), falls back to methods with most edges.
    """
    nodes = list(node_by_id.values())
    method_nodes = [n for n in nodes if n.get("node_type") == "method"]

    # Prefer endpoint methods
    endpoints = [n for n in method_nodes if n.get("is_endpoint")]
    if endpoints:
        candidates = sorted(
            endpoints, key=lambda n: len(n.get("calls", [])), reverse=True
        )
    else:
        # Fall back to most-connected methods
        candidates = sorted(
            method_nodes, key=lambda n: len(n.get("calls", [])), reverse=True
        )

    results = []
    for node in candidates[
        : limit * 3
    ]:  # oversample to account for resolution failures
        qn = node.get("qualified_name", "")
        if "#" in qn:
            class_part, method_part = qn.split("#", 1)
            class_name = class_part.split(".")[-1]
            method_name = method_part.split("(")[0]
            selector = f"{class_name}.{method_name}"
            if selector not in results:
                results.append(selector)
        if len(results) >= limit:
            break
    return results


# ---------------------------------------------------------------------------
# Ground truth extraction from graph
# ---------------------------------------------------------------------------


def _ground_truth_callees(method_node: dict, node_by_id: dict) -> set[str]:
    """All method names that this method calls (from graph edges)."""
    names = set()
    for call in method_node.get("calls", []):
        target_id = call.get("target_id") if isinstance(call, dict) else call
        target = node_by_id.get(target_id)
        if target:
            name = target.get("method_name") or target.get("qualified_name", "")
            if name:
                names.add(name)
    return names


def _ground_truth_callers(method_id: str, node_by_id: dict) -> set[str]:
    """All method names that call this method (from graph edges)."""
    names = set()
    for node in node_by_id.values():
        for call in node.get("calls", []):
            target_id = call.get("target_id") if isinstance(call, dict) else call
            if target_id == method_id:
                name = node.get("method_name") or node.get("qualified_name", "")
                if name:
                    names.add(name)
    return names


def _all_graph_symbols(node_by_id: dict) -> set[str]:
    """All method and class names in the graph — used to score fabrication."""
    symbols = set()
    for node in node_by_id.values():
        if node.get("method_name"):
            symbols.add(node["method_name"])
        if node.get("class_name"):
            symbols.add(node["class_name"])
        if node.get("qualified_name"):
            # add short name too
            qn = node["qualified_name"]
            if "#" in qn:
                symbols.add(qn.split("#")[1].split("(")[0])
    return symbols


def _extract_java_identifiers(text: str) -> set[str]:
    """Extract plausible Java method/class identifiers from LLM response text."""
    # Match camelCase or PascalCase identifiers, min 4 chars
    tokens = re.findall(r"\b([a-zA-Z][a-zA-Z0-9]{3,})\b", text)
    # Filter out common English words and test framework terms
    stopwords = {
        "this",
        "that",
        "with",
        "from",
        "returns",
        "method",
        "class",
        "when",
        "then",
        "given",
        "should",
        "assert",
        "verify",
        "mock",
        "test",
        "void",
        "true",
        "false",
        "null",
        "return",
        "throws",
        "catch",
        "finally",
        "import",
        "public",
        "private",
        "static",
        "final",
        "override",
        "string",
        "list",
        "object",
        "integer",
        "boolean",
        "optional",
    }
    return {t for t in tokens if t.lower() not in stopwords}


def _score_claims(predicted: set[str], ground_truth: set[str]) -> dict:
    tp = len(predicted & ground_truth)
    fp = len(predicted - ground_truth)
    fn = len(ground_truth - predicted)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "hallucination_rate": round(fp / (tp + fp), 3) if (tp + fp) > 0 else 0.0,
    }


def _drift_score(claims_a: set[str], claims_b: set[str]) -> float:
    union = claims_a | claims_b
    if not union:
        return 0.0
    symmetric_diff = claims_a.symmetric_difference(claims_b)
    return round(len(symmetric_diff) / len(union), 3)


# ---------------------------------------------------------------------------
# Debug log
# ---------------------------------------------------------------------------

_debug_lines: list[str] = []
_total_cost: float = 0.0
_total_input_tokens: int = 0
_total_output_tokens: int = 0

# Haiku 4.5 pricing per 1M tokens
_PRICING = {
    "claude-haiku-4-5": {"input": 0.80, "output": 4.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-7": {"input": 15.0, "output": 45.0},
}


def _call_cost(model: str, inp: int, out: int) -> float:
    p = _PRICING.get(model, {"input": 3.0, "output": 15.0})
    return (inp * p["input"] + out * p["output"]) / 1_000_000


def _debug(text: str) -> None:
    _debug_lines.append(text)


def _flush_debug(output_path: str) -> None:
    if output_path and _debug_lines:
        debug_path = Path(output_path).with_name("debug_validation.txt")
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text("\n".join(_debug_lines) + "\n")
        print(f"Debug log written to: {debug_path}")


# ---------------------------------------------------------------------------
# Claude API caller
# ---------------------------------------------------------------------------


def _call_claude(
    client, model: str, context: str, question: str
) -> tuple[str, int, int]:
    global _total_cost, _total_input_tokens, _total_output_tokens
    resp = client.messages.create(
        model=model,
        max_tokens=1000,
        messages=[
            {"role": "user", "content": f"CONTEXT:\n{context}\n\nQUESTION:\n{question}"}
        ],
    )
    text = resp.content[0].text if resp.content else ""
    inp, out = resp.usage.input_tokens, resp.usage.output_tokens
    cost = _call_cost(model, inp, out)
    _total_cost += cost
    _total_input_tokens += inp
    _total_output_tokens += out
    print(
        f"        ↳ in={inp} out={out} cost=${cost:.5f} | session total=${_total_cost:.4f}",
        flush=True,
    )
    return text, inp, out


# ---------------------------------------------------------------------------
# Multiple choice helpers
# ---------------------------------------------------------------------------


def _make_mc_question(
    stem: str, correct: str, distractors: list[str], n_choices: int = 4
) -> tuple[str, str]:
    """
    Build a multiple choice question with one correct answer and n-1 distractors.
    Returns (question_text, correct_letter).
    """
    pool = distractors[:]
    random.shuffle(pool)
    choices = [correct] + pool[: n_choices - 1]
    random.shuffle(choices)
    correct_letter = "ABCD"[choices.index(correct)]
    lines = [stem, ""]
    for i, c in enumerate(choices):
        letter = "ABCD"[i]
        lines.append(f"  {letter}) {c}")
    lines.append("\nReply with ONLY the letter of the correct answer (A, B, C, or D).")
    return "\n".join(lines), correct_letter


def _extract_mc_answer(text: str) -> str:
    """Extract A/B/C/D from model response — look for the final answer, not the first letter."""
    t = text.strip()
    # Try explicit answer patterns first: "**B**", "Answer: B", "The answer is B", "answer is B"
    for pattern in [
        r"\*\*([A-D])\*\*",  # **B**
        r"answer is[:\s]+([A-D])\b",  # answer is B
        r"answer[:\s]+([A-D])\b",  # answer: B
        r"correct answer is[:\s]+([A-D])\b",  # correct answer is B
        r"^\s*([A-D])\s*$",  # lone letter on its own line
        r"([A-D])\s*$",  # last letter at end of text
    ]:
        m = re.search(pattern, t, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).upper()
    # Last resort: final standalone letter in text
    matches = re.findall(r"\b([A-D])\b", t.upper())
    return matches[-1] if matches else ""


def _fake_method_names(all_symbols: set[str], exclude: set[str], n: int) -> list[str]:
    """Pick n real graph symbols that are NOT the correct answer — plausible distractors."""
    pool = list(all_symbols - exclude)
    random.shuffle(pool)
    return pool[:n]


# ---------------------------------------------------------------------------
# Individual tests
# ---------------------------------------------------------------------------


def _run_mc_round(
    client,
    model: str,
    stem: str,
    correct: str,
    distractors: list[str],
    jidra_ctx: str,
    naive_src: str,
    test_name: str,
    q_num: int,
) -> dict:
    """Run one multiple choice question against both contexts. Returns per-label results."""
    question, correct_letter = _make_mc_question(stem, correct, distractors)
    results = {}
    for label, context in [("traditional", naive_src), ("jidra", jidra_ctx)]:
        answer, inp, out = _call_claude(client, model, context, question)
        picked = _extract_mc_answer(answer)
        correct_flag = picked == correct_letter
        results[label] = {
            "correct": correct_flag,
            "picked": picked,
            "correct_letter": correct_letter,
            "correct_answer": correct,
            "input_tokens": inp,
            "output_tokens": out,
        }
        _debug(f"\n{'─' * 60}")
        _debug(f"TEST: {test_name}  Q{q_num}  [{label.upper()}]")
        _debug(f"QUESTION:\n{question}")
        _debug(f"MODEL RESPONSE: {answer.strip()}")
        _debug(
            f"PICKED: {picked}  CORRECT: {correct_letter} ({correct})  → {'✓' if correct_flag else '✗'}"
        )
    return results


def test_callee_mc(
    client,
    model,
    method_node,
    node_by_id,
    jidra_ctx,
    naive_src,
    method_name,
    all_symbols,
) -> dict:
    """Test 1 — Does the model know what this method calls?

    Multiple choice: given 1 real callee + 3 real-but-wrong distractors from the graph,
    pick the one that `method_name` actually calls.
    Scoring: % correct across N questions. Length-independent.
    """
    callees = list(_ground_truth_callees(method_node, node_by_id))
    if not callees:
        return {
            "test": "callee_mc",
            "method": method_name,
            "skipped": "no callees in graph",
        }

    random.shuffle(callees)
    questions_correct = {"traditional": 0, "jidra": 0}
    questions_total = 0
    all_q_results = []

    for i, correct_callee in enumerate(callees[:5]):  # up to 5 questions per method
        distractors = _fake_method_names(all_symbols, {correct_callee}, n=3)
        if len(distractors) < 3:
            continue
        stem = f"Which of the following methods does `{method_name}` directly call?"
        round_results = _run_mc_round(
            client,
            model,
            stem,
            correct_callee,
            distractors,
            jidra_ctx,
            naive_src,
            "callee_mc",
            i + 1,
        )
        questions_total += 1
        for label in ("traditional", "jidra"):
            if round_results[label]["correct"]:
                questions_correct[label] += 1
        all_q_results.append(round_results)

    accuracy = {
        label: round(questions_correct[label] / questions_total, 3)
        if questions_total
        else 0.0
        for label in ("traditional", "jidra")
    }
    return {
        "test": "callee_mc",
        "method": method_name,
        "questions": questions_total,
        "traditional": {
            "accuracy": accuracy["traditional"],
            "correct": questions_correct["traditional"],
        },
        "jidra": {"accuracy": accuracy["jidra"], "correct": questions_correct["jidra"]},
        "question_details": all_q_results,
    }


def test_caller_mc(
    client,
    model,
    method_node,
    node_by_id,
    jidra_ctx,
    naive_src,
    method_name,
    all_symbols,
) -> dict:
    """Test 2 — Does the model know what calls this method?

    Multiple choice: given 1 real caller + 3 real-but-wrong distractors,
    pick the one that actually calls `method_name`.
    """
    method_id = method_node.get("id", "")
    callers = list(_ground_truth_callers(method_id, node_by_id))
    if not callers:
        return {
            "test": "caller_mc",
            "method": method_name,
            "skipped": "no callers in graph",
        }

    random.shuffle(callers)
    questions_correct = {"traditional": 0, "jidra": 0}
    questions_total = 0
    all_q_results = []

    for i, correct_caller in enumerate(callers[:5]):
        distractors = _fake_method_names(all_symbols, {correct_caller}, n=3)
        if len(distractors) < 3:
            continue
        stem = f"Which of the following methods calls `{method_name}`?"
        round_results = _run_mc_round(
            client,
            model,
            stem,
            correct_caller,
            distractors,
            jidra_ctx,
            naive_src,
            "caller_mc",
            i + 1,
        )
        questions_total += 1
        for label in ("traditional", "jidra"):
            if round_results[label]["correct"]:
                questions_correct[label] += 1
        all_q_results.append(round_results)

    accuracy = {
        label: round(questions_correct[label] / questions_total, 3)
        if questions_total
        else 0.0
        for label in ("traditional", "jidra")
    }
    return {
        "test": "caller_mc",
        "method": method_name,
        "questions": questions_total,
        "traditional": {
            "accuracy": accuracy["traditional"],
            "correct": questions_correct["traditional"],
        },
        "jidra": {"accuracy": accuracy["jidra"], "correct": questions_correct["jidra"]},
        "question_details": all_q_results,
    }


def test_dependency_mc(
    client,
    model,
    method_node,
    node_by_id,
    jidra_ctx,
    naive_src,
    method_name,
    all_symbols,
) -> dict:
    """Test 3 — Does the model understand which component this method depends on?

    Multiple choice: given the method and 4 class names (1 real dependency class + 3 distractors),
    pick the one this method actually uses.
    """
    # Collect classes of callees as real dependencies
    dep_classes = set()
    for call in method_node.get("calls", []):
        target_id = call.get("target_id") if isinstance(call, dict) else call
        target = node_by_id.get(target_id)
        if target:
            cn = target.get("class_name") or ""
            if cn:
                dep_classes.add(cn)

    if not dep_classes:
        return {
            "test": "dependency_mc",
            "method": method_name,
            "skipped": "no dependency classes in graph",
        }

    # Distractor class names — real classes in graph but not dependencies
    all_classes = {
        n.get("class_name") for n in node_by_id.values() if n.get("class_name")
    }
    non_dep_classes = list(all_classes - dep_classes)

    dep_list = list(dep_classes)
    random.shuffle(dep_list)
    questions_correct = {"traditional": 0, "jidra": 0}
    questions_total = 0
    all_q_results = []

    for i, correct_class in enumerate(dep_list[:5]):
        distractors = random.sample(non_dep_classes, min(3, len(non_dep_classes)))
        if len(distractors) < 3:
            continue
        stem = (
            f"Which of the following classes does `{method_name}` directly depend on?"
        )
        round_results = _run_mc_round(
            client,
            model,
            stem,
            correct_class,
            distractors,
            jidra_ctx,
            naive_src,
            "dependency_mc",
            i + 1,
        )
        questions_total += 1
        for label in ("traditional", "jidra"):
            if round_results[label]["correct"]:
                questions_correct[label] += 1
        all_q_results.append(round_results)

    accuracy = {
        label: round(questions_correct[label] / questions_total, 3)
        if questions_total
        else 0.0
        for label in ("traditional", "jidra")
    }
    return {
        "test": "dependency_mc",
        "method": method_name,
        "questions": questions_total,
        "traditional": {
            "accuracy": accuracy["traditional"],
            "correct": questions_correct["traditional"],
        },
        "jidra": {"accuracy": accuracy["jidra"], "correct": questions_correct["jidra"]},
        "question_details": all_q_results,
    }


"""
Test framework symbols that are always valid — not hallucinations.
Filtering these out before scoring ensures we only flag truly invented project symbols.
"""
TEST_FRAMEWORK_SYMBOLS = {
    # JUnit 5
    "Test",
    "BeforeEach",
    "AfterEach",
    "BeforeAll",
    "AfterAll",
    "DisplayName",
    "ExtendWith",
    "Nested",
    "ParameterizedTest",
    "ValueSource",
    "CsvSource",
    "MethodSource",
    "EnumSource",
    "NullSource",
    "Disabled",
    "RepeatedTest",
    "TestFactory",
    "DynamicTest",
    "Assertions",
    "Assumptions",
    # Assertions
    "assertEquals",
    "assertNotNull",
    "assertNull",
    "assertTrue",
    "assertFalse",
    "assertThrows",
    "assertDoesNotThrow",
    "assertAll",
    "assertThat",
    "fail",
    "assertNotEquals",
    "assertSame",
    "assertNotSame",
    "assertInstanceOf",
    # Mockito
    "Mock",
    "InjectMocks",
    "Spy",
    "Captor",
    "MockitoExtension",
    "MockitoAnnotations",
    "when",
    "verify",
    "times",
    "never",
    "any",
    "anyString",
    "anyInt",
    "anyLong",
    "anyList",
    "anyMap",
    "doReturn",
    "doThrow",
    "doNothing",
    "thenReturn",
    "thenThrow",
    "thenAnswer",
    "given",
    "willReturn",
    "ArgumentCaptor",
    "Mockito",
    "BDDMockito",
    "eq",
    "isNull",
    "notNull",
    "contains",
    "matches",
    # Spring Test
    "SpringBootTest",
    "WebMvcTest",
    "MockBean",
    "SpyBean",
    "AutoConfigureMockMvc",
    "MockMvc",
    "perform",
    "andExpect",
    "status",
    "content",
    "jsonPath",
    # Common Java test types
    "List",
    "Optional",
    "String",
    "Map",
    "Collections",
    "Arrays",
    "RuntimeException",
    "Exception",
    "IllegalArgumentException",
    "NullPointerException",
    "IOException",
}


def test_unit_test_generation(
    client,
    model,
    method_node,
    node_by_id,
    jidra_ctx,
    naive_src,
    method_name,
    all_symbols,
    retrieved_symbols,
) -> dict:
    """Test 4: Do generated unit tests reference real project symbols vs invented ones?

    Scoring: extract identifiers from generated test, exclude known test framework symbols,
    then check remaining identifiers against graph or retrieved context. Remaining unknowns = fabricated project symbols.
    """
    question = (
        f"Write a JUnit 5 unit test class for `{method_name}`. "
        "Use Mockito for mocking. Include at least 3 test cases covering the happy path and edge cases."
    )
    results = {}
    for label, context in [("traditional", naive_src), ("jidra", jidra_ctx)]:
        answer, inp, out = _call_claude(client, model, context, question)
        _debug(f"\n{'─' * 60}")
        _debug(f"TEST: unit_test_generation  [{label.upper()}]")
        _debug(f"QUESTION: {question}")
        _debug(f"MODEL RESPONSE:\n{answer.strip()}")
        all_identifiers = _extract_java_identifiers(answer)
        # Remove known test framework symbols — these are valid, not hallucinations
        project_identifiers = all_identifiers - TEST_FRAMEWORK_SYMBOLS
        fabricated = project_identifiers - all_symbols
        real = project_identifiers & all_symbols
        fabrication_rate = (
            round(len(fabricated) / len(project_identifiers), 3)
            if project_identifiers
            else 0.0
        )
        results[label] = {
            "total_identifiers": len(all_identifiers),
            "project_identifiers": len(project_identifiers),
            "real_symbols": len(real),
            "fabricated_symbols": len(fabricated),
            "fabrication_rate": fabrication_rate,
            "fabricated_examples": sorted(fabricated)[:10],
            "input_tokens": inp,
            "output_tokens": out,
            "answer_preview": answer[:500],
        }
    return {"test": "unit_test_generation", "method": method_name, **results}


def hybrid_tests(
    client,
    model,
    method_node,
    node_by_id,
    jidra_ctx,
    naive_src,
    method_name,
    all_symbols,
) -> list[dict]:
    """Hybrid tests: natural language queries partially aligned with code."""
    questions = [
        f"What happens after `{method_name}` returns results?",
        f"Which service organizes results after `{method_name}` finishes?",
        f"Where does the response go after `{method_name}` emits results?",
        f"What downstream step runs after the main search processor returns data?",
    ]
    results = []
    for idx, question in enumerate(questions, 1):
        answer, inp, out = _call_claude(client, model, jidra_ctx, question)
        results.append(
            {
                "test": "hybrid_tests",
                "question": question,
                "answer_preview": answer[:300],
                "input_tokens": inp,
                "output_tokens": out,
            }
        )
    return results


def test_consistency_drift(
    client,
    model,
    method_node,
    node_by_id,
    jidra_ctx,
    naive_src,
    method_name,
    all_symbols,
) -> dict:
    """Test 5: Does the model give consistent answers across two separate calls?"""
    question = (
        f"Describe what `{method_name}` does and list its key dependencies. "
        "Be specific about method names and classes it interacts with."
    )
    results = {}
    for label, context in [("traditional", naive_src), ("jidra", jidra_ctx)]:
        # Two independent calls — simulates separate sessions
        answer_a, inp_a, out_a = _call_claude(client, model, context, question)
        _debug(f"\n{'─' * 60}")
        _debug(f"TEST: consistency_drift  [{label.upper()}]  RUN 1")
        _debug(f"QUESTION: {question}")
        _debug(f"MODEL RESPONSE:\n{answer_a.strip()}")
        time.sleep(1)
        answer_b, inp_b, out_b = _call_claude(client, model, context, question)
        _debug(f"\n{'─' * 60}")
        _debug(f"TEST: consistency_drift  [{label.upper()}]  RUN 2")
        _debug(f"MODEL RESPONSE:\n{answer_b.strip()}")

        # Only measure drift on graph-verifiable symbols — filters out generic English
        # and measures whether the model consistently references the SAME real code
        claims_a = _extract_java_identifiers(answer_a) & all_symbols
        claims_b = _extract_java_identifiers(answer_b) & all_symbols
        drift = _drift_score(claims_a, claims_b)

        _debug(
            f"DRIFT: {drift}  verified_run1={len(claims_a)}  verified_run2={len(claims_b)}"
        )
        _debug(f"STABLE: {sorted(claims_a & claims_b)[:10]}")
        _debug(f"DRIFTED: {sorted(claims_a.symmetric_difference(claims_b))[:10]}")

        results[label] = {
            "drift_score": drift,
            "verified_claims_run1": len(claims_a),
            "verified_claims_run2": len(claims_b),
            "drifted_claims": sorted(claims_a.symmetric_difference(claims_b))[:10],
            "stable_claims": sorted(claims_a & claims_b)[:10],
            "input_tokens_avg": (inp_a + inp_b) // 2,
            "answer_run1_preview": answer_a[:300],
            "answer_run2_preview": answer_b[:300],
        }
    return {"test": "consistency_drift", "method": method_name, **results}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="JIDRA hallucination & consistency validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--graph", required=True, help="Path to graph.db")
    parser.add_argument("--codebase", required=True, help="Path to Java repo root")

    method_group = parser.add_mutually_exclusive_group()
    method_group.add_argument(
        "--methods",
        help="Comma-separated method selectors, e.g. 'OrderController.createOrder,PaymentService.charge'",
    )
    method_group.add_argument(
        "--methods-file",
        metavar="FILE",
        help="Path to file with method selectors (one per line or JSON array)",
    )
    method_group.add_argument(
        "--auto-discover",
        action="store_true",
        help="Auto-discover methods from the graph (prefers REST endpoints)",
    )

    parser.add_argument(
        "--discover-limit",
        type=int,
        default=5,
        help="Number of methods to auto-discover (default: 5)",
    )
    parser.add_argument(
        "--model", default="claude-opus-4-7", help="Claude model to use"
    )
    parser.add_argument(
        "--tests",
        default="1,2,3,4,5",
        help="Comma-separated test numbers to run: 1=callee accuracy, 2=caller tracing, "
        "3=change impact, 4=unit test gen, 5=drift (default: all)",
    )
    parser.add_argument("--output", help="Write JSON results to this file")
    args = parser.parse_args()

    try:
        from anthropic import Anthropic
    except ImportError:
        sys.exit("anthropic package required: pip install anthropic")

    graph_path = Path(args.graph).resolve()
    codebase_path = Path(args.codebase).resolve()

    if not graph_path.exists():
        sys.exit(f"Graph not found: {graph_path}")
    if not codebase_path.exists():
        sys.exit(f"Codebase not found: {codebase_path}")

    run_tests = {int(t.strip()) for t in args.tests.split(",")}

    conn = graph_store.connect(graph_path)
    graph = graph_store.load_graph(conn, variant="validated")
    node_by_id = graph_store.load_nodes(conn, variant="validated")
    all_symbols = _all_graph_symbols(node_by_id)
    client = Anthropic()

    # Resolve method list
    if args.methods_file:
        methods = _load_methods_from_file(args.methods_file)
        print(f"Loaded {len(methods)} methods from {args.methods_file}")
    elif args.auto_discover:
        methods = _auto_discover_methods(graph, node_by_id, args.discover_limit)
        print(f"Auto-discovered {len(methods)} methods from graph")
    elif args.methods:
        methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    else:
        sys.exit(
            "No methods specified. Use --methods, --methods-file, or --auto-discover.\n"
            "Example: --methods 'OrderController.createOrder,PaymentService.charge'\n"
            "         --methods-file my_methods.txt\n"
            "         --auto-discover --discover-limit 5"
        )

    if not methods:
        sys.exit("Method list is empty.")

    print(f"\n{'=' * 70}")
    print("JIDRA Hallucination & Consistency Validation")
    print(f"{'=' * 70}")
    print(f"Model:   {args.model}")
    print(f"Methods: {len(methods)}")
    for m in methods:
        print(f"  • {m}")
    print(f"Tests:   {sorted(run_tests)}")
    print(f"{'=' * 70}\n")

    all_results = []

    for method_selector in methods:
        print(f"\n── {method_selector} ──")
        candidates = _resolve_method_selector(graph, method_selector)
        if not candidates:
            print(f"  ✗ Not found in graph, skipping")
            continue
        if len(candidates) > 1:
            print(f"  ✗ Ambiguous ({len(candidates)} matches), skipping")
            continue

        method = candidates[0]
        method_node = node_by_id.get(method.id)
        if not method_node:
            print(f"  ✗ Node not found in raw graph, skipping")
            continue

        jidra_ctx = _build_jidra_context(method_node)
        retrieved_symbols = set(_extract_java_identifiers(str(jidra_ctx)))
        _, naive_src = _collect_naive_files(method_node, node_by_id, codebase_path)
        if not naive_src:
            print(f"  ✗ Could not read source files for naive context, skipping")
            continue

        method_name = method_node.get("method_name") or method_selector.split(".")[-1]
        method_results = {"method": method_selector, "tests": []}

        test_fns = {
            1: lambda: test_callee_mc(
                client,
                args.model,
                method_node,
                node_by_id,
                jidra_ctx,
                naive_src,
                method_name,
                all_symbols,
            ),
            2: lambda: test_caller_mc(
                client,
                args.model,
                method_node,
                node_by_id,
                jidra_ctx,
                naive_src,
                method_name,
                all_symbols,
            ),
            3: lambda: test_dependency_mc(
                client,
                args.model,
                method_node,
                node_by_id,
                jidra_ctx,
                naive_src,
                method_name,
                all_symbols,
            ),
            4: lambda: test_unit_test_generation(
                client,
                args.model,
                method_node,
                node_by_id,
                jidra_ctx,
                naive_src,
                method_name,
                all_symbols,
            ),
            5: lambda: test_consistency_drift(
                client,
                args.model,
                method_node,
                node_by_id,
                jidra_ctx,
                naive_src,
                method_name,
                all_symbols,
            ),
        }

        test_labels = {
            1: "Callee identification (MC)",
            2: "Caller identification (MC)",
            3: "Dependency identification (MC)",
            4: "Unit test generation",
            5: "Consistency / drift",
        }

        for test_num in sorted(run_tests):
            if test_num not in test_fns:
                continue
            print(f"  [{test_num}] {test_labels[test_num]}...", flush=True)
            try:
                result = test_fns[test_num]()
                method_results["tests"].append(result)
                _print_test_summary(result)
            except Exception as e:
                print(f"      ✗ Error: {e}")
                method_results["tests"].append(
                    {"test": test_labels[test_num], "error": str(e)}
                )

        all_results.append(method_results)

    _print_aggregate_summary(all_results, run_tests)

    print(f"\n{'=' * 70}")
    print("Cost Summary")
    print(f"{'=' * 70}")
    print(f"Total API calls:    {_total_input_tokens and 'see below'}")
    print(f"Total input tokens: {_total_input_tokens:,}")
    print(f"Total output tokens:{_total_output_tokens:,}")
    print(f"Total cost:         ${_total_cost:.4f}")
    print(f"{'=' * 70}\n")

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(all_results, indent=2))
        print(f"Results written to: {args.output}")
        _flush_debug(args.output)


def _print_test_summary(result: dict) -> None:
    test = result.get("test", "")
    trad = result.get("traditional", {})
    jidra = result.get("jidra", {})

    if test in ("callee_mc", "caller_mc", "dependency_mc"):
        q = result.get("questions", 0)
        print(
            f"      Traditional — accuracy={trad.get('accuracy', 0):.2f}  ({trad.get('correct', 0)}/{q} correct)"
        )
        print(
            f"      JIDRA       — accuracy={jidra.get('accuracy', 0):.2f}  ({jidra.get('correct', 0)}/{q} correct)"
        )
    elif test == "unit_test_generation":
        print(
            f"      Traditional — fabrication_rate={trad.get('fabrication_rate'):.2f}  ({trad.get('fabricated_symbols')} fabricated / {trad.get('total_identifiers')} total)"
        )
        print(
            f"      JIDRA       — fabrication_rate={jidra.get('fabrication_rate'):.2f}  ({jidra.get('fabricated_symbols')} fabricated / {jidra.get('total_identifiers')} total)"
        )
    elif test == "consistency_drift":
        print(f"      Traditional — drift={trad.get('drift_score'):.2f}")
        print(f"      JIDRA       — drift={jidra.get('drift_score'):.2f}")


def _print_aggregate_summary(all_results: list, run_tests: set) -> None:
    print(f"\n{'=' * 70}")
    print("Aggregate Summary")
    print(f"{'=' * 70}")

    metrics = {
        "accuracy": {"traditional": [], "jidra": []},
        "fabrication_rate": {"traditional": [], "jidra": []},
        "drift_score": {"traditional": [], "jidra": []},
    }

    test_metric_map = {
        "callee_mc": "accuracy",
        "caller_mc": "accuracy",
        "dependency_mc": "accuracy",
        "unit_test_generation": "fabrication_rate",
        "consistency_drift": "drift_score",
    }

    for method_result in all_results:
        for test in method_result.get("tests", []):
            metric_key = test_metric_map.get(test.get("test", ""))
            if not metric_key:
                continue
            for label in ("traditional", "jidra"):
                val = test.get(label, {}).get(metric_key)
                if val is not None:
                    metrics[metric_key][label].append(val)

    # For accuracy: higher JIDRA is better. For fabrication/drift: lower JIDRA is better.
    higher_is_better = {"accuracy"}

    for metric, values in metrics.items():
        t_vals = values["traditional"]
        j_vals = values["jidra"]
        if not t_vals:
            continue
        t_avg = sum(t_vals) / len(t_vals)
        j_avg = sum(j_vals) / len(j_vals)
        if metric in higher_is_better:
            improvement = round((j_avg - t_avg) / max(t_avg, 0.001) * 100, 1)
        else:
            improvement = round((t_avg - j_avg) / max(t_avg, 0.001) * 100, 1)
        print(
            f"{metric:<22}  traditional={t_avg:.3f}  jidra={j_avg:.3f}  improvement={improvement:+.1f}%"
        )

    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    main()
