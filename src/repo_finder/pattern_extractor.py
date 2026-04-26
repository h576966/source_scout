import re
from datetime import UTC, datetime

from . import repo_inspector
from .github_client import get_client
from .models import Pattern, PatternReport

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
        timestamp=datetime.now(UTC).isoformat(),
    )
