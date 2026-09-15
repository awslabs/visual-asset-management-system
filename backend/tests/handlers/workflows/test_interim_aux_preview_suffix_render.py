# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The last hop of the ``auxPreviewPipelineSuffix`` chain: the next pipeline's RENDERED config.

Guards:

- **S3-CONTRACTS-005** -- a pipeline's configured viewer subfolder must not depend on its position in
  the workflow. ``test_asl_output_contract.py`` asserts the hops that carry the value (the ASL's
  ``$.stepAuxPreviewSuffixes[i]`` index into the execution's own list -> interim payload -> the
  manifest object put to S3) and ``test_execute_metadata_identity_and_gate.py`` asserts the list the
  execute handler sends. This file asserts the hop those stop short of: the interim lambda re-renders
  the next step's input
  configuration from that same manifest, so the ``{{auxPreviewPipelineSuffix}}`` tag and the derived
  ``{{firstAssetFileAuxPreviewS3Uri}}`` path a template writes its viewer data to are the values a
  pipeline actually reads. A manifest that carries the suffix while the rendered config does not would
  leave the tag empty with the manifest looking correct. The final class states the invariant across
  BOTH producers -- one pipeline record driving the launch path and the interim path -- since a
  step-2-only assertion still passes if step 1 is what moves.

The suffixed and unsuffixed arms are compared against each other rather than against a literal path, so
the assertion states the relationship (the subfolder is appended to the file's own aux preview prefix)
instead of pinning the prefix layout, which is derived elsewhere.
"""

import json
import os
import re

import pytest
from unittest.mock import MagicMock, patch

# Table/bucket names the lambda resolves at import time.
for _k, _v in {
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME": "t-pin-cfg",
    "S3_ASSETAUXILIARY_STORAGE_BUCKET": "t-aux",
}.items():
    os.environ.setdefault(_k, _v)

from backend.backend.handlers.workflows.sfn import interimPipelineTracking as ipt
from backend.backend.common.workflows import executionRecords as er
from backend.backend.common.workflows import pipelineRecords as pr
from backend.backend.common.workflows import templateRender as tr
from backend.backend.common.workflows.workflowAsl import to_asl_pipeline_dict
from backend.backend.common.workflows.workflowAslBuilder import generate_workflow_asl

_MANIFEST_KEY = "pipelines/workflowExecutionInputs/EXEC1/pipeline2/manifest.json"
_CONFIG_KEY = "pipelines/workflowExecutionInputs/EXEC1/pipeline2/config.json"

# Every tag the suffix reaches: the raw value, the aux preview path derived from it, and the
# unsuffixed prefix it is derived FROM (so the two can be compared without hardcoding either).
_RAW_CONFIG = json.dumps({
    "suffix": "{{auxPreviewPipelineSuffix}}",
    "viewerUri": "{{firstAssetFileAuxPreviewS3Uri}}",
    "previewPrefix": "{{firstAssetFileAuxPreviewPrefix}}",
})


def _prepare(body_overrides):
    """Run a step transition and return (manifest, rendered config text) for the next pipeline.

    Only the interim lambda's own AWS calls are stubbed; the manifest envelope, the tag catalog and
    the render are the real ones, so the assertions sit on what the next pipeline would read.
    """
    body = {
        "workflowExecutionId": "EXEC1", "workflowId": "wf1", "workflowDatabaseId": "wdb1",
        "executingUserName": "user@x",
        "workflowExecutionS3InputOutputBucket": "runbkt",
        "outputFilesPrefix": "pipelines/p1/job-1/output/EXEC1/files/",
        "nextPipelineManifestS3Key": _MANIFEST_KEY,
        "nextPipelineConfigS3Key": _CONFIG_KEY,
        "nextPipelineExecutionId": "P2", "nextPipelineId": "potree",
        "nextPipelineDatabaseId": "pdb", "nextPipelineJobName": "job-2",
    }
    body.update(body_overrides)

    written = {}
    inputs_table = MagicMock(query=MagicMock(return_value={"Items": [
        {"inputAssetFileKey": "/a1/scan.e57", "databaseId": "db", "assetId": "a1",
         "s3Bucket": "abkt", "assetRootS3Key": "a1/", "versionId": "iv1"}]}))

    def _get_object(**kwargs):
        assert kwargs["Key"] == _CONFIG_KEY
        return {"Body": MagicMock(read=lambda: _RAW_CONFIG.encode("utf-8"))}

    with patch.object(ipt.dynamodb, "Table", return_value=inputs_table), \
         patch.object(ipt.s3c, "get_object", MagicMock(side_effect=_get_object)), \
         patch.object(ipt.s3c, "put_object",
                      MagicMock(side_effect=lambda **kw: written.update({kw["Key"]: kw["Body"]}))), \
         patch.object(ipt.eo, "list_current_output_files", return_value=[]):
        ipt.prepare_next_pipeline(body)

    return (json.loads(written[_MANIFEST_KEY].decode("utf-8")),
            written[_CONFIG_KEY].decode("utf-8"))


@pytest.mark.unit
class TestAuxPreviewSuffixReachesTheRenderedConfig:

    def test_the_rendered_config_carries_the_threaded_viewer_subfolder(self):
        _manifest, rendered = _prepare({"nextPipelineAuxPreviewSuffix": "/PotreeViewer"})
        config = json.loads(rendered)
        assert config["suffix"] == "/PotreeViewer"
        assert config["viewerUri"].endswith("/PotreeViewer")

    def test_the_viewer_uri_is_the_files_own_preview_prefix_plus_the_subfolder(self):
        """The subfolder is appended to the input file's aux preview prefix, not substituted for it or
        placed above it -- the two arms differ by exactly the subfolder."""
        _manifest, suffixed = _prepare({"nextPipelineAuxPreviewSuffix": "/PotreeViewer"})
        _plain_manifest, plain = _prepare({"nextPipelineAuxPreviewSuffix": ""})
        suffixed_config, plain_config = json.loads(suffixed), json.loads(plain)
        assert suffixed_config["previewPrefix"] == plain_config["previewPrefix"]
        assert suffixed_config["viewerUri"] == plain_config["viewerUri"] + "/PotreeViewer"

    def test_the_manifest_and_the_rendered_config_report_the_same_suffix(self):
        """The two consumers read different objects -- manifestHelper reads the manifest, a template
        reads the rendered tag -- so a step whose manifest and config disagree writes its viewer data
        to two places. The value is asserted alongside the agreement: agreement ALONE also holds when
        both are empty, which is the state this guards against."""
        manifest, rendered = _prepare({"nextPipelineAuxPreviewSuffix": "/PotreeViewer"})
        assert manifest["auxPreviewPipelineSuffix"] == json.loads(rendered)["suffix"] == "/PotreeViewer"

    def test_a_next_pipeline_declaring_no_subfolder_renders_an_unsuffixed_preview_path(self):
        """Positive control: a pipeline without the field keeps the plain per-file preview path -- no
        trailing separator, no literal placeholder text."""
        manifest, rendered = _prepare({"nextPipelineAuxPreviewSuffix": ""})
        config = json.loads(rendered)
        assert manifest["auxPreviewPipelineSuffix"] == ""
        assert config["suffix"] == ""
        assert config["viewerUri"] == f"s3://{manifest['auxBucket']}/{config['previewPrefix']}"

    def test_a_state_machine_baked_before_the_key_existed_still_renders(self):
        """Positive control for backward compatibility: an in-flight definition carrying no
        nextPipelineAuxPreviewSuffix must render rather than raise or emit a null."""
        manifest, rendered = _prepare({})
        config = json.loads(rendered)
        assert manifest["auxPreviewPipelineSuffix"] == ""
        assert config["suffix"] == ""

    def test_every_tag_in_the_probe_config_was_substituted(self):
        """Control against a vacuous suite: the assertions above read rendered values, so an
        unrendered config (or a tag the catalog dropped) must not pass as agreement."""
        _manifest, rendered = _prepare({"nextPipelineAuxPreviewSuffix": "/PotreeViewer"})
        assert "{{" not in rendered
        assert set(json.loads(rendered)) == {"suffix", "viewerUri", "previewPrefix"}


def _pipeline_record(pipeline_id, aux_preview_suffix=""):
    return {
        "pipelineId": pipeline_id, "databaseId": "pdb",
        "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"}},
        "systemConfig": pr.build_pipeline_system_config(
            aux_preview_pipeline_suffix=aux_preview_suffix),
    }


def _declared_suffix(record):
    """The launch path's own sourcing expression, in one place so both arms below read the record the
    same way (executeWorkflow builds its per-step list with this expression)."""
    return (record.get("systemConfig", {}) or {}).get("auxPreviewPipelineSuffix", "") or ""


def _suffix_the_asl_threads_to_step_two(record):
    """The value a deployed definition actually hands the interim lambda for step 2. The definition
    threads a static INDEX into the execution's own stepAuxPreviewSuffixes list, so the JSONPath is
    read out of the generated ASL and resolved against that list the way Step Functions resolves it --
    the index is not restated here, and the record stays the only input either arm is given."""
    records = [_pipeline_record("first"), record]
    definition, _job_names = generate_workflow_asl(
        [to_asl_pipeline_dict(rec, f"job-{i + 1}") for i, rec in enumerate(records)],
        "db", "wf",
        process_workflow_output_function="pf",
        interim_tracking_function="itf",
        error_handler_function="ehf")
    interim = [definition["States"][k] for k in definition["States"] if k.startswith("interim-")][0]
    path = interim["Parameters"]["Payload"]["body"]["nextPipelineAuxPreviewSuffix.$"]
    match = re.fullmatch(r"\$\.stepAuxPreviewSuffixes\[(\d+)\]", path)
    assert match, f"interim state threads {path!r}, not an index into the per-execution list"
    return [_declared_suffix(rec) for rec in records][int(match.group(1))]


@pytest.mark.unit
class TestSamePipelineResolvesTheSameAuxPreviewPathAtAnyStepPosition:
    """The invariant the whole chain exists for, asserted across BOTH producers.

    The tests above cover the interim (steps 2+) side alone, so they would still pass if the launch
    path changed. Here one pipeline record drives both arms -- the execute handler's envelope builder
    for step 1, and record -> per-execution suffix list -> the generated interim state's index into it
    -> interim lambda for step 2 -- and the rendered viewer path must come out identical."""

    def test_a_viewer_pipeline_writes_to_the_same_location_at_step_1_and_at_step_2(self):
        record = _pipeline_record("potree", "/PotreeViewer")
        step2_manifest, step2_rendered = _prepare(
            {"nextPipelineAuxPreviewSuffix": _suffix_the_asl_threads_to_step_two(record)})
        step1_rendered = self._render_step_one(record, step2_manifest)
        step2_config = json.loads(step2_rendered)
        assert step1_rendered["suffix"] == step2_config["suffix"] == "/PotreeViewer"
        assert step1_rendered["viewerUri"] == step2_config["viewerUri"]

    def test_the_step_2_arm_diverges_when_the_interim_payload_carries_no_suffix(self):
        """Negative control: the parity assertion is only meaningful if it can fail. An interim
        payload without the threaded value -- the pre-threading behaviour -- must break parity."""
        record = _pipeline_record("potree", "/PotreeViewer")
        step2_manifest, step2_rendered = _prepare({"nextPipelineAuxPreviewSuffix": ""})
        step1_rendered = self._render_step_one(record, step2_manifest)
        assert step1_rendered["viewerUri"] != json.loads(step2_rendered)["viewerUri"]

    @staticmethod
    def _render_step_one(record, step2_manifest):
        """Step 1's rendered config: the launch path's own sourcing expression (executeWorkflow reads
        systemConfig.auxPreviewPipelineSuffix off the pipeline record at entry 0 of its per-step list)
        through the real envelope builder. The input file entry is reused from the step-2 manifest so
        the suffix is the only thing that can differ between the arms."""
        launch_suffix = _declared_suffix(record)
        step1_manifest = er.build_manifest_envelope(
            input_files=step2_manifest["inputFiles"],
            input_metadata_s3_location=step2_manifest["inputMetadataS3Location"],
            outputs={"bucket": "runbkt", "files": "f/", "previews": "p/",
                     "metadata": "m/", "results": "r/"},
            aux_bucket=step2_manifest["auxBucket"],
            aux_temp_prefix="pipelines/potree/EXEC1/",
            aux_preview_pipeline_suffix=launch_suffix)
        return json.loads(tr.render_config(_RAW_CONFIG, step1_manifest, {"pipelineId": "potree"}))
