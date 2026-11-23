#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A single-file asset's referenced files come from its own companion folder.

A single-file asset's location names one object (``scans/model.gltf``, no trailing ``/``); what the
asset holds besides it is its companion folder ``scans/model.gltf/``. The object's parent directory
``scans/`` holds keys the asset does not hold, such as another asset's files. The manifest records the
location as the input's ``assetRootS3Key``, and the files the object references are confined to the
companion folder; a reference that leaves it is reported as not fetched.

The run goes through ``convert()`` with the manifest it reads, so the asset root travels from the
manifest to the fetch as it does in a real run. The conversion itself runs in process with a stubbed
trimesh (``inprocess_worker``)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inprocess_worker import in_process_worker  # noqa: E402
from test_3dbasic_referenced_files import FakeS3  # noqa: E402
from test_conversion_contract import _exporting_trimesh, _load  # noqa: E402

BUCKET = "abkt"
LOCATION = "scans/model.gltf"
COMPANION = LOCATION + "/tex.png"
# Another asset's file beside the single-file asset's object.
SIBLING = "scans/sibling.png"
GLTF = json.dumps({"asset": {"version": "2.0"},
                   "images": [{"uri": "tex.png"}, {"uri": "sibling.png"}, {"uri": "../sibling.png"}]})


def _run(asset_root, root_bucket=BUCKET, gltf=GLTF, objects=None):
    """Convert ``LOCATION`` from an asset whose location is ``asset_root`` (recorded in ``root_bucket``);
    return the result and the S3 client."""
    mod = _load()
    client = FakeS3({LOCATION: gltf.encode(), COMPANION: b"png", SIBLING: b"other asset", **(objects or {})})
    manifest = {
        "inputFiles": [{"relativePath": "/model.gltf", "bucket": BUCKET, "key": LOCATION,
                        "assetRootS3Key": asset_root, "assetRootBucket": root_bucket}],
        "outputs": {"bucket": "obkt", "files": "pipelines/p1/JOB/output/E1/files/"},
    }

    def _download(bucket, key, path, **_):
        with open(path, "wb") as handle:
            handle.write(client.objects[key])
        return path

    with patch.object(mod, "fetch_input_configuration", MagicMock(return_value={"outputType": ".glb"})), \
            patch.object(mod, "fetch_manifest", MagicMock(return_value=manifest)), \
            patch.object(mod, "download", MagicMock(side_effect=_download)), \
            patch.object(mod, "uploadV2", MagicMock(side_effect=lambda bucket, key, path: key)), \
            patch.object(mod, "head_input", MagicMock(return_value=(len(gltf), '"e"'))), \
            patch.object(mod, "_s3_client_for_bucket", MagicMock(return_value=client)), \
            patch.object(mod, "run_worker", in_process_worker(_exporting_trimesh())):
        result = mod.convert({"inputManifestS3Location": f"s3://{BUCKET}/m.json"})
    return result, client


@pytest.mark.unit
class TestASingleFileAssetsReferences:
    def test_only_the_companion_folder_is_read(self):
        result, client = _run(LOCATION)
        assert [key for key, _ in client.downloads] == [COMPANION]
        # The sibling exists, so a fetch confined to the parent directory would have read it.
        assert SIBLING not in client.heads
        assert all(key.startswith(LOCATION + "/") for key in client.heads)
        # The two references that do not name a file in the folder are left out and reported.
        assert result["statusCode"] == 200 and "2 referenced files" in result["body"], result["body"]

    def test_a_folder_asset_still_reads_beside_the_input(self):
        # Control: in a folder asset the input's directory is the asset, so its sibling is the asset's
        # file and is read.
        _, client = _run("scans/")
        assert SIBLING in [key for key, _ in client.downloads]
        assert COMPANION not in client.heads

    def test_a_root_recorded_in_another_bucket_is_not_applied(self):
        # The root applies only to an input read from the root's own bucket; any other input keeps the
        # rule it had (the input's own directory here).
        _, client = _run(LOCATION, root_bucket="other-bucket")
        assert SIBLING in [key for key, _ in client.downloads]
        assert COMPANION not in client.heads

    def test_a_reference_into_a_folder_named_like_the_object_is_reported_not_fetched(self):
        # The object sits at its file name locally, so model.gltf/x.png (the companion file the asset
        # lists at /model.gltf/x.png) would need a folder where the object's file is. It is reported as
        # not fetched, and the run goes on with the files that could be placed.
        gltf = json.dumps({"asset": {"version": "2.0"}, "images": [{"uri": "tex.png"}, {"uri": "model.gltf/x.png"}]})
        result, client = _run(LOCATION, gltf=gltf, objects={LOCATION + "/model.gltf/x.png": b"png"})
        assert [key for key, _ in client.downloads] == [COMPANION]
        assert result["statusCode"] == 200 and "1 referenced file" in result["body"], result["body"]
