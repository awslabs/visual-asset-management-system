#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The container installs a hash-locked dependency set.

requirements.txt names the direct dependencies with lower bounds only, so installing it directly lets
two builds of the same commit resolve different releases. The image therefore installs
requirements.lock, generated from requirements.txt, with pip's --require-hashes: every package,
transitive ones included, is pinned to one version and its distribution hashes. These tests hold the
three halves together: the Dockerfile installs the lock in hash-checking mode, every entry of the lock
is pinned and hashed, and every direct dependency is present in the lock."""

import os
import re

import pytest

CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKERFILE = os.path.join(CONTAINER_DIR, "Dockerfile")
REQUIREMENTS = os.path.join(CONTAINER_DIR, "requirements.txt")
LOCK = os.path.join(CONTAINER_DIR, "requirements.lock")

_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _lock_entries():
    """{canonical name: (version, [hashes])} for every requirement in the lock."""
    with open(LOCK, encoding="utf-8") as handle:
        text = handle.read()
    entries = {}
    # Each requirement is one logical line continued with backslashes.
    for logical in re.split(r"(?<!\\)\n", text):
        line = logical.replace("\\\n", " ").strip()
        if not line or line.startswith("#"):
            continue
        requirement = line.split()[0]
        assert "==" in requirement, f"lock entry is not pinned to one version: {requirement}"
        name, version = requirement.split("==", 1)
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", line)
        entries[_canonical(name)] = (version, hashes)
    return entries


def _direct_dependencies():
    names = []
    with open(REQUIREMENTS, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            match = _NAME.match(line)
            assert match, f"unparseable requirement: {raw!r}"
            names.append(_canonical(match.group(1)))
    return names


@pytest.mark.unit
class TestDependencyLock:
    def test_the_image_installs_the_lock_in_hash_checking_mode(self):
        with open(DOCKERFILE, encoding="utf-8") as handle:
            instructions = [line for line in handle.read().splitlines()
                            if line.strip() and not line.lstrip().startswith("#")]
        installs = [line for line in instructions if re.search(r"\bpip3?\s+install\b", line)]
        assert installs, "the Dockerfile installs no Python packages"
        for line in installs:
            assert "--require-hashes" in line, f"pip install without hash checking: {line}"
            assert "requirements.lock" in line, f"pip install of something other than the lock: {line}"
        unpinned = [line for line in instructions if "requirements.txt" in line]
        assert unpinned == [], "the image must not copy or install the unpinned direct-dependency list"

    def test_every_lock_entry_is_pinned_and_hashed(self):
        entries = _lock_entries()
        assert len(entries) > 20, "the lock parsed to almost nothing; the parser is not reading it"
        unhashed = sorted(name for name, (_, hashes) in entries.items() if not hashes)
        assert unhashed == []

    def test_every_direct_dependency_is_locked(self):
        entries = _lock_entries()
        direct = _direct_dependencies()
        assert "pyvista" in direct and "usd-core" in direct, \
            "the direct-dependency parser is not reading requirements.txt"
        missing = sorted(name for name in direct if name not in entries)
        assert missing == []
