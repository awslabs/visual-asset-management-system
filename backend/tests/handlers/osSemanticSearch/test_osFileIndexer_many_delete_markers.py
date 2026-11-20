# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A delete event on a key with more newer delete markers than one listing page holds.

A DELETE without a version id on a key whose current version is already a delete marker adds another
marker, so a key can carry more than 1,000 delete markers above its newest version. A listing that
reads one page of ``Prefix=key`` then sees markers only and takes the archived file for a permanently
deleted one. ``common.s3.list_key_versions_to_newest_version`` pages until it reaches a version of the
key, an entry of another key, or the end. The shipped helper (loaded from source, since tests/conftest.py
registers a double of ``common.s3``) and the double are both run against the same fake, and the file
indexer is run end to end over it.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

from tests.handlers.osSemanticSearch.test_osFileIndexer_location_attribution import (  # noqa: F401
    BUCKET,
    CUSTOM,
    _record,
    _wire,
    fileIndexer,
)

_MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "common", "s3.py")
KEY = "projects/building-a/x.obj"


def _load_shipped_helper():
    with open(_MODULE_PATH) as f:
        source = f.read()
    start = source.index("def list_key_versions_to_newest_version(")
    end = source.index("\ndef list_all_objects(", start)
    namespace = {"S3_VERSIONS_PAGE_SIZE": 1000, "s3_asset_client": None}
    exec(compile(source[start:end], _MODULE_PATH, "exec"), namespace)
    return namespace["list_key_versions_to_newest_version"]


HELPERS = {
    "shipped": _load_shipped_helper(),
    "double": sys.modules["common.s3"].list_key_versions_to_newest_version,
}


class _PagedVersions:
    """ListObjectVersions over `entries` (already in S3's order: key ascending, newest first within
    a key), `MaxKeys` entries a page, continuing from the marker pair it answered last."""

    def __init__(self, entries):
        self.entries = entries
        self.calls = []

    def list_object_versions(self, Bucket, Prefix, MaxKeys=1000, KeyMarker=None, VersionIdMarker=None, **_):
        self.calls.append({"KeyMarker": KeyMarker, "VersionIdMarker": VersionIdMarker})
        matching = [e for e in self.entries if e["Key"].startswith(Prefix)]
        start = int(VersionIdMarker) if VersionIdMarker is not None else 0
        page = matching[start:start + MaxKeys]
        truncated = start + MaxKeys < len(matching)
        response = {
            "Versions": [e for e in page if not e.get("marker")],
            "DeleteMarkers": [e for e in page if e.get("marker")],
            "IsTruncated": truncated,
        }
        if truncated:
            response["NextKeyMarker"] = page[-1]["Key"]
            response["NextVersionIdMarker"] = str(start + MaxKeys)
        return response


def _markers(key, count):
    return [{"Key": key, "VersionId": f"m{i}", "IsLatest": i == 0, "marker": True, "LastModified": 10_000 - i}
            for i in range(count)]


def _version(key, version_id, latest=False):
    return {"Key": key, "VersionId": version_id, "IsLatest": latest, "LastModified": 1}


ARCHIVED_UNDER_MANY_MARKERS = _markers(KEY, 1500) + [_version(KEY, "v1")] + [_version(KEY + ".bak", "s1", True)]
MARKERS_ONLY_WITH_SIBLING = _markers(KEY, 1500) + [_version(KEY + ".bak", "s1", True)]


@pytest.mark.unit
@pytest.mark.parametrize("helper", sorted(HELPERS))
class TestTheWalkReachesTheNewestVersion:
    def test_a_version_behind_more_than_a_page_of_markers_is_found(self, helper):
        client = _PagedVersions(ARCHIVED_UNDER_MANY_MARKERS)
        listing = HELPERS[helper]("bucket", KEY, client=client)
        assert [v["VersionId"] for v in listing["Versions"]] == ["v1"]
        assert listing["DeleteMarkers"] and all(m["Key"] == KEY for m in listing["DeleteMarkers"])
        assert any(m.get("IsLatest") for m in listing["DeleteMarkers"])

    def test_a_longer_key_ends_the_walk_and_is_never_counted_as_the_key(self, helper):
        client = _PagedVersions(MARKERS_ONLY_WITH_SIBLING)
        listing = HELPERS[helper]("bucket", KEY, client=client)
        assert listing["Versions"] == []
        assert len(client.calls) >= 2

    def test_a_current_version_ends_the_walk_on_the_first_page(self, helper):
        client = _PagedVersions([_version(KEY, "v2", True), _version(KEY, "v1")] + _markers(KEY + ".bak", 3000))
        listing = HELPERS[helper]("bucket", KEY, client=client)
        assert [v["VersionId"] for v in listing["Versions"]] == ["v2", "v1"]
        assert client.calls and client.calls[0]["KeyMarker"] is None
        assert len(client.calls) <= 1


@pytest.mark.unit
class TestFileIndexerOverManyMarkers:
    def test_an_archived_file_behind_many_markers_stays_indexed_as_archived(self, fileIndexer, monkeypatch):
        _wire(fileIndexer, monkeypatch, [CUSTOM], ready=True)
        fileIndexer.s3_client = _PagedVersions(ARCHIVED_UNDER_MANY_MARKERS)
        fileIndexer.s3_client.head_object = MagicMock(return_value={"Metadata": {}, "VersionId": "v1"})
        fileIndexer.build_file_document = MagicMock(return_value=MagicMock())
        result = fileIndexer.handle_s3_notification(_record(KEY, event="ObjectRemoved:Delete"))
        assert result.operation == "index"
        assert not fileIndexer.delete_file_document.called
        assert fileIndexer.build_file_document.call_args.args[0].isArchived is True
