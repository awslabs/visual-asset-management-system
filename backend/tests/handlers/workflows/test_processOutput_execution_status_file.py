# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""processWorkflowExecutionOutput honours the reserved results object ``execution.status.json``.

A pipeline that has already written its outputs and then meets a failure it can report -- a Bedrock
access denial after the attribute file landed, say -- writes ``{"status": "FAILED", "error", "cause"}``
under its results prefix and returns normally, so the workflow still reaches the process-output step and
the outputs it wrote are ingested. The step then records the execution FAILED with
``executionError = "<error>: <cause>"``. Both terminal paths are covered here, because they run
different code: the results-only path (``outputLocationType: none``) and the asset write-back path.

The file stays an ordinary results row, so the report is also readable from the execution record. A
status other than FAILED, or no file at all, leaves the outcome to the write-back result -- which is the
positive control every FAILED assertion below is paired with.
"""

import io
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

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
}.items():
    os.environ.setdefault(_k, _v)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.common.s3PathPatterns import EXECUTION_STATUS_RESULTS_FILENAME  # noqa: E402
from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po  # noqa: E402

RUN_BUCKET = "run-io-bucket"
ASSET_BUCKET = "output-asset-bucket"
METADATA_PREFIX = "pipelines/p1/JOB/output/E1/metadata/"
RESULTS_PREFIX = "pipelines/p1/JOB/output/E1/results/"
STATUS_KEY = RESULTS_PREFIX + EXECUTION_STATUS_RESULTS_FILENAME
SUMMARY_KEY = RESULTS_PREFIX + "analysis-summary.json"
ATTRIBUTE_KEY = METADATA_PREFIX + "scan.e57.attribute.json"

FAILED_STATUS = {"status": "FAILED", "error": "BedrockAccessDenied",
                 "cause": "AccessDeniedException: model access is not enabled"}
EXPECTED_ERROR = "BedrockAccessDenied: AccessDeniedException: model access is not enabled"

# What process_metadata_file reports for the attribute file when the write-back succeeds.
APPLIED = [{"metadataKey": "sys_file", "metadataValue": "{}"}]


def _asset_event(**overrides):
    body = {
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
        "resultsPathKey": RESULTS_PREFIX,
    }
    body.update(overrides)
    return {"body": body}


def _results_only_event(**overrides):
    body = {
        "outputLocationType": "none",
        "outputAssetId": "", "outputDatabaseId": "",
        "workflowExecutionId": "E1", "endStatePipelineExecutionId": "P1",
        "workflowDatabaseId": "GLOBAL", "workflowId": "wf1",
        "workflowExecutionS3InputOutputBucket": RUN_BUCKET,
        "resultsPathKey": RESULTS_PREFIX,
        "executingUserName": "SYSTEM_USER", "executingRequestContext": {},
    }
    body.update(overrides)
    return {"body": body}


def _s3_stub(objects):
    """An S3 client whose get_object serves ``objects`` ({key: bytes}) and records every read."""
    reads = []

    def _get_object(Bucket, Key, **_kwargs):
        reads.append((Bucket, Key))
        if Key not in objects:
            raise KeyError(Key)
        return {"Body": io.BytesIO(objects[Key])}

    stub = MagicMock()
    stub.get_object.side_effect = _get_object
    return stub, reads


def _listing(objects, prefix):
    """The listing verify_get_path_objects reports for one output prefix: every staged object under
    it, carrying the Size the results reader uses to choose a plain GET."""
    contents = [{"Key": key, "Size": len(body)}
                for key, body in objects.items() if key.startswith(prefix)]
    return {"Contents": contents} if contents else {}


def _staged(status=None, extra=None):
    """The run bucket after a pipeline wrote an attribute file and a results summary, plus an optional
    status object (a dict to serialize, or raw bytes)."""
    objects = {ATTRIBUTE_KEY: b'{"type": "attribute", "metadata": []}',
               SUMMARY_KEY: b'{"model": "haiku"}'}
    if status is not None:
        objects[STATUS_KEY] = status if isinstance(status, bytes) else json.dumps(status).encode("utf-8")
    objects.update(extra or {})
    return objects


def _run_asset_path(objects, metadata_result=APPLIED):
    """Drive the asset write-back path over staged ``objects``. ``metadata_result`` is what the
    metadata write-back reports (None = the metadata service refused). Returns (record kwargs, reads)."""
    s3, reads = _s3_stub(objects)
    with patch.object(po, "lookup_existing_asset",
                      return_value={"databaseId": "db1", "assetId": "asset1", "bucketId": "b1"}), \
         patch.object(po, "get_default_bucket_details",
                      return_value={"bucketId": "b1", "bucketName": ASSET_BUCKET,
                                    "baseAssetsPrefix": "assets/"}), \
         patch.object(po, "_fetch_execution_logs", return_value=("", "")), \
         patch.object(po, "verify_get_path_objects",
                      side_effect=lambda _bucket, prefix: _listing(objects, prefix)), \
         patch.object(po, "process_metadata_file", return_value=metadata_result), \
         patch.object(po, "s3c", s3), \
         patch.object(po, "record_execution_outputs") as m_record:
        resp = po.lambda_handler(_asset_event(), MagicMock())
    assert resp["statusCode"] == 200
    assert m_record.call_count == 1
    return m_record.call_args.kwargs, reads


def _run_results_only(objects):
    s3, reads = _s3_stub(objects)
    with patch.object(po, "lookup_existing_asset") as m_lookup, \
         patch.object(po, "_fetch_execution_logs", return_value=("", "")), \
         patch.object(po, "verify_get_path_objects",
                      side_effect=lambda _bucket, prefix: _listing(objects, prefix)), \
         patch.object(po, "s3c", s3), \
         patch.object(po, "record_execution_outputs") as m_record:
        resp = po.lambda_handler(_results_only_event(), MagicMock())
    assert resp["statusCode"] == 200
    m_lookup.assert_not_called()
    assert m_record.call_count == 1
    return m_record.call_args.kwargs, reads


@pytest.mark.unit
class TestReservedName:
    def test_the_reserved_results_object_name(self):
        assert EXECUTION_STATUS_RESULTS_FILENAME == "execution.status.json"


@pytest.mark.unit
class TestAssetWriteBackPath:
    def test_a_failed_status_file_records_the_execution_failed(self):
        kw, _reads = _run_asset_path(_staged(FAILED_STATUS))
        assert kw["execution_status"] == "FAILED"
        assert kw["execution_error"] == EXPECTED_ERROR

    def test_no_status_file_leaves_the_outcome_to_the_write_back(self):
        """Positive control for every FAILED assertion in this class."""
        kw, _reads = _run_asset_path(_staged())
        assert kw["execution_status"] == "SUCCEEDED"
        assert kw["execution_error"] == ""

    def test_a_status_other_than_failed_does_not_flip_the_outcome(self):
        kw, _reads = _run_asset_path(_staged({"status": "SUCCEEDED", "error": "", "cause": ""}))
        assert kw["execution_status"] == "SUCCEEDED"
        assert kw["execution_error"] == ""

    def test_the_status_file_is_still_recorded_as_a_results_row(self):
        kw, _reads = _run_asset_path(_staged(FAILED_STATUS))
        rows = {r["relativeFilePath"]: r for r in kw["output_results"]}
        assert "/" + EXECUTION_STATUS_RESULTS_FILENAME in rows
        assert json.loads(rows["/" + EXECUTION_STATUS_RESULTS_FILENAME]["resultsContent"]) == FAILED_STATUS
        assert "/analysis-summary.json" in rows

    def test_outputs_written_before_the_reported_failure_are_still_recorded(self):
        """The attribute file the pipeline wrote before failing was applied and is recorded: a FAILED
        outcome does not discard what landed."""
        kw, _reads = _run_asset_path(_staged(FAILED_STATUS))
        assert kw["execution_status"] == "FAILED"
        assert [m["metadataKey"] for m in kw["output_metadata"]] == ["sys_file"]

    def test_the_status_file_is_read_once_from_the_run_bucket(self):
        """The results collector already read the object for its results row; the report reuses that
        text rather than fetching the object a second time."""
        _kw, reads = _run_asset_path(_staged(FAILED_STATUS))
        assert reads.count((RUN_BUCKET, STATUS_KEY)) == 1
        assert all(bucket == RUN_BUCKET for bucket, _key in reads)

    def test_a_status_file_beyond_the_results_recording_cap_is_still_read(self):
        """The collector stops at MAX_RECORDED_OUTPUT_RESULT_ROWS rows, so a status object listed after
        that point has no results row; it is then fetched on its own -- the one case that costs a read.
        The filler names sort before ``execution.status.json`` the way S3 would list them."""
        filler = {RESULTS_PREFIX + f"analysis-{i:05d}.json": b"{}"
                  for i in range(po.MAX_RECORDED_OUTPUT_RESULT_ROWS)}
        objects = {ATTRIBUTE_KEY: b'{"type": "attribute", "metadata": []}', **filler,
                   STATUS_KEY: json.dumps(FAILED_STATUS).encode("utf-8")}
        kw, reads = _run_asset_path(objects)
        assert kw["execution_status"] == "FAILED"
        assert kw["execution_error"] == EXPECTED_ERROR
        assert reads.count((RUN_BUCKET, STATUS_KEY)) == 1
        assert "/" + EXECUTION_STATUS_RESULTS_FILENAME not in {
            r["relativeFilePath"] for r in kw["output_results"]}

    def test_the_reported_error_leads_the_summary_when_the_write_back_also_failed(self):
        kw, _reads = _run_asset_path(_staged(FAILED_STATUS), metadata_result=None)
        assert kw["execution_status"] == "FAILED"
        assert kw["execution_error"].startswith(EXPECTED_ERROR)
        assert po.METADATA_WRITE_BACK_FAILURE in kw["execution_error"]

    def test_an_unreadable_status_file_is_itself_a_failure(self):
        kw, _reads = _run_asset_path(_staged(b"not json"))
        assert kw["execution_status"] == "FAILED"
        assert kw["execution_error"] == po.EXECUTION_STATUS_FILE_UNREADABLE

    def test_a_status_file_that_is_not_an_object_is_itself_a_failure(self):
        kw, _reads = _run_asset_path(_staged(b'["FAILED"]'))
        assert kw["execution_status"] == "FAILED"
        assert kw["execution_error"] == po.EXECUTION_STATUS_FILE_UNREADABLE

    def test_the_cause_is_bounded(self):
        long_cause = "x" * (po.EXECUTION_STATUS_CAUSE_MAX_CHARS + 500)
        kw, _reads = _run_asset_path(
            _staged({"status": "FAILED", "error": "BedrockThrottled", "cause": long_cause}))
        assert kw["execution_error"] == "BedrockThrottled: " + "x" * po.EXECUTION_STATUS_CAUSE_MAX_CHARS

    def test_a_status_file_with_no_cause_records_the_error_alone(self):
        kw, _reads = _run_asset_path(_staged({"status": "FAILED", "error": "BedrockModelError"}))
        assert kw["execution_error"] == "BedrockModelError"

    def test_a_failed_status_file_with_no_error_code_still_fails(self):
        kw, _reads = _run_asset_path(_staged({"status": "FAILED", "cause": "no code given"}))
        assert kw["execution_status"] == "FAILED"
        assert kw["execution_error"] == po.EXECUTION_STATUS_DEFAULT_ERROR + ": no code given"


@pytest.mark.unit
class TestResultsOnlyPath:
    def test_a_failed_status_file_records_the_execution_failed(self):
        kw, _reads = _run_results_only(_staged(FAILED_STATUS))
        assert kw["execution_status"] == "FAILED"
        assert kw["execution_error"] == EXPECTED_ERROR
        assert kw["output_files"] == [] and kw["output_metadata"] == []

    def test_no_status_file_leaves_the_outcome_to_the_write_back(self):
        kw, _reads = _run_results_only(_staged())
        assert kw["execution_status"] == "SUCCEEDED"
        assert kw["execution_error"] == ""

    def test_the_status_file_is_still_recorded_as_a_results_row(self):
        kw, _reads = _run_results_only(_staged(FAILED_STATUS))
        assert "/" + EXECUTION_STATUS_RESULTS_FILENAME in {
            r["relativeFilePath"] for r in kw["output_results"]}

    def test_the_status_file_is_read_once_from_the_run_bucket(self):
        _kw, reads = _run_results_only(_staged(FAILED_STATUS))
        assert reads.count((RUN_BUCKET, STATUS_KEY)) == 1
        assert all(bucket == RUN_BUCKET for bucket, _key in reads)
