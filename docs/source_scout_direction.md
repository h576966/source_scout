# Source Scout Direction

Source Scout should optimize one job:

> Given a coding task, return one or a few evidence-backed source bundles that
> make Codex faster and less wasteful.

This document is a short product orientation. Scope boundaries live in
[`complexity-budget.md`](complexity-budget.md).

## Product Shape

Source Scout is an RLM-first local source reuse assistant for coding agents. It
is focused on your working stack: TypeScript, JavaScript, Python, AI/local-AI
harnesses, data tooling, Next.js, Node, and React. It is not a generic GitHub
search replacement, repo ranking site, SaaS product, or autonomous integration
system.

The useful workflow is:

```text
coding task
  -> broad catalog retrieval
  -> RLM project understanding, candidate comparison, and reranking
  -> task-specific assessment and source bundle review
  -> get_source_bundle
  -> Codex reads cited source, edits, and tests
```

The output should be small and actionable: exact files, line evidence, commit
SHA, dependencies, adaptation notes, and a task-linked bundle.

## Architecture Direction

- RLM is the primary reasoning layer for project understanding, candidate
  comparison, reranking, source bundle review, and eval diagnostics.
- The deterministic catalog search is broad retrieval. It should recall plausible
  candidates quickly, then hand them to RLM reasoning instead of acting as the
  final intelligence layer.
- Deterministic code remains responsible for bounded file access, path safety,
  line-range validation, hashing, persistence, traces, manifests, and eval
  metrics.
- The current `find_reusable_code -> assess_reusable_code -> get_source_bundle`
  loop stays usable while RLM components become the main architecture around it.

## Current Product Path

- Build and maintain a local catalog of recent, public, commit-pinned source
  snapshots from the opinionated `personal-code` discovery domain.
- Extract deterministic evidence from paths, manifests, dependencies, and source
  files without executing repository code.
- Use RLM reasoning to inspect project context, compare candidates, review
  bundles, and explain eval failures over bounded read-only tools.
- Use Gemma and FastContext as local model roles inside that architecture:
  FastContext finds file and line evidence, while Gemma assesses validated
  evidence for a specific task.
- Track reuse outcomes against the original task signature.

## Model Roles

- Deterministic code validates, bounds, hashes, gates, fingerprints, and
  persists.
- RLM coordinates reasoning over bounded local context and candidate data.
- FastContext scouts evidence as a read-only specialist.
- Gemma interprets validated evidence for task-specific assessment.
- Codex reads the cited source and owns edits/tests.

These boundaries keep Source Scout practical as a personal developer tool while
making RLM the central reasoning architecture.

## Practical Priorities

Near-term work should make the RLM-first loop more useful without expanding into
a hosted or autonomous integration product:

- Better shortlist quality for real coding tasks.
- RLM-backed candidate comparison and reranking over broad retrieval results.
- RLM bundle review that tells Codex what to read and adapt first.
- RLM eval diagnostics that explain why expected candidates lost.
- Read-only project understanding for target-project fit.
- Better assessment calibration from golden evals.
- Better evidence bundles with fewer irrelevant files.
- Lower token/time waste for Codex through local exploration.
- Simpler code and tests around the active product path.

No accidental mutation of user projects and no execution of cloned repository
code remain hard boundaries. If an idea does not improve project understanding,
candidate comparison, bundle usefulness, or eval learning speed, keep it out of
the main path.
