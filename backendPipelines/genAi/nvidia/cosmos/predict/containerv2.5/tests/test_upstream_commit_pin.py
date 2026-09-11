#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The upstream framework this image is built on is pinned to a commit, and the build proves it landed.

The Dockerfile clones `nvidia-cosmos/cosmos-predict2.5` at build time. With no ref, the framework
version becomes a function of WHEN the image was built rather than of what VAMS commit built it. Three
things in this container depend on that revision being fixed, which is more than reproducibility:

*   `uv sync --locked` resolves against the lockfile AT the cloned commit, so the whole Python
    environment moves with it. The Dockerfile already records one instance of that biting -- upstream
    switched its `.python-version` to 3.13 and the flash-attn wheel stopped resolving.
*   `entrypoint.sh` patches `checkpoint_db.py` by matching an EXACT upstream source line, and when the
    pattern is not found it logs "already patched or pattern changed -- skipping" and carries on. An
    upstream edit to that line turns the mitigation off silently.
*   The container looks for the generated video by the name the framework gives it, so an upstream
    rename sends every run down the fallback path in `find_output_video`.

Three parts, each inert without the others:

*   **a pinned default** -- a full 40-character commit id, so an unattended build is reproducible;
*   **a verified checkout** -- `git rev-parse HEAD` compared against the argument, which is what makes
    a branch or tag name passed to `--build-arg` fail the build instead of silently tracking a moving
    ref (`git checkout --detach main` succeeds);
*   **recorded provenance** -- the resolved commit written into the image and echoed by the
    entrypoint, so a run's own log names the code it ran and the next re-pin is decidable rather than
    archaeological.

Not addressed here, and not addressable by a pin: this image cannot be built where the build host has
no internet egress. The clone, the base image, the AWS CLI installer and `uv sync` all reach out, so a
restricted partition needs a mirrored build regardless.
"""

import os
import re

import pytest

from conftest import CONTAINER_DIR

COMMIT_ARG = "COSMOS_PREDICT_COMMIT"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def dockerfile_text():
    with open(os.path.join(CONTAINER_DIR, "Dockerfile"), encoding="utf-8") as handle:
        return handle.read()


def entrypoint_text():
    with open(os.path.join(CONTAINER_DIR, "entrypoint.sh"), encoding="utf-8") as handle:
        return handle.read()


def logical_lines(text):
    """The Dockerfile's instructions with backslash continuations joined, so a multi-line RUN reads as
    one instruction -- which is what matters, since a checkout in a LATER RUN would not be in the same
    layer as its clone."""
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    return [line.strip() for line in joined.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def arg_defaults(text):
    defaults = {}
    for line in logical_lines(text):
        match = re.match(r"ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if match:
            defaults[match.group(1)] = match.group(2).strip()
    return defaults


def clone_instructions(text):
    return [line for line in logical_lines(text) if "git clone" in line]


# ============================ the pin ============================

def test_the_dockerfile_still_clones_the_framework():
    """Control for every assertion below: they are all about a clone, and would pass vacuously on a
    Dockerfile that had stopped cloning anything."""
    assert clone_instructions(dockerfile_text()), "no git clone found; the checks below prove nothing"


def test_the_pinned_commit_default_is_a_full_commit_id():
    default = arg_defaults(dockerfile_text()).get(COMMIT_ARG)
    assert default is not None, f"no ARG {COMMIT_ARG} default; an unattended build is not reproducible"
    assert FULL_SHA.match(default), (
        f"{COMMIT_ARG} default {default!r} is not a full 40-character commit id. A branch or tag moves, "
        "and an abbreviated id fails the rev-parse comparison below.")


@pytest.mark.parametrize("instruction", clone_instructions(dockerfile_text()))
def test_every_clone_checks_out_the_pinned_commit(instruction):
    assert f"git checkout --detach ${{{COMMIT_ARG}}}" in instruction, (
        "a clone with no checkout takes the upstream default branch as it stands at build time: "
        f"{instruction}")


@pytest.mark.parametrize("instruction", clone_instructions(dockerfile_text()))
def test_every_clone_verifies_the_checkout_landed(instruction):
    """`git checkout --detach main` succeeds, so without comparing HEAD to the argument a build arg
    naming a branch would report a pin while tracking a moving ref."""
    assert "git rev-parse HEAD" in instruction and f"${{{COMMIT_ARG}}}" in instruction, instruction
    assert re.search(r'test\s+"\$\(git rev-parse HEAD\)"\s*=\s*"\$\{%s\}"' % COMMIT_ARG,
                     instruction), instruction


def test_the_repository_url_is_a_build_argument_too():
    """So a deployment building from an internal mirror does not have to edit the Dockerfile."""
    assert "COSMOS_PREDICT_REPO" in arg_defaults(dockerfile_text())


def test_the_locked_dependency_sync_is_still_what_resolves_the_environment():
    """Names the coupling the pin protects: `--locked` reads the lockfile at the cloned commit, so an
    unpinned clone moves the whole Python environment. If this ever stops being `--locked`, the pin's
    justification changes and this test is where that shows."""
    assert "uv sync --locked" in dockerfile_text()


# ============================ recorded provenance ============================

def test_the_resolved_commit_is_written_into_the_image():
    assert "VAMS_UPSTREAM_COMMIT" in dockerfile_text(), (
        "without the commit recorded in the image, a running container cannot say which upstream "
        "code it carries")


def test_the_entrypoint_reports_the_commit_at_run_time():
    text = entrypoint_text()
    assert "VAMS_UPSTREAM_COMMIT" in text and "cosmos-predict2.5 commit" in text


def test_the_provenance_echo_does_not_fail_a_run_when_the_file_is_absent():
    """The entrypoint runs under `set -e`, so an unguarded `cat` of a missing file would kill every run
    on an image built before the file existed."""
    text = entrypoint_text()
    assert re.search(r"if \[ -f /opt/cosmos-predict2\.5/VAMS_UPSTREAM_COMMIT \]", text), text


def test_the_provenance_echo_precedes_the_upstream_source_patch():
    """The patch step edits upstream source by pattern match and reports 'pattern changed --
    skipping' when it misses. Logging the commit first is what makes that line diagnosable.

    Anchored on the echo statement and on the patch's own `CHECKPOINT_DB=` assignment rather than on
    the first mention of either. A comment naming the patch step sits above the echo, so a
    position test keyed on the bare word `checkpoint_db` measures the comment instead.
    """
    text = entrypoint_text()
    echo = 'echo "cosmos-predict2.5 commit'
    patch = "CHECKPOINT_DB="
    assert echo in text, "the entrypoint does not echo the upstream commit"
    assert patch in text, "the checkpoint_db patch step is gone; this test no longer has a subject"
    assert text.index(echo) < text.index(patch), (
        "the commit is logged after the patch step, so a skipped patch cannot be tied to a revision")
