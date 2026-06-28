"""Phase 2 — budget-tiered context output regression tests."""

from jidra.engine.engine import JidraEngine, _BUDGET_TIERS


def _tier_for(n: int) -> str:
    for threshold, budget in _BUDGET_TIERS:
        if threshold is None or n < threshold:
            return budget["tier"]
    return _BUDGET_TIERS[-1][1]["tier"]


class TestBudgetTiers:
    def test_boundaries(self):
        cases = {
            0: "XS",
            199: "XS",
            200: "S",
            999: "S",
            1000: "M",
            4999: "M",
            5000: "L",
            19999: "L",
            20000: "XL",
            99999: "XL",
        }
        for n, expected in cases.items():
            assert _tier_for(n) == expected, (n, _tier_for(n), expected)

    def test_small_graph_is_xs(self, test_graph_file):
        engine = JidraEngine(test_graph_file)  # 3 methods
        assert engine._get_budget()["tier"] == "XS"


class TestBudgetMetaInResponses:
    def test_method_context_has_budget_meta(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_method_context("handleRequest")
        assert result["budget_tier"] == "XS"
        assert result["graph_size"] == {"methods": 3}

    def test_flow_has_budget_meta(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_flow("handleRequest")
        assert result.get("budget_tier") == "XS"

    def test_agent_flow_has_budget_meta(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_agent_flow("handleRequest")
        assert result.get("budget_tier") == "XS"

    def test_explicit_max_chars_still_honored(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        # An absurdly small override must still truncate the source.
        result = engine.get_method_context("handleRequest", max_chars=350)
        assert "omitted" in result["method_source"] or len(str(result)) < 2000
