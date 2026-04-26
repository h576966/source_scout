# GitHub API Patterns

## Search Endpoint

```python
# URL: GET https://api.github.com/search/repositories
# Query: q parameter with qualifiers
# Sort: stars, forks, updated, best match
# Order: desc, asc
# Pagination: per_page (max 100), page

params = {
    "q": "language:python topic:fastapi stars:>=100 pushed:>2024-01-01",
    "sort": "stars",
    "order": "desc",
    "per_page": 30,
}
```

## Search Query Qualifiers

| Qualifier | Example |
|-----------|---------|
| `language:` | `language:typescript` |
| `stars:` | `stars:>=100`, `stars:100..500` |
| `forks:` | `forks:>=50` |
| `topic:` | `topic:react` |
| `pushed:` | `pushed:>2024-01-01` |
| `created:` | `created:>2023-06-01` |
| `license:` | `license:mit` |
| `archived:` | `archived:false` |
| `fork:` | `fork:true` (include forks), `fork:only` (only forks) |
| `in:` | `in:readme`, `in:name`, `in:description`, `in:topics` |
| `org:` | `org:google` |
| `user:` | `user:torvalds` |

## Rate Limit Headers

Every response includes these headers (lowercase):

```
x-ratelimit-limit: 5000
x-ratelimit-remaining: 4987
x-ratelimit-used: 13
x-ratelimit-reset: 1712345678          # UTC epoch seconds
x-ratelimit-resource: core              # or "search"
```

Search rate limits: 30 req/min (auth'd), 10 req/min (unauth'd).

Check for exhaustion:
```python
remaining = int(response.headers.get("x-ratelimit-remaining", "1"))
if remaining == 0:
    reset_time = int(response.headers["x-ratelimit-reset"])
    retry_after = reset_time - int(time.time())
    raise RateLimitError(f"Rate limit exceeded. Retry in {retry_after}s", retry_after=retry_after)
```

## Pagination via Link Header

```
link: <https://api.github.com/search/repositories?q=...&page=2>; rel="next",
      <https://api.github.com/search/repositories?q=...&page=10>; rel="last"
```

Parse using regex:
```python
import re
LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')
```

## Repository Metadata

Endpoint: `GET /repos/{owner}/{repo}`

Key response fields:
- `full_name`, `description`, `html_url`
- `language`, `topics`, `license` (object with `spdx_id`, `name`)
- `stargazers_count`, `forks_count`, `open_issues_count`, `watchers`
- `pushed_at`, `created_at`, `updated_at`
- `archived`, `disabled`, `private`
- `default_branch`, `size`
- `has_issues`, `has_pages`, `has_wiki`, `has_discussions`

## README Endpoint

Endpoint: `GET /repos/{owner}/{repo}/readme`

Response:
```json
{
  "name": "README.md",
  "content": "IyBFeGFtcGxl...",    // base64-encoded
  "encoding": "base64",
  "size": 1234
}
```

Decode: `base64.b64decode(content).decode("utf-8")`

For raw content without base64: add header `Accept: application/vnd.github.raw+json`

## Contents Endpoint (File Tree)

Endpoint: `GET /repos/{owner}/{repo}/contents/{path}`

For root directory, use empty path or just `/repos/{owner}/{repo}/contents`.

Response for directory: array of `{ type: "file"|"dir", name, path, size, ... }`
Response for file: single object with `content` (base64), `encoding`

Limits:
- Max 1000 files per directory
- Files ≤1 MB: full content in `content` field
- Files 1-100 MB: `encoding: "none"`, `content: ""`
- Files >100 MB: not supported

## Commits Endpoint

Endpoint: `GET /repos/{owner}/{repo}/commits?per_page=20&since=...`

Use for activity evaluation — check recency and frequency of commits.

## Error Codes

| Status | Meaning | Action |
|--------|---------|--------|
| 200 | Success | Process response |
| 403 + rate limit header | Rate limited | Calculate retry_after, warn client |
| 404 | Not found or private | Return user-facing "not found" error |
| 422 | Invalid query | Return the GitHub error message |
| 5xx | Server error | Retry once after 1s, then fail |

## Required API Token Scope

Public repo access only — no special scopes needed.
Token can be a fine-grained PAT or classic PAT with `public_repo` scope.
