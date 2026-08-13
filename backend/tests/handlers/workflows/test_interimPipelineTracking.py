# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-step INTAKE narrowing and step-transition bookkeeping in the interim tracking lambda.

Step 1's manifest is narrowed at launch, where the effective pipeline config resolves. Steps 2+ have
their manifests assembled mid-run by this lambda, so the same narrowing has to happen here from the
filters and arity threaded into the interim payload. Covered alongside it: the pinned input version
that must survive into a later step's manifest, the per-step start-date stamp the details view reports
a duration from, the single output-folder listing per transition, and the S3 read faults that must
surface instead of degrading a step's inputs.
"""

import json
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError

# Table names the lambda resolves at import time.
for _k, _v in {
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "S3_ASSETAUXILIARY_STORAGE_BUCKET": "t-aux",
}.items():
    os.environ.setdefault(_k, _v)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf

from backend.backend.handlers.workflows.sfn import interimPipelineTracking as ipt

# The lambda's own exception class: it imports models.common under the deployed (flat) module path,
# so the class object differs from the one backend.backend.models.common exposes.
VAMSGeneralErrorResponse = ipt.VAMSGeneralErrorResponse


def _entry(relative_path):
    return {"relativePath": relative_path, "databaseId": "db", "assetId": "a1",
            "assetRootS3Key": "a1/", "auxPreviewPrefix": "db/a1/preview",
            "bucket": "abkt", "key": f"a1{relative_path}", "versionId": "v1"}


@pytest.mark.unit
class TestNextPipelineInputNarrowing:
    """The next step receives only the files its own effective config admits, and an arity its
    narrowed set cannot satisfy fails the step where the cause is visible."""

    def test_allow_filter_narrows_the_manifest(self):
        files = [_entry("/a.glb"), _entry("/b.e57"), _entry("/c.las")]
        body = {"nextPipelineInputFileFilters": {"allow": ["*.glb", "*.las"]},
                "nextPipelineInputFileArity": "multi"}
        narrowed = ipt.narrow_next_pipeline_inputs(body, files)
        assert [f["relativePath"] for f in narrowed] == ["/a.glb", "/c.las"]

    def test_exclude_filter_removes_matching_files(self):
        files = [_entry("/a.glb"), _entry("/thumb.png")]
        body = {"nextPipelineInputFileFilters": {"exclude": ["*.png"]},
                "nextPipelineInputFileArity": "multi"}
        narrowed = ipt.narrow_next_pipeline_inputs(body, files)
        assert [f["relativePath"] for f in narrowed] == ["/a.glb"]

    def test_absent_keys_narrow_nothing(self):
        # An execution launched without the threaded keys keeps the run's whole selection.
        files = [_entry("/a.glb"), _entry("/b.e57")]
        assert ipt.narrow_next_pipeline_inputs({}, files) == files

    def test_empty_filter_map_narrows_nothing(self):
        files = [_entry("/a.glb")]
        body = {"nextPipelineInputFileFilters": {"allow": [], "exclude": []},
                "nextPipelineInputFileArity": ""}
        assert ipt.narrow_next_pipeline_inputs(body, files) == files

    def test_arity_none_receives_no_files(self):
        files = [_entry("/a.glb"), _entry("/b.e57")]
        assert ipt.narrow_next_pipeline_inputs({"nextPipelineInputFileArity": "none"}, files) == []

    def test_arity_one_with_two_admitted_files_raises(self):
        files = [_entry("/a.glb"), _entry("/b.glb")]
        body = {"nextPipelineInputFileFilters": {"allow": ["*.glb"]},
                "nextPipelineInputFileArity": "one", "nextPipelineId": "thumbnailer"}
        with pytest.raises(VAMSGeneralErrorResponse) as excinfo:
            ipt.narrow_next_pipeline_inputs(body, files)
        assert "thumbnailer" in str(excinfo.value)

    def test_arity_one_admits_exactly_one_after_filtering(self):
        # Two candidate files, one admitted: the arity gate passes because it runs on the NARROWED
        # set, not on the run's whole selection.
        files = [_entry("/a.glb"), _entry("/b.e57")]
        body = {"nextPipelineInputFileFilters": {"allow": ["*.glb"]},
                "nextPipelineInputFileArity": "one"}
        narrowed = ipt.narrow_next_pipeline_inputs(body, files)
        assert [f["relativePath"] for f in narrowed] == ["/a.glb"]

    def test_arity_multi_with_nothing_admitted_raises(self):
        # The chain-breakage case: step 1 emitted only files step 2 cannot read.
        files = [_entry("/out.txt")]
        body = {"nextPipelineInputFileFilters": {"allow": ["*.glb"]},
                "nextPipelineInputFileArity": "multi", "nextPipelineId": "converter"}
        with pytest.raises(VAMSGeneralErrorResponse):
            ipt.narrow_next_pipeline_inputs(body, files)

    def test_written_manifest_carries_only_the_admitted_files(self):
        inputs_table = MagicMock(query=MagicMock(return_value={"Items": [
            {"inputAssetFileKey": "/a1/scan.e57", "databaseId": "db", "assetId": "a1",
             "s3Bucket": "abkt", "assetRootS3Key": "a1/", "versionId": "iv1"},
        ]}))
        body = {
            "workflowExecutionId": "EXEC1",
            "workflowExecutionS3InputOutputBucket": "runbkt",
            "outputFilesPrefix": "pipelines/p1/EXEC1/files/",
            "nextPipelineManifestS3Key": "in/EXEC1/pipeline2/manifest.json",
            "nextPipelineInputFileFilters": {"allow": ["*.glb"]},
            "nextPipelineInputFileArity": "multi",
        }
        captured = {}
        put_object = MagicMock(side_effect=lambda **kw: captured.update({kw["Key"]: kw["Body"]}))
        listing = [{"key": "pipelines/p1/EXEC1/files/scan.glb", "relativePath": "/scan.glb",
                    "versionId": "ov1", "fileSize": 1, "contentType": ""}]
        with patch.object(ipt.dynamodb, "Table", return_value=inputs_table), \
             patch.object(ipt.s3c, "put_object", put_object), \
             patch.object(ipt.eo, "list_current_output_files", return_value=listing):
            ipt.prepare_next_pipeline(body)
        envelope = json.loads(captured[body["nextPipelineManifestS3Key"]].decode("utf-8"))
        # The original .e57 input is dropped; only the .glb the step declared it reads survives.
        assert [f["relativePath"] for f in envelope["inputFiles"]] == ["/scan.glb"]


@pytest.mark.unit
class TestOriginalInputVersionPinning:
    """The version an execution resolved at launch travels into every later step's manifest, so a
    two-step run reads the same object bytes step 1 read."""

    def test_stored_version_is_carried_into_the_entry(self):
        inputs_table = MagicMock(query=MagicMock(return_value={"Items": [
            {"inputAssetFileKey": "/a1/scan.e57", "databaseId": "db", "assetId": "a1",
             "s3Bucket": "abkt", "assetRootS3Key": "a1/", "versionId": "pinned-v7"},
        ]}))
        with patch.object(ipt.dynamodb, "Table", return_value=inputs_table):
            entries = ipt._get_original_input_entries("EXEC1")
        assert entries[0]["versionId"] == "pinned-v7"

    def test_absent_stored_version_is_empty(self):
        inputs_table = MagicMock(query=MagicMock(return_value={"Items": [
            {"inputAssetFileKey": "/a1/scan.e57", "databaseId": "db", "assetId": "a1",
             "s3Bucket": "abkt", "assetRootS3Key": "a1/"},
        ]}))
        with patch.object(ipt.dynamodb, "Table", return_value=inputs_table):
            entries = ipt._get_original_input_entries("EXEC1")
        assert entries[0]["versionId"] == ""

    def test_unshadowed_manifest_entry_keeps_the_pinned_version(self):
        inputs_table = MagicMock(query=MagicMock(return_value={"Items": [
            {"inputAssetFileKey": "/a1/scan.e57", "databaseId": "db", "assetId": "a1",
             "s3Bucket": "abkt", "assetRootS3Key": "a1/", "versionId": "pinned-v7"},
        ]}))
        body = {
            "workflowExecutionId": "EXEC1",
            "workflowExecutionS3InputOutputBucket": "runbkt",
            "outputFilesPrefix": "pipelines/p1/EXEC1/files/",
            "nextPipelineManifestS3Key": "in/EXEC1/pipeline2/manifest.json",
        }
        captured = {}
        put_object = MagicMock(side_effect=lambda **kw: captured.update({kw["Key"]: kw["Body"]}))
        with patch.object(ipt.dynamodb, "Table", return_value=inputs_table), \
             patch.object(ipt.s3c, "put_object", put_object), \
             patch.object(ipt.eo, "list_current_output_files", return_value=[]):
            ipt.prepare_next_pipeline(body)
        envelope = json.loads(captured[body["nextPipelineManifestS3Key"]].decode("utf-8"))
        assert envelope["inputFiles"][0]["versionId"] == "pinned-v7"


@pytest.mark.unit
class TestPipelineStartDateStamp:
    """Each step's start time is recorded as it is flipped NEW -> RUNNING, so the details view can
    report a per-step duration."""

    def _run(self, body, table):
        with patch.object(ipt.dynamodb, "Table", return_value=table), \
             patch.object(ipt.eo, "recorded_output_versions", return_value={}), \
             patch.object(ipt.eo, "list_current_output_files", return_value=[]), \
             patch.object(ipt.eo, "record_pipeline_output_files", MagicMock()), \
             patch.object(ipt.eo, "set_pipeline_status", MagicMock()), \
             patch.object(ipt.eo, "set_pipeline_status_running", MagicMock()):
            ipt.record_previous_pipeline_outputs(body)

    def test_next_pipeline_row_is_stamped(self):
        table = MagicMock()
        self._run({"workflowExecutionId": "EXEC1", "fromPipelineExecutionId": "P1",
                   "nextPipelineExecutionId": "P2"}, table)
        kwargs = table.update_item.call_args.kwargs
        assert kwargs["Key"] == {"pipelineExecutionId": "P2", "workflowExecutionId": "EXEC1"}
        assert "executionStartDate = :sd" in kwargs["UpdateExpression"]
        # A real timestamp, never the empty string a GSI sort key rejects.
        assert kwargs["ExpressionAttributeValues"][":sd"]

    def test_existing_start_date_is_not_overwritten(self):
        table = MagicMock()
        self._run({"workflowExecutionId": "EXEC1", "nextPipelineExecutionId": "P2"}, table)
        condition = table.update_item.call_args.kwargs["ConditionExpression"]
        assert "attribute_not_exists(executionStartDate)" in condition

    def test_no_stamp_without_a_next_pipeline(self):
        table = MagicMock()
        self._run({"workflowExecutionId": "EXEC1", "fromPipelineExecutionId": "P1"}, table)
        table.update_item.assert_not_called()

    def test_stamp_failure_does_not_fail_the_transition(self):
        table = MagicMock()
        table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem")
        # Timing is bookkeeping: it must not break the step transition.
        self._run({"workflowExecutionId": "EXEC1", "nextPipelineExecutionId": "P2"}, table)


@pytest.mark.unit
class TestOutputListingIsReused:
    """The shared output folder is listed once per step transition: the attribution pass and the
    shadowing pass need the identical set."""

    def test_listing_happens_once_per_invocation(self):
        body = {
            "workflowExecutionId": "EXEC1",
            "workflowExecutionS3InputOutputBucket": "runbkt",
            "outputFilesPrefix": "pipelines/p1/EXEC1/files/",
            "fromPipelineExecutionId": "P1",
            "priorPipelineExecutionIds": ["P1"],
            "nextPipelineManifestS3Key": "in/EXEC1/pipeline2/manifest.json",
        }
        listing = MagicMock(return_value=[
            {"key": "pipelines/p1/EXEC1/files/out.glb", "relativePath": "/out.glb",
             "versionId": "ov1", "fileSize": 1, "contentType": ""}])
        with patch.object(ipt.s3c, "put_object", MagicMock()), \
             patch.object(ipt.eo, "recorded_output_versions", return_value={}), \
             patch.object(ipt.eo, "list_current_output_files", listing), \
             patch.object(ipt.eo, "record_pipeline_output_files", MagicMock()), \
             patch.object(ipt.eo, "set_pipeline_status", MagicMock()), \
             patch.object(ipt, "_stamp_pipeline_start_date", MagicMock()), \
             patch.object(ipt, "_get_original_input_entries", return_value=[]):
            ipt.lambda_handler({"body": body}, MagicMock())
        assert listing.call_count == 1

    def test_manifest_still_shadows_from_the_reused_listing(self):
        body = {
            "workflowExecutionId": "EXEC1",
            "workflowExecutionS3InputOutputBucket": "runbkt",
            "outputFilesPrefix": "pipelines/p1/EXEC1/files/",
            "fromPipelineExecutionId": "P1",
            "priorPipelineExecutionIds": ["P1"],
            "nextPipelineManifestS3Key": "in/EXEC1/pipeline2/manifest.json",
        }
        captured = {}
        put_object = MagicMock(side_effect=lambda **kw: captured.update({kw["Key"]: kw["Body"]}))
        listing = [{"key": "pipelines/p1/EXEC1/files/scan.e57", "relativePath": "/scan.e57",
                    "versionId": "ov9", "fileSize": 1, "contentType": ""}]
        with patch.object(ipt.s3c, "put_object", put_object), \
             patch.object(ipt.eo, "recorded_output_versions", return_value={}), \
             patch.object(ipt.eo, "list_current_output_files", return_value=listing), \
             patch.object(ipt.eo, "record_pipeline_output_files", MagicMock()), \
             patch.object(ipt.eo, "set_pipeline_status", MagicMock()), \
             patch.object(ipt, "_stamp_pipeline_start_date", MagicMock()), \
             patch.object(ipt, "_get_original_input_entries", return_value=[
                 {"relativePath": "/scan.e57", "bucket": "abkt", "key": "a1/scan.e57",
                  "versionId": "iv1"}]):
            ipt.lambda_handler({"body": body}, MagicMock())
        envelope = json.loads(captured[body["nextPipelineManifestS3Key"]].decode("utf-8"))
        assert envelope["inputFiles"][0]["versionId"] == "ov9"
        assert envelope["inputFiles"][0]["key"] == "pipelines/p1/EXEC1/files/scan.e57"

    def test_module_s3_client_retries(self):
        # botocore normalizes max_attempts to total_max_attempts (retries + 1).
        assert ipt.s3c.meta.config.retries["total_max_attempts"] == 6
        assert ipt.s3c.meta.config.retries["mode"] == "adaptive"


@pytest.mark.unit
class TestConfigRenderReadFaults:
    """A read fault on the next step's config or metadata is a fault, not an absent file: it must
    surface so the interim state's Catch reconciles the run rather than shipping literal {{tags}}."""

    RAW_CFG = '{"asset": "{{firstAssetFileAssetId}}"}'

    def _body(self):
        return {
            "workflowExecutionId": "EXEC1",
            "nextPipelineConfigS3Key": "in/EXEC1/pipeline2/config.json",
            "nextPipelineId": "convertPipe",
        }

    def test_absent_config_is_a_no_op(self):
        get_object = MagicMock(side_effect=ClientError(
            {"Error": {"Code": "NoSuchKey"}}, "GetObject"))
        put_object = MagicMock()
        with patch.object(ipt.s3c, "get_object", get_object), \
             patch.object(ipt.s3c, "put_object", put_object):
            ipt._render_next_pipeline_config(
                self._body(), {"inputFiles": []}, "runbkt", "in/EXEC1/pipeline2/config.json")
        put_object.assert_not_called()

    def test_transient_read_fault_raises(self):
        get_object = MagicMock(side_effect=ClientError(
            {"Error": {"Code": "InternalError"}}, "GetObject"))
        with patch.object(ipt.s3c, "get_object", get_object), \
             patch.object(ipt.s3c, "put_object", MagicMock()):
            with pytest.raises(ClientError):
                ipt._render_next_pipeline_config(
                    self._body(), {"inputFiles": []}, "runbkt", "in/EXEC1/pipeline2/config.json")

    def test_kms_denied_on_the_config_read_raises(self):
        get_object = MagicMock(side_effect=ClientError(
            {"Error": {"Code": "KMS.KMSInvalidStateException"}}, "GetObject"))
        with patch.object(ipt.s3c, "get_object", get_object), \
             patch.object(ipt.s3c, "put_object", MagicMock()):
            with pytest.raises(ClientError):
                ipt._render_next_pipeline_config(
                    self._body(), {"inputFiles": []}, "runbkt", "in/EXEC1/pipeline2/config.json")

    def test_metadata_read_fault_raises_instead_of_rendering_empty(self):
        cfg = '{"prompt": "{{assetMetadataObject}}"}'
        manifest = {"inputFiles": [{"databaseId": "db", "assetId": "a1", "relativePath": "/x.e57"}],
                    "inputMetadataS3Location": "s3://runbkt/shared/metadata.json"}

        def get_object(**kwargs):
            if kwargs["Key"].endswith("config.json"):
                return {"Body": MagicMock(read=lambda: cfg.encode("utf-8"))}
            raise ClientError({"Error": {"Code": "InternalError"}}, "GetObject")

        with patch.object(ipt.s3c, "get_object", MagicMock(side_effect=get_object)), \
             patch.object(ipt.s3c, "put_object", MagicMock()):
            with pytest.raises(ClientError):
                ipt._render_next_pipeline_config(
                    self._body(), manifest, "runbkt", "in/EXEC1/pipeline2/config.json")
