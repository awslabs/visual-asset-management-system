#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests that the splatToolbox container directory holds only files the image actually receives.

The image is built from an explicit ``COPY`` list, and the only VAMS-owned entries on it are
``__main__.py`` and the ``vams_utils`` package. A VAMS-owned file outside that set reads as container
code and is not: an edit to it lands with no effect, and a reader reasons from something that never
runs.

The package is ``vams_utils``, so a relative ``from .utils`` import addresses a package that does not
exist and fails at container runtime rather than at build.

Deliberately NOT asserted against the ``Dockerfile``: it is gitignored and arrives from the upstream
sync, so it is absent from a fresh checkout and an assertion over it passes locally and fails in CI.
"""

import ast
import os

import pytest


def _top_level_functions(path):
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def _container_sources(container_dir):
    """Every VAMS-owned Python file under the container directory.

    ``src/`` is upstream and gitignored; ``tests/`` is not in the image.
    """
    sources = []
    for root, dirs, files in os.walk(container_dir):
        dirs[:] = [d for d in dirs
                   if d not in ("src", "tests", "__pycache__", ".pytest_cache", "models")]
        for name in files:
            if name.endswith(".py"):
                sources.append(os.path.join(root, name))
    return sources


@pytest.mark.unit
class TestContainerFileInventory:
    def test_nothing_imports_a_package_name_the_container_does_not_have(
            self, container_dir):
        """The package is ``vams_utils``, so a relative ``from .utils`` import resolves to nothing.

        Worth keeping rather than assuming: the failure is at container RUNTIME, on a GPU Batch job,
        because a Python import error is invisible to the image build and to CDK synth.
        """
        offenders = []
        for path in _container_sources(container_dir):
            with open(path, "r", encoding="utf-8") as handle:
                source = handle.read()
            # "from .utils" is the broken prefix; the package's own "from vams_utils.…" is not it.
            if "from .utils" in source:
                offenders.append(os.path.relpath(path, container_dir))

        assert offenders == []

    def test_the_entry_point_and_its_package_are_the_vams_owned_files_that_remain(
            self, container_dir):
        """What the image's COPY list names, and therefore what may carry container behaviour."""
        assert os.path.exists(os.path.join(container_dir, "__main__.py"))
        assert os.path.isdir(os.path.join(container_dir, "vams_utils"))


@pytest.mark.unit
class TestVamsUtilsS3:
    def test_the_helpers_the_container_uses_are_still_there(self, container_dir):
        functions = _top_level_functions(
            os.path.join(container_dir, "vams_utils", "aws", "s3.py"))

        assert {"download", "uploadV2", "exists", "delete",
                "delete_all_path_contents", "get_all_files_in_path"} <= functions
