# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""createAsset with a bucketExistingKey asks bucket sync to import the files already there: one message
on the created-events queue of the asset's own registration. A create refused because another asset's
location holds the key raises AssetLocationInUseError, logged once at WARNING with that asset."""

import json
from unittest.mock import MagicMock

import pytest

from tests.handlers.assets.test_createAsset_conditional_put import _load as _load_create_asset

QUEUE = "https://sqs.us-east-1.amazonaws.com/111122223333/vams-t1-bucketSyncCreated--0"


def _wire(m, queue_url=QUEUE):
    m.asset_table = MagicMock()
    m.asset_table.get_item.return_value = {}
    m.database_table = MagicMock()
    m.database_table.get_item.return_value = {"Item": {"databaseId": "testdb1"}}
    details = {"bucketId": "b1", "bucketName": "vams-assets", "baseAssetsPrefix": ""}
    if queue_url:
        details["syncCreatedQueueUrl"] = queue_url
    m.get_default_bucket_details = MagicMock(return_value=details)
    m.assert_existing_key_not_owned = MagicMock()
    m.assert_derived_asset_key_not_owned = MagicMock()
    m.resolve_colocated_bucket_ids = MagicMock(return_value=["b1"])
    m.check_s3_key_exists = MagicMock(return_value=True)
    m.check_s3_prefix_exists = MagicMock(return_value=True)
    m.check_s3_folder_has_objects = MagicMock(return_value=True)
    m.create_initial_version_record = MagicMock(return_value="0")
    m.create_sns_topic_for_asset = MagicMock(return_value="arn:sns")
    m.update_asset_count = MagicMock()
    m.write_asset_history_record = MagicMock()
    m.validate_tags_exist = MagicMock(return_value=True)
    m.verify_all_required_tags_satisfied = MagicMock(return_value=True)
    m.sqs_client = MagicMock()
    m.logger = MagicMock()


def _create(m, key=None, external=False, asset_id="asset-1"):
    kwargs = {"bucketExistingKey": key} if key else {}
    request = m.CreateAssetRequestModel(databaseId="testdb1", assetId=asset_id, assetName=asset_id,
                                        description="import", isDistributable=True, tags=[], **kwargs)
    return m.create_asset(request, {"tokens": ["user1"]}, external)


def _requests(m):
    return [(c.kwargs["QueueUrl"], json.loads(c.kwargs["MessageBody"]))
            for c in m.sqs_client.send_message.call_args_list]


@pytest.mark.unit
class TestTheFilesAlreadyThereAreImported:
    @pytest.mark.parametrize("key,location", [("projects/synced/", "projects/synced/"),
                                              ("projects/unmarked", "projects/unmarked/")])
    def test_one_import_request_to_the_registrations_queue(self, key, location):
        m = _load_create_asset(fresh=True)
        _wire(m)
        if not key.endswith("/"):
            # A folder given without its '/': nothing is stored at the key, objects are under it
            m.check_s3_key_exists = MagicMock(return_value=False)
        _create(m, key)
        assert _requests(m) == [(QUEUE, {"vamsImportRequest": {
            "bucket": "vams-assets", "location": location, "start": True,
            "owner": {"databaseId": "testdb1", "assetId": "asset-1", "bucketId": "b1"}}})]

    def test_a_registration_without_a_queue_sends_nothing(self):
        m = _load_create_asset(fresh=True)
        _wire(m, queue_url=None)
        _create(m, "projects/synced/")
        m.sqs_client.send_message.assert_not_called()
        m.logger.warning.assert_called()

    def test_a_failed_send_still_creates_the_asset(self):
        m = _load_create_asset(fresh=True)
        _wire(m)
        m.sqs_client.send_message.side_effect = RuntimeError("throttled")
        response = _create(m, "projects/synced/")
        assert response.assetId == "asset-1"
        m.asset_table.put_item.assert_called_once()

    def test_an_asset_without_an_existing_key_sends_nothing(self):
        m = _load_create_asset(fresh=True)
        _wire(m)
        m.check_s3_prefix_exists = MagicMock(return_value=False)
        m.create_prefix_folder = MagicMock()
        _create(m)
        m.sqs_client.send_message.assert_not_called()

    def test_an_auto_create_from_bucket_sync_sends_nothing(self):
        m = _load_create_asset(fresh=True)
        _wire(m)
        _create(m, external=True, asset_id="newfolder")
        m.sqs_client.send_message.assert_not_called()


@pytest.mark.unit
class TestALocationInUseIsAnExpectedRefusal:
    def test_the_bucket_walk_refusal_is_typed_and_logged_once_at_warning(self):
        m = _load_create_asset(fresh=True)
        m.index_ready = MagicMock(return_value=False)
        m.logger = MagicMock()
        m.asset_table = MagicMock()
        m.asset_table.query.return_value = {"Items": [{
            "databaseId": "db2", "assetId": "owner-1", "assetLocation": {"Key": "a1/sub/"}}]}
        with pytest.raises(m.AssetLocationInUseError) as raised:
            m.assert_existing_key_not_owned(["b1"], "a1/", "vams-assets")
        assert isinstance(raised.value, m.VAMSGeneralErrorResponse)
        assert raised.value.owner == "db2:owner-1 at a1/sub/"
        assert "already in use by another asset" in str(raised.value)
        m.logger.error.assert_not_called()
        assert m.logger.warning.call_count == 1
        assert "db2:owner-1" in m.logger.warning.call_args.args[0]

    def test_the_derived_key_refusal_is_typed_and_logged_once_at_warning(self):
        m = _load_create_asset(fresh=True)
        m.logger = MagicMock()
        m.asset_table = MagicMock()
        m.asset_table.query.return_value = {"Items": [{
            "databaseId": "db2", "assetId": "a1", "assetLocation": {"Key": "a1/"}}]}
        with pytest.raises(m.AssetLocationInUseError) as raised:
            m.assert_derived_asset_key_not_owned(["b1"], "a1", "a1/", "db1")
        assert raised.value.owner == "db2:a1 at a1/"
        m.logger.error.assert_not_called()
        assert m.logger.warning.call_count == 1
