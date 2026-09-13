# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The handler's S3 input layer: the primary file is fetched at the manifest's version, sibling files
(textures, .mtl, .bin, USD layers) are fetched from the primary's parent prefix, and every local write
stays inside the invocation's input directory.

An S3 key is an opaque byte string, so a sibling's key relative to the parent prefix can carry a leading
separator or `..` segments and resolve anywhere the process can write -- before a subprocess is launched
in the same environment. These cases are the legacy container's `test_hierarchy_download_confinement.py`
restated against `download_siblings`, plus the extension filter and the size/count caps that the legacy
stage did not have.
"""

import json
import os

import pytest
from botocore.exceptions import ClientError

from blenderTestSupport import FakeS3, load_handler

PRIMARY = "xid/test/pump.glb"
PARENT = "xid/test/"


def _handler(tmp_path, listing=(), objects=None):
    fake = FakeS3(listing=listing, objects=objects or {})
    fake.sandbox = str(tmp_path)
    return load_handler(fake), fake


def _inside(path, root):
    return os.path.realpath(path).startswith(os.path.realpath(root) + os.sep)


@pytest.mark.unit
@pytest.mark.parametrize("uri,expected", [
    ("s3://bucket/key", ("bucket", "key")),
    ("s3://bucket/a/b/c.glb", ("bucket", "a/b/c.glb")),
    ("s3://bucket/prefix/", ("bucket", "prefix/")),
])
def test_parse_s3_uri(tmp_path, uri, expected):
    handler, _fake = _handler(tmp_path)
    assert handler.parse_s3_uri(uri) == expected


@pytest.mark.unit
@pytest.mark.parametrize("uri", ["", "bucket/key", "s3://", "s3://bucket", "s3://bucket/", None])
def test_parse_s3_uri_rejects_malformed_values(tmp_path, uri):
    handler, _fake = _handler(tmp_path)
    with pytest.raises(ValueError):
        handler.parse_s3_uri(uri)


@pytest.mark.unit
def test_traversal_keys_never_write_outside_the_input_directory(tmp_path):
    listing = [
        {"Key": PRIMARY, "Size": 10},
        {"Key": "xid/test//etc/evil.png", "Size": 3},
        {"Key": "xid/test/../../../../tmp/evil.png", "Size": 3},
        {"Key": "xid/test/textures/wood.png", "Size": 3},
    ]
    objects = {entry["Key"]: b"bytes" for entry in listing}
    handler, fake = _handler(tmp_path, listing, objects)
    input_root = str(tmp_path / "input")
    os.makedirs(input_root)

    downloaded, warnings = handler.download_siblings("asset-bucket", PRIMARY, input_root)

    for download in fake.downloads:
        assert _inside(download["path"], input_root), f"{download['key']} written to {download['path']}"
    assert "xid/test/../../../../tmp/evil.png" not in [d["key"] for d in fake.downloads]
    assert any("outside the input directory" in w and "evil.png" in w for w in warnings)
    # The leading-separator key keeps its content, re-rooted under the input directory.
    rerooted = [d for d in fake.downloads if d["key"] == "xid/test//etc/evil.png"]
    assert len(rerooted) == 1
    assert os.path.realpath(rerooted[0]["path"]) == os.path.realpath(os.path.join(input_root, "etc", "evil.png"))
    assert os.path.realpath(os.path.join(input_root, "textures", "wood.png")) in \
        [os.path.realpath(p) for p in downloaded]


@pytest.mark.unit
def test_interior_parent_segments_are_still_downloaded(tmp_path):
    listing = [{"Key": PRIMARY, "Size": 10}, {"Key": "xid/test/sub/../wood.png", "Size": 3}]
    handler, fake = _handler(tmp_path, listing, {k["Key"]: b"x" for k in listing})
    input_root = str(tmp_path / "input")
    os.makedirs(input_root)
    downloaded, warnings = handler.download_siblings("asset-bucket", PRIMARY, input_root)
    assert warnings == []
    assert [os.path.realpath(p) for p in downloaded] == [os.path.realpath(os.path.join(input_root, "wood.png"))]


@pytest.mark.unit
def test_primary_directory_markers_and_foreign_extensions_are_skipped(tmp_path):
    listing = [
        {"Key": PRIMARY, "Size": 10},
        {"Key": "xid/test/", "Size": 0},
        {"Key": "xid/test/textures/", "Size": 0},
        {"Key": "xid/test/notes.txt", "Size": 3},
        {"Key": "xid/test/other.glb", "Size": 3},
        {"Key": "xid/test/pump.mtl", "Size": 3},
        {"Key": "xid/test/geometry.bin", "Size": 3},
        {"Key": "xid/test/layer.usda", "Size": 3},
    ]
    handler, fake = _handler(tmp_path, listing, {k["Key"]: b"x" for k in listing})
    input_root = str(tmp_path / "input")
    os.makedirs(input_root)
    downloaded, _warnings = handler.download_siblings("asset-bucket", PRIMARY, input_root)
    assert sorted(d["key"] for d in fake.downloads) == [
        "xid/test/geometry.bin", "xid/test/layer.usda", "xid/test/pump.mtl"]
    assert len(downloaded) == 3


@pytest.mark.unit
def test_listing_is_paginated_under_the_parent_prefix(tmp_path):
    listing = [{"Key": f"{PARENT}t{i}.png", "Size": 1} for i in range(6)] + [{"Key": PRIMARY, "Size": 10}]
    handler, fake = _handler(tmp_path, listing, {k["Key"]: b"x" for k in listing})
    input_root = str(tmp_path / "input")
    os.makedirs(input_root)
    downloaded, _warnings = handler.download_siblings("asset-bucket", PRIMARY, input_root)
    assert fake.list_calls == [{"Bucket": "asset-bucket", "Prefix": PARENT}]
    assert len(downloaded) == 6, "both listing pages must be consumed"


@pytest.mark.unit
def test_caps_stop_the_download_with_a_warning(tmp_path, monkeypatch):
    listing = [{"Key": f"{PARENT}t{i}.png", "Size": 1} for i in range(5)]
    handler, fake = _handler(tmp_path, listing, {k["Key"]: b"x" for k in listing})
    monkeypatch.setattr(handler, "MAX_SIBLING_FILES", 2)
    input_root = str(tmp_path / "input")
    os.makedirs(input_root)
    downloaded, warnings = handler.download_siblings("asset-bucket", PRIMARY, input_root)
    assert len(downloaded) == 2
    assert any("stopped at 2 files" in w for w in warnings)

    fake.downloads.clear()
    monkeypatch.setattr(handler, "MAX_SIBLING_FILES", 500)
    monkeypatch.setattr(handler, "MAX_SIBLING_TOTAL_BYTES", 3)
    downloaded, warnings = handler.download_siblings("asset-bucket", PRIMARY, input_root)
    assert len(downloaded) == 3
    assert any("stopped at 3 bytes" in w for w in warnings)


@pytest.mark.unit
def test_primary_download_passes_the_version_id_only_when_given(tmp_path):
    handler, fake = _handler(tmp_path, objects={PRIMARY: b"glb"})
    dest = str(tmp_path / "input" / "pump.glb")
    os.makedirs(os.path.dirname(dest))
    handler.download_primary("asset-bucket", PRIMARY, "v123", dest)
    handler.download_primary("asset-bucket", PRIMARY, "", dest)
    assert fake.downloads[0]["extra"] == {"VersionId": "v123"}
    assert fake.downloads[1]["extra"] is None
    assert open(dest, "rb").read() == b"glb"


@pytest.mark.unit
def test_confined_local_path_rules(tmp_path):
    handler, _fake = _handler(tmp_path)
    root = str(tmp_path / "input")
    os.makedirs(root)
    assert handler.confined_local_path(root, "a/b.png") == os.path.realpath(os.path.join(root, "a", "b.png"))
    assert handler.confined_local_path(root, "/a/b.png") == os.path.realpath(os.path.join(root, "a", "b.png"))
    assert handler.confined_local_path(root, "../b.png") is None
    assert handler.confined_local_path(root, "") is None
    assert handler.confined_local_path(root, "a/../../b.png") is None


@pytest.mark.unit
@pytest.mark.parametrize("body,expected,warning_fragment", [
    (json.dumps({"includeSiblingFiles": "false", "renderViews": 3}),
     {"includeSiblingFiles": False, "renderViews": 3}, None),
    (json.dumps({"includeSiblingFiles": True, "renderViews": 99}),
     {"includeSiblingFiles": True, "renderViews": 8}, None),
    (json.dumps({"renderViews": 0}), {"includeSiblingFiles": True, "renderViews": 1}, None),
    (json.dumps({"renderViews": "many"}), {"includeSiblingFiles": True, "renderViews": 8}, "not an integer"),
    (json.dumps({"unrelated": 1}), {"includeSiblingFiles": True, "renderViews": 8}, None),
    (b"{not json", {"includeSiblingFiles": True, "renderViews": 8}, "unreadable"),
])
def test_render_options_from_the_template_configuration(tmp_path, body, expected, warning_fragment):
    raw = body if isinstance(body, bytes) else body.encode("utf-8")
    handler, _fake = _handler(tmp_path, objects={"run/config.json": raw})
    options, warnings = handler.load_render_options("s3://run-bucket/run/config.json")
    assert options == expected
    if warning_fragment is None:
        assert warnings == []
    else:
        assert len(warnings) == 1 and warning_fragment in warnings[0]


@pytest.mark.unit
def test_render_options_when_the_configuration_is_absent_or_unnamed(tmp_path):
    handler, _fake = _handler(tmp_path)
    assert handler.load_render_options("") == ({"includeSiblingFiles": True, "renderViews": 8}, [])
    options, warnings = handler.load_render_options("s3://run-bucket/run/missing.json")
    assert options == {"includeSiblingFiles": True, "renderViews": 8}
    assert len(warnings) == 1 and "missing" in warnings[0]


@pytest.mark.unit
def test_read_json_object_distinguishes_missing_from_forbidden(tmp_path):
    handler, fake = _handler(tmp_path, objects={"a/b.json": b'{"ok": true}', "a/list.json": b"[1]"})
    assert handler.read_json_object("bucket", "a/b.json") == {"ok": True}
    assert handler.read_json_object("bucket", "a/none.json") is None
    with pytest.raises(ValueError):
        handler.read_json_object("bucket", "a/list.json")

    def _forbidden(Bucket, Key):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject")

    fake.get_object = _forbidden
    with pytest.raises(ClientError):
        handler.read_json_object("bucket", "a/b.json")
