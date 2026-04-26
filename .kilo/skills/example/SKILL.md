---
name: example
description: Template for project-specific skills. Replace this with patterns unique to your project (e.g., API conventions, migration steps, deploy flow). Delete this example.
---

# Skill Name

Keep skills focused on what the LLM cannot infer from your codebase alone.

## When to Add a Skill

- Non-standard architecture patterns specific to your project
- Multi-step processes with manual gates (deploy, rollback, migration)
- Conventions enforced by judgment rather than linters
- Domain-specific knowledge (regulatory, compliance, business logic)

## Format

The frontmatter `name` becomes the `/skill <name>` identifier. The markdown body is injected into the agent's context. Keep skills under 60 lines — longer than that, and compaction may trim them.
