#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The selected input file is read at the S3 version the run recorded at launch
(triggerInput.versionId); the other layers of the asset are read at their current version."""
import json

import pytest

from miris_upload_container_harness import ASSET_ID, definition, fake_usd

pytestmark = pytest.mark.unit

ROOT = f"{ASSET_ID}/"


def _layered_asset(container, monkeypatch):
    for rel in ("scene.usda", "geo/geo.usd", "textures/wood.png"):
        container.s3.add("asset-bucket", f"{ROOT}{rel}", "x" * 10)
    fake_usd(container, monkeypatch, {"scene.usda": {"geo/geo.usd"}})


def _with_version(trigger_rel, version):
    d = json.loads(definition(trigger_rel=trigger_rel))
    d["stages"][0]["triggerInput"]["versionId"] = version
    return json.dumps(d)


def _download_args(container, rel):
    return [args for _, key, args in container.s3.downloads if key == f"{ROOT}{rel}"]


def _source_version(container):
    body = json.loads(container.s3.put_object.call_args.kwargs["Body"])
    return {e["metadataKey"]: e["metadataValue"] for e in body["metadata"]}["miris_source_version_id"]


@pytest.mark.parametrize("version", ["v-launch", "null"])
def test_the_selected_root_is_read_at_its_launch_version(container, monkeypatch, version):
    # "null" is a real version id (written while versioning was off) and is sent.
    _layered_asset(container, monkeypatch)

    container.run(_with_version("scene.usda", version))

    assert _download_args(container, "scene.usda") == [{"VersionId": version}]
    assert _download_args(container, "textures/wood.png") == [{"VersionId": f"v-{ROOT}textures/wood.png"}]
    assert _source_version(container) == version


def test_a_selected_layer_is_read_at_its_launch_version_and_the_root_at_its_current_one(
        container, monkeypatch):
    _layered_asset(container, monkeypatch)

    container.run(_with_version("geo/geo.usd", "v-launch"))

    assert _download_args(container, "geo/geo.usd") == [{"VersionId": "v-launch"}]
    assert _download_args(container, "scene.usda") == [{"VersionId": f"v-{ROOT}scene.usda"}]
    assert _source_version(container) == f"v-{ROOT}scene.usda"


@pytest.mark.parametrize("version", ["", None])
def test_without_a_launch_version_the_reads_are_unchanged(container, monkeypatch, version):
    _layered_asset(container, monkeypatch)
    run_definition = definition(trigger_rel="scene.usda") if version is None else _with_version("scene.usda", version)

    container.run(run_definition)

    assert _download_args(container, "scene.usda") == [{"VersionId": f"v-{ROOT}scene.usda"}]
    assert _source_version(container) == f"v-{ROOT}scene.usda"


def test_a_launch_version_deleted_since_launch_fails_naming_the_file_and_version(
        container, monkeypatch, capsys):
    _layered_asset(container, monkeypatch)
    container.s3.missing_versions.add((f"{ROOT}scene.usda", "v-gone"))

    with pytest.raises(SystemExit) as exited:
        container.run(_with_version("scene.usda", "v-gone"))

    assert exited.value.code == container.main.EXIT_INPUT_VERSION_MISSING == 13
    logged = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    missing = [entry for entry in logged if entry.get("msg") == "input_version_missing"]
    assert missing and missing[0]["file"] == "scene.usda" and missing[0]["version_id"] == "v-gone"
    container.miris_cls.assert_not_called()
    container.s3.put_object.assert_not_called()
