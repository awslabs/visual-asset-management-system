# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Uploads, workflow outputs and workflow inputs of a single-file asset resolve the keys it holds.

A single-file asset (location key with no trailing ``/``) holds its object, its companion folder
``{file key}/`` and its own previews. An upload or workflow output lands in the companion folder, a
file's preview at its own preview key. A user upload at the asset's own file path writes a new version
of its object, as for any other file; a workflow output at that path is refused. A workflow input of the asset resolves to the object
itself, not to the companion folder. Asset-type detection after an upload counts only the keys the
asset holds.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402
from backend.backend.handlers.assets import sqsUploadFileLarge  # noqa: F401,E402
from backend.tests.handlers.assets.test_uploadFile_preview_extension_init import (  # noqa: E402
    _asset, _initialize_upload_env, _request)
from backend.tests.handlers.assets.test_large_file_workflow_provenance import _external_request  # noqa: E402
from backend.tests.handlers.workflows.test_executeWorkflow import ewv2  # noqa: E402
from backend.tests.handlers.workflows.test_output_metadata_type_key import po  # noqa: E402

FILE_LOCATION = "p1/model.glb"
CLAIMS = {"tokens": ["alice@corp"]}
OWN_FILE_MESSAGE = "A workflow output cannot replace the file of a single-file asset"


@pytest.mark.unit
class TestUploadKey:
    @pytest.mark.parametrize("relative,expected", [
        ("/sub/x.obj", "p1/model.glb/sub/x.obj"),
        ("sub/x.obj", "p1/model.glb/sub/x.obj"),
        ("/model.glb.bak", "p1/model.glb/model.glb.bak"),
        ("/model.glb.previewFile.png", "p1/model.glb.previewFile.png"),
        ("/model.glb", "p1/model.glb"),
    ])
    def test_a_single_file_location_resolves_inside_the_asset(self, relative, expected):
        from backend.backend.handlers.assets import uploadFile as uf
        assert uf.normalize_s3_path(FILE_LOCATION, relative) == expected

    @pytest.mark.parametrize("relative,expected", [("/out/a.obj", "asset-1/out/a.obj"),
                                                   ("asset-1/out/a.obj", "asset-1/out/a.obj")])
    def test_control_a_folder_location_joins_as_before(self, relative, expected):
        from backend.backend.handlers.assets import uploadFile as uf
        assert uf.normalize_s3_path("asset-1/", relative) == expected


@pytest.mark.unit
class TestInitializeUpload:
    def test_an_upload_lands_in_the_companion_folder(self):
        with _initialize_upload_env(_asset(assetLocation={"Key": FILE_LOCATION})) as (uf, mock_s3):
            uf.initialize_upload(_request("assetFile", "/out/scan.laz"), CLAIMS)
        key = mock_s3.create_multipart_upload.call_args.kwargs["Key"]
        assert key.endswith("p1/model.glb/out/scan.laz")

    @pytest.mark.parametrize("relative", ["/model.glb", "model.glb", FILE_LOCATION])
    def test_an_upload_at_the_own_file_path_writes_a_new_version_of_the_object(self, relative):
        with _initialize_upload_env(_asset(assetLocation={"Key": FILE_LOCATION})) as (uf, mock_s3):
            uf.initialize_upload(_request("assetFile", relative), CLAIMS)
        key = mock_s3.create_multipart_upload.call_args.kwargs["Key"]
        assert key.endswith(FILE_LOCATION)
        assert not key.endswith(FILE_LOCATION + "/model.glb")


def _complete_external(relative, file_size=1024):
    """Run complete_external_upload (a workflow output write) for one file of a single-file asset."""
    from backend.backend.handlers.assets import uploadFile as uf
    request_model = _external_request(files=[{"relativeKey": relative, "tempKey": "temp/up-1/x"}])
    with patch.object(uf, 'get_upload_details', return_value={
        'assetId': 'asset-1', 'databaseId': 'db-1', 'uploadType': 'assetFile',
        'isExternalUpload': True, 'temporaryPrefix': 'temp/up-1/',
    }), patch.object(uf, 'get_asset_details', return_value={
        'assetId': 'asset-1', 'databaseId': 'db-1', 'bucketId': 'bucket-1',
        'assetLocation': {'Key': FILE_LOCATION},
    }), patch.object(uf, 'get_default_bucket_details', return_value={
        'bucketId': 'bucket-1', 'bucketName': 'asset-bucket', 'baseAssetsPrefix': '',
    }), patch.object(uf, 'get_database_details', return_value={'databaseId': 'db-1'}), \
            patch.object(uf, 'asset_upload_table', MagicMock()), \
            patch.object(uf, 'delete_upload_details', MagicMock()), \
            patch.object(uf, 's3', MagicMock()) as mock_s3, \
            patch.object(uf, 'queue_large_file_for_processing', return_value=True) as queued:
        mock_s3.head_object.return_value = {'ContentLength': file_size}
        uf.claims_and_roles = {"tokens": ["alice@corp"]}
        response = uf.complete_external_upload("up-1", request_model, {})
    return response, queued


@pytest.mark.unit
class TestWorkflowOutputs:
    def test_an_output_lands_in_the_companion_folder(self):
        _, queued = _complete_external("/out/scan.laz", file_size=6 * 1024 ** 3)
        assert queued.call_args.args[0]["finalS3Key"] == "p1/model.glb/out/scan.laz"

    def test_an_output_at_the_own_file_path_is_refused(self):
        response, queued = _complete_external("/model.glb", file_size=6 * 1024 ** 3)
        assert [r.success for r in response.fileResults] == [False]
        assert OWN_FILE_MESSAGE in response.fileResults[0].error
        queued.assert_not_called()

    @pytest.mark.parametrize("relative,expected", [
        ("/out/scan.laz", "p1/model.glb/out/scan.laz"),
        ("model.glb.previewFile.png", "p1/model.glb.previewFile.png"),
    ])
    def test_the_destination_check_resolves_as_the_upload_does(self, relative, expected):
        asset = {"assetId": "asset-1", "assetLocation": {"Key": FILE_LOCATION}}
        assert po._destination_key({"relativeFilePath": relative}, asset, "") == expected


def _contents(*keys):
    return [{"Key": key} for key in keys]


@pytest.mark.unit
class TestAssetTypeAfterUpload:
    """The sample reads the location's own keys by their own prefixes: the object with a one-key listing,
    its previews and its companion folder. The bare location is never listed as a wide prefix, so keys that
    only share its characters cannot fill the sample."""

    @staticmethod
    def _listing(*keys):
        calls = []

        def _list_all_objects(bucket, prefix, client=None, max_objects=None):
            calls.append((prefix, max_objects))
            listed = _contents(*sorted(k for k in keys if k.startswith(prefix)))
            return listed[:max_objects] if max_objects is not None else listed

        return _list_all_objects, calls

    @pytest.mark.parametrize("module_name", ["uploadFile", "sqsUploadFileLarge"])
    def test_keys_that_only_share_the_characters_are_not_counted(self, module_name):
        import importlib
        module = importlib.import_module(f"backend.backend.handlers.assets.{module_name}")
        fake, calls = self._listing("p1/model.glb", "p1/model.glb.bak", "p1/model.glbx/inner.txt")
        with patch.object(module, "list_all_objects", side_effect=fake), \
                patch.object(module, "is_file_archived", return_value=False):
            assert module.determine_asset_type("asset-1", "bucket", FILE_LOCATION) == ".glb"
        assert all(max_objects == 1 for prefix, max_objects in calls if prefix == FILE_LOCATION), calls

    @pytest.mark.parametrize("module_name", ["uploadFile", "sqsUploadFileLarge"])
    def test_a_companion_file_makes_a_folder(self, module_name):
        import importlib
        module = importlib.import_module(f"backend.backend.handlers.assets.{module_name}")
        fake, calls = self._listing("p1/model.glb", "p1/model.glb-old/n.txt", "p1/model.glb/sub/x.obj")
        with patch.object(module, "list_all_objects", side_effect=fake), \
                patch.object(module, "is_file_archived", return_value=False):
            assert module.determine_asset_type("asset-1", "bucket", FILE_LOCATION) == "folder"
        assert ("p1/model.glb/", module.ASSET_TYPE_DETECTION_SAMPLE_SIZE) in calls, calls


@pytest.mark.unit
class TestWorkflowInputs:
    @pytest.mark.parametrize("relative,expected", [
        ("/", FILE_LOCATION), ("", FILE_LOCATION), ("/model.glb", FILE_LOCATION),
        ("/sub/x.obj", "p1/model.glb/sub/x.obj"), ("/sub/", "p1/model.glb/sub/"),
    ])
    def test_a_single_file_input_resolves_inside_the_asset(self, relative, expected):
        assert ewv2._input_key(FILE_LOCATION, relative) == expected

    @pytest.mark.parametrize("relative,expected", [("/", "assetX/"), ("", "assetX/"),
                                                   ("/a/b.glb", "assetX/a/b.glb")])
    def test_control_a_folder_input_resolves_as_before(self, relative, expected):
        assert ewv2._input_key("assetX/", relative) == expected

    def test_the_input_check_heads_the_object_of_a_whole_asset_selection(self):
        asset = {"databaseId": "db1", "assetId": "a1", "bucketId": "b1", "assetLocation": {"Key": FILE_LOCATION}}
        items = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/"}]
        seen = []

        def _exists(bucket, key, version_id=""):
            seen.append(key)
            return True, "v-1"

        with patch.object(ewv2, "_asset_bucket_details", return_value={"bucketName": "bkt"}), \
                patch.object(ewv2, "_input_exists_in_s3", side_effect=_exists):
            missing = ewv2._verify_inputs_exist(items, {("db1", "a1"): asset})
        assert missing == []
        assert seen == [FILE_LOCATION]
