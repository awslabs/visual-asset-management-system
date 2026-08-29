# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The generated ASL's outbound contract with the two lambdas that read it.

Guards:

- **S3-CONTRACTS-005** -- ``auxPreviewPipelineSuffix``. The execute handler sources a pipeline's
  configured viewer subfolder from its record for step 1; the interim path hardcoded ``""`` for every
  later step, so the same pipeline wrote its viewer data to a different aux location purely because
  of its position in the workflow. The value now travels pipeline record -> flat ASL pipeline dict ->
  interim payload -> next pipeline's manifest, and each hop is asserted separately so a break cannot
  be papered over by the next one.
- **S3-CONTRACTS-039** -- the process-output payload carried ``pipeline`` and ``description``, which
  the end-state lambda never reads. Asserted as a property (every body key is referenced in the
  reader) rather than as two named absences, so a future dead key is caught too.
- **S3-CONTRACTS-040** -- the ``PIPELINE_OUTPUT_RESULTS_PREFIX`` comment declared the prefix unused by
  workflow generation while the generator threads it into every definition. Asserted as the general
  claim: no constant may be documented as unused by workflow generation while the generator
  references it.
"""

import json
import os
import re
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux")

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf

from backend.backend.common.workflows.workflowAslBuilder import generate_workflow_asl
from backend.backend.common.workflows.workflowAsl import to_asl_pipeline_dict
from backend.backend.common.workflows import pipelineRecords as pr
from backend.backend.handlers.workflows.sfn import interimPipelineTracking as ipt

_BACKEND_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")
_READER_SOURCE = os.path.normpath(os.path.join(
    _BACKEND_ROOT, "handlers", "workflows", "sfn", "processWorkflowExecutionOutput.py"))
_PATH_PATTERNS_SOURCE = os.path.normpath(os.path.join(_BACKEND_ROOT, "common", "s3PathPatterns.py"))
_ASL_BUILDER_SOURCE = os.path.normpath(os.path.join(
    _BACKEND_ROOT, "common", "workflows", "workflowAslBuilder.py"))


def _pipeline(name, **extra):
    base = {"name": name, "pipelineId": name, "databaseId": "pdb"}
    base.update(extra)
    return base


def _asl(pipelines):
    definition, job_names = generate_workflow_asl(
        pipelines, "db", "wf",
        process_workflow_output_function="pf",
        interim_tracking_function="itf",
        error_handler_function="ehf")
    return definition, job_names


def _state(definition, prefix):
    return [definition["States"][k] for k in definition["States"] if k.startswith(prefix)]


def _payload_body(state):
    return state["Parameters"]["Payload"]["body"]


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# S3-CONTRACTS-005
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestAuxPreviewSuffixReachesLaterSteps:

    def test_the_pipeline_record_adapter_carries_the_configured_suffix(self):
        """Hop 1. The generator only sees the flat dict this adapter builds, so a suffix dropped here
        makes every later hop inert no matter how correct it is."""
        record = {
            "pipelineId": "potree", "databaseId": "pdb",
            "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"}},
            "systemConfig": pr.build_pipeline_system_config(
                aux_preview_pipeline_suffix="/PotreeViewer"),
        }
        assert to_asl_pipeline_dict(record, "job-2")["auxPreviewPipelineSuffix"] == "/PotreeViewer"

    def test_the_adapter_reports_no_suffix_for_a_pipeline_that_declares_none(self):
        """Negative control: the key is present and empty, not absent-or-arbitrary."""
        record = {"pipelineId": "plain", "databaseId": "pdb",
                  "systemConfig": pr.build_pipeline_system_config()}
        assert to_asl_pipeline_dict(record, "job-1")["auxPreviewPipelineSuffix"] == ""

    def test_each_interim_state_carries_the_suffix_of_the_step_it_prepares(self):
        """Hop 2. Three distinct suffixes, so an off-by-one (carrying the current or the first
        pipeline's value) cannot pass."""
        definition, _jobs = _asl([
            _pipeline("p1", auxPreviewPipelineSuffix="/One"),
            _pipeline("p2", auxPreviewPipelineSuffix="/Two"),
            _pipeline("p3", auxPreviewPipelineSuffix="/Three"),
        ])
        interims = sorted(
            (k for k in definition["States"] if k.startswith("interim-")),
            key=lambda k: int(k.split("-")[1]))
        suffixes = [_payload_body(definition["States"][k])["nextPipelineAuxPreviewSuffix"]
                    for k in interims]
        assert suffixes == ["/Two", "/Three"]

    def test_a_next_pipeline_without_a_suffix_threads_an_empty_string(self):
        definition, _jobs = _asl([_pipeline("p1", auxPreviewPipelineSuffix="/One"),
                                  _pipeline("p2")])
        body = _payload_body(_state(definition, "interim-")[0])
        assert body["nextPipelineAuxPreviewSuffix"] == ""

    def test_a_raw_pipeline_record_shape_is_also_accepted(self):
        definition, _jobs = _asl([
            _pipeline("p1"),
            _pipeline("p2", systemConfig={"auxPreviewPipelineSuffix": "/Nested"}),
        ])
        body = _payload_body(_state(definition, "interim-")[0])
        assert body["nextPipelineAuxPreviewSuffix"] == "/Nested"

    def test_the_interim_lambda_writes_the_threaded_suffix_into_the_next_manifest(self):
        """Hop 3. The manifest value is what manifestHelper and the {{auxPreviewPipelineSuffix}} tag
        read, so it is asserted on the object actually put to S3."""
        manifest = self._manifest_for(
            {"nextPipelineAuxPreviewSuffix": "/PotreeViewer"})
        assert manifest["auxPreviewPipelineSuffix"] == "/PotreeViewer"

    def test_an_execution_launched_before_the_suffix_was_threaded_still_writes_an_empty_string(self):
        """Negative control + backward compatibility: an in-flight state machine baked without the
        key must not crash the interim step or write a null."""
        manifest = self._manifest_for({})
        assert manifest["auxPreviewPipelineSuffix"] == ""

    @staticmethod
    def _manifest_for(body_overrides):
        manifest_key = "pipelines/workflowExecutionInputs/EXEC1/pipeline2/manifest.json"
        body = {
            "workflowExecutionId": "EXEC1", "workflowId": "wf1", "workflowDatabaseId": "wdb1",
            "executingUserName": "user@x",
            "workflowExecutionS3InputOutputBucket": "abkt",
            "outputFilesPrefix": "pipelines/p1/job-1/output/EXEC1/files/",
            "nextPipelineManifestS3Key": manifest_key,
            "nextPipelineConfigS3Key": "",
            "nextPipelineExecutionId": "P2", "nextPipelineId": "potree",
            "nextPipelineDatabaseId": "pdb", "nextPipelineJobName": "job-2",
        }
        body.update(body_overrides)
        captured = {}
        inputs_table = MagicMock(query=MagicMock(return_value={"Items": [
            {"inputAssetFileKey": "/a1/scan.e57", "databaseId": "db", "assetId": "a1",
             "s3Bucket": "abkt", "assetRootS3Key": "a1/"}]}))
        with patch.object(ipt.dynamodb, "Table", return_value=inputs_table), \
             patch.object(ipt.s3c, "put_object",
                          MagicMock(side_effect=lambda **kw: captured.update(
                              {kw["Key"]: kw["Body"]}))), \
             patch.object(ipt.eo, "list_current_output_files", return_value=[]):
            ipt.prepare_next_pipeline(body)
        return json.loads(captured[manifest_key].decode("utf-8"))


# ---------------------------------------------------------------------------
# S3-CONTRACTS-039
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestProcessOutputPayloadHasNoDeadKeys:

    @staticmethod
    def _body_keys():
        definition, _jobs = _asl([_pipeline("p1"), _pipeline("p2")])
        body = _payload_body(_state(definition, "process-outputs-")[0])
        return [key[:-2] if key.endswith(".$") else key for key in body]

    def test_every_body_key_is_referenced_by_the_end_state_lambda(self):
        source = _read(_READER_SOURCE)
        unread = [key for key in self._body_keys()
                  if f"'{key}'" not in source and f'"{key}"' not in source]
        assert unread == []

    def test_the_scan_would_notice_a_key_the_reader_ignores(self):
        """Positive control for the scan: a fabricated key is detected as unread, so the assertion
        above is not passing because the search always matches."""
        source = _read(_READER_SOURCE)
        fabricated = "stepLabelNobodyReads"
        assert f"'{fabricated}'" not in source and f'"{fabricated}"' not in source

    def test_the_two_reported_dead_keys_are_gone(self):
        assert "pipeline" not in self._body_keys()
        assert "description" not in self._body_keys()

    def test_the_keys_the_end_state_lambda_needs_are_still_sent(self):
        """Control against over-removal: dropping dead keys must not drop live ones."""
        keys = set(self._body_keys())
        assert {"workflowExecutionId", "workflowDatabaseId", "workflowId",
                "endStatePipelineExecutionId", "priorPipelineExecutionIds",
                "filesPathKey", "metadataPathKey", "previewPathKey", "resultsPathKey",
                "workflowExecutionS3InputOutputBucket", "outputLocationType",
                "outputAssetId", "outputDatabaseId", "outputFileBaseExecutionPathExtension",
                "executingUserName", "executingRequestContext"} <= keys


# ---------------------------------------------------------------------------
# S3-CONTRACTS-040
# ---------------------------------------------------------------------------

_UNUSED_CLAIM = "not yet used by workflow generation"


def _documented_as_unused():
    """Constant names whose s3PathPatterns comment entry claims workflow generation does not use them.

    An entry starts at ``# NAME: ...`` and continues on the indented ``#   ...`` lines below it, which
    is the file's own comment convention.
    """
    entries = {}
    current = None
    for line in _read(_PATH_PATTERNS_SOURCE).splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            current = None
            continue
        text = stripped.lstrip("#").strip()
        match = re.match(r"^([A-Z][A-Z0-9_]+):\s*(.*)$", text)
        if match:
            current = match.group(1)
            entries[current] = match.group(2)
        elif current:
            entries[current] += " " + text
    return {name for name, body in entries.items() if _UNUSED_CLAIM in body.lower()}


@pytest.mark.unit
class TestPathPatternCommentsMatchTheGenerator:

    def test_no_constant_is_documented_as_unused_while_the_generator_uses_it(self):
        builder_source = _read(_ASL_BUILDER_SOURCE)
        contradicted = sorted(name for name in _documented_as_unused()
                              if name in builder_source)
        assert contradicted == []

    def test_the_scan_finds_the_claim_at_all(self):
        """Positive control: the file still carries such a claim (PIPELINE_INPUT_PREFIX), so the
        assertion above is not green merely because the scan matched nothing."""
        assert "PIPELINE_INPUT_PREFIX" in _documented_as_unused()

    def test_the_results_prefix_is_genuinely_used_by_the_generator(self):
        """Anchors the pair: the constant the stale comment described IS referenced, so re-adding the
        claim to it fails the first assertion rather than silently agreeing with it."""
        assert "PIPELINE_OUTPUT_RESULTS_PREFIX" in _read(_ASL_BUILDER_SOURCE)
        assert "PIPELINE_OUTPUT_RESULTS_PREFIX" not in _documented_as_unused()
