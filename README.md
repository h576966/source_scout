# GitHub Project Finder MCP

An MCP server that enables AI coding agents to find, evaluate, and extract value from relevant public GitHub repositories. This is a **decision + compression layer** on top of GitHub — not a search wrapper.

## Purpose

AI agents often need to find reference implementations, evaluate libraries, and extract implementation patterns. Raw GitHub search returns noise. This server:

- Finds high-signal repositories ranked by relevance, activity, and quality
- Returns short structured summaries with clear verdicts
- Compares repositories side-by-side
- Extracts distilled implementation patterns from READMEs and key files

## Prerequisites

- Python 3.11+
- GitHub personal access token (public repo access only)

## Quick Start

```bash
pip install -e .
set GITHUB_TOKEN=ghp_your_token_here
fastmcp run src/repo_finder/server.py:mcp
```

## Tools

| Tool | Input | Output |
|------|-------|--------|
| `find_repos_for_task` | Task description + optional filters (language, min stars, max age, license) | Top 5–10 ranked repos with summaries and verdicts |
| `inspect_github_repo` | GitHub repo URL or `owner/repo` | Structured analysis: purpose, structure, quality, verdict |
| `compare_github_repos` | 2–5 repo URLs | Side-by-side comparison with recommended choice |
| `extract_patterns_from_repo` | Repo URL + optional focus area | Distilled patterns: architecture, code structure, best practices |

All tools return structured JSON with `verdict`, `cached`, and `timestamp` fields.

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `GITHUB_TOKEN` | **required** | GitHub personal access token |
| `CACHE_TTL_SEARCH` | `1800` | Search result cache TTL in seconds (30 min) |
| `CACHE_TTL_METADATA` | `3600` | Repo metadata cache TTL in seconds (1 hour) |
| `CACHE_TTL_README` | `7200` | README cache TTL in seconds (2 hours) |
| `CACHE_DB_PATH` | in-memory | Optional persistent cache path (e.g., `cache.duckdb`) |

## Example Agent Workflow

```
Agent task: "Build a lightweight FastAPI backend with auth and SQLite"

1. find_repos_for_task(task="lightweight FastAPI backend auth SQLite", language="Python")
   → 8 ranked repos with verdicts

2. inspect_github_repo("tiangolo/full-stack-fastapi-template")
   → Structured analysis: purpose, architecture, quality signals

3. inspect_github_repo("fastapi-users/fastapi-users")
   → Structured analysis

4. compare_github_repos(["repo1", "repo2", "repo3"])
   → Side-by-side comparison, recommended: repo2

5. extract_patterns_from_repo("best-repo-url", focus="auth")
   → Auth patterns, code structure, best practices
```

## Project Structure

```
src/repo_finder/
    server.py              # FastMCP server + 4 tool definitions
    github_client.py       # GitHub REST API client
    cache.py               # DuckDB-based query/result cache
    ranker.py              # Multi-factor repo scoring
    repo_inspector.py      # Deep repo analysis
    pattern_extractor.py   # README + file tree pattern extraction
    models.py              # Shared dataclasses
    __main__.py            # CLI entry point
```

## Non-Goals

- Not a full GitHub crawler
- Not indexing every repository
- No vector database (can be added later)
- No local repo cloning in MVP
- No tree-sitter code parsing in MVP

## License

MIT
