import pytest
import tempfile
from pathlib import Path
import json

from jidra.models import Graph, MethodEntry, ClassEntry, CallSite, ResolvedCallEdge
from jidra.graph_io import load_graph_jsonl
from jidra.exporter import export_jsonl, graph_records


@pytest.fixture
def simple_test_graph():
    """Create a minimal test graph fixture."""
    graph = Graph(
        classes=[
            ClassEntry(
                id="cls_1",
                package_name="com.example",
                name="TestController",
                full_name="com.example.TestController",
                file_path="src/main/java/com/example/TestController.java",
                start_line=1,
                end_line=20,
            ),
            ClassEntry(
                id="cls_2",
                package_name="com.example",
                name="TestService",
                full_name="com.example.TestService",
                file_path="src/main/java/com/example/TestService.java",
                start_line=1,
                end_line=30,
            ),
            ClassEntry(
                id="cls_3",
                package_name="com.example",
                name="TestRepository",
                full_name="com.example.TestRepository",
                file_path="src/main/java/com/example/TestRepository.java",
                start_line=1,
                end_line=40,
            ),
        ],
        methods=[
            MethodEntry(
                id="m_1",
                class_id="cls_1",
                class_full_name="com.example.TestController",
                method_name="handleRequest",
                return_type="String",
                parameter_types=["String"],
                parameter_names=["id"],
                signature="com.example.TestController#handleRequest(String)",
                file_path="src/main/java/com/example/TestController.java",
                start_line=10,
                end_line=15,
                source="public String handleRequest(String id) { return service.process(id); }",
                class_context={},
            ),
            MethodEntry(
                id="m_2",
                class_id="cls_2",
                class_full_name="com.example.TestService",
                method_name="process",
                return_type="String",
                parameter_types=["String"],
                parameter_names=["id"],
                signature="com.example.TestService#process(String)",
                file_path="src/main/java/com/example/TestService.java",
                start_line=20,
                end_line=25,
                source="public String process(String id) { return repo.fetch(id); }",
                class_context={},
            ),
            MethodEntry(
                id="m_3",
                class_id="cls_3",
                class_full_name="com.example.TestRepository",
                method_name="fetch",
                return_type="String",
                parameter_types=["String"],
                parameter_names=["id"],
                signature="com.example.TestRepository#fetch(String)",
                file_path="src/main/java/com/example/TestRepository.java",
                start_line=30,
                end_line=35,
                source="public String fetch(String id) { return id; }",
                class_context={},
            ),
        ],
        fields=[],
        callsites=[
            CallSite(
                id="cs_1",
                caller_method_id="m_1",
                callee_name="process",
                receiver="service",
                argument_count=1,
                file_path="src/main/java/com/example/TestController.java",
                line=11,
                column=20,
                text="service.process(id)",
                receiver_type="com.example.TestService",
                resolved_candidates=["m_2"],
                resolution_status="resolved",
            ),
            CallSite(
                id="cs_2",
                caller_method_id="m_2",
                callee_name="fetch",
                receiver="repo",
                argument_count=1,
                file_path="src/main/java/com/example/TestService.java",
                line=21,
                column=20,
                text="repo.fetch(id)",
                receiver_type="com.example.TestRepository",
                resolved_candidates=["m_3"],
                resolution_status="resolved",
            ),
        ],
        inheritance_edges=[],
        resolved_call_edges=[
            ResolvedCallEdge(
                id="edge_1",
                callsite_id="cs_1",
                caller_method_id="m_1",
                callee_method_id="m_2",
            ),
            ResolvedCallEdge(
                id="edge_2",
                callsite_id="cs_2",
                caller_method_id="m_2",
                callee_method_id="m_3",
            ),
        ],
    )
    return graph


@pytest.fixture
def test_graph_file(simple_test_graph, tmp_path):
    """Write test graph to a temporary JSONL file and return its path."""
    graph_file = tmp_path / "test_graph.jsonl"
    records = graph_records(simple_test_graph)
    export_jsonl(graph_file, records)
    return str(graph_file)


@pytest.fixture
def loaded_test_graph(test_graph_file):
    """Load the test graph from file."""
    return load_graph_jsonl(Path(test_graph_file))
