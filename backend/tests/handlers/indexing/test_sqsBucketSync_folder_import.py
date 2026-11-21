# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Importing a folder: the files already in it get no S3 event, so bucket sync lists them and sends
them to its own queue as ObjectCreated records of the importing asset. An `init` marker in a
first-level folder starts the import and sets the asset type. Each imported object is tagged,
recorded in the ledger and forwarded to the file-indexer topic, but starts no upload workflow; a
version the ledger already has is skipped."""

import json
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from tests.handlers.indexing.test_sqsBucketSync_location_attribution import (  # noqa: F401
    BUCKET,
    _bus_records,
    _history_rows,
    _record,
    _restore_module_attrs,
    _row,
    _sns_records,
    _sqs,
    _wire,
)
from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load

al = sys.modules['common.assetLocations']
NOT_FOUND = al.Resolution(al.RESOLVED_NOT_FOUND)

OWN_QUEUE = "https://sqs.us-east-1.amazonaws.com/123456789012/bucketSyncCreated--0"
_EXTRA = ("created_queue_url", "IMPORT_RECORDS_PER_MESSAGE", "_time_left_ms",
          "IMPORTED_FILES_START_UPLOAD_WORKFLOWS", "tag_version", "logger", "create_asset")


@pytest.fixture(autouse=True)
def _restore_extra(_restore_module_attrs):  # noqa: F811
    m = _load()
    saved = {name: getattr(m, name) for name in _EXTRA}
    yield
    for name, value in saved.items():
        setattr(m, name, value)


def _version(key, version_id="v1", latest=True):
    return {"Key": key, "VersionId": version_id, "IsLatest": latest, "Size": 3, "ETag": '"e"',
            "LastModified": datetime(2026, 1, 2, tzinfo=timezone.utc)}


def _wire_import(m, versions_pages):
    _wire(m, live=_row())
    m.created_queue_url = OWN_QUEUE
    m.RESERVED_S3_PREFIX_FOLDERS = {"preview", "previews"}
    m.tag_version = MagicMock(return_value=None)
    m.s3_client.list_object_versions.side_effect = [
        {"Versions": page, "IsTruncated": index < len(versions_pages) - 1,
         "NextKeyMarker": page[-1]["Key"] if page else None, "NextVersionIdMarker": "vm"}
        for index, page in enumerate(versions_pages)]


def _sent_bodies(m):
    return [json.loads(c.kwargs["MessageBody"]) for c in m.sqs_client.send_message.call_args_list
            if c.kwargs.get("QueueUrl") == OWN_QUEUE]


def _sent_records(m):
    records = []
    for body in _sent_bodies(m):
        if body.get("Type") == "Notification":
            records.extend(json.loads(body["Message"])["Records"])
    return records


@pytest.mark.unit
class TestAnInitMarkerImportsItsFolder:
    def test_the_other_files_are_queued_and_the_asset_type_is_set(self):
        m = _load()
        _wire_import(m, [[_version("a1/model.glb"), _version("a1/sub/part.obj"), _version("a1/sub/"),
                          _version("a1/preview/x.png"), _version("a1/old.bin", "v0", latest=False)]])
        result = m.process_s3_record(_record("a1/init"))
        assert result[:2] == (True, False)
        m.delete_s3_object.assert_called_once()
        m.update_asset_type.assert_called_once()
        records = _sent_records(m)
        assert [r["s3"]["object"]["key"] for r in records] == ["a1/model.glb", "a1/sub/part.obj"]
        assert all(r["vamsImport"] == {"databaseId": "db1", "assetId": "a1", "bucketId": "bucket-1"}
                   for r in records)
        assert all(r["eventName"] == "ObjectCreated:Put" and r["s3"]["object"]["versionId"] == "v1"
                   for r in records)

    def test_listing_pages_and_message_pages(self):
        m = _load()
        _wire_import(m, [[_version(f"a1/f{i}.bin") for i in range(3)], [_version(f"a1/g{i}.bin") for i in range(2)]])
        m.IMPORT_RECORDS_PER_MESSAGE = 2
        m.process_s3_record(_record("a1/init"))
        assert m.s3_client.list_object_versions.call_count == 2
        second = m.s3_client.list_object_versions.call_args_list[1].kwargs
        assert (second["KeyMarker"], second["VersionIdMarker"]) == ("a1/f2.bin", "vm")
        assert [len(json.loads(b["Message"])["Records"]) for b in _sent_bodies(m)] == [2, 2, 1]

    def test_a_listing_short_of_time_sends_a_continuation(self):
        m = _load()
        _wire_import(m, [[_version("a1/f0.bin")], [_version("a1/f1.bin")]])
        m._time_left_ms = lambda: 1000
        m.process_s3_record(_record("a1/init"))
        bodies = _sent_bodies(m)
        assert bodies[-1]["vamsImportRequest"]["keyMarker"] == "a1/f0.bin"
        assert bodies[-1]["vamsImportRequest"]["owner"]["assetId"] == "a1"

    def test_a_continuation_message_lists_from_its_markers(self):
        m = _load()
        _wire_import(m, [[_version("a1/f1.bin")]])
        _wire_request_checks(m)
        response = m.lambda_handler_created({"Records": [_request_message(keyMarker="a1/f0.bin")]}, MagicMock())
        assert response["batchItemFailures"] == []
        assert m.s3_client.list_object_versions.call_args.kwargs["KeyMarker"] == "a1/f0.bin"
        assert [r["s3"]["object"]["key"] for r in _sent_records(m)] == ["a1/f1.bin"]
        # A continuation does not set the asset type again.
        m.update_asset_type.assert_not_called()


OWNER = {"databaseId": "db1", "assetId": "a1", "bucketId": "bucket-1"}


def _request_message(message_id="req-1", **fields):
    request = {"bucket": BUCKET, "location": "a1/", "owner": dict(OWNER), "versionIdMarker": "vm", **fields}
    return {"messageId": message_id, "receiptHandle": "rh", "eventSource": "aws:sqs",
            "attributes": {"ApproximateReceiveCount": "1"}, "body": json.dumps({"vamsImportRequest": request})}


def _wire_request_checks(m, row=None, registration=None):
    m.dynamodb.Table.return_value.get_item.return_value = {"Item": row if row is not None else _row()}
    m.get_bucket_info_from_bucket_id = MagicMock(return_value=registration or {
        "bucketId": "bucket-1", "bucketName": BUCKET, "baseAssetsPrefix": "/"})


@pytest.mark.unit
class TestAnImportRequestFromCreateAsset:
    def test_a_valid_request_sets_the_asset_type_once_and_queues_the_files(self):
        m = _load()
        _wire_import(m, [[_version("a1/model.glb"), _version("a1/b.obj")]])
        _wire_request_checks(m)
        response = m.lambda_handler_created({"Records": [_request_message(start=True)]}, MagicMock())
        assert response["batchItemFailures"] == []
        m.update_asset_type.assert_called_once()
        assert m.update_asset_type.call_args.args == ("bucket-1", "a1", BUCKET, "a1/")
        assert [r["s3"]["object"]["key"] for r in _sent_records(m)] == ["a1/model.glb", "a1/b.obj"]
        assert m.dynamodb.Table.return_value.get_item.call_args.kwargs["ConsistentRead"] is True

    def test_a_single_file_location_imports_its_own_object_and_companions_only(self):
        m = _load()
        location = "files/model.glb"
        _wire_import(m, [[_version("files/model.glb"), _version("files/model.glb/notes.txt"),
                          _version("files/model.glb.bak")]])
        _wire_request_checks(m, row=_row(location=location))
        m.lambda_handler_created({"Records": [_request_message(start=True, location=location)]}, MagicMock())
        assert m.update_asset_type.call_args.args == ("bucket-1", "a1", BUCKET, location)
        assert [r["s3"]["object"]["key"] for r in _sent_records(m)] == ["files/model.glb", "files/model.glb/notes.txt"]

    @pytest.mark.parametrize("label,fields,row,registration", [
        ("another location", {"location": "other/"}, None, None),
        ("the bucket root", {"location": ""}, None, None),
        ("another bucket", {"bucket": "someone-elses-bucket"}, None, None),
        ("another registration", {"owner": {**OWNER, "bucketId": "bucket-2"}}, None, None),
        ("an archived partition", {"owner": {**OWNER, "databaseId": "db1#deleted"}}, None, None),
        ("no such asset", {}, {}, None),
        ("an archived asset", {}, {**_row(), "status": "archived"}, None),
        ("a registration of another bucket", {}, None,
         {"bucketId": "bucket-1", "bucketName": "other-bucket", "baseAssetsPrefix": "/"}),
        ("a registration under another prefix", {}, None,
         {"bucketId": "bucket-1", "bucketName": BUCKET, "baseAssetsPrefix": "elsewhere/"}),
        ("missing fields", {"owner": {"databaseId": "db1"}}, None, None),
    ])
    def test_a_forged_request_is_dropped_without_listing(self, label, fields, row, registration):
        m = _load()
        _wire_import(m, [[_version("a1/model.glb")]])
        _wire_request_checks(m, row=row, registration=registration)
        m.logger = MagicMock()
        response = m.lambda_handler_created({"Records": [_request_message(start=True, **fields)]}, MagicMock())
        assert response["batchItemFailures"] == [], label
        m.s3_client.list_object_versions.assert_not_called()
        m.sqs_client.send_message.assert_not_called()
        m.update_asset_type.assert_not_called()
        assert any("Import request dropped" in c.args[0] for c in m.logger.warning.call_args_list), label


def _page_message(records, message_id="page-1"):
    return {"messageId": message_id, "receiptHandle": f"rh-{message_id}", "eventSource": "aws:sqs",
            "attributes": {"ApproximateReceiveCount": "1"},
            "body": json.dumps({"Type": "Notification", "Message": json.dumps({"Records": records})})}


def _imported(key, version_id="v1"):
    m = _load()
    return m.import_record(BUCKET, _version(key, version_id),
                           {"databaseId": "db1", "assetId": "a1", "bucketId": "bucket-1"})


@pytest.mark.unit
class TestImportedObjects:
    def _wire_processing(self, m, synced=False):
        _wire(m, live=_row())
        m.tag_version = MagicMock(return_value=None)
        m.dynamodb.Table.return_value.get_item.return_value = {"Item": _row()}
        m.asset_file_version_history_table.get_item.return_value = {"Item": {"versionId": "v1"}} if synced else {}

    def test_an_imported_object_is_recorded_tagged_and_indexed_without_an_upload_event(self):
        m = _load()
        self._wire_processing(m)
        response = m.lambda_handler_created({"Records": [_page_message([_imported("a1/sub/model.glb")])]},
                                            MagicMock())
        assert response["batchItemFailures"] == []
        [row] = _history_rows(m)
        assert (row["filePath"], row["versionId"], row["resolvedBy"]) == ("/sub/model.glb", "v1", "import")
        m.tag_version.assert_called_once()
        [forwarded] = _sns_records(m)
        assert forwarded["vamsBinding"]["resolvedBy"] == "import"
        assert _bus_records(m) == []
        m.resolve_owner.assert_not_called()
        m.create_new_asset.assert_not_called()

    def test_the_switch_point_starts_upload_workflows_too(self):
        m = _load()
        self._wire_processing(m)
        m.IMPORTED_FILES_START_UPLOAD_WORKFLOWS = True
        m.lambda_handler_created({"Records": [_page_message([_imported("a1/model.glb")])]}, MagicMock())
        assert len(_bus_records(m)) == 1

    def test_a_version_the_ledger_has_is_skipped(self):
        m = _load()
        self._wire_processing(m, synced=True)
        m.lambda_handler_created({"Records": [_page_message([_imported("a1/model.glb")])]}, MagicMock())
        assert _history_rows(m) == []
        m.tag_version.assert_not_called()
        assert _sns_records(m) == []

    def test_an_object_the_asset_no_longer_holds_is_skipped(self):
        m = _load()
        self._wire_processing(m)
        m.dynamodb.Table.return_value.get_item.return_value = {"Item": _row(location="other/")}
        m.lambda_handler_created({"Records": [_page_message([_imported("a1/model.glb")])]}, MagicMock())
        assert _history_rows(m) == [] and _sns_records(m) == []

    def test_a_key_with_a_plus_and_a_space_round_trips(self):
        m = _load()
        self._wire_processing(m)
        record = _imported("a1/A+B c.glb")
        assert record["s3"]["object"]["key"] == "a1/A%2BB+c.glb"
        m.lambda_handler_created({"Records": [_page_message([record])]}, MagicMock())
        assert m.s3_client.head_object.call_args.kwargs["Key"] == "a1/A+B c.glb"


@pytest.mark.unit
class TestARefusedAutoCreateIsNotAnError:
    """An auto-create refused because another asset's location already holds the folder is expected:
    no ERROR line from bucket sync, the AutoCreateRefused count, and the record still forwarded."""

    def _metric_lines(self, out):
        return [json.loads(line) for line in out.splitlines() if line.startswith('{"_aws"')]

    def test_a_refusal_from_create_asset(self, capsys, _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire(m)
        m.logger = MagicMock()
        m.create_new_asset = _restore_module_attrs["create_new_asset"]
        m.get_or_create_database_for_bucket = MagicMock(return_value="testdb1")
        m.resolve_owner = MagicMock(return_value=NOT_FOUND)
        m.create_asset = MagicMock(side_effect=m.AssetLocationInUseError(
            "The specified bucketExistingKey is already in use by another asset", owner="db2:o1 at a1/sub/"))
        capsys.readouterr()
        result = m.process_s3_record(_record("a1/model.glb"))
        assert result == (False, True, "Auto-create of asset a1 refused")
        m.logger.error.assert_not_called()
        m.logger.exception.assert_not_called()
        m.logger.warning.assert_not_called()
        metrics = self._metric_lines(capsys.readouterr().out)
        assert [(x["Reason"], x["AutoCreateRefused"]) for x in metrics] == [("locationInUse", 1)]
        assert metrics[0]["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "VAMS/BucketSync"

    def test_a_refusal_by_the_ready_index_is_one_warning(self, capsys):
        m = _load()
        owner = al.LocationEntry(database_id="db2", asset_id="o1", bucket_id="bucket-1", location="a1/sub/")
        _wire(m, resolve=[NOT_FOUND, NOT_FOUND], ready=True, conflicts=[owner])
        m.logger = MagicMock()
        capsys.readouterr()
        result = m.process_s3_record(_record("a1/model.glb"))
        assert result == (False, True, "Auto-create of asset a1 refused")
        m.logger.error.assert_not_called()
        assert m.logger.warning.call_count == 1
        assert "db2:o1" in m.logger.warning.call_args.args[0]
        metrics = self._metric_lines(capsys.readouterr().out)
        assert [x["AutoCreateRefused"] for x in metrics] == [1]

    def test_many_refused_objects_log_no_error_lines(self, capsys, _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire(m)
        m.logger = MagicMock()
        m.create_new_asset = _restore_module_attrs["create_new_asset"]
        m.get_or_create_database_for_bucket = MagicMock(return_value="testdb1")
        m.resolve_owner = MagicMock(return_value=NOT_FOUND)
        m.create_asset = MagicMock(side_effect=m.AssetLocationInUseError("in use", owner="db2:o1 at a1/sub/"))
        records = [_sqs(f"msg-{i}", _record(f"a1/f{i}.glb")) for i in range(25)]
        capsys.readouterr()
        response = m.lambda_handler_created({"Records": records}, MagicMock())
        assert response["batchItemFailures"] == []
        m.logger.error.assert_not_called()
        m.logger.exception.assert_not_called()
        assert len(self._metric_lines(capsys.readouterr().out)) == 25
