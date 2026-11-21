# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bucket sync attributes a file by the asset whose stored location contains it.

Normal assets keep the first-folder fast path. Any other stored location is found through the
location index; a miss there is only trusted once the index is ready. Every forwarded record carries
the answer as `vamsBinding`, and the history row of the event's own version records `etag`, `size`
and `resolvedBy`.
"""

import json
import sys
from unittest.mock import MagicMock

import pytest

from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load

al = sys.modules['common.assetLocations']

BUCKET = "asset-bucket"
QUEUE_ARN = "arn:aws:sqs:us-east-1:123456789012:bucketSyncCreated--0"
REJECTED_URL = "https://sqs.us-east-1.amazonaws.com/123456789012/rejected"

_PATCHED_ATTRS = (
    "asset_bucket_name", "asset_bucket_prefix", "RESERVED_S3_PREFIX_FOLDERS", "get_bucket_id",
    "validate_asset_id", "lookup_asset", "lookup_archived_asset", "restore_archived_asset",
    "get_or_create_database_for_bucket", "create_new_asset", "object_still_exists",
    "tag_version", "update_asset_type", "s3_client", "asset_file_version_history_table",
    "resolve_owner", "index_ready", "find_conflicts", "sqs_client", "rejected_queue_url",
    "sns_client", "file_indexer_sns_topic_arn", "events_client", "orchestration_bus_name",
    "orchestration_event_source_prefix", "dynamodb", "is_versioning_enabled", "delete_s3_object",
    "lookup_databases", "get_bucket_info_from_bucket_id", "create_new_database",
    "lookup_archived_database", "database_id", "is_object_permanently_deleted",
    "delete_file_metadata_on_s3_delete", "determine_asset_type", "_update_asset_type_attribute",
    "SNS_MESSAGE_MAX_BYTES", "EVENTBRIDGE_ENTRY_MAX_BYTES", "PUBLISH_SIZE_MARGIN_BYTES",
)


def _tagged(m):
    """(bucket, key, databaseId, assetId) of each VAMS tag write bucket sync asked for."""
    return [(c.args[1], c.args[2], c.args[4], c.args[5]) for c in m.tag_version.call_args_list]


@pytest.fixture(autouse=True)
def _restore_module_attrs():
    m = _load()
    saved = {name: getattr(m, name) for name in _PATCHED_ATTRS}
    m.database_cache.clear()
    m._queue_urls.clear()
    yield saved
    for name, value in saved.items():
        setattr(m, name, value)
    m.database_cache.clear()
    m._queue_urls.clear()


def _row(database_id="db1", asset_id="a1", location="a1/"):
    return {"databaseId": database_id, "assetId": asset_id, "bucketId": "bucket-1",
            "assetLocation": {"Key": location}, "assetType": ".glb"}


def _entry(database_id="db1", asset_id="owner-1", location="projects/alpha/"):
    return al.LocationEntry(database_id=database_id, asset_id=asset_id, bucket_id="bucket-1",
                            location=location)


def _found(entry):
    return al.Resolution(al.RESOLVED_FOUND, entry=entry, row=_row(entry.database_id, entry.asset_id, entry.location))


NOT_FOUND = al.Resolution(al.RESOLVED_NOT_FOUND)


def _wire(m, live=None, resolve=(NOT_FOUND,), ready=False, conflicts=()):
    m.asset_bucket_name = BUCKET
    m.asset_bucket_prefix = ""
    m.RESERVED_S3_PREFIX_FOLDERS = set()
    m.get_bucket_id = MagicMock(return_value="bucket-1")
    m.validate_asset_id = MagicMock(return_value=True)
    m.lookup_asset = MagicMock(return_value=live)
    m.lookup_archived_asset = MagicMock(return_value=None)
    m.restore_archived_asset = MagicMock(return_value="db1")
    m.get_or_create_database_for_bucket = MagicMock(return_value="db1")
    m.create_new_asset = MagicMock(return_value="a1")
    m.object_still_exists = MagicMock(return_value=True)
    m.tag_version = MagicMock(return_value=None)
    m.update_asset_type = MagicMock(return_value=True)
    m.is_versioning_enabled = MagicMock(return_value=True)
    m.delete_s3_object = MagicMock(return_value=True)
    # The archived-owner lookup that follows a live miss reads the index too (an index hit counts at any time)
    m.resolve_owner = MagicMock(side_effect=list(resolve) + [NOT_FOUND] * 4)
    m.index_ready = MagicMock(return_value=ready)
    m.find_conflicts = MagicMock(return_value=list(conflicts))
    m.dynamodb = MagicMock()
    m.s3_client = MagicMock()
    m.s3_client.head_object.side_effect = lambda **kw: {
        "VersionId": kw.get("VersionId", "v-current"), "Metadata": {}, "LastModified": None,
        "ETag": '"etag-1"', "ContentLength": 12}
    m.s3_client.list_objects_v2.return_value = {"Contents": []}
    m.asset_file_version_history_table = MagicMock()
    m.sqs_client = MagicMock()
    m.sqs_client.get_queue_url.return_value = {"QueueUrl": "https://sqs.example/q"}
    m.rejected_queue_url = REJECTED_URL
    m.sns_client = MagicMock()
    m.sns_client.publish.return_value = {"MessageId": "sns-1"}
    m.file_indexer_sns_topic_arn = "arn:aws:sns:us-east-1:123456789012:file-indexer"
    m.events_client = MagicMock()
    m.events_client.put_events.return_value = {"Entries": [{"EventId": "e1"}]}
    m.orchestration_bus_name = "bus"
    m.orchestration_event_source_prefix = "vams"


def _record(key, version_id=None, event_name="ObjectCreated:Put"):
    obj = {"key": key, "size": 12}
    if version_id:
        obj["versionId"] = version_id
    return {"eventSource": "aws:s3", "eventName": event_name,
            "s3": {"bucket": {"name": BUCKET}, "object": obj}}


def _sqs(message_id, record, receive_count=1):
    return {"messageId": message_id, "receiptHandle": f"rh-{message_id}", "eventSource": "aws:sqs",
            "eventSourceARN": QUEUE_ARN, "attributes": {"ApproximateReceiveCount": str(receive_count)},
            "body": json.dumps({"Type": "Notification", "Message": json.dumps({"Records": [record]})})}


def _history_rows(m):
    return [c.kwargs["Item"] for c in m.asset_file_version_history_table.put_item.call_args_list]


def _sns_records(m):
    records = []
    for c in m.sns_client.publish.call_args_list:
        for sqs_record in json.loads(c.kwargs["Message"])["Records"]:
            records.extend(json.loads(json.loads(sqs_record["body"])["Message"])["Records"])
    return records


def _as_set(records):
    """Records as a set of canonical JSON strings, for a comparison that does not pin their order."""
    return {json.dumps(r, sort_keys=True) for r in records}


def _bus_records(m):
    records = []
    for c in m.events_client.put_events.call_args_list:
        records.extend(json.loads(c.kwargs["Entries"][0]["Detail"])["Records"])
    return records


@pytest.mark.unit
class TestNormalAssetParity:
    def test_a_direct_write_into_a_normal_asset_is_unchanged_except_the_added_fields(self):
        m = _load()
        _wire(m, live=_row())
        record = _record("a1/sub/model.glb")

        response = m.lambda_handler_created({"Records": [_sqs("msg-1", record)]}, MagicMock())

        assert response["batchItemFailures"] == []
        m.resolve_owner.assert_not_called()
        assert (BUCKET, "a1/sub/model.glb", "db1", "a1") in _tagged(m)
        assert m.update_asset_type.call_args.args == ("bucket-1", "a1", BUCKET, "a1/")
        [row] = _history_rows(m)
        assert row["databaseId:assetId:filePath"] == "db1:a1:/sub/model.glb"
        assert (row["versionId"], row["changeSource"], row["filePath"]) == ("v-current", "direct", "/sub/model.glb")
        assert (row["etag"], row["size"], row["resolvedBy"]) == ("etag-1", 12, "fastPath")
        binding = {"databaseId": "db1", "assetId": "a1", "bucketId": "bucket-1",
                   "relativePath": "/sub/model.glb", "resolvedBy": "fastPath"}
        # Forwarded records are the received ones plus vamsBinding, on the topic and on the bus.
        assert _as_set(_sns_records(m)) == _as_set([{**record, "vamsBinding": binding}])
        assert _as_set(_bus_records(m)) == _as_set([{**record, "vamsBinding": binding}])

    def test_an_init_marker_in_a_first_level_folder_is_still_deleted(self):
        m = _load()
        _wire(m, live=_row())
        assert m.process_s3_record(_record("a1/init"), binding_out={}) == (
            True, False, "Deleted init object a1/init; queued 0 existing files")
        assert m.delete_s3_object.called


@pytest.mark.unit
class TestCustomRootLocation:
    KEY = "projects/alpha/sub/x.glb"

    @pytest.mark.parametrize("ready", [False, True])
    def test_resolved_through_the_index_with_and_without_the_marker(self, ready):
        m = _load()
        _wire(m, resolve=[_found(_entry())], ready=ready)
        binding = {}
        assert m.process_s3_record(_record(self.KEY, "v7"), binding_out=binding)[:2] == (True, True)
        assert binding == {"databaseId": "db1", "assetId": "owner-1", "bucketId": "bucket-1",
                           "relativePath": "/sub/x.glb", "resolvedBy": "locationIndex"}
        assert (BUCKET, self.KEY, "db1", "owner-1") in _tagged(m)
        assert m.update_asset_type.call_args.args == ("bucket-1", "owner-1", BUCKET, "projects/alpha/")
        [row] = _history_rows(m)
        assert (row["filePath"], row["versionId"], row["resolvedBy"]) == ("/sub/x.glb", "v7", "locationIndex")
        m.create_new_asset.assert_not_called()
        m.get_or_create_database_for_bucket.assert_not_called()

    def test_a_first_folder_hit_that_does_not_hold_the_key_resolves_by_location(self):
        # Asset `projects` exists but is stored at another location: the first folder is not its owner.
        m = _load()
        _wire(m, live=_row(asset_id="projects", location="elsewhere/"), resolve=[_found(_entry())])
        binding = {}
        m.process_s3_record(_record(self.KEY), binding_out=binding)
        assert binding["assetId"] == "owner-1"

    def test_ambiguous_goes_to_the_rejected_queue_and_attributes_nothing(self):
        m = _load()
        two = (_entry(asset_id="o1"), _entry(database_id="db2", asset_id="o2"))
        _wire(m, resolve=[al.Resolution(al.RESOLVED_AMBIGUOUS, candidates=two)])
        binding = {}
        result = m.process_s3_record(_record(self.KEY), binding_out=binding)
        assert result == (True, False, f"Rejected {self.KEY}: ambiguousLocation")
        body = json.loads(m.sqs_client.send_message.call_args.kwargs["MessageBody"])
        assert m.sqs_client.send_message.call_args.kwargs["QueueUrl"] == REJECTED_URL
        assert (body["reason"], body["bucket"], body["key"]) == ("ambiguousLocation", BUCKET, self.KEY)
        assert body["candidateAssets"] == ["db1:o1", "db2:o2"]
        assert body["record"]["s3"]["object"]["key"] == self.KEY
        assert binding == {}
        m.tag_version.assert_not_called()
        m.update_asset_type.assert_not_called()
        m.asset_file_version_history_table.put_item.assert_not_called()

    def test_a_rejected_record_the_queue_did_not_accept_is_received_again(self):
        m = _load()
        _wire(m, resolve=[al.Resolution(al.RESOLVED_AMBIGUOUS, candidates=(_entry(), _entry(asset_id="o2")))])
        m.sqs_client.send_message.side_effect = RuntimeError("throttled")
        response = m.lambda_handler_created({"Records": [_sqs("msg-9", _record(self.KEY))]}, MagicMock())
        assert response["batchItemFailures"] == [{"itemIdentifier": "msg-9"}]
        m.sqs_client.change_message_visibility.assert_not_called()
        m.sns_client.publish.assert_not_called()

    def test_an_object_named_init_inside_a_custom_root_location_is_an_ordinary_file(self):
        m = _load()
        _wire(m, resolve=[_found(_entry())])
        assert m.process_s3_record(_record("projects/alpha/init"), binding_out={})[:2] == (True, True)
        m.delete_s3_object.assert_not_called()
        assert m.tag_version.called

    def test_an_init_marker_restoring_a_first_level_asset_through_the_index_is_still_deleted(self):
        m = _load()
        archived = _entry(database_id="db1#deleted", asset_id="a1", location="a1/")
        _wire(m, resolve=[NOT_FOUND, _found(archived)], ready=True)
        assert m.process_s3_record(_record("a1/init"), binding_out={}) == (
            True, False, "Deleted init object a1/init; queued 0 existing files")
        assert m.restore_archived_asset.called
        assert m.delete_s3_object.called

    def test_an_archived_owner_is_restored_by_location_once_ready(self):
        m = _load()
        archived = _entry(database_id="db1#deleted")
        _wire(m, resolve=[NOT_FOUND, _found(archived)], ready=True)
        binding = {}
        m.process_s3_record(_record(self.KEY), binding_out=binding)
        assert m.resolve_owner.call_args_list[1].kwargs["include_archived"] is True
        restore = m.restore_archived_asset.call_args.args
        assert restore[:2] == ("bucket-1", "owner-1")
        m.lookup_archived_asset.assert_not_called()
        m.create_new_asset.assert_not_called()
        assert binding["databaseId"] == "db1" and binding["resolvedBy"] == "locationIndex"

    def test_before_the_index_is_ready_a_miss_keeps_the_first_folder_logic(self):
        m = _load()
        _wire(m, resolve=[NOT_FOUND], ready=False)
        m.process_s3_record(_record("newfolder/file.glb"), binding_out={})
        m.lookup_archived_asset.assert_any_call("bucket-1", "newfolder")
        m.find_conflicts.assert_not_called()
        m.create_new_asset.assert_any_call("bucket-1", "db1", "newfolder")


@pytest.mark.unit
class TestAutoCreateOnceIndexReady:
    KEY = "newfolder/file.glb"

    def test_refused_when_the_location_overlaps_an_asset(self):
        m = _load()
        _wire(m, resolve=[NOT_FOUND, NOT_FOUND], ready=True, conflicts=[_entry(location="newfolder/inner/")])
        success, should_index, message = m.process_s3_record(_record(self.KEY), receive_count=1)
        assert (success, should_index) == (False, True)
        assert message == "Auto-create of asset newfolder refused"
        assert (BUCKET, "newfolder/") in {c.args[1:] for c in m.find_conflicts.call_args_list}
        m.get_or_create_database_for_bucket.assert_not_called()
        m.create_new_asset.assert_not_called()

    def test_deferred_once_when_the_folder_holds_other_objects_then_created(self):
        m = _load()
        _wire(m, resolve=[NOT_FOUND, NOT_FOUND], ready=True)
        m.s3_client.list_objects_v2.return_value = {"Contents": [{"Key": "newfolder/other.glb"}]}

        response = m.lambda_handler_created({"Records": [_sqs("msg-1", _record(self.KEY), 1)]}, MagicMock())

        assert response["batchItemFailures"] == [{"itemIdentifier": "msg-1"}]
        m.sqs_client.change_message_visibility.assert_any_call(
            QueueUrl="https://sqs.example/q", ReceiptHandle="rh-msg-1", VisibilityTimeout=10)
        m.sqs_client.get_queue_url.assert_any_call(QueueName="bucketSyncCreated--0",
                                                   QueueOwnerAWSAccountId="123456789012")
        m.create_new_asset.assert_not_called()
        m.sns_client.publish.assert_not_called()

        # The next receive resolves again, without the container cache, and creates the asset.
        m.resolve_owner = MagicMock(side_effect=[NOT_FOUND, NOT_FOUND])
        response = m.lambda_handler_created({"Records": [_sqs("msg-1", _record(self.KEY), 2)]}, MagicMock())
        assert response["batchItemFailures"] == []
        assert m.resolve_owner.call_args_list, "the redelivered record was not resolved"
        assert all(c.kwargs["use_cache"] is False for c in m.resolve_owner.call_args_list)
        m.create_new_asset.assert_any_call("bucket-1", "db1", "newfolder")
        assert {r["vamsBinding"]["resolvedBy"] for r in _sns_records(m)} == {"fastPath"}

    def test_an_empty_folder_is_created_without_deferral(self):
        m = _load()
        _wire(m, resolve=[NOT_FOUND, NOT_FOUND], ready=True)
        m.s3_client.list_objects_v2.return_value = {"Contents": [{"Key": self.KEY}]}
        assert m.process_s3_record(_record(self.KEY), receive_count=1)[:2] == (True, True)
        assert m.create_new_asset.called


@pytest.mark.unit
class TestDatabaseForAutoCreatedAssets:
    def test_the_assigned_database_rule(self):
        m = _load()
        m.database_id = "default-db"
        m.get_bucket_info_from_bucket_id = MagicMock(
            return_value={"bucketName": BUCKET, "baseAssetsPrefix": "/"})
        m.lookup_archived_database = MagicMock(return_value=None)
        m.create_new_database = MagicMock(side_effect=lambda bucket_id, db_id: db_id)
        m.dynamodb = MagicMock()
        m.dynamodb.Table.return_value.get_item.return_value = {}
        auto_db = m.get_or_create_database_for_bucket.__globals__["hashlib"].md5(
            (BUCKET + ":root").encode()).hexdigest()[:8]
        auto_db = f"default-db-{auto_db}"

        def choose(*assigned):
            m.database_cache.clear()
            m.lookup_databases = MagicMock(return_value=[
                {"databaseId": d, "defaultBucketId": "bucket-1"} for d in assigned])
            return m.get_or_create_database_for_bucket("bucket-1", BUCKET, "")

        assert choose() == auto_db
        assert choose("team-db") == "team-db"
        assert choose("default-db") == "default-db"
        assert choose("team-db", "default-db", "other-db") == "default-db"
        # Several assigned without the default: the bucket's auto-generated database, never the first.
        assert choose("team-db", "other-db") == auto_db

    def test_an_archived_auto_generated_database_is_served_from_the_cache(self):
        m = _load()
        m.database_id = "default-db"
        m.lookup_databases = MagicMock(return_value=[])
        m.lookup_archived_database = MagicMock(return_value={"databaseId": "x#deleted"})
        m.create_new_database = MagicMock()
        m.dynamodb = MagicMock()
        m.dynamodb.Table.return_value.get_item.return_value = {}
        assert m.get_or_create_database_for_bucket("bucket-1", BUCKET, "") is None
        assert m.get_or_create_database_for_bucket("bucket-1", BUCKET, "") is None
        # The second miss is served from the cache: at most one read of each table.
        assert m.lookup_databases.called and m.lookup_databases.call_count <= 1
        assert m.lookup_archived_database.called and m.lookup_archived_database.call_count <= 1
        m.create_new_database.assert_not_called()


@pytest.mark.unit
class TestVersionExactHistory:
    def test_two_writes_to_one_key_give_two_rows_with_their_own_versions(self):
        m = _load()
        _wire(m, live=_row())
        records = [_record("a1/model.glb", "v1"), _record("a1/model.glb", "v2")]
        m.lambda_handler_created({"Records": [_sqs("m1", records[0]), _sqs("m2", records[1])]}, MagicMock())
        heads = {(c.kwargs.get("Key"), c.kwargs.get("VersionId")) for c in m.s3_client.head_object.call_args_list}
        assert {("a1/model.glb", "v1"), ("a1/model.glb", "v2")} <= heads
        rows = _history_rows(m)
        assert rows, "no history row was written"
        assert {r["versionId"] for r in rows} == {"v1", "v2"}
        assert all((r["etag"], r["size"], r["resolvedBy"]) == ("etag-1", 12, "fastPath") for r in rows)


def _deleted(key):
    return {"eventSource": "aws:s3", "eventName": "ObjectRemoved:Delete",
            "s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}


@pytest.mark.unit
class TestDeletedPath:
    def _wire_deleted(self, m, live=None, resolve=(NOT_FOUND,)):
        _wire(m, live=live, resolve=resolve)
        m.is_object_permanently_deleted = MagicMock(return_value=True)
        m.delete_file_metadata_on_s3_delete = MagicMock()

    @pytest.mark.parametrize("live", [_row(), None])
    def test_one_asset_table_read_per_event(self, live, _restore_module_attrs):
        m = _load()
        self._wire_deleted(m, live=live)
        # The real asset type update: it must take the row it is given rather than read it again.
        m.update_asset_type = _restore_module_attrs["update_asset_type"]
        m.determine_asset_type = MagicMock(return_value=".glb")
        m._update_asset_type_attribute = MagicMock(return_value=True)
        m.lambda_handler_deleted({"Records": [_deleted("a1/model.glb")]}, MagicMock())
        # One read of the asset table per event: the asset type update takes the row it is given.
        assert m.lookup_asset.called and m.lookup_asset.call_count <= 1

    def test_custom_root_metadata_cleanup_uses_the_location_relative_path(self):
        m = _load()
        self._wire_deleted(m, resolve=[_found(_entry())])
        m.lambda_handler_deleted({"Records": [_sqs("d1", _deleted("projects/alpha/sub/x.glb"))]}, MagicMock())
        assert m.resolve_owner.call_args.kwargs["include_archived"] is True
        m.delete_file_metadata_on_s3_delete.assert_any_call("db1", "owner-1", "/sub/x.glb")
        assert m.update_asset_type.call_args.args == ("bucket-1", "owner-1", BUCKET, "projects/alpha/")
        [forwarded] = _sns_records(m)
        assert forwarded["vamsBinding"] == {"databaseId": "db1", "assetId": "owner-1", "bucketId": "bucket-1",
                                            "relativePath": "/sub/x.glb", "resolvedBy": "locationIndex"}

    def test_an_archived_owner_cleans_up_under_its_live_database_and_keeps_its_type(self):
        m = _load()
        self._wire_deleted(m, resolve=[_found(_entry(database_id="db1#deleted"))])
        m.lambda_handler_deleted({"Records": [_deleted("projects/alpha/x.glb")]}, MagicMock())
        m.delete_file_metadata_on_s3_delete.assert_called_once_with("db1", "owner-1", "/x.glb")
        m.update_asset_type.assert_not_called()


@pytest.mark.unit
class TestPublishSizeLimits:
    def test_the_topic_message_is_split_when_bindings_would_exceed_it(self):
        m = _load()
        _wire(m, live=_row())
        m.SNS_MESSAGE_MAX_BYTES = 2500
        m.PUBLISH_SIZE_MARGIN_BYTES = 0
        m.EVENTBRIDGE_ENTRY_MAX_BYTES = 700
        records = [_record(f"a1/file-{i}.glb") for i in range(4)]
        m.lambda_handler_created({"Records": [_sqs(f"m{i}", r) for i, r in enumerate(records)]}, MagicMock())
        expected_keys = {r["s3"]["object"]["key"] for r in records}
        published = m.sns_client.publish.call_args_list
        assert len(published) > 1, "the records were not split across messages"
        assert all(len(c.kwargs["Message"].encode()) <= 2500 for c in published)
        assert {r["s3"]["object"]["key"] for r in _sns_records(m)} == expected_keys
        assert m.events_client.put_events.call_count > 1
        bus_records = _bus_records(m)
        assert {r["s3"]["object"]["key"] for r in bus_records} == expected_keys
        assert all(r["vamsBinding"]["assetId"] == "a1" for r in bus_records)


PAGE_LIMIT = RuntimeError("Location index query did not finish within 5 pages")
TOO_DEEP = al.Resolution(al.RESOLVED_TOO_DEEP)


@pytest.mark.unit
class TestAKeyTheIndexCannotAnswerFor:
    """A key with more candidate locations than the index resolves, or a lookup that does not finish
    within its page limit, is attributed as before the index: by its first folder only."""

    KEY = "newfolder/file.glb"

    @pytest.mark.parametrize("answer", [TOO_DEEP, PAGE_LIMIT], ids=["tooDeep", "pageLimit"])
    def test_the_created_path_decides_by_the_first_folder(self, answer):
        m = _load()
        _wire(m, resolve=[answer], ready=True, conflicts=[_entry(location="newfolder/inner/")])
        result = m.process_s3_record(_record(self.KEY), receive_count=1)
        assert result[:2] == (True, True)
        assert m.resolve_owner.call_count == 1
        m.lookup_archived_asset.assert_called_once_with("bucket-1", "newfolder")
        m.find_conflicts.assert_not_called()
        m.create_new_asset.assert_any_call("bucket-1", "db1", "newfolder")

    @pytest.mark.parametrize("error", [al.LocationTooDeepError("Location has 70 candidate locations"), PAGE_LIMIT],
                             ids=["tooDeep", "pageLimit"])
    def test_an_auto_create_check_the_index_cannot_answer_leaves_the_decision_to_create_asset(self, error):
        m = _load()
        _wire(m)
        m.find_conflicts = MagicMock(side_effect=error)
        assert m.auto_create_refusal(MagicMock(), BUCKET, "newfolder/") is None

    def test_the_deleted_path_forwards_the_record_without_a_cleanup(self):
        m = _load()
        _wire(m, resolve=[PAGE_LIMIT])
        m.is_object_permanently_deleted = MagicMock(return_value=True)
        m.delete_file_metadata_on_s3_delete = MagicMock()
        m.lambda_handler_deleted({"Records": [_sqs("d1", _deleted("projects/alpha/x.glb"))]}, MagicMock())
        m.delete_file_metadata_on_s3_delete.assert_not_called()
        assert [r["s3"]["object"]["key"] for r in _sns_records(m)] == ["projects/alpha/x.glb"]


@pytest.mark.unit
class TestARegistrationRootAsset:
    """A legacy asset stored at its registration's prefix holds every key under it. With its row in the
    location index (the real `resolve_owner` and `find_conflicts` over an in-memory index), a direct write
    under it that the fast path misses is attributed to it, and an auto-create of a folder under it is
    refused. An asset at the bucket root holds no key and is not in the index, so it blocks nothing."""

    def _wire_index(self, m, rows):
        from tests.common.fileAttributionFakes import AttributionAssetTable
        _wire(m, ready=True)
        m.asset_bucket_prefix = "assets/"
        table = AttributionAssetTable(rows)
        m.dynamodb.Table.return_value = table
        m.resolve_owner = al.resolve_owner
        m.find_conflicts = al.find_conflicts
        al.clear_location_cache()
        return table

    def _root(self, location="assets/"):
        from tests.common.fileAttributionFakes import asset_row
        return asset_row("db1", "root", "bucket-1", location, BUCKET)

    def test_a_direct_write_under_it_is_attributed_to_it(self):
        m = _load()
        self._wire_index(m, [self._root()])
        result = m.process_s3_record(_record("assets/x/model.glb"), receive_count=1)
        assert result[:2] == (True, True)
        m.create_new_asset.assert_not_called()
        [row] = _history_rows(m)
        assert (row["databaseId:assetId:filePath"], row["resolvedBy"]) == ("db1:root:/x/model.glb", "locationIndex")
        assert _tagged(m) == [(BUCKET, "assets/x/model.glb", "db1", "root")]

    def test_an_auto_create_of_a_folder_under_it_is_refused(self):
        m = _load()
        table = self._wire_index(m, [self._root()])
        refusal = m.auto_create_refusal(table, BUCKET, "assets/x/")
        assert refusal is not None and "db1:root at assets/" in refusal

    def test_a_bucket_root_asset_is_not_indexed_and_refuses_nothing(self):
        m = _load()
        root = self._root(location="")
        assert al.LOCATION_KEY_ATTRIBUTE not in root
        table = self._wire_index(m, [root])
        assert m.auto_create_refusal(table, BUCKET, "assets/x/") is None


@pytest.mark.unit
class TestAnArchivedOwnerGoneSinceTheLookup:
    """#1799 with attribution by location: the index names an archived owner whose record is gone by the
    time it is restored. The key is then handled like any other key with no asset, under its first
    folder, and nothing is filed under the gone asset."""

    def test_the_key_falls_back_to_its_first_folder(self):
        m = _load()
        gone = _entry(database_id="db1#deleted", asset_id="owner-1", location="projects/alpha/")
        _wire(m, resolve=[NOT_FOUND, _found(gone)], ready=True)
        m.restore_archived_asset = MagicMock(return_value=m.ARCHIVED_RECORD_GONE)
        response = m.lambda_handler_created({"Records": [_sqs("msg-1", _record("projects/alpha/x.glb"), 2)]},
                                            MagicMock())
        assert response["batchItemFailures"] == []
        m.create_new_asset.assert_called_once_with("bucket-1", "db1", "projects")
        [forwarded] = _sns_records(m)
        assert (forwarded["vamsBinding"]["assetId"], forwarded["vamsBinding"]["resolvedBy"]) == (
            "projects", "fastPath")
        assert [(key, asset) for _bucket, key, _db, asset in _tagged(m)] == [("projects/alpha/x.glb", "projects")]


@pytest.mark.unit
class TestAnArchivedOwnerHitBeforeTheIndexIsReady:
    """An index hit counts at any time, an archived owner's included: before the backfill a direct write into
    an archived custom-root asset's folder restores that asset and is recorded, tagged and bound to it, as the
    indexers (which read the same index) attribute it. Only a miss waits for readiness."""

    def test_the_archived_custom_root_asset_is_restored_and_the_file_recorded(self):
        m = _load()
        archived = _entry(database_id="db1#deleted", asset_id="owner-1", location="projects/alpha/")
        _wire(m, resolve=[NOT_FOUND, _found(archived)], ready=False)
        response = m.lambda_handler_created({"Records": [_sqs("msg-1", _record("projects/alpha/x.glb"))]},
                                            MagicMock())
        assert response["batchItemFailures"] == []
        assert m.restore_archived_asset.call_args.args[:2] == ("bucket-1", "owner-1")
        m.lookup_archived_asset.assert_not_called()
        m.create_new_asset.assert_not_called()
        [row] = _history_rows(m)
        assert (row["databaseId:assetId:filePath"], row["resolvedBy"]) == ("db1:owner-1:/x.glb", "locationIndex")
        assert _tagged(m) == [(BUCKET, "projects/alpha/x.glb", "db1", "owner-1")]
        [forwarded] = _sns_records(m)
        assert (forwarded["vamsBinding"]["assetId"], forwarded["vamsBinding"]["databaseId"]) == ("owner-1", "db1")

    def test_a_miss_before_readiness_still_uses_the_first_folder(self):
        m = _load()
        _wire(m, resolve=[NOT_FOUND, NOT_FOUND], ready=False)
        m.process_s3_record(_record("newfolder/file.glb"))
        m.lookup_archived_asset.assert_called_once_with("bucket-1", "newfolder")


@pytest.mark.unit
class TestOneAutoCreateRefusedDatapointPerRecord:
    """A deferred auto-create that is refused when the record comes back counts the refusal once."""

    def test_deferral_then_refusal_writes_one_metric_line(self, capsys, _restore_module_attrs):
        m = _load()
        _wire(m, resolve=[NOT_FOUND, NOT_FOUND], ready=True)
        m.s3_client.list_objects_v2.return_value = {"Contents": [{"Key": "newfolder/other.glb"}]}
        m.create_new_asset = MagicMock(side_effect=m.AssetLocationInUseError("in use", owner="db2:o1 at newfolder/"))
        capsys.readouterr()
        first = m.lambda_handler_created({"Records": [_sqs("msg-1", _record("newfolder/f.glb"), 1)]}, MagicMock())
        assert first["batchItemFailures"] == [{"itemIdentifier": "msg-1"}]
        m.resolve_owner = MagicMock(side_effect=[NOT_FOUND, NOT_FOUND])
        second = m.lambda_handler_created({"Records": [_sqs("msg-1", _record("newfolder/f.glb"), 2)]}, MagicMock())
        assert second["batchItemFailures"] == []
        lines = [line for line in capsys.readouterr().out.splitlines() if '"AutoCreateRefused"' in line]
        assert len(lines) == 1
