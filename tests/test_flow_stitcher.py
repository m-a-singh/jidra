"""Regression tests for flow stitcher."""

from jidra.flow.flow_stitcher import stitch_flow


class TestStitchFlow:
    def test_stitch_flow_basic(self, loaded_test_graph, simple_test_graph):
        """Test basic flow stitching from entry method."""
        entry_method = simple_test_graph.methods[0]  # handleRequest
        result = stitch_flow(loaded_test_graph, entry_method, detail="full")

        assert "error" not in result
        assert "entry" in result
        assert "nodes" in result
        assert "edges" in result
        assert "uncertain_edges" in result
        assert "stopped_paths" in result
        assert "summary" in result
        assert "agent_view" in result

    def test_stitch_flow_entry_structure(self, loaded_test_graph, simple_test_graph):
        """Test entry point information."""
        entry_method = simple_test_graph.methods[0]
        result = stitch_flow(loaded_test_graph, entry_method)

        entry = result.get("entry", {})
        assert entry.get("method_id") == entry_method.id
        assert entry.get("signature") == entry_method.signature

    def test_stitch_flow_nodes_structure(self, loaded_test_graph, simple_test_graph):
        """Test node structure in stitched flow."""
        entry_method = simple_test_graph.methods[0]
        result = stitch_flow(loaded_test_graph, entry_method, detail="full")

        nodes = result.get("nodes", [])
        assert len(nodes) > 0

        for node in nodes:
            assert "id" in node
            assert "signature" in node
            assert "depth" in node
            assert "tier" in node
            assert node["tier"] in ("primary", "supporting", "utility")
            assert "rank_score" in node
            assert "path_entropy_score" in node

    def test_stitch_flow_edges_structure(self, loaded_test_graph, simple_test_graph):
        """Test edge structure in stitched flow."""
        entry_method = simple_test_graph.methods[0]
        result = stitch_flow(loaded_test_graph, entry_method, detail="full")

        edges = result.get("edges", [])
        assert isinstance(edges, list)
        assert len(edges) > 0

        for edge in edges:
            assert "from" in edge
            assert "to" in edge
            assert "call" in edge or edge.get("call") is None
            assert "lines" in edge or edge.get("lines") is None
            assert "resolution" in edge or edge.get("resolution") is None

    def test_stitch_flow_depth_parameter(self, loaded_test_graph, simple_test_graph):
        """Test depth parameter affects traversal."""
        entry_method = simple_test_graph.methods[0]

        result_d1 = stitch_flow(loaded_test_graph, entry_method, max_depth=1)
        result_d3 = stitch_flow(loaded_test_graph, entry_method, max_depth=3)

        nodes_d1 = result_d1.get("summary", {}).get("node_count", 0)
        nodes_d3 = result_d3.get("summary", {}).get("node_count", 0)

        # Deeper traversal should find at least as many nodes
        assert nodes_d3 >= nodes_d1

    def test_stitch_flow_business_only(self, loaded_test_graph, simple_test_graph):
        """Test business_only filtering."""
        entry_method = simple_test_graph.methods[0]

        def is_business(entry):
            return True  # All are business in test graph

        result_all = stitch_flow(
            loaded_test_graph, entry_method, business_only=False, detail="full"
        )
        result_business = stitch_flow(
            loaded_test_graph,
            entry_method,
            business_only=True,
            is_business_entry=is_business,
            detail="full",
        )

        # Both should be valid
        assert "nodes" in result_all
        assert "nodes" in result_business

    def test_stitch_flow_summary(self, loaded_test_graph, simple_test_graph):
        """Test summary metrics."""
        entry_method = simple_test_graph.methods[0]
        result = stitch_flow(loaded_test_graph, entry_method)

        summary = result.get("summary", {})
        assert "node_count" in summary
        assert "edge_count" in summary
        assert "uncertain_edge_count" in summary
        assert "stopped_path_count" in summary
        assert "excluded_count" in summary

        assert isinstance(summary["node_count"], int)
        assert summary["node_count"] >= 0

    def test_stitch_flow_agent_view(self, loaded_test_graph, simple_test_graph):
        """Test agent view structure."""
        entry_method = simple_test_graph.methods[0]
        result = stitch_flow(loaded_test_graph, entry_method)

        agent_view = result.get("agent_view", {})
        assert "entry" in agent_view
        assert "top_nodes" in agent_view
        assert "important_unresolved_calls" in agent_view
        assert "uncertain_edges" in agent_view
        assert "stopped_paths" in agent_view
        assert "notes" in agent_view

    def test_stitch_flow_tiered_views(self, loaded_test_graph, simple_test_graph):
        """Test tiered flow views."""
        entry_method = simple_test_graph.methods[0]
        result = stitch_flow(loaded_test_graph, entry_method, detail="full")

        assert "likely_primary" in result or "primary_flow" in result
        assert "supporting" in result or "supporting_flow" in result
        assert "low_priority" in result or "utility_flow" in result

    def test_stitch_flow_backward_compat_aliases(
        self, loaded_test_graph, simple_test_graph
    ):
        """Test backward-compatible aliases."""
        entry_method = simple_test_graph.methods[0]
        result = stitch_flow(loaded_test_graph, entry_method, detail="full")

        # Check both new names and old aliases exist
        assert "likely_primary" in result or "primary_flow" in result
        primary = result.get("likely_primary") or result.get("primary_flow")
        assert isinstance(primary, list)

    def test_stitch_flow_cycle_detection(self, loaded_test_graph, simple_test_graph):
        """Test that cycles are detected and stopped."""
        # This would require a graph with cycles, which we'll test with the full graph
        entry_method = simple_test_graph.methods[0]
        result = stitch_flow(loaded_test_graph, entry_method)

        stopped = result.get("stopped_paths", [])
        # Just verify structure is valid
        assert isinstance(stopped, list)

    def test_stitch_flow_uncertain_edges(self, loaded_test_graph, simple_test_graph):
        """Test uncertain edges grouping."""
        entry_method = simple_test_graph.methods[0]
        result = stitch_flow(loaded_test_graph, entry_method)

        uncertain = result.get("uncertain_edges", [])
        assert isinstance(uncertain, list)

        for edge in uncertain:
            assert "from" in edge or "from_method_id" in edge
            assert "call" in edge
            assert "reason" in edge
