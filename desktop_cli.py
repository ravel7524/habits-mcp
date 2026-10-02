"""Downloadable stdio MCP entry point; private phone access requires explicit flags."""

import argparse
import asyncio
import os
import sys
from pathlib import Path


def default_data_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Habits Desktop MCP"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Habits Desktop MCP"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "habits-desktop-mcp"


def main() -> None:
    parser = argparse.ArgumentParser(description="Local stdio MCP for academic proposals. Phone writes always require phone review and confirmation.")
    parser.add_argument("--phone-bind", help="Explicitly enable the TLS phone bridge on this private/loopback IP; no wildcard/public IPs.")
    parser.add_argument("--phone-bridge", action="store_true", help="Explicitly enable the TLS phone bridge on loopback (127.0.0.1 by default).")
    parser.add_argument("--phone-port", type=int, default=8766)
    parser.add_argument("--data-dir", type=Path, default=None, help="Dedicated private bridge state directory; no global client configuration is changed.")
    parser.add_argument("--version", action="version", version="habits-desktop-mcp 0.1.0")
    args = parser.parse_args()
    if not 1 <= args.phone_port <= 65535:
        parser.error("phone-port must be 1 through 65535")
    server = None
    try:
        bridge = None
        if args.phone_bind is not None or args.phone_bridge:
            from .desktop_bridge import DesktopBridge
            from .phone_http import PhoneBridgeServer
            bridge = DesktopBridge(args.data_dir or default_data_directory(), args.phone_bind or "127.0.0.1", args.phone_port)
            server = PhoneBridgeServer(bridge)
            server.start()
            print(f"Habits private phone bridge listening at {bridge.base_url}. MCP uses stdio; phone plans require review.", file=sys.stderr)
        from .mcp_server import run_stdio
        asyncio.run(run_stdio(bridge))
    except (ValueError, OSError, ModuleNotFoundError) as error:
        parser.exit(2, f"Habits MCP startup failed: {error}\n")
    finally:
        if server is not None:
            server.close()


if __name__ == "__main__":
    main()
