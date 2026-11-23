#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fail the image build when an upstream requirements file differs from the copy the image's lock was resolved from.

The lock of an image is resolved from a copy of the upstream repository's requirement files, recorded under
locks/<model>/upstream/ at the same relative path plus the suffix `.recorded` (so a dependency scanner does not
mistake the copy for an install file of this repository). Right after the clone this script compares each
recorded copy with the file in the clone at the pinned commit. A moved commit, a hand-edited copy or a changed upstream
file stops the build before the image is finished, instead of shipping a lock that no longer describes what the
upstream repository asks for.

Usage: check_upstream_requirements.py --recorded <recorded directory> --clone <clone directory> <relative path>...
Line endings are ignored. Standard library only.
"""

import argparse
import sys


def read(path, what):
    try:
        with open(path, "rb") as handle:
            return handle.read().replace(b"\r\n", b"\n")
    except OSError as exc:
        raise SystemExit(f"cannot read {what} {path}: {exc.strerror or exc}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--recorded", required=True, help="directory holding the recorded copies")
    parser.add_argument("--clone", required=True, help="the upstream clone at its pinned commit")
    parser.add_argument("paths", nargs="+", help="requirement files, relative to both directories")
    args = parser.parse_args()

    problems = []
    for relative in args.paths:
        recorded = read(f"{args.recorded}/{relative}.recorded", "the recorded copy")
        cloned = read(f"{args.clone}/{relative}", "the upstream file")
        if recorded != cloned:
            have = set(cloned.decode("utf-8", "replace").splitlines())
            want = set(recorded.decode("utf-8", "replace").splitlines())
            added = sorted(have - want)
            removed = sorted(want - have)
            detail = "".join(f"\n    upstream has: {line}" for line in added) + "".join(
                f"\n    the copy has: {line}" for line in removed
            )
            problems.append(f"{relative} differs from the recorded copy{detail or ' (same lines, different order)'}")
    if problems:
        print("An upstream requirements file differs from the copy this image's lock was resolved from:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "Copy the new file into the image's upstream/ directory, update the matching lines of its pyproject.toml, "
            "run `uv lock` and re-export the lock files (locks/tools/relock.md).",
            file=sys.stderr,
        )
        return 1
    print(f"{len(args.paths)} upstream requirements file(s) match the recorded copies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
