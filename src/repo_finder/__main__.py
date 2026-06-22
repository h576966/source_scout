import argparse
import asyncio
import json
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

    eval_parser = subparsers.add_parser("eval", help="Run a local golden eval suite")
    eval_parser.add_argument("--suite", default="ui-reuse")
    eval_parser.add_argument("--top-k", type=int, default=5)
    eval_parser.add_argument("--label", default=None)
    eval_parser.add_argument("--output", default=None)

    profile_parser = subparsers.add_parser(
        "profile",
        help="Profile repository cards with Gemma via LM Studio",
    )
    profile_parser.add_argument("--limit", type=int, default=30)
    profile_parser.add_argument("--force", action="store_true")

    lmstudio_parser = subparsers.add_parser("lmstudio-status", help="Check local LM Studio connectivity")
    lmstudio_parser.add_argument("--start-server", action="store_true")
    lmstudio_parser.add_argument("--smoke-test", action="store_true")

    fastcontext_parser = subparsers.add_parser(
        "fastcontext-status",
        help="Check local FastContext model connectivity through LM Studio",
    )
    fastcontext_parser.add_argument("--start-server", action="store_true")
    fastcontext_parser.add_argument("--smoke-test", action="store_true")

    refine_parser = subparsers.add_parser(
        "refine-evidence",
        help="Use FastContext to refine evidence for a catalog candidate",
    )
    refine_parser.add_argument("--candidate-id")
    refine_parser.add_argument("--task")
    refine_parser.add_argument("--suite")
    refine_parser.add_argument("--top-k", type=int, default=3)
    refine_parser.add_argument("--label", default=None)
    refine_parser.add_argument("--output", default=None)
    refine_parser.add_argument("--limit-tasks", type=int, default=None)
    refine_parser.add_argument("--max-turns", type=int, default=6)

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

    if args.command == "eval":
        from pathlib import Path

        from .eval_runner import run_eval

        output_path = Path(args.output) if args.output else None
        result = run_eval(args.suite, args.top_k, label=args.label, output_path=output_path)
        summary = {
            "suite_id": result["suite_id"],
            "label": result["label"],
            "passed": result["passed"],
            "metrics": result["metrics"],
            "report_path": result["report_path"],
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    if args.command == "profile":
        from .profiler import profile_repository_cards

        result = asyncio.run(profile_repository_cards(args.limit, force=args.force))
        print(result)
        return

    if args.command == "lmstudio-status":
        status_result = asyncio.run(_lmstudio_status(args.start_server, args.smoke_test))
        print(json.dumps(status_result, indent=2, sort_keys=True))
        return

    if args.command == "fastcontext-status":
        status_result = asyncio.run(_fastcontext_status(args.start_server, args.smoke_test))
        print(json.dumps(status_result, indent=2, sort_keys=True))
        return

    if args.command == "refine-evidence":
        from pathlib import Path

        from . import fastcontext

        if args.suite:
            if args.candidate_id or args.task:
                refine_parser.error("--suite cannot be combined with --candidate-id or --task.")
            output_path = Path(args.output) if args.output else None
            result = asyncio.run(
                fastcontext.refine_suite(
                    suite=args.suite,
                    top_k=args.top_k,
                    label=args.label,
                    output_path=output_path,
                    max_turns=args.max_turns,
                    limit_tasks=args.limit_tasks,
                )
            )
            summary = {
                "suite_id": result["suite_id"],
                "label": result["label"],
                "metrics": result["metrics"],
                "scoring_recommendation": result["scoring_recommendation"],
                "report_path": result["report_path"],
            }
            print(json.dumps(summary, indent=2, sort_keys=True))
            return
        if not args.candidate_id or not args.task:
            refine_parser.error("--candidate-id and --task are required unless --suite is used.")
        result = asyncio.run(
            fastcontext.refine_candidate(
                candidate_id=args.candidate_id,
                task=args.task,
                max_turns=args.max_turns,
            )
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    if args.command == "gc":
        from .pipeline import gc

        result = gc(args.keep_per_repo)
        print(result)
        return


async def _lmstudio_status(start_server: bool, smoke_test: bool) -> dict[str, object]:
    from . import lmstudio

    config = lmstudio.get_config()
    started = False
    try:
        status = await lmstudio.validate_models(config)
    except lmstudio.LMStudioError as exc:
        if not start_server:
            return {
                "base_url": config.base_url,
                "reachable": False,
                "error": str(exc),
                "hint": "Run repo-finder lmstudio-status --start-server",
            }
        lmstudio.start_server()
        started = True
        await asyncio.sleep(1)
        status = await lmstudio.validate_models(config)

    result: dict[str, object] = {"reachable": True, "started_server": started, **status}
    if smoke_test:
        try:
            result["smoke_test"] = await lmstudio.chat_json(
                model_id=config.gemma_model,
                messages=[
                    {"role": "system", "content": "Return only valid JSON."},
                    {"role": "user", "content": 'Return exactly {"ok": true}.'},
                ],
                config=config,
                max_tokens=100,
            )
        except lmstudio.LMStudioError as exc:
            result["smoke_test"] = {"ok": False, "error": str(exc)}
    return result


async def _fastcontext_status(start_server: bool, smoke_test: bool) -> dict[str, object]:
    from . import fastcontext, lmstudio

    config = lmstudio.get_config()
    started = False
    try:
        status = await lmstudio.validate_models(config)
    except lmstudio.LMStudioError as exc:
        if not start_server:
            return {
                "base_url": config.base_url,
                "reachable": False,
                "error": str(exc),
                "hint": "Run repo-finder fastcontext-status --start-server",
            }
        lmstudio.start_server()
        started = True
        await asyncio.sleep(1)
        status = await lmstudio.validate_models(config)

    result: dict[str, object] = {"reachable": True, "started_server": started, **status}
    if smoke_test:
        try:
            result["smoke_test"] = await fastcontext.smoke_test(config)
        except (fastcontext.FastContextError, lmstudio.LMStudioError) as exc:
            result["smoke_test"] = {"ok": False, "error": str(exc)}
    return result


if __name__ == "__main__":
    main()
