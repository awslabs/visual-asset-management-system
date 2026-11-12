# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The 3D Preview Thumbnail container's incomplete-preview result is recorded as an output results row.

When a preview was rendered without some of its referenced files, the container writes
``preview-incomplete.json`` directly under the execution's results folder. Nothing in this step is
specific to it: it is an ordinary result object, so processWorkflowExecutionOutput records it as a
PipelineExecutionOutputResults row (Output Results in the web app, ``vamscli execution details
results``) and the execution stays SUCCEEDED. This feeds the document the container itself builds to
both terminal paths; the step is unchanged.
"""

import importlib
import json
import os
import sys

import pytest

from tests.handlers.workflows.test_processOutput_execution_status_file import (  # noqa: E402
    RESULTS_PREFIX,
    _run_asset_path,
    _run_results_only,
    _staged,
)
from backend.backend.common.workflows import executionRecords as er  # noqa: E402

_CONTAINER_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..",
    "backendPipelines", "preview", "3dThumbnail", "container"))


def _container_dependencies():
    """The container's preview_pipeline.dependencies, imported from its source tree."""
    sys.path.insert(0, _CONTAINER_DIR)
    try:
        return importlib.import_module("preview_pipeline.dependencies")
    finally:
        sys.path.remove(_CONTAINER_DIR)


deps = _container_dependencies()
RESULT_KEY = RESULTS_PREFIX + deps.INCOMPLETE_RESULT_FILENAME


def _document():
    report = deps.FetchReport()
    report.skipped.append(deps.SkippedReference("scene/parts/wheel.usda", "missing from the asset",
                                                "scene/root.usda"))
    report.skipped.append(deps.SkippedReference("http://example.com/tex.png",
                                                "outside the asset (URI scheme)", "scene/root.usda",
                                                rejected=True))
    return deps.incomplete_result_document(deps.incomplete_preview(report), "scene/root.usda")


def _the_row(output_results):
    rows = [r for r in output_results if r.get("s3Key") == RESULT_KEY]
    assert len(rows) == 1, output_results
    return rows[0]


@pytest.mark.unit
class TestTheIncompletePreviewResultIsRecorded:
    @pytest.mark.parametrize("run", [_run_results_only, _run_asset_path])
    def test_the_document_becomes_one_results_row_and_the_run_stays_succeeded(self, run):
        body = _document()
        kw, _reads = run(_staged(extra={RESULT_KEY: body}))
        assert kw["execution_status"] == "SUCCEEDED"
        assert kw["execution_error"] == ""
        row = _the_row(kw["output_results"])
        assert row["relativeFilePath"] == "/" + deps.INCOMPLETE_RESULT_FILENAME
        assert row["resultsContent"] == body.decode("utf-8")

        record = er.build_output_result_record("P1", row["relativeFilePath"], row["resultsContent"],
                                               row["s3Key"])
        assert record["resultsContentTruncated"] is False
        document = json.loads(record["resultsContent"])
        assert document["status"] == "INCOMPLETE"
        assert document["counts"] == {"skipped": 2, "geometryLayers": 1, "texturesOrOther": 1,
                                      "notFollowed": 0}

    def test_the_document_fits_the_rows_text_budget_untruncated(self):
        assert deps.MAX_RESULT_DOCUMENT_BYTES < er.MAX_TEXT_FIELD_BYTES

    def test_a_complete_run_records_no_such_row(self):
        kw, _reads = _run_results_only(_staged())
        assert not [r for r in kw["output_results"] if r.get("s3Key") == RESULT_KEY]
