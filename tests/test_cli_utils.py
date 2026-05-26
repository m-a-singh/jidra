import os
from types import SimpleNamespace

import pytest

from jidra.cli import (
    _extract_focused_map_sections,
    _is_meaningful_signature,
    _match_stack_frames_to_methods,
    _method_filename_part,
    _normalize_stack_trace_text,
    _parse_stack_trace,
    _safe_filename_part,
    is_business_entry,
)






def test_safe_filename_part_basic():
    assert _safe_filename_part("Hello World") == "hello_world"
    assert _safe_filename_part("a---b") == "a_b"
    assert _safe_filename_part("   ") == "unknown"
    assert _safe_filename_part("A/B\\C") == "a_b_c"


def test_method_filename_part_prefers_class_and_method_name():
    m = SimpleNamespace(class_full_name="com.example.FooService", method_name="doThing", id="mid")
    assert _method_filename_part(m) == "fooservice_dothing"


def test_method_filename_part_falls_back_to_id():
    # _method_filename_part uses getattr(..., "") and will stringify None -> "None".
    # So for this test, omit those attrs entirely to exercise the id fallback.
    m = SimpleNamespace(id="MyId")
    assert _method_filename_part(m) == "myid"


def test_normalize_stack_trace_inserts_newlines():
    raw = "java.lang.RuntimeException: boom at a.b.C.m(C.java:12) at x.y.Z.n(Z.java:9)"
    norm = _normalize_stack_trace_text(raw)
    # Should split into multiple lines containing stack frames
    assert "\n" in norm
    assert "at a.b.C.m(C.java:12)" in norm


def test_parse_stack_trace_extracts_frames():
    text = """java.lang.RuntimeException: boom
        at a.b.C.m(C.java:12)
        at x.y.Z.n(Z.java:9)
    """
    frames = _parse_stack_trace(text)
    assert len(frames) == 2
    assert frames[0]["class_full_name"] == "a.b.C"
    assert frames[0]["method_name"] == "m"
    assert frames[0]["file_name"] == "C.java"
    assert frames[0]["line"] == 12


def test_is_meaningful_signature_filters_infra():
    assert _is_meaningful_signature("com.x.Service#doThing()")
    assert not _is_meaningful_signature("com.x.metrics.Meter#increment()")
    assert not _is_meaningful_signature("com.x.logging.Log#info()")


def test_is_business_entry_call_name_filter_is_case_insensitive():
    entry = {"call": "INCREMENT", "target_signature": "com.x.Foo#increment()"}
    assert not is_business_entry(entry, non_business_call_names={"increment"}, non_business_signature_parts=())


def test_is_business_entry_signature_part_filter_is_case_insensitive():
    entry = {"call": "doThing", "target_signature": "com.x.Metrics.Foo#doThing()"}
    assert not is_business_entry(entry, non_business_call_names=set(), non_business_signature_parts=(".metrics.",))


def test_extract_focused_map_sections_stops_at_root_flow_header():
    md = """# Title
line1
## Something
line2
## Root Flow
should_not_be_included
"""
    out = _extract_focused_map_sections(md)
    assert "should_not_be_included" not in out
    assert "line2" in out


def test_match_stack_frames_respects_project_prefix_env(sample_graph, monkeypatch):
    # Pick a real class from the sample graph if possible. If not, just ensure it doesn't crash.
    # We'll take the first method in graph and craft a matching frame.
    methods = list(sample_graph.methods)
    assert methods, "sample graph should contain methods"
    m = methods[0]

    monkeypatch.setenv("JIDRA_PROJECT_PREFIXES", (m.class_full_name.split(".")[0] + "."))

    frames = [
        {
            "frame_index": 0,
            "raw_index": 0,
            "class_full_name": m.class_full_name,
            "method_name": m.method_name,
            "file_name": os.path.basename(m.file_path),
            "line": int(m.start_line or 1),
        }
    ]
    matched_rows, anchor = _match_stack_frames_to_methods(sample_graph, frames)
    assert len(matched_rows) == 1
    assert matched_rows[0]["match_status"] in {"matched", "ambiguous"}
    assert anchor is not None
