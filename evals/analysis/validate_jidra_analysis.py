#!/usr/bin/env python3
"""Compare Claude API with/without JIDRA tools available.

Let Claude decide which JIDRA tools to call, measuring the real benefit
of having tools available vs raw queries.
"""

import sys
import time
from dataclasses import dataclass
from typing import Any

try:
    from tabulate import tabulate
except ImportError:
    print("Error: tabulate package required. Install with: pip install tabulate")
    sys.exit(1)

try:
    from anthropic import Anthropic
except ImportError:
    print("Error: anthropic package required. Install with: pip install anthropic")
    sys.exit(1)


@dataclass
class QueryMetrics:
    """Metrics from a single Claude API call."""

    name: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int = 0
    stop_reason: str = ""
    cost: float = 0.0
    latency_s: float = 0.0
    response_preview: str = ""
    response_full: str = ""
    tool_calls: int = 0


@dataclass
class ComparisonResult:
    """Side-by-side comparison of with-tools vs without-tools."""

    query: str
    with_tools_metrics: QueryMetrics
    without_tools_metrics: QueryMetrics

    def format_comparison(self) -> str:
        """Format results as readable comparison table."""
        w = self.with_tools_metrics
        n = self.without_tools_metrics

        reduction_pct = (
            (n.input_tokens - w.input_tokens) / n.input_tokens * 100
            if n.input_tokens > 0
            else 0.0
        )
        savings = n.cost - w.cost
        savings_pct = (savings / n.cost * 100) if n.cost > 0 else 0.0

        rows = [
            [
                "Input Tokens",
                f"{w.input_tokens:,}",
                f"{n.input_tokens:,}",
                f"{w.input_tokens - n.input_tokens:,} ({reduction_pct:.1f}%)",
            ],
            [
                "Output Tokens",
                f"{w.output_tokens:,}",
                f"{n.output_tokens:,}",
                f"{w.output_tokens - n.output_tokens:,}",
            ],
            ["Tool Calls", f"{w.tool_calls}", f"{n.tool_calls}", "-"],
            [
                "Cost",
                f"${w.cost:.6f}",
                f"${n.cost:.6f}",
                f"${savings:.6f} ({savings_pct:.1f}%)",
            ],
            [
                "Latency (s)",
                f"{w.latency_s:.2f}s",
                f"{n.latency_s:.2f}s",
                f"{w.latency_s - n.latency_s:.2f}s",
            ],
        ]

        headers = ["Metric", "With Tools", "Without Tools", "Difference"]
        return tabulate(rows, headers=headers, tablefmt="grid")


def get_llm_pricing(model: str) -> dict[str, float]:
    """Get pricing for a model ($/1M tokens)."""
    pricing = {
        "claude-opus-4-7": {"input": 15.0, "output": 45.0},
        "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
        "claude-haiku-4-5": {"input": 0.80, "output": 4.0},
    }
    if model not in pricing:
        raise ValueError(f"Unknown model: {model}. Available: {list(pricing.keys())}")
    return pricing[model]


def calculate_cost(
    model: str, input_tokens: int, output_tokens: int, thinking_tokens: int = 0
) -> float:
    """Calculate cost in USD."""
    pricing = get_llm_pricing(model)
    thinking_cost = (thinking_tokens * pricing["output"] * 3) if thinking_tokens else 0
    return (
        input_tokens * pricing["input"]
        + output_tokens * pricing["output"]
        + thinking_cost
    ) / 1_000_000


def get_jidra_tools() -> list[dict[str, Any]]:
    """Get JIDRA tools schema for Claude to use."""
    return [
        {
            "name": "jidra_get_method_context",
            "description": "Get context for a specific method including source code, calls, and dependencies",
            "input_schema": {
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "description": "Method identifier or qualified name",
                    }
                },
                "required": ["method"],
            },
        },
        {
            "name": "jidra_get_flow",
            "description": "Get downstream call graph for a method",
            "input_schema": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "Method identifier"}
                },
                "required": ["method"],
            },
        },
        {
            "name": "jidra_get_call_chain",
            "description": "Find the call chain between two methods",
            "input_schema": {
                "type": "object",
                "properties": {
                    "from_method": {"type": "string", "description": "Starting method"},
                    "to_method": {"type": "string", "description": "Target method"},
                },
                "required": ["from_method", "to_method"],
            },
        },
    ]


def call_claude(
    client: Anthropic, model: str, query: str, use_tools: bool = True
) -> QueryMetrics:
    """Make Claude API call, optionally WITH JIDRA tools available."""
    start = time.time()

    kwargs = {
        "model": model,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": query}],
    }

    if use_tools:
        kwargs["tools"] = get_jidra_tools()

    message = client.messages.create(**kwargs)

    latency = time.time() - start
    usage = message.usage

    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    thinking_tokens = int(getattr(usage, "thinking_tokens", 0) or 0)

    cost = calculate_cost(model, input_tokens, output_tokens, thinking_tokens)

    response_preview = ""
    response_full = ""
    tool_calls = 0

    for block in message.content:
        if hasattr(block, "text"):
            response_full = block.text
            response_preview = block.text[:150]
        elif hasattr(block, "type") and block.type == "tool_use":
            tool_calls += 1

    return QueryMetrics(
        name="With Tools" if use_tools else "Without Tools",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        stop_reason=message.stop_reason or "",
        cost=cost,
        latency_s=latency,
        response_preview=response_preview,
        response_full=response_full,
        tool_calls=tool_calls,
    )


def validate_query(client: Anthropic, model: str, query: str) -> ComparisonResult:
    """Run query WITH and WITHOUT tools, compare results."""
    print(f"\n{'=' * 70}")
    print(f"Query: {query[:80]}...")
    print(f"{'=' * 70}\n")

    print("[1/2] Calling Claude WITH JIDRA tools available...")
    with_tools = call_claude(client, model, query, use_tools=True)
    print(
        f"      ✓ {with_tools.input_tokens:,} input, {with_tools.output_tokens:,} output, "
        f"{with_tools.tool_calls} tool call(s), ${with_tools.cost:.6f}"
    )

    print("\n[2/2] Calling Claude WITHOUT tools...")
    without_tools = call_claude(client, model, query, use_tools=False)
    print(
        f"      ✓ {without_tools.input_tokens:,} input, {without_tools.output_tokens:,} output, "
        f"${without_tools.cost:.6f}"
    )

    return ComparisonResult(
        query=query,
        with_tools_metrics=with_tools,
        without_tools_metrics=without_tools,
    )


def get_sample_queries() -> list[str]:
    """Sample queries that benefit from JIDRA tools."""
    return [
        "Analyze the PaymentService#processPayment method. What are the potential issues?",
        "What is the call flow for UserService#authenticate? Show me all dependencies.",
        "Trace the call chain from OrderService#checkout to Database#query",
        "Find all methods that call PaymentGateway#charge and analyze error handling",
        "Identify performance bottlenecks in SearchService#executeQuery",
    ]


def main():
    """Interactive validator loop."""
    import os
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="JIDRA Tool Effectiveness Validator - Compare Claude API with/without JIDRA tools"
    )
    parser.add_argument(
        "--codebase",
        type=Path,
        help="Path to codebase to analyze (e.g., /path/to/ai_watchtower)",
    )
    parser.add_argument(
        "--model",
        default="claude-opus-4-7",
        choices=["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"],
        help="LLM model to use",
    )
    args = parser.parse_args()

    auth_token = os.getenv("ANTHROPIC_AUTH_TOKEN")
    base_url = os.getenv("ANTHROPIC_BASE_URL")

    if not auth_token:
        print("Error: ANTHROPIC_AUTH_TOKEN not set")
        print("Set: export ANTHROPIC_AUTH_TOKEN=<your-token>")
        sys.exit(1)

    client = Anthropic(api_key=auth_token, base_url=base_url)
    model = args.model
    results: list[ComparisonResult] = []

    codebase_info = ""
    if args.codebase:
        codebase_info = f"\nCodebase: {args.codebase}"

    print("\n" + "=" * 70)
    print("JIDRA Tool Effectiveness Validator")
    print("=" * 70)
    print(f"\nModel: {model}")
    print(f"Endpoint: {base_url}")
    print(codebase_info)
    print("Compares Claude API WITH and WITHOUT JIDRA tools available.\n")

    while True:
        try:
            print("\nOptions:")
            print("  1. Enter a custom query")
            print("  2. Use sample query")
            print("  3. Show results summary")
            print("  4. Change model")
            print("  5. Exit")

            choice = input("\nChoice [1-5]: ").strip()

            if choice == "1":
                query = input("\nEnter your query: ").strip()
                if not query:
                    print("Query cannot be empty")
                    continue

            elif choice == "2":
                samples = get_sample_queries()
                print("\nSample queries:")
                for i, q in enumerate(samples, 1):
                    print(f"  {i}. {q[:60]}...")

                sample_choice = input("\nSelect [1-5]: ").strip()
                if sample_choice.isdigit() and 1 <= int(sample_choice) <= len(samples):
                    query = samples[int(sample_choice) - 1]
                else:
                    print("Invalid selection")
                    continue

            elif choice == "3":
                if not results:
                    print("\nNo results yet. Run some queries first.")
                    continue

                print("\n" + "=" * 70)
                print("RESULTS SUMMARY")
                print("=" * 70 + "\n")

                summary_rows = []
                for i, result in enumerate(results, 1):
                    w = result.with_tools_metrics
                    n = result.without_tools_metrics
                    reduction = (
                        (n.input_tokens - w.input_tokens) / n.input_tokens * 100
                        if n.input_tokens > 0
                        else 0
                    )
                    savings = n.cost - w.cost

                    summary_rows.append(
                        [
                            i,
                            result.query[:35] + "...",
                            f"{w.input_tokens:,}",
                            f"{n.input_tokens:,}",
                            f"{reduction:.1f}%",
                            f"{w.tool_calls}",
                            f"${savings:.6f}",
                        ]
                    )

                headers = [
                    "#",
                    "Query",
                    "With Tools",
                    "Without",
                    "Reduction",
                    "Tools Used",
                    "Savings",
                ]
                print(tabulate(summary_rows, headers=headers, tablefmt="grid"))

                total_reduction = sum(
                    (
                        r.without_tools_metrics.input_tokens
                        - r.with_tools_metrics.input_tokens
                    )
                    for r in results
                )
                total_savings = sum(
                    r.without_tools_metrics.cost - r.with_tools_metrics.cost
                    for r in results
                )
                total_tool_calls = sum(r.with_tools_metrics.tool_calls for r in results)

                print(f"\nTotal input tokens saved: {total_reduction:,}")
                print(f"Total cost savings: ${total_savings:.6f}")
                print(f"Total JIDRA tool calls made: {total_tool_calls}")
                continue

            elif choice == "4":
                print("\nAvailable models:")
                models = ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"]
                for i, m in enumerate(models, 1):
                    print(f"  {i}. {m}")
                model_choice = input("Select [1-3]: ").strip()
                if model_choice in ["1", "2", "3"]:
                    model = models[int(model_choice) - 1]
                    print(f"✓ Model changed to: {model}")
                continue

            elif choice == "5":
                print("\nExiting. Goodbye!")
                break
            else:
                print("Invalid choice. Try again.")
                continue

            try:
                result = validate_query(client, model, query)
                results.append(result)

                print("\n" + result.format_comparison())

                w = result.with_tools_metrics
                n = result.without_tools_metrics
                savings = n.cost - w.cost
                savings_pct = (savings / n.cost * 100) if n.cost > 0 else 0

                print("\n📊 Summary:")
                print(f"  With tools: ${w.cost:.6f} ({w.tool_calls} tools used)")
                print(f"  Without:    ${n.cost:.6f}")
                print(f"  Savings:    ${savings:.6f} ({savings_pct:.1f}%)")

                print(f"\n{'=' * 70}")
                print("RESPONSES")
                print(f"{'=' * 70}")

                print(f"\n🔧 WITH JIDRA TOOLS ({w.tool_calls} tool call(s)):")
                print("-" * 70)
                print(w.response_full if w.response_full else "(No text response)")

                print("\n\n❌ WITHOUT TOOLS:")
                print("-" * 70)
                print(n.response_full if n.response_full else "(No text response)")

            except Exception as e:
                print(f"\n❌ Error during API call: {e}")

        except KeyboardInterrupt:
            print("\n\nInterrupted. Goodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")


if __name__ == "__main__":
    main()
