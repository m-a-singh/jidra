"""Regression tests for jidra.py_extractor."""

from jidra.py_extractor import build_py_graph


CLASS_METHOD_SAMPLE = """from django.views import View


class HomeView(View):
    def get(self, request):
        return None

    async def post(self, request):
        return None


def standalone():
    return 1
"""


def test_class_methods_are_not_duplicated(tmp_path):
    """Each method on a class must yield exactly one MethodEntry, not a real
    method plus a phantom module-level function (regression for a bug where
    visit_ClassDef's generic_visit re-triggered visit_FunctionDef)."""
    (tmp_path / "views.py").write_text(CLASS_METHOD_SAMPLE)
    graph = build_py_graph(tmp_path, enable_validation=False)

    get_methods = [m for m in graph.methods if m.method_name == "get"]
    post_methods = [m for m in graph.methods if m.method_name == "post"]
    standalone_methods = [m for m in graph.methods if m.method_name == "standalone"]

    assert len(get_methods) == 1
    assert get_methods[0].class_full_name == "views.HomeView"

    assert len(post_methods) == 1
    assert post_methods[0].class_full_name == "views.HomeView"

    # The genuine top-level function should still be extracted exactly once.
    assert len(standalone_methods) == 1
    assert standalone_methods[0].class_full_name == "views"
