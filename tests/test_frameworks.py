"""Phase 4 — framework-aware extraction + endpoint/component queries."""

from pathlib import Path

import pytest

from jidra.graph import graph_store
from jidra.engine.engine import JidraEngine
from jidra.extractors.extractor import detect_frameworks
from jidra.models import ClassEntry, Graph, MethodEntry


PY_SAMPLE = """from fastapi import APIRouter
from flask import Blueprint
from django.views import View
from django.db import models

router = APIRouter()
bp = Blueprint("x", __name__)


@router.get("/items")
def read_item(id):
    return id


@bp.route("/login", methods=["POST"])
def login():
    return "ok"


class Article(models.Model):
    pass


class HomeView(View):
    def get(self, request):
        return None
"""


@pytest.fixture
def py_graph(tmp_path):
    (tmp_path / "app.py").write_text(PY_SAMPLE)
    from jidra.extractors.py_extractor import build_py_graph

    return build_py_graph(tmp_path)


class TestPythonExtraction:
    def test_fastapi_route(self, py_graph):
        m = next(m for m in py_graph.methods if m.method_name == "read_item")
        assert m.framework_role == "fastapi_route"
        assert m.is_endpoint and m.http_method == "GET" and m.route == "/items"

    def test_flask_route(self, py_graph):
        m = next(m for m in py_graph.methods if m.method_name == "login")
        assert m.framework_role == "flask_route"
        assert m.http_method == "POST"

    def test_django_model_and_view(self, py_graph):
        article = next(c for c in py_graph.classes if c.name == "Article")
        home = next(c for c in py_graph.classes if c.name == "HomeView")
        assert "django_model" in article.stereotypes
        assert "django_view" in home.stereotypes

    def test_django_handler(self, py_graph):
        handlers = [m for m in py_graph.methods if m.framework_role == "django_handler"]
        assert any(m.method_name == "get" for m in handlers)


class TestDetectFrameworks:
    def test_reads_requirements(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("fastapi==0.1\nflask\n")
        assert detect_frameworks(tmp_path) == {"fastapi", "flask"}

    def test_reads_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text('{"dependencies":{"react":"18"}}')
        assert "react" in detect_frameworks(tmp_path)


class TestEngineQueries:
    def _engine(self, tmp_path):
        db = tmp_path / "graph.db"
        conn = graph_store.connect(db)
        cls = ClassEntry(
            id="c1",
            package_name="ui",
            name="Button",
            full_name="ui.Button",
            file_path="ui/Button.tsx",
            start_line=1,
            end_line=5,
            stereotypes=["react_component"],
            language="typescript",
        )
        endpoint = MethodEntry(
            id="m1",
            class_id="c1",
            class_full_name="ui.Button",
            method_name="read",
            return_type="x",
            parameter_types=[],
            parameter_names=[],
            signature="read()",
            file_path="api.py",
            start_line=1,
            end_line=2,
            source="",
            class_context={},
            language="python",
            framework_role="fastapi_route",
            is_endpoint=True,
            http_method="GET",
            route="/r",
        )
        hook = MethodEntry(
            id="m2",
            class_id="c1",
            class_full_name="ui.Button",
            method_name="useAuth",
            return_type="x",
            parameter_types=[],
            parameter_names=[],
            signature="useAuth()",
            file_path="ui/useAuth.ts",
            start_line=1,
            end_line=2,
            source="",
            class_context={},
            language="typescript",
            framework_role="hook",
        )
        graph_store.save_full_graph(
            conn, Graph([cls], [endpoint, hook], [], [], [], [])
        )
        conn.close()
        return JidraEngine(str(db))

    def test_get_endpoints(self, tmp_path):
        result = self._engine(tmp_path).get_endpoints()
        assert result["count"] == 1
        assert result["endpoints"][0]["framework_role"] == "fastapi_route"

    def test_get_endpoints_framework_filter(self, tmp_path):
        engine = self._engine(tmp_path)
        assert engine.get_endpoints(framework="fastapi")["count"] == 1
        assert engine.get_endpoints(framework="spring")["count"] == 0

    def test_get_components(self, tmp_path):
        result = self._engine(tmp_path).get_components()
        names = {c["name"] for c in result["components"]}
        assert "ui.Button" in names  # class component
        assert "useAuth" in names  # method hook

    def test_framework_summary(self, tmp_path):
        summary = self._engine(tmp_path).get_framework_summary()
        assert summary["endpoints_total"] == 1
        assert summary["framework_roles"]["fastapi_route"] == 1
        assert summary["class_stereotypes"]["react_component"] == 1


class TestMethodRoundTrip:
    def test_framework_role_persists(self, tmp_path):
        db = tmp_path / "graph.db"
        conn = graph_store.connect(db)
        m = MethodEntry(
            id="m1",
            class_id="c1",
            class_full_name="A",
            method_name="onEvent",
            return_type="void",
            parameter_types=[],
            parameter_names=[],
            signature="void onEvent()",
            file_path="A.java",
            start_line=1,
            end_line=2,
            source="",
            class_context={},
            language="java",
            framework_role="event_listener",
        )
        graph_store.save_full_graph(conn, Graph([], [m], [], [], [], []))
        conn.close()
        loaded = graph_store.load_graph(graph_store.connect(Path(db)), variant="main")
        assert loaded.methods[0].framework_role == "event_listener"
