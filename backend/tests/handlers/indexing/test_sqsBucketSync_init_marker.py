# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Import markers in bucket sync: which keys are markers, and what deleting one may touch.

An operator writes an object named exactly ``init`` (or the console folder ``init/``) into an
existing folder of a newly added bucket so that the folder syncs: its ``ObjectCreated`` event
creates or finds the folder's asset, and bucket sync then deletes the marker with all its versions.
A key whose name only ends in ``init`` (``postinit``, ``config.init``, ``terrain_init``) is an
ordinary asset file and is never deleted, and deleting a marker never reaches another key's
versions, although the version listing for ``Prefix=<marker>`` also returns keys such as
``init.txt`` and everything under ``init/``.
"""

import itertools
from unittest.mock import MagicMock

import pytest

from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load

_BUCKET = "asset-bucket"
_PREFIX = "db/"

# _load() caches the module across test files, so every attribute replaced here is restored.
_PATCHED_ATTRS = (
    "asset_bucket_name", "asset_bucket_prefix", "RESERVED_S3_PREFIX_FOLDERS",
    "get_bucket_id", "validate_asset_id", "lookup_asset", "lookup_archived_asset",
    "restore_archived_asset", "get_or_create_database_for_bucket", "object_still_exists",
    "create_new_asset", "is_versioning_enabled", "delete_s3_object", "update_s3_metadata",
    "update_asset_type", "write_file_version_history", "s3_client", "parse_event",
    "publish_to_file_indexer_sns", "publish_to_orchestration_bus",
    "is_object_permanently_deleted", "delete_file_metadata_on_s3_delete",
)

MARKER_KEYS = [
    f"{_PREFIX}a1/init",
    f"{_PREFIX}a1/init/",
    f"{_PREFIX}a1/sub/init",
    f"{_PREFIX}a1/sub/init/",
]

INIT_SUFFIX_FILES = [
    f"{_PREFIX}a1/postinit",
    f"{_PREFIX}a1/config.init",
    f"{_PREFIX}a1/terrain_init",
    f"{_PREFIX}a1/x/init.txt",
]


@pytest.fixture(autouse=True)
def _restore_module_attrs():
    m = _load()
    saved = {name: getattr(m, name) for name in _PATCHED_ATTRS}
    yield
    for name, value in saved.items():
        setattr(m, name, value)


class _VersionedBucket:
    """``list_object_versions`` and ``delete_object`` of a versioned bucket, served from entries.

    The listing follows S3: entries whose key starts with ``Prefix``, in ascending key order with a
    key's entries newest first, ``PAGE`` entries per call (fewer than any ``MaxKeys``, which S3 is
    free to return), ``Versions`` and ``DeleteMarkers`` split out of the same window, and markers
    that resume after the last entry served. A resume on a marker pair never handed out, or more
    reads than ``max_reads``, raises instead of repeating or hanging.
    """

    def __init__(self, entries, page=5, max_reads=6):
        self.entries = sorted(entries, key=lambda entry: entry[0])  # stable: per-key order kept
        self.page = page
        self.max_reads = max_reads
        self.reads = []
        self.deleted = []

    def list_object_versions(self, Bucket, Prefix, KeyMarker=None, VersionIdMarker=None, **_):
        self.reads.append((KeyMarker, VersionIdMarker))
        if len(self.reads) > self.max_reads:
            raise AssertionError(f"listing did not stop after {self.max_reads} reads")
        listed = [entry for entry in self.entries if entry[0].startswith(Prefix)]
        start = 0
        if KeyMarker is not None:
            for index, (key, version_id, _marker) in enumerate(listed):
                if key == KeyMarker and version_id == VersionIdMarker:
                    start = index + 1
                    break
            else:
                raise AssertionError(f"resumed from a marker pair S3 never returned: {KeyMarker}/{VersionIdMarker}")
        window = listed[start:start + self.page]
        truncated = start + self.page < len(listed)
        response = {
            "Versions": [{"Key": k, "VersionId": v} for k, v, is_marker in window if not is_marker],
            "DeleteMarkers": [{"Key": k, "VersionId": v} for k, v, is_marker in window if is_marker],
            "IsTruncated": truncated,
        }
        if truncated:
            response["NextKeyMarker"] = window[-1][0]
            response["NextVersionIdMarker"] = window[-1][1]
        return response

    def delete_object(self, Bucket, Key, VersionId=None):
        self.deleted.append((Key, VersionId))
        return {}


def _created_record(key):
    return {"eventName": "ObjectCreated:Put",
            "s3": {"bucket": {"name": _BUCKET}, "object": {"key": key}}}


def _deleted_record(key):
    return {"eventSource": "aws:s3", "eventName": "ObjectRemoved:Delete",
            "s3": {"bucket": {"name": _BUCKET}, "object": {"key": key}}}


def _wire_created(m, live_asset=False, archived_asset=None):
    """Wiring for process_s3_record on a new folder, a live asset or an archived asset."""
    m.asset_bucket_name = _BUCKET
    m.asset_bucket_prefix = _PREFIX
    m.RESERVED_S3_PREFIX_FOLDERS = {"temp-uploads"}
    m.get_bucket_id = MagicMock(return_value="bucket-1")
    m.validate_asset_id = MagicMock(return_value=True)
    m.lookup_asset = MagicMock(return_value={"databaseId": "db1", "assetId": "a1"} if live_asset else None)
    m.lookup_archived_asset = MagicMock(return_value=archived_asset)
    m.restore_archived_asset = MagicMock(return_value="db1")
    m.get_or_create_database_for_bucket = MagicMock(return_value="db1")
    m.object_still_exists = MagicMock(return_value=True)
    m.create_new_asset = MagicMock(return_value="a1")
    m.is_versioning_enabled = MagicMock(return_value=True)
    m.delete_s3_object = MagicMock(return_value=True)
    m.update_s3_metadata = MagicMock(return_value=True)
    m.update_asset_type = MagicMock(return_value=True)
    m.write_file_version_history = MagicMock()
    m.s3_client = MagicMock()
    m.s3_client.head_object.return_value = {"VersionId": "v1", "Metadata": {}, "LastModified": None}


def _wire_deleted(m):
    m.asset_bucket_name = _BUCKET
    m.asset_bucket_prefix = _PREFIX
    m.RESERVED_S3_PREFIX_FOLDERS = {"temp-uploads"}
    m.get_bucket_id = MagicMock(return_value="bucket-1")
    m.validate_asset_id = MagicMock(return_value=True)
    m.lookup_asset = MagicMock(return_value={"databaseId": "db1", "assetId": "a1"})
    m.is_object_permanently_deleted = MagicMock(return_value=True)
    m.delete_file_metadata_on_s3_delete = MagicMock()
    m.update_asset_type = MagicMock(return_value=True)
    m.publish_to_file_indexer_sns = MagicMock(return_value=True)


@pytest.mark.unit
class TestIsInitMarker:
    @pytest.mark.parametrize("key", [
        "init", "init/", f"{_PREFIX}init",
        f"{_PREFIX}a1/init", f"{_PREFIX}a1/init/",
        f"{_PREFIX}a1/sub/init", f"{_PREFIX}a1/sub/init/",
    ])
    def test_marker(self, key):
        assert _load().is_init_marker(key) is True

    @pytest.mark.parametrize("key", [
        f"{_PREFIX}a1/postinit", f"{_PREFIX}a1/config.init", f"{_PREFIX}a1/terrain_init",
        f"{_PREFIX}a1/postinit/", f"{_PREFIX}a1/x/init.txt", f"{_PREFIX}a1/init.json",
        f"{_PREFIX}a1/initial/", f"{_PREFIX}a1/init.d/x", f"{_PREFIX}a1/INIT", f"{_PREFIX}a1/Init",
        f"{_PREFIX}a1/init/keep.glb", "postinit", "",
    ])
    def test_not_marker(self, key):
        assert _load().is_init_marker(key) is False

    @staticmethod
    def _corpus():
        names = ["init", "INIT", "Init", "postinit", "config.init", "terrain_init", "xinit", ".init",
                 "init.txt", "initial", "init.d", "", "a"]
        parents = ["", "init/", f"{_PREFIX}", f"{_PREFIX}a1/", f"{_PREFIX}a1/sub/", f"{_PREFIX}a1/init/"]
        tails = ["", "/", "//"]
        return [parent + name + tail for parent, name, tail in itertools.product(parents, names, tails)]

    def test_every_marker_was_already_a_marker(self):
        """The marker set only narrows: nothing that was left alone before becomes a marker."""
        m = _load()
        for key in self._corpus():
            if m.is_init_marker(key):
                assert key.endswith("init") or key.endswith("init/"), key

    def test_marker_means_last_segment_is_exactly_init(self):
        m = _load()
        for key in self._corpus():
            segments = key.split("/")
            expected = segments[-1] == "init" or (key.endswith("/") and segments[-2:] == ["init", ""])
            assert m.is_init_marker(key) is expected, key


@pytest.mark.unit
class TestMarkerImportOnCreatedPath:
    """A marker creates, restores or finds its folder's asset and is then deleted, and nothing else
    runs for it: no stamp, no asset-type update, no history row, nothing forwarded or published."""

    def _assert_marker_only_deleted(self, m, key, result):
        m.delete_s3_object.assert_called_once_with(_BUCKET, key, True)
        m.update_s3_metadata.assert_not_called()
        m.update_asset_type.assert_not_called()
        m.write_file_version_history.assert_not_called()
        m.s3_client.head_object.assert_not_called()
        assert result == (True, False, f"Deleted init object {key}")

    @pytest.mark.parametrize("key", MARKER_KEYS)
    def test_new_folder_creates_asset_and_deletes_marker(self, key):
        m = _load()
        _wire_created(m)

        result = m.process_s3_record(_created_record(key))

        m.create_new_asset.assert_called_once_with("bucket-1", "db1", "a1")
        self._assert_marker_only_deleted(m, key, result)

    @pytest.mark.parametrize("key", MARKER_KEYS)
    def test_live_asset_only_deletes_marker(self, key):
        m = _load()
        _wire_created(m, live_asset=True)

        result = m.process_s3_record(_created_record(key))

        m.create_new_asset.assert_not_called()
        self._assert_marker_only_deleted(m, key, result)

    def test_archived_asset_is_restored_then_marker_deleted(self):
        m = _load()
        archived = {"databaseId": "db1#deleted", "assetId": "a1", "status": "archived"}
        _wire_created(m, archived_asset=archived)
        key = f"{_PREFIX}a1/init"

        result = m.process_s3_record(_created_record(key))

        m.restore_archived_asset.assert_called_once_with("bucket-1", "a1", archived)
        m.create_new_asset.assert_not_called()
        self._assert_marker_only_deleted(m, key, result)

    def test_marker_reaches_neither_indexers_nor_trigger_bus(self):
        m = _load()
        _wire_created(m)
        m.parse_event = MagicMock(side_effect=lambda event: event)
        m.publish_to_file_indexer_sns = MagicMock(return_value=True)
        m.publish_to_orchestration_bus = MagicMock()

        m.lambda_handler_created({"Records": [_created_record(key) for key in MARKER_KEYS]}, MagicMock())

        assert m.delete_s3_object.call_count == len(MARKER_KEYS)
        m.publish_to_file_indexer_sns.assert_not_called()
        m.publish_to_orchestration_bus.assert_not_called()


@pytest.mark.unit
class TestInitSuffixFilesAreOrdinaryFiles:
    @pytest.mark.parametrize("key", INIT_SUFFIX_FILES)
    def test_created_file_is_kept_stamped_and_indexed(self, key):
        m = _load()
        _wire_created(m, live_asset=True)

        result = m.process_s3_record(_created_record(key))

        m.delete_s3_object.assert_not_called()
        m.update_s3_metadata.assert_called_once_with(_BUCKET, key, "db1", "a1")
        m.update_asset_type.assert_called_once()
        assert result == (True, True, f"Successfully processed {key}")

    def test_folder_ending_in_init_is_a_folder_marker(self):
        m = _load()
        _wire_created(m, live_asset=True)
        key = f"{_PREFIX}a1/postinit/"

        result = m.process_s3_record(_created_record(key))

        m.delete_s3_object.assert_not_called()
        m.update_s3_metadata.assert_called_once_with(_BUCKET, key, "db1", "a1")
        assert result == (True, False, f"Processed folder marker {key}")

    def test_deleted_file_is_cleaned_up_and_forwarded(self):
        m = _load()
        _wire_deleted(m)

        m.lambda_handler_deleted({"Records": [_deleted_record(key) for key in INIT_SUFFIX_FILES]}, MagicMock())

        m.publish_to_file_indexer_sns.assert_called_once()
        forwarded = m.publish_to_file_indexer_sns.call_args.args[0]["Records"]
        assert [r["s3"]["object"]["key"] for r in forwarded] == INIT_SUFFIX_FILES
        cleaned = [c.args[2] for c in m.delete_file_metadata_on_s3_delete.call_args_list]
        assert cleaned == ["postinit", "config.init", "terrain_init", "x/init.txt"]
        assert m.update_asset_type.call_count == len(INIT_SUFFIX_FILES)


@pytest.mark.unit
class TestMarkerDeleteEchoesOnDeletePath:
    def test_echoes_of_marker_version_deletes_are_dropped(self):
        m = _load()
        _wire_deleted(m)

        m.lambda_handler_deleted({"Records": [_deleted_record(key) for key in MARKER_KEYS]}, MagicMock())

        m.publish_to_file_indexer_sns.assert_not_called()
        m.delete_file_metadata_on_s3_delete.assert_not_called()
        m.update_asset_type.assert_not_called()


@pytest.mark.unit
class TestDeleteS3ObjectReach:
    """``list_object_versions(Prefix=marker)`` also returns every key that starts with the marker's
    key. Only the marker's own versions and delete markers are deleted, from every page."""

    def test_siblings_on_the_same_page_are_never_sent(self):
        marker = f"{_PREFIX}a1/init"
        bucket = _VersionedBucket([
            (marker, "m-v2", False), (marker, "m-dm1", True), (marker, "m-v1", False),
            (f"{_PREFIX}a1/init.txt", "t-v2", False), (f"{_PREFIX}a1/init.txt", "t-v1", False),
            (f"{_PREFIX}a1/init/keep.glb", "k-v1", False),
        ], page=5)
        m = _load()
        m.s3_client = bucket

        assert m.delete_s3_object(_BUCKET, marker, True) is True

        assert sorted(bucket.deleted) == sorted([(marker, "m-v2"), (marker, "m-dm1"), (marker, "m-v1")])
        assert bucket.reads == [(None, None)], "the second page holds only other keys and is not read"

    def test_folder_marker_never_sends_the_files_under_it(self):
        marker = f"{_PREFIX}a1/init/"
        bucket = _VersionedBucket([
            (marker, "f-v2", False), (marker, "f-v1", False),
            (f"{_PREFIX}a1/init/keep.glb", "k-v1", False), (f"{_PREFIX}a1/init/sub/x.glb", "x-v1", False),
        ], page=5)
        m = _load()
        m.s3_client = bucket

        assert m.delete_s3_object(_BUCKET, marker, True) is True

        assert sorted(bucket.deleted) == sorted([(marker, "f-v2"), (marker, "f-v1")])

    def test_marker_versions_past_the_first_page_are_deleted(self):
        marker = f"{_PREFIX}a1/init"
        own = [(marker, f"m-v{n}", n % 3 == 0) for n in range(7, 0, -1)]
        bucket = _VersionedBucket(own, page=3)
        m = _load()
        m.s3_client = bucket

        assert m.delete_s3_object(_BUCKET, marker, True) is True

        assert sorted(bucket.deleted) == sorted((marker, v) for _, v, _ in own)
        assert len(bucket.reads) == 3

    def test_listing_against_a_bare_mock_reads_once(self):
        """``MagicMock.get('IsTruncated')`` is truthy, so a loop continuing on that value never ends."""
        m = _load()
        m.s3_client = MagicMock()

        assert m.delete_s3_object(_BUCKET, f"{_PREFIX}a1/init", True) is True

        assert m.s3_client.list_object_versions.call_count == 1

    def test_unversioned_bucket_deletes_the_object_without_listing(self):
        marker = f"{_PREFIX}a1/init"
        bucket = _VersionedBucket([(f"{_PREFIX}a1/init.txt", "t-v1", False)])
        m = _load()
        m.s3_client = bucket

        assert m.delete_s3_object(_BUCKET, marker, False) is True

        assert bucket.deleted == [(marker, None)]
        assert bucket.reads == []
