# FastMCP Patterns

## Minimal Server

```python
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

mcp = FastMCP("ServerName")

@mcp.tool(annotations={"readOnlyHint": True})
async def my_tool(param: str) -> MyResult:
    if not param:
        raise ToolError("param is required")
    return MyResult(...)

if __name__ == "__main__":
    mcp.run()  # default transport="stdio"
```

## Tool Definition Patterns

### Sync tool (runs in threadpool):
```python
@mcp.tool
def cpu_intensive(x: int) -> int:
    return x * 2
```

### Async tool (for I/O):
```python
@mcp.tool(annotations={"readOnlyHint": True})
async def fetch_data(url: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        return resp.json()
```

### With metadata:
```python
@mcp.tool(
    name="custom_name",
    description="Tool description",
    tags={"search"},
    annotations={"readOnlyHint": True},
    timeout=30.0,
)
```

## Input Validation

### Simple parameter with Field:
```python
from typing import Annotated
from pydantic import Field

@mcp.tool
async def search(
    query: Annotated[str, Field(description="Search query")],
    limit: Annotated[int, Field(ge=1, le=10, description="Max results")] = 10,
) -> SearchResult:
    ...
```

### Complex input with Pydantic model:
```python
from pydantic import BaseModel

class SearchInput(BaseModel):
    query: str
    language: str | None = None
    min_stars: int = 0

@mcp.tool
async def search(input: SearchInput) -> SearchResult:
    ...
```

Models must be passed as JSON objects, not stringified JSON.

## Output Patterns

### Return dataclass for structured content:
```python
from dataclasses import dataclass

@dataclass
class MyResult:
    items: list[str]
    total: int
    verdict: str
    cached: bool
    timestamp: str
```

FastMCP auto-generates both text content and structured content from dataclasses.

### Return Pydantic model:
```python
from pydantic import BaseModel

class MyResult(BaseModel):
    items: list[str]
    total: int
```

### Explicit ToolResult for full control:
```python
from fastmcp.tools.tool import ToolResult

return ToolResult(
    content="Summary text",
    structured_content={"data": ...},
    meta={"execution_time_ms": 145},
)
```

## Error Handling

### User-facing errors:
```python
from fastmcp.exceptions import ToolError

if not valid:
    raise ToolError("Input validation failed: ...")
```

### System errors:
```python
raise RuntimeError(json.dumps({
    "error": "Rate limit exceeded",
    "recoverable": True,
    "retry_after": 45,
}))
```

### Production error masking:
```python
mcp = FastMCP(name="Server", mask_error_details=True)
```
With masking on:
- `ToolError("msg")` → client sees "msg"
- `ValueError("msg")` → client sees generic message

## Transport

- `stdio` — default, for local MCP clients like Kilo/Claude Desktop
- `http` — for remote clients, endpoint at `http://host:port/mcp`
- `sse` — deprecated, avoid

## Running

```bash
# Via __main__ (custom CLI args):
python -m repo_finder

# Via fastmcp CLI (imports server object):
fastmcp run src/repo_finder/server.py:mcp

# HTTP transport:
fastmcp run src/repo_finder/server.py:mcp --transport http --port 8000
```

## Dependency

```toml
[project]
dependencies = [
    "fastmcp>=2.0.0,<3.0.0",
]
```

`fastmcp` bundles `pydantic`, `starlette`, `uvicorn`. No additional deps needed for basic usage.
