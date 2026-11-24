#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Another asset stored inside the asset's location keeps its files: the run's one listing leaves out
every key a nested location holds, so those files are not root candidates, not downloaded and not
in the dependency manifest."""
import json

import pytest

from miris_upload_container_harness import ASSET_ID, definition, fake_usd, manifest_puts

pytestmark = pytest.mark.unit

ROOT = f"{ASSET_ID}/"
OWN = ("scene.usda", "mat.usdc")
NESTED_FILES = ("parts/bolt.usda", "parts/steel.png", "scans/scan.usdz", "scans/scan.usdz.previewFile.png")
NESTED_LOCATIONS = [f"{ROOT}parts/", f"{ROOT}scans/scan.usdz"]
GRAPH = {"scene.usda": {"mat.usdc"}}


def _asset(container):
    for rel in OWN + NESTED_FILES:
        container.s3.add("asset-bucket", f"{ROOT}{rel}", b"x" * 10)


def test_a_nested_assets_files_are_left_out_of_the_upload(container, monkeypatch):
    _asset(container)
    fake_usd(container, monkeypatch, GRAPH)

    container.run(definition(trigger_rel="scene.usda", nestedLocations=NESTED_LOCATIONS))

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
                                 nestedLocations=NESTED_LOCATIONS))

    assert exited.value.code == container.main.EXIT_ROOT_NOT_FOUND
    container.miris_cls.assert_not_called()
