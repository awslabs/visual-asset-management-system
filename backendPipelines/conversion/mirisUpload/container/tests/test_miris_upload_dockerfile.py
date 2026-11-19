#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The Miris upload image: digest-pinned, patched, hash-locked, non-root, world-readable source,
and shipping every module it imports (the Dockerfile COPYs an explicit file list)."""
import os
import re

import pytest

pytestmark = pytest.mark.unit

CONTAINER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DOCKERFILE = os.path.join(CONTAINER_DIR, "Dockerfile")


def _lines():
    with open(DOCKERFILE, encoding="utf-8") as handle:
        return [line.rstrip() for line in handle if line.strip() and not line.lstrip().startswith("#")]


def _copied_sources(lines):
    copied = set()
    for line in lines:
        if line.startswith("COPY "):
            copied.update(part.rstrip("/") for part in line.split()[1:-1])
    return copied


def test_the_base_image_is_pinned_by_digest():
    froms = [line for line in _lines() if line.startswith("FROM ")]
    assert len(froms) == 1, froms
    assert re.search(r"python:3\.12-slim@sha256:[0-9a-f]{64}$", froms[0]), froms[0]


def test_debian_security_updates_are_applied_and_the_lists_removed():
    runs = [line for line in _lines() if "apt-get" in line]
    joined = " ".join(_lines())
    assert runs, "no apt-get layer"
    assert "apt-get update && apt-get upgrade -y" in joined
    assert "rm -rf /var/lib/apt/lists/*" in joined


def test_every_copy_of_source_is_followed_by_a_chmod():
    lines = _lines()
    for index, line in enumerate(lines):
        if not line.startswith("COPY ") or "requirements.lock" in line:
            continue
        following = lines[index + 1]
        assert following.startswith("RUN chmod -R a+rX"), (line, following)


def test_dependencies_install_from_the_hash_locked_set():
    lines = _lines()
    joined = " ".join(lines)
    copy_at = next(i for i, line in enumerate(lines) if line.startswith("COPY ") and "requirements.lock" in line)
    install = lines[copy_at + 1] + " " + lines[copy_at + 2]
    assert install.startswith("RUN chmod a+r /build/requirements.lock")
    assert "pip install --no-cache-dir --require-hashes -r /build/requirements.lock" in install
    # Only the lock is installed: an unhashed requirements.txt install would bypass the hashes.
    assert "requirements.txt" not in joined
    with open(os.path.join(CONTAINER_DIR, "requirements.lock"), encoding="utf-8") as handle:
        lock = handle.read()
    assert "--hash=sha256:" in lock
    with open(os.path.join(CONTAINER_DIR, "requirements.txt"), encoding="utf-8") as handle:
        direct = [line.split("==")[0] for line in handle if line.strip() and not line.startswith("#")]
    assert direct, "control: no direct dependencies listed"
    for name in direct:
        assert re.search(rf"^{re.escape(name)}==\S+ \\$", lock, re.M), name


def test_the_image_runs_as_a_created_non_root_user():
    lines = _lines()
    user_at = next(i for i, line in enumerate(lines) if line.startswith("USER "))
    name = lines[user_at].split()[1]
    assert name not in ("root", "0")
    assert any("useradd" in line and name in line for line in lines[:user_at])
    last_copy = max(i for i, line in enumerate(lines) if line.startswith("COPY "))
    entrypoint = next(i for i, line in enumerate(lines) if line.startswith("ENTRYPOINT "))
    assert last_copy < user_at < entrypoint


def test_every_runtime_module_is_copied():
    copied = _copied_sources(_lines())
    expected = {name for name in os.listdir(CONTAINER_DIR)
                if name.endswith(".py") or name == "entrypoint.sh"}
    expected |= {name for name in os.listdir(CONTAINER_DIR)
                 if os.path.isfile(os.path.join(CONTAINER_DIR, name, "__init__.py"))}
    assert expected, "control: no container sources found"
    assert expected <= copied, sorted(expected - copied)
