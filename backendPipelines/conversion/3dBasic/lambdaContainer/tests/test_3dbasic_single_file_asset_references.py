#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A single-file asset's referenced files come from its own companion folder.

A single-file asset's location names one object (``p/model.gltf``, no trailing ``/``); what the asset
holds besides it is its companion folder ``p/model.gltf/``. The object's parent directory ``p/`` also
holds keys the asset does not hold (``p/other/``, ``p/model.gltf.previewFile.png/``, ``p/sibling.png``).
The manifest records the location as the input's ``assetRootS3Key``, and the files the object
references are confined to the companion folder; a reference that leaves it is skipped, not read.

The run goes through ``lambda_handler`` with the manifest it reads, so the asset root travels from the
manifest to the fetch as it does in a real run; trimesh is stubbed."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_3dbasic_referenced_files import FakeS3  # noqa: E402
from test_conversion_contract import _exporting_trimesh, _load  # noqa: E402

BUCKET = "abkt"
LOCATION = "p/model.gltf"
COMPANION = LOCATION + "/tex.png"
# Keys under the object's parent directory that the asset does not hold.
OTHER_FOLDER = "p/other/tex.png"
PREVIEW_NAMED_FOLDER = "p/model.gltf.previewFile.png/x.png"
SIBLING = "p/sibling.png"
GLTF = json.dumps({"asset": {"version": "2.0"},
                   "images": [{"uri": "tex.png"}, {"uri": "sibling.png"}, {"uri": "other/tex.png"},
                              {"uri": "../other/tex.png"}, {"uri": "model.gltf.previewFile.png/x.png"}]})


def _run(asset_root, root_bucket=BUCKET, gltf=GLTF, objects=None, key=LOCATION, relative="/model.gltf"):
    """Convert ``key`` from an asset whose location is ``asset_root``; return the S3 client, the keys
    ``download`` read and the trimesh stub."""
    mod = _load()
    client = FakeS3({LOCATION: gltf.encode(), COMPANION: b"png", OTHER_FOLDER: b"not in the asset",
                     PREVIEW_NAMED_FOLDER: b"not in the asset", SIBLING: b"not in the asset", **(objects or {})})
    entry = {"relativePath": relative, "bucket": root_bucket, "key": key}
    if asset_root is not None:
        entry["assetRootS3Key"] = asset_root
    manifest = {"inputFiles": [entry], "outputs": {"bucket": "obkt", "files": "pipelines/p1/JOB/output/E1/files/"}}
    downloaded = []

    def _download(bucket, object_key, path):
        downloaded.append(object_key)
        with open(path, "wb") as handle:
            handle.write(client.objects[object_key])
        return path

    stub = _exporting_trimesh()
    with patch.object(mod, "fetch_input_configuration", MagicMock(return_value={"outputType": ".glb"})), \
            patch.object(mod, "fetch_manifest", MagicMock(return_value=manifest)), \
            patch.object(mod, "download", MagicMock(side_effect=_download)), \
            patch.object(mod, "uploadV2", MagicMock(side_effect=lambda bucket, k, path: k)), \
            patch.object(mod, "s3_client", client), \
            patch.object(mod, "trimesh", stub):
        result = mod.lambda_handler({"body": {"inputManifestS3Location": f"s3://{BUCKET}/m.json"}}, None)
    assert result["statusCode"] == 200
    return client, downloaded, stub


def _read(client, downloaded):
    """Every key the run looked up or read."""
    return set(client.heads) | {k for k, _ in client.downloads} | set(downloaded)


@pytest.mark.unit
class TestASingleFileAssetsReferences:
    def test_only_the_asset_own_keys_are_read(self):
        client, downloaded, _ = _run(LOCATION)
        assert downloaded == [LOCATION]
        assert [k for k, _ in client.downloads] == [COMPANION]
        # Those keys exist, and none of them is looked up.
        for key in _read(client, downloaded):
            assert key == LOCATION or key.startswith(LOCATION + "/"), key
        assert not {OTHER_FOLDER, PREVIEW_NAMED_FOLDER, SIBLING} & _read(client, downloaded)

    def test_the_loader_is_served_the_companion_file_only(self):
        _, _, stub = _run(LOCATION)
        # The run's working directory is gone by now, so the served names are read from the resolver's map.
        resolver = stub.load.call_args.kwargs["resolver"]
        assert list(resolver.keys()) == ["tex.png"]

    def test_a_companion_folder_file_stays_in_the_folder(self):
        nested = LOCATION + "/parts/a.gltf"
        gltf = json.dumps({"asset": {"version": "2.0"}, "images": [{"uri": "../tex.png"}, {"uri": "../../other/tex.png"}]})
        client, downloaded, _ = _run(LOCATION, key=nested, relative="/parts/a.gltf", objects={nested: gltf.encode()})
        assert [k for k, _ in client.downloads] == [COMPANION]
        assert OTHER_FOLDER not in _read(client, downloaded)

    def test_a_folder_asset_still_reads_beside_the_input(self):
        # Control: in a folder asset the input's directory is the asset, so its sibling is the asset's file.
        client, _, _ = _run("p/")
        assert SIBLING in [k for k, _ in client.downloads]
        assert OTHER_FOLDER in [k for k, _ in client.downloads]
        assert COMPANION not in client.heads

    def test_a_manifest_without_an_asset_root_keeps_the_rule_it_had(self):
        client, _, _ = _run(None)
        assert SIBLING in [k for k, _ in client.downloads]

    def test_a_reference_into_a_folder_named_like_the_object_is_skipped(self):
        # The object sits at its file name locally, so model.gltf/x.png (the companion file the asset lists
        # at /model.gltf/x.png) would need a folder where the object's file is; it is skipped, and the run
        # converts with the files that could be placed.
        gltf = json.dumps({"asset": {"version": "2.0"}, "images": [{"uri": "tex.png"}, {"uri": "model.gltf/x.png"}]})
        client, _, _ = _run(LOCATION, gltf=gltf, objects={LOCATION + "/model.gltf/x.png": b"png"})
        assert [k for k, _ in client.downloads] == [COMPANION]
