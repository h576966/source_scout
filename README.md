# Repo Finder

Local-first MCP server and CLI for finding reusable Next.js / React / TypeScript
UI code in public GitHub repositories.

The current direction is a **catalog-first reuse layer**, not generic GitHub
search. The system scouts candidate repositories, stores reproducible local
snapshots by commit SHA, extracts deterministic file-level evidence, and exposes
small source bundles to coding agents.

See `docs/repo-finder-direction.md` for the full design direction.

## Prerequisites

- Python 3.11+
- GitHub personal access token for public repository access
- Optional later: LM Studio with local Gemma/FastContext models

## Setup

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:GITHUB_TOKEN = "ghp_your_token_here"
```

## Catalog Workflow

```powershell
repo-finder scout --domain nextjs-ui --limit 500
repo-finder qualify --limit 100
repo-finder evidence --capability data-table --limit 30
repo-finder serve-mcp
```

Generated catalog data is stored under `.repo_finder/` by default:

```text
.repo_finder/
  cache.duckdb
  repos/
  bundles/
  logs/
```

Set `REPO_FINDER_HOME` to use a different local storage directory.

## MCP Tools

Default tools:

| Tool | Purpose |
|------|---------|
| `find_reusable_code(task, project_path=None, max_repos=3)` | Return shortlisted reusable candidates with evidence paths and adaptation notes. |
| `get_source_bundle(candidate_id)` | Copy recommended files/config into a local bundle and write a manifest. |
| `record_reuse_outcome(candidate_id, outcome, notes=None)` | Track selected, integrated, or rejected candidates for future ranking. |

Legacy generic GitHub tools are hidden by default. Set
`REPO_FINDER_ENABLE_LEGACY_TOOLS=1` only for debugging older behavior.

## Local Checks

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe -m pytest -q
```

Corpus quality check:

```powershell
.\.venv\Scripts\python.exe scripts\run_quality_checks.py
```

## Project Structure

```text
src/repo_finder/
  server.py          # FastMCP tools
  __main__.py        # CLI commands
  catalog.py         # Persistent DuckDB catalog
  pipeline.py        # Scout/qualify/gc workflow
  evidence.py        # Deterministic evidence extraction
  bundles.py         # Source bundle generation
  snapshotter.py     # Commit-SHA local snapshots
  github_client.py   # GitHub REST client
```

## Constraints

- First domain is Next.js / React / TypeScript UI reuse.
- Do not execute arbitrary cloned repository code.
- Analyze exact commit SHAs, not moving branch heads.
- Keep generated data local.
- Use local/manual review only; no external PR review services.
