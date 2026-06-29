from pathlib import Path

from jidra.extractors.go_extractor import build_go_graph, build_go_graph_for_files


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


def test_build_go_graph_resolves_package_scoped_function_call(tmp_path):
    """Plain identifier calls (no receiver) resolve to top-level functions
    declared anywhere in the same package directory, not just the same file."""
    root = _write_fixture(tmp_path)
    graph = build_go_graph(root)

    make_it_talk = next(m for m in graph.methods if m.method_name == "MakeItTalk")
    new_dog = next(m for m in graph.methods if m.method_name == "NewDog")

    new_dog_callsite_ids = {
        cs.id
        for cs in graph.callsites
        if cs.caller_method_id == make_it_talk.id and cs.callee_name == "NewDog"
    }
    assert new_dog_callsite_ids

    resolved_to_new_dog = [
        e
        for e in graph.resolved_call_edges
        if e.callsite_id in new_dog_callsite_ids and e.callee_method_id == new_dog.id
    ]
    assert resolved_to_new_dog


def test_build_go_graph_resolves_calls_across_files_in_same_package(tmp_path):
    """Two files declaring `package animal` in the same directory must be
    treated as one package for call resolution (the main reason for the
    two-pass collect-then-resolve design)."""
    pkg_dir = tmp_path / "animal"
    pkg_dir.mkdir()
    (pkg_dir / "animal.go").write_text(
        """package animal

type Animal struct {
	Name string
}

func (a *Animal) Speak() string {
	return a.Name
}
"""
    )
    (pkg_dir / "factory.go").write_text(
        """package animal

func NewAnimal(name string) *Animal {
	return &Animal{Name: name}
}

func MakeItTalk() string {
	a := NewAnimal("Rex")
	return a.Speak()
}
"""
    )

    graph = build_go_graph(tmp_path)

    new_animal = next(m for m in graph.methods if m.method_name == "NewAnimal")
    speak = next(m for m in graph.methods if m.method_name == "Speak")
    make_it_talk = next(m for m in graph.methods if m.method_name == "MakeItTalk")

    new_animal_callsites = {
        cs.id
        for cs in graph.callsites
        if cs.caller_method_id == make_it_talk.id and cs.callee_name == "NewAnimal"
    }
    speak_callsites = {
        cs.id
        for cs in graph.callsites
        if cs.caller_method_id == make_it_talk.id and cs.callee_name == "Speak"
    }

    assert any(
        e.callsite_id in new_animal_callsites and e.callee_method_id == new_animal.id
        for e in graph.resolved_call_edges
    )
    assert any(
        e.callsite_id in speak_callsites and e.callee_method_id == speak.id
        for e in graph.resolved_call_edges
    )


def test_build_go_graph_var_declaration_type_inference(tmp_path):
    """`var a Animal` (not `:=`) must also be tracked for receiver-type resolution."""
    pkg_dir = tmp_path / "animal"
    pkg_dir.mkdir()
    (pkg_dir / "animal.go").write_text(
        """package animal

type Animal struct {
	Name string
}

func (a *Animal) Speak() string {
	return a.Name
}

func TalkWithVarDecl() string {
	var a Animal
	a.Name = "Rex"
	return a.Speak()
}
"""
    )

    graph = build_go_graph(tmp_path)

    speak = next(m for m in graph.methods if m.method_name == "Speak")
    talk = next(m for m in graph.methods if m.method_name == "TalkWithVarDecl")

    speak_callsites = {
        cs.id
        for cs in graph.callsites
        if cs.caller_method_id == talk.id and cs.callee_name == "Speak"
    }
    assert speak_callsites
    assert any(
        e.callsite_id in speak_callsites and e.callee_method_id == speak.id
        for e in graph.resolved_call_edges
    )


def test_build_go_graph_range_loop_type_inference(tmp_path):
    """`for _, v := range someMap` must infer v's element type so calls on v resolve."""
    pkg_dir = tmp_path / "zoo"
    pkg_dir.mkdir()
    (pkg_dir / "zoo.go").write_text(
        """package zoo

type Animal struct {
	Name string
}

func (a *Animal) Speak() string {
	return a.Name
}

func SpeakAllInMap(animals map[string]*Animal) {
	for _, a := range animals {
		a.Speak()
	}
}

func SpeakAllInSlice(animals []Animal) {
	for _, a := range animals {
		a.Speak()
	}
}
"""
    )

    graph = build_go_graph(tmp_path)

    speak = next(m for m in graph.methods if m.method_name == "Speak")
    speak_callsites = {cs.id for cs in graph.callsites if cs.callee_name == "Speak"}

    resolved_speak_edges = [
        e
        for e in graph.resolved_call_edges
        if e.callsite_id in speak_callsites and e.callee_method_id == speak.id
    ]
    # One resolved Speak() call from the map-range loop and one from the slice-range loop.
    assert len(resolved_speak_edges) == 2


def test_build_go_graph_range_over_function_call_type_inference(tmp_path):
    """`for _, v := range getAnimals()` infers v's type from the callee's
    own return type, not just from a previously-typed local variable."""
    pkg_dir = tmp_path / "zoo"
    pkg_dir.mkdir()
    (pkg_dir / "zoo.go").write_text(
        """package zoo

type Animal struct {
	Name string
}

func (a *Animal) Speak() string {
	return a.Name
}

func GetAnimals() []Animal {
	return nil
}

func SpeakAllFromCall() {
	for _, a := range GetAnimals() {
		a.Speak()
	}
}
"""
    )

    graph = build_go_graph(tmp_path)

    speak = next(m for m in graph.methods if m.method_name == "Speak")
    speak_all = next(m for m in graph.methods if m.method_name == "SpeakAllFromCall")

    speak_callsites = {
        cs.id
        for cs in graph.callsites
        if cs.caller_method_id == speak_all.id and cs.callee_name == "Speak"
    }
    assert speak_callsites
    assert any(
        e.callsite_id in speak_callsites and e.callee_method_id == speak.id
        for e in graph.resolved_call_edges
    )


def test_build_go_graph_for_files_incremental(tmp_path):
    """Incremental extraction should produce structurally correct, unresolved
    graphs (matching the existing Java/Python incremental contract) that can
    later be merged and resolved by the caller."""
    root = _write_fixture(tmp_path)
    go_file = root / "animal" / "animal.go"

    graph = build_go_graph_for_files({go_file}, root)

    class_names = {c.name for c in graph.classes}
    assert "Animal" in class_names
    assert "Dog" in class_names

    method_names = {m.method_name for m in graph.methods}
    assert {"Speak", "Greet", "NewDog", "MakeItTalk"} <= method_names

    # Incremental extraction is intentionally unresolved; resolution happens
    # when the caller merges this into the full graph.
    assert graph.callsites == []
    assert graph.resolved_call_edges == []


def test_cross_file_method_resolution(tmp_path):
    """Methods declared in a different file from their receiver type must appear in the graph."""
    pkg = tmp_path / "svc"
    pkg.mkdir()

    # Type lives in types.go
    (pkg / "types.go").write_text("package svc\n\ntype Service struct{ Name string }\n")
    # Methods live in service.go — different file, same package
    (pkg / "service.go").write_text(
        "package svc\n\nfunc (s *Service) Run() string { return s.Name }\n"
        "func (s *Service) Stop() {}\n"
    )

    graph = build_go_graph(tmp_path)

    method_names = {m.method_name for m in graph.methods}
    assert "Run" in method_names, (
        "method in separate file from type should be extracted"
    )
    assert "Stop" in method_names

    # Both methods should be owned by the Service class
    run = next(m for m in graph.methods if m.method_name == "Run")
    assert "Service" in run.class_full_name


def test_build_go_graph_for_files_skips_missing_files(tmp_path):
    root = _write_fixture(tmp_path)
    missing = root / "animal" / "does_not_exist.go"

    graph = build_go_graph_for_files({missing}, root)

    assert graph.classes == []
    assert graph.methods == []
