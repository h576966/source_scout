import os
import re

from fastmcp.exceptions import ToolError

from . import SKIP_DIRS, _now_iso, cloner, repo_inspector
from .github_client import get_client
from .models import DeepPatternReport, Pattern, PatternReport

_SECTION_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_FOCUS_KEYWORDS: dict[str, list[str]] = {
    "api": ["api", "endpoint", "route", "rest", "graphql"],
    "auth": ["auth", "authentication", "authorization", "login", "oauth", "jwt", "token"],
    "data pipeline": ["pipeline", "etl", "stream", "queue", "ingestion"],
    "database": ["database", "db", "sql", "migration", "orm", "postgres", "mysql", "sqlite"],
    "testing": ["test", "testing", "mock", "fixture", "pytest"],
    "deployment": ["deploy", "docker", "kubernetes", "ci", "cd", "production"],
    "architecture": ["architecture", "design", "structure", "module", "component", "pattern"],
    "configuration": ["config", "configuration", "settings", "env", "environment"],
    "logging": ["log", "logging", "monitor", "observability", "trace"],
    "error handling": ["error", "exception", "handling", "retry", "fallback"],
}

_DEFAULT_FILES_TO_PREVIEW = [
    "src/main.py",
    "app/main.py",
    "src/app.py",
    "app.py",
    "main.py",
    "src/index.ts",
    "src/index.js",
    "index.ts",
    "index.js",
    "pyproject.toml",
    "setup.py",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "Dockerfile",
]


def parse_readme_sections(readme: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(readme))
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = len(readme)
        sections[title] = readme[start:end].strip()
    return sections


async def collect_key_file_previews(
    owner: str, repo: str, key_files: list[str], max_lines: int = 30
) -> dict[str, str]:
    client = get_client()
    previews: dict[str, str] = {}
    files_to_fetch = key_files[:5]
    if not files_to_fetch:
        candidates = _DEFAULT_FILES_TO_PREVIEW[:5]
    else:
        candidates = files_to_fetch[:5]

    for path in candidates:
        content = await client.get_file_content(owner, repo, path, max_lines)
        if content:
            previews[path] = content

    return previews


def _matches_focus(text: str, focus: str) -> bool:
    keywords = _FOCUS_KEYWORDS.get(focus.lower())
    if not keywords:
        text_words = set(text.lower().split())
        focus_words = set(focus.lower().split())
        return bool(text_words & focus_words)
    return any(kw in text.lower() for kw in keywords)


def _extract_architecture_patterns(
    sections: dict[str, str], file_tree: list[str], previews: dict[str, str], focus: str | None
) -> list[Pattern]:
    patterns: list[Pattern] = []
    arch_keywords = ["architecture", "design", "structure", "overview"]
    arch_titles = [t for t in sections if any(w in t.lower() for w in arch_keywords)]
    for title in arch_titles:
        content = sections[title][:500]
        if focus and not _matches_focus(content, focus):
            continue
        patterns.append(
            Pattern(
                category="architecture",
                title=f"Architecture: {title}",
                description=content,
                snippet=None,
                source="README",
            )
        )

    if file_tree:
        tree_text = "Top-level: " + ", ".join(file_tree[:20])
        if not focus or _matches_focus(tree_text, focus):
            patterns.append(
                Pattern(
                    category="architecture",
                    title="Project structure",
                    description=tree_text,
                    snippet=None,
                    source="file_tree",
                )
            )

    return patterns


def _extract_best_practices(
    sections: dict[str, str], previews: dict[str, str], focus: str | None
) -> list[Pattern]:
    patterns: list[Pattern] = []
    bp_keywords = ["contributing", "development", "guidelines", "conventions"]
    bp_titles = [t for t in sections if any(w in t.lower() for w in bp_keywords)]
    for title in bp_titles:
        content = sections[title][:500]
        if focus and not _matches_focus(content, focus):
            continue
        patterns.append(
            Pattern(
                category="best_practice",
                title=f"Practice: {title}",
                description=content,
                snippet=None,
                source="README",
            )
        )

    for path, content in previews.items():
        trimmed = content[:400]
        if focus and not _matches_focus(trimmed, focus):
            continue
        patterns.append(
            Pattern(
                category="code_structure",
                title=f"Key file: {path}",
                description=f"First {content.count(chr(10)) + 1} lines of {path}",
                snippet=content,
                source="file_preview",
            )
        )

    return patterns


def distill_patterns(
    sections: dict[str, str],
    file_tree: list[str],
    previews: dict[str, str],
    focus: str | None,
) -> list[Pattern]:
    patterns: list[Pattern] = []
    patterns.extend(_extract_architecture_patterns(sections, file_tree, previews, focus))
    patterns.extend(_extract_best_practices(sections, previews, focus))
    return patterns


async def extract_patterns(
    owner: str, repo: str, focus: str | None = None
) -> PatternReport:
    client = get_client()
    readme = await client.get_readme(owner, repo)

    sections: dict[str, str] = {}
    if readme:
        sections = parse_readme_sections(readme)

    structure = await repo_inspector.analyze_structure(owner, repo)
    file_tree = structure.files + structure.dirs
    key_files = structure.key_files

    previews: dict[str, str] = {}
    if key_files:
        previews = await collect_key_file_previews(owner, repo, key_files)

    patterns = distill_patterns(sections, file_tree, previews, focus)

    # Verdict domain: pattern extraction richness from README + file tree
    verdict = "useful" if len(patterns) >= 2 else "maybe" if patterns else "skip"

    return PatternReport(
        owner=owner,
        repo=repo,
        patterns=patterns,
        file_tree=file_tree,
        readme_sections=list(sections.keys()),
        focus=focus,
        verdict=verdict,
        cached=False,
        timestamp=_now_iso(),
    )


_PATTERN_CACHE: dict[str, DeepPatternReport] = {}


_FRAMEWORK_MARKERS: dict[str, list[str]] = {
    "fastapi": ["setup.py", "pyproject.toml", "main.py", "app/main.py"],
    "flask": ["app.py", "wsgi.py", "requirements.txt"],
    "django": ["manage.py", "settings.py", "urls.py", "wsgi.py"],
    "react": ["package.json", "src/App.tsx", "src/App.jsx", "public/index.html"],
    "next.js": ["next.config.js", "next.config.ts", "app/layout.tsx", "pages/_app.tsx", "pages/_app.js"],
    "express": ["app.js", "server.js", "routes/", "middleware/"],
    "vue": ["vue.config.js", "src/App.vue", "src/main.ts", "src/main.js"],
    "angular": ["angular.json", "src/app/app.module.ts", "src/main.ts"],
    "svelte": ["svelte.config.js", "src/App.svelte", "src/routes/"],
    "spring": ["pom.xml", "build.gradle", "src/main/java/"],
    "rails": ["Gemfile", "config/routes.rb", "app/controllers/"],
    "laravel": ["artisan", "composer.json", "routes/web.php"],
    "go": ["go.mod", "main.go"],
    "rust": ["Cargo.toml", "src/main.rs"],
}


def detect_framework(repo_path: str) -> str | None:
    root_entries = set(os.listdir(repo_path))
    best_framework = None
    best_score = 0

    for framework, markers in _FRAMEWORK_MARKERS.items():
        score = 0
        for marker in markers:
            marker_path = os.path.join(repo_path, marker)
            if os.path.exists(marker_path):
                score += 1
            elif marker in root_entries:
                score += 1
        if score > best_score:
            best_score = score
            best_framework = framework

    if best_score <= 0:
        return None
    if best_framework == "go" and best_score == 1:
        if "pyproject.toml" in root_entries or "setup.py" in root_entries:
            return None
    if best_framework == "rust" and best_score == 1:
        if "package.json" in root_entries:
            return None
    return best_framework


def _collect_all_files(repo_path: str, prefix: str = "") -> list[str]:
    files: list[str] = []
    try:
        entries = sorted(os.listdir(repo_path))
    except PermissionError:
        return files
    for entry in entries:
        if entry in SKIP_DIRS:
            continue
        full = os.path.join(repo_path, entry)
        rel = f"{prefix}/{entry}" if prefix else entry
        if os.path.isfile(full):
            files.append(rel)
        elif os.path.isdir(full):
            files.append(rel + "/")
            files.extend(_collect_all_files(full, rel))
    return files


def _read_file(path: str, max_lines: int = 100) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                lines.append(line.rstrip("\n"))
            return "\n".join(lines)
    except (OSError, UnicodeDecodeError):
        return None


def _identify_language_patterns(repo_path: str, framework: str | None) -> list[Pattern]:
    patterns: list[Pattern] = []
    if not framework:
        return patterns

    router_count = 0
    dep_count = 0
    component_count = 0
    hook_count = 0

    for dirpath, _dirnames, filenames in os.walk(repo_path):
        for f in filenames:
            filepath = os.path.join(dirpath, f)
            content: str | None = None

            if framework == "fastapi" and f.endswith(".py"):
                content = _read_file(filepath, max_lines=100)
                if content:
                    router_count += content.count("APIRouter")
                    dep_count += content.count("Depends(")

            elif framework in ("react", "next.js") and f.endswith((".tsx", ".jsx")):
                content = _read_file(filepath, max_lines=100)
                if content:
                    if "export default function" in content or "export function" in content:
                        component_count += 1
                    if "useState" in content or "useEffect" in content:
                        hook_count += 1

    if framework == "fastapi":
        if router_count > 0:
            patterns.append(Pattern(
                category="code_structure",
                title="FastAPI router pattern",
                description=f"Found {router_count} APIRouter usage(s) — modular route organization",
                snippet=f"APIRouter count: {router_count}, Depends count: {dep_count}",
                source="file_preview",
            ))
        if "alembic" in " ".join(os.listdir(repo_path)):
            patterns.append(Pattern(
                category="best_practice",
                title="Database migrations (Alembic)",
                description="Project uses Alembic for database migrations",
                snippet=None,
                source="file_tree",
            ))

    if framework in ("react", "next.js") and component_count > 0:
        patterns.append(Pattern(
            category="code_structure",
            title="React component structure",
            description=f"Found {component_count} React component(s) with {hook_count} hook usage(s)",
            snippet=f"Components: {component_count}, Hooks: {hook_count}",
            source="file_preview",
        ))

    if framework in ("django", "flask"):
        patterns.append(Pattern(
            category="code_structure",
            title=f"{framework.title()} web application structure detected",
            description=f"Standard {framework} project layout identified",
            snippet=None,
            source="file_tree",
        ))

    return patterns


async def extract_patterns_deep(
    owner: str, repo: str, focus: str | None = None
) -> DeepPatternReport:
    cached = _PATTERN_CACHE.get(f"{owner}/{repo}")
    if cached:
        return cached

    repo_url = f"https://github.com/{owner}/{repo}"
    clone_path = ""

    try:
        clone_path = cloner.clone_repo(repo_url)
    except ToolError:
        return DeepPatternReport(
            owner=owner,
            repo=repo,
            framework=None,
            verdict="skip",
            cached=False,
            timestamp=_now_iso(),
        )

    try:
        tree_raw = cloner.get_directory_tree(clone_path)
        tree_visual = cloner.format_tree(tree_raw, max_lines=200)

        framework = detect_framework(clone_path)

        all_files = _collect_all_files(clone_path)
        key_candidates = [
            f for f in all_files
            if any(
                f.endswith(ext)
                for ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".toml", ".json", ".yml", ".yaml")
            )
            and not f.startswith(".venv/")
            and "node_modules/" not in f
        ]

        if focus:
            key_candidates = [f for f in key_candidates if _matches_focus(f, focus)]

        key_candidates = key_candidates[:20]

        full_file_snippets: dict[str, str] = {}
        for rel_path in key_candidates:
            abs_path = os.path.join(clone_path, rel_path)
            content = _read_file(abs_path, max_lines=100)
            if content:
                full_file_snippets[rel_path] = content

        lang_patterns = _identify_language_patterns(clone_path, framework)

        if focus and lang_patterns:
            lang_patterns = [
                p for p in lang_patterns
                if _matches_focus(p.title, focus)
                or _matches_focus(p.description, focus)
            ]

        # Verdict domain: deep local inspection — framework detection + file-level patterns
        verdict = "useful" if framework or lang_patterns else "maybe"

        report = DeepPatternReport(
            owner=owner,
            repo=repo,
            framework=framework,
            patterns=lang_patterns,
            full_file_snippets=full_file_snippets,
            tree_visual=tree_visual,
            verdict=verdict,
            cached=False,
            timestamp=_now_iso(),
        )

        _PATTERN_CACHE[f"{owner}/{repo}"] = report
        return report

    finally:
        if clone_path:
            cloner.cleanup_clone(clone_path)
