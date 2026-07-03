from pathlib import Path
from typing import Any

from .capabilities import (
    AI_DATA_CAPABILITIES,
    BACKEND_CAPABILITIES,
    BACKEND_PATH_PARTS,
    BACKGROUND_JOB_FALSE_POSITIVE_TERMS,
    BACKGROUND_JOB_STRONG_TERMS,
    CAPABILITY_INTENT_HINTS,
    CAPABILITY_PATH_TERMS,
)


def _task_terms(task: str) -> set[str]:
    normalized = task.lower().replace("-", " ").replace("_", " ")
    return {term for term in normalized.split() if len(term) > 2}


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _profile_match_score(profile: dict[str, Any] | None) -> float:
    if not profile:
        return 0.0

    quality = (
        _float_value(profile.get("likely_usefulness"))
        + _float_value(profile.get("extractability"))
        + _float_value(profile.get("maintenance_quality"))
    ) / 3
    if quality <= 0 and not profile.get("concerns"):
        return 0.0
    concerns = " ".join(str(value).lower() for value in profile.get("concerns", []))
    concern_penalty = 0.08 if any(term in concerns for term in ("coupled", "low quality", "unclear")) else 0.0
    combined = (quality * 0.35) - concern_penalty
    return round(
        max(0.0, min(1.0, combined)),
        4,
    )


def _has_profile_signal(profile: dict[str, Any] | None) -> bool:
    if not profile:
        return False
    if any(
        _float_value(profile.get(key)) > 0
        for key in ("likely_usefulness", "extractability", "maintenance_quality")
    ):
        return True
    concerns = profile.get("concerns", [])
    return isinstance(concerns, list) and any(str(value).strip() for value in concerns)


def _synthesis_score(synthesis: dict[str, Any], key: str) -> float:
    return max(0.0, min(1.0, _float_value(synthesis.get(key))))


def _has_backend_path(paths: list[Any]) -> bool:
    for raw_path in paths:
        path = str(raw_path).replace("\\", "/").lower()
        parts = set(path.split("/"))
        if parts & BACKEND_PATH_PARTS:
            return True
        if _path_tokens(path) & BACKEND_PATH_PARTS:
            return True
        if path.startswith(("lib/", "src/lib/", "app/api/", "src/app/api/", "worker/", "src/worker/")):
            return True
    return False


def _path_tokens(path: str) -> set[str]:
    tokens: set[str] = set()
    for part in path.replace("\\", "/").lower().split("/"):
        stem = Path(part).stem
        tokens.add(part)
        tokens.add(stem)
        tokens.update(token for token in stem.replace("_", "-").split("-") if token)
    return tokens


def _all_path_tokens(paths: list[Any]) -> set[str]:
    tokens: set[str] = set()
    for raw_path in paths:
        tokens.update(_path_tokens(str(raw_path)))
    return tokens


def _paths_contain_any(paths: list[Any], terms: set[str]) -> bool:
    joined = " ".join(str(path).replace("\\", "/").lower() for path in paths)
    return any(term in joined for term in terms)


def _backend_path_alignment_score(capability: str, paths: list[Any]) -> float:
    if capability not in BACKEND_CAPABILITIES and capability not in AI_DATA_CAPABILITIES:
        return 0.0

    if capability == "background-jobs":
        return _background_job_path_alignment_score(paths)

    wanted_terms = CAPABILITY_PATH_TERMS.get(capability, set())
    if not wanted_terms:
        return 0.0
    hits = len(_all_path_tokens(paths) & wanted_terms)
    if hits <= 0:
        return -0.18
    return min(0.16, hits * 0.04)


def _background_job_path_alignment_score(paths: list[Any]) -> float:
    strong_hits = 0
    false_positive_only = False
    for raw_path in paths:
        path = str(raw_path).replace("\\", "/").lower()
        is_false_positive = any(term in path for term in BACKGROUND_JOB_FALSE_POSITIVE_TERMS)
        tokens = _path_tokens(path)
        if tokens & BACKGROUND_JOB_STRONG_TERMS and not is_false_positive:
            strong_hits += 1
        elif is_false_positive:
            false_positive_only = True

    if strong_hits <= 0:
        return -0.36 if false_positive_only else -0.24
    return min(0.18, strong_hits * 0.04)


def _capability_intent_scores(task: str) -> dict[str, float]:
    lowered = task.lower().replace("-", " ").replace("_", " ")
    scores: dict[str, float] = {}
    for capability, hints in CAPABILITY_INTENT_HINTS.items():
        score = 0.0
        for hint in hints:
            normalized_hint = hint.lower().replace("-", " ").replace("_", " ")
            if normalized_hint in lowered:
                score += 0.35 if " " in hint else 0.18
        scores[capability] = min(1.0, score)
    return scores
