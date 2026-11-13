"""The ``vams-mcp`` command.

``vams-mcp`` runs the server (over stdio, or remote with ``VAMS_MCP_TRANSPORT=streamable-http``).
``vams-mcp setup-remote ...`` writes a remote server's auth configuration and prints what a remote MCP
client needs to connect (``setup_remote.py``); it does not start the server or import it.

Over stdio nothing may be printed to stdout (the JSON-RPC channel), so every message here goes to
stderr. Any other argument is ignored, as before this dispatcher existed, so a host that passes one
keeps working.
"""

from __future__ import annotations

import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> None:
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] == "setup-remote":
        from .setup_remote import main as setup_main

        sys.exit(setup_main(args[1:]))

    from .config import ConfigError

    try:
        from .server import main as server_main

        # A configuration the server cannot run with stops it before anything listens: one read at
        # import (for example a remote server without its auth configuration) or at start (a bind
        # beyond this machine without an https resource URL).
        server_main()
    except ConfigError as exc:
        print(f"[vams-mcp] cannot start: {exc}", file=sys.stderr)
        sys.exit(2)
