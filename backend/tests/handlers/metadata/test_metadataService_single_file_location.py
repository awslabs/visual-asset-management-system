# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""File metadata of a single-file asset is checked against the keys the asset holds.

Before file metadata is written, ``validate_file_exists`` checks the file in S3. For a single-file asset
(location key with no trailing ``/``) it appended ``/`` and the file path to the location, so the asset's
own file read as missing. The asset holds its object, its companion folder ``{file key}/`` and its own
previews; a key that only shares its characters is never the one checked. A folder asset is the control.
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.metadata import metadataService
from backend.backend.handlers.metadata.metadataService import VAMSGeneralErrorResponse


def _check(location, file_path, existing):
    s3 = MagicMock()

    def _head(Bucket, Key):
        if Key in existing:
            return {}
        raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    s3.head_object.side_effect = _head
    asset = {"databaseId": "db1", "assetId": "a1", "bucketId": "b1", "assetLocation": {"Key": location}}
    with patch.object(metadataService, "validate_asset_exists", MagicMock(return_value=asset)), \
            patch.object(metadataService, "get_bucket_details", MagicMock(return_value={"bucketName": "bkt"})), \
            patch.object(metadataService, "s3", s3):
        result = metadataService.validate_file_exists("db1", "a1", file_path)
    return result, [c.kwargs["Key"] for c in s3.head_object.call_args_list]


EXISTING = {"p1/model.glb", "p1/model.glb.bak", "p1/model.glb/sub/x.txt"}


@pytest.mark.unit
class TestValidateFileExists:
    def test_a_single_file_asset_checks_its_own_object(self):
        assert _check("p1/model.glb", "/model.glb", EXISTING) == (True, ["p1/model.glb"])

    def test_a_single_file_asset_checks_its_companion_folder(self):
        assert _check("p1/model.glb", "/sub/x.txt", EXISTING) == (True, ["p1/model.glb/sub/x.txt"])

    def test_a_path_never_reaches_a_key_that_only_shares_its_characters(self):
        with pytest.raises(VAMSGeneralErrorResponse):
            _check("p1/model.glb", "/model.glb.bak", EXISTING)

    def test_the_asset_as_a_whole_is_no_file(self):
        with pytest.raises(VAMSGeneralErrorResponse):
            _check("p1/model.glb", "/", EXISTING)

    def test_control_a_folder_asset_joins_the_path(self):
        assert _check("proj/a/", "/sub/x.txt", {"proj/a/sub/x.txt"}) == (True, ["proj/a/sub/x.txt"])
