# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Miris upload container's stream metadata object and body round-trip through process-output."""

import datetime
import importlib.util
import os
import sys
import types

import pytest

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
    "WORKFLOW_STORAGE_TABLE_V2_NAME": "t-wf-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME": "t-wf-locks",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-cfg",
}.items():
    os.environ.setdefault(_k, _v)

# The handlers.workflows package __init__ imports get_task_builder from
# common.workflows.stepfunctions_builder at import time; these tests do not exercise ASL generation.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po  # noqa: E402
from backend.backend.models.metadata import UpdateFileMetadataRequestModel  # noqa: E402

_REPO_ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
_STREAM_METADATA_PATH = os.path.join(
    _REPO_ROOT, "backendPipelines", "addon", "conversion", "mirisUpload", "container", "stream_metadata.py")

_spec = importlib.util.spec_from_file_location("miris_upload_stream_metadata", _STREAM_METADATA_PATH)
stream_metadata = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stream_metadata)

pytestmark = pytest.mark.unit

# The shape the workflow ASL hands the process-output step as metadataPathKey.
METADATA_PREFIX = "pipelines/miris-upload/MirisUpload_job/output/E1/metadata/"


@pytest.mark.parametrize("usd_relative_path", [
    "pump.usdz",
    "scenes/v2/pump.usda",
    "scenes/Coral House.usdz",
])
def test_written_key_derives_to_the_usd_relative_path(usd_relative_path):
    # The container names the object after the root USD's path relative to the asset root.
    key = stream_metadata.metadata_object_key(METADATA_PREFIX, usd_relative_path)

    assert os.path.basename(key) != "asset.metadata.json"
    assert key in [o["Key"] for o in po.filter_metadata_files([{"Key": key}])]
    assert po.extract_file_path_from_metadata_filename(key, METADATA_PREFIX) == usd_relative_path


def test_body_is_accepted_by_the_file_metadata_update_model():
    body = stream_metadata.build_stream_metadata_body(
        asset_uuid="0b6f4c9e-6a43-4bb5-9d0c-4b3e2b1f7a10",
        source_version_id="3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY",
        display_name="Pump Housing",
        uploaded_at=datetime.datetime(2026, 10, 14, 9, 30, tzinfo=datetime.timezone.utc),
        dependency_manifest="ab" * 32,
    )

    # process_metadata_file forwards metadata/updateType and adds filePath/type for a file target.
    request = UpdateFileMetadataRequestModel(
        filePath="scenes/v2/pump.usda", type=body["type"],
        metadata=body["metadata"], updateType=body["updateType"])

    assert {item.metadataKey: item.metadataValueType.value for item in request.metadata} == {
        "miris_asset_uuid": "string",
        "miris_source_version_id": "string",
        "miris_uploaded_at": "date",
        "miris_display_name": "string",
        "miris_dependency_manifest": "string",
    }
    assert request.updateType.value == "update"

