#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Write an image's frozen-set.txt into the constraint-dependencies of its pyproject.toml.

Each image project under locks/<model>/ holds the baseline set it locks twice: as frozen-set.txt (the
`pip freeze --all` of the image as it was built before the lock existed) and as the `constraint-dependencies`
of pyproject.toml, so `uv lock` resolves every package to the version the image already ships. The two copies
are kept equal by this script; infra/test/pipelines/imageTo3dLocks.test.ts fails when they differ.

The block it rewrites sits between the two marker comments in pyproject.toml:

    # BEGIN frozen-set constraints (locks/tools/sync_constraints.py)
    # END frozen-set constraints

Usage: sync_constraints.py <locks/<model> directory> [--check]
  --check   change nothing, exit 1 when pyproject.toml is not in step with frozen-set.txt
Standard library only.
"""

import json
import os
import re
import sys

BEGIN = "# BEGIN frozen-set constraints (locks/tools/sync_constraints.py)"
END = "# END frozen-set constraints"
FROZEN_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9._+!-]*)$")


def read_frozen(path):
    """The (name, version) pairs of a frozen-set.txt, in file order. Anything else than a `name==version`
    line, a blank line or a `#` comment fails: a `name @ path` line means the baseline holds a package
    built from source, which belongs in out-of-lock.json instead."""
    pairs = []
    with open(path, encoding="utf-8") as handle:
        for number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = FROZEN_LINE.match(line)
            if not match:
                raise SystemExit(f"{path}:{number}: not a `name==version` line: {line!r}")
            pairs.append((match.group(1), match.group(2)))
    return pairs


def block(pairs):
    body = "".join(f"    {json.dumps(f'{name}=={version}')},\n" for name, version in pairs)
    return f"{BEGIN}\nconstraint-dependencies = [\n{body}]\n{END}"


def main():
    args = [a for a in sys.argv[1:] if a != "--check"]
    check = "--check" in sys.argv[1:]
    if len(args) != 1:
        raise SystemExit(__doc__)
    directory = args[0]
    frozen = os.path.join(directory, "frozen-set.txt")
    project = os.path.join(directory, "pyproject.toml")
    with open(project, encoding="utf-8") as handle:
        text = handle.read()
    if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(BEGIN) > text.index(END):
        raise SystemExit(f"{project} needs exactly one BEGIN and one END marker comment, in that order")
    start = text.index(BEGIN)
    stop = text.index(END) + len(END)
    wanted = text[:start] + block(read_frozen(frozen)) + text[stop:]
    if wanted == text:
        print(f"{project}: constraints match {frozen}")
        return 0
    if check:
        print(f"{project}: constraints differ from {frozen}; run sync_constraints.py {directory}", file=sys.stderr)
        return 1
    with open(project, "w", encoding="utf-8") as handle:
        handle.write(wanted)
    print(f"{project}: constraints rewritten from {frozen}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
