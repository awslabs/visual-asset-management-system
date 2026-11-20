# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""createAsset refuses a bucketExistingKey inside a VAMS system folder.

A bucketExistingKey that no asset owns used to pass, so an asset could be bound to system data: pipeline
run inputs and outputs (``pipelines/``), previews, temporary uploads, workspaces, or prepared downloads
(``vams.preparedDownloads/``). A single-file asset makes such an object downloadable. Every key whose path,
with or without the bucket's base prefix, has one of those folders as a segment is refused with one 400
message, before any Amazon S3 read; a normal key still reaches the S3 and ownership checks.
"""

from unittest.mock import MagicMock

import pytest

from tests.handlers.assets.test_createAsset_conditional_put import _load

MESSAGE = "The specified bucketExistingKey is inside a folder VAMS reserves for system use. Choose another location."


RESERVED = {"pipeline", "pipelines", "preview", "previews", "temp-upload", "temp-uploads", "workspace",
            "workspaces", "vams.preparedDownloads"}


class _ReachedOwnershipCheck(Exception):
    pass


def _refused(error):
    return error is not None and error.endswith(MESSAGE)


def _unreserved(key):
    """The same key with every reserved folder name lengthened, so no segment names a system folder."""
    return "/".join(part + "-data" if part in RESERVED else part for part in key.split("/"))


def _create(base_prefix, existing_key):
    m = _load(fresh=True)
    m.asset_table = MagicMock()
    m.database_table = MagicMock()
    m.database_table.get_item.return_value = {"Item": {"databaseId": "db-one"}}
    m.validate_tags_exist = MagicMock()
    m.verify_all_required_tags_satisfied = MagicMock()
    m.get_default_bucket_details = MagicMock(return_value={
        "bucketId": "b1", "bucketName": "asset-bucket", "baseAssetsPrefix": base_prefix})
    m.resolve_colocated_bucket_ids = MagicMock(return_value=["b1"])
    m.check_s3_key_exists = MagicMock(return_value=True)
    m.assert_existing_key_not_owned = MagicMock(side_effect=_ReachedOwnershipCheck)
    request = m.CreateAssetRequestModel(
        databaseId="db-one", assetName="asset-a", description="a description", isDistributable=True, tags=[],
        bucketExistingKey=existing_key)
    try:
        m.create_asset(request, {"tokens": ["tester"], "roles": []})
    except _ReachedOwnershipCheck:
        return m, None
    except m.VAMSGeneralErrorResponse as e:
        return m, str(e)
    return m, None


@pytest.mark.unit
class TestReservedExistingKey:
    @pytest.mark.parametrize("base_prefix,existing_key", [
        # A reserved first segment
        ("/", "pipelines/wf1/output/e1/files/model.glb"),
        ("/", "preview/x/thumb.png"),
        ("/", "temp-uploads/u1/part.bin"),
        # A reserved deeper segment
        ("/", "projects/a1/preview/PotreeViewer/octree.bin"),
        ("/", "projects/a1/workspace/scratch.bin"),
        ("/", "projects/pipeline/run/out.glb"),
        # The prepared-downloads folder, first and deeper
        ("/", "vams.preparedDownloads/db1/a1/u/r/archive.zip"),
        ("/", "projects/vams.preparedDownloads/archive.zip"),
        # Under a base prefix, given with and without it
        ("vams-area/", "vams-area/pipelines/wf1/output/model.glb"),
        ("vams-area/", "pipelines/wf1/output/model.glb"),
        ("vams-area/", "vams-area/a1/previews/thumb.png"),
        ("vams-area/", "vams-area/vams.preparedDownloads/archive.zip"),
    ])
    def test_a_key_inside_a_system_folder_is_refused_before_any_s3_read(self, base_prefix, existing_key):
        m, error = _create(base_prefix, existing_key)
        assert _refused(error), error
        m.check_s3_key_exists.assert_not_called()
        m.assert_existing_key_not_owned.assert_not_called()
        # A segment that only contains a reserved name is a normal segment
        m, error = _create(base_prefix, _unreserved(existing_key))
        assert error is None, (existing_key, error)
        m.assert_existing_key_not_owned.assert_called_once()

    @pytest.mark.parametrize("base_prefix,existing_key", [
        ("/", "projects/model.glb"),
        ("/", "projects/building-a/"),
        ("vams-area/", "vams-area/projects/model.glb"),
        ("/", "projects/pipelinesdata/model.glb"),
        ("/", "projects/my.preparedDownloads/archive.zip"),
    ])
    def test_a_normal_key_reaches_the_s3_and_ownership_checks(self, base_prefix, existing_key):
        m, error = _create(base_prefix, existing_key)
        assert error is None
        m.check_s3_key_exists.assert_called_once()
        m.assert_existing_key_not_owned.assert_called_once()
        # The same key placed inside a system folder is refused
        reserved_key = existing_key.replace("projects/", "projects/pipelines/", 1)
        assert _refused(_create(base_prefix, reserved_key)[1])


ASSET_ID_MESSAGE = "The specified assetId is the name of a folder VAMS reserves for system use. Choose another asset ID."


def _create_with_id(asset_id, existing_key=None):
    m = _load(fresh=True)
    m.asset_table = MagicMock()
    m.asset_table.get_item.return_value = {}
    m.database_table = MagicMock()
    # The database lookup is the first read after the asset id checks; answering "no database"
    # ends the call there, so reaching it shows the id was accepted.
    m.database_table.get_item.return_value = {}
    request = m.CreateAssetRequestModel(
        databaseId="db-one", assetName="asset-a", description="a description", isDistributable=True, tags=[],
        assetId=asset_id, bucketExistingKey=existing_key)
    try:
        m.create_asset(request, {"tokens": ["tester"], "roles": []})
    except m.VAMSGeneralErrorResponse as e:
        return m, str(e)
    return m, None


@pytest.mark.unit
class TestReservedAssetId:
    @pytest.mark.parametrize("asset_id", sorted(RESERVED))
    def test_an_asset_id_that_names_a_system_folder_is_refused_before_any_read(self, asset_id):
        m, error = _create_with_id(asset_id)
        assert error is not None and error.endswith(ASSET_ID_MESSAGE), error
        m.asset_table.get_item.assert_not_called()
        m.database_table.get_item.assert_not_called()
        # The same with a bucketExistingKey: the id is refused whatever the location
        m, error = _create_with_id(asset_id, "projects/model.glb")
        assert error is not None and error.endswith(ASSET_ID_MESSAGE), error
        # An id that only contains the name, and a generated id, reach the database lookup
        for accepted in (asset_id + "-model", "my-" + asset_id, None):
            m, error = _create_with_id(accepted)
            assert error is not None and "Database does not exist" in error, (accepted, error)
            m.database_table.get_item.assert_called_once()
