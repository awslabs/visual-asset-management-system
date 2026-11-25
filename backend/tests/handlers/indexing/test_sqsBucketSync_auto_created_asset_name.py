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

from unittest.mock import MagicMock

import pytest

from tests.handlers.indexing.test_sqsBucketSync_asset_id_in_use import (  # noqa: E402
    _created, _handle, _real_create, _rejections, _wire_rows,
)
from tests.handlers.indexing.test_sqsBucketSync_location_attribution import (  # noqa: E402,F401
    _history_rows, _load, _restore_module_attrs,
)

# The database bucket sync creates the bucket's assets in (a database id has at least 4 characters)
DATABASE = "default-db"
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


def _validators():
    import common.validators as validators
    return validators


@pytest.mark.unit
class TestTheDerivedName:
    @NAMED
    def test_each_refused_character_becomes_an_underscore(self, folder):
        m = _load()
        assert m.auto_created_asset_name(folder) == FOLDERS[folder]

    @NAMED
    def test_the_name_satisfies_the_asset_name_rule_and_the_folder_the_id_rule(self, folder):
        m = _load()
        validate = _validators().validate
        assert validate({"assetId": {"value": folder, "validator": "ASSET_ID"}})[0] is True
        assert validate({"assetName": {"value": folder, "validator": "OBJECT_NAME"}})[0] is False
        assert validate({"assetName": {"value": m.auto_created_asset_name(folder),
                                       "validator": "OBJECT_NAME"}})[0] is True

    @pytest.mark.parametrize("folder", ["site-01", "Site 01 v2.0", "a_b.c-d"])
    def test_control_a_name_the_rule_accepts_is_kept(self, folder):
        m = _load()
        assert m.auto_created_asset_name(folder) == folder

    def test_the_description_is_cut_to_its_limit(self):
        m = _load()
        description = m.auto_created_asset_description(LONG)
        assert len(description) == 256
        assert description.startswith("Auto-created asset for survey-")
        assert m.auto_created_asset_description("p,q") == "Auto-created asset for p,q"

    def test_the_limits_are_the_create_models(self):
        m = _load()
        fields = m.CreateAssetRequestModel.__fields__
        assert m.AUTO_CREATED_ASSET_NAME_MAX_LENGTH == fields["assetName"].field_info.max_length
        assert m.AUTO_CREATED_ASSET_DESCRIPTION_MAX_LENGTH == fields["description"].field_info.max_length


@pytest.mark.unit
class TestCreateNewAsset:
    @NAMED
    def test_the_asset_is_created_with_the_folder_as_id_and_the_derived_name(
            self, folder, monkeypatch, _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire_rows(m, [])
        create_asset = _real_create(m, monkeypatch, _restore_module_attrs)
        assert m.create_new_asset("bucket-1", DATABASE, folder) == folder
        [request] = [c.args[0] for c in create_asset.call_args_list]
        assert (request.assetId, request.assetName) == (folder, FOLDERS[folder])
        assert request.description == m.auto_created_asset_description(folder)
        cached = m.asset_cache.get(f"bucket-1:{folder}")
        assert (cached["assetId"], cached["assetName"]) == (folder, FOLDERS[folder])

    def test_control_an_ordinary_folder_keeps_name_equal_to_id(self, monkeypatch,
                                                                _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire_rows(m, [])
        create_asset = _real_create(m, monkeypatch, _restore_module_attrs)
        assert m.create_new_asset("bucket-1", DATABASE, "site-01") == "site-01"
        [request] = [c.args[0] for c in create_asset.call_args_list]
        assert (request.assetId, request.assetName, request.description) == (
            "site-01", "site-01", "Auto-created asset for site-01")


@pytest.mark.unit
class TestADirectWrite:
    """The created-events handler, with the real ID check and create_new_asset (createAsset's create mocked)."""

    @NAMED
    def test_the_file_is_attributed_to_the_new_asset_and_no_error_is_logged(
            self, folder, monkeypatch, _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire_rows(m, [], target_database=DATABASE)
        m.validate_asset_id = _restore_module_attrs["validate_asset_id"]
        create_asset = _real_create(m, monkeypatch, _restore_module_attrs)
        logger = MagicMock()
        monkeypatch.setattr(m, "logger", logger)

        response = _handle(m, f"{folder}/f.txt")

        assert response["batchItemFailures"] == []
        assert _created(create_asset) == {(DATABASE, folder)}
        assert {r["databaseId:assetId:filePath"] for r in _history_rows(m)} == {
            ":".join((DATABASE, folder, "/f.txt"))}
        assert _rejections(m) == []
        logger.error.assert_not_called()
        logger.exception.assert_not_called()
