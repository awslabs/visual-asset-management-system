#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fail the image build when Isaac-GR00T's dependency lists differ from the copy this image locks.

uv.lock is resolved from a copy of Isaac-GR00T's `dependencies` and `base` extra (pyproject.toml here),
recorded in upstream-requirements.json. This script compares that record with the pyproject.toml of the
clone at STABLE_COMMIT, so a moved pin, a hand-edited copy or a changed upstream file stops the build
before anything is installed.

Usage: check_upstream_requirements.py <upstream pyproject.toml> <upstream-requirements.json>

Python 3.10 has no tomllib and no package is installed at this point of the build, so the two lists are
read with the standard library only: each is a TOML array of one quoted requirement per line, which is the
shape of the upstream file. A line this reader does not understand fails the check instead of being skipped.
"""

import json
import re
import sys

ITEM = re.compile(r'^"((?:[^"\\]|\\.)*)"\s*,?\s*(?:#.*)?$')


def read_list(lines, header, key):
    """The quoted items of `key = [` ... `]` inside the TOML table `[header]`."""
    in_table = False
    items = None
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if line.startswith("["):
            in_table = line == f"[{header}]"
            continue
        if items is None:
            if in_table and re.match(rf"^{re.escape(key)}\s*=\s*\[\s*$", line):
                items = []
            continue
        if line == "]":
            return items
        if not line or line.startswith("#"):
            continue
        match = ITEM.match(line)
        if not match:
            raise SystemExit(f"line {number} of the upstream pyproject.toml is not a quoted requirement: {line!r}")
        items.append(json.loads(f'"{match.group(1)}"'))
    raise SystemExit(f"`{key}` in [{header}] not found, or not closed, in the upstream pyproject.toml")


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    with open(sys.argv[1], encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    with open(sys.argv[2], encoding="utf-8") as handle:
        recorded = json.load(handle)
    upstream = {
        "dependencies": read_list(lines, "project", "dependencies"),
        "base": read_list(lines, "project.optional-dependencies", "base"),
    }
    problems = []
    for name in ("dependencies", "base"):
        want, have = recorded[name], upstream[name]
        for entry in have:
            if entry not in want:
                problems.append(f"{name}: upstream has {entry!r}, the copy does not")
        for entry in want:
            if entry not in have:
                problems.append(f"{name}: the copy has {entry!r}, upstream does not")
        if not problems and want != have:
            problems.append(f"{name}: same entries in a different order")
    if problems:
        print("Isaac-GR00T's dependency lists differ from the copy this image locks:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "Update upstream-requirements.json and the matching lines of pyproject.toml, run `uv lock`, "
            "and re-export requirements.lock and requirements-build.lock.",
            file=sys.stderr,
        )
        return 1
    print(
        f"Isaac-GR00T {recorded['commit']}: {len(upstream['dependencies'])} dependencies and "
        f"{len(upstream['base'])} base extra entries match the locked copy"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
