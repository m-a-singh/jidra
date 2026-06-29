import json

from jidra.server.mcp_server import _log_session_call


def test_logs_one_valid_json_line_per_call(tmp_path):
    codebase = tmp_path / "repo"
    codebase.mkdir()

    _log_session_call(str(codebase), "jidra_get_method_source", "m_1")
    _log_session_call(str(codebase), "jidra_get_flow", "m_2")
    _log_session_call(str(codebase), "jidra_check_staleness")

    log_path = codebase / ".jidra" / "session_log.jsonl"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3

    rows = [json.loads(line) for line in lines]
    assert rows[0]["tool_name"] == "jidra_get_method_source"
    assert rows[0]["method_id"] == "m_1"
    assert "timestamp" in rows[0]
    assert rows[2]["tool_name"] == "jidra_check_staleness"
    assert rows[2]["method_id"] is None


def test_logging_failure_does_not_raise(tmp_path):
    # Point "codebase" at a file (not a dir), so mkdir(".jidra") fails.
    not_a_dir = tmp_path / "not_a_dir"
    not_a_dir.write_text("x", encoding="utf-8")

    # Should not raise even though the log directory can't be created.
    _log_session_call(str(not_a_dir), "jidra_get_method_source", "m_1")
