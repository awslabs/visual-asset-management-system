#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The per-mode time limit of a run (#1669): 8 hours for training, 9 hours for evaluation.

openPipeline emits the mode's seconds as ``taskTimeoutSeconds``; the state machine's batch task reads
it (``TimeoutSecondsPath``) and executeBatchJob passes it to AWS Batch as the attempt's duration. Each
value equals the mode's vamsSchema bundle ``taskTimeout``.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from test_config_precedence import _event, _load
from test_isaaclab_physics import _evaluate, _s3, _train
from test_isaaclab_submit_log import _load_execute_batch_job

_SCHEMA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           os.pardir, "vamsSchema")


def _bundle_timeout(mode):
    with open(os.path.join(_SCHEMA_DIR, mode, "pipeline.json"), encoding="utf-8") as handle:
        return int(json.load(handle)["executionConfig"]["taskTimeout"])


def _payload(event):
    mod = _load()
    with patch.object(mod, "s3_client", _s3()):
        return mod.lambda_handler(event, MagicMock())


@pytest.mark.unit
class TestOpenPipelineEmitsTheModeLimit:
    def test_training_gets_8_hours(self):
        assert _payload(_train())["taskTimeoutSeconds"] == 28800

    def test_evaluation_gets_9_hours(self):
        assert _payload(_evaluate())["taskTimeoutSeconds"] == 32400

    @pytest.mark.parametrize("mode,builder", [("training", _train), ("evaluation", _evaluate)])
    def test_each_value_equals_its_bundle_taskTimeout(self, mode, builder):
        assert _payload(builder())["taskTimeoutSeconds"] == _bundle_timeout(mode)


@pytest.mark.unit
class TestBatchAttempt:
    def _submit(self, **extra):
        module = _load_execute_batch_job()
        event = {
            "jobName": "isaac-1",
            "definition": json.dumps({"trainingConfig": {"mode": "evaluate"}}),
            "taskToken": "t",
            "outputS3AssetFilesPath": "s3://b/a/",
            "inputS3AssetFilePath": "s3://b/a/in.usd",
        }
        event.update(extra)
        module.lambda_handler(event, None)
        return module.batch.submit_job.call_args.kwargs

    def test_the_submission_carries_the_mode_limit_as_the_attempt_duration(self):
        assert self._submit(taskTimeoutSeconds=32400)["timeout"] == {"attemptDurationSeconds": 32400}
        assert self._submit(taskTimeoutSeconds=28800)["timeout"] == {"attemptDurationSeconds": 28800}

    def test_a_submission_without_a_limit_leaves_the_job_definition_value(self):
        assert "timeout" not in self._submit()
