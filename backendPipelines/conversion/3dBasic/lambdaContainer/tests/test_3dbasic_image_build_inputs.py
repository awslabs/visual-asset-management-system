#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""What goes into the 3D Basic Conversion image (#506, #510).

- ``requirements.txt`` is the export of ``poetry.lock`` (backend ``CLAUDE.md``, "Updating Python
  Dependencies"): the Dockerfile installs the requirements file, so a hand edit that disagrees with the
  lock ships one version and is silently reverted by the next export. Both files are compared package
  by package.
- Pillow is installed. trimesh decodes and encodes texture images through it; without it textures are
  dropped from every conversion and an OBJ with texture coordinates fails to load (#506).
- The base image is pinned by digest, so two builds of one commit start from the same image.
- ``.dockerignore`` keeps the tests, the Poetry files and local caches out of ``COPY . .`` while every
  runtime module still enters the image.
"""

import fnmatch
import os
import re
import tomllib

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The modules lambda.py imports from the build context. A runtime module the ignore file excluded would
# fail at container RUNTIME, not at the image build.
_RUNTIME_PATHS = ("lambda.py", "referenced_files.py", "common/logger.py", "requirements.txt")


def _read(name):
    with open(os.path.join(_LAMBDA_DIR, name), encoding="utf-8") as handle:
        return handle.read()


def _requirements():
    pins = {}
    for line in _read("requirements.txt").splitlines():
        match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)", line)
        if match:
            pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    return pins


def _lock():
    with open(os.path.join(_LAMBDA_DIR, "poetry.lock"), "rb") as handle:
        lock = tomllib.load(handle)
    return {package["name"].lower(): package["version"] for package in lock["package"]
            if "main" in package.get("groups", ["main"])}


def _dockerignore_patterns():
    patterns = []
    for line in _read(".dockerignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _ignored(path, patterns):
    """Whether a build-context path is excluded: a pattern matches the path or one of its parents.
    Enough of Docker's matching for the plain patterns this file uses (no `!` exceptions)."""
    parts = path.split("/")
    candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    for pattern in patterns:
        assert not pattern.startswith("!"), "exceptions are not modelled by this check"
        bare = pattern.rstrip("/")
        for candidate in candidates:
            if fnmatch.fnmatch(candidate, bare) or (
                    bare.startswith("**/") and fnmatch.fnmatch(candidate.split("/")[-1], bare[3:])):
                return True
    return False


@pytest.mark.unit
class TestRequirementsAreTheLockExport:
    def test_the_two_files_pin_the_same_packages_at_the_same_versions(self):
        assert _requirements() == _lock()

    def test_the_comparison_is_not_vacuous(self):
        # Control: both readers found the packages, so equality above is not two empty dicts.
        assert {"trimesh", "numpy", "pillow", "aws-lambda-powertools"} <= set(_requirements())

    def test_pillow_is_installed(self):
        assert "pillow" in _requirements()
        assert re.search(r"(?m)^pillow\s*=", _read("pyproject.toml"))

    def test_trimesh_stays_on_the_4x_line(self):
        assert _requirements()["trimesh"].split(".")[0] == "4"


@pytest.mark.unit
class TestBaseImage:
    def test_the_base_image_is_pinned_by_digest_for_amd64(self):
        froms = [line for line in _read("Dockerfile").splitlines() if line.startswith("FROM")]
        assert len(froms) == 1, froms
        assert re.match(
            r"^FROM --platform=linux/amd64 public\.ecr\.aws/lambda/python:3\.12@sha256:[0-9a-f]{64}$",
            froms[0]), froms[0]


@pytest.mark.unit
class TestDockerIgnore:
    @pytest.mark.parametrize("path", [
        "tests/test_conversion_contract.py", "tests/conftest.py", "__pycache__/lambda.cpython-312.pyc",
        "common/__pycache__/logger.cpython-312.pyc", ".pytest_cache/v/cache/nodeids", "poetry.lock",
        "pyproject.toml", "Dockerfile", ".dockerignore",
    ])
    def test_it_keeps_build_only_files_out_of_the_image(self, path):
        assert _ignored(path, _dockerignore_patterns()), path

    @pytest.mark.parametrize("path", _RUNTIME_PATHS)
    def test_it_lets_every_runtime_file_in(self, path):
        assert os.path.isfile(os.path.join(_LAMBDA_DIR, path)), path
        assert not _ignored(path, _dockerignore_patterns()), path

    def test_every_local_module_lambda_imports_is_a_runtime_path(self):
        # A new module beside lambda.py has to be listed above, which is what makes the check above
        # cover it.
        local = {name[:-3] for name in os.listdir(_LAMBDA_DIR) if name.endswith(".py")}
        imported = set(re.findall(r"(?m)^(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", _read("lambda.py")))
        for module in sorted(imported & (local | {"common"})):
            assert any(p == f"{module}.py" or p.startswith(f"{module}/") for p in _RUNTIME_PATHS), module
