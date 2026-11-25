#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Another asset stored inside the asset's location keeps its files: the run reads the nested
locations from the object its definition names, and its one listing leaves out every key they hold,
so those files are not root candidates, not downloaded and not in the dependency manifest."""
import json

import pytest

from miris_upload_container_harness import ASSET_ID, AUX_BUCKET, definition, fake_usd, manifest_puts

pytestmark = pytest.mark.unit

ROOT = f"{ASSET_ID}/"
OWN = ("scene.usda", "mat.usdc")
NESTED_FILES = ("parts/bolt.usda", "parts/steel.png", "scans/scan.usdz", "scans/scan.usdz.previewFile.png")
NESTED_LOCATIONS = [f"{ROOT}parts/", f"{ROOT}scans/scan.usdz"]
NESTED_OBJECT = {"bucketName": AUX_BUCKET,
                 "objectKey": "pipelines/miris-upload/nested-locations/E1.json"}
GRAPH = {"scene.usda": {"mat.usdc"}}


def _asset(container):
    for rel in OWN + NESTED_FILES:
        container.s3.add("asset-bucket", f"{ROOT}{rel}", b"x" * 10)
    container.s3.add(AUX_BUCKET, NESTED_OBJECT["objectKey"],
                     json.dumps({"nestedLocations": NESTED_LOCATIONS}).encode())


def test_a_nested_assets_files_are_left_out_of_the_upload(container, monkeypatch):
    _asset(container)
    fake_usd(container, monkeypatch, GRAPH)

    container.run(definition(trigger_rel="scene.usda", nestedLocations=NESTED_OBJECT))

    assert container.miris.start_upload.call_args.kwargs["content_path"] == "scene.usdz"
    downloaded = {key[len(ROOT):] for _, key, _ in container.s3.downloads}
    assert downloaded and not downloaded & set(NESTED_FILES)
    put, = manifest_puts(container)
    assert not set(json.loads(put["Body"])["files"]) & set(NESTED_FILES)


def test_control_without_the_nested_locations_their_layers_are_root_candidates(container, monkeypatch):
    _asset(container)
    fake_usd(container, monkeypatch, GRAPH)

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="scene.usda"))

    assert exited.value.code == container.main.EXIT_AMBIGUOUS_ROOT


def test_a_manual_run_on_a_nested_assets_file_finds_no_root(container, monkeypatch):
    _asset(container)
    fake_usd(container, monkeypatch, GRAPH)

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="parts/bolt.usda", manualRun=True,
                                 nestedLocations=NESTED_OBJECT))

    assert exited.value.code == container.main.EXIT_ROOT_NOT_FOUND
    container.miris_cls.assert_not_called()


@pytest.mark.parametrize("stored", [None, b"not json", b'{"nestedLocations": "parts/"}'],
                         ids=["missing", "not-json", "not-a-list"])
def test_a_nested_locations_object_that_cannot_be_read_fails_before_anything_reaches_miris(
        container, monkeypatch, capsys, stored):
    _asset(container)
    key = (AUX_BUCKET, NESTED_OBJECT["objectKey"])
    if stored is None:
        container.s3.files.pop(key)
    else:
        container.s3.files[key] = stored
    fake_usd(container, monkeypatch, GRAPH)

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="scene.usda", nestedLocations=NESTED_OBJECT))

    assert exited.value.code == container.main.EXIT_USAGE
    assert '"nested_locations_unreadable"' in capsys.readouterr().out
    container.miris_cls.assert_not_called()
    assert container.s3.downloads == []
