"""Tests for cost/ROI calculator."""

import pytest
from pathlib import Path
from jidra.cost_calculator import (
    CostCalculator,
    CostBreakdown,
    GraphStats,
    analyze_graph,
)

GRAPH_PATH = Path(__file__).parent.parent / "jidra/output/graph_validated.jsonl"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def calc():
    return CostCalculator()


@pytest.fixture
def real_stats():
    """GraphStats measured from the real graph_validated.jsonl."""
    if not GRAPH_PATH.exists():
        pytest.skip("graph_validated.jsonl not present")
    return analyze_graph(GRAPH_PATH)


@pytest.fixture
def synthetic_stats():
    """Deterministic stats for unit tests that don't need a real graph."""
    return GraphStats(
        num_classes=200,
        num_methods=800,
        num_endpoints=20,
        num_files=150,
        avg_jidra_tokens=600,
        avg_naive_tokens=6000,
        avg_calls_per_method=10.0,
        token_reduction_pct=90.0,
    )


@pytest.fixture
def small_stats():
    return GraphStats(
        num_classes=20,
        num_methods=80,
        num_endpoints=5,
        num_files=20,
        avg_jidra_tokens=300,
        avg_naive_tokens=1500,
        avg_calls_per_method=4.0,
        token_reduction_pct=80.0,
    )


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


class TestPricing:
    def test_get_known_model(self, calc):
        p = calc.get_llm_pricing("claude-opus-4-7")
        assert p["input"] > 0 and p["output"] > 0

    def test_unknown_model_raises(self, calc):
        with pytest.raises(ValueError, match="Unknown model"):
            calc.get_llm_pricing("nonexistent-model")

    def test_opus_more_expensive_than_haiku(self, calc):
        opus = calc.get_llm_pricing("claude-opus-4-7")
        haiku = calc.get_llm_pricing("claude-haiku-4-5")
        assert opus["input"] > haiku["input"]
        assert opus["output"] > haiku["output"]

    def test_query_cost_scales_with_tokens(self, calc):
        low = calc.calculate_query_cost("claude-sonnet-4-6", 1_000, 500)
        high = calc.calculate_query_cost("claude-sonnet-4-6", 10_000, 500)
        assert high > low


# ---------------------------------------------------------------------------
# analyze_graph — real file
# ---------------------------------------------------------------------------


class TestAnalyzeGraph:
    def test_returns_graph_stats(self, real_stats):
        assert isinstance(real_stats, GraphStats)

    def test_counts_are_positive(self, real_stats):
        assert real_stats.num_classes > 0
        assert real_stats.num_methods > 0
        assert real_stats.num_files > 0

    def test_jidra_tokens_measured(self, real_stats):
        assert real_stats.avg_jidra_tokens > 0

    def test_multi_file_naive_larger_than_single_jidra(self, real_stats):
        # The whole point: naive multi-file context > focused JIDRA response
        assert real_stats.avg_naive_tokens > real_stats.avg_jidra_tokens

    def test_reduction_pct_is_meaningful(self, real_stats):
        # Should be positive and significant for a real call-chain query
        assert real_stats.token_reduction_pct > 0

    def test_missing_graph_raises(self, calc):
        with pytest.raises(FileNotFoundError):
            analyze_graph(Path("/nonexistent/graph.jsonl"))


# ---------------------------------------------------------------------------
# CostBreakdown — synthetic stats
# ---------------------------------------------------------------------------


class TestCostBreakdown:
    def test_with_jidra_cheaper(self, calc, synthetic_stats):
        bd = calc.calculate_cost_breakdown("claude-opus-4-7", synthetic_stats)
        assert isinstance(bd, CostBreakdown)
        assert bd.without_jidra > bd.with_jidra
        assert bd.savings_per_query > 0

    def test_savings_ratio_reflects_reduction(self, calc, synthetic_stats):
        bd = calc.calculate_cost_breakdown("claude-opus-4-7", synthetic_stats)
        # 90% token reduction → significant cost savings (output tokens unchanged)
        assert bd.savings_ratio > 50

    def test_expensive_model_bigger_absolute_savings(self, calc, synthetic_stats):
        opus = calc.calculate_cost_breakdown("claude-opus-4-7", synthetic_stats)
        haiku = calc.calculate_cost_breakdown("claude-haiku-4-5", synthetic_stats)
        assert opus.savings_per_query > haiku.savings_per_query

    def test_savings_ratio_zero_for_equal_costs(self, calc):
        equal_stats = GraphStats(
            num_classes=10,
            num_methods=50,
            num_endpoints=2,
            num_files=10,
            avg_jidra_tokens=1000,
            avg_naive_tokens=1000,
            avg_calls_per_method=1.0,
            token_reduction_pct=0.0,
        )
        bd = calc.calculate_cost_breakdown("claude-sonnet-4-6", equal_stats)
        assert bd.savings_ratio == 0.0

    def test_small_codebase_still_shows_savings(self, calc, small_stats):
        bd = calc.calculate_cost_breakdown("claude-sonnet-4-6", small_stats)
        assert bd.savings_per_query > 0


# ---------------------------------------------------------------------------
# ROIAnalysis
# ---------------------------------------------------------------------------


class TestROIAnalysis:
    def test_annual_savings_scales_with_queries(self, calc, synthetic_stats):
        roi_100 = calc.calculate_roi("claude-opus-4-7", synthetic_stats, 100)
        roi_1000 = calc.calculate_roi("claude-opus-4-7", synthetic_stats, 1000)
        assert roi_1000.annual_savings == pytest.approx(
            roi_100.annual_savings * 10, rel=0.01
        )

    def test_payback_calculated_when_costs_given(self, calc, synthetic_stats):
        roi = calc.calculate_roi(
            "claude-opus-4-7",
            synthetic_stats,
            1000,
            jidra_setup_cost=100.0,
            jidra_annual_cost=10.0,
        )
        assert roi.payback_months is not None
        assert roi.payback_months > 0

    def test_payback_none_when_no_setup_cost(self, calc, synthetic_stats):
        roi = calc.calculate_roi("claude-opus-4-7", synthetic_stats, 500)
        assert roi.payback_months is None

    def test_negative_roi_when_costs_exceed_savings(self, calc, small_stats):
        roi = calc.calculate_roi(
            "claude-haiku-4-5",
            small_stats,
            10,
            jidra_setup_cost=100_000.0,
            jidra_annual_cost=50_000.0,
        )
        assert roi.year_1_roi_pct is not None
        assert roi.year_1_roi_pct < 0

    def test_graph_stats_embedded_in_roi(self, calc, synthetic_stats):
        roi = calc.calculate_roi("claude-sonnet-4-6", synthetic_stats, 200)
        assert roi.graph_stats is synthetic_stats


# ---------------------------------------------------------------------------
# End-to-end with real graph
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_real_graph_produces_positive_savings(self, calc, real_stats):
        roi = calc.calculate_roi("claude-sonnet-4-6", real_stats, 500)
        assert roi.annual_savings > 0

    def test_real_graph_opus_saves_more_than_haiku(self, calc, real_stats):
        opus = calc.calculate_roi("claude-opus-4-7", real_stats, 500)
        haiku = calc.calculate_roi("claude-haiku-4-5", real_stats, 500)
        assert opus.annual_savings > haiku.annual_savings
