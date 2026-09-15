# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""processWorkflowExecutionOutput write ORDER — a stated system requirement with no prior guard.

The requirement: a pipeline may emit a new asset file AND metadata that belongs on that new file in
the same run, so by the time metadata is written the file must already have been ingested and exist
on the asset. `lambda_handler` satisfies it today — the metadata block carries the comment "needs to
happen after S3 file processing" and `process_external_upload` is called synchronously (its per-file
outcome is read) before the metadata path is even listed.

Nothing pinned that ordering. Every other processOutput test asserts what each block DOES, not the
sequence they run in, so moving the metadata block above the file block would keep them all green
while breaking the requirement: metadata would target a file that does not exist yet, and the write
would be rejected by the metadata service's own file-existence check — turning a correct pipeline
into a failed execution.

Also asserted here, because they are the same contract seen from the write side:

* all THREE write kinds are dispatched — asset-level metadata (`asset.metadata.json`, recorded
  against "/"), file-level metadata, and file-level ATTRIBUTES (`metadata_type="attribute"`);
* attributes are ordered after file ingestion for the same reason metadata is;
* the results block runs last, after both files and metadata.
"""

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

from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po  # noqa: E402

RUN_BUCKET = "run-io-bucket"
ASSET_BUCKET = "output-asset-bucket"
FILES_PREFIX = "pipelines/p1/JOB/output/E1/files/"
METADATA_PREFIX = "pipelines/p1/JOB/output/E1/metadata/"
RESULTS_PREFIX = "pipelines/p1/JOB/output/E1/results/"

NEW_FILE = "converted.obj"


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


def _listing_for(prefix):
    """The objects each output path reports. The metadata path carries all three kinds at once, which
    is the shape a pipeline that annotates its own new file produces."""
    if prefix == FILES_PREFIX:
        return {"Contents": [{"Key": FILES_PREFIX + NEW_FILE, "Size": 11}]}
    if prefix == METADATA_PREFIX:
        return {"Contents": [
            {"Key": METADATA_PREFIX + "asset.metadata.json", "Size": 9},
            {"Key": METADATA_PREFIX + NEW_FILE + ".metadata.json", "Size": 9},
            {"Key": METADATA_PREFIX + NEW_FILE + ".attribute.json", "Size": 9},
        ]}
    if prefix == RESULTS_PREFIX:
        return {"Contents": [{"Key": RESULTS_PREFIX + "summary.txt", "Size": 4}]}
    return {}


def _run_recording_order():
    """Drive lambda_handler over files + metadata + results, recording the ORDER of the write calls.

    Every recorded step appends to one shared list, so the assertions are about sequence rather than
    about any single call's arguments.
    """
    order = []

    def _upload(*_a, **_kw):
        order.append(("ingest_files", _a[3] if len(_a) > 3 else ""))
        return {"overallSuccess": True,
                "fileResults": [{"relativeKey": NEW_FILE, "success": True}]}

    def _metadata(_bucket, s3_key, _mdpath, _db, _asset, file_path, metadata_type, _ctx):
        order.append((f"write_{metadata_type}", file_path or "/"))
        return [{"metadataKey": "k", "metadataValue": "v"}]

    def _results(_bucket, _path, _objs):
        order.append(("read_results", ""))
        return ([], [])

    lookup = patch.object(po, "lookup_existing_asset",
                          return_value={"databaseId": "db1", "assetId": "asset1.glb",
                                        "bucketId": "b1"})
    bucket = patch.object(po, "get_default_bucket_details",
                          return_value={"bucketId": "b1", "bucketName": ASSET_BUCKET,
                                        "baseAssetsPrefix": "assets/"})
    logs = patch.object(po, "_fetch_execution_logs", return_value=("", ""))

    with lookup, bucket, logs, \
            patch.object(po, "verify_get_path_objects", side_effect=lambda _b, p: _listing_for(p)), \
            patch.object(po, "create_external_upload_record", return_value="upl-1"), \
            patch.object(po, "_stamp_output_objects", return_value=True), \
            patch.object(po, "process_external_upload", side_effect=_upload), \
            patch.object(po, "process_metadata_file", side_effect=_metadata), \
            patch.object(po, "_collect_result_outputs", side_effect=_results), \
            patch.object(po, "record_execution_outputs") as m_record:
        resp = po.lambda_handler(
            _event(filesPathKey=FILES_PREFIX, metadataPathKey=METADATA_PREFIX,
                   resultsPathKey=RESULTS_PREFIX), MagicMock())
    assert resp["statusCode"] == 200
    return order, m_record.call_args.kwargs


@pytest.mark.unit
class TestFilesAreWrittenBeforeMetadata:
    def test_the_run_exercised_every_write_kind(self):
        """Positive control. Every ordering assertion below is a statement about a list, and an empty
        or partial list satisfies most of them trivially — so the kinds must be present first."""
        order, _kw = _run_recording_order()
        kinds = [step for step, _target in order]
        assert "ingest_files" in kinds, order
        assert "write_metadata" in kinds, order
        assert "write_attribute" in kinds, order
        assert "read_results" in kinds, order

    def test_file_ingestion_precedes_every_metadata_write(self):
        """The requirement itself: metadata that belongs on a newly output file must be written only
        after that file has been ingested onto the asset."""
        order, _kw = _run_recording_order()
        kinds = [step for step, _target in order]
        first_ingest = kinds.index("ingest_files")
        for later in ("write_metadata", "write_attribute"):
            assert first_ingest < kinds.index(later), (
                f"{later} ran before the file ingestion, so it would target a file that does not "
                f"exist on the asset yet: {order}")

    def test_metadata_targets_the_new_file_by_its_asset_relative_path(self):
        order, _kw = _run_recording_order()
        targets = {step: target for step, target in order}
        # Asset-level metadata is recorded against the asset root, file-level against the new file.
        assert targets["write_metadata"] in ("/", NEW_FILE), order
        file_writes = [t for s, t in order if s == "write_metadata"]
        assert NEW_FILE in file_writes, f"no file-level metadata targeted {NEW_FILE}: {order}"

    def test_both_asset_level_and_file_level_metadata_are_written(self):
        """asset.metadata.json goes to the asset (no file path); every other metadata file goes to
        its file. A handler that treated them alike would silently drop one of the two."""
        order, _kw = _run_recording_order()
        metadata_targets = [t for s, t in order if s == "write_metadata"]
        assert "/" in metadata_targets, f"no asset-level metadata write: {order}"
        assert NEW_FILE in metadata_targets, f"no file-level metadata write: {order}"

    def test_file_attributes_are_written_as_attributes_not_metadata(self):
        """Attributes travel the same route with metadata_type='attribute'. Sending them as
        'metadata' would file them in the wrong store with no error."""
        order, _kw = _run_recording_order()
        attribute_targets = [t for s, t in order if s == "write_attribute"]
        assert attribute_targets == [NEW_FILE], order

    def test_results_are_read_after_files_and_metadata(self):
        order, _kw = _run_recording_order()
        kinds = [step for step, _target in order]
        last_write = max(kinds.index(k) for k in ("ingest_files", "write_metadata",
                                                  "write_attribute"))
        assert kinds.index("read_results") > last_write, (
            f"results were collected before the asset writes finished: {order}")

    def test_the_run_is_recorded_as_succeeded(self):
        """Guards against an ordering that is correct but leaves the execution reported as failed."""
        _order, kw = _run_recording_order()
        assert kw["execution_status"] == "SUCCEEDED", kw.get("execution_error")
