# Source Scout Direction

Source Scout should optimize one job:

> Given a coding task, return one or a few evidence-backed source bundles that
> make Codex faster and less wasteful.

This document is a short product orientation. Scope boundaries live in
[`complexity-budget.md`](complexity-budget.md).

## Product Shape

Source Scout is a local-first reuse assistant for coding agents. It is not a
generic GitHub search replacement, repo ranking site, SaaS product, or autonomous
integration system.

The useful workflow is:

```text
coding task
  -> find_reusable_code
  -> assess_reusable_code when needed
  -> get_source_bundle
  -> Codex reads cited source, edits, and tests
```

The output should be small and actionable: exact files, line evidence, commit
SHA, dependencies, adaptation notes, and a task-linked bundle.

## Current Product Path

- Build and maintain a local catalog of recent, public, commit-pinned source
  snapshots.
- Extract deterministic evidence from paths, manifests, dependencies, and source
  files without executing repository code.
- Use Gemma only to assess validated evidence for a specific task.
- Use FastContext only to find additional file and line evidence when the
  deterministic scan is weak or when local repo exploration would otherwise
  waste search/read cycles.
- Track reuse outcomes against the original task signature.

## Model Roles

- Deterministic code validates, scores, gates, fingerprints, and persists.
- FastContext scouts evidence only.
- Gemma interprets validated evidence only.
- Codex reads the cited source and owns edits/tests.

These boundaries keep the system reproducible and avoid turning Source Scout
into a broad agent framework.

## Practical Priorities

Near-term work should improve the current loop, not expand the product:

- Better shortlist quality for real coding tasks.
- Better assessment calibration from golden evals.
- Better evidence bundles with fewer irrelevant files.
- Lower token/time waste for Codex through local exploration.
- Simpler code and tests around the active product path.

Historical design reports, broad future architecture, and speculative provider
plans do not belong in the main docs. If an idea does not improve the task to
bundle loop, keep it out of scope until evals show a concrete need.
