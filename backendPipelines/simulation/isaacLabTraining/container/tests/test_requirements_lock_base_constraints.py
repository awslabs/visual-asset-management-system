#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The hashed lock leaves the Isaac Lab base image's packages alone (issue #1669, finding R5-1).

``requirements.lock`` is resolved against ``base-constraints.txt``, the frozen package set of the
digest-pinned base image, so a build adds only what the base lacks and never changes a package NVIDIA
ships (numpy, pillow and others).
"""

import os
import re

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)", re.MULTILINE)


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _read(name):
    with open(os.path.join(_CONTAINER_DIR, name), encoding="utf-8") as handle:
        return handle.read()


def _pins(text):
    return {_norm(name): version for name, version in _PIN.findall(text)}


def test_lock_does_not_change_a_package_the_base_image_ships():
    constraints = _pins(_read("base-constraints.txt"))
    lock = _pins(_read("requirements.lock"))
    assert constraints and lock
    changed = {
        name: (constraints[name], version)
        for name, version in lock.items()
        if name in constraints and constraints[name] != version
    }
    assert not changed, f"lock pins differ from the base image (base, lock): {changed}"


def test_lock_adds_the_direct_dependencies():
    lock = _pins(_read("requirements.lock"))
    for name in _pins(_read("requirements.txt")):
        assert name in lock


def test_constraints_header_digest_matches_the_dockerfile():
    dockerfile = _read("Dockerfile")
    digest = re.search(r"^ARG ISAAC_LAB_DIGEST=(sha256:[0-9a-f]{64})$", dockerfile, re.MULTILINE).group(1)
    header = _read("base-constraints.txt").split("\n\n")[0]
    assert f"Base image digest: {digest}" in header
    # The regeneration command must pull the base by digest: a re-pointed tag would freeze a different
    # image than the header names.
    command = re.search(r"^# Command: (.+)$", header, re.MULTILINE).group(1)
    assert f"@{digest}" in command
