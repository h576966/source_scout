# Development Workflow

## Phase 1: Plan → Gate: Plan Approved

1. Use `/plan` or switch to the **plan** agent.
2. The plan agent produces a structured plan.
3. Review the plan before approving.
4. **Gate:** All implementation steps are specific enough to execute without ambiguity.

## Phase 2: Execute → Gate: Lint + Tests Pass

1. Delegate each plan step to the **worker** agent via Task tool.
2. Worker implements the change, runs lint and tests.
3. **Gate:** Lint and tests must pass. After 2 failed attempts, escalate — do not retry.

## Phase 3: Review → Gate: No CRITICAL Issues

1. Run `/review` (or delegate to **reviewer**) after each meaningful change.
2. Fix all CRITICAL issues. WARNING issues should be fixed or documented. INFO is optional.
3. **Gate:** No CRITICAL issues remain.

## Escalation Policy

After 2 failed attempts with the same approach:

1. **Worker:** Escalate to a stronger model.
2. **Reviewer:** Revisit the plan with the plan agent.
3. **Plan:** Ask for clarification — requirements are likely unclear.
