import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "GitHub Project Finder MCP Server — "
            "Find, inspect, compare, and extract patterns from GitHub repos."
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
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ERROR: GITHUB_TOKEN environment variable is required.", file=sys.stderr)
        print("Set it via: set GITHUB_TOKEN=ghp_your_token  (Windows)", file=sys.stderr)
        print("         or: export GITHUB_TOKEN=ghp_your_token  (Unix)", file=sys.stderr)
        sys.exit(1)

    from .server import mcp

    if args.transport == "http":
        print(f"Starting MCP server on http://127.0.0.1:{args.port}/mcp")
        mcp.run(transport="http", host="127.0.0.1", port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
