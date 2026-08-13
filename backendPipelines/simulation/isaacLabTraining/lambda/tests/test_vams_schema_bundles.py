#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Consistency checks on the isaacLabTraining vamsSchema bundles.

Execute-time validation reads the WORKFLOW record's assetScope, so a pipeline that operates on the
whole asset only runs when its workflow bundle declares the matching scope. Training and evaluation
share one vamsExecute lambda and one state machine, discriminated solely by ``trainingConfig.mode``
from the template config body, so the evaluation pipeline must require a template."""

import os
import json

import pytest

_SCHEMA_ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "vamsSchema"))


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

    def test_evaluation_trigger_defaults_to_the_evaluation_template(self):
        trigger = _load("evaluation", "workflow.json")["triggers"][0]
        assert trigger["defaultTemplateIds"]["GLOBAL:isaaclab-evaluation"] == \
            "isaaclab-evaluation-cartpole"

    def test_evaluation_trigger_filter_matches_the_pipeline_filter(self):
        # A trigger-launched execution is validated against the pipeline's inputFileFilters, so a
        # trigger allowing an extension the pipeline excludes can never produce a running execution.
        trigger_allow = _load("evaluation", "workflow.json")["triggers"][0]["inputFileFilters"]["allow"]
        pipeline_allow = _load("evaluation", "pipeline.json")["systemConfig"]["inputFileFilters"]["allow"]
        assert sorted(trigger_allow) == sorted(pipeline_allow)
