# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The recorded metadata write-back failure names what the metadata service refused.

A database with ``restrictMetadataOutsideSchemas`` and a schema for the entity type rejects every key
its schemas do not define, so a pipeline's metadata file fails to apply and the execution is recorded
FAILED. The service's 400 body says which field (``Field 'ext_width' is not defined in the metadata
schema ...``); ``executionError`` carries that message behind the generic constant, so the execution
record tells an operator which field to add to a schema. A refusal whose body carries no message keeps
the bare constant (the control), and several files refused for the same reason record the message once.
"""

import io
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

if "models.assetsV3" not in sys.modules:
    _assetsv3_stub = types.ModuleType("models.assetsV3")
    _assetsv3_stub.AssetUploadTableModel = MagicMock()
    sys.modules["models.assetsV3"] = _assetsv3_stub

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

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po  # noqa: E402

RUN_BUCKET = "run-io-bucket"
ASSET_BUCKET = "output-asset-bucket"
METADATA_PREFIX = "pipelines/p1/JOB/output/E1/metadata/"
ASSET_METADATA_KEY = METADATA_PREFIX + "asset.metadata.json"
FILE_METADATA_KEYS = (METADATA_PREFIX + "scan.e57.metadata.json",
                      METADATA_PREFIX + "photo.jpg.metadata.json")

# The body the metadata service answers with when a restricted database's schemas do not define the key
# (metadataService builds it from metadataSchemaValidation.validate_metadata_keys_against_schema).
SCHEMA_REFUSAL = ("Metadata key validation failed: Field 'ext_width' is not defined in the metadata "
                  "schema. Only schema-defined fields are allowed when restrictMetadataOutsideSchemas "
                  "is enabled.")
# One metadata file carrying the key the schema does not define.
METADATA_FILE = json.dumps({"type": "metadata", "metadata": [
    {"metadataKey": "ext_width", "metadataValue": "1.2"}]}).encode("utf-8")


def _event():
    return {"body": {
        "outputAssetId": "asset1",
        "outputDatabaseId": "db1",
        "workflowExecutionId": "E1",
        "endStatePipelineExecutionId": "P1",
        "workflowDatabaseId": "GLOBAL",
        "workflowId": "wf1",
        "workflowExecutionS3InputOutputBucket": RUN_BUCKET,
        "executingUserName": "SYSTEM_USER",
        "executingRequestContext": {"http": {}},
        "metadataPathKey": METADATA_PREFIX,
    }}


def _payload(status_code, body):
    """One metadata-service Lambda payload. ``body`` is JSON-encoded when it is a dict, sent as-is when
    it is a string, and omitted when None."""
    payload = {"statusCode": status_code}
    if body is not None:
        payload["body"] = json.dumps(body) if isinstance(body, dict) else body
    return payload


def _service(*payloads):
    """A metadata-service cross-call stub answering successive invocations with ``payloads`` in order
    and repeating the last one; each answer is a fresh Payload stream, as the Lambda client returns."""
    answers = list(payloads)

    def _invoke(_event):
        payload = answers.pop(0) if len(answers) > 1 else answers[0]
        return {"Payload": io.BytesIO(json.dumps(payload).encode("utf-8"))}

    return _invoke


def _run(keys, service):
    """Drive the asset write-back path over the metadata files ``keys`` (each carrying METADATA_FILE),
    with ``service`` answering the metadata cross-call. Returns record_execution_outputs' kwargs."""
    listing = {"Contents": [{"Key": key, "Size": len(METADATA_FILE)} for key in keys]}
    s3 = MagicMock()
    s3.get_object.side_effect = lambda Bucket, Key, **_kwargs: {"Body": io.BytesIO(METADATA_FILE)}
    with patch.object(po, "lookup_existing_asset",
                      return_value={"databaseId": "db1", "assetId": "asset1", "bucketId": "b1"}), \
         patch.object(po, "get_default_bucket_details",
                      return_value={"bucketId": "b1", "bucketName": ASSET_BUCKET,
                                    "baseAssetsPrefix": "assets/"}), \
         patch.object(po, "_fetch_execution_logs", return_value=("", "")), \
         patch.object(po, "verify_get_path_objects", return_value=listing), \
         patch.object(po, "s3c", s3), \
         patch.object(po, "_lambda_metadata_service", side_effect=service), \
         patch.object(po, "record_execution_outputs") as m_record:
        resp = po.lambda_handler(_event(), MagicMock())
    assert resp["statusCode"] == 200
    assert m_record.call_count == 1
    return m_record.call_args.kwargs


@pytest.mark.unit
class TestTheRecordedErrorNamesTheRefusedField:
    def test_a_schema_refusal_records_the_constant_and_the_service_message(self):
        kw = _run([ASSET_METADATA_KEY], _service(_payload(400, {"message": SCHEMA_REFUSAL})))
        assert kw["execution_status"] == "FAILED"
        assert kw["execution_error"] == po.METADATA_WRITE_BACK_FAILURE.rstrip(".") + ": " + SCHEMA_REFUSAL
        assert "ext_width" in kw["execution_error"]
        assert kw["output_metadata"] == []

    def test_a_file_level_refusal_is_recorded_the_same_way(self):
        kw = _run([FILE_METADATA_KEYS[0]], _service(_payload(400, {"message": SCHEMA_REFUSAL})))
        assert kw["execution_error"] == po.METADATA_WRITE_BACK_FAILURE.rstrip(".") + ": " + SCHEMA_REFUSAL

    def test_a_body_carrying_error_instead_of_message_is_surfaced_too(self):
        kw = _run([ASSET_METADATA_KEY], _service(_payload(403, {"error": "Not Authorized"})))
        assert kw["execution_error"] == po.METADATA_WRITE_BACK_FAILURE.rstrip(".") + ": Not Authorized"

    def test_message_wins_when_a_body_carries_both(self):
        kw = _run([ASSET_METADATA_KEY], _service(_payload(400, {"message": "m", "error": "e"})))
        assert kw["execution_error"] == po.METADATA_WRITE_BACK_FAILURE.rstrip(".") + ": m"

    def test_the_message_is_bounded(self):
        long_message = "x" * (po.METADATA_WRITE_BACK_MESSAGE_MAX_CHARS + 500)
        kw = _run([ASSET_METADATA_KEY], _service(_payload(400, {"message": long_message})))
        assert kw["execution_error"] == (po.METADATA_WRITE_BACK_FAILURE.rstrip(".") + ": "
                                         + "x" * po.METADATA_WRITE_BACK_MESSAGE_MAX_CHARS)

    def test_an_accepted_write_records_no_error(self):
        """Positive control: the same file accepted by the service leaves the run SUCCEEDED and records
        the key, so the FAILED assertions above are about the refusal, not the harness."""
        kw = _run([ASSET_METADATA_KEY], _service(_payload(200, {"message": "ok"})))
        assert kw["execution_status"] == "SUCCEEDED"
        assert kw["execution_error"] == ""
        assert [m["metadataKey"] for m in kw["output_metadata"]] == ["ext_width"]


@pytest.mark.unit
class TestARefusalWithoutAMessageKeepsTheBareConstant:
    def test_a_body_with_neither_key(self):
        kw = _run([ASSET_METADATA_KEY], _service(_payload(500, {"detail": "boom"})))
        assert kw["execution_status"] == "FAILED"
        assert kw["execution_error"] == po.METADATA_WRITE_BACK_FAILURE

    def test_no_body_at_all(self):
        kw = _run([ASSET_METADATA_KEY], _service(_payload(502, None)))
        assert kw["execution_error"] == po.METADATA_WRITE_BACK_FAILURE

    def test_a_body_that_is_not_json(self):
        kw = _run([ASSET_METADATA_KEY], _service(_payload(500, "<html>Bad Gateway</html>")))
        assert kw["execution_error"] == po.METADATA_WRITE_BACK_FAILURE

    def test_an_empty_message(self):
        kw = _run([ASSET_METADATA_KEY], _service(_payload(400, {"message": ""})))
        assert kw["execution_error"] == po.METADATA_WRITE_BACK_FAILURE

    def test_a_cross_call_that_raises(self):
        """The service unreachable: there is nothing to quote."""
        def _unreachable(_event):
            raise RuntimeError("invoke failed")
        kw = _run([ASSET_METADATA_KEY], _unreachable)
        assert kw["execution_status"] == "FAILED"
        assert kw["execution_error"] == po.METADATA_WRITE_BACK_FAILURE


@pytest.mark.unit
class TestTheSameMessageIsRecordedOnce:
    def test_two_files_refused_for_the_same_reason(self):
        kw = _run(list(FILE_METADATA_KEYS), _service(_payload(400, {"message": SCHEMA_REFUSAL})))
        assert kw["execution_status"] == "FAILED"
        assert kw["execution_error"] == po.METADATA_WRITE_BACK_FAILURE.rstrip(".") + ": " + SCHEMA_REFUSAL
        assert kw["execution_error"].count("ext_width") == 1

    def test_two_files_refused_for_different_reasons_record_both(self):
        """Negative control for the deduplication: distinct messages are both kept, in file order, so
        the assertion above is about equal messages and not about the first file winning."""
        second = SCHEMA_REFUSAL.replace("ext_width", "ext_height")
        kw = _run(list(FILE_METADATA_KEYS),
                  _service(_payload(400, {"message": SCHEMA_REFUSAL}),
                           _payload(400, {"message": second})))
        assert kw["execution_error"] == (po.METADATA_WRITE_BACK_FAILURE.rstrip(".") + ": " + SCHEMA_REFUSAL + " "
                                         + po.METADATA_WRITE_BACK_FAILURE.rstrip(".") + ": " + second)
