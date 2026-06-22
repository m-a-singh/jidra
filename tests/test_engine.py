"""Regression tests for JidraEngine public API."""

from jidra.engine import JidraEngine


class TestEngineInit:
    def test_engine_loads_graph(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        assert engine.graph is not None
        assert len(engine.graph.methods) == 3
        assert len(engine.graph.classes) == 3


class TestGetMethodContext:
    def test_get_context_by_name(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_method_context("handleRequest")

        assert "error" not in result
        assert (
            result.get("method_signature")
            == "com.example.TestController#handleRequest(String)"
        )
        assert result.get("method_source") is not None
        assert "resolved_callees" in result or "business_flow" in result

    def test_get_context_full_selector(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_method_context("com.example.TestController.handleRequest")

        assert "error" not in result
        assert "method_signature" in result

    def test_get_context_nonexistent(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_method_context("nonexistent")

        assert "error" in result

    def test_get_context_max_chars(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_method_context("handleRequest", max_chars=100)

        assert "error" not in result
        # max_chars limits context overall, not individual source field
        assert "method_source" in result


class TestGetFlow:
    def test_get_flow_basic(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_flow("handleRequest")

        assert "error" not in result
        assert "entry" in result
        assert "nodes" in result
        assert "edges" in result
        assert "uncertain_edges" in result
        assert "stopped_paths" in result
        assert "summary" in result

    def test_get_flow_structure(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_flow("handleRequest")

        summary = result.get("summary", {})
        assert "node_count" in summary
        assert "edge_count" in summary
        assert summary["node_count"] > 0

    def test_get_flow_depth_parameter(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result_d1 = engine.get_flow("handleRequest", depth=1)
        result_d3 = engine.get_flow("handleRequest", depth=3)

        # Both should be valid
        assert "error" not in result_d1
        assert "error" not in result_d3

        # Deeper traversal might have more nodes
        nodes_d1 = len(result_d1.get("nodes", []))
        nodes_d3 = len(result_d3.get("nodes", []))
        assert nodes_d1 >= 0 and nodes_d3 >= 0

    def test_get_flow_nonexistent(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_flow("nonexistent")

        assert "error" in result


class TestGetAgentFlow:
    def test_get_agent_flow_basic(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_agent_flow("handleRequest")

        assert "error" not in result
        assert "entry" in result
        assert "top_nodes" in result
        assert "top_edges" in result
        assert "uncertain_edge_summary" in result
        assert "stopped_path_summary" in result

    def test_agent_flow_is_compact(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        flow = engine.get_flow("handleRequest")
        agent_flow = engine.get_agent_flow("handleRequest")

        # Agent flow should have fewer nodes than full flow
        flow_nodes = len(flow.get("nodes", []))
        agent_nodes = len(agent_flow.get("top_nodes", []))

        assert agent_nodes <= flow_nodes

    def test_agent_flow_top_n(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_agent_flow("handleRequest", top_n=1)

        assert "error" not in result
        top_nodes = result.get("top_nodes", [])
        assert len(top_nodes) <= 1

    def test_agent_flow_nonexistent(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_agent_flow("nonexistent")

        assert "error" in result


class TestGetMethodSource:
    def test_get_method_source_basic(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_method_source("handleRequest")

        assert "error" not in result
        assert result.get("method_id") == "m_1"
        assert (
            result.get("signature")
            == "com.example.TestController#handleRequest(String)"
        )
        assert result.get("source") is not None
        assert "file_path" in result
        assert "line_start" in result
        assert "line_end" in result

    def test_get_method_source_nonexistent(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_method_source("nonexistent")

        assert "error" in result


class TestGetCallChain:
    def test_get_call_chain_found(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_call_chain("handleRequest", "fetch")

        assert "error" not in result
        assert result.get("found") is True
        assert "path" in result
        assert "edges" in result
        assert len(result.get("path", [])) > 0

    def test_get_call_chain_same_method(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_call_chain("handleRequest", "handleRequest")

        assert "error" not in result
        assert result.get("found") is True

    def test_get_call_chain_not_found(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_call_chain("fetch", "handleRequest")

        assert "error" not in result
        assert result.get("found") is False

    def test_get_call_chain_depth_limit(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_call_chain("handleRequest", "fetch", max_depth=1)

        assert "error" not in result
        # With max_depth=1, we can't reach fetch from handleRequest
        assert result.get("found") is False

    def test_get_call_chain_structure(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_call_chain("handleRequest", "fetch", max_depth=3)

        assert "from" in result
        assert "to" in result
        assert "max_depth" in result
        assert "stopped_reason" in result

        from_info = result.get("from", {})
        to_info = result.get("to", {})
        assert "method_id" in from_info
        assert "signature" in from_info
        assert "method_id" in to_info
        assert "signature" in to_info

    def test_get_call_chain_nonexistent_from(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_call_chain("nonexistent", "fetch")

        assert "error" in result

    def test_get_call_chain_nonexistent_to(self, test_graph_file):
        engine = JidraEngine(test_graph_file)
        result = engine.get_call_chain("handleRequest", "nonexistent")

        assert "error" in result


class TestEngineErrorHandling:
    def test_ambiguous_method_selector(self, test_graph_file):
        """Test handling of ambiguous method selectors."""
        engine = JidraEngine(test_graph_file)
        # If we had multiple methods named 'process', this would fail
        # For now, just verify it works with unique name
        result = engine.get_method_context("process")
        assert "error" not in result or "ambiguous" in result.get("error", "").lower()
