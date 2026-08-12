# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock

import pytest

from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load

# _load() caches the module across test files, so any attribute this file
# replaces with a mock must be restored after each test or the mock leaks
# into the other sqsBucketSync test suites.
_PATCHED_ATTRS = (
    "asset_bucket_name", "asset_bucket_prefix", "RESERVED_S3_PREFIX_FOLDERS",
    "get_bucket_id", "validate_asset_id", "lookup_asset",
    "delete_file_metadata_on_s3_delete", "update_asset_type",
    "publish_to_file_indexer_sns", "publish_to_orchestration_bus",
    "extract_asset_id_from_key", "parse_event", "process_s3_record",
)


@pytest.fixture(autouse=True)
def _restore_module_attrs():
    m = _load()
    saved = {name: getattr(m, name) for name in _PATCHED_ATTRS}
    yield
    for name, value in saved.items():
        setattr(m, name, value)


def _s3_delete_record(key="db/x-asset-1/file.glb", bucket="asset-bucket"):
    return {
        "eventSource": "aws:s3",
        "eventName": "ObjectRemoved:DeleteMarkerCreated",
        "s3": {"bucket": {"name": bucket}, "object": {"key": key}},
    }


def _wire_delete_handler(m):
    """Common wiring so lambda_handler_deleted reaches per-record processing."""
    m.asset_bucket_name = "asset-bucket"
    m.asset_bucket_prefix = "db/"
    m.RESERVED_S3_PREFIX_FOLDERS = {"temp-uploads"}
    m.get_bucket_id = MagicMock(return_value="bucket-1")
    m.validate_asset_id = MagicMock(return_value=True)
    m.lookup_asset = MagicMock(return_value={"databaseId": "db1", "assetId": "x-asset-1"})
    m.delete_file_metadata_on_s3_delete = MagicMock()
    m.update_asset_type = MagicMock(return_value=True)
    m.publish_to_file_indexer_sns = MagicMock()


@pytest.mark.unit
class TestDeleteHandlerForwardsToIndexers:
    """Deletes must reach the file indexer even when VAMS-side cleanup is skipped,
    otherwise OpenSearch and other registered indexers never remove their records."""

    def test_forwards_when_asset_record_missing(self):
        m = _load()
        _wire_delete_handler(m)
        m.lookup_asset = MagicMock(return_value=None)  # asset record already gone

        event = {"Records": [_s3_delete_record()]}
        m.lambda_handler_deleted(event, MagicMock())

        m.publish_to_file_indexer_sns.assert_called_once()
        # No metadata cleanup attempted, but asset type update still runs
        m.delete_file_metadata_on_s3_delete.assert_not_called()

    def test_forwards_when_bucket_id_missing(self):
        m = _load()
        _wire_delete_handler(m)
        m.get_bucket_id = MagicMock(return_value=None)

        event = {"Records": [_s3_delete_record()]}
        m.lambda_handler_deleted(event, MagicMock())

        m.publish_to_file_indexer_sns.assert_called_once()

    def test_forwards_when_cleanup_raises(self):
        m = _load()
        _wire_delete_handler(m)
        m.update_asset_type = MagicMock(side_effect=RuntimeError("boom"))

        event = {"Records": [_s3_delete_record()]}
        m.lambda_handler_deleted(event, MagicMock())

        m.publish_to_file_indexer_sns.assert_called_once()

    def test_one_bad_record_does_not_block_others(self):
        # A cleanup exception on the first record must not prevent the second
        # record from being processed and forwarded.
        m = _load()
        _wire_delete_handler(m)
        m.update_asset_type = MagicMock(side_effect=[RuntimeError("boom"), True])

        event = {"Records": [
            _s3_delete_record(key="db/x-asset-1/a.glb"),
            _s3_delete_record(key="db/x-asset-1/b.glb"),
        ]}
        m.lambda_handler_deleted(event, MagicMock())

        m.publish_to_file_indexer_sns.assert_called_once()
        published_event = m.publish_to_file_indexer_sns.call_args.args[0]
        assert len(published_event["Records"]) == 2

    def test_folder_markers_and_init_files_not_forwarded(self):
        m = _load()
        _wire_delete_handler(m)

        event = {"Records": [
            _s3_delete_record(key="db/x-asset-1/folder/"),
            _s3_delete_record(key="db/x-asset-1/init"),
        ]}
        m.lambda_handler_deleted(event, MagicMock())

        m.publish_to_file_indexer_sns.assert_not_called()

    def test_reserved_folder_not_forwarded(self):
        m = _load()
        _wire_delete_handler(m)
        m.extract_asset_id_from_key = MagicMock(return_value="temp-uploads")

        event = {"Records": [_s3_delete_record(key="db/temp-uploads/file.glb")]}
        m.lambda_handler_deleted(event, MagicMock())

        m.publish_to_file_indexer_sns.assert_not_called()


@pytest.mark.unit
class TestCreatedHandlerForwardsToIndexers:
    """Created events flagged for indexing must be published even when other
    records in the batch hard-errored."""

    def test_publishes_indexable_records_despite_hard_errors(self):
        m = _load()
        m.parse_event = MagicMock(side_effect=lambda e: e)
        record_ok = {"eventSource": "aws:s3", "s3": {"bucket": {"name": "b"}, "object": {"key": "db/a1/ok.glb"}}}
        record_bad = {"eventSource": "aws:s3", "s3": {"bucket": {"name": "b"}, "object": {"key": "db/a2/bad.glb"}}}
        # First record indexes fine; second hard-errors but is still flagged
        # for indexing (should_index=True on failure paths).
        m.process_s3_record = MagicMock(side_effect=[
            (True, True, "Successfully processed db/a1/ok.glb"),
            (False, True, "Failed to update metadata for db/a2/bad.glb"),
        ])
        m.publish_to_file_indexer_sns = MagicMock()
        m.publish_to_orchestration_bus = MagicMock()

        event = {"Records": [record_ok, record_bad]}
        m.lambda_handler_created(event, MagicMock())

        m.publish_to_file_indexer_sns.assert_called_once()
        published_event = m.publish_to_file_indexer_sns.call_args.args[0]
        assert len(published_event["Records"]) == 2

    def test_stale_create_event_still_forwarded(self):
        # process_s3_record returns (True, True, ...) for a stale create whose
        # object is gone — the record must reach the indexers for reconciliation.
        m = _load()
        m.parse_event = MagicMock(side_effect=lambda e: e)
        record = {"eventSource": "aws:s3", "s3": {"bucket": {"name": "b"}, "object": {"key": "db/a1/gone.glb"}}}
        m.process_s3_record = MagicMock(return_value=(True, True, "Skipped stale create event for db/a1/gone.glb"))
        m.publish_to_file_indexer_sns = MagicMock()
        m.publish_to_orchestration_bus = MagicMock()

        m.lambda_handler_created({"Records": [record]}, MagicMock())

        m.publish_to_file_indexer_sns.assert_called_once()

    def test_records_not_flagged_for_indexing_are_withheld(self):
        m = _load()
        m.parse_event = MagicMock(side_effect=lambda e: e)
        record = {"eventSource": "aws:s3", "s3": {"bucket": {"name": "b"}, "object": {"key": "db/a1/folder/"}}}
        m.process_s3_record = MagicMock(return_value=(True, False, "Processed folder marker db/a1/folder/"))
        m.publish_to_file_indexer_sns = MagicMock()
        m.publish_to_orchestration_bus = MagicMock()

        m.lambda_handler_created({"Records": [record]}, MagicMock())

        m.publish_to_file_indexer_sns.assert_not_called()
        m.publish_to_orchestration_bus.assert_not_called()
