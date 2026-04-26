# Project Instructions

Local-first agentic development with a structured Plan → Execute → Review workflow.

## Workflow

1. **Plan** — `/plan` or switch to the plan agent for design and architecture. Never code first.
2. **Execute** — Delegate implementation steps to the worker agent. One step at a time.
3. **Review** — `/review` after every meaningful change. Address CRITICAL issues before proceeding.

## Agents

| Agent | Mode | Model | Use for |
|-------|------|-------|---------|
| plan | primary | deepseek-v4-pro | System design, architecture, planning |
| ask | primary | deepseek-v4-flash | Code explanation, questions, research |
| reviewer | subagent | deepseek-v4-pro | Code review (read-only) |
| worker | subagent | deepseek-v4-flash | Implementation of defined tasks |

## Skills

Add project-specific skills to `.kilo/skills/`. Each skill is a directory with a `SKILL.md` file. The directory name becomes the skill identifier (`/skill <name>`). Skills should encode patterns the LLM cannot infer from your codebase — skip anything generic (debugging, TDD, code review). An example stub is provided in `.kilo/skills/example/`.

## Do NOT

- **Do not jump to implementation without a plan.** Non-trivial changes require a written plan first.
- **Do not add dependencies or libraries without discussion.**
- **Do not refactor unrelated code.**
- **Do not leave debug logs, TODO comments, or commented-out code.**
- **Do not skip linting, type-checking, or tests.** Work is not done until all three pass. After 2 failed fix attempts, escalate.
