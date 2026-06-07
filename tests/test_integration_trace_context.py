from jidra.context_builder import build_method_context

from jidra.trace_engine import trace_method


def test_trace_method_smoke(sample_graph):
    methods = list(sample_graph.methods)
    assert methods
    m = methods[0]

    result = trace_method(sample_graph, m.id, max_depth=2)
    assert "error" not in result
    assert result["root"]["id"] == m.id
    assert isinstance(result.get("flow"), list)


def test_build_method_context_smoke(sample_graph):
    methods = list(sample_graph.methods)
    assert methods
    m = methods[0]

    ctx = build_method_context(sample_graph, m.id, max_chars=4000)
    assert "error" not in ctx
    # context_builder currently returns method_signature (not method_id)
    assert ctx.get("method_signature") == m.signature
