# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""processWorkflowExecutionOutput end-state contract:

- file-level metadata/attribute JSON is READ from the run I/O bucket the objects were listed in
  (not the output asset's bucket), so a multi-bucket deployment still applies them;
- the output listing pages to exhaustion, so an output block larger than one S3 page is complete;
- a total failure RAISES so the Step Functions Catch routes to the error-handler state instead of
  the state machine reporting the run SUCCEEDED with no outputs.
"""

import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch

# models.assetsV3 is imported for real here. It must NOT be replaced with a partial stub: a module
# installed into sys.modules persists for the whole session, so a stub exposing only the one name
# this file needs makes every LATER test module that imports any other model from assetsV3 fail at
# collection — which, because a collection error aborts the run, took the entire
# tests/handlers/workflows directory (1389 tests) down whenever this file was collected first.

for _k, _v in {
    "S3_ASSET_BUCKETS_STORAGE_TABLE_NAME": "t-buckets",
    "METADATA_SERVICE_LAMBDA_FUNCTION_NAME": "t-md-svc",
    "FILE_UPLOAD_LAMBDA_FUNCTION_NAME": "t-upload",
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "ASSET_UPLOAD_TABLE_NAME": "t-asset-upload",
    "DATABASE_STORAGE_TABLE_NAME": "t-db",
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME": "t-om",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
}.items():
    os.environ.setdefault(_k, _v)

# The handlers.workflows package __init__ imports get_task_builder from
# common.workflows.stepfunctions_builder at import time; the shared test mock package does not
# provide that submodule. These tests do not exercise ASL generation.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po

RUN_BUCKET = "run-io-bucket"
ASSET_BUCKET = "output-asset-bucket"
METADATA_PREFIX = "pipelines/p1/JOB/output/E1/metadata/"


def _event(**overrides):
    body = {
        "outputAssetId": "asset1.glb",
        "outputDatabaseId": "db1",
        "workflowExecutionId": "E1",
        "endStatePipelineExecutionId": "P1",
        "workflowDatabaseId": "wdb",
        "workflowId": "wf1",
        "workflowExecutionS3InputOutputBucket": RUN_BUCKET,
        "executingUserName": "SYSTEM_USER",
        "executingRequestContext": {"http": {}},
    }
    body.update(overrides)
    return {"body": body}


@pytest.mark.unit
class TestFileLevelMetadataReadBucket:
    """File-level metadata/attribute JSON must be read from the bucket it was listed in."""

    def _run(self):
        listing = {"Contents": [
            {"Key": METADATA_PREFIX + "asset.metadata.json", "Size": 5},
            {"Key": METADATA_PREFIX + "models/part.glb.metadata.json", "Size": 5},
            {"Key": METADATA_PREFIX + "models/part.glb.attribute.json", "Size": 5},
        ]}
        with patch.object(po, "lookup_existing_asset",
                          return_value={"databaseId": "db1", "assetId": "asset1.glb",
                                        "bucketId": "b1"}), \
             patch.object(po, "get_default_bucket_details",
                          return_value={"bucketId": "b1", "bucketName": ASSET_BUCKET,
                                        "baseAssetsPrefix": "assets/"}), \
             patch.object(po, "verify_get_path_objects", return_value=listing), \
             patch.object(po, "process_metadata_file") as m_process, \
             patch.object(po, "record_execution_outputs"), \
             patch.object(po, "_fetch_execution_logs", return_value=("", "")):
            resp = po.lambda_handler(_event(metadataPathKey=METADATA_PREFIX), MagicMock())
        assert resp["statusCode"] == 200
        return m_process

    def test_all_metadata_reads_target_the_run_bucket(self):
        m_process = self._run()
        # asset-level + file-level metadata + file-level attribute
        assert m_process.call_count == 3
        buckets = {call.args[0] for call in m_process.call_args_list}
        assert buckets == {RUN_BUCKET}

    def test_file_level_kinds_are_both_covered(self):
        m_process = self._run()
        by_type = {call.args[6]: call.args[0] for call in m_process.call_args_list
                   if call.args[5] is not None}
        assert by_type == {"metadata": RUN_BUCKET, "attribute": RUN_BUCKET}


@pytest.mark.unit
class TestVerifyGetPathObjectsPaging:
    """The output listing pages to exhaustion; objects past the first page are not dropped."""

    def test_returns_objects_from_every_page(self):
        page1 = {"Contents": [{"Key": f"{METADATA_PREFIX}f{i}", "Size": 1} for i in range(1000)],
                 "IsTruncated": True}
        page2 = {"Contents": [{"Key": f"{METADATA_PREFIX}g{i}", "Size": 1} for i in range(500)]}
        paginator = MagicMock()
        paginator.paginate.return_value = iter([page1, page2])
        with patch.object(po, "s3c") as m_s3:
            m_s3.get_paginator.return_value = paginator
            result = po.verify_get_path_objects(RUN_BUCKET, METADATA_PREFIX)
        assert len(result["Contents"]) == 1500
        assert result["Contents"][-1]["Key"] == f"{METADATA_PREFIX}g499"

    def test_empty_prefix_has_no_contents_key(self):
        paginator = MagicMock()
        paginator.paginate.return_value = iter([{}])
        with patch.object(po, "s3c") as m_s3:
            m_s3.get_paginator.return_value = paginator
            result = po.verify_get_path_objects(RUN_BUCKET, METADATA_PREFIX)
        # Callers gate on 'Contents' in the listing, mirroring the list_objects_v2 response shape.
        assert "Contents" not in result


@pytest.mark.unit
class TestFailuresPropagateToStepFunctions:
    """Step Functions invokes this state as a plain lambda task and never inspects the returned
    payload, so a failure must raise for the state's Catch to route to the error handler."""

    def test_bucket_lookup_failure_raises(self):
        with patch.object(po, "lookup_existing_asset",
                          return_value={"databaseId": "db1", "assetId": "asset1.glb",
                                        "bucketId": "b1"}), \
             patch.object(po, "get_default_bucket_details",
                          side_effect=Exception("Error getting bucket details.")):
            with pytest.raises(Exception) as exc:
                po.lambda_handler(_event(), MagicMock())
        assert "bucket details" in str(exc.value)

    def test_authorization_denial_raises(self):
        enforcer = MagicMock()
        enforcer.enforce.return_value = False
        with patch.object(po, "lookup_existing_asset",
                          return_value={"databaseId": "db1", "assetId": "asset1.glb",
                                        "bucketId": "b1"}), \
             patch.object(po, "CasbinEnforcer", return_value=enforcer), \
             patch.object(po, "get_default_bucket_details",
                          return_value={"bucketId": "b1", "bucketName": ASSET_BUCKET,
                                        "baseAssetsPrefix": "assets/"}):
            # A non-system executing user goes through Casbin, which denies here.
            with pytest.raises(Exception):
                po.lambda_handler(_event(executingUserName="alice"), MagicMock())

    def test_validation_failures_still_return_a_response(self):
        # Malformed input is a caller contract error, not a run failure: it stays a 400 payload.
        resp = po.lambda_handler({"body": {"outputAssetId": "a.glb", "outputDatabaseId": "db1"}},
                                 MagicMock())
        assert resp["statusCode"] == 400
