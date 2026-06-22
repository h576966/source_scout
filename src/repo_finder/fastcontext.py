import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from . import catalog, lmstudio
from .constants import SKIP_DIRS

PROMPT_VERSION = "fastcontext-refine-v1"
SCHEMA_VERSION = "fastcontext-evidence-v1"
ANALYZER_VERSION = "fastcontext-harness-v1"

DEFAULT_MAX_TURNS = 6
MAX_TOOL_CALLS_PER_TURN = 5
MAX_GLOB_RESULTS = 80
MAX_GREP_RESULTS = 80
MAX_READ_LINES = 160
MAX_READ_FILE_BYTES = 240_000
MAX_GREP_FILE_BYTES = 1_000_000


class FastContextError(RuntimeError):
    pass


@dataclass(frozen=True)
class FastContextCitation:
    path: str
    start_line: int | None = None
    end_line: int | None = None
    reason: str = ""

    def evidence_path(self) -> str:
        if self.start_line is None:
            return self.path
        end_line = self.end_line if self.end_line is not None else self.start_line
        return f"{self.path}:{self.start_line}-{max(self.start_line, end_line)}"


@dataclass(frozen=True)
class ParsedFastContextResponse:
    tool_calls: list[dict[str, Any]]
    citations: list[FastContextCitation]
    notes: list[str]


async def ensure_fastcontext_available(
    config: lmstudio.LMStudioConfig | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    active = config or lmstudio.get_config()
    status = await lmstudio.validate_models(active, transport=transport)
    if not status["fastcontext_available"]:
        raise lmstudio.LMStudioError(
            f"Configured FastContext model '{active.fastcontext_model}' is not available in LM Studio."
        )


async def smoke_test(
    config: lmstudio.LMStudioConfig | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    active = config or lmstudio.get_config()
    await ensure_fastcontext_available(active, transport=transport)
    content = await lmstudio.chat_text(
        model_id=active.fastcontext_model,
        messages=[
            {
                "role": "system",
                "content": "Return only valid JSON.",
            },
            {
                "role": "user",
                "content": 'Return exactly {"ok": true}.',
            },
        ],
        config=active,
        transport=transport,
        max_tokens=100,
        temperature=0.0,
    )
    return lmstudio.parse_json_content(content)


async def refine_candidate(
    candidate_id: str,
    task: str,
    max_turns: int = DEFAULT_MAX_TURNS,
    transport: httpx.AsyncBaseTransport | None = None,
    validate_model: bool = True,
) -> dict[str, Any]:
    if not task.strip():
        raise FastContextError("task is required.")

    asset = catalog.get_asset_detail(candidate_id)
    if asset is None:
        raise FastContextError(f"Unknown candidate_id: {candidate_id}")

    config = lmstudio.get_config()
    snapshot_root = Path(str(asset["snapshot_path"]))
    if not snapshot_root.exists() or not snapshot_root.is_dir():
        raise FastContextError(f"Snapshot path does not exist: {snapshot_root}")

    task_sig = catalog.task_signature(task)
    query = _build_query(asset, task)
    trajectory: list[dict[str, Any]] = []

    try:
        if validate_model:
            await ensure_fastcontext_available(config, transport=transport)
        messages = _messages(asset, query)
        for turn in range(1, max(1, max_turns) + 1):
            content = await lmstudio.chat_text(
                model_id=config.fastcontext_model,
                messages=messages,
                config=config,
                transport=transport,
                max_tokens=3000,
                temperature=0.0,
            )
            parsed = parse_fastcontext_response(content)
            trajectory.append(
                {
                    "turn": turn,
                    "model_response": content,
                    "tool_calls": parsed.tool_calls,
                    "final_citations": [citation.evidence_path() for citation in parsed.citations],
                }
            )

            if parsed.citations:
                evidence_paths, validation_notes = _validated_evidence_paths(
                    snapshot_root,
                    parsed.citations,
                )
                if evidence_paths:
                    notes = [*parsed.notes, *validation_notes]
                    return _store_refinement(
                        asset=asset,
                        candidate_id=candidate_id,
                        task_signature=task_sig,
                        model_id=config.fastcontext_model,
                        query=query,
                        evidence_paths=evidence_paths,
                        notes=notes,
                        trajectory=trajectory,
                    )

            if parsed.tool_calls:
                observations = [
                    execute_tool(snapshot_root, call)
                    for call in parsed.tool_calls[:MAX_TOOL_CALLS_PER_TURN]
                ]
                trajectory[-1]["tool_observations"] = observations
                messages.extend(
                    [
                        {"role": "assistant", "content": content},
                        {
                            "role": "user",
                            "content": (
                                "Tool observations JSON:\n"
                                f"{json.dumps(observations, sort_keys=True)}\n\n"
                                "Continue. Return either more tool_calls JSON or final_answer JSON."
                            ),
                        },
                    ]
                )
                continue

            messages.extend(
                [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "That response did not contain usable tool calls or citations. "
                            "Return JSON with tool_calls, or JSON final_answer with evidence paths."
                        ),
                    },
                ]
            )

        raise FastContextError("FastContext did not return usable evidence before max_turns.")
    except Exception as exc:
        catalog.record_analysis_run(
            "fastcontext-refine",
            "failed",
            {"candidate_id": candidate_id, "task_signature": task_sig, "error": str(exc)},
            repo_id=str(asset["repo_id"]),
            snapshot_id=str(asset["snapshot_id"]),
            model_id=config.fastcontext_model,
            prompt_version=PROMPT_VERSION,
            analyzer_version=ANALYZER_VERSION,
        )
        raise


async def refine_suite(
    suite: str,
    top_k: int,
    label: str | None = None,
    output_path: Path | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    limit_tasks: int | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    if top_k < 1:
        raise FastContextError("top_k must be at least 1.")
    if limit_tasks is not None and limit_tasks < 1:
        raise FastContextError("limit_tasks must be at least 1.")

    from . import eval_runner

    loaded_suite = eval_runner.load_suite(suite)
    suite_id = str(loaded_suite["suite_id"])
    config = lmstudio.get_config()
    await ensure_fastcontext_available(config, transport=transport)

    tasks = list(loaded_suite["tasks"])
    if limit_tasks is not None:
        tasks = tasks[:limit_tasks]

    task_reports = []
    for task in tasks:
        task_reports.append(
            await _refine_suite_task(
                task=task,
                top_k=top_k,
                max_turns=max_turns,
                transport=transport,
            )
        )

    metrics = _batch_metrics(task_reports)
    report_path = output_path or default_refinement_report_path(suite_id, label)
    report = {
        "suite_id": suite_id,
        "description": loaded_suite.get("description", ""),
        "label": label,
        "top_k": top_k,
        "max_turns": max_turns,
        "model_id": config.fastcontext_model,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "timestamp": datetime.now(UTC).isoformat(),
        "metrics": metrics,
        "scoring_recommendation": _scoring_recommendation(metrics),
        "tasks": task_reports,
        "report_path": str(report_path),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    catalog.record_analysis_run(
        "fastcontext-batch-refine",
        "completed" if int(metrics["failed_refinements"]) == 0 else "completed_with_failures",
        {
            "suite_id": suite_id,
            "label": label,
            "top_k": top_k,
            "max_turns": max_turns,
            "metrics": metrics,
            "report_path": str(report_path),
        },
        model_id=config.fastcontext_model,
        prompt_version=PROMPT_VERSION,
        analyzer_version=ANALYZER_VERSION,
    )
    return report


def default_refinement_report_path(suite_id: str, label: str | None = None) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    suffix = f"_{_safe_label(label)}" if label else ""
    return catalog.ensure_home() / "fastcontext_runs" / suite_id / f"{timestamp}{suffix}.json"


def parse_fastcontext_response(content: str) -> ParsedFastContextResponse:
    try:
        parsed = lmstudio.parse_json_content(content)
    except lmstudio.LMStudioError:
        return ParsedFastContextResponse(
            tool_calls=_parse_function_style_tool_calls(content),
            citations=_parse_final_answer_citations(content),
            notes=[],
        )

    return ParsedFastContextResponse(
        tool_calls=_extract_tool_calls(parsed),
        citations=_extract_citations(parsed),
        notes=_extract_notes(parsed),
    )


def execute_tool(root: Path, call: dict[str, Any]) -> dict[str, Any]:
    tool = _tool_name(call)
    args = _tool_args(call)
    try:
        if tool == "READ":
            result = read_file(
                root,
                str(args.get("path", "")),
                start=_optional_int(args.get("start") or args.get("start_line")),
                end=_optional_int(args.get("end") or args.get("end_line")),
            )
        elif tool == "GLOB":
            result = glob_paths(root, str(args.get("pattern") or args.get("glob") or "**/*"))
        elif tool == "GREP":
            result = grep_paths(
                root,
                str(args.get("pattern", "")),
                file_glob=str(args["glob"]) if args.get("glob") else None,
            )
        else:
            raise FastContextError(f"Unsupported tool: {tool}")
        return {"tool": tool, "args": args, "ok": True, "result": result}
    except Exception as exc:
        return {"tool": tool, "args": args, "ok": False, "error": str(exc)}


def read_file(
    root: Path,
    rel_path: str,
    start: int | None = None,
    end: int | None = None,
) -> dict[str, Any]:
    path, safe_rel = _resolve_under_root(root, rel_path)
    if not path.is_file():
        raise FastContextError(f"READ target is not a file: {safe_rel}")
    if path.stat().st_size > MAX_READ_FILE_BYTES:
        raise FastContextError(f"READ target is too large: {safe_rel}")

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return {"path": safe_rel, "start_line": 1, "end_line": 0, "content": ""}

    start_line = min(max(1, start or 1), len(lines))
    end_line = min(len(lines), end or start_line + MAX_READ_LINES - 1)
    if end_line - start_line + 1 > MAX_READ_LINES:
        end_line = start_line + MAX_READ_LINES - 1
    selected = lines[start_line - 1 : end_line]
    content = "\n".join(
        f"{line_number}: {line}"
        for line_number, line in enumerate(selected, start=start_line)
    )
    return {
        "path": safe_rel,
        "start_line": start_line,
        "end_line": end_line,
        "content": content,
    }


def glob_paths(root: Path, pattern: str, limit: int = MAX_GLOB_RESULTS) -> dict[str, Any]:
    safe_pattern = _safe_glob_pattern(pattern)
    matches: list[str] = []
    paths = sorted(
        (path for path in root.glob(safe_pattern)),
        key=lambda path: path.as_posix().lower(),
    )
    for path in paths:
        if len(matches) >= limit:
            break
        if _should_skip_path(root, path):
            continue
        if path.is_file():
            matches.append(_relative_path(root, path))
    matches.sort()
    return {"pattern": safe_pattern, "matches": matches, "truncated": len(matches) >= limit}


def grep_paths(
    root: Path,
    pattern: str,
    file_glob: str | None = None,
    limit: int = MAX_GREP_RESULTS,
) -> dict[str, Any]:
    if not pattern.strip():
        raise FastContextError("GREP requires a non-empty pattern.")
    try:
        compiled = re.compile(pattern, flags=re.IGNORECASE)
        regex_mode = True
    except re.error:
        compiled = re.compile(re.escape(pattern), flags=re.IGNORECASE)
        regex_mode = False

    candidates = _grep_candidate_files(root, file_glob)
    matches: list[dict[str, Any]] = []
    for path in candidates:
        if len(matches) >= limit:
            break
        if path.stat().st_size > MAX_GREP_FILE_BYTES:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel_path = _relative_path(root, path)
        for line_number, line in enumerate(lines, start=1):
            if not compiled.search(line):
                continue
            matches.append(
                {
                    "path": rel_path,
                    "line": line_number,
                    "citation": f"{rel_path}:{line_number}-{line_number}",
                    "text": line.strip()[:220],
                }
            )
            if len(matches) >= limit:
                break

    return {
        "pattern": pattern,
        "glob": file_glob,
        "regex_mode": regex_mode,
        "matches": matches,
        "truncated": len(matches) >= limit,
    }


async def _refine_suite_task(
    *,
    task: dict[str, Any],
    top_k: int,
    max_turns: int,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    candidates = catalog.search_assets(str(task["task"]), max_repos=top_k)
    candidate_reports = []
    for rank, candidate in enumerate(candidates, start=1):
        report = _deterministic_candidate_report(task, candidate, rank)
        try:
            refinement = await refine_candidate(
                candidate_id=candidate.candidate_id,
                task=str(task["task"]),
                max_turns=max_turns,
                transport=transport,
                validate_model=False,
            )
            refined_paths = [str(path) for path in refinement["evidence_paths"]]
            report.update(
                {
                    "refinement_status": "completed",
                    "refinement_id": refinement["refinement_id"],
                    "analysis_run_id": refinement["analysis_run_id"],
                    "refined_evidence_paths": refined_paths,
                    "refined_evidence_count": len(refined_paths),
                    "refined_path_constraint_ok": _path_terms_ok(
                        refined_paths,
                        task["required_path_terms_any"],
                    ),
                    "refined_notes": refinement.get("notes", []),
                }
            )
        except Exception as exc:
            report.update(
                {
                    "refinement_status": "failed",
                    "refinement_error": str(exc),
                    "refined_evidence_paths": [],
                    "refined_evidence_count": 0,
                    "refined_path_constraint_ok": False,
                    "refined_notes": [],
                }
            )
        candidate_reports.append(report)

    return {
        "id": task["id"],
        "task": task["task"],
        "capability": task["capability"],
        "task_signature": catalog.task_signature(str(task["task"])),
        "expected_repo_ids": task["expected_repo_ids"],
        "acceptable_repo_ids": task["acceptable_repo_ids"],
        "required_path_terms_any": task["required_path_terms_any"],
        "required_dependencies_any": task["required_dependencies_any"],
        "candidate_count": len(candidate_reports),
        "completed_refinements": sum(
            1 for candidate in candidate_reports if candidate["refinement_status"] == "completed"
        ),
        "failed_refinements": sum(
            1 for candidate in candidate_reports if candidate["refinement_status"] == "failed"
        ),
        "candidates": candidate_reports,
    }


def _deterministic_candidate_report(task: dict[str, Any], candidate: Any, rank: int) -> dict[str, Any]:
    label_match = (
        candidate.repo_id in task["expected_repo_ids"]
        or candidate.repo_id in task["acceptable_repo_ids"]
    )
    deterministic_paths = [str(path) for path in candidate.evidence_paths]
    return {
        "rank": rank,
        "candidate_id": candidate.candidate_id,
        "repo_id": candidate.repo_id,
        "capability": candidate.capability,
        "score": candidate.score,
        "label_match": label_match,
        "entry_paths": candidate.entry_paths,
        "external_dependencies": candidate.external_dependencies,
        "deterministic_evidence_paths": deterministic_paths,
        "deterministic_evidence_count": len(deterministic_paths),
        "deterministic_path_constraint_ok": _path_terms_ok(
            candidate.entry_paths + candidate.evidence_paths,
            task["required_path_terms_any"],
        ),
        "dependency_constraint_ok": _dependencies_ok(
            candidate.external_dependencies,
            task["required_dependencies_any"],
        ),
    }


def _batch_metrics(task_reports: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [
        candidate
        for task in task_reports
        for candidate in task["candidates"]
    ]
    total_candidates = len(candidates)
    completed = sum(1 for candidate in candidates if candidate["refinement_status"] == "completed")
    failed = total_candidates - completed
    deterministic_evidence_total = sum(
        int(candidate["deterministic_evidence_count"]) for candidate in candidates
    )
    refined_evidence_total = sum(int(candidate["refined_evidence_count"]) for candidate in candidates)
    label_matches = [candidate for candidate in candidates if candidate["label_match"]]
    refined_label_matches = [
        candidate
        for candidate in label_matches
        if candidate["refinement_status"] == "completed"
        and int(candidate["refined_evidence_count"]) > 0
    ]
    refined_path_constraint_failures = sum(
        1
        for candidate in label_matches
        if candidate["refinement_status"] == "completed"
        and not candidate["refined_path_constraint_ok"]
    )
    top_1_refined = sum(
        1
        for task in task_reports
        if task["candidates"]
        and task["candidates"][0]["label_match"]
        and task["candidates"][0]["refinement_status"] == "completed"
        and int(task["candidates"][0]["refined_evidence_count"]) > 0
    )
    return {
        "task_count": len(task_reports),
        "candidate_count": total_candidates,
        "completed_refinements": completed,
        "failed_refinements": failed,
        "refinement_success_rate": round(completed / total_candidates, 4) if total_candidates else 0.0,
        "label_match_count": len(label_matches),
        "refined_label_match_count": len(refined_label_matches),
        "top_1_label_matches_with_refined_evidence": top_1_refined,
        "refined_path_constraint_failures": refined_path_constraint_failures,
        "deterministic_evidence_paths_total": deterministic_evidence_total,
        "refined_evidence_paths_total": refined_evidence_total,
        "evidence_compaction_ratio": round(
            refined_evidence_total / deterministic_evidence_total,
            4,
        ) if deterministic_evidence_total else 0.0,
    }


def _scoring_recommendation(metrics: dict[str, Any]) -> dict[str, str]:
    if int(metrics["candidate_count"]) == 0:
        return {
            "status": "not_ready",
            "reason": "No candidates were refined.",
            "next_step": "Refresh deterministic evidence before using FastContext for scoring.",
        }
    if int(metrics["failed_refinements"]) > 0:
        return {
            "status": "not_ready",
            "reason": "Some FastContext refinements failed.",
            "next_step": "Fix prompt/runtime failures before wiring refined evidence into scoring.",
        }
    if float(metrics["refinement_success_rate"]) < 0.9:
        return {
            "status": "not_ready",
            "reason": "Refinement coverage is below 90%.",
            "next_step": "Run more batch refinements and inspect failure modes.",
        }
    if int(metrics["refined_path_constraint_failures"]) > 0:
        return {
            "status": "cautious",
            "reason": "Some labeled candidates produced refined evidence that missed required path terms.",
            "next_step": "Use refined evidence only as a tie-breaker until path constraints are stable.",
        }
    return {
        "status": "tie_breaker_ready",
        "reason": "FastContext refined all candidates with task-linked citations.",
        "next_step": (
            "Use refined evidence as a small tie-breaker or confidence boost for already-shortlisted "
            "candidates, not as a replacement for deterministic gates."
        ),
    }


def _path_terms_ok(paths: list[str], required_terms: list[str]) -> bool:
    if not required_terms:
        return True
    searchable = " ".join(paths).lower()
    return any(term.lower() in searchable for term in required_terms)


def _dependencies_ok(dependencies: list[str], required_dependencies: list[str]) -> bool:
    if not required_dependencies:
        return True
    available = {dependency.lower() for dependency in dependencies}
    return any(dependency.lower() in available for dependency in required_dependencies)


def _build_query(asset: dict[str, Any], task: str) -> str:
    return (
        f"{task.strip()}\n"
        f"Capability: {asset['capability']}\n"
        "Find the smallest set of source files and line ranges that prove whether this "
        "candidate is reusable for the task."
    )


def _messages(asset: dict[str, Any], query: str) -> list[dict[str, str]]:
    context = {
        "repo_id": asset["repo_id"],
        "commit_sha": asset["commit_sha"],
        "capability": asset["capability"],
        "entry_paths": asset["entry_paths"],
        "dependency_paths": asset["dependency_paths"],
        "external_dependencies": asset["external_dependencies"],
        "deterministic_evidence_paths": asset["evidence_paths"],
    }
    return [
        {
            "role": "system",
            "content": (
                "You are FastContext, a read-only repository exploration subagent. "
                "Never execute code and never suggest edits. Use only these JSON tools:\n"
                '{"tool_calls":[{"tool":"GREP","args":{"pattern":"string","glob":"**/*.ts"}}]}\n'
                '{"tool_calls":[{"tool":"GLOB","args":{"pattern":"**/*.tsx"}}]}\n'
                '{"tool_calls":[{"tool":"READ","args":{"path":"relative/file.ts","start":1,"end":80}}]}\n'
                "When done, return only JSON: "
                '{"final_answer":{"evidence":[{"path":"relative/file.ts","start_line":1,'
                '"end_line":20,"reason":"why this matters"}],"notes":["short note"]}}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Context JSON:\n{json.dumps(context, sort_keys=True)}\n\n"
                f"Exploration query:\n{query}"
            ),
        },
    ]


def _store_refinement(
    *,
    asset: dict[str, Any],
    candidate_id: str,
    task_signature: str,
    model_id: str,
    query: str,
    evidence_paths: list[str],
    notes: list[str],
    trajectory: list[dict[str, Any]],
) -> dict[str, Any]:
    refinement_id = catalog.store_evidence_refinement(
        asset_id=candidate_id,
        repo_id=str(asset["repo_id"]),
        snapshot_id=str(asset["snapshot_id"]),
        task_signature=task_signature,
        capability=str(asset["capability"]),
        model_id=model_id,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        query=query,
        evidence_paths=evidence_paths,
        notes=notes,
        trajectory=trajectory,
    )
    run_id = catalog.record_analysis_run(
        "fastcontext-refine",
        "completed",
        {
            "candidate_id": candidate_id,
            "task_signature": task_signature,
            "schema_version": SCHEMA_VERSION,
            "refinement_id": refinement_id,
            "evidence_count": len(evidence_paths),
        },
        repo_id=str(asset["repo_id"]),
        snapshot_id=str(asset["snapshot_id"]),
        model_id=model_id,
        prompt_version=PROMPT_VERSION,
        analyzer_version=ANALYZER_VERSION,
    )
    return {
        "candidate_id": candidate_id,
        "task_signature": task_signature,
        "repo_id": asset["repo_id"],
        "snapshot_id": asset["snapshot_id"],
        "capability": asset["capability"],
        "model_id": model_id,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "refinement_id": refinement_id,
        "analysis_run_id": run_id,
        "evidence_paths": evidence_paths,
        "notes": notes,
    }


def _validated_evidence_paths(
    root: Path,
    citations: list[FastContextCitation],
) -> tuple[list[str], list[str]]:
    evidence_paths: list[str] = []
    notes: list[str] = []
    for citation in citations:
        try:
            path, safe_rel = _resolve_under_root(root, citation.path)
        except FastContextError as exc:
            notes.append(f"Skipped invalid citation '{citation.evidence_path()}': {exc}")
            continue
        if not path.is_file():
            notes.append(f"Skipped missing citation file: {safe_rel}")
            continue
        normalized = FastContextCitation(
            path=safe_rel,
            start_line=citation.start_line,
            end_line=citation.end_line,
            reason=citation.reason,
        )
        evidence_paths.append(normalized.evidence_path())
    return sorted(set(evidence_paths)), notes


def _extract_tool_calls(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    raw_calls = parsed.get("tool_calls") or parsed.get("tools") or parsed.get("actions") or []
    if isinstance(raw_calls, dict):
        raw_calls = [raw_calls]
    if not isinstance(raw_calls, list):
        return []
    calls: list[dict[str, Any]] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            continue
        tool = _tool_name(raw_call)
        if tool in {"READ", "GLOB", "GREP"}:
            calls.append({"tool": tool, "args": _tool_args(raw_call)})
    return calls


def _extract_citations(parsed: dict[str, Any]) -> list[FastContextCitation]:
    final_answer = parsed.get("final_answer") or parsed.get("evidence") or parsed.get("citations")
    evidence: Any
    if isinstance(final_answer, dict):
        evidence = final_answer.get("evidence") or final_answer.get("citations") or []
    else:
        evidence = final_answer
    return _citations_from_value(evidence)


def _extract_notes(parsed: dict[str, Any]) -> list[str]:
    final_answer = parsed.get("final_answer")
    notes = final_answer.get("notes") if isinstance(final_answer, dict) else parsed.get("notes")
    if not isinstance(notes, list):
        return []
    return [str(note) for note in notes]


def _citations_from_value(value: Any) -> list[FastContextCitation]:
    if isinstance(value, str):
        return _parse_citation_lines(value)
    if not isinstance(value, list):
        return []
    citations: list[FastContextCitation] = []
    for item in value:
        if isinstance(item, str):
            citations.extend(_parse_citation_lines(item))
        elif isinstance(item, dict):
            path = str(item.get("path") or item.get("file") or "").strip()
            if not path:
                continue
            start_line = _optional_int(item.get("start_line") or item.get("start"))
            end_line = _optional_int(item.get("end_line") or item.get("end"))
            citations.append(
                FastContextCitation(
                    path=path,
                    start_line=start_line,
                    end_line=end_line,
                    reason=str(item.get("reason", "")),
                )
            )
    return citations


def _parse_final_answer_citations(content: str) -> list[FastContextCitation]:
    block = re.search(
        r"<final_answer>\s*(?P<body>.*?)\s*</final_answer>",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if block:
        return _parse_citation_lines(block.group("body"))
    return _parse_citation_lines(content)


def _parse_citation_lines(text: str) -> list[FastContextCitation]:
    citations: list[FastContextCitation] = []
    for raw_line in text.splitlines():
        line = raw_line.strip().strip("-*` ")
        if not line:
            continue
        match = re.search(
            r"(?P<path>[\w./\\()[\]@ -]+\.(?:ts|tsx|js|jsx|json|md|css|scss|mjs|cjs))"
            r"[:#L]+(?P<start>\d+)(?:[-:L]+(?P<end>\d+))?",
            line,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        citations.append(
            FastContextCitation(
                path=match.group("path").strip(),
                start_line=int(match.group("start")),
                end_line=int(match.group("end")) if match.group("end") else None,
            )
        )
    return citations


def _parse_function_style_tool_calls(content: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for match in re.finditer(
        r"\b(?P<tool>READ|GLOB|GREP)\s*\((?P<body>.*?)\)",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        tool = match.group("tool").upper()
        body = match.group("body")
        args = _parse_call_args(body)
        if tool == "READ" and "path" not in args:
            quoted = _first_quoted(body)
            if quoted:
                args["path"] = quoted
        if tool == "GLOB" and "pattern" not in args:
            quoted = _first_quoted(body)
            if quoted:
                args["pattern"] = quoted
        if tool == "GREP" and "pattern" not in args:
            quoted = _first_quoted(body)
            if quoted:
                args["pattern"] = quoted
        calls.append({"tool": tool, "args": args})
    return calls


def _parse_call_args(body: str) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for match in re.finditer(
        r"(?P<key>\w+)\s*=\s*(?P<value>\"[^\"]*\"|'[^']*'|\d+)",
        body,
        flags=re.DOTALL,
    ):
        value = match.group("value").strip()
        if value.isdigit():
            args[match.group("key")] = int(value)
        else:
            args[match.group("key")] = value.strip("\"'")
    return args


def _first_quoted(value: str) -> str | None:
    match = re.search(r"\"([^\"]+)\"|'([^']+)'", value)
    if not match:
        return None
    return str(match.group(1) or match.group(2))


def _tool_name(call: dict[str, Any]) -> str:
    function = call.get("function")
    if isinstance(function, dict) and function.get("name"):
        return str(function["name"]).upper()
    return str(call.get("tool") or call.get("name") or "").upper()


def _tool_args(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function")
    raw_args: Any = call.get("args") or call.get("arguments")
    if isinstance(function, dict) and function.get("arguments") is not None:
        raw_args = function["arguments"]
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError:
            return _parse_call_args(raw_args)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _resolve_under_root(root: Path, rel_path: str) -> tuple[Path, str]:
    root_resolved = root.resolve()
    cleaned = _strip_line_suffix(rel_path.strip()).replace("\\", "/")
    if not cleaned:
        raise FastContextError("Path is required.")

    if Path(cleaned).is_absolute():
        candidate = Path(cleaned).resolve()
    else:
        parts = PurePosixPath(cleaned).parts
        if ".." in parts:
            raise FastContextError(f"Path escapes snapshot root: {rel_path}")
        candidate = (root_resolved / cleaned).resolve()

    try:
        relative = candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise FastContextError(f"Path escapes snapshot root: {rel_path}") from exc

    if any(part in SKIP_DIRS for part in relative.parts):
        raise FastContextError(f"Path is under a skipped directory: {rel_path}")
    return candidate, relative.as_posix()


def _relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _safe_glob_pattern(pattern: str) -> str:
    cleaned = pattern.strip().replace("\\", "/") or "**/*"
    if Path(cleaned).is_absolute() or cleaned.startswith("/"):
        raise FastContextError(f"Glob pattern must be relative: {pattern}")
    if ".." in PurePosixPath(cleaned).parts:
        raise FastContextError(f"Glob pattern escapes snapshot root: {pattern}")
    return cleaned


def _should_skip_path(root: Path, path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return True
    return any(part in SKIP_DIRS for part in relative.parts)


def _grep_candidate_files(root: Path, file_glob: str | None) -> list[Path]:
    if file_glob:
        safe_pattern = _safe_glob_pattern(file_glob)
        candidates = [path for path in root.glob(safe_pattern) if path.is_file()]
    else:
        candidates = [path for path in root.rglob("*") if path.is_file()]
    return sorted(
        (path for path in candidates if not _should_skip_path(root, path)),
        key=lambda path: _relative_path(root, path),
    )


def _strip_line_suffix(path: str) -> str:
    return re.sub(r":\d+(?:-\d+)?$", "", path)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_label(label: str | None) -> str:
    if not label:
        return ""
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in label)
