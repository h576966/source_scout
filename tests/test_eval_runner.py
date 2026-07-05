import json
import sys
from pathlib import Path
from typing import Any

import pytest

from source_scout import catalog, eval_runner


def _suite_file(tmp_path: Path, tasks: list[dict[str, Any]]) -> Path:
    path = tmp_path / "suite.json"
    path.write_text(
        json.dumps(
            {
                "suite_id": "ui-reuse",
                "description": "test suite",
                "tasks": tasks,
            }
        ),
        encoding="utf-8",
    )
    return path


def _task(
    *,
    task_id: str = "task",
    repo_id: str = "good/repo",
    capability: str = "data-table",
    avoid_repo_ids: list[str] | None = None,
    required_path_terms_any: list[str] | None = None,
    required_dependencies_any: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "task": "Find a reusable data table",
        "capability": capability,
        "expected_repo_ids": [repo_id],
        "acceptable_repo_ids": [],
        "avoid_repo_ids": avoid_repo_ids or [],
        "required_path_terms_any": required_path_terms_any or [],
        "required_dependencies_any": required_dependencies_any or [],
        "max_rank_for_hit": 3,
    }


def _asset(
    tmp_path: Path,
    *,
    repo_id: str,
    capability: str,
    entry_paths: list[str],
    dependencies: list[str] | None = None,
    score: float = 0.9,
) -> str:
    owner, name = repo_id.split("/", 1)
    snapshot_root = tmp_path / owner / name
    snapshot_root.mkdir(parents=True)
    for entry_path in entry_paths:
        path = snapshot_root / entry_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("export const reusable = true\n", encoding="utf-8")
    (snapshot_root / "package.json").write_text(
        json.dumps({"dependencies": {dependency: "1.0.0" for dependency in dependencies or []}}),
        encoding="utf-8",
    )
    stored_repo_id = catalog.upsert_repository(
        {
            "owner": {"login": owner},
            "name": name,
            "full_name": repo_id,
            "html_url": f"https://github.com/{repo_id}",
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
        },
        "test",
    )
    snapshot_id = catalog.upsert_snapshot(stored_repo_id, f"{owner}sha", "main", snapshot_root)
    catalog.upsert_repository_card(
        snapshot_id,
        {
            "card_version": "repo-card-v1",
            "gemma_profile": {
                "repository_type": "reference_application",
                "capabilities": [
                    {
                        "name": capability.replace("-", " "),
                        "confidence": 1.0,
                        "evidence": entry_paths,
                    }
                ],
                "likely_usefulness": 0.9,
                "extractability": 0.9,
                "maintenance_quality": 0.9,
                "needs_fastcontext": True,
                "concerns": [],
            },
        },
    )
    return catalog.upsert_asset(
        snapshot_id,
        stored_repo_id,
        capability,
        {
            "entry_paths": entry_paths,
            "dependency_paths": ["package.json"],
            "external_dependencies": dependencies or [],
            "evidence_paths": [f"{entry_paths[0]}:1-3"],
            "reuse_score": score,
            "synthesis": {
                "adaptation_notes": [],
                "ui_path_score": 0.9,
                "noise_penalty": 0.0,
                "capability_path_score": 1.0,
            },
        },
    )


def test_load_suite_validates_required_fields(tmp_path: Path) -> None:
    suite_path = _suite_file(
        tmp_path,
        [
            _task(
                required_path_terms_any=["data-table"],
                required_dependencies_any=["@tanstack/react-table"],
            )
        ],
    )

    loaded = eval_runner.load_suite(str(suite_path))

    assert loaded["suite_id"] == "ui-reuse"
    assert loaded["tasks"][0]["id"] == "task"
    assert loaded["tasks"][0]["required_dependencies_any"] == ["@tanstack/react-table"]


def test_tracked_golden_suites_load_by_alias() -> None:
    ui_suite = eval_runner.load_suite("ui-reuse")
    backend_suite = eval_runner.load_suite("nextjs-backend")
    personal_suite = eval_runner.load_suite("personal-code")

    assert len(ui_suite["tasks"]) == 10
    assert len(backend_suite["tasks"]) == 10
    assert len(personal_suite["tasks"]) >= 10


def test_load_suite_rejects_missing_labels() -> None:
    with pytest.raises(ValueError, match="expected or acceptable"):
        eval_runner.validate_suite(
            {
                "suite_id": "bad",
                "tasks": [
                    {
                        "id": "bad",
                        "task": "Find something",
                        "capability": "data-table",
                    }
                ],
            }
        )


def test_evaluate_suite_scores_hits_constraints_and_mrr(tmp_path: Path) -> None:
    _asset(
        tmp_path,
        repo_id="good/repo",
        capability="data-table",
        entry_paths=["components/data-table/data-table.tsx"],
        dependencies=["@tanstack/react-table"],
    )
    suite = eval_runner.validate_suite(
        {
            "suite_id": "ui-reuse",
            "tasks": [
                _task(
                    required_path_terms_any=["data-table"],
                    required_dependencies_any=["@tanstack/react-table"],
                )
            ],
        }
    )

    report = eval_runner.evaluate_suite(suite, top_k=5, label="unit")

    assert report["metrics"]["top_1_hits"] == 1
    assert report["metrics"]["top_3_hits"] == 1
    assert report["metrics"]["mrr"] == 1.0
    assert report["tasks"][0]["candidates"][0]["failure_reasons"] == []


def test_evaluate_suite_tracks_avoid_violations(tmp_path: Path) -> None:
    _asset(
        tmp_path,
        repo_id="ufukayyildiz/omnidock",
        capability="settings",
        entry_paths=["src/worker/schema.ts"],
        score=1.0,
    )
    suite = eval_runner.validate_suite(
        {
            "suite_id": "ui-reuse",
            "tasks": [
                _task(
                    repo_id="missing/repo",
                    capability="settings",
                    avoid_repo_ids=["ufukayyildiz/omnidock"],
                )
            ],
        }
    )

    report = eval_runner.evaluate_suite(suite, top_k=5)

    assert report["metrics"]["avoid_repo_violations"] == 1
    assert "avoid_repo_in_top3" in report["tasks"][0]["candidates"][0]["failure_reasons"]


def test_eval_cli_writes_report(tmp_path: Path, monkeypatch, capsys) -> None:
    import source_scout.__main__ as main_module

    _asset(
        tmp_path,
        repo_id="good/repo",
        capability="data-table",
        entry_paths=["components/data-table/data-table.tsx"],
    )
    suite_path = _suite_file(tmp_path, [_task(required_path_terms_any=["data-table"])])
    output_path = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "source_scout",
            "eval",
            "--suite",
            str(suite_path),
            "--top-k",
            "5",
            "--label",
            "unit",
            "--output",
            str(output_path),
        ],
    )

    main_module.main()

    captured = capsys.readouterr()
    assert '"suite_id": "ui-reuse"' in captured.out
    assert output_path.exists()
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["metrics"]["top_1_hits"] == 1


@pytest.mark.asyncio
async def test_reuse_loop_report_records_find_assess_bundle_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset_id = _asset(
        tmp_path,
        repo_id="good/repo",
        capability="data-table",
        entry_paths=["components/data-table/data-table.tsx"],
        dependencies=["@tanstack/react-table"],
    )
    suite = eval_runner.validate_suite(
        {
            "suite_id": "ui-reuse",
            "description": "loop suite",
            "tasks": [_task(required_dependencies_any=["@tanstack/react-table"])],
        }
    )

    class FakeAssessment:
        final_verdict = "select"
        reuse_score = 0.86
        confidence = 0.77
        evidence_coverage = 0.66
        validation_notes = ["useful note", "", "second note"]

    async def fake_assess_candidate(**kwargs: Any) -> FakeAssessment:
        assert kwargs["candidate_id"] == asset_id
        assert kwargs["fastcontext_policy"] == "never"
        assert kwargs["max_evidence_rounds"] == 0
        return FakeAssessment()

    monkeypatch.setattr(eval_runner.assessor, "assess_candidate", fake_assess_candidate)
    output_path = tmp_path / "reuse-loop.json"

    report = await eval_runner.run_reuse_loop_report(
        str(_suite_file(tmp_path, suite["tasks"])),
        top_k=3,
        label="unit",
        output_path=output_path,
        limit_tasks=1,
    )

    task = report["tasks"][0]
    assert output_path.exists()
    assert report["metrics"]["task_count"] == 1
    assert report["metrics"]["top_k_expected_or_acceptable_hits"] == 1
    assert report["metrics"]["top_1_expected_or_acceptable_hits"] == 1
    assert report["metrics"]["top_1_expected_or_acceptable_hit_rate"] == 1.0
    assert report["metrics"]["selected_verdict_counts"] == {"select": 1}
    assert task["returned_candidates"] == [
        {"rank": 1, "candidate_id": asset_id, "repo_id": "good/repo"}
    ]
    assert task["expected_or_acceptable_repo_in_top_k"] is True
    assert task["selected_candidate_id"] == asset_id
    assert task["selected_repo_id"] == "good/repo"
    assert task["selected_is_expected_or_acceptable"] is True
    assert task["assessment_final_verdict"] == "select"
    assert task["reuse_score"] == 0.86
    assert task["confidence"] == 0.77
    assert task["evidence_coverage"] == 0.66
    assert Path(task["bundle_path"]).parts[-2:] == (asset_id, task["task_signature"])
    assert task["copied_file_count"] == 2
    assert task["missing_file_count"] == 0
    assert task["notable_validation_notes"] == ["useful note", "second note"]


@pytest.mark.asyncio
async def test_reuse_loop_report_attempts_bundle_when_assessment_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset_id = _asset(
        tmp_path,
        repo_id="good/repo",
        capability="data-table",
        entry_paths=["components/data-table/data-table.tsx"],
    )
    suite = eval_runner.validate_suite(
        {
            "suite_id": "ui-reuse",
            "description": "loop suite",
            "tasks": [_task()],
        }
    )

    async def fake_assess_candidate(**kwargs: Any) -> None:
        assert kwargs["candidate_id"] == asset_id
        raise RuntimeError("assessment failed")

    monkeypatch.setattr(eval_runner.assessor, "assess_candidate", fake_assess_candidate)

    report = await eval_runner.evaluate_reuse_loop_suite(suite, top_k=3, label="unit")

    task = report["tasks"][0]
    assert report["passed"] is False
    assert report["metrics"]["assessment_error_count"] == 1
    assert report["metrics"]["bundle_count"] == 1
    assert report["metrics"]["bundle_error_count"] == 0
    assert task["assessment_error"] == "assessment failed"
    assert task["bundle_error"] is None
    assert task["bundle_path"] is not None
    assert Path(task["bundle_path"]).parts[-2:] == (asset_id, task["task_signature"])


def test_eval_reuse_loop_cli_prints_summary(monkeypatch, capsys, tmp_path: Path) -> None:
    import source_scout.__main__ as main_module

    async def fake_run_reuse_loop_report(
        suite: str,
        top_k: int,
        label: str | None = None,
        output_path: Path | None = None,
        *,
        limit_tasks: int | None = None,
        fastcontext_policy: str = "never",
        max_evidence_rounds: int = 0,
        force_assessment: bool = True,
    ) -> dict[str, Any]:
        assert suite == "ui-reuse"
        assert top_k == 2
        assert label == "unit"
        assert output_path == tmp_path / "loop.json"
        assert limit_tasks == 1
        assert fastcontext_policy == "never"
        assert max_evidence_rounds == 0
        assert force_assessment is False
        return {
            "suite_id": "ui-reuse",
            "label": label,
            "passed": True,
            "metrics": {"task_count": 1},
            "report_path": str(output_path),
        }

    monkeypatch.setattr(eval_runner, "run_reuse_loop_report", fake_run_reuse_loop_report)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "source_scout",
            "eval-reuse-loop",
            "--suite",
            "ui-reuse",
            "--top-k",
            "2",
            "--label",
            "unit",
            "--output",
            str(tmp_path / "loop.json"),
            "--limit-tasks",
            "1",
            "--use-cache",
        ],
    )

    main_module.main()

    captured = capsys.readouterr()
    assert '"suite_id": "ui-reuse"' in captured.out
    assert '"task_count": 1' in captured.out
