#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""One listing pins the run: every download is read at the listed ETag (the selected file at its
launch version), a file that changes mid-run fails the run, and the dependency manifest of the
files the upload holds is written to the auxiliary bucket before the stream metadata names it."""
import hashlib
import json
import os

import pytest

from dependency_manifest import build_manifest, canonical_bytes, manifest_id, normalize_etag
from miris_upload_container_harness import (
    ASSET_ID, AUX_BUCKET, definition, etag_of, fake_usd, manifest_puts)

pytestmark = pytest.mark.unit

ROOT = f"{ASSET_ID}/"
FILES = {"scene.usda": b"#usda 1.0", "geo/geo.usd": b"geo", "textures/wood.png": b"png",
         "unrelated/notes.png": b"notes"}


def _asset(container, monkeypatch):
    """A root naming geo/geo.usd and textures/wood.png, beside a file nothing references."""
    for rel, body in FILES.items():
        container.s3.add("asset-bucket", f"{ROOT}{rel}", body)
    fake_usd(container, monkeypatch, {"scene.usda": {"geo/geo.usd"}})

    def dependencies(path):
        base = os.path.dirname(path)
        return ([path, os.path.join(base, "geo/geo.usd")], [os.path.join(base, "textures/wood.png")],
                [], [])

    monkeypatch.setattr(container.main, "compute_dependencies", dependencies)


def _log(capsys, msg):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()
            if line.startswith("{") and json.loads(line).get("msg") == msg]


def _stream_metadata(container):
    body = json.loads(container.s3.put_object.call_args.kwargs["Body"])
    return {e["metadataKey"]: e["metadataValue"] for e in body["metadata"]}


@pytest.mark.parametrize("change", [b"edited", None], ids=["edited", "removed"])
@pytest.mark.parametrize("versioned", [True, False])
def test_a_file_changed_after_the_listing_fails_the_run_naming_it(
        container, monkeypatch, capsys, change, versioned):
    _asset(container, monkeypatch)
    if not versioned:
        container.s3.unversioned.update(f"{ROOT}{rel}" for rel in FILES)
    container.s3.changes_after_listing[("asset-bucket", f"{ROOT}textures/wood.png")] = change

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="scene.usda"))

    assert exited.value.code == container.main.EXIT_ASSET_CHANGED_DURING_RUN == 19
    entry, = _log(capsys, "asset_changed_during_run")
    assert entry["file"] == "textures/wood.png"
    container.miris_cls.assert_not_called()
    container.s3.put_object.assert_not_called()


def test_the_manifest_holds_the_root_and_its_tree_at_the_etags_downloaded(container, monkeypatch):
    _asset(container, monkeypatch)

    container.run(definition(trigger_rel="scene.usda"))

    put, = manifest_puts(container)
    assert json.loads(put["Body"]) == {
        "root": "scene.usda",
        "files": {rel: normalize_etag(etag_of(FILES[rel]))
                  for rel in ("scene.usda", "geo/geo.usd", "textures/wood.png")},
    }
    sha = hashlib.sha256(put["Body"]).hexdigest()
    assert put["Key"] == f"pipelines/miris-upload/manifests/db1/{ASSET_ID}/{sha}.json"
    assert put["ContentType"] == "application/json"


def test_the_manifest_is_written_before_the_stream_metadata_that_names_it(container, monkeypatch):
    _asset(container, monkeypatch)
    order = []
    container.s3.put_object.side_effect = lambda **kw: order.append(kw["Bucket"])
    container.miris.start_upload.side_effect = (
        lambda **kw: order.append("miris") or {**container.miris.start_upload.return_value})

    container.run(definition(trigger_rel="scene.usda"))

    assert order == [AUX_BUCKET, "miris", "asset-bucket"]
    put, = manifest_puts(container)
    assert _stream_metadata(container)["miris_dependency_manifest"] == hashlib.sha256(
        put["Body"]).hexdigest()


def test_a_manifest_that_cannot_be_written_fails_before_anything_reaches_miris(
        container, monkeypatch, capsys):
    _asset(container, monkeypatch)
    container.s3.put_object.side_effect = Exception("AccessDenied")

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="scene.usda"))

    assert exited.value.code == container.main.EXIT_UNEXPECTED == 1
    assert _log(capsys, "dependency_manifest_not_written")
    assert [c.kwargs["Bucket"] for c in container.s3.put_object.call_args_list] == [AUX_BUCKET]
    container.miris_cls.assert_not_called()


def test_the_root_read_at_its_launch_version_is_recorded_at_that_version_etag(container, monkeypatch):
    _asset(container, monkeypatch)
    container.s3.version_etags[(f"{ROOT}scene.usda", "v-launch")] = '"launch-etag"'
    d = json.loads(definition(trigger_rel="scene.usda"))
    d["stages"][0]["triggerInput"]["versionId"] = "v-launch"

    container.run(json.dumps(d))

    put, = manifest_puts(container)
    assert json.loads(put["Body"])["files"]["scene.usda"] == "launch-etag"
    assert (f"{ROOT}scene.usda", {"VersionId": "v-launch"}) in container.s3.heads


def test_a_definition_without_the_auxiliary_bucket_fails_before_listing(container):
    d = json.loads(definition())
    del d["stages"][0]["auxiliaryBucketName"]

    with pytest.raises(SystemExit) as exited:
        container.run(json.dumps(d))

    assert exited.value.code == container.main.EXIT_USAGE == 2
    assert container.s3.list_calls == []


def test_the_canonical_bytes_and_id_do_not_depend_on_order_or_etag_quoting():
    first = build_manifest("scene.usda", {"scene.usda": '"a1"', "textures/wood.png": "b2-3"})
    second = build_manifest("/scene.usda", {"/textures/wood.png": ' "b2-3"', "scene.usda": "a1"})

    assert canonical_bytes(first) == canonical_bytes(second) == (
        b'{"files":{"scene.usda":"a1","textures/wood.png":"b2-3"},"root":"scene.usda"}')
    assert manifest_id(canonical_bytes(first)) == hashlib.sha256(canonical_bytes(second)).hexdigest()
