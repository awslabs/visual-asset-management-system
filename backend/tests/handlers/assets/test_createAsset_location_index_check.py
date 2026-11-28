# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""createAsset: the ownership check reads the location index once it is ready, and a
bucketExistingKey may name a folder that has objects but no folder-marker object."""

import sys
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from tests.handlers.assets.test_createAsset_conditional_put import _load as _load_create_asset

al = sys.modules['common.assetLocations']

IN_USE = "The specified bucketExistingKey is already in use by another asset"
MISSING = "The specified bucketExistingKey does not exist in the asset's database default S3 bucket"
FILE_KEY = ("The specified bucketExistingKey names a file. It must name a folder (a key ending in /) that "
            "holds the asset's files.")


def _entry(database_id="other-db", asset_id="owner-1", location="projects/alpha/"):
    return al.LocationEntry(database_id=database_id, asset_id=asset_id, bucket_id="b1", location=location)


def _wire_check(m, ready, conflicts=None, conflict_error=None):
    m.index_ready = MagicMock(return_value=ready)
    m.find_conflicts = MagicMock(return_value=list(conflicts or []), side_effect=conflict_error)
    m.asset_table = MagicMock()
    m.asset_table.query.return_value = {"Items": []}


@pytest.mark.unit
class TestOwnershipCheckUsesTheIndexOnlyWhenReady:
    def test_ready_index_is_read_and_the_bucket_walk_is_not(self):
        m = _load_create_asset(fresh=True)
        _wire_check(m, ready=True)
        m.assert_existing_key_not_owned(["b1", "b2"], "projects/alpha/", "vams-assets")
        m.find_conflicts.assert_called_once_with(m.asset_table, "vams-assets", "projects/alpha/")
        m.asset_table.query.assert_not_called()

    def test_ready_index_conflict_gives_the_same_refusal_as_the_walk(self):
        m = _load_create_asset(fresh=True)
        _wire_check(m, ready=True, conflicts=[_entry()])
        with pytest.raises(m.VAMSGeneralErrorResponse) as raised:
            m.assert_existing_key_not_owned(["b1"], "/projects/alpha/sub/", "vams-assets")
        assert IN_USE in str(raised.value)
        m.find_conflicts.assert_called_once_with(m.asset_table, "vams-assets", "projects/alpha/sub/")

    def test_index_not_ready_keeps_the_bucket_walk(self):
        m = _load_create_asset(fresh=True)
        _wire_check(m, ready=False)
        m.asset_table.query.return_value = {"Items": [{
            "databaseId": "other-db", "assetId": "owner-1", "assetLocation": {"Key": "projects/alpha/"}}]}
        with pytest.raises(m.VAMSGeneralErrorResponse) as raised:
            m.assert_existing_key_not_owned(["b1"], "projects/alpha/", "vams-assets")
        assert IN_USE in str(raised.value)
        m.find_conflicts.assert_not_called()
        assert m.asset_table.query.call_args.kwargs["IndexName"] == "BucketIdGSI"

        # A ready index with no bucket name to look it up by also keeps the walk.
        _wire_check(m, ready=True)
        m.assert_existing_key_not_owned(["b1"], "projects/alpha/")
        m.find_conflicts.assert_not_called()
        m.asset_table.query.assert_called_once()

    def test_an_unreadable_index_refuses_with_the_generic_error(self):
        m = _load_create_asset(fresh=True)
        throttled = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "Query")
        _wire_check(m, ready=True, conflict_error=throttled)
        with pytest.raises(m.VAMSGeneralErrorResponse) as raised:
            m.assert_existing_key_not_owned(["b1"], "projects/alpha/", "vams-assets")
        assert "Error validating S3 location" in str(raised.value)
        m.asset_table.query.assert_not_called()

    UNANSWERED = [al.LocationTooDeepError("Location has 70 candidate locations, more than 64"),
                  RuntimeError("Location index query did not finish within 5 pages")]

    @pytest.mark.parametrize("error", UNANSWERED)
    def test_a_location_the_index_cannot_answer_for_is_checked_by_the_bucket_walk(self, error):
        m = _load_create_asset(fresh=True)
        _wire_check(m, ready=True, conflict_error=error)
        m.asset_table.query.return_value = {"Items": [{
            "databaseId": "other-db", "assetId": "owner-1", "assetLocation": {"Key": "projects/alpha/"}}]}
        with pytest.raises(m.VAMSGeneralErrorResponse) as raised:
            m.assert_existing_key_not_owned(["b1"], "projects/alpha/sub/", "vams-assets")
        assert IN_USE in str(raised.value)
        assert m.asset_table.query.call_args.kwargs["IndexName"] == "BucketIdGSI"

    @pytest.mark.parametrize("error", UNANSWERED)
    def test_a_free_location_the_index_cannot_answer_for_passes_the_bucket_walk(self, error):
        m = _load_create_asset(fresh=True)
        _wire_check(m, ready=True, conflict_error=error)
        m.assert_existing_key_not_owned(["b1"], "projects/alpha/", "vams-assets")
        m.asset_table.query.assert_called_once()


@pytest.mark.unit
class TestExistingKeyFolderWithoutMarker:
    def _wire(self, m, head_hits, folder_has_objects):
        m.asset_table = MagicMock()
        m.asset_table.get_item.return_value = {}
        m.database_table = MagicMock()
        m.database_table.get_item.return_value = {"Item": {"databaseId": "testdb1"}}
        m.get_default_bucket_details = MagicMock(return_value={
            "bucketId": "b1", "bucketName": "vams-assets", "baseAssetsPrefix": ""})
        m.assert_existing_key_not_owned = MagicMock()
        m.resolve_colocated_bucket_ids = MagicMock(return_value=["b1"])
        m.check_s3_key_exists = MagicMock(return_value=head_hits)
        m.check_s3_folder_has_objects = MagicMock(return_value=folder_has_objects)
        m.create_initial_version_record = MagicMock(return_value="0")
        m.create_sns_topic_for_asset = MagicMock(return_value="arn:sns")
        m.update_asset_count = MagicMock()
        m.write_asset_history_record = MagicMock()
        m.validate_tags_exist = MagicMock(return_value=True)
        m.verify_all_required_tags_satisfied = MagicMock(return_value=True)

    @staticmethod
    def _create(m, key):
        request = m.CreateAssetRequestModel(databaseId="testdb1", assetId="asset-1", assetName="asset-1",
                                            description="folder import", isDistributable=True, tags=[],
                                            bucketExistingKey=key)
        m.create_asset(request, {"tokens": ["user1"]})
        return m.asset_table.put_item.call_args.kwargs["Item"]

    @pytest.mark.parametrize("key", ["projects/synced", "projects/synced/"])
    def test_a_folder_with_objects_and_no_marker_is_imported_as_a_folder(self, key):
        m = _load_create_asset(fresh=True)
        self._wire(m, head_hits=False, folder_has_objects=True)
        row = self._create(m, key)
        assert row["assetLocation"] == {"Key": "projects/synced/"}
        assert row["locationKey"] == "projects/synced/"
        m.check_s3_folder_has_objects.assert_called_once_with("vams-assets", "projects/synced/")
        m.assert_existing_key_not_owned.assert_called_once_with(["b1"], "projects/synced/", "vams-assets")

    def test_a_key_that_names_an_object_is_refused_even_with_objects_under_it(self):
        # An asset is created on a folder: objects under `{key}/` do not make a file key a folder.
        m = _load_create_asset(fresh=True)
        self._wire(m, head_hits=True, folder_has_objects=True)
        with pytest.raises(m.VAMSGeneralErrorResponse) as raised:
            self._create(m, "projects/model.glb")
        assert str(raised.value).endswith(FILE_KEY)
        m.asset_table.put_item.assert_not_called()
        m.check_s3_folder_has_objects.assert_not_called()
        m.assert_existing_key_not_owned.assert_not_called()

    def test_the_folder_probe_reads_one_entry_under_the_folder(self):
        m = _load_create_asset(fresh=True)
        m.s3_client = MagicMock()
        m.s3_client.list_objects_v2.return_value = {"Contents": [{"Key": "projects/synced/a.glb"}], "KeyCount": 1}
        assert m.check_s3_folder_has_objects("vams-assets", "projects/synced/") is True
        m.s3_client.list_objects_v2.assert_called_once_with(
            Bucket="vams-assets", Prefix="projects/synced/", MaxKeys=1)
        m.s3_client.list_objects_v2.return_value = {"KeyCount": 0}
        assert m.check_s3_folder_has_objects("vams-assets", "projects/synced/") is False

    def test_nothing_at_the_key_or_under_it_is_still_refused(self):
        m = _load_create_asset(fresh=True)
        self._wire(m, head_hits=False, folder_has_objects=False)
        with pytest.raises(m.VAMSGeneralErrorResponse) as raised:
            self._create(m, "projects/missing")
        assert MISSING in str(raised.value)
        m.asset_table.put_item.assert_not_called()
        # The folder form was probed before refusing.
        m.check_s3_folder_has_objects.assert_called_once_with("vams-assets", "projects/missing/")
