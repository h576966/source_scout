import argparse
import asyncio
import os
import sys


def _require_github_token() -> None:
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return
    print("ERROR: GITHUB_TOKEN environment variable is required for this command.", file=sys.stderr)
    print("Set it via: set GITHUB_TOKEN=ghp_your_token  (Windows)", file=sys.stderr)
    print("         or: export GITHUB_TOKEN=ghp_your_token  (Unix)", file=sys.stderr)
    sys.exit(1)


def _run_mcp(transport: str, port: int) -> None:
    from .server import mcp

    if transport == "http":
        print(f"Starting MCP server on http://127.0.0.1:{port}/mcp")
        mcp.run(transport="http", host="127.0.0.1", port=port)
    else:
        mcp.run()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Repo Finder — catalog-first local reuse layer for Next.js/React UI code."
        ),
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for HTTP transport (default: 8000)",
    )
    subparsers = parser.add_subparsers(dest="command")

    scout_parser = subparsers.add_parser("scout", help="Discover raw Next.js UI candidate repositories")
    scout_parser.add_argument("--domain", default="nextjs-ui", choices=["nextjs-ui"])
    scout_parser.add_argument("--limit", type=int, default=500)

    qualify_parser = subparsers.add_parser("qualify", help="Clone and qualify repositories into snapshots")
    qualify_parser.add_argument("--limit", type=int, default=100)

    evidence_parser = subparsers.add_parser("evidence", help="Create deterministic evidence assets")
    evidence_parser.add_argument("--capability", required=True)
    evidence_parser.add_argument("--limit", type=int, default=30)

    serve_parser = subparsers.add_parser("serve-mcp", help="Run the MCP server")
    serve_parser.add_argument("--transport", choices=["stdio", "http"], default=None)
    serve_parser.add_argument("--port", type=int, default=None)

    gc_parser = subparsers.add_parser("gc", help="Garbage-collect old local snapshots")
    gc_parser.add_argument("--keep-per-repo", type=int, default=2)

    args = parser.parse_args()

    if args.command in (None, "serve-mcp"):
        transport = args.transport
        port = args.port
        if args.command == "serve-mcp":
            transport = args.transport or parser.get_default("transport")
            port = args.port or parser.get_default("port")
        _run_mcp(str(transport), int(port))
        return

    if args.command == "scout":
        _require_github_token()
        from .pipeline import scout

        result = asyncio.run(scout(args.domain, args.limit))
        print(result)
        return

    if args.command == "qualify":
        _require_github_token()
        from .pipeline import qualify

        result = asyncio.run(qualify(args.limit))
        print(result)
        return

    if args.command == "evidence":
        from .evidence import run_evidence

        result = run_evidence(args.capability, args.limit)
        print(result)
        return

    if args.command == "gc":
        from .pipeline import gc

        result = gc(args.keep_per_repo)
        print(result)
        return


if __name__ == "__main__":
    main()
