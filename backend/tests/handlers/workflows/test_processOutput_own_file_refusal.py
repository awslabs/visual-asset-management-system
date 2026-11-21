# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A workflow output refused at a single-file asset's own file path is named with its reason (D5).

The file ingestion refuses an output whose path names the file of a single-file asset, per file, with a
fixed message the user can act on. The run used to end FAILED with only the generic write-back error, so the
reason appeared nowhere a user reads. The run's error now carries that refusal after the file it refused;
any other per-file error still stays out of it.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

# Table names output processing reads at import, so the file also runs on its own
for _name in ("WORKFLOW_STORAGE_TABLE_V2_NAME", "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME",
              "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME"):
    os.environ.setdefault(_name, f"t-{_name.lower()}")

from tests.handlers.workflows.test_processOutput_ingestion_outcome import (  # noqa: E402
    FILES_PREFIX, _asset_patches, _event, po,
)

REFUSAL = ("A workflow output cannot replace the file of a single-file asset. "
           "Write the output under another file name or folder.")


def _run(upload_result, listing):
    lookup, bucket, logs = _asset_patches()
    with lookup, bucket, logs, \
            patch.object(po, "verify_get_path_objects", return_value=listing), \
            patch.object(po, "create_external_upload_record", return_value="upl-1"), \
            patch.object(po, "_stamp_output_objects", return_value=True), \
            patch.object(po, "process_external_upload", return_value=upload_result), \
            patch.object(po, "record_execution_outputs") as m_record:
        resp = po.lambda_handler(_event(filesPathKey=FILES_PREFIX), MagicMock())
    assert resp["statusCode"] == 200
    return m_record.call_args.kwargs


@pytest.mark.unit
class TestTheOwnFileRefusalReachesTheRun:
    def test_the_run_error_names_the_refused_file_and_why(self):
        listing = {"Contents": [{"Key": FILES_PREFIX + "model.glb", "Size": 7},
                                {"Key": FILES_PREFIX + "other.bin", "Size": 7}]}
        kw = _run({
            "overallSuccess": False,
            "fileResults": [
                {"relativeKey": "model.glb", "success": False, "error": REFUSAL},
                {"relativeKey": "other.bin", "success": False,
                 "error": "File temporary key pipelines/x/y does not start with expected prefix z"},
            ],
        }, listing)
        assert kw["execution_status"] == "FAILED"
        assert kw["execution_error"] == (
            "The asset file write-back failed. "
            f"Writing output file 'model.glb' into the asset failed: {REFUSAL} "
            "Writing output file 'other.bin' into the asset failed.")
        # An error the user cannot act on, with internal keys, stays in the log
        assert "pipelines/x/y" not in kw["execution_error"]

    def test_the_refusal_text_is_the_one_the_upload_completion_answers(self):
        from common.assetLocations import NESTED_PATH_REFUSAL, OWN_FILE_OUTPUT_REFUSAL, SHARED_LOCATION_WRITE_REFUSAL
        assert OWN_FILE_OUTPUT_REFUSAL == REFUSAL
        # An output into the location of an asset nested in the target asset, or into a location another live
        # asset also holds, is refused the same way
        assert po.USER_CORRECTABLE_WRITE_BACK_REFUSALS == frozenset(
            {REFUSAL, NESTED_PATH_REFUSAL, SHARED_LOCATION_WRITE_REFUSAL})
