#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fail the image build when the installed Python packages are not exactly the ones the image's lock names.

An image's Python set is its hashed lock files (requirements-build.lock, requirements.lock and, when the GPL
mesh-repair backends were requested, requirements-gpl.lock) plus the packages recorded in out-of-lock.json
(built from source at a pinned commit, or a prebuilt wheel checked by its SHA-256). This script reads the
packages the interpreter sees and fails on

  - a package the lock names that is not installed, or is installed at another version;
  - an installed package that neither the lock nor out-of-lock.json names;
  - a recorded out-of-lock package that is missing or at another version.

The lock files are pinned `name==version ; marker` lines, and `name @ <url of a .whl file> ; marker` lines for a
prebuilt wheel (the version is the one in the wheel's file name, without a local label such as `+cu12torch2.6`,
which is what the installed package's metadata carries); a line whose marker does not hold on this machine is
skipped. Only names and versions are printed.

Usage: check_installed_set.py --dir <locks/<model> directory in the image> [--gpl true|false]
Standard library plus `packaging`, which the locks install (it is a dependency of the build tools).
"""

import argparse
import importlib.metadata
import json
import os
import re
import sys

LOCK_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)\s*(?:;\s*(.*?))?\s*(?:\\)?$")
# A prebuilt wheel: `name @ https://.../<dist>-<version>-<tags>.whl ; marker`. Its file name carries the version.
WHEEL_LINE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s+@\s+https://[^\s;?#%]*/([A-Za-z0-9_.]+)-([0-9][A-Za-z0-9_.!+]*)-[^\s;/?#%]+\.whl"
    r"\s*(?:;\s*(.*?))?\s*(?:\\)?$"
)


def canonical(name):
    """PEP 503 normalised name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def evaluate(marker):
    """Whether an environment marker holds here. An empty marker holds."""
    if not marker:
        return True
    try:
        from packaging.markers import Marker
    except ImportError:
        raise SystemExit("cannot evaluate the lock's environment markers: `packaging` is not installed")
    return Marker(marker).evaluate()


def read_lock(path):
    """{canonical name: (name, version)} of the pins in one hashed export that apply to this machine."""
    pins = {}
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        raise SystemExit(f"cannot read the lock {path}: {exc.strerror or exc}")
    for number, raw in enumerate(lines, start=1):
        if not raw or raw[0] in " #\t":
            continue
        match = LOCK_LINE.match(raw)
        if match:
            name, version, marker = match.groups()
        else:
            wheel = WHEEL_LINE.match(raw)
            if not wheel or canonical(wheel.group(1)) != canonical(wheel.group(2)):
                raise SystemExit(f"{path}:{number}: not a pinned requirement: {raw[:80]!r}")
            name, version, marker = wheel.group(1), wheel.group(3).split("+", 1)[0], wheel.group(4)
        if evaluate(marker):
            pins[canonical(name)] = (name, version)
    return pins


def read_out_of_lock(path):
    """{canonical name: (name, version)} of the packages recorded as installed outside the lock."""
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read the record {path}: {getattr(exc, 'strerror', None) or exc}")
    packages = record.get("packages") if isinstance(record, dict) else None
    if not isinstance(packages, list):
        raise SystemExit(f"the record {path} has no `packages` list")
    pins = {}
    for entry in packages:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not isinstance(entry.get("version"), str):
            raise SystemExit(f"the record {path} has a package without a string `name` and `version`")
        pins[canonical(entry["name"])] = (entry["name"], entry["version"])
    return pins


def installed():
    """{canonical name: (name, version)} of every distribution the interpreter sees."""
    found = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            found[canonical(name)] = (name, dist.version)
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dir", required=True, help="the image's locks/<model> directory")
    parser.add_argument("--gpl", default="true", choices=["true", "false"], help="whether the GPL repair lock was installed")
    args = parser.parse_args()

    wanted = {}
    locks = ["requirements-build.lock", "requirements.lock"] + (["requirements-gpl.lock"] if args.gpl == "true" else [])
    for lock in locks:
        for key, pin in read_lock(os.path.join(args.dir, lock)).items():
            if key in wanted and wanted[key][1] != pin[1]:
                raise SystemExit(f"{lock} pins {pin[0]} {pin[1]}, another lock of this image pins {wanted[key][1]}")
            wanted[key] = pin
    outside = read_out_of_lock(os.path.join(args.dir, "out-of-lock.json"))
    for key in outside:
        if key in wanted:
            raise SystemExit(f"{outside[key][0]} is both in the lock and in out-of-lock.json")
    wanted.update(outside)

    have = installed()
    problems = []
    for key, (name, version) in sorted(wanted.items()):
        if key not in have:
            problems.append(f"missing: {name} {version}")
        elif have[key][1] != version:
            problems.append(f"different version: {name} is {have[key][1]}, the lock has {version}")
    for key, (name, version) in sorted(have.items()):
        if key not in wanted:
            problems.append(f"not in the lock: {name} {version}")
    if problems:
        print(f"The installed packages differ from the lock in {args.dir}:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print("Re-lock the image (locks/tools/relock.md) or remove the install that adds the package.", file=sys.stderr)
        return 1
    print(f"{args.dir}: {len(wanted)} packages installed, all as locked ({len(outside)} outside the lock)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
