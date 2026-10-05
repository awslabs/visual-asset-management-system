# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Garnet file indexer classifies an S3 event by the key's current state, not by a HEAD error.

`handle_s3_notification` read the asset and database ids from a HeadObject on the key's current
version and acknowledged the record on any `ClientError`. A HeadObject on a key whose current version
is a delete marker (an archive) answers 404, so every file archive was acknowledged without an update
and the entity kept `isArchived: false`; a throttle or access error was acknowledged the same way
instead of being redriven.

`get_s3_file_info`, which the metadata-stream path uses, recognised an archive only on the error code
`NoSuchKey`. A HeadObject error has no body, so botocore reports the HTTP status (`'404'`) as the code
and the archive branch never ran.

The fake client answers the way S3 does: HeadObject without a version id is 404 when the current
version is a delete marker or no version exists, HeadObject on a delete-marker version is 405, and
ListObjectVersions returns one key's entries newest first with `IsLatest` on the current one.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.garnetFramework import garnetDataIndexFile

KEY = "a1/part.stp"
IDS = {"assetid": "a1", "databaseid": "db1"}
ASSET = {"assetId": "a1", "assetName": "Part", "bucketId": "b1",
         "assetLocation": {"Key": "a1/"}}
BUCKET = {"bucketId": "b1", "bucketName": "bucket", "baseAssetsPrefix": ""}

LIVE = [
    {"VersionId": "v2", "marker": False, "Metadata": IDS},
    {"VersionId": "v1", "marker": False, "Metadata": IDS},
]
ARCHIVED = [{"VersionId": "dm1", "marker": True}] + LIVE


def _client_error(code, op="HeadObject"):
    return ClientError({"Error": {"Code": code}}, op)


def _fake_s3(history, head_error=None):
    """An S3 client over one key's version history, given newest first.

    Each entry is ``{"VersionId": ..., "marker": bool, "Metadata": {...}}``.
    """
    client = MagicMock()

    def head(entry):
        return {
            "Metadata": dict(entry.get("Metadata", {})),
            "VersionId": entry["VersionId"],
            "ContentLength": 7,
            "LastModified": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "ETag": f'"etag-{entry["VersionId"]}"',
            "ContentType": "model/step",
        }

    def head_object(Bucket, Key, VersionId=None):
        if head_error:
            raise _client_error(head_error)
        if Key != KEY:
            raise _client_error("404")
        if VersionId is None:
            if not history or history[0]["marker"]:
                raise _client_error("404")
            return head(history[0])
        for entry in history:
            if entry["VersionId"] == VersionId:
                if entry["marker"]:
                    raise _client_error("405")
                return head(entry)
        raise _client_error("404")

    def list_object_versions(Bucket, Prefix, MaxKeys=None, **_):
        rows = []
        if KEY.startswith(Prefix):
            for position, entry in enumerate(history):
                rows.append((entry, {
                    "Key": KEY,
                    "VersionId": entry["VersionId"],
                    "IsLatest": position == 0,
                    "LastModified": datetime(2026, 9, 1, 0, 0, len(history) - position,
                                             tzinfo=timezone.utc),
                }))
        if MaxKeys is not None:
            rows = rows[:MaxKeys]
        return {
            "Versions": [dict(row, Size=7, ETag=f'"etag-{row["VersionId"]}"')
                         for entry, row in rows if not entry["marker"]],
            "DeleteMarkers": [row for entry, row in rows if entry["marker"]],
        }

    client.head_object.side_effect = head_object
    client.list_object_versions.side_effect = list_object_versions
    return client


def _live_partition_only(database_id, asset_id):
    return ASSET if database_id == "db1" else None


def _run(history, event_name="ObjectRemoved:DeleteMarkerCreated", head_error=None,
         asset_lookup=_live_partition_only):
    """Run handle_s3_notification; return (result, entities sent, actions recorded)."""
    m = garnetDataIndexFile
    record = {"eventName": event_name,
              "s3": {"bucket": {"name": "bucket"}, "object": {"key": KEY}}}
    with patch.object(m, "s3_client", _fake_s3(history, head_error)), \
            patch.object(m, "get_asset_details", side_effect=asset_lookup), \
            patch.object(m, "get_bucket_details", return_value=BUCKET), \
            patch.object(m, "get_file_metadata", return_value=({}, {})), \
            patch.object(m, "send_to_garnet_ingestion_queue", return_value=True) as send, \
            patch.object(m, "_record_sync") as record_sync:
        result = m.handle_s3_notification(record)
    sent = [call.args[0] for call in send.call_args_list]
    actions = [call.args[1] for call in record_sync.call_args_list]
    return result, sent, actions


@pytest.mark.unit
class TestS3EventClassifiedByCurrentState:
    def test_archived_key_is_sent_with_the_archived_flag(self):
        result, sent, actions = _run(ARCHIVED)
        assert result is True
        assert len(sent) == 1, "the archive was acknowledged without updating the Garnet entity"
        entity = sent[0]
        assert entity["id"] == "urn:vams:file:db1:a1:%2Fpart.stp"
        assert entity["isArchived"] == {"type": "Property", "value": True}
        # Described by the newest remaining version, not an older one
        assert entity["s3VersionId"]["value"] == "v2"
        assert actions == ["delete"]

    def test_file_of_an_archived_asset_resolves_the_archived_record(self):
        """Archiving an asset moves its record to `{databaseId}#deleted`; the file's S3
        metadata keeps the live database id, which the entity id is built from."""
        def archived_partition_only(database_id, asset_id):
            return ASSET if database_id == "db1#deleted" else None

        result, sent, _ = _run(ARCHIVED, asset_lookup=archived_partition_only)
        assert result is True
        assert len(sent) == 1, "the file of an archived asset was skipped"
        assert sent[0]["id"] == "urn:vams:file:db1:a1:%2Fpart.stp"
        assert sent[0]["isArchived"]["value"] is True

    def test_head_error_other_than_not_found_fails_the_record(self):
        """A throttle or access error says nothing about the key; failing the record lets
        the event-source mapping redrive it instead of deleting it."""
        result, sent, actions = _run(LIVE, event_name="ObjectCreated:Put", head_error="503")
        assert result is False, "a HeadObject 503 was acknowledged as a skip"
        assert sent == [] and actions == []

    def test_restored_key_is_sent_live(self):
        """Control: removing the delete marker (unarchive) emits ObjectRemoved:Delete on a
        key whose current version is live again."""
        result, sent, _ = _run(LIVE, event_name="ObjectRemoved:Delete")
        assert result is True
        assert len(sent) == 1
        assert sent[0]["isArchived"]["value"] is False
        assert sent[0]["s3VersionId"]["value"] == "v2"

    def test_permanently_deleted_key_is_acknowledged_without_a_send(self):
        """Control: with no version left there is nothing to describe, and the ingestion
        queue cannot delete an entity, so the record is acknowledged unchanged."""
        result, sent, actions = _run([], event_name="ObjectRemoved:Delete")
        assert result is True
        assert sent == [] and actions == []


@pytest.mark.unit
class TestGetS3FileInfoArchiveDetection:
    """The metadata-stream path reads archive state through get_s3_file_info."""

    def test_archived_key_reports_archived_from_the_newest_version(self):
        with patch.object(garnetDataIndexFile, "s3_client", _fake_s3(ARCHIVED)):
            info, archived = garnetDataIndexFile.get_s3_file_info("bucket", KEY)
        assert archived is True, "a HeadObject 404 on a delete-marker key was not read as an archive"
        assert info["versionId"] == "v2"

    def test_live_key_reports_live(self):
        """Control for the test above."""
        with patch.object(garnetDataIndexFile, "s3_client", _fake_s3(LIVE)):
            info, archived = garnetDataIndexFile.get_s3_file_info("bucket", KEY)
        assert archived is False
        assert info["versionId"] == "v2"

    def test_key_with_no_version_reports_nothing(self):
        """Control: a permanently deleted key is neither live nor archived."""
        with patch.object(garnetDataIndexFile, "s3_client", _fake_s3([])):
            assert garnetDataIndexFile.get_s3_file_info("bucket", KEY) == (None, False)
