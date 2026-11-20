# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Make the container package importable and share the raster fixtures.

The fixtures under fixtures/raster are the contract's shared set (planning raster-contract section 1.2): the
same files and `.expected.json` sidecars are carried by the SYSTEM metadata extractor's tests. Tests that need
the GDAL command line skip with a reason when it is not on PATH; the image carries it, so they run in the
container (see the handoff for the command)."""

import json
import os
import shutil
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
FIXTURES = os.path.join(_HERE, "fixtures", "raster")

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")


def fixture_path(name):
    return os.path.join(FIXTURES, name)


def expected(name):
    with open(fixture_path(name[:-4] + ".expected.json"), encoding="utf-8") as handle:
        return json.load(handle)


requires_gdal = pytest.mark.skipif(
    shutil.which("gdal_translate") is None, reason="gdal_translate is not on PATH (runs inside the image)")
