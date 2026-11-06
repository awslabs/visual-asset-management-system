# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A workflow whose only outputs are over the async threshold does not end FAILED (#1140).

The external completion route used to answer a large-only write-back "No files were successfully
uploaded" with overallSuccess False, and the output step recorded the run FAILED ("The asset file
write-back failed.") while its output landed. Pinned here on the output step's side, for both the
queue path (no ingestion batch: a direct invocation, or a run whose files are queued) and the copy
engine path, with the answer the fixed route gives (see
tests/handlers/assets/test_upload_completion_large_files_only.py for the route itself)."""

import os
from unittest.mock import MagicMock, patch

import pytest

for _k, _v in {
    "WORKFLOW_STORAGE_TABLE_V2_NAME": "t-wf-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-inputs",
    "WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME": "t-locks",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-config",
    "LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE_NAME": "t-copy-jobs",
}.items():
    os.environ.setdefault(_k, _v)

from backend.tests.handlers.workflows import test_processOutput_ingestion_outcome as base  # noqa: E402

po = base.po
GIB = 1024 ** 3
FILES_PREFIX = base.FILES_PREFIX
WRITE_BACK_FAILED = "The asset file write-back failed."

LARGE_ONLY_LISTING = {"Contents": [{"Key": FILES_PREFIX + "scan.e57", "Size": 3 * GIB}]}

# The fixed external completion's answer for one queued file over the threshold.
QUEUED_ANSWER = {
    "message": "External upload completed successfully", "overallSuccess": True,
    "largeFileAsynchronousHandling": True,
    "fileResults": [{"relativeKey": "scan.e57", "uploadIdS3": "external", "success": True,
                     "largeFileAsynchronousHandling": True}],
}
# The pre-fix answer: the same file accepted, but the completion reported as failed.
PRE_FIX_ANSWER = {**QUEUED_ANSWER, "message": "No files were successfully uploaded",
                  "overallSuccess": False}


def _run(answer, **event_overrides):
    lookup, bucket, logs = base._asset_patches()
    with lookup, bucket, logs, \
            patch.object(po, "verify_get_path_objects", return_value=LARGE_ONLY_LISTING), \
            patch.object(po, "create_external_upload_record", return_value="upl-1"), \
            patch.object(po, "_stamp_output_objects", return_value=True), \
            patch.object(po, "process_external_upload", return_value=answer), \
            patch.object(po, "record_execution_outputs") as record, \
            patch.object(po, "copy_jobs_table", return_value=MagicMock()), \
            patch.object(po, "_mark_ingesting", return_value=True), \
            patch.object(po.loc, "record_batch"), \
            patch.object(po.loc, "claim_notification", return_value=None), \
            patch.object(po, "s3c"):
        po.lambda_handler(base._event(filesPathKey=FILES_PREFIX, **event_overrides), MagicMock())
    return record


@pytest.mark.unit
class TestALargeOnlyWriteBackIsNotRecordedFailed:
    def test_the_ingestion_outcome_reads_the_answer_as_success(self):
        all_ingested, ingested = po._ingestion_outcome(QUEUED_ANSWER)
        assert all_ingested is True and ingested == {"scan.e57"}

    def test_a_queued_large_only_output_records_the_run_succeeded(self):
        record = _run(QUEUED_ANSWER)
        kwargs = record.call_args.kwargs
        assert kwargs["execution_status"] == "SUCCEEDED"
        assert WRITE_BACK_FAILED not in (kwargs.get("execution_error") or "")

    def test_the_pre_fix_answer_is_what_failed_the_run(self):
        # Guards the test above: the shape the route used to send is read as a failed write-back,
        # so the fix is in the route's answer, which the assets test pins.
        record = _run(PRE_FIX_ANSWER)
        assert record.call_args.kwargs["execution_status"] == "FAILED"

    def test_a_large_only_output_on_the_engine_waits_instead_of_failing(self):
        answer = {**QUEUED_ANSWER, "fileResults": [
            {**QUEUED_ANSWER["fileResults"][0], "ingestionJobId": "E#abc"}]}
        with patch.object(po, "_begin_waiting_for_ingestion",
                          return_value={"outputIngestionPending": True}) as wait:
            record = _run(answer, awaitOutputIngestion=True)
        record.assert_not_called()
        args = wait.call_args.args
        # output_failures (7th positional) is empty: the write-back is not recorded as failed.
        assert args[6] == []
        assert args[7] == {"scan.e57": "E#abc"}
