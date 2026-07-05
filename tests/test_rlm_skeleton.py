import json
from pathlib import Path

import pytest

from source_scout import bundles, catalog
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


def _repo_metadata(owner: str = "owner", name: str = "repo") -> dict[str, object]:
    return {
        "owner": {"login": owner},
        "name": name,
        "full_name": f"{owner}/{name}",
        "html_url": f"https://github.com/{owner}/{name}",
        "private": False,
        "archived": False,
        "mirror_url": None,
        "fork": False,
        "is_template": False,
        "language": "Python",
        "size": 10,
        "created_at": "2026-01-15T00:00:00Z",
        "pushed_at": "2026-06-20T12:00:00Z",
        "topics": ["source-scout"],
    }


def _create_candidate(tmp_path: Path) -> tuple[str, Path]:
    snapshot_root = tmp_path / "snapshot"
    (snapshot_root / "src").mkdir(parents=True)
    (snapshot_root / "src" / "app.py").write_text(
        "def reusable():\n    return 'ok'\n",
        encoding="utf-8",
    )
    (snapshot_root / "src" / "helper.py").write_text(
        "def helper():\n    return reusable()\n",
        encoding="utf-8",
    )
    (snapshot_root / "package.json").write_text(
        json.dumps({"dependencies": {"fastapi": "1.0.0"}}),
        encoding="utf-8",
    )
    repo_id = catalog.upsert_repository(_repo_metadata(), "test")
    snapshot_id = catalog.upsert_snapshot(repo_id, "abc123", "main", snapshot_root)
    catalog.upsert_repository_card(
        snapshot_id,
        {
            "card_version": "repo-card-v1",
            "package_manifests": {"package.json": {"dependencies": {"fastapi": "1.0.0"}}},
            "tree_summary": {"total_files": 3, "source_files": ["src/app.py", "src/helper.py"]},
            "stack_signals": {"has_python_files": True},
            "deterministic_features": {"source_file_count": 2},
            "gemma_profile": {"capabilities": [{"name": "route handler", "confidence": 0.8}]},
        },
    )
    asset_id = catalog.upsert_asset(
        snapshot_id,
        repo_id,
        "route-handlers",
        {
            "entry_paths": ["src/app.py"],
            "dependency_paths": ["package.json"],
            "external_dependencies": ["fastapi"],
            "evidence_paths": ["src/helper.py:1-2"],
            "synthesis": {"adaptation_notes": ["Copy app and helper."]},
            "reuse_score": 0.8,
        },
    )
    return asset_id, snapshot_root


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


def test_rlm_list_filtering_does_not_pretruncate_before_glob_match(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "aaa.txt").write_text("not a match\n", encoding="utf-8")
    (root / "zzz.py").write_text("needle\n", encoding="utf-8")
    tools = RlmReadOnlyTools(root, max_files=1)

    listed = tools.list_files("**/*.py")
    grep = tools.grep_text("needle", file_glob="**/*.py")

    assert listed["matches"] == ["zzz.py"]
    assert grep["matches"] == [{"path": "zzz.py", "line": 1, "text": "needle"}]


def test_rlm_tools_load_candidate_context_and_repository_card(tmp_path: Path) -> None:
    asset_id, _snapshot_root = _create_candidate(tmp_path)
    tools = RlmReadOnlyTools(tmp_path)

    context = tools.load_candidate_context(asset_id)
    card = tools.load_repository_card(asset_id)

    assert context["asset"]["asset_id"] == asset_id
    assert context["repo"]["repo_id"] == "owner/repo"
    assert context["snapshot"]["commit_sha"] == "abc123"
    assert context["card"]["tree_summary"]["total_files"] == 3
    assert card["card"]["stack_signals"]["has_python_files"] is True


def test_rlm_tools_load_repo_map_from_fixture_project(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (root / "package.json").write_text(json.dumps({"scripts": {"test": "pytest"}}), encoding="utf-8")
    tools = RlmReadOnlyTools(root)

    result = tools.load_repo_map(limit=10)

    assert result["source"] == {"kind": "local_root"}
    assert any(entry["path"] == "src/app.py" for entry in result["entries"])
    assert any(entry["kind"] == "manifest" for entry in result["entries"])


def test_rlm_tools_read_candidate_source_safely(tmp_path: Path) -> None:
    asset_id, snapshot_root = _create_candidate(tmp_path)
    tools = RlmReadOnlyTools(tmp_path)

    result = tools.read_candidate_source(asset_id, "src/app.py", start_line=1, limit=1)

    assert result["candidate_id"] == asset_id
    assert result["root"] == str(snapshot_root.resolve())
    assert result["path"] == "src/app.py"
    assert result["content"] == "1|def reusable():"
    with pytest.raises(RlmToolError, match="escapes"):
        tools.read_candidate_source(asset_id, "../escape.py")


def test_rlm_tools_read_bundle_source_safely(tmp_path: Path) -> None:
    asset_id, _snapshot_root = _create_candidate(tmp_path)
    bundle = bundles.create_source_bundle(asset_id, "task123")
    tools = RlmReadOnlyTools(tmp_path)

    result = tools.read_bundle_source(asset_id, "task123", "src/helper.py", start_line=1, limit=1)

    assert result["candidate_id"] == asset_id
    assert result["task_signature"] == "task123"
    assert result["root"] == str((Path(bundle.bundle_path) / "source").resolve())
    assert result["content"] == "1|def helper():"
    with pytest.raises(RlmToolError, match="escapes"):
        tools.read_bundle_source(asset_id, "task123", "../escape.py")


def test_rlm_tools_load_reuse_loop_report(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / ".source_scout" / "reuse_loop_reports").mkdir(parents=True)
    report_path = root / ".source_scout" / "reuse_loop_reports" / "loop.json"
    report_path.write_text(
        json.dumps(
            {
                "suite_id": "ui-reuse",
                "label": "unit",
                "top_k": 3,
                "passed": False,
                "metrics": {"task_count": 1},
                "tasks": [
                    {
                        "id": "task-1",
                        "task": "Find reusable code",
                        "selected_repo_id": "owner/repo",
                        "assessment_error": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    tools = RlmReadOnlyTools(root)

    result = tools.load_reuse_loop_report(".source_scout/reuse_loop_reports/loop.json")

    assert result["path"] == ".source_scout/reuse_loop_reports/loop.json"
    assert result["report"]["suite_id"] == "ui-reuse"
    assert result["report"]["tasks"][0]["selected_repo_id"] == "owner/repo"


def test_rlm_root_sensitive_tools_reject_path_traversal(tmp_path: Path) -> None:
    asset_id, _snapshot_root = _create_candidate(tmp_path)
    bundle = bundles.create_source_bundle(asset_id, "task123")
    root = tmp_path / "project"
    root.mkdir()
    (root / "report.json").write_text("{}", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "report.json").write_text("{}", encoding="utf-8")
    (Path(bundle.bundle_path) / "source" / "src" / "local.py").write_text(
        "print('local')\n",
        encoding="utf-8",
    )
    tools = RlmReadOnlyTools(root)

    with pytest.raises(RlmToolError, match="escapes"):
        tools.read_file_range("../outside.py")
    with pytest.raises(RlmToolError, match="escapes"):
        tools.grep_text("x", search_path="../")
    with pytest.raises(RlmToolError, match="escapes"):
        tools.load_repo_map("../")
    with pytest.raises(RlmToolError, match="escapes"):
        tools.read_candidate_source(asset_id, "../outside.py")
    with pytest.raises(RlmToolError, match="escapes"):
        tools.read_bundle_source(asset_id, "task123", "../outside.py")
    with pytest.raises(RlmToolError, match="escapes"):
        tools.load_reuse_loop_report("../report.json")
    with pytest.raises(RlmToolError, match="under .source_scout"):
        tools.load_reuse_loop_report(".git/report.json")


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
