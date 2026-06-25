"""Phase 7 — in-process tree-sitter TypeScript extraction."""

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_typescript")

from jidra.ts_treesitter import build_ts_graph_treesitter  # noqa: E402


def _build(tmp_path: Path, files: dict[str, str]):
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return build_ts_graph_treesitter(tmp_path)


class TestStructure:
    def test_class_method_field_extraction(self, tmp_path):
        g = _build(
            tmp_path,
            {
                "svc.ts": (
                    "export class Svc extends Base implements IFoo {\n"
                    "  private token: string;\n"
                    "  validate(t: string): boolean { return true; }\n"
                    "}\n"
                )
            },
        )
        cls = next(c for c in g.classes if c.name == "Svc")
        assert cls.extends == "Base"
        assert "IFoo" in cls.implements
        assert cls.language == "typescript"
        assert any(m.method_name == "validate" for m in g.methods)
        assert any(f.name == "token" for f in g.fields)

    def test_interface_stereotype(self, tmp_path):
        g = _build(tmp_path, {"i.ts": "export interface IFoo { id: number; }\n"})
        cls = next(c for c in g.classes if c.name == "IFoo")
        assert "interface" in cls.stereotypes

    def test_module_level_function(self, tmp_path):
        g = _build(
            tmp_path, {"util.ts": "export function helper(): number { return 1; }\n"}
        )
        assert any(m.method_name == "helper" for m in g.methods)

    def test_arrow_const_function(self, tmp_path):
        g = _build(
            tmp_path, {"a.ts": "export const add = (a: number, b: number) => a + b;\n"}
        )
        m = next(m for m in g.methods if m.method_name == "add")
        assert m.parameter_types == ["number", "number"]

    def test_inheritance_edges(self, tmp_path):
        g = _build(tmp_path, {"a.ts": "export class C extends B {}\n"})
        assert any(
            e.target_class == "B" and e.relation == "extends"
            for e in g.inheritance_edges
        )


class TestFrameworkRoles:
    def test_react_hook(self, tmp_path):
        g = _build(
            tmp_path,
            {"useAuth.ts": "export function useAuth(): string { return ''; }\n"},
        )
        m = next(m for m in g.methods if m.method_name == "useAuth")
        assert m.framework_role == "hook"

    def test_jsx_component(self, tmp_path):
        g = _build(
            tmp_path,
            {"Button.tsx": "export const Button = (p: any) => { return <div/>; };\n"},
        )
        m = next(m for m in g.methods if m.method_name == "Button")
        assert m.framework_role == "component"

    def test_angular_component_stereotype(self, tmp_path):
        g = _build(
            tmp_path,
            {"app.ts": ("@Component({selector: 'app'})\nexport class AppCmp {}\n")},
        )
        cls = next(c for c in g.classes if c.name == "AppCmp")
        assert "angular_component" in cls.stereotypes


class TestResolution:
    def test_this_call_resolves(self, tmp_path):
        g = _build(
            tmp_path,
            {
                "svc.ts": (
                    "export class Svc {\n"
                    "  run(): number { return this.helper(); }\n"
                    "  helper(): number { return 1; }\n"
                    "}\n"
                )
            },
        )
        by_id = {m.id: m.method_name for m in g.methods}
        edges = {
            (by_id[e.caller_method_id], by_id[e.callee_method_id])
            for e in g.resolved_call_edges
        }
        assert ("run", "helper") in edges

    def test_typed_param_receiver_resolves(self, tmp_path):
        g = _build(
            tmp_path,
            {
                "a.ts": (
                    "export class Svc { run(): number { return 1; } }\n"
                    "export class Other { go(s: Svc): number { return s.run(); } }\n"
                )
            },
        )
        by_id = {m.id: m.method_name for m in g.methods}
        edges = {
            (by_id[e.caller_method_id], by_id[e.callee_method_id])
            for e in g.resolved_call_edges
        }
        assert ("go", "run") in edges
