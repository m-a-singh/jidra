"""JIDRA Cost/ROI Calculator — derived from actual graph_validated.jsonl, not estimates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# LLM Pricing (as of June 2026) in $/1M tokens
LLM_PRICING = {
    "claude-opus-4-7": {"input": 15.0, "output": 45.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.0},
    "gpt-4-turbo": {"input": 10.0, "output": 30.0},
    "gpt-4o": {"input": 2.50, "output": 10.0},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.0},
}

_CHARS_PER_TOKEN = 4

_ANALYSIS_QUESTION = """Analyze this method:
1. What does it do? Describe the main processing steps.
2. Identify 2-3 potential performance issues or bottlenecks.
3. How does it interact with other components?

Be technical and specific."""


@dataclass
class GraphStats:
    """Facts measured directly from graph_validated.jsonl."""

    num_classes: int
    num_methods: int
    num_endpoints: int
    num_files: int
    avg_jidra_tokens: int
    avg_naive_tokens: int
    avg_calls_per_method: float
    token_reduction_pct: float


@dataclass
class MethodProof:
    """Token measurement for a specific method — offline (no API calls)."""

    method_qualified_name: str
    method_file: str
    method_lines: str  # e.g. "345-568"

    # JIDRA side
    jidra_tokens: int
    jidra_context_preview: str  # first 200 chars of what JIDRA returns

    # Naive side
    naive_tokens: int
    naive_files: list[str]  # source files included in naive context

    # Derived
    token_reduction_pct: float

    # Per-query cost (set after model is known)
    model: str = ""
    cost_without_jidra: float = 0.0
    cost_with_jidra: float = 0.0
    savings_per_query: float = 0.0
    annual_savings: float = 0.0  # savings_per_query * num_queries


@dataclass
class MethodProofOnline(MethodProof):
    """Extends MethodProof with real Claude API measurements."""

    # Traditional (raw source)
    api_traditional_input_tokens: int = 0
    api_traditional_output_tokens: int = 0
    api_traditional_cost: float = 0.0
    api_traditional_answer: str = ""

    # JIDRA (graph context)
    api_jidra_input_tokens: int = 0
    api_jidra_output_tokens: int = 0
    api_jidra_cost: float = 0.0
    api_jidra_answer_preview: str = ""

    # Real measured reduction (from API, not chars/4 estimate)
    api_token_reduction_pct: float = 0.0
    api_savings_per_query: float = 0.0
    api_annual_savings: float = 0.0


@dataclass
class CostBreakdown:
    """Per-query cost comparison (graph-wide averages)."""

    model: str
    without_jidra: float
    with_jidra: float
    savings_per_query: float

    @property
    def savings_ratio(self) -> float:
        if self.without_jidra == 0:
            return 0.0
        return (self.savings_per_query / self.without_jidra) * 100


@dataclass
class ROIAnalysis:
    """Full annual ROI analysis (graph-wide averages)."""

    model: str
    num_queries: int
    graph_stats: GraphStats

    cost_without_jidra: float
    cost_with_jidra: float
    annual_savings: float

    jidra_setup_cost: float
    jidra_annual_cost: float

    payback_months: Optional[float]
    year_1_roi_pct: Optional[float]

    def __post_init__(self):
        if self.jidra_annual_cost > 0:
            net_savings = self.annual_savings - self.jidra_annual_cost
            if net_savings > 0 and self.jidra_setup_cost > 0:
                self.payback_months = (self.jidra_setup_cost / net_savings) * 12
            if self.annual_savings > 0:
                net_year1 = net_savings - self.jidra_setup_cost
                self.year_1_roi_pct = (
                    net_year1 / (self.jidra_setup_cost + self.jidra_annual_cost)
                ) * 100


def _chars_to_tokens(chars: int) -> int:
    return max(1, chars // _CHARS_PER_TOKEN)


def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = LLM_PRICING.get(model, {"input": 3.0, "output": 15.0})
    return (
        input_tokens * pricing["input"] + output_tokens * pricing["output"]
    ) / 1_000_000


def _build_jidra_context(method_node: dict) -> str:
    """Build the JIDRA tool response for a method node — what Claude actually receives."""
    p = method_node.get("payload", {})
    return json.dumps(
        {
            "method": method_node.get("qualified_name"),
            "source": p.get("source", ""),
            "class_context": p.get("class_context", ""),
            "calls": method_node.get("calls", [])[:10],
            "called_by": method_node.get("called_by", [])[:10],
        },
        indent=2,
    )


def _collect_naive_files(
    method_node: dict,
    node_by_id: dict,
    codebase: Path | None,
) -> tuple[list[str], str]:
    """
    Collect source files needed for a naive (no-JIDRA) context.
    Returns (list_of_file_paths, concatenated_source).

    Walks the method's call edges and collects the source file of the method
    itself plus the files of all direct callees — the same files you'd paste
    into context without JIDRA.
    """
    file_paths: dict[str, str] = {}  # path -> source

    def _read_file(graph_path: str) -> str:
        """Try to read a source file, remapping paths if codebase is given."""
        if not graph_path:
            return ""
        p = Path(graph_path)
        if p.exists():
            try:
                return p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                return ""
        if codebase:
            # Try to remap: find the part after the repo root by matching
            # the codebase directory name somewhere in the path
            parts = p.parts
            codebase_parts = codebase.resolve().parts
            for i, part in enumerate(parts):
                if part == codebase_parts[-1]:
                    relative = Path(*parts[i + 1 :])
                    remapped = codebase / relative
                    if remapped.exists():
                        try:
                            return remapped.read_text(encoding="utf-8", errors="ignore")
                        except Exception:
                            return ""
        return ""

    # The method's own file
    own_file = method_node.get("file_path", "")
    src = _read_file(own_file)
    if src:
        file_paths[own_file] = src
    elif own_file:
        # Fall back to source from graph payload if file is not readable
        file_paths[own_file] = method_node.get("payload", {}).get("source", "")

    # Files of direct callees — calls is a list of call objects with target_id
    for call in method_node.get("calls", []):
        callee_id = call.get("target_id") if isinstance(call, dict) else call
        if not callee_id:
            continue
        callee = node_by_id.get(callee_id)
        if not callee:
            continue
        callee_file = callee.get("file_path", "")
        if callee_file and callee_file not in file_paths:
            src = _read_file(callee_file)
            if src:
                file_paths[callee_file] = src
            elif callee_file:
                file_paths[callee_file] = callee.get("payload", {}).get("source", "")

    naive_source = "\n\n".join(
        f"// File: {fp}\n{src}" for fp, src in file_paths.items() if src
    )
    return list(file_paths.keys()), naive_source


def analyze_method_offline(
    graph_path: Path,
    method_selector: str,
    model: str,
    num_queries: int,
    codebase: Path | None = None,
) -> MethodProof:
    """
    Measure token costs for a specific method without making API calls.
    Uses chars/4 approximation for token counting.
    """
    from .selector import _resolve_method_selector
    from .graph_io import load_graph_jsonl

    graph = load_graph_jsonl(graph_path)
    candidates = _resolve_method_selector(graph, method_selector)
    if not candidates:
        raise ValueError(f"No method matched selector: {method_selector!r}")
    if len(candidates) > 1:
        names = [getattr(m, "qualified_name", m.id) for m in candidates[:5]]
        raise ValueError(
            f"Ambiguous selector {method_selector!r} — {len(candidates)} matches. "
            f"Be more specific. Candidates: {names}"
        )
    method = candidates[0]

    # Reload as raw dicts so we can walk node structure
    nodes = [
        json.loads(line) for line in graph_path.read_text().splitlines() if line.strip()
    ]
    node_by_id = {n.get("id"): n for n in nodes}
    method_node = node_by_id.get(method.id)
    if not method_node:
        raise ValueError(f"Method node {method.id} not found in raw graph")

    # JIDRA context
    jidra_ctx = _build_jidra_context(method_node)
    jidra_tokens = _chars_to_tokens(len(jidra_ctx))

    # Naive context (raw source files)
    naive_files, naive_source = _collect_naive_files(method_node, node_by_id, codebase)
    naive_tokens = (
        _chars_to_tokens(len(naive_source))
        if naive_source
        else _chars_to_tokens(
            sum(
                len(
                    node_by_id.get(c.get("target_id") if isinstance(c, dict) else c, {})
                    .get("payload", {})
                    .get("source", "")
                )
                for c in method_node.get("calls", [])
            )
            + len(method_node.get("payload", {}).get("source", ""))
        )
    )

    reduction_pct = (
        (naive_tokens - jidra_tokens) / naive_tokens * 100
        if naive_tokens > jidra_tokens
        else 0.0
    )

    start = method_node.get("start_line", "?")
    end = method_node.get("end_line", "?")

    # Cost per query (assume ~800 output tokens — consistent with empirical tests)
    cost_without = _calc_cost(model, naive_tokens, 800)
    cost_with = _calc_cost(model, jidra_tokens, 800)

    return MethodProof(
        method_qualified_name=method_node.get("qualified_name", method.id),
        method_file=method_node.get("file_path", ""),
        method_lines=f"{start}-{end}",
        jidra_tokens=jidra_tokens,
        jidra_context_preview=jidra_ctx[:200],
        naive_tokens=naive_tokens,
        naive_files=naive_files,
        token_reduction_pct=round(reduction_pct, 1),
        model=model,
        cost_without_jidra=cost_without,
        cost_with_jidra=cost_with,
        savings_per_query=cost_without - cost_with,
        annual_savings=(cost_without - cost_with) * num_queries,
    )


def analyze_method_online(
    graph_path: Path,
    method_selector: str,
    model: str,
    num_queries: int,
    codebase: Path | None = None,
) -> MethodProofOnline:
    """
    Measure token costs using real Claude API calls — exact numbers.
    Requires ANTHROPIC_API_KEY in environment.
    Mirrors the empirical_proof_test.py approach.
    """
    import time

    try:
        from anthropic import Anthropic
    except ImportError:
        raise RuntimeError(
            "anthropic package required for online mode: pip install anthropic"
        )

    # Run offline first to get contexts
    offline = analyze_method_offline(
        graph_path, method_selector, model, num_queries, codebase
    )

    # Re-derive the actual context strings
    from .graph_io import load_graph_jsonl

    nodes = [
        json.loads(line) for line in graph_path.read_text().splitlines() if line.strip()
    ]
    node_by_id = {n.get("id"): n for n in nodes}

    from .selector import _resolve_method_selector

    graph = load_graph_jsonl(graph_path)
    candidates = _resolve_method_selector(graph, method_selector)
    method_node = node_by_id.get(candidates[0].id)

    jidra_ctx = _build_jidra_context(method_node)
    _, naive_source = _collect_naive_files(method_node, node_by_id, codebase)

    if not naive_source:
        raise RuntimeError(
            "Could not read source files for naive context. "
            "Provide --codebase pointing to the Java repo root."
        )

    client = Anthropic()
    question = f"Analyze the {method_node.get('method_name', 'method')} method:\n{_ANALYSIS_QUESTION}"

    def _call(context: str) -> tuple[int, int, float, str]:
        start = time.time()
        resp = client.messages.create(
            model=model,
            max_tokens=800,
            messages=[
                {"role": "user", "content": f"CONTEXT:\n{context}\n\n{question}"}
            ],
        )
        elapsed = time.time() - start
        inp = resp.usage.input_tokens
        out = resp.usage.output_tokens
        cost = _calc_cost(model, inp, out)
        answer = resp.content[0].text if resp.content else ""
        print(f"  {elapsed:.1f}s  input={inp}  output={out}  cost=${cost:.5f}")
        return inp, out, cost, answer

    print(
        f"\nCalling Claude API — Traditional context ({offline.naive_tokens} estimated tokens)..."
    )
    trad_in, trad_out, trad_cost, trad_answer = _call(naive_source)

    print(
        f"Calling Claude API — JIDRA context ({offline.jidra_tokens} estimated tokens)..."
    )
    jidra_in, jidra_out, jidra_cost, jidra_answer = _call(jidra_ctx)

    api_reduction = (trad_in - jidra_in) / trad_in * 100 if trad_in > jidra_in else 0.0
    api_savings_per_query = trad_cost - jidra_cost

    return MethodProofOnline(
        # offline fields
        method_qualified_name=offline.method_qualified_name,
        method_file=offline.method_file,
        method_lines=offline.method_lines,
        jidra_tokens=offline.jidra_tokens,
        jidra_context_preview=offline.jidra_context_preview,
        naive_tokens=offline.naive_tokens,
        naive_files=offline.naive_files,
        token_reduction_pct=offline.token_reduction_pct,
        model=model,
        cost_without_jidra=offline.cost_without_jidra,
        cost_with_jidra=offline.cost_with_jidra,
        savings_per_query=offline.savings_per_query,
        annual_savings=offline.annual_savings,
        # online fields
        api_traditional_input_tokens=trad_in,
        api_traditional_output_tokens=trad_out,
        api_traditional_cost=trad_cost,
        api_traditional_answer=trad_answer,
        api_jidra_input_tokens=jidra_in,
        api_jidra_output_tokens=jidra_out,
        api_jidra_cost=jidra_cost,
        api_jidra_answer_preview=jidra_answer[:300],
        api_token_reduction_pct=round(api_reduction, 1),
        api_savings_per_query=api_savings_per_query,
        api_annual_savings=api_savings_per_query * num_queries,
    )


def analyze_graph(graph_path: Path) -> GraphStats:
    """
    Read graph_validated.jsonl and measure real average token costs across all methods.
    Use analyze_method_offline() for a specific method instead.
    """
    nodes = [
        json.loads(line) for line in graph_path.read_text().splitlines() if line.strip()
    ]

    method_nodes = [n for n in nodes if n.get("node_type") == "method"]
    class_nodes = [n for n in nodes if n.get("node_type") == "class"]

    jidra_token_sizes = []
    for m in method_nodes:
        jidra_token_sizes.append(_chars_to_tokens(len(_build_jidra_context(m))))

    avg_jidra = (
        sum(jidra_token_sizes) // len(jidra_token_sizes) if jidra_token_sizes else 0
    )

    file_sources: dict[str, list[str]] = {}
    for m in method_nodes:
        fp = m.get("file_path") or "unknown"
        src = m.get("payload", {}).get("source", "")
        file_sources.setdefault(fp, []).append(src)

    file_token_sizes = [
        _chars_to_tokens(sum(len(s) for s in srcs)) for srcs in file_sources.values()
    ]
    avg_file_tokens = (
        sum(file_token_sizes) // len(file_token_sizes) if file_token_sizes else 0
    )

    calls_counts = [len(m.get("calls", [])) for m in method_nodes if m.get("calls")]
    avg_calls = sum(calls_counts) / len(calls_counts) if calls_counts else 1.0
    avg_naive = int(avg_calls * avg_file_tokens)

    reduction_pct = (
        (avg_naive - avg_jidra) / avg_naive * 100 if avg_naive > avg_jidra else 0.0
    )

    return GraphStats(
        num_classes=len(class_nodes),
        num_methods=len(method_nodes),
        num_endpoints=sum(1 for m in method_nodes if m.get("is_endpoint")),
        num_files=len(file_sources),
        avg_jidra_tokens=avg_jidra,
        avg_naive_tokens=avg_naive,
        avg_calls_per_method=round(avg_calls, 1),
        token_reduction_pct=round(reduction_pct, 1),
    )


class CostCalculator:
    """Calculate JIDRA cost savings from a real graph file."""

    def __init__(self):
        self.pricing = LLM_PRICING

    def get_llm_pricing(self, model: str) -> dict[str, float]:
        if model not in self.pricing:
            raise ValueError(
                f"Unknown model: {model}. Available: {list(self.pricing.keys())}"
            )
        return self.pricing[model]

    def calculate_query_cost(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> float:
        pricing = self.get_llm_pricing(model)
        return (
            input_tokens * pricing["input"] + output_tokens * pricing["output"]
        ) / 1_000_000

    def calculate_cost_breakdown(
        self, model: str, stats: GraphStats, avg_output_tokens: int = 1000
    ) -> CostBreakdown:
        cost_without = self.calculate_query_cost(
            model, stats.avg_naive_tokens, avg_output_tokens
        )
        cost_with = self.calculate_query_cost(
            model, stats.avg_jidra_tokens, avg_output_tokens
        )
        return CostBreakdown(
            model=model,
            without_jidra=cost_without,
            with_jidra=cost_with,
            savings_per_query=cost_without - cost_with,
        )

    def calculate_roi(
        self,
        model: str,
        stats: GraphStats,
        num_queries_per_year: int,
        avg_output_tokens: int = 1000,
        jidra_setup_cost: float = 0.0,
        jidra_annual_cost: float = 0.0,
    ) -> ROIAnalysis:
        breakdown = self.calculate_cost_breakdown(model, stats, avg_output_tokens)
        annual_without = breakdown.without_jidra * num_queries_per_year
        annual_with = breakdown.with_jidra * num_queries_per_year
        return ROIAnalysis(
            model=model,
            num_queries=num_queries_per_year,
            graph_stats=stats,
            cost_without_jidra=annual_without,
            cost_with_jidra=annual_with,
            annual_savings=annual_without - annual_with,
            jidra_setup_cost=jidra_setup_cost,
            jidra_annual_cost=jidra_annual_cost,
            payback_months=None,
            year_1_roi_pct=None,
        )


def format_currency(amount: float) -> str:
    return f"${amount:,.4f}"


def format_method_proof(proof: MethodProof) -> str:
    is_online = isinstance(proof, MethodProofOnline)
    lines = [
        "",
        "=" * 70,
        "JIDRA Cost/ROI — Method Proof",
        "=" * 70,
        f"Method:   {proof.method_qualified_name}",
        f"Location: {Path(proof.method_file).name}:{proof.method_lines}",
        f"Model:    {proof.model}",
        "",
    ]

    if is_online:
        lines += [
            "Token Measurement  (REAL — from Claude API)",
            "-" * 70,
            f"Without JIDRA: {proof.api_traditional_input_tokens:,} input tokens",
            f"  ({len(proof.naive_files)} source files concatenated)",
            f"With JIDRA:    {proof.api_jidra_input_tokens:,} input tokens",
            "  (jidra_get_method_context response)",
            f"Reduction:     {proof.api_token_reduction_pct:.1f}%",
            "",
            "Cost Per Query  (REAL)",
            "-" * 70,
            f"Without JIDRA: {format_currency(proof.api_traditional_cost)}",
            f"With JIDRA:    {format_currency(proof.api_jidra_cost)}",
            f"Savings:       {format_currency(proof.api_savings_per_query)}",
            "",
            f"Annual Savings ({proof.annual_savings / proof.savings_per_query if proof.savings_per_query else 0:.0f} queries): "
            f"{format_currency(proof.api_annual_savings)}",
        ]
    else:
        lines += [
            "Token Measurement  (estimated, chars/4)",
            "-" * 70,
            f"Without JIDRA: {proof.naive_tokens:,} tokens",
            f"  ({len(proof.naive_files)} source file(s): {', '.join(Path(f).name for f in proof.naive_files[:3])}{'...' if len(proof.naive_files) > 3 else ''})",
            f"With JIDRA:    {proof.jidra_tokens:,} tokens",
            "  (jidra_get_method_context response)",
            f"Reduction:     {proof.token_reduction_pct:.1f}%",
            "",
            "Cost Per Query  (estimated)",
            "-" * 70,
            f"Without JIDRA: {format_currency(proof.cost_without_jidra)}",
            f"With JIDRA:    {format_currency(proof.cost_with_jidra)}",
            f"Savings:       {format_currency(proof.savings_per_query)}",
            "",
            f"Annual Savings ({int(proof.annual_savings / proof.savings_per_query) if proof.savings_per_query else 0} queries): "
            f"{format_currency(proof.annual_savings)}",
        ]

    lines += ["=" * 70, ""]
    return "\n".join(lines)


def format_stats(stats: GraphStats) -> str:
    lines = [
        "",
        "=" * 70,
        "Codebase Analysis (from graph_validated.jsonl)",
        "=" * 70,
        f"Classes:               {stats.num_classes:,}",
        f"Methods:               {stats.num_methods:,}",
        f"Endpoints:             {stats.num_endpoints:,}",
        f"Source Files:          {stats.num_files:,}",
        f"Avg calls/method:      {stats.avg_calls_per_method}",
        "",
        "Average Token Cost Per Query  (across all methods)",
        "-" * 70,
        f"Without JIDRA (naive): {stats.avg_naive_tokens:,} tokens",
        f"  (avg {stats.avg_calls_per_method} files × avg file size)",
        f"With JIDRA:            {stats.avg_jidra_tokens:,} tokens",
        f"Token Reduction:       {stats.token_reduction_pct:.1f}%",
        "=" * 70,
        "",
    ]
    return "\n".join(lines)


def format_metrics(roi: ROIAnalysis) -> str:
    lines = [
        "",
        "=" * 70,
        "JIDRA Annual Cost Projection  (graph-wide averages)",
        "=" * 70,
        f"Model:          {roi.model}",
        f"Annual Queries: {roi.num_queries:,}",
        f"Token Reduction:{roi.graph_stats.token_reduction_pct:.1f}%",
        "",
        "Annual LLM Costs",
        "-" * 70,
        f"Without JIDRA:  {format_currency(roi.cost_without_jidra)}",
        f"With JIDRA:     {format_currency(roi.cost_with_jidra)}",
        f"Annual Savings: {format_currency(roi.annual_savings)}",
        "=" * 70,
        "",
    ]
    return "\n".join(lines)
