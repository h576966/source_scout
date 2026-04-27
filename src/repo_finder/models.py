from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class RepoScore:
    total: float
    relevance: float
    activity: float
    popularity: float
    structure: float
    license: float
    verdict: str


@dataclass
class RepoSummary:
    full_name: str
    html_url: str
    description: str | None
    language: str | None
    stars: int
    last_push: str
    score: float
    verdict: str
    risks: list[str] = field(default_factory=list)


@dataclass
class FindReposResult:
    query: str
    total_candidates_scored: int
    results: list[RepoSummary]
    cached: bool
    timestamp: str


@dataclass
class RepoStructure:
    dirs: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    key_files: list[str] = field(default_factory=list)


@dataclass
class QualityReport:
    signals: dict[str, str] = field(default_factory=dict)
    score: float = 0.0


@dataclass
class InspectionResult:
    owner: str
    repo: str
    description: str | None
    language: str | None
    stars: int
    forks: int
    open_issues: int
    license_name: str | None
    last_push: str
    archived: bool
    structure: RepoStructure
    quality: QualityReport
    readme_preview: str | None
    verdict: str
    verdict_reasoning: str
    cached: bool
    timestamp: str


@dataclass
class CompareItem:
    full_name: str
    stars: int
    activity: str
    quality_score: float
    license_name: str | None
    verdict: str


@dataclass
class CompareResult:
    repos: list[CompareItem]
    recommended: str
    reasoning: str
    cached: bool
    timestamp: str


@dataclass
class Pattern:
    category: str
    title: str
    description: str
    snippet: str | None
    source: str


@dataclass
class PatternReport:
    owner: str
    repo: str
    patterns: list[Pattern] = field(default_factory=list)
    file_tree: list[str] = field(default_factory=list)
    readme_sections: list[str] = field(default_factory=list)
    focus: str | None = None
    verdict: str = "maybe"
    cached: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class DeepPatternReport:
    owner: str
    repo: str
    framework: str | None
    patterns: list[Pattern] = field(default_factory=list)
    full_file_snippets: dict[str, str] = field(default_factory=dict)
    tree_visual: str = ""
    verdict: str = "maybe"
    cached: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class RateLimitError(Exception):
    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after
