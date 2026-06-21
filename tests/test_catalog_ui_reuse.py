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


def test_evidence_scan_returns_line_citations_and_dependencies(tmp_path: Path) -> None:
    _write_nextjs_fixture(tmp_path)
    result = evidence.scan_snapshot(tmp_path, "data-table")
    assert "components/data-table/data-table.tsx" in result["entry_paths"]
    assert "@tanstack/react-table" in result["external_dependencies"]
    assert any(path.startswith("components/data-table/data-table.tsx:") for path in result["evidence_paths"])
    assert result["reuse_score"] > 0


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
