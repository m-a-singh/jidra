from pathlib import Path

from jidra.go_extractor import build_go_graph


GO_FIXTURE = """\
package animal

type Animal struct {
	Name string
}

func (a *Animal) Speak() string {
	return a.Name
}

type Dog struct {
	Animal
	Breed string
}

func (d *Dog) Greet() string {
	return d.Speak()
}

func NewDog(name string) *Dog {
	return &Dog{Animal: Animal{Name: name}}
}

func MakeItTalk() string {
	d := NewDog("Rex")
	return d.Greet()
}
"""


def _write_fixture(tmp_path: Path) -> Path:
    pkg_dir = tmp_path / "animal"
    pkg_dir.mkdir()
    go_file = pkg_dir / "animal.go"
    go_file.write_text(GO_FIXTURE)
    return tmp_path


def test_build_go_graph_extracts_structs_and_methods(tmp_path):
    root = _write_fixture(tmp_path)
    graph = build_go_graph(root)

    class_names = {c.name for c in graph.classes}
    assert "Animal" in class_names
    assert "Dog" in class_names

    for c in graph.classes:
        assert c.language == "go"

    dog_cls = next(c for c in graph.classes if c.name == "Dog")
    assert "struct" in dog_cls.stereotypes

    method_names = {m.method_name for m in graph.methods}
    assert {"Speak", "Greet", "NewDog", "MakeItTalk"} <= method_names
    for m in graph.methods:
        assert m.language == "go"


def test_build_go_graph_embeds_inheritance_edge(tmp_path):
    root = _write_fixture(tmp_path)
    graph = build_go_graph(root)

    embeds = [e for e in graph.inheritance_edges if e.relation == "embeds"]
    assert any(e.target_class == "Animal" for e in embeds)


def test_build_go_graph_resolves_calls(tmp_path):
    root = _write_fixture(tmp_path)
    graph = build_go_graph(root)

    assert len(graph.resolved_call_edges) > 0

    greet_method = next(m for m in graph.methods if m.method_name == "Greet")
    speak_method = next(m for m in graph.methods if m.method_name == "Speak")

    speak_callsite_ids = {
        cs.id for cs in graph.callsites if cs.caller_method_id == greet_method.id
    }
    resolved_to_speak = [
        e
        for e in graph.resolved_call_edges
        if e.callsite_id in speak_callsite_ids and e.callee_method_id == speak_method.id
    ]
    assert resolved_to_speak

    make_it_talk = next(m for m in graph.methods if m.method_name == "MakeItTalk")
    greet_callsite_ids = {
        cs.id for cs in graph.callsites if cs.caller_method_id == make_it_talk.id
    }
    resolved_to_greet = [
        e
        for e in graph.resolved_call_edges
        if e.callsite_id in greet_callsite_ids and e.callee_method_id == greet_method.id
    ]
    assert resolved_to_greet
