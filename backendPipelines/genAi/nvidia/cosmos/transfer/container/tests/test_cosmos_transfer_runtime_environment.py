#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The Cosmos Transfer job runs with the NCCL wheel the image locks.

The image build syncs upstream's environment from upstream's uv.lock at the pinned commit, then installs
nvidia-nccl-cu12 from the hashed requirements-nccl.lock over it. Upstream's lock pins an older NCCL, so a
command that syncs or installs into that environment afterwards puts upstream's version back. That holds
for a later build step and for entrypoint.sh at job start. The entrypoint used to run
`uv sync --locked` at every start, with its errors discarded.
"""

import re
from pathlib import Path

CONTAINER_DIR = Path(__file__).resolve().parent.parent

# Commands that change an installed Python environment.
_ENV_CHANGE = re.compile(
    r"\buv\s+(?:sync|add|remove|pip\s+(?:install|sync|uninstall))\b"
    r"|\bpip3?\s+(?:install|uninstall)\b"
    r"|\bpython3?\s+-m\s+pip\s+(?:install|uninstall)\b"
)


def _commands(text):
    """The script's lines without comment lines, with backslash continuations joined."""
    joined = re.sub(r"\\\n", " ", text)
    return [line for line in joined.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def _dockerfile_runs():
    return [line for line in _commands((CONTAINER_DIR / "Dockerfile").read_text()) if line.lstrip().startswith("RUN ")]


def _nccl_install_index(runs):
    installs = [i for i, run in enumerate(runs) if "requirements-nccl.lock" in run]
    assert len(installs) == 1, f"expected one RUN that installs requirements-nccl.lock, found {len(installs)}"
    return installs[0]


def test_entrypoint_does_not_change_the_environment():
    changes = [line.strip() for line in _commands((CONTAINER_DIR / "entrypoint.sh").read_text())
               if _ENV_CHANGE.search(line)]
    assert changes == [], (
        "entrypoint.sh must not sync or install into the image's environment: upstream's lock would "
        f"replace the locked NCCL wheel at every job start. Found: {changes}")


def test_nccl_install_is_hashed_and_follows_upstream_sync():
    runs = _dockerfile_runs()
    index = _nccl_install_index(runs)
    install = runs[index]
    assert re.search(r"\buv\s+pip\s+install\b", install)
    assert "--require-hashes" in install and "--only-binary :all:" in install
    assert any(re.search(r"\buv\s+sync\b.*--locked", run) for run in runs[:index]), (
        "the NCCL install must come after upstream's `uv sync --locked`")


def test_no_build_step_changes_the_environment_after_the_nccl_install():
    runs = _dockerfile_runs()
    later = [run.strip() for run in runs[_nccl_install_index(runs) + 1:] if _ENV_CHANGE.search(run)]
    assert later == [], f"a build step after the NCCL install changes the environment: {later}"
