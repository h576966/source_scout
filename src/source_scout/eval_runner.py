from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from . import assessor, bundles, catalog, eval_support

SUITE_ALIASES = {
    "ui-reuse": "ui_reuse_v1.json",
    "nextjs-backend": "nextjs_backend_v1.json",
    "personal-code": "personal_code_v1.json",
}
UI_REUSE_PASSING_TOP1 = 6
UI_REUSE_PASSING_TOP3 = 8
FastContextPolicy = Literal["auto", "always", "never"]


def load_suite(suite: str) -> dict[str, Any]:
    parsed = eval_support.load_suite_json(
        suite,
        SUITE_ALIASES,
        suite_label="eval",
        title_label="Eval",
    )
    return validate_suite(parsed)


def validate_suite(raw_suite: dict[str, Any]) -> dict[str, Any]:
    suite_id = str(raw_suite.get("suite_id", "")).strip()
    if not suite_id:
        raise ValueError("Eval suite requires suite_id.")
    tasks = raw_suite.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Eval suite requires a non-empty tasks list.")

    validated_tasks = []
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            raise ValueError(f"Task {index} must be an object.")
        validated_tasks.append(_validate_task(task, index))

    return {
        "suite_id": suite_id,
        "description": str(raw_suite.get("description", "")),
        "tasks": validated_tasks,
    }


def run_eval(
    suite: str,
    top_k: int,
    label: str | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    loaded = load_suite(suite)
    report = evaluate_suite(loaded, top_k=top_k, label=label)
    path = output_path or default_report_path(str(loaded["suite_id"]), label)
    eval_support.write_report(report, path)
    catalog.record_analysis_run(
        "eval",
        "completed" if report["passed"] else "failed",
        {
            "suite_id": loaded["suite_id"],
            "label": label,
            "top_k": top_k,
            "metrics": report["metrics"],
            "report_path": str(path),
        },
    )
    return report


async def run_reuse_loop_report(
    suite: str,
    top_k: int,
    label: str | None = None,
    output_path: Path | None = None,
    *,
    limit_tasks: int | None = None,
    fastcontext_policy: FastContextPolicy = "never",
    max_evidence_rounds: int = 0,
    force_assessment: bool = True,
    assessment_runtime: assessor.AssessmentRuntime | None = None,
) -> dict[str, Any]:
    loaded = load_suite(suite)
    report = await evaluate_reuse_loop_suite(
        loaded,
        top_k=top_k,
        label=label,
        limit_tasks=limit_tasks,
        fastcontext_policy=fastcontext_policy,
        max_evidence_rounds=max_evidence_rounds,
        force_assessment=force_assessment,
        assessment_runtime=assessment_runtime,
    )
    path = output_path or reuse_loop_report_path(str(loaded["suite_id"]), label)
    eval_support.write_report(report, path)
    catalog.record_analysis_run(
        "eval-reuse-loop",
        "completed" if report["passed"] else "failed",
        {
            "suite_id": loaded["suite_id"],
            "label": label,
            "top_k": top_k,
            "limit_tasks": limit_tasks,
            "fastcontext_policy": fastcontext_policy,
            "max_evidence_rounds": max_evidence_rounds,
            "metrics": report["metrics"],
            "report_path": str(path),
        },
    )
    return report


def evaluate_suite(suite: dict[str, Any], top_k: int, label: str | None = None) -> dict[str, Any]:
    if top_k < 1:
        raise ValueError("top_k must be at least 1.")

    task_reports = [_evaluate_task(task, top_k) for task in suite["tasks"]]
    metrics = _metrics(task_reports)
    passed = _passes_threshold(str(suite["suite_id"]), metrics)
    return {
        "suite_id": suite["suite_id"],
        "description": suite.get("description", ""),
        "label": label,
        "top_k": top_k,
        "timestamp": datetime.now(UTC).isoformat(),
        "passed": passed,
        "metrics": metrics,
        "tasks": task_reports,
    }


async def evaluate_reuse_loop_suite(
    suite: dict[str, Any],
    *,
    top_k: int,
    label: str | None = None,
    limit_tasks: int | None = None,
    fastcontext_policy: FastContextPolicy = "never",
    max_evidence_rounds: int = 0,
    force_assessment: bool = True,
    assessment_runtime: assessor.AssessmentRuntime | None = None,
) -> dict[str, Any]:
    if top_k < 1:
        raise ValueError("top_k must be at least 1.")
    if limit_tasks is not None and limit_tasks < 1:
        raise ValueError("limit_tasks must be at least 1.")
    if fastcontext_policy not in {"auto", "always", "never"}:
        raise ValueError("fastcontext_policy must be one of: auto, always, never.")
    if max_evidence_rounds < 0 or max_evidence_rounds > 2:
        raise ValueError("max_evidence_rounds must be between 0 and 2.")

    tasks = list(suite["tasks"])
    if limit_tasks is not None:
        tasks = tasks[:limit_tasks]
    task_reports = [
        await _evaluate_reuse_loop_task(
            task,
            top_k=top_k,
            fastcontext_policy=fastcontext_policy,
            max_evidence_rounds=max_evidence_rounds,
            force_assessment=force_assessment,
            assessment_runtime=assessment_runtime,
        )
        for task in tasks
    ]
    metrics = _reuse_loop_metrics(task_reports)
    return {
        "suite_id": suite["suite_id"],
        "description": suite.get("description", ""),
        "label": label,
        "top_k": top_k,
        "limit_tasks": limit_tasks,
        "fastcontext_policy": fastcontext_policy,
        "max_evidence_rounds": max_evidence_rounds,
        "timestamp": datetime.now(UTC).isoformat(),
        "passed": (
            int(metrics["top_k_expected_or_acceptable_hits"]) == int(metrics["task_count"])
            and int(metrics["assessment_error_count"]) == 0
            and int(metrics["bundle_error_count"]) == 0
        ),
        "metrics": metrics,
        "tasks": task_reports,
    }


def default_report_path(suite_id: str, label: str | None = None) -> Path:
    return eval_support.default_report_path("eval_runs", suite_id, label)


def reuse_loop_report_path(suite_id: str, label: str | None = None) -> Path:
    return eval_support.default_report_path("reuse_loop_reports", suite_id, label)


def _suite_path(suite: str) -> Path:
    return eval_support.suite_path(suite, SUITE_ALIASES, suite_label="eval")


def _validate_task(task: dict[str, Any], index: int) -> dict[str, Any]:
    task_id = str(task.get("id", "")).strip()
    task_text = str(task.get("task", "")).strip()
    capability = str(task.get("capability", "")).strip()
    if not task_id or not task_text or not capability:
        raise ValueError(f"Task {index} requires id, task, and capability.")
    expected = _string_list(task.get("expected_repo_ids"))
    acceptable = _string_list(task.get("acceptable_repo_ids"))
    if not expected and not acceptable:
        raise ValueError(f"Task {task_id} requires expected or acceptable repos.")
    return {
        "id": task_id,
        "task": task_text,
        "capability": capability,
        "expected_repo_ids": expected,
        "acceptable_repo_ids": acceptable,
        "avoid_repo_ids": _string_list(task.get("avoid_repo_ids")),
        "required_path_terms_any": _string_list(task.get("required_path_terms_any")),
        "required_dependencies_any": _string_list(task.get("required_dependencies_any")),
        "max_rank_for_hit": int(task.get("max_rank_for_hit", 3)),
        "notes": str(task.get("notes", "")),
    }


def _evaluate_task(task: dict[str, Any], top_k: int) -> dict[str, Any]:
    results = catalog.search_assets(str(task["task"]), max_repos=top_k)
    candidates: list[dict[str, Any]] = []
    first_hit_rank: int | None = None
    blocked_label_match = False
    avoid_violations = 0
    max_rank = int(task["max_rank_for_hit"])

    for rank, candidate in enumerate(results, start=1):
        expected = candidate.repo_id in task["expected_repo_ids"]
        acceptable = candidate.repo_id in task["acceptable_repo_ids"]
        label_match = expected or acceptable
        path_ok = _path_constraint_ok(candidate, task["required_path_terms_any"])
        dependency_ok = _dependency_constraint_ok(candidate, task["required_dependencies_any"])
        evidence_ok = bool(candidate.evidence_paths)
        avoid_violation = (
            rank <= 3
            and candidate.repo_id in task["avoid_repo_ids"]
            and not _avoid_exception(candidate, str(task["capability"]))
        )
        if avoid_violation:
            avoid_violations += 1

        failure_reasons = _failure_reasons(
            label_match=label_match,
            path_ok=path_ok,
            dependency_ok=dependency_ok,
            evidence_ok=evidence_ok,
            avoid_violation=avoid_violation,
        )
        if label_match and rank <= max_rank and (not path_ok or not dependency_ok or not evidence_ok):
            blocked_label_match = True
        if (
            first_hit_rank is None
            and label_match
            and rank <= max_rank
            and path_ok
            and dependency_ok
            and evidence_ok
            and not avoid_violation
        ):
            first_hit_rank = rank

        candidates.append(
            {
                "rank": rank,
                "candidate_id": candidate.candidate_id,
                "repo_id": candidate.repo_id,
                "score": candidate.score,
                "capability": candidate.capability,
                "label_match": label_match,
                "expected_match": expected,
                "acceptable_match": acceptable,
                "entry_paths": candidate.entry_paths,
                "external_dependencies": candidate.external_dependencies,
                "evidence_paths": candidate.evidence_paths,
                "failure_reasons": failure_reasons,
            }
        )

    constraint_failures = 1 if first_hit_rank is None and blocked_label_match else 0
    return {
        "id": task["id"],
        "task": task["task"],
        "capability": task["capability"],
        "max_rank_for_hit": max_rank,
        "first_hit_rank": first_hit_rank,
        "top_1_hit": first_hit_rank == 1,
        "top_3_hit": first_hit_rank is not None and first_hit_rank <= 3,
        "top_5_hit": first_hit_rank is not None and first_hit_rank <= 5,
        "avoid_violations": avoid_violations,
        "constraint_failures": constraint_failures,
        "candidates": candidates,
    }


async def _evaluate_reuse_loop_task(
    task: dict[str, Any],
    *,
    top_k: int,
    fastcontext_policy: FastContextPolicy,
    max_evidence_rounds: int,
    force_assessment: bool,
    assessment_runtime: assessor.AssessmentRuntime | None,
) -> dict[str, Any]:
    task_text = str(task["task"])
    task_signature = catalog.task_signature(task_text)
    results = catalog.search_assets(task_text, max_repos=top_k)
    returned = [
        {
            "rank": rank,
            "candidate_id": candidate.candidate_id,
            "repo_id": candidate.repo_id,
        }
        for rank, candidate in enumerate(results, start=1)
    ]
    hit_rank = _expected_or_acceptable_rank(
        returned,
        expected_repo_ids=task["expected_repo_ids"],
        acceptable_repo_ids=task["acceptable_repo_ids"],
    )
    selected = results[0] if results else None
    selected_candidate_id = selected.candidate_id if selected is not None else None

    assessment_fields: dict[str, Any] = {
        "assessment_final_verdict": None,
        "reuse_score": None,
        "confidence": None,
        "evidence_coverage": None,
        "notable_validation_notes": [],
        "assessment_error": None,
    }
    bundle_fields: dict[str, Any] = {
        "bundle_path": None,
        "copied_file_count": 0,
        "missing_file_count": 0,
        "bundle_error": None,
    }
    if selected_candidate_id is not None:
        assessment_fields = await _reuse_loop_assessment_fields(
            candidate_id=selected_candidate_id,
            task=task_text,
            fastcontext_policy=fastcontext_policy,
            max_evidence_rounds=max_evidence_rounds,
            force_assessment=force_assessment,
            assessment_runtime=assessment_runtime,
        )
        if assessment_fields["assessment_error"] is None:
            bundle_fields = _reuse_loop_bundle_fields(selected_candidate_id, task_signature)

    return {
        "id": task["id"],
        "task": task_text,
        "task_signature": task_signature,
        "expected_repo_ids": task["expected_repo_ids"],
        "acceptable_repo_ids": task["acceptable_repo_ids"],
        "returned_candidates": returned,
        "expected_or_acceptable_repo_in_top_k": hit_rank is not None,
        "first_expected_or_acceptable_rank": hit_rank,
        "selected_candidate_id": selected_candidate_id,
        **assessment_fields,
        **bundle_fields,
    }


async def _reuse_loop_assessment_fields(
    *,
    candidate_id: str,
    task: str,
    fastcontext_policy: FastContextPolicy,
    max_evidence_rounds: int,
    force_assessment: bool,
    assessment_runtime: assessor.AssessmentRuntime | None,
) -> dict[str, Any]:
    try:
        assessment = await assessor.assess_candidate(
            candidate_id=candidate_id,
            task=task,
            fastcontext_policy=fastcontext_policy,
            max_evidence_rounds=max_evidence_rounds,
            force=force_assessment,
            runtime=assessment_runtime,
        )
    except Exception as exc:
        return {
            "assessment_final_verdict": None,
            "reuse_score": None,
            "confidence": None,
            "evidence_coverage": None,
            "notable_validation_notes": [],
            "assessment_error": str(exc),
        }
    return {
        "assessment_final_verdict": str(assessment.final_verdict),
        "reuse_score": float(assessment.reuse_score),
        "confidence": float(assessment.confidence),
        "evidence_coverage": float(assessment.evidence_coverage),
        "notable_validation_notes": _notable_validation_notes(assessment.validation_notes),
        "assessment_error": None,
    }


def _reuse_loop_bundle_fields(candidate_id: str, task_signature: str) -> dict[str, Any]:
    try:
        bundle = bundles.create_source_bundle(candidate_id, task_signature)
    except Exception as exc:
        return {
            "bundle_path": None,
            "copied_file_count": 0,
            "missing_file_count": 0,
            "bundle_error": str(exc),
        }
    return {
        "bundle_path": bundle.bundle_path,
        "copied_file_count": len(bundle.files),
        "missing_file_count": len(bundle.missing_files),
        "bundle_error": None,
    }


def _metrics(task_reports: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(task_reports)
    top_1 = sum(1 for task in task_reports if task["top_1_hit"])
    top_3 = sum(1 for task in task_reports if task["top_3_hit"])
    top_5 = sum(1 for task in task_reports if task["top_5_hit"])
    reciprocal_sum = sum(1 / task["first_hit_rank"] for task in task_reports if task["first_hit_rank"])
    avoid_violations = sum(int(task["avoid_violations"]) for task in task_reports)
    constraint_failures = sum(int(task["constraint_failures"]) for task in task_reports)
    return {
        "task_count": total,
        "top_1_hits": top_1,
        "top_3_hits": top_3,
        "top_5_hits": top_5,
        "top_1_hit_rate": round(top_1 / total, 4) if total else 0.0,
        "top_3_hit_rate": round(top_3 / total, 4) if total else 0.0,
        "top_5_hit_rate": round(top_5 / total, 4) if total else 0.0,
        "mrr": round(reciprocal_sum / total, 4) if total else 0.0,
        "avoid_repo_violations": avoid_violations,
        "evidence_constraint_failures": constraint_failures,
    }


def _reuse_loop_metrics(task_reports: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(task_reports)
    top_k_hits = sum(1 for task in task_reports if task["expected_or_acceptable_repo_in_top_k"])
    assessed = sum(1 for task in task_reports if task["assessment_final_verdict"] is not None)
    bundled = sum(1 for task in task_reports if task["bundle_path"])
    return {
        "task_count": total,
        "top_k_expected_or_acceptable_hits": top_k_hits,
        "top_k_expected_or_acceptable_hit_rate": round(top_k_hits / total, 4) if total else 0.0,
        "assessed_count": assessed,
        "selected_verdict_counts": _count_values(
            [
                str(task["assessment_final_verdict"])
                for task in task_reports
                if task["assessment_final_verdict"] is not None
            ]
        ),
        "assessment_error_count": sum(1 for task in task_reports if task["assessment_error"]),
        "bundle_count": bundled,
        "bundle_error_count": sum(1 for task in task_reports if task["bundle_error"]),
        "copied_file_count": sum(int(task["copied_file_count"]) for task in task_reports),
        "missing_file_count": sum(int(task["missing_file_count"]) for task in task_reports),
    }


def _expected_or_acceptable_rank(
    returned: list[dict[str, Any]],
    *,
    expected_repo_ids: list[str],
    acceptable_repo_ids: list[str],
) -> int | None:
    labeled = set(expected_repo_ids) | set(acceptable_repo_ids)
    for candidate in returned:
        if candidate["repo_id"] in labeled:
            return int(candidate["rank"])
    return None


def _notable_validation_notes(notes: list[str]) -> list[str]:
    return [note for note in notes if note.strip()][:5]


def _count_values(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _passes_threshold(suite_id: str, metrics: dict[str, Any]) -> bool:
    if suite_id == "ui-reuse":
        return (
            int(metrics["top_3_hits"]) >= UI_REUSE_PASSING_TOP3
            and int(metrics["top_1_hits"]) >= UI_REUSE_PASSING_TOP1
            and int(metrics["avoid_repo_violations"]) == 0
            and int(metrics["evidence_constraint_failures"]) == 0
        )
    return int(metrics["top_3_hits"]) >= max(1, int(metrics["task_count"]) // 2)


def _path_constraint_ok(candidate: Any, required_terms: list[str]) -> bool:
    if not required_terms:
        return True
    searchable = " ".join(candidate.entry_paths + candidate.evidence_paths).lower()
    return any(term.lower() in searchable for term in required_terms)


def _dependency_constraint_ok(candidate: Any, required_dependencies: list[str]) -> bool:
    if not required_dependencies:
        return True
    available = {dependency.lower() for dependency in candidate.external_dependencies}
    return any(dependency.lower() in available for dependency in required_dependencies)


def _avoid_exception(candidate: Any, capability: str) -> bool:
    if candidate.repo_id == "ufukayyildiz/omnidock":
        return any(path.startswith("src/ui/") for path in candidate.entry_paths + candidate.evidence_paths)
    if (
        candidate.repo_id == "x0ll/Ransomware-Attack-Detection-Using-Machine-Learning"
        and capability == "data-table"
    ):
        text = " ".join(candidate.entry_paths + candidate.evidence_paths).lower()
        return any(term in text for term in ("table", "data-table", "columns"))
    return False


def _failure_reasons(
    *,
    label_match: bool,
    path_ok: bool,
    dependency_ok: bool,
    evidence_ok: bool,
    avoid_violation: bool,
) -> list[str]:
    reasons: list[str] = []
    if not label_match:
        reasons.append("repo_not_labeled_relevant")
    if not path_ok:
        reasons.append("missing_required_path_term")
    if not dependency_ok:
        reasons.append("missing_required_dependency")
    if not evidence_ok:
        reasons.append("missing_evidence_paths")
    if avoid_violation:
        reasons.append("avoid_repo_in_top3")
    return reasons


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Expected a list of strings.")
    return [str(item) for item in value]


def _safe_label(label: str | None) -> str:
    return eval_support.safe_label(label)
