#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check every `vra ...` invocation in the Markdown of the package (skill SOPs, adapters, READMEs) and
of the paths named with ``--with`` (the package's documentation pages) against the real argparse
parser: unknown subcommands or flags fail the check.

Usage: check_doc_commands.py <package-root> [--with <path> ...]
"""
from __future__ import annotations

import argparse
import re
import shlex
import sys
from pathlib import Path

from vra import cli

FLAG_RE = re.compile(r"^--?[A-Za-z][A-Za-z0-9-]*")


def parser_index(ap: argparse.ArgumentParser) -> dict[str, set[str]]:
    """{'deploy': {'--env', ...}, 'ledger upgrade-config': {...}, '': global flags}."""
    out: dict[str, set[str]] = {"": {o for a in ap._actions for o in a.option_strings}}

    def walk(p: argparse.ArgumentParser, prefix: str) -> None:
        for a in p._actions:
            if isinstance(a, argparse._SubParsersAction):
                for name, sp in a.choices.items():
                    key = f"{prefix} {name}".strip()
                    out[key] = {o for x in sp._actions for o in x.option_strings} | out[""]
                    walk(sp, key)
    walk(ap, "")
    return out


def invocations(text: str) -> list[str]:
    cands = re.findall(r"`(vra [^`]+)`", text)
    for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
        for line in block.splitlines():
            m = re.search(r"(?:^|[\s;&|(])(vra\s+[^#|;&)]+)", line)
            if m:
                cands.append(m.group(1))
    return cands


def markdown(base: Path) -> list[Path]:
    if base.is_file():
        return [base] if base.suffix == ".md" else []
    return [p for p in base.rglob("*.md") if "node_modules" not in p.parts and ".pytest_cache" not in p.parts]


def check(root: Path, extra: list[Path] | None = None) -> list[str]:
    idx = parser_index(cli.build_parser())
    problems = []
    repo = root.parent.parent
    files = {p.resolve() for base in [root, *(extra or [])] for p in markdown(base)}
    for f in sorted(files):
        for inv in invocations(f.read_text(encoding="utf-8")):
            inv = inv.replace("\\\n", " ").strip()
            if "-->" in inv or "\\|" in inv:
                continue  # mermaid edge labels, escaped table alternatives
            try:
                toks = shlex.split(inv, posix=True)
            except ValueError:
                toks = inv.split()
            toks = toks[1:]
            # skip global flags before the subcommand
            i = 0
            while i < len(toks) and toks[i].startswith("-"):
                if toks[i] in ("--ledger", "--work-dir", "--run-id"):
                    i += 1
                i += 1
            if i >= len(toks):
                continue
            cmd = toks[i]
            if not re.match(r"^[a-z][a-z-]*$", cmd) or cmd in ("is", "and", "or", "the", "does", "never", "can",
                                                                  "will", "itself", "only", "runs", "exits", "appends",
                                                                  "writes", "reads", "stops", "refuses"):
                continue  # prose such as "vra never deploys"
            key = cmd
            nested = f"{cmd} {toks[i + 1]}" if i + 1 < len(toks) and not toks[i + 1].startswith("-") else None
            if nested and nested in idx:  # command groups: ledger, history, home
                key, i = nested, i + 1
            elif nested and any(k.startswith(cmd + " ") for k in idx):
                problems.append(f"{rel(f, repo)}: unknown subcommand in `{inv}`")
                continue
            if key not in idx:
                if cmd in idx:
                    problems.append(f"{rel(f, repo)}: unknown subcommand in `{inv}`")
                elif cmd not in ("credential-process",):
                    problems.append(f"{rel(f, repo)}: unknown command {cmd!r} in `{inv}`")
                continue
            for t in toks[i + 1:]:
                m = FLAG_RE.match(t)
                if not m:
                    continue
                flag = m.group(0)
                if flag in ("-h",) or flag in idx[key]:
                    continue
                problems.append(f"{rel(f, repo)}: `vra {key}` has no flag {flag} (in `{inv}`)")
    return problems


def rel(p: Path, repo: Path) -> str:
    try:
        return p.relative_to(repo.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="check `vra ...` commands in Markdown against the CLI")
    ap.add_argument("root", type=Path)
    ap.add_argument("--with", dest="extra", action="append", type=Path, default=[])
    args = ap.parse_args()
    missing = [str(x) for x in args.extra if not x.exists()]
    if missing:
        print(f"--with path(s) not found: {', '.join(missing)}")
        sys.exit(1)
    probs = check(args.root.resolve(), [x.resolve() for x in args.extra])
    for p in probs:
        print(p)
    print(f"doc command check: {len(probs)} problem(s)")
    sys.exit(1 if probs else 0)
