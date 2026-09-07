# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The action the Garnet file indexer PERSISTS, asserted on the DynamoDB item.

S2-BACKEND-082 is about the value stored in the sync-tracking row's `action`
attribute. The sibling tests stop at `_record_sync` and assert its arguments, so
they pin the event -> action mapping but not the six positional arguments
`_record_sync` forwards to `write_outbound_sync_record`. Those tests still pass
if `action` and `sync_status` swap slots -- the row would then persist
action="success", the same class of wrong metadata, invisibly.

These tests run the real `_record_sync` and the real `write_outbound_sync_record`
with only the tracking table stubbed, and assert on the item handed to
`put_item`, so the whole chain from event name to stored attribute is covered.
"""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

def _writer_globals():
    """The namespace `write_outbound_sync_record` actually reads its module globals from.

    Not `sys.modules['common.syncTracking']`, and not `common.syncTracking`. The conftest layering
    loads `common/syncTracking.py` by path with `spec_from_file_location` + `exec_module` and never
    registers the result in `sys.modules`, so the handler's
    `from common.syncTracking import write_outbound_sync_record` binds a function whose
    `__globals__` belongs to an ORPHANED module instance -- one reachable through no name at all
    (`[k for k, v in sys.modules.items() if v.__dict__ is f.__globals__]` is empty). In that instance
    `sync_tracking_outbound_table` is None, so the writer logged
    "Sync tracking table not configured; skipping sync record" and returned no matter which named
    module the test patched, and the test read that as the handler recording nothing.

    Patching the function's own `__globals__` is therefore the only target that the writer reads, and
    `patch.dict` restores the entry afterwards.
    """
    return garnetDataIndexFile.write_outbound_sync_record.__globals__
from backend.backend.handlers.addon.garnetFramework import garnetDataIndexFile


def _s3_record(event_name):
    return {
        "eventName": event_name,
        "s3": {"bucket": {"name": "bucket"}, "object": {"key": "a1/part.stp"}},
    }


def _stream_record(event_name):
    key = {"databaseId:assetId:filePath": {"S": "db1:a1:/part.stp"}}
    return {"eventName": event_name,
            "dynamodb": {"Keys": key, "NewImage": key}}


def _lookup_patches(m, include_s3_client, send_ok):
    """Stub every lookup the handlers make, leaving the sync-record path real."""
    patches = [
        patch.object(m, "get_asset_details",
                     return_value={"assetId": "a1", "bucketId": "b1",
                                   "assetLocation": {"Key": "a1/"}}),
        patch.object(m, "get_bucket_details",
                     return_value={"bucketName": "bucket", "baseAssetsPrefix": ""}),
        patch.object(m, "get_file_metadata", return_value=({}, {})),
        patch.object(m, "get_s3_file_info", return_value=({"versionId": "v7"}, False)),
        patch.object(m, "convert_file_to_ngsi_ld",
                     return_value={"id": "urn:vams:file:db1:a1:%2Fpart.stp",
                                   "type": "VAMSFile"}),
        patch.object(m, "send_to_garnet_ingestion_queue", return_value=send_ok),
    ]
    if include_s3_client:
        s3_client = MagicMock()
        s3_client.head_object.return_value = {
            "Metadata": {"assetid": "a1", "databaseid": "db1"}
        }
        patches.append(patch.object(m, "s3_client", s3_client))
    return patches


def _persisted_item(handler, record, include_s3_client=False, send_ok=True):
    """Run one handler and return the item written to the sync-tracking table."""
    m = garnetDataIndexFile
    table = MagicMock()
    # The table lives on the shared common.syncTracking module, so it is entered
    # on a stack: anything raising during setup or in the handler still unwinds it.
    with ExitStack() as stack:
        for p in _lookup_patches(m, include_s3_client, send_ok):
            stack.enter_context(p)
        stack.enter_context(patch.dict(
            _writer_globals(), {"sync_tracking_outbound_table": table}))
        handler(record)
    # write_outbound_sync_record swallows every failure, so an unwritten record
    # would otherwise surface as an opaque None below.
    assert table.put_item.called, "no sync-tracking record was written"
    return table.put_item.call_args[1]["Item"]


@pytest.mark.unit
class TestPersistedSyncAction:
    """The stored `action` follows the event name, not a constant."""

    def test_stream_insert_persists_create(self):
        item = _persisted_item(garnetDataIndexFile.handle_file_metadata_stream,
                               _stream_record("INSERT"))
        assert item["action"] == _writer_globals()["SYNC_ACTION_CREATE"]
        assert item["objectType"] == _writer_globals()["SYNC_OBJECT_TYPE_ASSET_FILE"]
        assert item["objectId"] == "db1:a1:/part.stp"

    def test_stream_remove_persists_delete(self):
        item = _persisted_item(garnetDataIndexFile.handle_file_metadata_stream,
                               _stream_record("REMOVE"))
        assert item["action"] == _writer_globals()["SYNC_ACTION_DELETE"]

    def test_s3_object_created_persists_create(self):
        item = _persisted_item(garnetDataIndexFile.handle_s3_notification,
                               _s3_record("ObjectCreated:Put"),
                               include_s3_client=True)
        assert item["action"] == _writer_globals()["SYNC_ACTION_CREATE"]
        assert item["s3VersionId"] == "v7"

    def test_stream_modify_persists_modify(self):
        """Positive control. `modify` is the value the indexer produced for every
        event before the action was derived, so this arm must keep passing: it
        proves the deriving branch did not simply relabel the ordinary case."""
        item = _persisted_item(garnetDataIndexFile.handle_file_metadata_stream,
                               _stream_record("MODIFY"))
        assert item["action"] == _writer_globals()["SYNC_ACTION_MODIFY"]

    def test_action_and_status_are_not_transposed(self):
        """`_record_sync` forwards action and sync_status as adjacent positional
        arguments. Asserted as exact values on both arms of the send flag, because
        the allow-lists are disjoint: a set-membership assertion holds for any
        transposition the allow-list has already rejected, and rejecting the row
        drops it silently. Pinning the pair keeps the two slots distinguishable."""
        queued = _persisted_item(garnetDataIndexFile.handle_file_metadata_stream,
                                 _stream_record("INSERT"))
        assert queued["action"] == _writer_globals()["SYNC_ACTION_CREATE"]
        assert queued["syncStatus"] == _writer_globals()["SYNC_STATUS_SUCCESS"]

        not_queued = _persisted_item(garnetDataIndexFile.handle_file_metadata_stream,
                                     _stream_record("INSERT"), send_ok=False)
        assert not_queued["action"] == _writer_globals()["SYNC_ACTION_CREATE"]
        assert not_queued["syncStatus"] == _writer_globals()["SYNC_STATUS_FAILED"]
