# Coding Conventions

## Code Style

- Follow the existing style in the codebase. Look at neighboring files before writing new code.
- Match indentation, quoting, import style, and naming conventions exactly.
- One concept per file. Split files that exceed 500 lines.
- Functions do one thing. Keep them under 50 lines.

## Architecture

- Prefer plain functions over classes unless the codebase consistently uses classes.
- Reuse existing patterns, libraries, and utilities. New abstractions require justification.
- Group related files in directories with clear boundaries. Avoid deep nesting.

## Error Handling

- Handle errors explicitly. Never swallow exceptions with empty catch blocks.
- Validate inputs at system boundaries. Return meaningful error messages.

## Do NOT

- **Do not commit secrets, API keys, or credentials.** Use environment variables.
- **Do not leave dead code, commented-out blocks, or debug statements.**
- **Do not use wildcard imports** unless the project consistently uses them.
- **Do not add comments that describe what the code does.** Comments explain **why**, not **what**.
- **Do not add TODO comments in production code.** Convert them to tickets or fix them immediately.

## Testing

- Write tests for new functionality before claiming work is done.
- Test behavior, not implementation details.
- One assertion concept per test. Use descriptive test names.
- Do not skip or disable flaky tests — fix them.

## Token Budget (Advisory)

- Prefer concise output over verbose prose. Shorter responses keep the context focused and costs lower.
- Architect plans: use bullet points and numbered steps. Skip explanatory preamble.
- Reviewer output: use the severity format directly. Skip process explanation.
- Worker: report changes in 1-3 bullet points rather than narrative paragraphs.
- These are guidelines — exceed them when the task genuinely requires more detail.

## Tool Calls

- Before calling a tool, read the parameter schema shown in the Available Tools list. Enum-constrained parameters (like `format` on webfetch: `"text" | "markdown" | "html"`) accept only those exact values. Do not guess — check the schema first.
