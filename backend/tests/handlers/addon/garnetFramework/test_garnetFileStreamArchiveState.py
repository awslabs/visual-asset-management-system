# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Garnet file indexer's metadata-stream path re-sends an archived file as archived.

A metadata or attribute delete on an archived file emits a REMOVE stream record (the file-metadata
DELETE route does not require the file to exist), and `handle_file_metadata_stream` re-sends the
file's `VAMSFile` entity with the archive state `get_s3_file_info` reads from S3. HeadObject on a key
whose current version is a delete marker answers with the code `'404'` (a HEAD response has no body),
so a reader that recognises only `'NoSuchKey'` reports the file as live, and the upsert-only ingestion
queue overwrites the entity's `isArchived: true` with `false`.

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
ASSET = {"assetId": "a1", "assetName": "Part", "bucketId": "b1",
         "assetLocation": {"Key": "a1/"}}
BUCKET = {"bucketId": "b1", "bucketName": "bucket", "baseAssetsPrefix": ""}

LIVE = [
    {"VersionId": "v2", "marker": False},
    {"VersionId": "v1", "marker": False},
]
ARCHIVED = [{"VersionId": "dm1", "marker": True}] + LIVE


def _client_error(code, op="HeadObject"):
    return ClientError({"Error": {"Code": code}}, op)


def _fake_s3(history):
    """An S3 client over one key's version history, given newest first.

    Each entry is ``{"VersionId": ..., "marker": bool}``.
    """
    client = MagicMock()

    def head(entry):
        return {
            "ContentLength": 7,
            "LastModified": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "ETag": f'"etag-{entry["VersionId"]}"',
            "VersionId": entry["VersionId"],
            "ContentType": "model/step",
            "Metadata": {"assetid": "a1", "databaseid": "db1"},
        }

    def head_object(Bucket, Key, VersionId=None):
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


def _metadata_remove_record():
    return {
        "eventName": "REMOVE",
        "dynamodb": {"Keys": {
            "databaseId:assetId:filePath": {"S": "db1:a1:/part.stp"},
            "metadataKey": {"S": "material"},
        }},
    }


def _run(history):
    """Run handle_file_metadata_stream; return (result, entities sent, _record_sync mock)."""
    m = garnetDataIndexFile
    with patch.object(m, "s3_client", _fake_s3(history)), \
            patch.object(m, "get_asset_details", return_value=ASSET), \
            patch.object(m, "get_bucket_details", return_value=BUCKET), \
            patch.object(m, "get_file_metadata", return_value=({}, {})), \
            patch.object(m, "send_to_garnet_ingestion_queue", return_value=True) as send, \
            patch.object(m, "_record_sync") as record_sync:
        result = m.handle_file_metadata_stream(_metadata_remove_record())
    return result, [call.args[0] for call in send.call_args_list], record_sync


@pytest.mark.unit
class TestMetadataStreamArchiveState:
    def test_metadata_remove_on_archived_file_sends_it_archived(self):
        result, sent, record_sync = _run(ARCHIVED)
        assert result is True
        assert len(sent) == 1
        entity = sent[0]
        assert entity["isArchived"] == {"type": "Property", "value": True}, \
            "an archived file was re-sent to Garnet as live"
        # Described by the newest remaining version
        assert entity["s3VersionId"]["value"] == "v2"
        assert entity["fileSize"]["value"] == 7
        assert record_sync.call_args.kwargs["s3_version_id"] == "v2"

    def test_metadata_remove_on_live_file_sends_it_live(self):
        """Control for the archived arm."""
        result, sent, _ = _run(LIVE)
        assert result is True
        assert len(sent) == 1
        assert sent[0]["isArchived"]["value"] is False
        assert sent[0]["s3VersionId"]["value"] == "v2"
