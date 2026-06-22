import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from repo_finder import catalog, fastcontext, lmstudio


@pytest.fixture(autouse=True)
def isolated_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("REPO_FINDER_HOME", str(tmp_path / ".repo_finder"))
    catalog.reset_connection()
    yield
    catalog.reset_connection()


def _repo_metadata(owner: str, name: str) -> dict[str, Any]:
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
        "language": "TypeScript",
        "size": 10,
        "created_at": "2026-01-15T00:00:00Z",
        "pushed_at": "2026-06-20T12:00:00Z",
        "topics": ["nextjs"],
    }


def _write_snapshot(root: Path) -> None:
    (root / "src" / "components").mkdir(parents=True)
    (root / "src" / "components" / "data-table.tsx").write_text(
        "\n".join(
            [
                "import { useReactTable } from '@tanstack/react-table'",
                "export function DataTable() {",
                "  const table = useReactTable({ columns: [] })",
                "  return <table>{table.getRowModel().rows.length}</table>",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        json.dumps({"dependencies": {"next": "15", "@tanstack/react-table": "8"}}),
        encoding="utf-8",
    )


def _create_candidate(tmp_path: Path) -> str:
    snapshot_root = tmp_path / "snapshot"
    snapshot_root.mkdir()
    _write_snapshot(snapshot_root)
    repo_id = catalog.upsert_repository(_repo_metadata("owner", "repo"), "test")
    snapshot_id = catalog.upsert_snapshot(repo_id, "abc123", "main", snapshot_root)
    catalog.upsert_repository_card(snapshot_id, {"card_version": "repo-card-v1"})
    return catalog.upsert_asset(
        snapshot_id,
        repo_id,
        "data-table",
        {
            "entry_paths": ["src/components/data-table.tsx"],
            "dependency_paths": ["package.json"],
            "external_dependencies": ["@tanstack/react-table"],
            "evidence_paths": ["src/components/data-table.tsx:1-4"],
            "reuse_score": 0.9,
            "synthesis": {
                "adaptation_notes": [],
                "ui_path_score": 1.0,
                "noise_penalty": 0.0,
                "capability_path_score": 1.0,
            },
        },
    )


def test_fastcontext_tools_are_sandboxed_and_read_only(tmp_path: Path) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()
    _write_snapshot(root)
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    grep_result = fastcontext.grep_paths(root, "useReactTable", file_glob="**/*.tsx")
    assert grep_result["matches"][0]["citation"] == "src/components/data-table.tsx:1-1"

    read_result = fastcontext.read_file(root, "src/components/data-table.tsx", start=1, end=2)
    assert read_result["content"].startswith("1: import")

    glob_result = fastcontext.glob_paths(root, "**/*.tsx")
    assert glob_result["matches"] == ["src/components/data-table.tsx"]

    with pytest.raises(fastcontext.FastContextError):
        fastcontext.read_file(root, "../secret.txt")


def test_parse_fastcontext_json_and_final_answer_formats() -> None:
    tool_response = fastcontext.parse_fastcontext_response(
        json.dumps(
            {
                "tool_calls": [
                    {"tool": "GREP", "args": {"pattern": "useReactTable", "glob": "**/*.tsx"}}
                ]
            }
        )
    )
    assert tool_response.tool_calls == [
        {"tool": "GREP", "args": {"pattern": "useReactTable", "glob": "**/*.tsx"}}
    ]

    final_response = fastcontext.parse_fastcontext_response(
        "<final_answer>\nsrc/components/data-table.tsx:1-4\n</final_answer>"
    )
    assert final_response.citations[0].evidence_path() == "src/components/data-table.tsx:1-4"


@pytest.mark.asyncio
async def test_refine_candidate_stores_fastcontext_evidence(tmp_path: Path) -> None:
    candidate_id = _create_candidate(tmp_path)
    chat_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chat_calls
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={"data": [{"id": lmstudio.DEFAULT_FASTCONTEXT_MODEL}]},
            )
        assert request.url.path == "/v1/chat/completions"
        chat_calls += 1
        payload = json.loads(request.content)
        assert payload["model"] == lmstudio.DEFAULT_FASTCONTEXT_MODEL
        if chat_calls == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "tool_calls": [
                                            {
                                                "tool": "GREP",
                                                "args": {
                                                    "pattern": "useReactTable",
                                                    "glob": "**/*.tsx",
                                                },
                                            }
                                        ]
                                    }
                                )
                            }
                        }
                    ]
                },
            )

        assert "src/components/data-table.tsx" in payload["messages"][-1]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "final_answer": {
                                        "evidence": [
                                            {
                                                "path": "src/components/data-table.tsx",
                                                "start_line": 1,
                                                "end_line": 4,
                                                "reason": "TanStack table implementation",
                                            }
                                        ],
                                        "notes": ["Reusable table component"],
                                    }
                                }
                            )
                        }
                    }
                ]
            },
        )

    result = await fastcontext.refine_candidate(
        candidate_id,
        "Find a reusable TanStack data table",
        transport=httpx.MockTransport(handler),
    )

    assert result["candidate_id"] == candidate_id
    assert result["evidence_paths"] == ["src/components/data-table.tsx:1-4"]
    assert result["notes"] == ["Reusable table component"]

    refinements = catalog.get_connection().execute(
        """
        SELECT asset_id, task_signature, evidence_paths, notes
        FROM evidence_refinements
        """
    ).fetchall()
    assert len(refinements) == 1
    assert refinements[0][0] == candidate_id
    assert json.loads(refinements[0][2]) == ["src/components/data-table.tsx:1-4"]

    runs = catalog.get_connection().execute(
        """
        SELECT stage_name, status, model_id
        FROM analysis_runs
        WHERE stage_name = 'fastcontext-refine'
        """
    ).fetchall()
    assert runs == [("fastcontext-refine", "completed", lmstudio.DEFAULT_FASTCONTEXT_MODEL)]


@pytest.mark.asyncio
async def test_refine_suite_writes_comparison_report(tmp_path: Path, monkeypatch) -> None:
    candidate_id = _create_candidate(tmp_path)
    suite_path = tmp_path / "suite.json"
    output_path = tmp_path / "report.json"
    suite_path.write_text(
        json.dumps(
            {
                "suite_id": "ui-reuse",
                "description": "unit suite",
                "tasks": [
                    {
                        "id": "table",
                        "task": "Find a reusable TanStack data table",
                        "capability": "data-table",
                        "expected_repo_ids": ["owner/repo"],
                        "acceptable_repo_ids": [],
                        "avoid_repo_ids": [],
                        "required_path_terms_any": ["data-table"],
                        "required_dependencies_any": ["@tanstack/react-table"],
                        "max_rank_for_hit": 3,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def fake_ensure_fastcontext_available(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_refine_candidate(
        candidate_id: str,
        task: str,
        max_turns: int = fastcontext.DEFAULT_MAX_TURNS,
        transport: httpx.AsyncBaseTransport | None = None,
        validate_model: bool = True,
    ) -> dict[str, object]:
        assert task == "Find a reusable TanStack data table"
        assert max_turns == 2
        assert validate_model is False
        assert transport is None
        return {
            "candidate_id": candidate_id,
            "task_signature": catalog.task_signature(task),
            "repo_id": "owner/repo",
            "snapshot_id": "snapshot",
            "capability": "data-table",
            "model_id": lmstudio.DEFAULT_FASTCONTEXT_MODEL,
            "prompt_version": fastcontext.PROMPT_VERSION,
            "schema_version": fastcontext.SCHEMA_VERSION,
            "refinement_id": "refined",
            "analysis_run_id": "run",
            "evidence_paths": ["src/components/data-table.tsx:1-4"],
            "notes": ["focused evidence"],
        }

    monkeypatch.setattr(fastcontext, "ensure_fastcontext_available", fake_ensure_fastcontext_available)
    monkeypatch.setattr(fastcontext, "refine_candidate", fake_refine_candidate)

    result = await fastcontext.refine_suite(
        str(suite_path),
        top_k=1,
        label="unit",
        output_path=output_path,
        max_turns=2,
    )

    assert result["report_path"] == str(output_path)
    assert result["metrics"]["candidate_count"] == 1
    assert result["metrics"]["completed_refinements"] == 1
    assert result["scoring_recommendation"]["status"] == "tie_breaker_ready"
    assert result["tasks"][0]["candidates"][0]["candidate_id"] == candidate_id
    assert result["tasks"][0]["candidates"][0]["refined_evidence_paths"] == [
        "src/components/data-table.tsx:1-4"
    ]

    stored_report = json.loads(output_path.read_text(encoding="utf-8"))
    assert stored_report["tasks"][0]["candidates"][0]["deterministic_evidence_paths"] == [
        "src/components/data-table.tsx:1-4"
    ]

    runs = catalog.get_connection().execute(
        """
        SELECT stage_name, status, model_id
        FROM analysis_runs
        WHERE stage_name = 'fastcontext-batch-refine'
        """
    ).fetchall()
    assert runs == [("fastcontext-batch-refine", "completed", lmstudio.DEFAULT_FASTCONTEXT_MODEL)]


def test_fastcontext_status_cli_prints_json(monkeypatch, capsys) -> None:
    import repo_finder.__main__ as main_module

    async def fake_status(start_server: bool, smoke_test: bool) -> dict[str, object]:
        assert start_server is True
        assert smoke_test is True
        return {"reachable": True, "fastcontext_available": True}

    monkeypatch.setattr(main_module, "_fastcontext_status", fake_status)
    monkeypatch.setattr(
        sys,
        "argv",
        ["repo-finder", "fastcontext-status", "--start-server", "--smoke-test"],
    )
    main_module.main()
    captured = capsys.readouterr()
    assert '"fastcontext_available": true' in captured.out


def test_refine_evidence_cli_invokes_fastcontext(monkeypatch, capsys) -> None:
    import repo_finder.__main__ as main_module

    async def fake_refine_candidate(
        candidate_id: str,
        task: str,
        max_turns: int = fastcontext.DEFAULT_MAX_TURNS,
    ) -> dict[str, object]:
        assert candidate_id == "abc"
        assert task == "Find evidence"
        assert max_turns == 2
        return {"candidate_id": candidate_id, "evidence_paths": ["src/file.ts:1-2"]}

    monkeypatch.setattr(fastcontext, "refine_candidate", fake_refine_candidate)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "repo-finder",
            "refine-evidence",
            "--candidate-id",
            "abc",
            "--task",
            "Find evidence",
            "--max-turns",
            "2",
        ],
    )
    main_module.main()
    captured = capsys.readouterr()
    assert '"candidate_id": "abc"' in captured.out


def test_refine_evidence_cli_invokes_suite_batch(monkeypatch, capsys, tmp_path: Path) -> None:
    import repo_finder.__main__ as main_module

    output_path = tmp_path / "report.json"

    async def fake_refine_suite(
        suite: str,
        top_k: int,
        label: str | None = None,
        output_path: Path | None = None,
        max_turns: int = fastcontext.DEFAULT_MAX_TURNS,
        limit_tasks: int | None = None,
    ) -> dict[str, object]:
        assert suite == "ui-reuse"
        assert top_k == 2
        assert label == "unit"
        assert output_path == tmp_path / "report.json"
        assert max_turns == 3
        assert limit_tasks == 1
        return {
            "suite_id": "ui-reuse",
            "label": label,
            "metrics": {"candidate_count": 2},
            "scoring_recommendation": {"status": "tie_breaker_ready"},
            "report_path": str(output_path),
        }

    monkeypatch.setattr(fastcontext, "refine_suite", fake_refine_suite)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "repo-finder",
            "refine-evidence",
            "--suite",
            "ui-reuse",
            "--top-k",
            "2",
            "--label",
            "unit",
            "--output",
            str(output_path),
            "--max-turns",
            "3",
            "--limit-tasks",
            "1",
        ],
    )
    main_module.main()
    captured = capsys.readouterr()
    assert '"suite_id": "ui-reuse"' in captured.out
    assert '"candidate_count": 2' in captured.out
