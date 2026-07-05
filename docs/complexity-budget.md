# Complexity Budget

This document is the scope guardrail for Source Scout. Source Scout is moving
toward an RLM-first local architecture, but the budget stays local, bounded, and
evidence-backed rather than hosted, autonomous, or generic search.

## Product Core

- Local-first catalog of reusable source candidates.
- Commit-pinned snapshots and reproducible local evidence.
- Deterministic file, path, dependency, freshness, and evidence validation.
- RLM reasoning over bounded read-only context for project understanding,
  candidate comparison, reranking, bundle review, and eval diagnostics.
- Task-specific reuse assessment for a candidate and a concrete user task.
- Small MCP surface that helps coding agents find, assess, bundle, and track
  reusable code.

## Allowed Near-Term Features

- Eval-backed scoring and shortlist tuning.
- RLM core scaffolding: schemas, traces, prompts, and read-only local tools.
- RLM-backed diagnostics for failed evals and bundle usefulness.
- RLM-backed candidate comparison and reranking over broad catalog retrieval.
- Read-only target-project understanding for fit signals.
- Better deterministic evidence extraction and path/dependency signals.
- Gemma assessor calibration over validated evidence.
- Bounded FastContext refinement for missing or weak evidence.
- Standalone local exploration for the current repo or personal repos.
- Reuse outcome tracking tied to task signatures.
- Narrow new domains only after a golden eval suite exists.

## Deferred Features

- Broad framework, language, or repository-type coverage.
- Full dashboard or hosted UI product.
- Vector database or semantic index layer.
- Autonomous integration into target projects.
- Full open-ended RLM controller without eval coverage and hard tool bounds.
- Cloud/frontier model routing and provider abstraction.
- Multi-user accounts, auth, billing, or permissions.
- Full dependency, license, or legal compliance automation.

## Explicit Non-Goals

- Replacing GitHub search.
- Executing cloned repository code.
- Mutating user projects automatically.
- Letting RLM tools write files, run shell commands, or alter target projects.
- Building a public SaaS product.
- Ranking all repositories by generic quality.
- Adding external PR review workflows.
- Making license or legal reuse decisions.

## Default MCP Surface

Default MCP tools stay small:

- `find_reusable_code`
- `assess_reusable_code`
- `get_source_bundle`
- `record_reuse_outcome`
- `explore_local_code`

## Model Role Boundaries

- Deterministic code validates paths, line ranges, commit SHA, evidence hashes,
  scores, verdicts, bounded file access, manifests, traces, eval metrics, and
  persistence.
- RLM is the primary reasoning layer for understanding projects, comparing and
  reranking candidates, reviewing bundles, and diagnosing eval failures.
- Catalog search is broad retrieval, not the final intelligence layer.
- FastContext scouts for file and line evidence only.
- Gemma assesses validated evidence only; it does not write final scores.
- Codex reads cited source, edits code, and runs tests.
