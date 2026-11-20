#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import datetime
import json
import os
import sys

import pytest

_CONTAINER_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _CONTAINER_DIR)

from stream_metadata import (  # noqa: E402
    build_stream_metadata_body,
    format_uploaded_at,
    metadata_object_key,
    metadata_output_location,
)
from miris_upload_container_harness import (  # noqa: E402
    ASSET_ID, AUX_BUCKET, METADATA_PREFIX, definition, etag_of, manifest_puts)

pytestmark = pytest.mark.unit

UPLOADED_AT = datetime.datetime(2026, 10, 14, 9, 30, 15, 123456, tzinfo=datetime.timezone.utc)


def _body(**overrides):
    kwargs = {
        "asset_uuid": "0b6f4c9e-6a43-4bb5-9d0c-4b3e2b1f7a10",
        "source_version_id": "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY",
        "display_name": "Pump Housing",
        "uploaded_at": UPLOADED_AT,
        "dependency_manifest": "ab" * 32,
    }
    kwargs.update(overrides)
    return build_stream_metadata_body(**kwargs)


def _entries(body):
    return {item["metadataKey"]: item for item in body["metadata"]}


def test_body_is_a_file_metadata_upsert_of_the_five_typed_stream_keys():
    body = _body()

    assert (body["type"], body["updateType"]) == ("metadata", "update")
    assert {k: (e["metadataValue"], e["metadataValueType"]) for k, e in _entries(body).items()} == {
        "miris_asset_uuid": ("0b6f4c9e-6a43-4bb5-9d0c-4b3e2b1f7a10", "string"),
        "miris_source_version_id": ("3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY", "string"),
        "miris_uploaded_at": ("2026-10-14T09:30:15Z", "date"),
        "miris_display_name": ("Pump Housing", "string"),
        "miris_dependency_manifest": ("ab" * 32, "string"),
    }
    assert len(body["metadata"]) == 5
    assert json.loads(json.dumps(body)) == body


def test_uploaded_at_is_utc_and_parses_the_way_the_vams_date_validator_parses_it():
    value = _entries(_body())["miris_uploaded_at"]["metadataValue"]
    assert datetime.datetime.fromisoformat(value.replace("Z", "+00:00")) == UPLOADED_AT.replace(microsecond=0)

    plus_two = datetime.timezone(datetime.timedelta(hours=2))
    assert format_uploaded_at(datetime.datetime(2026, 10, 14, 11, 30, tzinfo=plus_two)) == "2026-10-14T09:30:00Z"


@pytest.mark.parametrize("version_id", [None, ""])
def test_unknown_source_version_is_written_as_empty_string(version_id):
    assert _entries(_body(source_version_id=version_id))["miris_source_version_id"]["metadataValue"] == ""


@pytest.mark.parametrize("rel", ["model.usda", "scenes/v2/model.usd", "Coral House.usdz"])
def test_the_metadata_key_is_the_usd_relative_path_verbatim(rel):
    assert metadata_object_key(METADATA_PREFIX, rel) == f"{METADATA_PREFIX}{rel}.metadata.json"


@pytest.mark.parametrize("prefix", [METADATA_PREFIX, METADATA_PREFIX.rstrip("/")])
def test_metadata_output_location_reads_the_stage_as_a_folder(prefix):
    stage = {"outputMetadataFiles": {"bucketName": "b", "objectDir": prefix}}
    assert metadata_output_location(stage) == ("b", METADATA_PREFIX)


@pytest.mark.parametrize("stage", [
    {},
    {"outputMetadataFiles": {"bucketName": "b", "objectDir": ""}},
    {"outputMetadataFiles": {"bucketName": "", "objectDir": METADATA_PREFIX}},
])
def test_metadata_output_location_rejects_a_missing_path(stage):
    with pytest.raises(ValueError, match="metadata output path"):
        metadata_output_location(stage)


# --- container entry point --------------------------------------------------------------------


def _usdz_asset(container, rel="pump.usdz", root=f"{ASSET_ID}/"):
    container.s3.add("asset-bucket", f"{root}{rel}", b"usdz")


def test_main_writes_the_stream_metadata_next_to_the_usd_relative_path(container):
    _usdz_asset(container, "scenes/pump.usdz")
    container.run(definition(trigger_rel="scenes/pump.usdz"))

    # The asset bucket gets the metadata alone; the manifest goes to the auxiliary bucket.
    assert [c.kwargs["Bucket"] for c in container.s3.put_object.call_args_list] == [
        AUX_BUCKET, "asset-bucket"]
    container.s3.upload_file.assert_not_called()
    put = container.s3.put_object.call_args.kwargs
    assert put["Bucket"] == "asset-bucket"
    assert put["Key"] == f"{METADATA_PREFIX}scenes/pump.usdz.metadata.json"
    assert put["ContentType"] == "application/json"
    entries = _entries(json.loads(put["Body"]))
    assert entries["miris_asset_uuid"]["metadataValue"] == "0b6f4c9e-6a43-4bb5-9d0c-4b3e2b1f7a10"
    assert entries["miris_display_name"]["metadataValue"] == "pump"


@pytest.mark.parametrize("versioned", [True, False])
def test_main_records_the_version_it_downloaded(container, versioned):
    _usdz_asset(container)
    key = f"{ASSET_ID}/pump.usdz"
    if not versioned:
        container.s3.unversioned.add(key)
    container.run(definition())

    version = f"v-{key}" if versioned else ""
    # Without a version id the conditional read itself carries the listed ETag.
    assert container.s3.downloads == ([("asset-bucket", key, {"VersionId": version})] if versioned else [])
    assert container.s3.streamed == ([] if versioned else [(key, etag_of(b"usdz"))])
    body = json.loads(container.s3.put_object.call_args.kwargs["Body"])
    assert _entries(body)["miris_source_version_id"]["metadataValue"] == version


def test_main_fails_before_uploading_when_the_metadata_path_is_missing(container):
    _usdz_asset(container)
    d = json.loads(definition())
    del d["stages"][0]["outputMetadataFiles"]

    with pytest.raises(SystemExit) as exit_info:
        container.run(json.dumps(d))

    assert exit_info.value.code == 7
    container.miris_cls.assert_not_called()
    container.s3.put_object.assert_not_called()


def test_asset_download_pins_every_file_to_the_listing(container, monkeypatch):
    root = f"{ASSET_ID}/"
    container.s3.add("asset-bucket", f"{root}scenes/pump.usda", "#usda 1.0")
    container.s3.add("asset-bucket", f"{root}scenes/textures/wood.png", "png")
    monkeypatch.setattr(container.main, "compute_dependencies",
                        lambda path: ([path], [os.path.join(os.path.dirname(path), "wood.png")], [], []))
    monkeypatch.setattr(container.main, "package_usdz", lambda root_path, out: open(out, "wb").write(b"z"))

    container.run(definition(trigger_rel="scenes/pump.usda"))

    pinned = {key: extra for _, key, extra in container.s3.downloads}
    assert pinned == {
        f"{root}scenes/pump.usda": {"VersionId": f"v-{root}scenes/pump.usda"},
        f"{root}scenes/textures/wood.png": {"VersionId": f"v-{root}scenes/textures/wood.png"},
    }
    # Each version downloaded is the one the HEAD found at the listed ETag.
    assert sorted(container.s3.heads) == [
        (f"{root}scenes/pump.usda", {"IfMatch": etag_of(b"#usda 1.0")}),
        (f"{root}scenes/textures/wood.png", {"IfMatch": etag_of(b"png")}),
    ]
