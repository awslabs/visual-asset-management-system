# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A compliance pipeline rule on the SYSTEM GenAI metadata workflow, as it is shipped.

The rule names the `GLOBAL` `system-genai-metadata` workflow like any other: the rule model accepts
it, and its input selection resolves against the workflow's and pipeline's shipped systemConfig
(`backendPipelines/system/genAiMetadata/vamsSchema`), which take one file per run and exclude
preview companions. The workflow writes no `compliance-output.json`, so a succeeded run is judged on
the derived `execution_success` and `processing_duration_seconds`, as for any workflow without that
file. Its `perInputFileVersion` restriction is the one the upload's own run holds, so a rule launch
over a file version that run is analyzing waits for it.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.tests.handlers.compliance._harness import ASSET, DB
from common.compliance import evaluationEngine as engine
from handlers.compliance import complianceEvaluationStore as store
from models.compliance import PipelineRule

SCHEMA_DIR = (Path(__file__).resolve().parents[4] / "backendPipelines" / "system" / "genAiMetadata"
              / "vamsSchema")
WORKFLOW = json.loads((SCHEMA_DIR / "workflow.json").read_text())
PIPELINE = json.loads((SCHEMA_DIR / "pipeline.json").read_text())
TEMPLATE = json.loads((SCHEMA_DIR / "templates" / "system-genai-metadata-default.json").read_text())

RULE_BODY = {
    "ruleType": "pipeline",
    "enforcement": "quarantine",
    "pipelineRef": {"databaseId": "GLOBAL", "workflowId": "system-genai-metadata",
                    "pipelineDatabaseId": "GLOBAL", "pipelineId": "system-genai-metadata",
                    "templateId": "system-genai-metadata-default"},
    "checks": [{"name": "must_succeed", "outputField": "execution_success",
                "tolerance": {"operator": "gte", "value": 1.0}}],
}


def _rule(**input_files):
    body = dict(RULE_BODY)
    if input_files:
        body["inputFiles"] = input_files
    return PipelineRule(**body)


def _resolve(rule, files):
    pipeline = {"pipelineId": "system-genai-metadata", "systemConfig": PIPELINE["systemConfig"],
                "enabled": True, "archived": False}
    workflow = {"workflowId": "system-genai-metadata", "systemConfig": WORKFLOW["systemConfig"]}
    return store.resolve_pipeline_rule_inputs(
        rule, DB, ASSET, workflow, pipeline, TEMPLATE, MagicMock(return_value=files))


def _inputs(*keys):
    return [{"databaseId": DB, "assetId": ASSET, "relativeFileKey": key} for key in keys]


@pytest.mark.unit
class TestTheRuleNamesTheSystemWorkflow:

    def test_the_rule_model_accepts_the_global_system_workflow(self):
        rule = _rule()
        assert (rule.pipelineRef.databaseId, rule.pipelineRef.workflowId) == \
            ("GLOBAL", "system-genai-metadata")

    def test_the_shipped_workflow_is_a_system_workflow_locked_per_file_version(self):
        assert WORKFLOW["isSystem"] is True
        assert WORKFLOW["systemConfig"]["concurrencyRestriction"] == "perInputFileVersion"
        assert WORKFLOW["systemConfig"]["inputFileArity"] == "one"


@pytest.mark.unit
class TestTheSelectionTakesOneFile:

    def test_matching_on_a_single_file_asset_sends_it(self):
        inputs, error = _resolve(_rule(), ["/pump.glb", "/pump.glb.previewFile.png"])
        assert error is None
        assert inputs == _inputs("/pump.glb")

    def test_matching_on_an_asset_of_several_files_is_an_error(self):
        inputs, error = _resolve(_rule(), ["/pump.glb", "/manual.pdf"])
        assert inputs is None
        assert error == store.INPUT_SELECTION_NOT_ONE_FILE

    def test_an_explicit_file_is_sent(self):
        inputs, error = _resolve(_rule(mode="explicit", keys=["/manual.pdf"]),
                                 ["/pump.glb", "/manual.pdf"])
        assert error is None
        assert inputs == _inputs("/manual.pdf")

    def test_the_whole_asset_is_refused(self):
        inputs, error = _resolve(_rule(mode="wholeAsset"), ["/pump.glb"])
        assert inputs is None
        assert error is not None


@pytest.mark.unit
class TestASucceededRunWithoutComplianceOutput:

    @pytest.mark.parametrize("status,passed", [("SUCCEEDED", True), ("FAILED", False)])
    def test_is_judged_on_the_derived_measurements(self, status, passed):
        (result,) = engine.evaluate_pipeline_rules(
            {"genai": _rule()}, status, None, started_at="2026-03-01T12:00:00+00:00",
            completed_at="2026-03-01T12:01:30+00:00", execution_id="e1")
        assert result.passed is passed
