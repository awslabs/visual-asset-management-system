#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Consistency checks on the isaacLabTraining vamsSchema bundles.

Execute-time validation reads the WORKFLOW record's assetScope, so a pipeline that operates on the
whole asset only runs when its workflow bundle declares the matching scope. Training and evaluation
share one vamsExecute lambda and one state machine, discriminated solely by ``trainingConfig.mode``
from the template config body, so the evaluation pipeline must require a template.

Both pipelines are launched manually, so neither bundle declares a trigger: a trigger a bundle ships is
registered as a workflow-triggers row at deploy time, and the deployment has no configuration that
suppresses one."""

import os
import json

import pytest

_SCHEMA_ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "vamsSchema"))
_BUNDLE_NAMES = ("training", "evaluation")


def _load(*parts):
    with open(os.path.join(_SCHEMA_ROOT, *parts), encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.unit
class TestTrainingBundle:
    def test_whole_asset_pipeline_has_whole_asset_workflow_scope(self):
        pipeline_scope = _load("training", "pipeline.json")["systemConfig"]["assetScope"]
        workflow_scope = _load("training", "workflow.json")["systemConfig"]["assetScope"]
        assert pipeline_scope.get("wholeAsset") is True
        assert workflow_scope["wholeAssetAllowed"] is True

    def test_workflow_arity_and_metadata_match_the_pipeline(self):
        pipeline_config = _load("training", "pipeline.json")["systemConfig"]
        workflow_config = _load("training", "workflow.json")["systemConfig"]
        assert workflow_config["inputFileArity"] == pipeline_config["inputFileArity"]
        assert workflow_config["metadataInputs"] == pipeline_config["metadataInputs"]


@pytest.mark.unit
class TestEvaluationBundle:
    def test_evaluation_requires_a_template_for_its_mode(self):
        assert _load("evaluation", "pipeline.json")["systemConfig"]["requireTemplate"] is True

    def test_shipped_evaluation_template_selects_evaluate_mode(self):
        template = _load("evaluation", "templates", "isaaclab-evaluation-cartpole.json")
        config_body = json.loads(template["configBody"])
        assert config_body["trainingConfig"]["mode"] == "evaluate"


@pytest.mark.unit
class TestManualExecutionOnly:
    @pytest.mark.parametrize("bundle", _BUNDLE_NAMES)
    def test_neither_bundle_declares_a_trigger(self, bundle):
        assert "triggers" not in _load(bundle, "workflow.json")

    @pytest.mark.parametrize("bundle", _BUNDLE_NAMES)
    def test_the_loader_reads_the_workflow_it_is_asserting_on(self, bundle):
        # Positive control: a path that resolved to nothing would raise here rather than let the
        # absence check above pass against an empty document.
        workflow = _load(bundle, "workflow.json")
        assert workflow["workflowName"].startswith("Isaac Lab")
        assert workflow["systemConfig"]

    def test_both_bundles_are_present(self):
        found = sorted(name for name in os.listdir(_SCHEMA_ROOT)
                       if os.path.isfile(os.path.join(_SCHEMA_ROOT, name, "workflow.json")))
        assert found == sorted(_BUNDLE_NAMES)
