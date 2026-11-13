# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The file and version handlers record each action after it succeeded.

A folder operation records one record per file it acted on, all delivered in the request's flush; a
failed or denied operation records nothing (the entrypoint records a failed request's outcome).
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.assets import assetFiles as files_svc
from backend.backend.handlers.assets import assetVersions as versions_svc

CLAIMS = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": False}
PATH = {"databaseId": "db1", "assetId": "asset-01"}


def _event(body, path=None):
    return {"pathParameters": dict(path or PATH), "body": json.dumps(body), "queryStringParameters": {},
            "requestContext": {"http": {"method": "DELETE", "path": "/database/db1/assets/asset-01/deleteFile"}}}


@pytest.fixture
def recorder(monkeypatch):
    recorder = MagicMock()
    for module in (files_svc, versions_svc):
        monkeypatch.setattr(module, "log_action", recorder)
        monkeypatch.setattr(module, "claims_and_roles", CLAIMS, raising=False)
    return recorder


@pytest.mark.unit
class TestFileRecords:
    def test_a_folder_delete_records_one_record_per_file(self, recorder, monkeypatch):
        monkeypatch.setattr(files_svc, "delete_file", lambda *args: files_svc.FileOperationResponseModel(
            success=True, message="deleted", affectedFiles=["/dir/a.glb", "/dir/b.glb"]))
        event = _event({"filePath": "/dir/", "isPrefix": True, "confirmPermanentDelete": True})
        assert files_svc.handle_delete_file(event, None)["statusCode"] == 200
        assert [call.kwargs["file_path"] for call in recorder.call_args_list] == ["/dir/a.glb", "/dir/b.glb"]
        assert all(call.args == (event, "fileDelete") for call in recorder.call_args_list)
        assert recorder.call_args.kwargs["changes"] == {"isPrefix": True, "confirmPermanentDelete": True}

    def test_a_file_operation_with_no_file_list_records_the_requested_path(self, recorder, monkeypatch):
        monkeypatch.setattr(files_svc, "archive_file", lambda *args: files_svc.FileOperationResponseModel(
            success=True, message="archived"))
        event = _event({"filePath": "/model.glb"})
        assert files_svc.handle_archive_file(event, None)["statusCode"] == 200
        recorder.assert_called_once()
        assert recorder.call_args.args == (event, "fileArchive")
        assert recorder.call_args.kwargs["file_path"] == "/model.glb"

    def test_a_refused_delete_records_nothing(self, recorder, monkeypatch):
        def refuse(*args):
            raise files_svc.VAMSGeneralErrorResponse("Not authorized", 403)
        monkeypatch.setattr(files_svc, "delete_file", refuse)
        files_svc.handle_delete_file(_event({"filePath": "/model.glb", "confirmPermanentDelete": True}), None)
        recorder.assert_not_called()

    def test_a_copy_records_source_and_target(self, recorder, monkeypatch):
        monkeypatch.setattr(files_svc, "copy_file", lambda *args: files_svc.FileOperationResponseModel(
            success=True, message="copied", affectedFiles=["/b.glb"]))
        event = _event({"sourcePath": "/a.glb", "destinationPath": "/b.glb", "destinationAssetId": "asset-02",
                        "destinationDatabaseId": "db-two"})
        assert files_svc.handle_copy_file(event, None)["statusCode"] == 200
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args == (event, "fileCopy")
        assert (kwargs["file_path"], kwargs["targetFilePath"]) == ("/a.glb", "/b.glb")
        assert (kwargs["targetDatabaseId"], kwargs["targetAssetId"]) == ("db-two", "asset-02")
        assert kwargs["file_count"] == 1

    def test_a_version_revert_records_both_versions(self, recorder, monkeypatch):
        monkeypatch.setattr(files_svc, "revert_file_version", lambda *args: files_svc.RevertFileVersionResponseModel(
            success=True, message="reverted", filePath="/a.glb", revertedFromVersionId="v1", newVersionId="v3"))
        event = _event({"filePath": "/a.glb"}, path={**PATH, "versionId": "v1"})
        assert files_svc.handle_revert_file_version(event, None)["statusCode"] == 200
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args == (event, "fileVersionRevert")
        assert kwargs["changes"] == {"versionId": "v3"} and kwargs["previous"] == {"versionId": "v1"}


@pytest.mark.unit
class TestVersionRecords:
    def test_a_version_create_records_the_request_without_the_file_list(self, recorder, monkeypatch):
        monkeypatch.setattr(versions_svc, "create_asset_version", lambda *args: versions_svc.AssetVersionOperationResponseModel(
            success=True, message="ok", assetId="asset-01", assetVersionId="3", operation="create",
            timestamp="2026-11-13T00:00:00Z"))
        event = _event({"useLatestFiles": True, "comment": "Release 3"})
        assert versions_svc.handle_create_version(event, None)["statusCode"] == 200
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args == (event, "assetVersionCreate")
        assert kwargs["assetVersionId"] == "3" and kwargs["changes"]["comment"] == "Release 3"
        assert "files" not in kwargs["changes"] and kwargs["file_count"] is None

    def test_a_refused_version_create_records_nothing(self, recorder, monkeypatch):
        def refuse(*args):
            raise versions_svc.VAMSGeneralErrorResponse("Not authorized", 403)
        monkeypatch.setattr(versions_svc, "create_asset_version", refuse)
        versions_svc.handle_create_version(_event({"useLatestFiles": True, "comment": "Release 3"}), None)
        recorder.assert_not_called()
