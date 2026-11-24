# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bucket sync creates no asset for a new folder whose name is an asset ID another asset uses.

The create is refused by create_asset (AssetIdInUseError). Bucket sync then logs one ERROR line that
names the object key and the reason, writes nothing (no metadata stamp, no asset type, no history),
and withholds the record from the indexers, because the file belongs to no asset. A folder whose name
is free still becomes an asset.
"""

from unittest.mock import MagicMock

import pytest

from tests.handlers.indexing import test_sqsBucketSync_recreation_guard as guard

_OBJECT_KEY = "db/taken-asset-id/model.glb"
_ASSET_ID = "taken-asset-id"


class _InUse(Exception):
    """Stands in for createAsset.AssetIdInUseError, which the loader stubs out."""


@pytest.fixture
def m(monkeypatch):
    # A fresh module: other test files replace this module's functions in place on the cached one.
    monkeypatch.setattr(guard, "_cached_guard_module", None)
    module = guard._load()
    monkeypatch.setattr(module, "AssetIdInUseError", _InUse, raising=False)
    return module


def _record():
    return {"s3": {"bucket": {"name": "asset-bucket"}, "object": {"key": _OBJECT_KEY}},
            "eventName": "ObjectCreated:Put"}


def _wire_create_branch(m):
    """Reach the create branch: no live or archived asset with the id in this bucket."""
    m.asset_bucket_name = "asset-bucket"
    m.asset_bucket_prefix = "db/"
    m.RESERVED_S3_PREFIX_FOLDERS = set()
    m.get_bucket_id = MagicMock(return_value="bucket-1")
    m.extract_asset_id_from_key = MagicMock(return_value=_ASSET_ID)
    m.validate_asset_id = MagicMock(return_value=True)
    m.lookup_asset = MagicMock(return_value=None)
    m.lookup_archived_asset = MagicMock(return_value=None)
    m.get_or_create_database_for_bucket = MagicMock(return_value="db-1")
    m.object_still_exists = MagicMock(return_value=True)
    m.update_s3_metadata = MagicMock(return_value=True)
    m.update_asset_type = MagicMock(return_value=True)
    m.write_file_version_history = MagicMock()
    m.s3_client = MagicMock()
    m.asset_cache = MagicMock()
    m.logger = MagicMock()


@pytest.mark.unit
class TestAFolderNamedLikeAnAssetIdInUse:

    def test_no_asset_is_created_and_nothing_is_written(self, m):
        _wire_create_branch(m)
        m.create_asset = MagicMock(side_effect=_InUse("Asset with specified ID already exists"))

        success, should_index, message = m.process_s3_record(_record())

        assert success is False
        assert _ASSET_ID in message
        m.update_s3_metadata.assert_not_called()
        m.update_asset_type.assert_not_called()
        m.write_file_version_history.assert_not_called()
        m.asset_cache.set.assert_not_called()

    def test_the_record_is_withheld_from_the_indexers(self, m):
        _wire_create_branch(m)
        m.create_asset = MagicMock(side_effect=_InUse("Asset with specified ID already exists"))

        _, should_index, _ = m.process_s3_record(_record())

        assert should_index is False

    def test_one_error_line_names_the_key_and_the_reason(self, m):
        _wire_create_branch(m)
        m.create_asset = MagicMock(side_effect=_InUse("Asset with specified ID already exists"))

        m.process_s3_record(_record())

        assert m.logger.error.call_count == 1
        line = m.logger.error.call_args.args[0]
        assert _OBJECT_KEY in line
        assert "already used by another asset" in line
        # The traceback form is kept for real failures; a refused id is not one.
        m.logger.exception.assert_not_called()

    def test_the_refusal_is_counted_as_a_skip_not_an_error(self, m):
        _wire_create_branch(m)
        m.create_asset = MagicMock(side_effect=_InUse("Asset with specified ID already exists"))
        m.publish_to_file_indexer_sns = MagicMock(return_value=True)
        m.publish_to_orchestration_bus = MagicMock()

        _, records = m.on_storage_event_created({"Records": [_record()]})

        assert records == []
        assert m.logger.error.call_count == 1


@pytest.mark.unit
class TestCreateNewAsset:

    def test_an_id_in_use_is_raised_to_the_caller(self, m):
        m.asset_cache = MagicMock()
        m.create_asset = MagicMock(side_effect=_InUse("Asset with specified ID already exists"))

        with pytest.raises(_InUse):
            m.create_new_asset("bucket-1", "db-1", _ASSET_ID)

        m.asset_cache.set.assert_not_called()

    def test_any_other_create_failure_still_returns_none(self, m):
        m.asset_cache = MagicMock()
        m.create_asset = MagicMock(side_effect=RuntimeError("table unavailable"))

        assert m.create_new_asset("bucket-1", "db-1", _ASSET_ID) is None
        m.asset_cache.set.assert_not_called()


@pytest.mark.unit
class TestAFreeFolderNameStillBecomesAnAsset:
    """Positive control: the refusal above is the id check, not a create branch that never runs."""

    def test_a_free_id_creates_the_asset_and_stamps_the_file(self, m):
        _wire_create_branch(m)
        m.create_asset = MagicMock(return_value=MagicMock(assetId=_ASSET_ID))

        success, should_index, _ = m.process_s3_record(_record())

        assert success is True and should_index is True
        m.create_asset.assert_called_once()
        m.update_s3_metadata.assert_called_once()
        m.logger.error.assert_not_called()
