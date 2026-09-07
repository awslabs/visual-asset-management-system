# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the output file base-execution path extension in processWorkflowExecutionOutput:
the extension is inserted into each output file's relative path immediately BEFORE the final
filename, so the pipeline's own output folder structure stays ahead of it and the extension names the
leaf folder. It defaults to '/' (no extra segment) and applies to asset FILE outputs
(path-structured), not previews (basename-only).

The upload lambda prepends the asset base key, so process_external_upload's job is to produce the
extended relativeKey. _collect_output_descriptors records the same extended relativeFilePath so
output provenance + the version-history join stay aligned. The placement rule itself is unit-tested
in tests/common/test_outputPathExtension.py; these tests fix it at the two lambda call sites.
"""

import os
import json
import pytest
from unittest.mock import MagicMock, patch

# processWorkflowExecutionOutput loads these env vars at import time.
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

from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po


def _files_payload(mock_ingest):
    """Extract the file_list the upload lambda was invoked with (from the synthetic API payload)."""
    payload = mock_ingest.call_args[0][0]
    body = json.loads(payload["body"])
    return body["files"]


@pytest.mark.unit
class TestProcessExternalUploadPathExtension:
    PREFIX = "pipelines/p1/JOB/output/E1/files/"

    def _run(self, extension):
        files = [self.PREFIX + "test/pump.glb", self.PREFIX + "root.glb"]
        ingest = MagicMock(return_value={"Payload": MagicMock(read=lambda: json.dumps(
            {"statusCode": 200, "body": json.dumps({"ok": True})}).encode("utf-8"))})
        with patch.object(po, "_lambda_file_ingestion", ingest):
            po.process_external_upload(
                "upload-1", "a1", "db", "assetFile", files, self.PREFIX, {"http": {}},
                file_base_execution_path_extension=extension)
        return {f["tempKey"]: f["relativeKey"] for f in _files_payload(ingest)}

    def test_default_slash_inserts_no_segment(self):
        rels = self._run("/")
        # relativeKey is just the path relative to the pipeline output folder (no extra segment).
        assert rels[self.PREFIX + "test/pump.glb"] == "test/pump.glb"
        assert rels[self.PREFIX + "root.glb"] == "root.glb"

    def test_the_folder_form_is_inserted_above_the_filename_not_at_the_path_start(self):
        rels = self._run("/exec-2026/")
        # A nested output keeps its own folders; the extension becomes the file's parent folder.
        assert rels[self.PREFIX + "test/pump.glb"] == "test/exec-2026/pump.glb"
        # A root-level output has no folders ahead of it, so the extension is its only one.
        assert rels[self.PREFIX + "root.glb"] == "exec-2026/root.glb"

    def test_the_non_folder_form_concatenates_onto_the_filename(self):
        """No trailing slash means the value is glued to the filename rather than made a folder —
        the distinction the normalizer now preserves."""
        rels = self._run("exec-2026")
        assert rels[self.PREFIX + "test/pump.glb"] == "test/exec-2026pump.glb"
        assert rels[self.PREFIX + "root.glb"] == "exec-2026root.glb"


@pytest.mark.unit
class TestCollectDescriptorsPathExtension:
    PREFIX = "pipelines/p1/JOB/output/E1/files/"

    def _objects(self):
        return {"Contents": [{"Key": self.PREFIX + "test/pump.glb", "Size": 10}]}

    def test_recorded_relative_path_matches_the_write_placement(self):
        """The recorded provenance must place the extension exactly where the write does, or the
        asset-file version-history join looks for a path nothing was written to."""
        with patch.object(po, "s3c") as s3c:
            s3c.head_object.return_value = {"VersionId": "v1"}
            descs = po._collect_output_descriptors(
                self._objects(), "file", self.PREFIX, "abkt",
                file_base_execution_path_extension="/exec-2026/")
        assert descs[0]["relativeFilePath"] == "test/exec-2026/pump.glb"
        assert descs[0]["s3VersionId"] == "v1"

    def test_default_slash_records_plain_relative_path(self):
        with patch.object(po, "s3c") as s3c:
            s3c.head_object.return_value = {"VersionId": "v1"}
            descs = po._collect_output_descriptors(
                self._objects(), "file", self.PREFIX, "abkt")
        assert descs[0]["relativeFilePath"] == "test/pump.glb"
