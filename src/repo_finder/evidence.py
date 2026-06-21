import json
from pathlib import Path
from typing import Any

from . import catalog
from .constants import SKIP_DIRS

SOURCE_EXTENSIONS = {".tsx", ".ts", ".jsx", ".js", ".css", ".mdx"}
CONFIG_NAMES = {
    "package.json",
    "components.json",
    "tailwind.config.js",
    "tailwind.config.ts",
    "postcss.config.js",
    "tsconfig.json",
}
MAX_FILE_BYTES = 1_000_000

CAPABILITY_TERMS: dict[str, list[str]] = {
    "data-table": ["data table", "datatable", "table", "tanstack", "columns"],
    "command-palette": ["command palette", "cmdk", "command", "combobox"],
    "auth-ui": ["auth", "login", "sign in", "signin", "signup", "password"],
    "settings": ["settings", "profile", "account", "preferences"],
    "dashboard": ["dashboard", "sidebar", "admin", "overview"],
    "forms": ["form", "react-hook-form", "zod", "validation"],
    "file-upload": ["upload", "dropzone", "file input", "progress"],
    "charts": ["chart", "recharts", "visx", "nivo", "graph"],
    "navigation": ["navigation", "navbar", "sidebar", "menu"],
    "admin-interface": ["admin", "dashboard", "table", "settings"],
}

RELEVANT_DEPENDENCIES = {
    "@hookform/resolvers",
    "@radix-ui/react-accordion",
    "@radix-ui/react-dialog",
    "@radix-ui/react-dropdown-menu",
    "@radix-ui/react-label",
    "@radix-ui/react-popover",
    "@radix-ui/react-select",
    "@radix-ui/react-slot",
    "@radix-ui/react-tabs",
    "@tanstack/react-table",
    "class-variance-authority",
    "cmdk",
    "lucide-react",
    "next",
    "react",
    "react-dom",
    "react-dropzone",
    "react-hook-form",
    "recharts",
    "tailwind-merge",
    "tailwindcss",
    "zod",
}


def normalize_capability(capability: str) -> str:
    return capability.strip().lower().replace("_", "-").replace(" ", "-")


def terms_for_capability(capability: str) -> list[str]:
    normalized = normalize_capability(capability)
    terms = CAPABILITY_TERMS.get(normalized)
    if terms:
        return terms
    return [part for part in normalized.replace("-", " ").split() if len(part) > 2]


def collect_scan_files(snapshot_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in snapshot_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in CONFIG_NAMES or path.suffix in SOURCE_EXTENSIONS:
            try:
                if path.stat().st_size <= MAX_FILE_BYTES:
                    files.append(path)
            except OSError:
                continue
    return files


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _line_citations(rel_path: str, content: str, terms: list[str], max_ranges: int = 3) -> list[str]:
    lines = content.splitlines()
    citations: list[str] = []
    lowered_terms = [term.lower() for term in terms]
    for index, line in enumerate(lines, start=1):
        lowered = line.lower()
        if any(term in lowered for term in lowered_terms):
            start = max(1, index - 2)
            end = min(len(lines), index + 2)
            citation = f"{rel_path}:{start}-{end}"
            if citation not in citations:
                citations.append(citation)
            if len(citations) >= max_ranges:
                break
    return citations


def _load_package_dependencies(snapshot_root: Path) -> tuple[dict[str, str], list[str]]:
    dependencies: dict[str, str] = {}
    manifests: list[str] = []
    for manifest in snapshot_root.rglob("package.json"):
        if any(part in SKIP_DIRS for part in manifest.parts):
            continue
        content = read_text(manifest)
        if not content:
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            continue
        manifests.append(_relative(snapshot_root, manifest))
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            section_values = parsed.get(section)
            if isinstance(section_values, dict):
                for name, version in section_values.items():
                    dependencies[str(name)] = str(version)
    return dependencies, manifests


def _dependency_paths(snapshot_root: Path, manifest_paths: list[str]) -> list[str]:
    paths = list(manifest_paths)
    for name in CONFIG_NAMES - {"package.json"}:
        for path in snapshot_root.rglob(name):
            if not any(part in SKIP_DIRS for part in path.parts):
                paths.append(_relative(snapshot_root, path))
    return sorted(set(paths))


def scan_snapshot(snapshot_root: Path, capability: str, max_files: int = 10) -> dict[str, Any]:
    normalized = normalize_capability(capability)
    terms = terms_for_capability(normalized)
    dependencies, manifest_paths = _load_package_dependencies(snapshot_root)
    relevant_dependencies = sorted(name for name in dependencies if name in RELEVANT_DEPENDENCIES)
    dependency_paths = _dependency_paths(snapshot_root, manifest_paths)

    scored_files: list[tuple[int, str, list[str]]] = []
    for path in collect_scan_files(snapshot_root):
        rel_path = _relative(snapshot_root, path)
        content = read_text(path)
        if content is None:
            continue
        searchable = f"{rel_path}\n{content}".lower()
        score = sum(searchable.count(term.lower()) for term in terms)
        if path.name == "package.json":
            score += sum(1 for dep in relevant_dependencies if dep.lower() in searchable)
        if score <= 0:
            continue
        citations = _line_citations(rel_path, content, terms)
        if not citations:
            line_count = max(1, min(80, len(content.splitlines())))
            citations = [f"{rel_path}:1-{line_count}"]
        scored_files.append((score, rel_path, citations))

    scored_files.sort(key=lambda item: item[0], reverse=True)
    top_files = scored_files[:max_files]
    entry_paths = [
        path
        for _, path, _ in top_files
        if Path(path).suffix in SOURCE_EXTENSIONS and not path.endswith(".mdx")
    ]
    evidence_paths: list[str] = []
    for _, _, citations in top_files:
        evidence_paths.extend(citations)

    adaptation_notes = ["Copy listed files, then adapt imports, routes, and project-specific data loading."]
    if any(dep.startswith("@radix-ui/") for dep in relevant_dependencies):
        adaptation_notes.append(
            "Install matching Radix packages or map primitives to the target shadcn/ui setup."
        )
    if "@tanstack/react-table" in relevant_dependencies:
        adaptation_notes.append(
            "Carry over the table column definitions and TanStack table dependency together."
        )
    if "react-hook-form" in relevant_dependencies or "zod" in relevant_dependencies:
        adaptation_notes.append("Keep form schema, resolver, and validation messages together.")
    if any("cmdk" == dep for dep in relevant_dependencies):
        adaptation_notes.append(
            "Command palettes usually depend on cmdk plus dialog/popover primitives."
        )
    if any("@/" in citation for citation in evidence_paths):
        adaptation_notes.append(
            "Replace source alias imports such as '@/...' with the target project's path aliases."
        )

    reuse_score = min(
        1.0,
        0.2 + (len(entry_paths) * 0.08) + (len(evidence_paths) * 0.03) + (len(relevant_dependencies) * 0.02),
    )

    return {
        "capability": normalized,
        "entry_paths": entry_paths[:max_files],
        "dependency_paths": dependency_paths,
        "external_dependencies": relevant_dependencies,
        "evidence_paths": sorted(set(evidence_paths)),
        "reuse_score": round(reuse_score, 4) if evidence_paths else 0.0,
        "synthesis": {
            "adaptation_notes": adaptation_notes,
            "match_terms": terms,
        },
    }


def run_evidence(capability: str, limit: int) -> dict[str, int]:
    stored = 0
    skipped = 0
    normalized = normalize_capability(capability)
    for snapshot in catalog.list_snapshots_for_evidence(limit):
        root = Path(str(snapshot["snapshot_path"]))
        if not root.exists():
            skipped += 1
            continue
        result = scan_snapshot(root, normalized)
        if not result["evidence_paths"]:
            skipped += 1
            continue
        catalog.upsert_asset(
            snapshot_id=str(snapshot["snapshot_id"]),
            repo_id=str(snapshot["repo_id"]),
            capability=normalized,
            evidence=result,
        )
        stored += 1
    catalog.record_analysis_run(
        "evidence",
        "completed",
        {"capability": normalized, "stored": stored, "skipped": skipped},
    )
    return {"stored_assets": stored, "skipped_snapshots": skipped}
