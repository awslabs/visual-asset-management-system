#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Write adapters/cursor/.cursor/cli.json from the Claude Code permission lists (one policy, two spellings).

  python3 adapters/cursor/gen_cli_json.py           # rewrite cli.json
  python3 adapters/cursor/gen_cli_json.py --check   # exit 1 when cli.json differs

Run it after changing adapters/claude/settings.json.example `permissions.allow` / `permissions.deny`. Claude's
`ask` list has no Cursor CLI equivalent: what is neither allowed nor denied prompts in an interactive session.
Python standard library only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[2]
CLAUDE = PKG / "adapters/claude/settings.json.example"
OUT = PKG / "adapters/cursor/.cursor/cli.json"


def cursor_rule(rule: str) -> str | None:
    """One Claude Code permission rule as a Cursor CLI permission token (None: Cursor has no equivalent)."""
    if rule.startswith("mcp__vra__"):
        return f"Mcp(vra:{rule.removeprefix('mcp__vra__')})"
    m = re.fullmatch(r"(Bash|Edit|Read)\((.*)\)", rule)
    if not m:
        return None  # Read / Grep / Glob without a path: Cursor allows reading by default
    kind, body = m.groups()
    if kind == "Edit":
        return f"Write({body})"
    if kind == "Read":
        return f"Read({body})"
    if body.endswith(":*"):
        cmd, _, args = body[:-2].partition(" ")
        return f"Shell({cmd}:{args}*)" if args else f"Shell({cmd})"
    cmd, _, args = body.partition(" ")
    return f"Shell({cmd}:{args})" if args else f"Shell({cmd})"


def cursor_rules(rules: list[str]) -> list[str]:
    out: list[str] = []
    for r in rules:
        c = cursor_rule(r)
        if c and c not in out:
            out.append(c)
    return out


def render() -> str:
    perms = json.loads(CLAUDE.read_text(encoding="utf-8"))["permissions"]
    doc = {"permissions": {"allow": cursor_rules(perms["allow"]), "deny": cursor_rules(perms["deny"])}}
    return json.dumps(doc, indent=4) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    text = render()
    if "--check" in args:
        if json.loads(OUT.read_text(encoding="utf-8")) != json.loads(text):
            print(f"{OUT} differs from the Claude Code lists; run {Path(__file__).name}", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
