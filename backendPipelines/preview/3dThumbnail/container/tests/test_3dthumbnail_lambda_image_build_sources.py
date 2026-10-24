#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Build sources of the Lambda 3D render image, which CloudFormation never inspects.

Every property here fails in a way a deploy reports as success: a floating base tag rebuilds a different
image months apart from identical sources; a USER placed before the last COPY leaves the handler
root-owned and one placed after ENTRYPOINT never applies; an unpinned requirement resolves differently
per build; a stray Open3D adds ~400 MB to an image that routes FBX elsewhere; and a .dockerignore that
excludes the package builds an image with no handler in it."""

import os
import re

import pytest

CONTAINER_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
LAMBDA_DOCKERFILE = os.path.join(CONTAINER_DIR, "Dockerfile.lambda")
FARGATE_DOCKERFILE = os.path.join(CONTAINER_DIR, "Dockerfile")
LAMBDA_REQUIREMENTS = os.path.join(CONTAINER_DIR, "requirements.lambda.txt")
FARGATE_REQUIREMENTS = os.path.join(CONTAINER_DIR, "requirements.txt")
DOCKERIGNORE = os.path.join(CONTAINER_DIR, ".dockerignore")

SPEC_PACKAGES = ("pyvista", "vtk", "trimesh", "numpy", "scipy", "laspy[lazrs]", "pye57", "cadquery", "Pillow",
                 "DracoPy", "usd-core", "ezdxf", "ifcopenshell", "lxml", "networkx")
EXACT_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9.]+$")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _lines(path):
    return _read(path).split("\n")


def _requirement_lines(path):
    return [line.strip() for line in _lines(path) if line.strip() and not line.lstrip().startswith("#")]


def _index(lines, pattern):
    matches = [i for i, line in enumerate(lines) if re.match(pattern, line)]
    return matches


@pytest.mark.unit
class TestLambdaDockerfile:
    def test_base_image_is_the_lambda_python_312_image_pinned_by_digest(self):
        froms = [line for line in _lines(LAMBDA_DOCKERFILE) if re.match(r"^FROM\s", line)]
        assert len(froms) == 1, froms
        assert re.match(r"^FROM\s+--platform=linux/amd64\s+public\.ecr\.aws/lambda/python:3\.12@sha256:[0-9a-f]{64}\s*$",
                        froms[0]), froms[0]

    def test_runs_as_a_non_root_user_created_after_the_last_copy_and_before_the_entrypoint(self):
        lines = _lines(LAMBDA_DOCKERFILE)
        user_at = _index(lines, r"^USER\s+\S")
        assert len(user_at) == 1
        name = lines[user_at[0]].split()[1]
        assert not re.match(r"^(root|0)(:|$)", name)
        last_copy = max(_index(lines, r"^COPY\s"))
        entrypoint_at = _index(lines, r"^ENTRYPOINT\s")
        assert len(entrypoint_at) == 1
        assert last_copy < user_at[0] < entrypoint_at[0]
        creation = "\n".join(line for line in lines if re.search(r"\buseradd\b", line))
        assert name in creation
        assert re.search(r"^ENV\s+HOME=/tmp/", "\n".join(lines), re.MULTILINE)

    def test_entrypoint_is_the_lambda_runtime_and_cmd_is_the_handler(self):
        text = _read(LAMBDA_DOCKERFILE)
        assert 'ENTRYPOINT ["/lambda-entrypoint.sh"]' in text
        assert 'CMD ["lambda_handler.lambda_handler"]' in text
        assert "Xvfb :99" not in text, "the handler starts Xvfb; the entrypoint does not"

    def test_installs_the_x_server_and_software_gl(self):
        text = _read(LAMBDA_DOCKERFILE)
        for package in ("xorg-x11-server-Xvfb", "mesa-libGL", "mesa-dri-drivers", "libXt", "libxkbcommon",
                        "shadow-utils"):
            assert package in text, package
        assert "LIBGL_ALWAYS_SOFTWARE=1" in text
        assert "DISPLAY=:99" in text

    def test_copies_the_shared_package_and_the_handler(self):
        text = _read(LAMBDA_DOCKERFILE)
        assert re.search(r"^COPY preview_pipeline \$\{LAMBDA_TASK_ROOT\}/preview_pipeline$", text, re.MULTILINE)
        assert re.search(r"^COPY lambda_handler\.py \$\{LAMBDA_TASK_ROOT\}/lambda_handler\.py$", text, re.MULTILINE)
        assert re.search(r"^COPY requirements\.lambda\.txt ", text, re.MULTILINE)
        assert "--only-binary=:all:" in text, "no compiler is installed, so every package must come as a wheel"
        assert "open3d" not in text.lower()


@pytest.mark.unit
class TestLambdaRequirements:
    def test_every_requirement_is_pinned_exactly(self):
        lines = _requirement_lines(LAMBDA_REQUIREMENTS)
        assert lines, "requirements.lambda.txt is empty"
        unpinned = [line for line in lines if not EXACT_PIN.match(line)]
        assert unpinned == [], unpinned

    def test_the_spec_packages_are_present(self):
        names = {EXACT_PIN.match(line).group(1) + (EXACT_PIN.match(line).group(2) or "")
                 for line in _requirement_lines(LAMBDA_REQUIREMENTS)}
        missing = [pkg for pkg in SPEC_PACKAGES if pkg not in names]
        assert missing == [], missing

    def test_open3d_is_absent(self):
        assert not any(line.lower().startswith("open3d") for line in _requirement_lines(LAMBDA_REQUIREMENTS))

    def test_boto3_matches_the_backend_layer_pin(self):
        lines = _requirement_lines(LAMBDA_REQUIREMENTS)
        assert "boto3==1.43.89" in lines
        assert "botocore==1.43.89" in lines

    def test_the_fargate_requirements_are_untouched(self):
        """Control: the thumbnail image keeps its own file, Open3D included (its FBX fallback)."""
        fargate = _requirement_lines(FARGATE_REQUIREMENTS)
        assert any(line.startswith("open3d") for line in fargate)
        assert any(line.startswith("pyvista>=") for line in fargate)


@pytest.mark.unit
class TestDockerignoreAndFargateImage:
    def test_dockerignore_excludes_tests_and_scratch_but_not_the_package(self):
        entries = [line.strip() for line in _lines(DOCKERIGNORE) if line.strip() and not line.startswith("#")]
        for required in ("tests/", "tmp/", "**/__pycache__/", "**/*.pyc", ".pytest_cache/"):
            assert required in entries, required
        for forbidden in ("preview_pipeline", "lambda_handler.py", "requirements", "Dockerfile"):
            assert not any(forbidden in entry for entry in entries), forbidden

    def test_the_fargate_dockerfile_still_ships_the_package_and_starts_xvfb_itself(self):
        text = _read(FARGATE_DOCKERFILE)
        assert "COPY ./preview_pipeline /app/preview_pipeline" in text
        assert "Xvfb :99" in text
        assert "USER appuser" in text
