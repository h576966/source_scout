import importlib
import json
from pathlib import Path

import pytest

from repo_finder import bundles, catalog, evidence, pipeline


@pytest.fixture(autouse=True)
def isolated_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("REPO_FINDER_HOME", str(tmp_path / ".repo_finder"))
    catalog.reset_connection()
    yield
    catalog.reset_connection()


def _write_nextjs_fixture(root: Path) -> None:
    (root / "components" / "data-table").mkdir(parents=True)
    (root / "components" / "data-table" / "data-table.tsx").write_text(
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
        json.dumps(
            {
                "dependencies": {
                    "next": "15.0.0",
                    "react": "19.0.0",
                    "@tanstack/react-table": "8.0.0",
                    "tailwindcss": "4.0.0",
                },
                "devDependencies": {"typescript": "5.0.0"},
            }
        ),
        encoding="utf-8",
    )
    (root / "tsconfig.json").write_text("{}", encoding="utf-8")


def _profile(capability_name: str, evidence_paths: list[str], confidence: float = 0.9) -> dict:
    return {
        "schema_version": "gemma-profile-v1",
        "repository_type": "reference_application",
        "capabilities": [
            {"name": capability_name, "confidence": confidence, "evidence": evidence_paths}
        ],
        "likely_usefulness": 0.8,
        "extractability": 0.8,
        "maintenance_quality": 0.7,
        "needs_fastcontext": True,
        "concerns": [],
    }


def test_catalog_schema_and_paths() -> None:
    conn = catalog.get_connection()
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }
    assert {
        "repositories",
        "snapshots",
        "repository_cards",
        "assets",
        "reuse_outcomes",
        "analysis_runs",
    }.issubset(tables)

    path = catalog.snapshot_path("owner", "repo", "abc123")
    assert path.name == "abc123"
    assert "owner__repo" in str(path)


def test_repository_card_and_ui_gates(tmp_path: Path) -> None:
    _write_nextjs_fixture(tmp_path)
    card = pipeline.build_repository_card(tmp_path)
    assert card["stack_signals"]["has_react_dependency"] is True
    assert card["stack_signals"]["has_next_dependency"] is True
    assert card["stack_signals"]["has_typescript_files"] is True

    metadata = {
        "private": False,
        "archived": False,
        "mirror_url": None,
        "pushed_at": "2026-06-20T12:00:00Z",
    }
    ok, reason = pipeline._passes_ui_gates(metadata, card)
    assert ok is True
    assert reason == "qualified"


def test_scout_queries_filter_archived_stale_and_large_repos() -> None:
    query = pipeline.build_nextjs_ui_queries()[0][1]

    assert "archived:false" in query
    assert "pushed:>=" in query
    assert f"size:<={pipeline.MAX_REPOSITORY_SIZE_KB}" in query


def test_metadata_gates_reject_archived_and_large_repos() -> None:
    base = {
        "private": False,
        "archived": False,
        "mirror_url": None,
        "pushed_at": "2026-06-20T12:00:00Z",
        "size": 10,
    }

    assert pipeline._passes_metadata_gates({**base, "archived": True}) == (False, "archived")
    assert pipeline._passes_metadata_gates(
        {**base, "size": pipeline.MAX_REPOSITORY_SIZE_KB + 1}
    ) == (False, "too large")


def test_list_repositories_for_qualification_filters_archived_and_large_repos() -> None:
    def store(name: str, archived: bool, size: int) -> None:
        catalog.upsert_repository(
            {
                "owner": {"login": "owner"},
                "name": name,
                "full_name": f"owner/{name}",
                "html_url": f"https://github.com/owner/{name}",
                "private": False,
                "archived": archived,
                "language": "TypeScript",
                "size": size,
                "topics": ["nextjs"],
            },
            "test",
        )

    store("good", archived=False, size=10)
    store("archived", archived=True, size=10)
    store("huge", archived=False, size=pipeline.MAX_REPOSITORY_SIZE_KB + 1)

    candidates = catalog.list_repositories_for_qualification(10)

    assert [candidate["repo_id"] for candidate in candidates] == ["owner/good"]


def test_snapshot_content_gates_reject_docs_only_and_lockfile_only(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    (docs_root / "README.md").write_text("# Docs only", encoding="utf-8")
    docs_card = pipeline.build_repository_card(docs_root)

    assert pipeline._passes_snapshot_content_gates(docs_card) == (False, "docs-only or empty")

    lock_root = tmp_path / "lock"
    lock_root.mkdir()
    (lock_root / "package-lock.json").write_text("{}", encoding="utf-8")
    lock_card = pipeline.build_repository_card(lock_root)

    assert pipeline._passes_snapshot_content_gates(lock_card) == (False, "lockfile-only")


def test_snapshot_content_gates_reject_generated_vendor_heavy_repo(tmp_path: Path) -> None:
    _write_nextjs_fixture(tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    for index in range(25):
        (dist / f"generated-{index}.js").write_text("export const value = 1", encoding="utf-8")

    card = pipeline.build_repository_card(tmp_path)

    assert card["deterministic_features"]["generated_vendor_file_count"] == 25
    assert pipeline._passes_snapshot_content_gates(card) == (False, "generated/vendor-heavy")


def test_evidence_scan_returns_line_citations_and_dependencies(tmp_path: Path) -> None:
    _write_nextjs_fixture(tmp_path)
    result = evidence.scan_snapshot(tmp_path, "data-table")
    assert "components/data-table/data-table.tsx" in result["entry_paths"]
    assert "@tanstack/react-table" in result["external_dependencies"]
    assert any(path.startswith("components/data-table/data-table.tsx:") for path in result["evidence_paths"])
    assert result["reuse_score"] > 0


def test_evidence_scan_prefers_ui_component_over_schema_noise(tmp_path: Path) -> None:
    _write_nextjs_fixture(tmp_path)
    (tmp_path / "drizzle").mkdir()
    (tmp_path / "drizzle" / "schema.ts").write_text(
        "\n".join(
            [
                "export const auditTable = table('audit', {",
                "  data: text('data'),",
                "  columns: text('columns'),",
                "  tableName: text('table_name'),",
                "})",
            ]
        ),
        encoding="utf-8",
    )

    result = evidence.scan_snapshot(tmp_path, "data-table")

    assert result["entry_paths"][0] == "components/data-table/data-table.tsx"
    assert "drizzle/schema.ts" not in result["entry_paths"]
    assert result["synthesis"]["ui_path_score"] > result["synthesis"]["noise_penalty"]


def test_bundle_manifest_copies_evidence_files(tmp_path: Path) -> None:
    snapshot_root = tmp_path / "snapshot"
    snapshot_root.mkdir()
    _write_nextjs_fixture(snapshot_root)

    repo_id = catalog.upsert_repository(
        {
            "owner": {"login": "owner"},
            "name": "repo",
            "full_name": "owner/repo",
            "html_url": "https://github.com/owner/repo",
            "private": False,
            "archived": False,
            "language": "TypeScript",
            "topics": ["nextjs"],
        },
        "test",
    )
    snapshot_id = catalog.upsert_snapshot(repo_id, "abc123", "main", snapshot_root)
    catalog.upsert_repository_card(snapshot_id, pipeline.build_repository_card(snapshot_root))
    asset_id = catalog.upsert_asset(
        snapshot_id,
        repo_id,
        "data-table",
        evidence.scan_snapshot(snapshot_root, "data-table"),
    )

    result = bundles.create_source_bundle(asset_id)
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["candidate_id"] == asset_id
    assert "components/data-table/data-table.tsx" in manifest["files"]
    assert (Path(result.bundle_path) / "source" / "components/data-table/data-table.tsx").exists()


def test_search_assets_uses_gemma_profile_and_ui_scores(tmp_path: Path) -> None:
    good_root = tmp_path / "good"
    good_root.mkdir()
    _write_nextjs_fixture(good_root)
    good_repo_id = catalog.upsert_repository(
        {
            "owner": {"login": "good"},
            "name": "repo",
            "full_name": "good/repo",
            "html_url": "https://github.com/good/repo",
            "private": False,
            "archived": False,
            "language": "TypeScript",
            "topics": ["nextjs", "table"],
        },
        "test",
    )
    good_snapshot_id = catalog.upsert_snapshot(good_repo_id, "goodsha", "main", good_root)
    good_card = pipeline.build_repository_card(good_root)
    good_card["gemma_profile"] = _profile(
        "Complex data tables",
        ["components/data-table/data-table.tsx"],
    )
    catalog.upsert_repository_card(good_snapshot_id, good_card)
    good_asset_id = catalog.upsert_asset(
        good_snapshot_id,
        good_repo_id,
        "data-table",
        evidence.scan_snapshot(good_root, "data-table"),
    )

    noisy_root = tmp_path / "noisy"
    noisy_root.mkdir()
    (noisy_root / "app" / "dashboard").mkdir(parents=True)
    (noisy_root / "app" / "dashboard" / "page.tsx").write_text(
        "export default function Dashboard() { return <main>Dashboard data</main> }",
        encoding="utf-8",
    )
    (noisy_root / "package.json").write_text(
        json.dumps({"dependencies": {"next": "15", "react": "19", "tailwindcss": "4"}}),
        encoding="utf-8",
    )
    noisy_repo_id = catalog.upsert_repository(
        {
            "owner": {"login": "noisy"},
            "name": "repo",
            "full_name": "noisy/repo",
            "html_url": "https://github.com/noisy/repo",
            "private": False,
            "archived": False,
            "language": "TypeScript",
            "topics": ["nextjs", "dashboard"],
        },
        "test",
    )
    noisy_snapshot_id = catalog.upsert_snapshot(noisy_repo_id, "noisysha", "main", noisy_root)
    noisy_card = pipeline.build_repository_card(noisy_root)
    noisy_card["gemma_profile"] = _profile("Dashboard shell", ["app/dashboard/page.tsx"])
    catalog.upsert_repository_card(noisy_snapshot_id, noisy_card)
    catalog.upsert_asset(
        noisy_snapshot_id,
        noisy_repo_id,
        "data-table",
        {
            "entry_paths": ["app/dashboard/page.tsx"],
            "dependency_paths": ["package.json"],
            "external_dependencies": ["next", "react", "tailwindcss"],
            "evidence_paths": ["app/dashboard/page.tsx:1-1", "package.json:1-1"],
            "reuse_score": 0.82,
            "synthesis": {
                "adaptation_notes": ["Dashboard-only evidence should not satisfy data-table reuse."],
                "ui_path_score": 0.45,
                "noise_penalty": 0.2,
            },
        },
    )

    results = catalog.search_assets(
        "Find a reusable data table for a Next.js Tailwind dashboard",
        max_repos=2,
    )

    assert results[0].candidate_id == good_asset_id
    assert results[0].repo_id == "good/repo"
    assert results[0].score > results[1].score


def test_search_assets_sorts_by_raw_score_before_display_clamp(tmp_path: Path) -> None:
    def store_asset(repo_id: str, entry_paths: list[str], dependencies: list[str], cap_path: float) -> str:
        owner, name = repo_id.split("/", 1)
        root = tmp_path / owner / name
        root.mkdir(parents=True)
        stored_repo_id = catalog.upsert_repository(
            {
                "owner": {"login": owner},
                "name": name,
                "full_name": repo_id,
                "html_url": f"https://github.com/{repo_id}",
                "private": False,
                "archived": False,
                "language": "TypeScript",
                "topics": ["nextjs", "table"],
            },
            "test",
        )
        snapshot_id = catalog.upsert_snapshot(stored_repo_id, f"{owner}sha", "main", root)
        catalog.upsert_repository_card(snapshot_id, {"card_version": "repo-card-v1"})
        return catalog.upsert_asset(
            snapshot_id,
            stored_repo_id,
            "data-table",
            {
                "entry_paths": entry_paths,
                "dependency_paths": ["package.json"],
                "external_dependencies": dependencies,
                "evidence_paths": [f"{entry_paths[0]}:1-3"],
                "reuse_score": 1.0,
                "synthesis": {
                    "adaptation_notes": [],
                    "ui_path_score": 1.0,
                    "noise_penalty": 0.0,
                    "capability_path_score": cap_path,
                },
            },
        )

    store_asset(
        "lower/repo",
        ["components/misc/page.tsx"],
        ["next", "react", "tailwindcss"],
        0.8,
    )
    better_asset_id = store_asset(
        "better/repo",
        ["components/data-table/data-table.tsx"],
        ["next", "react", "tailwindcss", "@tanstack/react-table"],
        1.0,
    )

    results = catalog.search_assets(
        "Find a reusable data table for a Next.js Tailwind dashboard",
        max_repos=2,
    )

    assert results[0].candidate_id == better_asset_id
    assert results[0].score == 1.0
    assert results[1].score == 1.0


def test_backend_path_detection_includes_action_filenames() -> None:
    assert catalog._has_backend_path(["src/app/(auth)/register/actions.ts"]) is True


def test_search_assets_prefers_real_background_job_paths_over_service_worker_noise(
    tmp_path: Path,
) -> None:
    def store_asset(repo_id: str, entry_paths: list[str], reuse_score: float) -> str:
        owner, name = repo_id.split("/", 1)
        root = tmp_path / owner / name
        root.mkdir(parents=True)
        stored_repo_id = catalog.upsert_repository(
            {
                "owner": {"login": owner},
                "name": name,
                "full_name": repo_id,
                "html_url": f"https://github.com/{repo_id}",
                "private": False,
                "archived": False,
                "language": "TypeScript",
                "topics": ["nextjs"],
            },
            "test",
        )
        snapshot_id = catalog.upsert_snapshot(stored_repo_id, f"{owner}sha", "main", root)
        catalog.upsert_repository_card(snapshot_id, {"card_version": "repo-card-v1"})
        return catalog.upsert_asset(
            snapshot_id,
            stored_repo_id,
            "background-jobs",
            {
                "entry_paths": entry_paths,
                "dependency_paths": ["package.json"],
                "external_dependencies": ["next", "react"],
                "evidence_paths": [f"{path}:1-3" for path in entry_paths],
                "reuse_score": reuse_score,
                "synthesis": {
                    "adaptation_notes": [],
                    "ui_path_score": 1.0,
                    "noise_penalty": 0.0,
                    "capability_path_score": 1.0,
                },
            },
        )

    store_asset(
        "pwa/repo",
        [
            "src/lib/register-service-worker.ts",
            "src/app/admin/feeding/components/FeedSchedule.tsx",
        ],
        1.0,
    )
    worker_asset_id = store_asset(
        "worker/repo",
        ["worker/src/lib/sequence-processor.ts", "worker/src/index.ts"],
        0.8,
    )

    results = catalog.search_assets(
        "Find background job, worker, sync, or scheduled processing code",
        max_repos=2,
    )

    assert results[0].candidate_id == worker_asset_id
    assert results[0].repo_id == "worker/repo"


async def _tool_names(module) -> set[str]:
    tools = await module.mcp.get_tools()
    return set(tools.keys())


@pytest.mark.asyncio
async def test_legacy_tools_hidden_by_default(monkeypatch) -> None:
    monkeypatch.delenv("REPO_FINDER_ENABLE_LEGACY_TOOLS", raising=False)
    import repo_finder.server as server

    reloaded = importlib.reload(server)
    names = await _tool_names(reloaded)
    assert {"find_reusable_code", "get_source_bundle", "record_reuse_outcome"}.issubset(names)
    assert "find_repos_for_task" not in names


@pytest.mark.asyncio
async def test_legacy_tools_enabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("REPO_FINDER_ENABLE_LEGACY_TOOLS", "1")
    import repo_finder.server as server

    reloaded = importlib.reload(server)
    names = await _tool_names(reloaded)
    assert "find_repos_for_task" in names
    assert "find_reusable_code" in names

    monkeypatch.delenv("REPO_FINDER_ENABLE_LEGACY_TOOLS", raising=False)
    importlib.reload(server)
