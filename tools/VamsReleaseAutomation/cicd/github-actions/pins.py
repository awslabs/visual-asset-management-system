#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Action pins of the vra GitHub Actions workflows (action-pins.json is the single list).

  pins.py check [FILE ...]          every `uses:` is a local path or owner/repo[/path]@<40-hex SHA> # <tag> listed
                                    in action-pins.json (default: every *.yml / action.yml in this directory)
  pins.py ghes SRC_DIR OUT_DIR      copy the workflows for GitHub Enterprise Server: actions with a `ghes` pin
                                    (upload-artifact / download-artifact v4+ do not run on GHES) are rewritten to it,
                                    and the inputs the GHES versions do not have are dropped

Python standard library only. Exit 1 lists every problem.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
USES = re.compile(r"^(?P<indent>\s*-?\s*)uses:\s*(?P<ref>\S+)(?:\s+#\s*(?P<comment>.*))?$")
PINNED = re.compile(r"^(?P<action>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?P<path>/[^@]*)?@(?P<sha>[0-9a-f]{40})$")
# inputs of the github.com versions that the GHES pins do not know
GHES_DROP_INPUTS = {"actions/upload-artifact": ("include-hidden-files",)}


def load_pins(path: Path = HERE / "action-pins.json") -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["actions"]


def workflow_files(base: Path = HERE) -> list[Path]:
    return sorted(p for p in base.rglob("*") if p.is_file() and p.suffix in (".yml", ".yaml"))


def check_text(text: str, pins: dict, name: str = "") -> list[str]:
    problems = []
    for n, line in enumerate(text.splitlines(), 1):
        m = USES.match(line)
        if not m:
            continue
        ref = m.group("ref").strip("'\"")
        if ref.startswith("./"):
            continue  # an action or reusable workflow of the same checkout
        p = PINNED.match(ref)
        if not p:
            problems.append(f"{name}:{n}: {ref} is not pinned to a full commit SHA")
            continue
        pin = pins.get(p.group("action"))
        if pin is None:
            problems.append(f"{name}:{n}: {p.group('action')} is not in action-pins.json")
            continue
        allowed = {pin["sha"]: pin["tag"]}
        if "ghes" in pin:
            allowed[pin["ghes"]["sha"]] = pin["ghes"]["tag"]
        if p.group("sha") not in allowed:
            problems.append(f"{name}:{n}: {p.group('action')}@{p.group('sha')} differs from action-pins.json "
                            f"({pin['sha']} {pin['tag']})")
            continue
        tag = allowed[p.group("sha")]
        if not (m.group("comment") or "").startswith(tag):
            problems.append(f"{name}:{n}: {p.group('action')} pin comment should start with {tag!r}")
    return problems


def ghes_text(text: str, pins: dict) -> str:
    out: list[str] = []
    drop: tuple[str, ...] = ()
    step_indent = -1
    for line in text.splitlines():
        m = USES.match(line)
        if m:
            p = PINNED.match(m.group("ref").strip("'\""))
            drop, step_indent = (), -1
            if p and "ghes" in pins.get(p.group("action"), {}):
                g = pins[p.group("action")]["ghes"]
                line = f"{m.group('indent')}uses: {p.group('action')}{p.group('path') or ''}@{g['sha']} # {g['tag']}"
                drop = GHES_DROP_INPUTS.get(p.group("action"), ())
                step_indent = len(m.group("indent").replace("-", " "))
        elif drop:
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if stripped.startswith("- ") or (stripped and indent < step_indent):
                drop, step_indent = (), -1  # next step / end of this step
            elif any(stripped.startswith(f"{k}:") for k in drop):
                continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ("check", "ghes"):
        print(__doc__, file=sys.stderr)
        return 2
    pins = load_pins()
    if args[0] == "check":
        files = [Path(a) for a in args[1:]] or workflow_files()
        problems = [p for f in files for p in check_text(f.read_text(encoding="utf-8"), pins, str(f))]
        for p in problems:
            print(f"pins: {p}", file=sys.stderr)
        print(f"pins: {len(files)} file(s), {len(problems)} problem(s)", file=sys.stderr)
        return 1 if problems else 0
    if len(args) != 3:
        print("usage: pins.py ghes SRC_DIR OUT_DIR", file=sys.stderr)
        return 2
    src, dst = Path(args[1]), Path(args[2])
    for f in workflow_files(src):
        target = dst / f.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(ghes_text(f.read_text(encoding="utf-8"), pins), encoding="utf-8")
        print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
