#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Both stages of the 3D thumbnail image apply the Debian security updates in the layer that installs packages.

The base image is pinned by digest, so it carries the Debian packages as they were the day that digest
was built; without an upgrade every OS fix published since stays out of the image until someone moves
the pin, and the image shipped with Critical findings that had fixed versions available. AWS Batch on
AWS Fargate patches the host, not the image's own packages. The runtime stage is the image a job pulls;
the builder stage is held to the same rule because the compilers and the venv it produces run on its
packages.

The runtime image does not ship a package manager. npm installs splat-transform in the builder, and the
pipeline runs only ``node``, so npm, npx and corepack, which arrive with the Node.js release tarball, are
deleted in the install layer before the runtime stage copies ``/opt/node``.

Read from the Dockerfile itself, instruction by instruction, with continuation lines joined, and split
into stages at each FROM.
"""

import pathlib
import re

import pytest

DOCKERFILE = pathlib.Path(__file__).resolve().parents[1] / "Dockerfile"

_PINNED_BASE = re.compile(
    r"^FROM --platform=linux/amd64 public\.ecr\.aws/docker/library/python:3\.12-slim"
    r"@(sha256:[0-9a-f]{64})(?: AS \w+)?$"
)


def _instructions():
    joined, current = [], ""
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not current and (not line or line.startswith("#")):
            continue
        if current and line.startswith("#"):
            continue  # a comment inside a continued instruction
        current = f"{current} {line}" if current else line
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        joined.append(current)
        current = ""
    return joined


def _stages():
    stages = []
    for instruction in _instructions():
        if instruction.startswith("FROM "):
            stages.append([instruction])
        else:
            assert stages, f"instruction before the first FROM: {instruction}"
            stages[-1].append(instruction)
    return stages


def _package_layer(stage):
    layers = [i for i in stage if i.startswith("RUN ") and "apt-get install" in i]
    assert len(layers) == 1, (stage[0], layers)
    return layers[0]


def _is_root_user(instruction):
    """Whether a ``USER`` instruction runs as root: its user part, before any ``:group``, is root or 0."""
    user = instruction[len("USER "):].strip().split(":", 1)[0]
    return user in ("root", "0")


@pytest.mark.unit
class TestTheImageTakesDebianSecurityUpdates:
    def test_the_dockerfile_keeps_its_builder_and_runtime_stages(self):
        # Control: a parse that saw one stage would let the per-stage checks below skip the builder.
        stages = _stages()
        assert len(stages) == 2, [s[0] for s in stages]
        assert stages[0][0].endswith(" AS builder"), stages[0][0]

    @pytest.mark.parametrize("stage_index", [0, 1], ids=["builder", "runtime"])
    def test_each_package_layer_upgrades_before_it_installs(self, stage_index):
        layer = _package_layer(_stages()[stage_index])
        update, upgrade, install = (layer.find(s) for s in
                                    ("apt-get update", "apt-get upgrade -y", "apt-get install"))
        assert 0 <= update < upgrade < install, layer

    @pytest.mark.parametrize("stage_index", [0, 1], ids=["builder", "runtime"])
    def test_each_package_layer_installs_without_recommends_and_removes_the_lists(self, stage_index):
        layer = _package_layer(_stages()[stage_index])
        assert "apt-get install -y --no-install-recommends" in layer, layer
        assert layer.endswith("rm -rf /var/lib/apt/lists/*"), layer

    def test_both_stages_pin_the_same_base_by_digest(self):
        digests = []
        for stage in _stages():
            match = _PINNED_BASE.match(stage[0])
            assert match, stage[0]
            digests.append(match.group(1))
        assert len(set(digests)) == 1, digests

    def test_the_runtime_stage_still_drops_to_a_non_root_user(self):
        users = [i for i in _stages()[-1] if i.startswith("USER ")]
        assert users and not _is_root_user(users[-1]), users

    @pytest.mark.parametrize("instruction, root", [
        ("USER root", True), ("USER 0", True), ("USER root:root", True), ("USER 0:0", True),
        ("USER 0:10001", True), ("USER root:appuser", True),
        ("USER appuser", False), ("USER 10001", False), ("USER appuser:appuser", False),
        ("USER 10001:10001", False),
    ])
    def test_the_user_check_reads_the_user_before_any_group(self, instruction, root):
        # Control for the check above: a `user:group` form names root by its user part.
        assert _is_root_user(instruction) is root


# What the Node.js 22 linux-x64 tarball installs besides `node` itself.
_PACKAGE_MANAGER = {
    "/opt/node/bin/npm",
    "/opt/node/bin/npx",
    "/opt/node/bin/corepack",
    "/opt/node/lib/node_modules/npm",
    "/opt/node/lib/node_modules/corepack",
}
_NPM_CI = re.compile(r"\bnpm ci\b")
_PACKAGE_MANAGER_WORD = re.compile(r"\b(?:npm|npx|corepack)\b")


def _steps(instruction):
    return [step.strip() for step in instruction[len("RUN "):].split("&&")]


def _removed_paths(step):
    return set(step.split()[2:]) if step.startswith("rm -rf ") else set()


@pytest.mark.unit
class TestTheRuntimeImageShipsNoPackageManager:
    def _builder_install_layer(self):
        builder = _stages()[0]
        layers = [n for n, i in enumerate(builder) if i.startswith("RUN ") and _NPM_CI.search(i)]
        assert len(layers) == 1, layers
        return builder, layers[0]

    def test_the_install_layer_removes_npm_npx_and_corepack_after_it_has_used_them(self):
        builder, index = self._builder_install_layer()
        steps = _steps(builder[index])
        install = next(n for n, s in enumerate(steps) if _NPM_CI.search(s))
        version_check = next(n for n, s in enumerate(steps) if "cli.mjs --version" in s)
        after = steps[max(install, version_check) + 1:]
        removed = set().union(*(_removed_paths(s) for s in after))
        assert _PACKAGE_MANAGER <= removed, sorted(_PACKAGE_MANAGER - removed)
        assert {p for p in removed if p.startswith("/opt/node")} == _PACKAGE_MANAGER, sorted(removed)
        removal = next(n for n, s in enumerate(after) if _removed_paths(s) & _PACKAGE_MANAGER)
        assert not any(_PACKAGE_MANAGER_WORD.search(s) for s in after[removal + 1:]), after

    def test_nothing_later_in_the_builder_puts_a_package_manager_back(self):
        builder, index = self._builder_install_layer()
        later = builder[index + 1:]
        assert not any("/opt/node" in i or _PACKAGE_MANAGER_WORD.search(i) for i in later), later

    def test_the_runtime_stage_takes_node_only_from_the_builder(self):
        runtime = _stages()[-1]
        copies = [i for i in runtime if i.startswith("COPY ") and "/opt/node" in i]
        assert copies == ["COPY --from=builder /opt/node /opt/node"], copies
        assert not any(_PACKAGE_MANAGER_WORD.search(i) for i in runtime), [
            i for i in runtime if _PACKAGE_MANAGER_WORD.search(i)]
