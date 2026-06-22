from jidra.context_builder import build_method_context
from jidra.models import Graph, MethodEntry, ClassEntry, CallSite


def _long_method_source(n_lines: int = 300) -> str:
    body_lines = [f"    int x{i} = {i};" for i in range(n_lines - 2)]
    lines = ["public void longMethod() {"] + body_lines + ["    return;", "}"]
    return "\n".join(lines)


def _graph_with_long_method(source: str) -> Graph:
    cls = ClassEntry(
        id="cls_1",
        package_name="com.example",
        name="Big",
        full_name="com.example.Big",
        file_path="src/main/java/com/example/Big.java",
        start_line=1,
        end_line=400,
    )
    method = MethodEntry(
        id="m_1",
        class_id="cls_1",
        class_full_name="com.example.Big",
        method_name="longMethod",
        return_type="void",
        parameter_types=[],
        parameter_names=[],
        signature="com.example.Big#longMethod()",
        file_path="src/main/java/com/example/Big.java",
        start_line=1,
        end_line=400,
        source=source,
        class_context={},
    )
    return Graph(
        classes=[cls],
        methods=[method],
        fields=[],
        callsites=[],
        inheritance_edges=[],
        resolved_call_edges=[],
    )


def test_truncated_method_source_preserves_tail():
    source = _long_method_source(300)
    graph = _graph_with_long_method(source)

    ctx = build_method_context(graph, "m_1", max_chars=4000)

    truncated = ctx["method_source"]
    assert len(str(ctx)) <= 4000 or len(truncated) <= 4000 // 3
    assert "return;" in truncated
    assert truncated.rstrip().endswith("}")
    assert "lines omitted" in truncated


def test_short_method_source_unmodified():
    source = "public void shortMethod() {\n    return;\n}"
    graph = _graph_with_long_method(source)

    ctx = build_method_context(graph, "m_1", max_chars=12000)

    assert ctx["method_source"] == source


def _graph_with_callsite(file_path: str, receiver: str, callee_name: str) -> Graph:
    cls = ClassEntry(
        id="cls_1",
        package_name="com.example",
        name="Thing",
        full_name="com.example.Thing",
        file_path=file_path,
        start_line=1,
        end_line=10,
    )
    method = MethodEntry(
        id="m_1",
        class_id="cls_1",
        class_full_name="com.example.Thing",
        method_name="doStuff",
        return_type="void",
        parameter_types=[],
        parameter_names=[],
        signature="com.example.Thing#doStuff()",
        file_path=file_path,
        start_line=1,
        end_line=10,
        source=f"{receiver}.{callee_name}();",
        class_context={},
    )
    callsite = CallSite(
        id="cs_1",
        caller_method_id="m_1",
        callee_name=callee_name,
        receiver=receiver,
        argument_count=0,
        file_path=file_path,
        line=2,
        column=1,
        text=f"{receiver}.{callee_name}()",
    )
    return Graph(
        classes=[cls],
        methods=[method],
        fields=[],
        callsites=[callsite],
        inheritance_edges=[],
        resolved_call_edges=[],
    )


def test_python_logging_calls_filtered():
    graph = _graph_with_callsite("src/app/service.py", "logging", "getLogger")
    ctx = build_method_context(graph, "m_1")
    assert ctx["unresolved_calls"] == []


def test_typescript_console_calls_filtered():
    graph = _graph_with_callsite("src/app/service.ts", "console", "log")
    ctx = build_method_context(graph, "m_1")
    assert ctx["unresolved_calls"] == []


def test_java_business_call_not_filtered():
    graph = _graph_with_callsite(
        "src/main/java/com/example/Thing.java", "logging", "getLogger"
    )
    ctx = build_method_context(graph, "m_1")
    assert len(ctx["unresolved_calls"]) == 1


def test_java_slf4j_still_filtered():
    graph = _graph_with_callsite(
        "src/main/java/com/example/Thing.java", "logger", "info"
    )
    ctx = build_method_context(graph, "m_1")
    assert ctx["unresolved_calls"] == []
