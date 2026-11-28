# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""createAsset creates an asset on a folder only: a bucketExistingKey that names a file is refused.

A key without a trailing ``/`` at which Amazon S3 holds an object is a file. It is refused with ``400`` and
one message, even when objects also sit under ``{key}/``, and nothing is written: no asset row, version
record, topic, import request, count or history. A folder is accepted with or without its trailing ``/``
when nothing is stored at the key itself. The base-prefix, root and reserved-folder checks run first and keep
their own messages.
"""

import json
from unittest.mock import MagicMock

import pytest

from tests.handlers.assets.test_createAsset_conditional_put import _load

FILE_KEY = ("The specified bucketExistingKey names a file. It must name a folder (a key ending in /) that "
            "holds the asset's files.")
MISSING = "The specified bucketExistingKey does not exist in the asset's database default S3 bucket"
RESERVED = "The specified bucketExistingKey is inside a folder VAMS reserves for system use. Choose another location."
QUEUE = "https://sqs.us-east-1.amazonaws.com/111122223333/vams-t1-bucketSyncCreated--0"


def _wire(m, base_prefix="", head_hits=True, folder_has_objects=True):
    """Wire the module so a create reaches the S3 checks. ``head_hits`` is whether HeadObject finds an
    object at the resolved key; ``folder_has_objects`` whether anything is stored under ``{key}/``."""
    m.asset_table = MagicMock()
    m.asset_table.get_item.return_value = {}
    m.database_table = MagicMock()
    m.database_table.get_item.return_value = {"Item": {"databaseId": "testdb1"}}
    m.get_default_bucket_details = MagicMock(return_value={
        "bucketId": "b1", "bucketName": "vams-assets", "baseAssetsPrefix": base_prefix,
        "syncCreatedQueueUrl": QUEUE})
    m.resolve_colocated_bucket_ids = MagicMock(return_value=["b1"])
    m.assert_existing_key_not_owned = MagicMock()
    m.assert_derived_asset_key_not_owned = MagicMock()
    m.check_s3_key_exists = MagicMock(return_value=head_hits)
    m.check_s3_folder_has_objects = MagicMock(return_value=folder_has_objects)
    m.check_s3_prefix_exists = MagicMock(return_value=False)
    m.create_prefix_folder = MagicMock()
    m.create_initial_version_record = MagicMock(return_value="0")
    m.create_sns_topic_for_asset = MagicMock(return_value="arn:sns")
    m.update_asset_count = MagicMock()
    m.write_asset_history_record = MagicMock()
    m.validate_tags_exist = MagicMock(return_value=True)
    m.verify_all_required_tags_satisfied = MagicMock(return_value=True)
    m.sqs_client = MagicMock()
    return m


def _request(m, key):
    return m.CreateAssetRequestModel(databaseId="testdb1", assetId="asset-1", assetName="asset-1",
                                     description="existing key", isDistributable=True, tags=[],
                                     bucketExistingKey=key)


def _create(m, key):
    """The stored row, or the refusal message."""
    try:
        m.create_asset(_request(m, key), {"tokens": ["user1"], "roles": []})
    except m.VAMSGeneralErrorResponse as e:
        return None, str(e)
    return m.asset_table.put_item.call_args.kwargs["Item"], None


def _assert_nothing_written(m):
    m.asset_table.put_item.assert_not_called()
    m.create_initial_version_record.assert_not_called()
    m.create_sns_topic_for_asset.assert_not_called()
    m.create_prefix_folder.assert_not_called()
    m.sqs_client.send_message.assert_not_called()
    m.update_asset_count.assert_not_called()
    m.write_asset_history_record.assert_not_called()


@pytest.mark.unit
class TestAFileKeyIsRefused:
    @pytest.mark.parametrize("base_prefix,key", [
        ("", "projects/model.glb"),
        ("", "/projects/model.glb"),
        ("", "projects/site-7/scan_01.e57"),
        ("vams-area/", "vams-area/projects/model.glb"),
        ("vams-area/", "projects/model.glb"),
    ])
    def test_a_key_that_names_an_object_is_refused_and_nothing_is_written(self, base_prefix, key):
        m = _wire(_load(fresh=True), base_prefix=base_prefix, head_hits=True, folder_has_objects=False)
        row, error = _create(m, key)
        assert row is None
        assert error is not None and error.endswith(FILE_KEY), error
        _assert_nothing_written(m)
        m.assert_existing_key_not_owned.assert_not_called()

    def test_objects_under_the_key_do_not_make_a_file_a_folder(self):
        m = _wire(_load(fresh=True), head_hits=True, folder_has_objects=True)
        row, error = _create(m, "projects/model.glb")
        assert error is not None and error.endswith(FILE_KEY), error
        # The folder form is never probed, so a file with a companion folder is not taken as that folder
        m.check_s3_folder_has_objects.assert_not_called()
        _assert_nothing_written(m)

    def test_the_message_names_neither_the_key_nor_another_asset(self):
        m = _wire(_load(fresh=True), head_hits=True)
        _, error = _create(m, "projects/secret-name.glb")
        assert "secret-name" not in error and "projects" not in error
        assert "asset-1" not in error

    def test_the_route_answers_400_with_the_message(self):
        m = _wire(_load(fresh=True), head_hits=True)
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        enforcer.enforce.return_value = True
        m.CasbinEnforcer = MagicMock(return_value=enforcer)
        m.request_to_claims = lambda event: {"tokens": ["tester"], "roles": []}
        body = {"databaseId": "testdb1", "assetName": "asset-1", "description": "existing key",
                "isDistributable": True, "tags": [], "bucketExistingKey": "projects/model.glb"}
        response = m.lambda_handler({"requestContext": {"http": {"method": "POST", "path": "/assets"}},
                                     "body": json.dumps(body)}, None)
        assert response["statusCode"] == 400, response
        assert json.loads(response["body"])["message"].endswith(FILE_KEY)
        _assert_nothing_written(m)


@pytest.mark.unit
class TestAFolderKeyIsAccepted:
    def test_a_folder_with_a_marker_object(self):
        m = _wire(_load(fresh=True), head_hits=True)
        row, error = _create(m, "projects/building-a/")
        assert error is None, error
        assert row["assetLocation"] == {"Key": "projects/building-a/"}
        m.assert_existing_key_not_owned.assert_called_once_with(["b1"], "projects/building-a/", "vams-assets")
        m.sqs_client.send_message.assert_called_once()

    def test_a_folder_with_a_trailing_slash_and_no_marker(self):
        m = _wire(_load(fresh=True), head_hits=False, folder_has_objects=True)
        row, error = _create(m, "projects/building-a/")
        assert error is None, error
        assert row["assetLocation"] == {"Key": "projects/building-a/"}

    def test_a_folder_without_its_trailing_slash_when_nothing_is_stored_at_the_key(self):
        m = _wire(_load(fresh=True), head_hits=False, folder_has_objects=True)
        row, error = _create(m, "projects/building-a")
        assert error is None, error
        assert row["assetLocation"] == {"Key": "projects/building-a/"}
        m.check_s3_folder_has_objects.assert_called_once_with("vams-assets", "projects/building-a/")

    def test_an_archived_file_at_the_key_with_objects_under_it_is_the_folder(self):
        # HeadObject answers 404 for a key whose newest version is a delete marker: nothing is stored
        # at the key, so the key names the folder under it.
        m = _wire(_load(fresh=True), head_hits=False, folder_has_objects=True)
        row, error = _create(m, "projects/model.glb")
        assert error is None, error
        assert row["assetLocation"] == {"Key": "projects/model.glb/"}

    def test_nothing_at_the_key_or_under_it_is_still_refused_as_missing(self):
        m = _wire(_load(fresh=True), head_hits=False, folder_has_objects=False)
        row, error = _create(m, "projects/missing")
        assert error is not None and error.endswith(MISSING), error
        _assert_nothing_written(m)


@pytest.mark.unit
class TestTheEarlierChecksRunFirst:
    @pytest.mark.parametrize("key,message", [
        ("pipelines/wf1/output/model.glb", RESERVED),
        ("projects/preview/thumb.png", RESERVED),
    ])
    def test_a_reserved_file_key_gets_the_reserved_message_before_any_s3_read(self, key, message):
        m = _wire(_load(fresh=True), head_hits=True)
        _, error = _create(m, key)
        assert error is not None and error.endswith(message), error
        m.check_s3_key_exists.assert_not_called()

    def test_the_root_and_a_key_outside_the_base_prefix_keep_their_messages(self):
        m = _wire(_load(fresh=True), base_prefix="vams-area/", head_hits=True)
        _, error = _create(m, "vams-area/")
        assert error is not None and "root" in error.lower(), error
        m.check_s3_key_exists.assert_not_called()

    def test_bucket_sync_auto_create_still_derives_a_folder(self):
        m = _wire(_load(fresh=True), head_hits=True)
        m.check_s3_prefix_exists = MagicMock(return_value=True)
        request = m.CreateAssetRequestModel(databaseId="testdb1", assetId="asset-1", assetName="asset-1",
                                            description="auto create", isDistributable=True, tags=[])
        m.create_asset(request, {"tokens": ["SYSTEM_USER"]}, True)
        row = m.asset_table.put_item.call_args.kwargs["Item"]
        assert row["assetLocation"]["Key"].endswith("/")
        m.check_s3_key_exists.assert_not_called()
