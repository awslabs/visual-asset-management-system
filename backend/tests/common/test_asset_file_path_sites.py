# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Issue #1846: every handler that turns an asset-relative file path into an S3 key uses one mapping.

``common.assetLocations.AssetFilePaths`` decides what a path names in an asset location: the single-file own
object at ``/{file name}``, ``..`` refused, and only keys the asset holds. A handler that joins a path onto the
location key itself would bring back the paths that miss the object, or reach a key outside the asset. This
test reads the handler sources: each file-route handler uses the shared mapping, and no handler under
``backend/handlers`` defines its own path-to-key join of the earlier shape. ``createAsset.normalize_s3_path``
joins a ``bucketExistingKey`` under the bucket's base prefix, not a path under an asset location, and is the one
named exception.
"""

import os
import re

import pytest

HANDLERS = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "handlers")

# The handlers whose routes take an asset-relative file path (or return one for a key)
FILE_PATH_SITES = (
    "assets/assetFiles.py",
    "assets/downloadAsset.py",
    "assets/streamAsset.py",
    "assets/streamAuxiliaryPreviewAsset.py",
    "assets/uploadFile.py",
    "assets/assetExportService.py",
    "metadata/metadataService.py",
    "workflows/executeWorkflow.py",
)

# The earlier per-site resolver: "if the path starts with the base key use it, else prepend the base key"
EARLIER_JOIN = re.compile(r"(file_path|relative_path|rel)\.startswith\((asset_base_key|asset_base_path|root)\)")
EXCEPTIONS = {"assets/createAsset.py"}


def _source(relative):
    with open(os.path.join(HANDLERS, relative), encoding="utf-8") as handle:
        return handle.read()


@pytest.mark.unit
@pytest.mark.parametrize("relative", FILE_PATH_SITES)
def test_each_file_path_site_uses_the_shared_mapping(relative):
    source = _source(relative)
    assert re.search(r"\b(asset_file_paths|AssetFilePaths)\b", source), (
        f"{relative} maps asset file paths without common.assetLocations.AssetFilePaths")


@pytest.mark.unit
def test_no_handler_defines_its_own_path_to_key_join():
    offenders = []
    for folder, _, files in os.walk(HANDLERS):
        for name in files:
            if not name.endswith(".py"):
                continue
            relative = os.path.relpath(os.path.join(folder, name), HANDLERS).replace(os.sep, "/")
            if relative in EXCEPTIONS:
                continue
            if EARLIER_JOIN.search(_source(relative)):
                offenders.append(relative)
    assert offenders == [], (
        "these handlers join a file path onto an asset location themselves, use "
        f"common.assetLocations.AssetFilePaths instead: {offenders}")
