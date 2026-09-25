# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The installed botocore knows the DynamoDB vector-search API, and every pin agrees on the version.

``SearchVectors`` and the ``VectorIndexes`` table property entered the DynamoDB service model in
botocore 1.43.64. Below that floor a client has no ``search_vectors`` method at all and
``create_table`` rejects ``VectorIndexes`` before any request leaves the process, so the Stubber
contract tests in ``tests/test_vectorStub.py`` and every vector-store test would either pass
vacuously or fail with an ``AttributeError`` far from its cause. This guard fails first and names
the cause. It is durable and never skips: a skip would let the contract tests pass against a model
that lacks the operation.

The second half pins the version itself. The runtime pin, the dev-group pin, the Lambda base-layer
pin and the three exported requirements files must carry one identical version. The layer bundling
build re-exports from poetry.lock (infra/lib/helper/lambda.ts) and installs that export, while this
suite's venv installs from the checked-in requirements-dev.txt, so the lock and the checked-in
exports must all agree with the pin or the suite runs against a different SDK than Lambda.
"""

import os
import re

import boto3
import botocore
import botocore.session
import pytest

VECTOR_API_FLOOR = (1, 43, 64)
PINNED_VERSION = "1.43.89"

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Relative path -> the regexes whose first group is a boto3/botocore version in that file.
_PIN_FILES = {
    "pyproject.toml": (
        r'"boto3 \(==([0-9.]+)\)"',
        r'"botocore \(==([0-9.]+)\)"',
        r'^boto3 = "([0-9.]+)"',
        r'^botocore = "([0-9.]+)"',
    ),
    "requirements.txt": (r"^boto3==([0-9.]+)", r"^botocore==([0-9.]+)"),
    "requirements-dev.txt": (r"^boto3==([0-9.]+)", r"^botocore==([0-9.]+)"),
    os.path.join("lambdaLayers", "base", "pyproject.toml"): (
        r'^boto3 = "([0-9.]+)"',
        r'^botocore = "([0-9.]+)"',
    ),
    os.path.join("lambdaLayers", "base", "requirements.txt"): (
        r"^boto3==([0-9.]+)",
        r"^botocore==([0-9.]+)",
    ),
}


def _version_tuple(version):
    return tuple(int(part) for part in version.split(".")[:3])


def _dynamodb_model():
    return botocore.session.get_session().get_service_model("dynamodb")


@pytest.mark.unit
class TestTheInstalledSdkKnowsTheVectorApi:
    def test_search_vectors_is_an_operation(self):
        assert "SearchVectors" in _dynamodb_model().operation_names, (
            f"botocore {botocore.__version__} has no SearchVectors; the floor is "
            f"{'.'.join(map(str, VECTOR_API_FLOOR))} -- install backend/requirements-dev.txt in a "
            "fresh venv (backend/.venv) and run the suite with its interpreter"
        )

    def test_create_table_accepts_vector_indexes(self):
        members = _dynamodb_model().operation_model("CreateTable").input_shape.members
        assert "VectorIndexes" in members

    def test_update_table_accepts_vector_index_updates(self):
        members = _dynamodb_model().operation_model("UpdateTable").input_shape.members
        assert "VectorIndexUpdates" in members

    def test_a_client_exposes_search_vectors(self):
        client = boto3.client("dynamodb", region_name="us-east-1")
        assert hasattr(client, "search_vectors")

    def test_installed_version_meets_the_floor(self):
        assert _version_tuple(botocore.__version__) >= VECTOR_API_FLOOR
        assert _version_tuple(boto3.__version__) >= VECTOR_API_FLOOR


@pytest.mark.unit
class TestEveryPinAgrees:
    @pytest.mark.parametrize("relative_path", sorted(_PIN_FILES))
    def test_pin_file_carries_the_pinned_version(self, relative_path):
        with open(os.path.join(_BACKEND_DIR, relative_path), encoding="utf-8") as handle:
            text = handle.read()
        found = []
        for pattern in _PIN_FILES[relative_path]:
            matches = re.findall(pattern, text, flags=re.MULTILINE)
            assert matches, f"{relative_path}: no line matches {pattern!r}"
            found.extend(matches)
        assert set(found) == {PINNED_VERSION}, (
            f"{relative_path} pins {sorted(set(found))}, expected {PINNED_VERSION}"
        )

    def test_pinned_version_meets_the_floor(self):
        assert _version_tuple(PINNED_VERSION) >= VECTOR_API_FLOOR
