import fnmatch
import json
import re
from pathlib import Path
from typing import Any

from source_scout import catalog, path_safety

from .schemas import RlmToolCall, RlmToolResult


class RlmToolError(ValueError):
    pass


class RlmReadOnlyTools:
    def __init__(
        self,
        root: str | Path,
        *,
        max_files: int = 200,
        max_read_lines: int = 80,
        max_file_bytes: int = 200_000,
        max_grep_results: int = 50,
    ):
        self.root = Path(root).expanduser().resolve()
        if not self.root.exists() or not self.root.is_dir():
            raise RlmToolError(f"RLM tool root must be an existing directory: {root}")
        self.max_files = max(1, max_files)
        self.max_read_lines = max(1, max_read_lines)
        self.max_file_bytes = max(1, max_file_bytes)
        self.max_grep_results = max(1, max_grep_results)

    def run(self, call: RlmToolCall) -> RlmToolResult:
        try:
            result = self._run_result(call.tool, call.arguments)
            return RlmToolResult(tool=call.tool, ok=True, result=result, call_id=call.call_id)
        except Exception as exc:
            return RlmToolResult(tool=call.tool, ok=False, error=str(exc), call_id=call.call_id)

    def list_files(self, pattern: str = "**/*", limit: int | None = None) -> dict[str, Any]:
        safe_pattern = self._safe_glob(pattern)
        effective_limit = self._limit(limit, self.max_files)
        matches: list[str] = []
        truncated = False
        for path in self._iter_files():
            rel_path = path_safety.relative_path(self.root, path)
            if not _matches_glob(rel_path, safe_pattern):
                continue
            if len(matches) >= effective_limit:
                truncated = True
                break
            matches.append(rel_path)
        return {
            "root": str(self.root),
            "pattern": safe_pattern,
            "matches": matches,
            "truncated": truncated,
        }

    def read_file_range(
        self,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        resolved, safe_rel = self._resolve_file(path)
        if resolved.stat().st_size > self.max_file_bytes:
            raise RlmToolError(f"RLM read target is too large: {safe_rel}")

        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        if not lines:
            return {
                "path": safe_rel,
                "start_line": 1,
                "end_line": 0,
                "line_count": 0,
                "content": "",
                "truncated": False,
            }

        bounded_start = min(max(1, start_line), len(lines))
        effective_limit = self._limit(limit, self.max_read_lines)
        requested_end = min(max(bounded_start, end_line or len(lines)), len(lines))
        bounded_end = min(requested_end, bounded_start + effective_limit - 1)
        selected = lines[bounded_start - 1 : bounded_end]
        return {
            "path": safe_rel,
            "start_line": bounded_start,
            "end_line": bounded_end,
            "line_count": len(lines),
            "content": "\n".join(
                f"{line_number}|{line}"
                for line_number, line in enumerate(selected, start=bounded_start)
            ),
            "truncated": bounded_end < requested_end,
        }

    def grep_text(
        self,
        pattern: str,
        *,
        file_glob: str = "**/*",
        search_path: str = ".",
        limit: int | None = None,
        ignore_case: bool = False,
    ) -> dict[str, Any]:
        if not pattern.strip():
            raise RlmToolError("RLM grep pattern is required.")
        safe_glob = self._safe_glob(file_glob)
        search_root, safe_search_path = self._resolve_directory(search_path)
        compiled, regex_mode = _compile_pattern(pattern, ignore_case=ignore_case)
        effective_limit = self._limit(limit, self.max_grep_results)
        matches: list[dict[str, Any]] = []
        truncated = False

        for path in self._iter_files(search_root):
            rel_path = path_safety.relative_path(self.root, path)
            if not _matches_glob(rel_path, safe_glob):
                continue
            if path.stat().st_size > self.max_file_bytes:
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, start=1):
                if not compiled.search(line):
                    continue
                if len(matches) >= effective_limit:
                    truncated = True
                    break
                matches.append(
                    {
                        "path": rel_path,
                        "line": line_number,
                        "text": line[:500],
                    }
                )
            if truncated:
                break

        return {
            "pattern": pattern,
            "regex": regex_mode,
            "file_glob": safe_glob,
            "search_path": safe_search_path,
            "matches": matches,
            "truncated": truncated,
        }

    def load_candidate_asset(self, candidate_id: str) -> dict[str, Any]:
        if not candidate_id.strip():
            raise RlmToolError("candidate_id is required.")
        asset = catalog.get_asset_detail(candidate_id)
        if asset is None:
            raise RlmToolError(f"Unknown candidate_id: {candidate_id}")
        return {"candidate_id": candidate_id, "asset": _jsonable(asset)}

    def load_bundle_manifest(self, candidate_id: str, task_signature: str) -> dict[str, Any]:
        if not candidate_id.strip():
            raise RlmToolError("candidate_id is required.")
        if not task_signature.strip():
            raise RlmToolError("task_signature is required.")
        try:
            manifest_path = catalog.bundle_path(candidate_id, task_signature) / "bundle.json"
        except ValueError as exc:
            raise RlmToolError(str(exc)) from exc
        if not manifest_path.exists():
            raise RlmToolError(f"Bundle manifest not found: {manifest_path}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RlmToolError(f"Could not read bundle manifest: {exc}") from exc
        if not isinstance(manifest, dict):
            raise RlmToolError(f"Bundle manifest has invalid shape: {manifest_path}")
        return {
            "candidate_id": candidate_id,
            "task_signature": task_signature,
            "manifest_path": str(manifest_path),
            "manifest": _jsonable(manifest),
        }

    def _run_result(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool == "list_files":
            return self.list_files(
                pattern=str(arguments.get("pattern") or "**/*"),
                limit=_optional_int(arguments.get("limit")),
            )
        if tool == "read_file_range":
            return self.read_file_range(
                str(arguments.get("path") or ""),
                start_line=_optional_int(arguments.get("start_line")) or 1,
                end_line=_optional_int(arguments.get("end_line")),
                limit=_optional_int(arguments.get("limit")),
            )
        if tool == "grep_text":
            return self.grep_text(
                str(arguments.get("pattern") or ""),
                file_glob=str(arguments.get("file_glob") or "**/*"),
                search_path=str(arguments.get("search_path") or "."),
                limit=_optional_int(arguments.get("limit")),
                ignore_case=bool(arguments.get("ignore_case", False)),
            )
        if tool == "load_candidate_asset":
            return self.load_candidate_asset(str(arguments.get("candidate_id") or ""))
        if tool == "load_bundle_manifest":
            return self.load_bundle_manifest(
                str(arguments.get("candidate_id") or ""),
                str(arguments.get("task_signature") or ""),
            )
        raise RlmToolError(f"Unsupported RLM tool: {tool}")

    def _iter_files(self, start: Path | None = None) -> list[Path]:
        root = start or self.root
        files = [
            path
            for path in root.rglob("*")
            if path.is_file() and not path_safety.should_skip_path(self.root, path)
        ]
        files.sort(key=lambda path: path_safety.relative_path(self.root, path))
        return files[: self.max_files]

    def _resolve_file(self, path: str) -> tuple[Path, str]:
        try:
            resolved, safe_rel = path_safety.resolve_under_root(self.root, path)
        except path_safety.PathSafetyError as exc:
            raise RlmToolError(str(exc)) from exc
        if not resolved.is_file():
            raise RlmToolError(f"RLM read target is not a file: {safe_rel}")
        return resolved, safe_rel

    def _resolve_directory(self, path: str) -> tuple[Path, str]:
        try:
            resolved, safe_rel = path_safety.resolve_under_root(self.root, path)
        except path_safety.PathSafetyError as exc:
            raise RlmToolError(str(exc)) from exc
        if not resolved.is_dir():
            raise RlmToolError(f"RLM search target is not a directory: {safe_rel}")
        return resolved, safe_rel

    def _safe_glob(self, pattern: str) -> str:
        try:
            return path_safety.safe_glob_pattern(self.root, pattern)
        except path_safety.PathSafetyError as exc:
            raise RlmToolError(str(exc)) from exc

    def _limit(self, requested: int | None, maximum: int) -> int:
        if requested is None:
            return maximum
        return min(max(1, requested), maximum)


def _glob_variants(pattern: str) -> list[str]:
    variants = [pattern]
    if "**/" in pattern:
        variants.append(pattern.replace("**/", ""))
    return variants


def _matches_glob(path: str, pattern: str) -> bool:
    if pattern in {"", ".", "**/*"}:
        return True
    return any(fnmatch.fnmatch(path, variant) for variant in _glob_variants(pattern))


def _compile_pattern(pattern: str, *, ignore_case: bool) -> tuple[re.Pattern[str], bool]:
    flags = re.IGNORECASE if ignore_case else 0
    try:
        return re.compile(pattern, flags=flags), True
    except re.error:
        return re.compile(re.escape(pattern), flags=flags), False


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
