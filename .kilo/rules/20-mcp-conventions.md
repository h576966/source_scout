# MCP Conventions

## Output Format

- All tool outputs MUST include: `verdict` ("useful"|"maybe"|"skip"), `cached` (bool), `timestamp` (ISO 8601 string)
- Never return raw GitHub API responses — always transform to structured output models
- Max 10 repos per list response
- Short summaries only — no full README dumps in list results
- Use dataclasses from `models.py` for all tool return types

## Error Responses

- All error responses MUST include: `error` (string), `recoverable` (boolean), `retry_after` (int | null)
- Use `ToolError` for user-visible validation errors (bad input, not found, private repo)
- Use `RuntimeError` with JSON-encoded error dict for system failures (API errors, rate limits, timeouts)
- Never expose stack traces or internal error details in tool output
- In production, set `mask_error_details=True` on FastMCP to prevent accidental leakage

## Data Boundaries

- No filesystem writes beyond the cache database
- No cloning of repositories (MVP constraint)
- No network calls beyond `api.github.com`
- GITHUB_TOKEN must be verified at server startup — fail fast

## Tool Design

- Each tool is a single `async def` function decorated with `@mcp.tool()`
- Inputs use Pydantic models or `Annotated` parameters with `Field` descriptions
- All I/O intensive work uses `async`/`await`
- Cache is checked first — if hit, skip downstream calls
- Each tool returns a single dataclass instance (auto-serialized to structured content by FastMCP)

## Ranking Constraints

- Never over-weight stars — popularity is only 15% of total score
- Always penalize archived repos (activity score becomes 0)
- Prefer simple, focused repos over massive frameworks
- Verdict thresholds: ≥0.7 = useful, ≥0.4 = maybe, <0.4 = skip
