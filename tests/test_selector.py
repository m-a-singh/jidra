import pytest


from jidra.selector import _resolve_method_selector





def test_resolve_method_selector_by_id(sample_graph):
    methods = list(sample_graph.methods)
    assert methods
    m = methods[0]
    out = _resolve_method_selector(sample_graph, m.id)
    assert out
    assert out[0].id == m.id


def test_resolve_method_selector_by_short_class_dot_method(sample_graph):
    methods = list(sample_graph.methods)
    assert methods
    m = methods[0]

    # selector supports short Class.method form
    class_leaf = m.class_full_name.split(".")[-1]
    selector = f"{class_leaf}.{m.method_name}"
    out = _resolve_method_selector(sample_graph, selector)
    assert out
    assert out[0].id == m.id
