import json
from pathlib import Path

import pytest

from source_scout.rlm import (
    RlmFinalResult,
    RlmFinding,
    RlmReadOnlyTools,
    RlmSession,
    RlmSessionConfig,
    RlmStep,
    RlmToolCall,
    RlmToolError,
    RlmToolResult,
    RlmTrace,
)
from source_scout.rlm import tools as rlm_tools


def test_rlm_schema_construction() -> None:
    config = RlmSessionConfig(task="Compare candidates", root_path=".", max_steps=4)
    call = RlmToolCall(tool="list_files", arguments={"pattern": "**/*.py"}, call_id="c1")
    result = RlmToolResult(
        tool="list_files",
        ok=True,
        result={"matches": ["src/source_scout/server.py"]},
        call_id="c1",
    )
    step = RlmStep(step_index=1, kind="tool", tool_calls=[call], tool_results=[result])
    finding = RlmFinding(
        summary="Candidate has a focused MCP tool surface.",
        evidence_paths=["src/source_scout/server.py:1-20"],
        candidate_id="candidate-1",
        repo_id="owner/repo",
        confidence=0.8,
    )
    final = RlmFinalResult(status="completed", answer="Use candidate-1", findings=[finding])

    trace = RlmTrace(session_id="session-1", config=config, steps=[step], final_result=final)

    assert trace.config.max_steps == 4
    assert trace.steps[0].tool_calls[0].arguments["pattern"] == "**/*.py"
    assert trace.final_result is not None
    assert trace.final_result.findings[0].repo_id == "owner/repo"


def test_rlm_session_config_rejects_mutation_and_execution_flags() -> None:
    with pytest.raises(ValueError):
        RlmSessionConfig(allow_project_mutation=True)
    with pytest.raises(ValueError):
        RlmSessionConfig(allow_code_execution=True)


def test_rlm_trace_serializes_to_json() -> None:
    session = RlmSession(
        RlmSessionConfig(task="Review bundle", root_path="."),
        session_id="session-1",
    )
    session.record_step(
        kind="tool",
        summary="Listed source files",
        tool_calls=[RlmToolCall(tool="list_files")],
        tool_results=[RlmToolResult(tool="list_files", ok=True, result={"matches": []})],
    )
    trace = session.finish(RlmFinalResult(status="completed", answer="No issues"))

    dumped = trace.to_jsonable()
    encoded = trace.model_dump_json()

    assert dumped["session_id"] == "session-1"
    assert dumped["steps"][0]["step_index"] == 1
    assert json.loads(encoded)["final_result"]["answer"] == "No issues"


def test_rlm_tools_enforce_path_safety(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "outside.py").write_text("print('escape')\n", encoding="utf-8")
    tools = RlmReadOnlyTools(root)

    with pytest.raises(RlmToolError, match="escapes"):
        tools.read_file_range("../outside.py")
    with pytest.raises(RlmToolError, match="escapes"):
        tools.list_files("../*")
    with pytest.raises(RlmToolError, match="escapes"):
        tools.grep_text("ok", search_path="../")


def test_rlm_tools_read_bounded_file_ranges(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    tools = RlmReadOnlyTools(root, max_read_lines=2)

    result = tools.read_file_range("src/app.py", start_line=2, end_line=4)

    assert result["path"] == "src/app.py"
    assert result["start_line"] == 2
    assert result["end_line"] == 3
    assert result["content"] == "2|two\n3|three"
    assert result["truncated"] is True


def test_rlm_tools_list_and_grep_are_bounded(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "root.py").write_text("needle\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("needle\n", encoding="utf-8")
    (root / "src" / "b.py").write_text("needle\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("needle\n", encoding="utf-8")
    tools = RlmReadOnlyTools(root, max_files=10, max_grep_results=1)

    listed = tools.list_files("**/*.py")
    grep = tools.grep_text("needle", file_glob="**/*.py")

    assert listed["matches"] == ["root.py", "src/a.py", "src/b.py"]
    assert grep["matches"] == [{"path": "root.py", "line": 1, "text": "needle"}]
    assert grep["truncated"] is True


def test_rlm_tools_load_candidate_and_bundle_with_mocked_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    (bundle_root / "bundle.json").write_text(
        json.dumps(
            {
                "candidate_id": "candidate-1",
                "task_signature": "task-1",
                "copied_files": ["src/app.py"],
            }
        ),
        encoding="utf-8",
    )

    def fake_get_asset_detail(candidate_id: str) -> dict[str, object] | None:
        assert candidate_id == "candidate-1"
        return {
            "asset_id": candidate_id,
            "repo_id": "owner/repo",
            "snapshot_path": tmp_path / "snapshot",
        }

    def fake_bundle_path(candidate_id: str, task_signature: str | None = None) -> Path:
        assert candidate_id == "candidate-1"
        assert task_signature == "task-1"
        return bundle_root

    monkeypatch.setattr(rlm_tools.catalog, "get_asset_detail", fake_get_asset_detail)
    monkeypatch.setattr(rlm_tools.catalog, "bundle_path", fake_bundle_path)

    tools = RlmReadOnlyTools(root)

    asset = tools.load_candidate_asset("candidate-1")
    manifest = tools.load_bundle_manifest("candidate-1", "task-1")

    assert asset["asset"]["repo_id"] == "owner/repo"
    assert asset["asset"]["snapshot_path"] == str(tmp_path / "snapshot")
    assert manifest["manifest"]["copied_files"] == ["src/app.py"]
