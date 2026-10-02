"""Run with python -m habits_mcp [stdio|http|validate]."""

import argparse
import asyncio
import json
from pathlib import Path

from .tools import propose_academic_plan
from .validator import MAX_BYTES, PlanValidationError


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate academic proposals; never apply app changes.")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("stdio", help="Run the official MCP SDK over local stdio (default).")
    http = commands.add_parser("http", help="Run unauthenticated development HTTP on 127.0.0.1 only.")
    http.add_argument("--port", type=int, default=8765)
    validate = commands.add_parser("validate", help="Validate a UTF-8 JSON file without the MCP dependency.")
    validate.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            with args.path.open("rb") as handle:
                result = propose_academic_plan(handle.read(MAX_BYTES + 1))
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "http":
            if not 1 <= args.port <= 65535:
                parser.error("port must be 1 through 65535")
            from .mcp_server import run_http
            run_http(args.port)
        else:
            from .mcp_server import run_stdio
            asyncio.run(run_stdio())
    except ModuleNotFoundError as error:
        if error.name and (error.name == "mcp" or error.name.startswith("mcp.")):
            parser.exit(2, "Install the isolated dependencies from requirements.txt to run MCP transports.\n")
        raise
    except (PlanValidationError, OSError) as error:
        parser.exit(2, f"Proposal rejected: {error}. No changes were applied.\n")


if __name__ == "__main__":
    main()
