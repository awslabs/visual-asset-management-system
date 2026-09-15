# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The metadata write-back cross-call addresses the metadata service by its route CONSTANT.

Guards S2-BACKEND-161: the synthetic path was built from string literals, while metadataService
dispatches on ``ApiRoute.matches()``. The literals matched the constants exactly, so nothing
misbehaved -- but a route-template rename would have stopped the pipeline metadata write-back at
runtime with only the generic ``METADATA_WRITE_BACK_FAILURE`` visible, while CDK synth, the route
registry test and every unit test stayed green.

The assertions compare the path handed to the cross-call against the constant, so a rename now fails
here instead of in production. The same file already addressed its file-ingestion cross-call this way.
"""

import json
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

if "models.assetsV3" not in sys.modules:
    _assetsv3_stub = types.ModuleType("models.assetsV3")
    _assetsv3_stub.AssetUploadTableModel = MagicMock()
    sys.modules["models.assetsV3"] = _assetsv3_stub

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md")
os.environ.setdefault("FILE_UPLOAD_LAMBDA_FUNCTION_NAME", "t-fu")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "t-upload")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "t-db")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf

from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po
from backend.backend.common.apiRoutes import (
    API_ASSET_METADATA,
    API_FILE_METADATA,
    METADATA_ROUTES,
)

DB = "smoke-db"
ASSET = "pump.glb"
_FILE_CONTENT = json.dumps({"metadata": [{"metadataKey": "k", "metadataValue": "v"}]})


def _ok_payload():
    class _Payload:
        def read(self):
            return json.dumps({"statusCode": 200,
                               "body": json.dumps({"message": "ok"})}).encode("utf-8")
    return {"Payload": _Payload()}


def _invoke(file_path, metadata_type):
    """Run the write-back and return the event handed to the metadata-service cross-call."""
    captured = {}

    def _cross_call(payload):
        captured["event"] = payload
        return _ok_payload()

    with patch.object(po.s3c, "get_object",
                      return_value={"Body": MagicMock(
                          read=MagicMock(return_value=_FILE_CONTENT.encode("utf-8")))}), \
         patch.object(po, "_lambda_metadata_service", side_effect=_cross_call):
        applied = po.process_metadata_file(
            bucket_name="abkt", s3_key="pipelines/p/j/output/E1/metadata/m.json",
            metadata_path_key="pipelines/p/j/output/E1/metadata/",
            database_id=DB, asset_id=ASSET, file_path=file_path,
            metadata_type=metadata_type, request_context={})
    assert applied is not None, "the write-back must have reached the cross-call"
    return captured["event"]


@pytest.mark.unit
class TestMetadataCrossCallPath:

    def test_asset_metadata_uses_the_asset_metadata_route_constant(self):
        event = _invoke(file_path="", metadata_type="metadata")
        expected = API_ASSET_METADATA.path.replace(
            "{databaseId}", DB).replace("{assetId}", ASSET)
        assert event["requestContext"]["http"]["path"] == expected

    def test_file_metadata_uses_the_file_metadata_route_constant(self):
        event = _invoke(file_path="/folder/pump.glb", metadata_type="metadata")
        expected = API_FILE_METADATA.path.replace(
            "{databaseId}", DB).replace("{assetId}", ASSET)
        assert event["requestContext"]["http"]["path"] == expected

    def test_file_attributes_use_the_file_metadata_route_constant(self):
        event = _invoke(file_path="/folder/pump.glb", metadata_type="attributes")
        expected = API_FILE_METADATA.path.replace(
            "{databaseId}", DB).replace("{assetId}", ASSET)
        assert event["requestContext"]["http"]["path"] == expected

    def test_the_two_routes_are_distinguishable(self):
        """Negative control: the asset and file paths must not be the same string, or both
        assertions above would pass with either constant."""
        asset_path = _invoke(file_path="", metadata_type="metadata")[
            "requestContext"]["http"]["path"]
        file_path = _invoke(file_path="/f.glb", metadata_type="metadata")[
            "requestContext"]["http"]["path"]
        assert asset_path != file_path

    def test_the_synthetic_path_matches_the_route_the_metadata_service_dispatches_on(self):
        """The end of the chain: metadataService routes with ApiRoute.matches(), so the produced path
        has to satisfy that matcher and no other metadata route."""
        for file_path, route in ((("", API_ASSET_METADATA)), ("/f.glb", API_FILE_METADATA)):
            path = _invoke(file_path=file_path, metadata_type="metadata")[
                "requestContext"]["http"]["path"]
            assert route.matches(path)
            others = [r for r in METADATA_ROUTES if r is not route and r.matches(path)]
            assert others == []

    def test_the_cross_call_is_still_attributed_to_the_system_identity(self):
        """Control against collateral change: the route rework must not disturb the caller identity
        the metadata service authorizes against."""
        event = _invoke(file_path="", metadata_type="metadata")
        assert event["lambdaCrossCall"]["userName"] == "SYSTEM_USER"
        assert event["pathParameters"] == {"databaseId": DB, "assetId": ASSET}
