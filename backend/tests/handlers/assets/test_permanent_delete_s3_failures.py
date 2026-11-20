# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A delete reports success only once Amazon S3 holds no version of the deleted objects.

``delete_s3_prefix_all_versions`` raises ``S3PrefixDeleteIncomplete`` when it cannot list the prefix or
when a key keeps a version or delete marker, naming the keys it deleted and the keys it kept. A bucket
that does not exist holds nothing, so its listing is empty and the delete completes. Its callers act on
that:

-   The asset permanent delete deletes the asset's S3 objects before any record. When S3 keeps
    something, the files whose every version is gone lose their file records (in batches) and their
    derived data, the asset keeps every other record (row, topic, subscription, metadata, links,
    versions, history), and the request answers 400, so the delete can be retried and the retry
    completes it.
-   The file route's prefix delete removes the records and derived data of the files it deleted and
    then answers 400. A single-file delete that S3 refuses keeps the file's previews, derived data and
    records.

The objects live in a versioned moto bucket. A wrapper client answers ``AccessDenied`` for the
listing or for chosen deletes, as S3 does for a bucket VAMS cannot write to or a version a policy
protects. The route tests run the handlers under the production audit decorator.
"""

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.customLogging import auditLogging as production_audit  # noqa: E402
from backend.backend.handlers.assets import assetFiles as af  # noqa: E402
from tests.handlers.assets.test_assetService_history import _asset, _load  # noqa: E402

BUCKET = "vams-test-assets"
FOLDER_LOCATION = "proj/a/"
FOLDER_FILES = ("proj/a/x.txt", "proj/a/x.txt.previewFile.png", "proj/a/sub/y.txt", "proj/a/sub/z.txt")
FOLDER_KEYS = ("proj/a/",) + FOLDER_FILES
SIBLING = "proj/a-other/keep.txt"
FILE_LOCATION = "p1/model.glb"
SHADOWED = "p1/model.glb/model.glb"
COMPANION = "p1/model.glb/sub/c.obj"
PREVIEW_KEY = "previews/a1/thumb.png"
CLAIMS = {"tokens": ["tester"], "roles": []}

MISSING_BUCKET = "vams-test-assets-deleted"
LAMBDA_CONTEXT = SimpleNamespace(aws_request_id="lambda-req-1", get_remaining_time_in_millis=lambda: 30000)

_FIX = ("Check that VAMS can delete objects in the asset's storage bucket (its permissions and any object "
        "lock), then try again.")
ASSET_MESSAGE = ("Error permanently deleting asset: the asset's storage could not be fully deleted. " + _FIX
                 + " The asset and its remaining files were kept.")
PREFIX_MESSAGE = ("Error deleting files: the files under the prefix could not all be deleted from the asset's "
                  "storage. " + _FIX + " The remaining files were kept.")
FILE_MESSAGE = ("Error deleting file: the file could not be fully deleted from the asset's storage. " + _FIX
                + " The file and its previews were kept.")


class _NeverRaised(Exception):
    """Stands in for an exception class the module under test does not define."""


def _incomplete_error():
    return getattr(af, "S3PrefixDeleteIncomplete", _NeverRaised)


def _access_denied(operation):
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, operation)


class _DeniedPaginator:
    def paginate(self, **kwargs):
        raise _access_denied("ListObjectVersions")


class _FaultyS3:
    """An S3 client that answers AccessDenied for the version listing or for chosen deletes.

    ``deny_keys`` refuses every delete of a key; ``deny_versions`` refuses one (key, VersionId).
    Everything else goes to the wrapped moto client.
    """

    def __init__(self, client):
        self._client = client
        self.deny_listing = False
        self.deny_all_deletes = False
        self.deny_keys = set()
        self.deny_versions = set()

    def get_paginator(self, name):
        if name == "list_object_versions" and self.deny_listing:
            return _DeniedPaginator()
        return self._client.get_paginator(name)

    def delete_object(self, **kwargs):
        key, version = kwargs.get("Key"), kwargs.get("VersionId")
        if self.deny_all_deletes or key in self.deny_keys or (key, version) in self.deny_versions:
            raise _access_denied("DeleteObject")
        return self._client.delete_object(**kwargs)

    def lift(self):
        self.deny_listing = self.deny_all_deletes = False
        self.deny_keys.clear()
        self.deny_versions.clear()

    def __getattr__(self, name):
        return getattr(self._client, name)


def _versions(client, key):
    listing = client.list_object_versions(Bucket=BUCKET, Prefix=key)
    return ([v["VersionId"] for v in listing.get("Versions", []) if v["Key"] == key]
            + [m["VersionId"] for m in listing.get("DeleteMarkers", []) if m["Key"] == key])


def _keys_under(client, prefix):
    listing = client.list_object_versions(Bucket=BUCKET, Prefix=prefix)
    return sorted({e["Key"] for e in listing.get("Versions", []) + listing.get("DeleteMarkers", [])})


@pytest.fixture
def s3(monkeypatch):
    """A versioned moto bucket holding a folder asset, a single-file asset and an asset preview."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in FOLDER_KEYS + (SIBLING, FILE_LOCATION, SHADOWED, COMPANION, PREVIEW_KEY):
            client.put_object(Bucket=BUCKET, Key=key, Body=b"v1")
        client.put_object(Bucket=BUCKET, Key="proj/a/x.txt", Body=b"v2")

        faulty = _FaultyS3(client)
        monkeypatch.setattr(af, "s3_client", faulty)
        # moto serves one request at a time reliably; the worker pool is not under test
        monkeypatch.setattr(af, "MAX_PARALLEL_S3_WORKERS", 1)
        yield SimpleNamespace(client=client, faulty=faulty)


@pytest.mark.unit
class TestPrefixDeleteReportsWhatS3Kept:
    def test_a_refused_listing_raises_and_deletes_nothing(self, s3):
        s3.faulty.deny_listing = True

        with pytest.raises(_incomplete_error()) as raised:
            af.delete_s3_prefix_all_versions(BUCKET, FOLDER_LOCATION, location=FOLDER_LOCATION)

        assert raised.value.deleted_keys == [] and raised.value.failed_keys == []
        assert _keys_under(s3.client, FOLDER_LOCATION) == sorted(FOLDER_KEYS)

    def test_refused_deletes_raise_and_keep_every_version(self, s3):
        s3.faulty.deny_all_deletes = True

        with pytest.raises(_incomplete_error()) as raised:
            af.delete_s3_prefix_all_versions(BUCKET, FOLDER_LOCATION, location=FOLDER_LOCATION)

        assert raised.value.deleted_keys == []
        assert sorted(raised.value.failed_keys) == sorted(FOLDER_KEYS)
        assert len(_versions(s3.client, "proj/a/x.txt")) == 2

    def test_a_partial_batch_names_the_deleted_and_the_kept_keys(self, s3):
        s3.faulty.deny_keys.add("proj/a/sub/z.txt")

        with pytest.raises(_incomplete_error()) as raised:
            af.delete_s3_prefix_all_versions(BUCKET, FOLDER_LOCATION, location=FOLDER_LOCATION)

        assert raised.value.failed_keys == ["proj/a/sub/z.txt"]
        assert sorted(raised.value.deleted_keys) == sorted(set(FOLDER_KEYS) - {"proj/a/sub/z.txt"})
        assert _keys_under(s3.client, FOLDER_LOCATION) == ["proj/a/sub/z.txt"]
        assert _keys_under(s3.client, "proj/a-other/") == [SIBLING]

    def test_a_key_that_keeps_one_of_its_versions_is_kept(self, s3):
        oldest = _versions(s3.client, "proj/a/x.txt")[-1]
        s3.faulty.deny_versions.add(("proj/a/x.txt", oldest))

        with pytest.raises(_incomplete_error()) as raised:
            af.delete_s3_prefix_all_versions(BUCKET, FOLDER_LOCATION, location=FOLDER_LOCATION)

        assert raised.value.failed_keys == ["proj/a/x.txt"]
        assert _versions(s3.client, "proj/a/x.txt") == [oldest]

    def test_a_complete_delete_returns_the_deleted_keys(self, s3):
        deleted = af.delete_s3_prefix_all_versions(BUCKET, FOLDER_LOCATION, location=FOLDER_LOCATION)

        assert sorted(deleted) == sorted(FOLDER_KEYS)
        assert _keys_under(s3.client, FOLDER_LOCATION) == []
        assert _keys_under(s3.client, "proj/a-other/") == [SIBLING]

    def test_a_bucket_that_does_not_exist_is_an_empty_listing(self, s3):
        assert af.delete_s3_prefix_all_versions(MISSING_BUCKET, FOLDER_LOCATION, location=FOLDER_LOCATION) == []

    def test_an_access_denied_listing_is_still_a_failure(self, s3):
        s3.faulty.deny_listing = True

        with pytest.raises(_incomplete_error()):
            af.delete_s3_prefix_all_versions(BUCKET, FOLDER_LOCATION, location=FOLDER_LOCATION)


@pytest.fixture
def aux(s3, monkeypatch):
    """The auxiliary-bucket delete assetFiles runs, observed: each call is one prefix."""
    observed = MagicMock(name="delete_assetAuxiliary_files")
    monkeypatch.setattr(af, "delete_assetAuxiliary_files", observed)
    return observed


def _aux_prefixes(observed):
    return sorted(call.args[0] for call in observed.call_args_list)


@pytest.fixture
def svc(s3, aux, monkeypatch):
    """assetService's permanent delete wired to the real S3 prefix delete and the moto bucket."""
    m = _load()
    asset = _asset(db="db1", aid="a1", assetLocation={"Key": FOLDER_LOCATION},
                   snsTopic="arn:aws:sns:us-east-1:111122223333:a1-topic")
    state = SimpleNamespace(m=m, asset=asset, client=s3.client, faulty=s3.faulty, aux=aux,
                            bucket={"bucketName": BUCKET})
    stubs = {
        "get_asset_details": MagicMock(side_effect=lambda *args, **kwargs: dict(state.asset)),
        "authorize_single_asset": MagicMock(return_value=True),
        "get_asset_bucket_details": MagicMock(side_effect=lambda *args, **kwargs: dict(state.bucket)),
        "delete_s3_prefix_all_versions": af.delete_s3_prefix_all_versions,
        "delete_file_metadata": MagicMock(name="delete_file_metadata"),
        "delete_files_metadata": MagicMock(name="delete_files_metadata"),
        "delete_file_version_records": MagicMock(),
        "delete_auxiliary_files_of_keys": getattr(af, "delete_auxiliary_files_of_keys", MagicMock()),
        "delete_assetAuxiliary_files": MagicMock(),
        "delete_asset_metadata_for_permanent_deletion": MagicMock(),
        "write_asset_history_record": MagicMock(),
        "send_subscription_email": MagicMock(),
        "update_asset_count": MagicMock(),
        "asset_table": MagicMock(),
        "sns_client": MagicMock(),
        "subscription_table": MagicMock(),
        "asset_links_table": None,
        "asset_upload_table": None,
        "comment_table": None,
        "versions_table": None,
        "asset_versions_files_table": None,
        "asset_file_metadata_versions_table": None,
    }
    if hasattr(af, "S3PrefixDeleteIncomplete"):
        stubs["S3PrefixDeleteIncomplete"] = af.S3PrefixDeleteIncomplete
    for name, value in stubs.items():
        monkeypatch.setattr(m, name, value, raising=False)
    yield state


def _delete_permanently(svc):
    request = MagicMock()
    request.confirmPermanentDelete = True
    return svc.m.delete_asset_permanent("db1", "a1", request, {"tokens": ["u1"]})


def _assert_asset_kept(m):
    m.asset_table.delete_item.assert_not_called()
    m.sns_client.delete_topic.assert_not_called()
    m.subscription_table.delete_item.assert_not_called()
    m.delete_asset_metadata_for_permanent_deletion.assert_not_called()
    m.delete_assetAuxiliary_files.assert_not_called()
    m.update_asset_count.assert_not_called()
    m.write_asset_history_record.assert_not_called()


def _file_metadata_paths(m):
    """The file paths whose metadata the partial cleanup removed, over every batch it sent."""
    return sorted(path for call in m.delete_files_metadata.call_args_list for path in call.args[2])


def _no_file_records_removed(m):
    m.delete_file_metadata.assert_not_called()
    assert _file_metadata_paths(m) == []


@pytest.mark.unit
class TestAssetPermanentDeleteStopsWhenS3KeepsObjects:
    def test_a_refused_listing_fails_and_keeps_the_asset(self, svc):
        svc.faulty.deny_listing = True

        with pytest.raises(svc.m.VAMSGeneralErrorResponse) as raised:
            _delete_permanently(svc)

        assert str(raised.value).endswith(ASSET_MESSAGE)
        _assert_asset_kept(svc.m)
        _no_file_records_removed(svc.m)
        svc.aux.assert_not_called()
        assert _keys_under(svc.client, FOLDER_LOCATION) == sorted(FOLDER_KEYS)

    def test_refused_deletes_fail_and_keep_the_asset_and_its_files(self, svc):
        svc.faulty.deny_all_deletes = True

        with pytest.raises(svc.m.VAMSGeneralErrorResponse) as raised:
            _delete_permanently(svc)

        assert str(raised.value).endswith(ASSET_MESSAGE)
        _assert_asset_kept(svc.m)
        _no_file_records_removed(svc.m)
        svc.aux.assert_not_called()
        assert _keys_under(svc.client, FOLDER_LOCATION) == sorted(FOLDER_KEYS)

    def test_a_partial_failure_clears_the_records_and_derived_data_of_the_deleted_files_only(self, svc):
        svc.faulty.deny_keys.add("proj/a/sub/z.txt")

        with pytest.raises(svc.m.VAMSGeneralErrorResponse):
            _delete_permanently(svc)

        _assert_asset_kept(svc.m)
        assert _keys_under(svc.client, FOLDER_LOCATION) == ["proj/a/sub/z.txt"]
        removed = ["/sub/y.txt", "/x.txt", "/x.txt.previewFile.png"]
        assert _file_metadata_paths(svc.m) == removed
        svc.m.delete_files_metadata.assert_called_once()
        svc.m.delete_file_metadata.assert_not_called()
        svc.m.delete_file_version_records.assert_called_once()
        assert sorted(svc.m.delete_file_version_records.call_args.args[2]) == removed
        assert _aux_prefixes(svc.aux) == ["db1/proj/a/sub/y.txt/", "db1/proj/a/x.txt.previewFile.png/",
                                          "db1/proj/a/x.txt/"]

    def test_a_kept_object_keeps_its_records_and_its_companion_copy_loses_its_own(self, svc):
        """The companion entry named like the object has its own path, /model.glb/model.glb."""
        svc.asset["assetLocation"] = {"Key": FILE_LOCATION}
        svc.faulty.deny_keys.add(FILE_LOCATION)

        with pytest.raises(svc.m.VAMSGeneralErrorResponse):
            _delete_permanently(svc)

        assert _keys_under(svc.client, FILE_LOCATION) == [FILE_LOCATION]
        assert _file_metadata_paths(svc.m) == ["/model.glb/model.glb", "/sub/c.obj"]
        assert _aux_prefixes(svc.aux) == ["db1/p1/model.glb/model.glb/", "db1/p1/model.glb/sub/c.obj/"]

    def test_a_refused_preview_delete_keeps_the_asset(self, svc):
        svc.asset["previewLocation"] = {"Key": PREVIEW_KEY}
        svc.faulty.deny_keys.add(PREVIEW_KEY)

        with pytest.raises(svc.m.VAMSGeneralErrorResponse):
            _delete_permanently(svc)

        svc.m.asset_table.delete_item.assert_not_called()
        svc.m.sns_client.delete_topic.assert_not_called()
        assert _keys_under(svc.client, FOLDER_LOCATION) == []
        assert _keys_under(svc.client, PREVIEW_KEY) == [PREVIEW_KEY]
        assert _file_metadata_paths(svc.m) == ["/sub/y.txt", "/sub/z.txt", "/x.txt", "/x.txt.previewFile.png"]
        # The file location completed, so its derived data went with one prefix delete
        svc.m.delete_assetAuxiliary_files.assert_called_once()
        svc.aux.assert_not_called()

    def test_a_retry_after_the_cause_is_gone_completes_the_delete(self, svc):
        svc.faulty.deny_keys.add("proj/a/sub/z.txt")
        with pytest.raises(svc.m.VAMSGeneralErrorResponse):
            _delete_permanently(svc)

        svc.faulty.lift()
        result = _delete_permanently(svc)

        assert result.success is True
        assert _keys_under(svc.client, FOLDER_LOCATION) == []
        deleted_rows = [call.kwargs["Key"] for call in svc.m.asset_table.delete_item.call_args_list]
        assert deleted_rows == [{"databaseId": "db1", "assetId": "a1"},
                                {"databaseId": "db1#deleted", "assetId": "a1"}]

    def test_records_are_removed_only_after_the_objects_are_gone(self, svc):
        seen = []
        record_removal = lambda name: lambda *args, **kwargs: seen.append(  # noqa: E731
            (name, _keys_under(svc.client, FOLDER_LOCATION)))
        svc.m.sns_client.delete_topic.side_effect = record_removal("topic")
        svc.m.subscription_table.delete_item.side_effect = record_removal("subscription")
        svc.m.asset_table.delete_item.side_effect = record_removal("asset row")

        _delete_permanently(svc)

        assert [name for name, _ in seen] == ["topic", "subscription", "asset row", "asset row"]
        assert all(keys == [] for _, keys in seen), seen


@pytest.mark.unit
class TestAssetPermanentDeleteSuccessIsUnchanged:
    def test_a_complete_delete_answers_as_before(self, svc):
        result = _delete_permanently(svc)

        assert result.success is True
        assert result.message == "Asset a1 permanently deleted from all systems"
        assert result.operation == "delete"
        assert _keys_under(svc.client, FOLDER_LOCATION) == []
        assert _keys_under(svc.client, "proj/a-other/") == [SIBLING]
        svc.m.sns_client.delete_topic.assert_called_once()
        svc.m.delete_asset_metadata_for_permanent_deletion.assert_called_once_with("db1", "a1")
        svc.m.delete_assetAuxiliary_files.assert_called_once()
        svc.m.write_asset_history_record.assert_called_once()
        _no_file_records_removed(svc.m)

    def test_a_complete_delete_with_a_preview_deletes_both_locations(self, svc):
        svc.asset["previewLocation"] = {"Key": PREVIEW_KEY}

        assert _delete_permanently(svc).success is True
        assert _keys_under(svc.client, FOLDER_LOCATION) == []
        assert _keys_under(svc.client, PREVIEW_KEY) == []

    def test_an_asset_whose_bucket_no_longer_exists_is_deleted(self, svc):
        svc.bucket = {"bucketName": MISSING_BUCKET}

        result = _delete_permanently(svc)

        assert result.success is True
        deleted_rows = [call.kwargs["Key"] for call in svc.m.asset_table.delete_item.call_args_list]
        assert deleted_rows == [{"databaseId": "db1", "assetId": "a1"},
                                {"databaseId": "db1#deleted", "assetId": "a1"}]
        svc.m.sns_client.delete_topic.assert_called_once()


@pytest.fixture
def files(s3, aux, monkeypatch):
    """assetFiles' file route on the folder asset, with its record helpers observed."""
    asset = {"databaseId": "db1", "assetId": "a1", "bucketId": "b1", "assetLocation": {"Key": FOLDER_LOCATION}}
    monkeypatch.setattr(af, "get_asset_with_permissions", lambda *args, **kwargs: asset)
    monkeypatch.setattr(af, "get_default_bucket_details",
                        lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET, "baseAssetsPrefix": ""})
    observed = {name: MagicMock(name=name) for name in ("delete_file_metadata", "delete_files_metadata",
                                                        "delete_file_version_records", "send_subscription_email")}
    for name, mock in observed.items():
        monkeypatch.setattr(af, name, mock, raising=False)
    yield SimpleNamespace(client=s3.client, faulty=s3.faulty, aux=aux, **observed)


def _prefix_metadata_paths(files):
    """Paths whose metadata the prefix delete removed, whether one at a time or in a batch."""
    single = [call.args[2] for call in files.delete_file_metadata.call_args_list]
    batched = [path for call in files.delete_files_metadata.call_args_list for path in call.args[2]
               if not path.endswith("/")]
    return sorted(single + batched)


@pytest.mark.unit
class TestFilePrefixDeleteStopsWhenS3KeepsObjects:
    def test_a_partial_prefix_delete_fails_and_clears_only_the_deleted_files_records(self, files):
        files.faulty.deny_keys.add("proj/a/sub/z.txt")

        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.delete_file("db1", "a1", "/sub/", True, True, CLAIMS)

        assert str(raised.value).endswith(PREFIX_MESSAGE)
        assert _prefix_metadata_paths(files) == ["sub/y.txt"]
        files.delete_file_metadata.assert_not_called()
        files.delete_file_version_records.assert_called_once_with("db1", "a1", ["sub/y.txt"])
        assert _aux_prefixes(files.aux) == ["db1/proj/a/sub/y.txt/"]
        files.send_subscription_email.assert_not_called()
        assert _keys_under(files.client, "proj/a/sub/") == ["proj/a/sub/z.txt"]

    def test_a_refused_prefix_listing_fails(self, files):
        files.faulty.deny_listing = True

        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.delete_file("db1", "a1", "/sub/", True, True, CLAIMS)

        assert str(raised.value).endswith(PREFIX_MESSAGE)
        assert _prefix_metadata_paths(files) == []
        files.aux.assert_not_called()
        assert _keys_under(files.client, "proj/a/sub/") == ["proj/a/sub/y.txt", "proj/a/sub/z.txt"]

    def test_a_complete_prefix_delete_answers_as_before(self, files):
        response = af.delete_file("db1", "a1", "/sub/", True, True, CLAIMS)

        assert response.success is True
        assert sorted(response.affectedFiles) == ["sub/y.txt", "sub/z.txt"]
        assert _prefix_metadata_paths(files) == ["sub/y.txt", "sub/z.txt"]
        assert _aux_prefixes(files.aux) == ["db1/proj/a/sub/"]
        files.send_subscription_email.assert_called_once_with("db1", "a1")


@pytest.mark.unit
class TestSingleFileDeleteChecksTheFileFirst:
    """A file delete that S3 refuses keeps the file's previews, derived data and records."""

    def test_a_refused_file_delete_keeps_its_previews_and_derived_data(self, files):
        files.faulty.deny_keys.add("proj/a/x.txt")

        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.delete_file("db1", "a1", "/x.txt", False, True, CLAIMS)

        assert str(raised.value).endswith(FILE_MESSAGE)
        assert _keys_under(files.client, "proj/a/x.txt") == ["proj/a/x.txt", "proj/a/x.txt.previewFile.png"]
        files.aux.assert_not_called()
        files.delete_file_metadata.assert_not_called()
        files.delete_file_version_records.assert_not_called()
        files.send_subscription_email.assert_not_called()

    def test_a_file_delete_removes_the_file_its_previews_and_derived_data(self, files):
        response = af.delete_file("db1", "a1", "/x.txt", False, True, CLAIMS)

        assert sorted(response.affectedFiles) == ["/x.txt", "/x.txt.previewFile.png"]
        assert _keys_under(files.client, "proj/a/x.txt") == []
        assert _aux_prefixes(files.aux) == ["db1/proj/a/x.txt/"]
        files.delete_file_metadata.assert_called_once_with("db1", "a1", "x.txt")


TABLE_KEYS = {"metadata": "metadataKey", "attributes": "attributeKey"}


@pytest.fixture
def record_tables(monkeypatch):
    """The file metadata and attribute tables in moto, with the asset-wide reads counted."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        tables = {}
        for name, row_key in TABLE_KEYS.items():
            dynamodb.create_table(
                TableName=f"test-{name}", BillingMode="PAY_PER_REQUEST",
                KeySchema=[{"AttributeName": row_key, "KeyType": "HASH"},
                           {"AttributeName": "databaseId:assetId:filePath", "KeyType": "RANGE"}],
                AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in
                                      (row_key, "databaseId:assetId:filePath", "databaseId:assetId")],
                GlobalSecondaryIndexes=[
                    {"IndexName": "DatabaseIdAssetIdIndex", "Projection": {"ProjectionType": "ALL"},
                     "KeySchema": [{"AttributeName": "databaseId:assetId", "KeyType": "HASH"},
                                   {"AttributeName": row_key, "KeyType": "RANGE"}]},
                    {"IndexName": "DatabaseIdAssetIdFilePathIndex", "Projection": {"ProjectionType": "ALL"},
                     "KeySchema": [{"AttributeName": "databaseId:assetId:filePath", "KeyType": "HASH"},
                                   {"AttributeName": row_key, "KeyType": "RANGE"}]}])
            tables[name] = dynamodb.Table(f"test-{name}")
        rows = {"metadata": [("/x.txt", "k1"), ("/x.txt", "k2"), ("/sub/y.txt", "k1"), ("/sub/z.txt", "k1"),
                             ("/", "assetLevel")],
                "attributes": [("/x.txt", "a1"), ("/sub/z.txt", "a1")]}
        for name, entries in rows.items():
            for path, key in entries:
                tables[name].put_item(Item={TABLE_KEYS[name]: key, "databaseId:assetId:filePath": f"db1:a1:{path}",
                                            "databaseId:assetId": "db1:a1"})
            tables[name].put_item(Item={TABLE_KEYS[name]: "other", "databaseId:assetId:filePath": "db1:a2:/x.txt",
                                        "databaseId:assetId": "db1:a2"})
        monkeypatch.setattr(af, "asset_file_metadata_table", tables["metadata"])
        monkeypatch.setattr(af, "file_attribute_table", tables["attributes"])
        reads = []
        real_query_all_items = af.query_all_items

        def counted(table, **kwargs):
            reads.append(kwargs.get("IndexName"))
            return real_query_all_items(table, **kwargs)
        monkeypatch.setattr(af, "query_all_items", counted)
        yield SimpleNamespace(tables=tables, reads=reads)


def _remaining(table):
    return sorted(item["databaseId:assetId:filePath"] for item in table.scan()["Items"])


@pytest.mark.unit
class TestFileRecordsAreDeletedInBatches:
    def test_the_named_files_lose_their_rows_with_one_read_per_table(self, record_tables):
        deleted = af.delete_files_metadata("db1", "a1", ["/x.txt", "sub/y.txt", "", "folder/"])

        assert deleted == 4
        assert record_tables.reads == ["DatabaseIdAssetIdIndex", "DatabaseIdAssetIdIndex"]
        assert _remaining(record_tables.tables["metadata"]) == ["db1:a1:/", "db1:a1:/sub/z.txt", "db1:a2:/x.txt"]
        assert _remaining(record_tables.tables["attributes"]) == ["db1:a1:/sub/z.txt", "db1:a2:/x.txt"]


def _api_event(path, body):
    return {
        "body": json.dumps(body), "queryStringParameters": None,
        "pathParameters": {"databaseId": "db1", "assetId": "a1"},
        "requestContext": {"requestId": "req-1",
                           "http": {"method": "DELETE", "path": path, "sourceIp": "198.51.100.7"},
                           "authorizer": {"lambda": {"vams:tokens": json.dumps(["u1"]),
                                                     "vams:roles": json.dumps(["admin"])}}},
    }


@pytest.fixture
def real_audit(monkeypatch):
    """The production entrypoint decorator, with its record writer observed."""
    audit = production_audit
    recorder = MagicMock(name="log_action")
    monkeypatch.setattr(audit, "log_action", recorder)
    monkeypatch.setattr(audit, "request_to_claims",
                        lambda event: {"tokens": ["u1"], "roles": ["admin"], "mfaEnabled": False})
    monkeypatch.setattr(audit, "cloudwatch_logs", MagicMock(name="cloudwatch_logs"))
    monkeypatch.setattr(audit, "audit_flush_client", MagicMock(name="audit_flush_client"))
    return SimpleNamespace(decorate=audit.audited_handler, recorder=recorder)


def _failed_records(recorder):
    return [(call.args[1], call.kwargs.get("outcome"), call.kwargs.get("reason"))
            for call in recorder.call_args_list]


@pytest.mark.unit
class TestTheRoutesAnswerTheRefusedDelete:
    def test_the_asset_route_answers_400_and_records_a_failed_delete(self, svc, real_audit):
        svc.faulty.deny_all_deletes = True
        handler = real_audit.decorate(svc.m.lambda_handler)

        response = handler(_api_event("/database/db1/assets/a1/deleteAsset", {"confirmPermanentDelete": True}),
                           LAMBDA_CONTEXT)

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"].endswith(ASSET_MESSAGE)
        assert _failed_records(real_audit.recorder) == [("assetDelete", "failed", "validation")]
        _assert_asset_kept(svc.m)

    def test_the_file_route_answers_400_and_records_a_failed_delete(self, files, real_audit, monkeypatch):
        monkeypatch.setattr(af, "request_to_claims", lambda event: dict(CLAIMS))
        monkeypatch.setattr(af, "CasbinEnforcer", MagicMock())
        files.faulty.deny_keys.add("proj/a/sub/z.txt")
        handler = real_audit.decorate(af.lambda_handler)

        response = handler(_api_event("/database/db1/assets/a1/deleteFile",
                                      {"filePath": "/sub/", "isPrefix": True, "confirmPermanentDelete": True}),
                           LAMBDA_CONTEXT)

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"].endswith(PREFIX_MESSAGE)
        assert _failed_records(real_audit.recorder) == [("fileDelete", "failed", "validation")]
