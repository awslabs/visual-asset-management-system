#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for execute-workflow input S3-existence verification: the input file/folder each selected
input points at must exist in S3 before a workflow launches (single input today, multi-file/folder
ready).

Imports of the handler are deferred into each test so they resolve AFTER the root conftest's
autouse mock-import + env setup runs (matching test_executeWorkflow_input_parameters.py); a
collection-time import would fail resolving SSM resource names. Run as part of the workflows
suite (the CI default), not in isolation."""

import json
from unittest.mock import MagicMock, patch

import pytest
import botocore


def _ew():
    from backend.backend.handlers.workflows import executeWorkflow as ew
    return ew


def _client_error(code):
    return botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": code}}, "HeadObject")


@pytest.mark.unit
class TestInputExistsInS3:
    def test_specific_file_exists(self):
        ew = _ew()
        s3 = MagicMock()
        s3.head_object.return_value = {"ContentLength": 10}
        with patch.object(ew, "s3c", s3):
            assert ew.input_exists_in_s3("bkt", "a1/test/x.glb") is True
        s3.head_object.assert_called_once_with(Bucket="bkt", Key="a1/test/x.glb")

    def test_specific_file_missing_returns_false(self):
        ew = _ew()
        s3 = MagicMock()
        s3.head_object.side_effect = _client_error("404")
        with patch.object(ew, "s3c", s3):
            assert ew.input_exists_in_s3("bkt", "a1/missing.glb") is False

    def test_nosuchkey_returns_false(self):
        ew = _ew()
        s3 = MagicMock()
        s3.head_object.side_effect = _client_error("NoSuchKey")
        with patch.object(ew, "s3c", s3):
            assert ew.input_exists_in_s3("bkt", "a1/missing.glb") is False

    def test_permission_error_reraises(self):
        ew = _ew()
        # A non-404 error (e.g. AccessDenied) must not be swallowed as "missing" — it re-raises so
        # the launch fails loudly rather than silently skipping the guard.
        s3 = MagicMock()
        s3.head_object.side_effect = _client_error("AccessDenied")
        with patch.object(ew, "s3c", s3):
            with pytest.raises(botocore.exceptions.ClientError):
                ew.input_exists_in_s3("bkt", "a1/x.glb")

    def test_folder_prefix_exists_when_objects_present(self):
        ew = _ew()
        s3 = MagicMock()
        s3.list_objects_v2.return_value = {"KeyCount": 1, "Contents": [{"Key": "a1/f/x.glb"}]}
        with patch.object(ew, "s3c", s3):
            assert ew.input_exists_in_s3("bkt", "a1/f/") is True
        s3.list_objects_v2.assert_called_once_with(Bucket="bkt", Prefix="a1/f/", MaxKeys=1)
        s3.head_object.assert_not_called()

    def test_folder_prefix_empty_returns_false(self):
        ew = _ew()
        s3 = MagicMock()
        s3.list_objects_v2.return_value = {"KeyCount": 0, "Contents": []}
        with patch.object(ew, "s3c", s3):
            assert ew.input_exists_in_s3("bkt", "a1/emptyfolder/") is False

    def test_empty_key_returns_false(self):
        ew = _ew()
        assert ew.input_exists_in_s3("bkt", "") is False


@pytest.mark.unit
class TestVerifyInputsExistInS3:
    def test_returns_missing_subset(self):
        ew = _ew()
        s3 = MagicMock()
        s3.head_object.side_effect = [
            {"ContentLength": 1},                # a.glb exists
            _client_error("404"),                # b.glb missing
        ]
        with patch.object(ew, "s3c", s3):
            missing = ew.verify_inputs_exist_in_s3("bkt", ["a1/a.glb", "a1/b.glb"])
        assert missing == ["a1/b.glb"]

    def test_all_present_returns_empty(self):
        ew = _ew()
        s3 = MagicMock()
        s3.head_object.return_value = {"ContentLength": 1}
        with patch.object(ew, "s3c", s3):
            assert ew.verify_inputs_exist_in_s3("bkt", ["a1/a.glb", "a1/b.glb"]) == []


# ---------------------------------------------------------------------------
# Handler-level: a missing input short-circuits with 404 before launch.
# ---------------------------------------------------------------------------

def _claims():
    return {"tokens": ["user@x"]}


def _event(body):
    return {
        "requestContext": {"http": {"method": "POST",
                                    "path": "/database/dbx/workflows/wfx/assets/a1/execute"},
                           "authorizer": {}},
        "pathParameters": {"databaseId": "dbx", "workflowId": "wfx", "assetId": "a1"},
        "body": json.dumps(body),
    }


def _drive(body, missing):
    ew = _ew()
    with patch.object(ew, "request_to_claims", return_value=_claims()), \
         patch.object(ew, "CasbinEnforcer") as MockEnf, \
         patch.object(ew, "get_asset", return_value=[{"assetId": "a1", "bucketId": "b1",
                      "assetLocation": {"Key": "a1/"}, "assetName": "n", "tags": []}]), \
         patch.object(ew, "get_workflow", return_value=[{"workflowId": "wfx", "databaseId": "dbx",
                      "workflow_arn": "arn:sm",
                      "specifiedPipelines": {"functions": [{"name": "p1", "inputParameters": "{}"}]}}]), \
         patch.object(ew, "validate_pipelines", return_value=(True, "")), \
         patch.object(ew, "get_default_bucket_details",
                      return_value={"bucketName": "bkt", "baseAssetsPrefix": "", "bucketId": "b1"}), \
         patch.object(ew, "verify_inputs_exist_in_s3", return_value=missing) as mock_verify, \
         patch.object(ew, "get_workflow_executions", return_value={"Items": []}), \
         patch.object(ew, "build_pipeline_input_metadata", return_value={"VAMS": {}}), \
         patch.object(ew, "launchWorkflow", return_value="EXEC123") as mock_launch:
        MockEnf.return_value.enforceAPI.return_value = True
        MockEnf.return_value.enforce.return_value = True
        resp = ew.lambda_handler(_event(body), MagicMock())
    return resp, mock_verify, mock_launch


@pytest.mark.unit
class TestExecuteWorkflowInputExistenceGate:
    def test_missing_input_returns_404_and_does_not_launch(self):
        resp, mock_verify, mock_launch = _drive(
            {"workflowDatabaseId": "dbx", "fileKey": "/f/x.glb"}, missing=["a1/f/x.glb"])
        assert resp["statusCode"] == 404
        assert "do not exist" in json.loads(resp["body"])["message"]
        mock_launch.assert_not_called()

    def test_present_input_launches(self):
        resp, mock_verify, mock_launch = _drive(
            {"workflowDatabaseId": "dbx", "fileKey": "/f/x.glb"}, missing=[])
        assert resp["statusCode"] == 200
        mock_launch.assert_called_once()

    def test_specific_file_checked_as_object(self):
        # A requested file is verified as the resolved full key.
        _resp, mock_verify, _launch = _drive(
            {"workflowDatabaseId": "dbx", "fileKey": "/f/x.glb"}, missing=[])
        keys = mock_verify.call_args[0][1]
        assert keys == ["a1/f/x.glb"]

    def test_no_file_checks_asset_base_prefix_as_folder(self):
        # No requested file -> the asset base prefix is verified as a folder (trailing slash).
        _resp, mock_verify, _launch = _drive({"workflowDatabaseId": "dbx"}, missing=[])
        keys = mock_verify.call_args[0][1]
        assert keys == ["a1/"]
