#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The pipeline `mode` must survive EVERY hop from the template to the container.

The chain is vamsExecute -> openPipeline -> (Step Functions) -> constructPipeline -> container argv.
Each of those lambdas enumerates the fields it forwards, so a new field is silently DROPPED by any hop
that was not updated — which is exactly what happened when this was first wired: the rendered config
carried mode 'evaluate', vamsExecute and constructPipeline both handled it, but openPipeline in the
middle passed a fixed field list and the container ran a 100-minute TRAINING job instead.

These tests pin each hop individually so the next added field cannot repeat that.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# openPipeline reads these at import time, so they must exist before it is imported.
os.environ.setdefault("STATE_MACHINE_ARN", "arn:aws:states:us-west-2:1:stateMachine:test")
os.environ.setdefault("ORCHESTRATION_BUS_NAME", "test-bus")
os.environ.setdefault("STATE_MACHINE_LOG_GROUP_NAME", "test-lg")
os.environ.setdefault("STATE_MACHINE_LOG_GROUP_ARN", "arn:aws:logs:us-west-2:1:log-group:test-lg")


@pytest.mark.unit
class TestConstructPipelineMode:
    def _run(self, event):
        import constructPipeline
        return constructPipeline.lambda_handler(event, None)

    def test_evaluate_mode_reaches_the_container_argv(self):
        result = self._run({"inputS3AssetPath": "s3://b/a/", "mode": "evaluate"})
        definition = json.loads(result["definition"][2])
        assert definition["mode"] == "evaluate"

    def test_evaluate_mode_names_the_job_so_it_is_identifiable(self):
        # The job name is the cheapest way to tell a misconfigured run from a correct one BEFORE it
        # leaves RUNNABLE and starts costing GPU time.
        result = self._run({"inputS3AssetPath": "s3://b/a/", "mode": "evaluate"})
        assert result["jobName"].startswith("gr00t-eval-")

    def test_finetune_is_the_default(self):
        # An older caller that sends no mode must keep training, not silently switch behaviour.
        result = self._run({"inputS3AssetPath": "s3://b/a/"})
        definition = json.loads(result["definition"][2])
        assert definition["mode"] == "finetune"
        assert result["jobName"].startswith("gr00t-finetune-")

    @pytest.mark.parametrize("raw", ["EVALUATE", " evaluate ", "Evaluate"])
    def test_mode_is_normalized(self, raw):
        result = self._run({"inputS3AssetPath": "s3://b/a/", "mode": raw})
        assert json.loads(result["definition"][2])["mode"] == "evaluate"


@pytest.mark.unit
class TestOpenPipelineMode:
    """openPipeline is the hop that dropped the field. It builds the Step Functions input from an
    explicit field list, so it is tested against that input rather than through a full invocation."""

    # openPipeline reads this key with [] rather than .get(), so a minimal event must carry it.
    BASE = {"inputS3AssetPath": "s3://b/a/", "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/x/"}

    def _sfn_input(self, event, monkeypatch):
        import openPipeline
        event = {**self.BASE, **event}
        captured = {}

        class _Sfn:
            def start_execution(self, **kwargs):
                captured["input"] = json.loads(kwargs["input"])
                captured["name"] = kwargs.get("name", "")
                return {"executionArn": "arn:aws:states:::execution:x:y"}

        monkeypatch.setattr(openPipeline, "sfn", _Sfn())
        monkeypatch.setattr(openPipeline, "STATE_MACHINE_ARN", "arn:aws:states:::stateMachine:x")
        # Orchestration-event emission is incidental to this assertion.
        monkeypatch.setattr(openPipeline, "emit_orchestration_event", lambda *a, **k: None,
                            raising=False)
        openPipeline.lambda_handler(event, None)
        return captured

    def test_forwards_evaluate_mode_to_the_state_machine(self, monkeypatch):
        captured = self._sfn_input(
            {"mode": "evaluate"}, monkeypatch)
        assert captured["input"]["mode"] == "evaluate"

    def test_names_the_execution_for_the_mode(self, monkeypatch):
        captured = self._sfn_input(
            {"mode": "evaluate"}, monkeypatch)
        assert captured["input"]["jobName"].startswith("gr00t-eval-")

    def test_defaults_to_finetune(self, monkeypatch):
        captured = self._sfn_input({}, monkeypatch)
        assert captured["input"]["mode"] == "finetune"
        assert captured["input"]["jobName"].startswith("gr00t-finetune-")
