# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bucket sync names an auto-created asset to the asset name rule, whatever its folder is called (#1813).

A file written directly to S3 under a new first-level folder becomes a new asset whose ID is the folder name.
The asset ID rule (`filename_pattern`) admits commas, apostrophes and non-ASCII letters; the asset name rule
(`object_name_pattern`) does not, and the description `Auto-created asset for {id}` outgrows its 256
characters once the folder name is longer than 233. Bucket sync used the folder name as the name, so for such
a folder the create failed validation, bucket sync logged an error and the file was attributed to no asset.
The name is now the folder name with each character the name rule refuses replaced by `_`, the description
is cut to its limit, and the asset ID stays the folder name.
"""

import json
from unittest.mock import MagicMock

import pytest

from tests.handlers.indexing import test_sqsBucketSync_recreation_guard as guard

# The database bucket sync creates the bucket's assets in (a database id has at least 4 characters)
DATABASE = "default-db"
BUCKET = "asset-bucket"
LONG = "survey-" + "x" * 226 + ",site" + "y" * 2  # 240 characters, a comma in it
FOLDERS = {
    # folder name (= asset ID) -> the name the asset gets
    "p,q": "p_q",
    "o'brien": "o_brien",
    "Ünïcødé Ordner": "_n_c_d_ Ordner",
    "名前-2026": "__-2026",
    LONG: LONG.replace(",", "_"),
}
NAMED = pytest.mark.parametrize("folder", list(FOLDERS), ids=["comma", "apostrophe", "latin-1", "cjk", "long"])


@pytest.fixture
def m(monkeypatch):
    # A fresh module: other test files replace this module's functions in place on the cached one.
    monkeypatch.setattr(guard, "_cached_guard_module", None)
    return guard._load()


def _validators():
    import common.validators as validators
    return validators


def _real_create(m):
    """The real create_new_asset and request model, with createAsset's own create mocked."""
    create_asset = MagicMock(side_effect=lambda request, claims, system: MagicMock(assetId=request.assetId))
    m.create_asset = create_asset
    return create_asset


def _wire_direct_write(m):
    """A new first-level folder at the bucket root: no live or archived asset with its name in the bucket.

    The asset ID check (`validate_asset_id`), the key parsing and `create_new_asset` are the real ones.
    """
    m.asset_bucket_name = BUCKET
    m.asset_bucket_prefix = "/"
    m.get_bucket_id = MagicMock(return_value="bucket-1")
    m.lookup_asset = MagicMock(return_value=None)
    m.lookup_archived_asset = MagicMock(return_value=None)
    m.get_or_create_database_for_bucket = MagicMock(return_value=DATABASE)
    m.object_still_exists = MagicMock(return_value=True)
    m.update_s3_metadata = MagicMock(return_value=True)
    m.update_asset_type = MagicMock(return_value=True)
    m.write_file_version_history = MagicMock()
    m.s3_client = MagicMock()
    m.s3_client.head_object.return_value = {"VersionId": "v1", "Metadata": {}}
    m.publish_to_file_indexer_sns = MagicMock(return_value=True)
    m.publish_to_orchestration_bus = MagicMock()
    m.logger = MagicMock()


def _handle(m, key):
    """The created-events handler for one SQS message carrying one direct write of `key`."""
    record = {"eventSource": "aws:s3", "eventName": "ObjectCreated:Put",
              "s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}
    message = {"messageId": "msg-1", "eventSource": "aws:sqs",
               "body": json.dumps({"Type": "Notification", "Message": json.dumps({"Records": [record]})})}
    return m.lambda_handler_created({"Records": [message]}, MagicMock())


def _created(create_asset):
    return {(c.args[0].databaseId, c.args[0].assetId) for c in create_asset.call_args_list}


def _history_files(m):
    return {c.args[:3] for c in m.write_file_version_history.call_args_list}


@pytest.mark.unit
class TestTheDerivedName:
    @NAMED
    def test_each_refused_character_becomes_an_underscore(self, m, folder):
        assert m.auto_created_asset_name(folder) == FOLDERS[folder]

    @NAMED
    def test_the_name_satisfies_the_asset_name_rule_and_the_folder_the_id_rule(self, m, folder):
        validate = _validators().validate
        assert validate({"assetId": {"value": folder, "validator": "ASSET_ID"}})[0] is True
        assert validate({"assetName": {"value": folder, "validator": "OBJECT_NAME"}})[0] is False
        assert validate({"assetName": {"value": m.auto_created_asset_name(folder),
                                       "validator": "OBJECT_NAME"}})[0] is True

    @pytest.mark.parametrize("folder", ["site-01", "Site 01 v2.0", "a_b.c-d"])
    def test_control_a_name_the_rule_accepts_is_kept(self, m, folder):
        assert m.auto_created_asset_name(folder) == folder

    def test_the_description_is_cut_to_its_limit(self, m):
        description = m.auto_created_asset_description(LONG)
        assert len(description) == 256
        assert description.startswith("Auto-created asset for survey-")
        assert m.auto_created_asset_description("p,q") == "Auto-created asset for p,q"

    def test_the_limits_are_the_create_models(self, m):
        fields = m.CreateAssetRequestModel.__fields__
        assert m.AUTO_CREATED_ASSET_NAME_MAX_LENGTH == fields["assetName"].field_info.max_length
        assert m.AUTO_CREATED_ASSET_DESCRIPTION_MAX_LENGTH == fields["description"].field_info.max_length


@pytest.mark.unit
class TestCreateNewAsset:
    @NAMED
    def test_the_asset_is_created_with_the_folder_as_id_and_the_derived_name(self, m, folder):
        create_asset = _real_create(m)
        assert m.create_new_asset("bucket-1", DATABASE, folder) == folder
        [request] = [c.args[0] for c in create_asset.call_args_list]
        assert (request.assetId, request.assetName) == (folder, FOLDERS[folder])
        assert request.description == m.auto_created_asset_description(folder)
        cached = m.asset_cache.get(f"bucket-1:{folder}")
        assert (cached["assetId"], cached["assetName"]) == (folder, FOLDERS[folder])

    def test_control_an_ordinary_folder_keeps_name_equal_to_id(self, m):
        create_asset = _real_create(m)
        assert m.create_new_asset("bucket-1", DATABASE, "site-01") == "site-01"
        [request] = [c.args[0] for c in create_asset.call_args_list]
        assert (request.assetId, request.assetName, request.description) == (
            "site-01", "site-01", "Auto-created asset for site-01")


@pytest.mark.unit
class TestADirectWrite:
    """The created-events handler, with the real ID check and create_new_asset (createAsset's create mocked)."""

    @NAMED
    def test_the_file_is_attributed_to_the_new_asset_and_no_error_is_logged(self, m, folder):
        _wire_direct_write(m)
        create_asset = _real_create(m)

        response = _handle(m, f"{folder}/f.txt")

        assert response["batchItemFailures"] == []
        assert _created(create_asset) == {(DATABASE, folder)}
        assert _history_files(m) == {(DATABASE, folder, "f.txt")}
        m.update_s3_metadata.assert_called_once_with(BUCKET, f"{folder}/f.txt", DATABASE, folder)
        m.logger.error.assert_not_called()
        m.logger.exception.assert_not_called()
