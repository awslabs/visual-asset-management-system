#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""openPipeline must fail the VAMS workflow's callback token on every route that can fail.

openPipeline is the FIRST state of this pipeline's internal state machine and that state carries no
`Catch` — the error handler is wired to the batch task only. The workflow task that invoked the
pipeline waits on a `waitForCallback` token with an 8-hour taskTimeout, and the container never
starts, so nothing downstream can report on that token. Every rejection this lambda raises is
therefore an 8-hour stall whose diagnostic ("No policy file found for evaluation. Provide one of:
'checkpointPath' ...") reaches nobody.

The reject-before-the-container-runs paths are ordinary operator input, not exotic failures: an
unrecognized mode, an `rlLibrary` the container has no Isaac Lab script for, a `checkpointPath` or
`customEnvironmentPath` that cannot be resolved to an asset root, and an evaluation run with no
discoverable policy file.

Both halves of the fix are required and each is inert alone — the handler call, and the
`states:SendTaskFailure` grant on THIS function in the CDK builder. See `backendPipelines/CLAUDE.md`
"Reporting Failure on a Task-Token Pipeline".
"""

import json
import os
import re
import sys
import types
import importlib
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for _k, _v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1"}.items():
    os.environ.setdefault(_k, _v)

TOKEN = "external-task-token-123"


def _repo_root():
    """Walk up to the repo root rather than counting `..` segments — pipeline directories sit at
    differing depths, and a miscounted relative path fails as a missing file (which reads like a
    broken test) instead of as the assertion these tests are making."""
    path = _LAMBDA_DIR
    while path != os.path.dirname(path):
        if os.path.isdir(os.path.join(path, "infra")) and os.path.isdir(
                os.path.join(path, "backendPipelines")):
            return path
        path = os.path.dirname(path)
    raise RuntimeError("repo root not found from " + _LAMBDA_DIR)


_BUILDER = os.path.join(
    _repo_root(), "infra", "lib", "nestedStacks", "pipelines", "simulation", "isaacLabTraining",
    "lambdaBuilder", "isaacLabTrainingFunctions.ts")


def _load():
    if "openPipeline" in sys.modules:
        return importlib.reload(sys.modules["openPipeline"])
    return importlib.import_module("openPipeline")


def _empty_s3():
    """An s3 client whose config load yields nothing and whose listings are empty."""
    s3 = MagicMock()
    s3.get_object.side_effect = Exception("no file config")
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": []}]
    s3.get_paginator.return_value = paginator
    return s3


def _event(**overrides):
    event = {
        "jobName": "isaaclab-training-abcd1234",
        "bucketAsset": "asset-bucket",
        "inputAssetLocationKey": "xid130a6/",
        "inputS3AssetFilePath": "s3://asset-bucket/xid130a6/scene.usd",
        "outputS3AssetFilesPath": "s3://asset-bucket/pipelines/p1/JOB/output/E1/files/",
        "externalSfnTaskToken": TOKEN,
    }
    event.update(overrides)
    return event


def _invoke(event, sfn=None, s3=None):
    """Invoke the handler with stubbed clients, returning (module, sfn stub, raised exception)."""
    mod = _load()
    sfn = sfn if sfn is not None else MagicMock()
    with patch.object(mod, "s3_client", s3 if s3 is not None else _empty_s3()), \
            patch.object(mod, "sfn_client", sfn):
        with pytest.raises(Exception) as excinfo:
            mod.lambda_handler(event, MagicMock())
    return mod, sfn, excinfo.value


# Each entry is a rejection an operator can trigger from the execute form. The pre-invoke class is
# the common one on this pipeline precisely because it costs nothing to hit.
REJECTIONS = {
    "unrecognized_mode": _event(trainingConfig={"mode": "sideways"}),
    "unsupported_rl_library": _event(
        trainingConfig={"mode": "train", "task": "Isaac-Cartpole-Direct-v0", "rlLibrary": "sb3"}),
    "checkpoint_path_without_asset_root": _event(
        bucketAsset="", trainingConfig={"mode": "evaluate", "checkpointPath": "ckpt/model_300.pt"}),
    "custom_environment_without_asset_root": _event(
        inputAssetLocationKey="",
        trainingConfig={"mode": "train", "customEnvironmentPath": "environments/env.tar.gz"}),
    "evaluation_with_no_discoverable_policy": _event(trainingConfig={"mode": "evaluate"}),
    # An operator-supplied S3 URI reaches the container as an object it downloads and then executes
    # (pip runs the environment package's setup code; torch deserializes the policy), so a URI
    # outside the executing asset's own bucket is rejected here rather than deep inside the job.
    "custom_environment_uri_outside_asset_bucket": _event(
        trainingConfig={"mode": "train", "task": "Isaac-Cartpole-Direct-v0",
                        "customEnvironmentS3Uri": "s3://other-asset-bucket/env.tar.gz"}),
    "custom_environment_uri_without_asset_bucket": _event(
        bucketAsset="",
        trainingConfig={"mode": "train", "task": "Isaac-Cartpole-Direct-v0",
                        "customEnvironmentS3Uri": "s3://asset-bucket/env.tar.gz"}),
    "custom_environment_uri_with_a_non_s3_scheme": _event(
        trainingConfig={"mode": "train", "task": "Isaac-Cartpole-Direct-v0",
                        "customEnvironmentS3Uri": "https://example.invalid/env.tar.gz"}),
    "policy_uri_outside_asset_bucket": _event(
        trainingConfig={"mode": "evaluate", "policyS3Uri": "s3://other-asset-bucket/model.pt"}),
    "policy_path_outside_asset_bucket": _event(
        trainingConfig={"mode": "evaluate", "policyPath": "s3://other-asset-bucket/model.pt"}),
}


@pytest.mark.unit
class TestEveryRejectionReportsTheToken:
    @pytest.mark.parametrize("case", sorted(REJECTIONS))
    def test_the_rejection_fails_the_external_token(self, case):
        _, sfn, _ = _invoke(REJECTIONS[case])
        assert sfn.send_task_failure.call_count == 1
        kwargs = sfn.send_task_failure.call_args.kwargs
        assert kwargs["taskToken"] == TOKEN
        assert kwargs["error"] == "IsaacLabPipelineError"
        # 256 is the SendTaskFailure cause limit and matches the peer implementations.
        assert len(kwargs["cause"]) <= 256

    @pytest.mark.parametrize("case", sorted(REJECTIONS))
    def test_the_rejection_still_propagates(self, case):
        # The state has no Catch, so the raise is what fails the internal execution. Reporting the
        # token must not swallow it, or the internal execution would read as succeeded.
        _, _, error = _invoke(REJECTIONS[case])
        assert isinstance(error, Exception)

    def test_the_cause_carries_the_diagnostic_the_operator_needs(self):
        _, sfn, _ = _invoke(REJECTIONS["evaluation_with_no_discoverable_policy"])
        assert "No policy file found for evaluation" in \
            sfn.send_task_failure.call_args.kwargs["cause"]


@pytest.mark.unit
class TestCallbackDiscipline:
    def test_a_direct_invoke_carrying_no_token_skips_the_callback(self):
        event = _event(trainingConfig={"mode": "sideways"})
        del event["externalSfnTaskToken"]
        _, sfn, _ = _invoke(event)
        sfn.send_task_failure.assert_not_called()

    def test_a_blank_token_skips_the_callback(self):
        _, sfn, _ = _invoke(_event(externalSfnTaskToken="", trainingConfig={"mode": "sideways"}))
        sfn.send_task_failure.assert_not_called()

    def test_a_failing_callback_does_not_mask_the_original_error(self):
        # AccessDeniedException here (the missing-grant case) must not replace the error worth
        # reading in CloudWatch.
        sfn = MagicMock()
        sfn.send_task_failure.side_effect = RuntimeError("AccessDeniedException")
        _, _, error = _invoke(_event(trainingConfig={"mode": "sideways"}), sfn=sfn)
        assert "Invalid mode: sideways" in str(error)

    def test_the_original_error_is_logged_before_the_callback(self):
        # The callback can fail; the log line is what survives either way, so it goes first.
        mod = _load()
        order = []
        sfn = MagicMock()
        sfn.send_task_failure.side_effect = lambda **kw: order.append("callback")
        with patch.object(mod, "s3_client", _empty_s3()), \
                patch.object(mod, "sfn_client", sfn), \
                patch.object(mod.logger, "exception",
                             MagicMock(side_effect=lambda *a, **k: order.append("log"))):
            with pytest.raises(Exception):
                mod.lambda_handler(_event(trainingConfig={"mode": "sideways"}), MagicMock())
        assert order == ["log", "callback"]

    def test_a_successful_build_reports_nothing(self):
        mod = _load()
        sfn = MagicMock()
        with patch.object(mod, "s3_client", _empty_s3()), patch.object(mod, "sfn_client", sfn):
            out = mod.lambda_handler(
                _event(trainingConfig={"mode": "train", "task": "Isaac-Cartpole-Direct-v0"}),
                MagicMock())
        assert out["status"] == "STARTING"
        sfn.send_task_failure.assert_not_called()


@pytest.mark.unit
class TestOperatorSuppliedUrisAreScopedToTheAssetBucket:
    """The bucket scope, from the other side.

    The rejections in `REJECTIONS` prove a foreign bucket is refused; on their own they are equally
    satisfied by a guard that refuses every URI, which would make the feature unusable rather than
    safe. These assert the legitimate spellings still resolve, and that the message names the bucket
    so an operator can act on it.
    """

    def _build(self, event, s3=None):
        mod = _load()
        with patch.object(mod, "s3_client", s3 if s3 is not None else _empty_s3()), \
                patch.object(mod, "sfn_client", MagicMock()):
            return mod, mod.build_job_config_payload(event)

    def test_a_same_bucket_custom_environment_uri_is_passed_through(self):
        uri = "s3://asset-bucket/xid130a6/environments/env.tar.gz"
        _, payload = self._build(_event(
            trainingConfig={"mode": "train", "task": "Isaac-Cartpole-Direct-v0",
                            "customEnvironmentS3Uri": uri}))
        assert json.loads(payload["definition"])["customEnvironmentS3Uri"] == uri

    def test_a_relative_custom_environment_path_still_resolves(self):
        _, payload = self._build(_event(
            trainingConfig={"mode": "train", "task": "Isaac-Cartpole-Direct-v0",
                            "customEnvironmentPath": "environments/env.tar.gz"}))
        assert json.loads(payload["definition"])["customEnvironmentS3Uri"] == \
            "s3://asset-bucket/xid130a6/environments/env.tar.gz"

    def test_no_custom_environment_stays_empty_rather_than_being_rejected(self):
        # The guard must not fire for a run that names no package at all.
        _, payload = self._build(_event(
            trainingConfig={"mode": "train", "task": "Isaac-Cartpole-Direct-v0"}))
        assert json.loads(payload["definition"])["customEnvironmentS3Uri"] == ""

    def test_a_same_bucket_policy_uri_is_passed_through(self):
        uri = "s3://asset-bucket/xid130a6/checkpoints/model_300.pt"
        _, payload = self._build(_event(
            trainingConfig={"mode": "evaluate", "policyS3Uri": uri}))
        assert json.loads(payload["definition"])["trainingConfig"]["policyS3Uri"] == uri

    def test_a_relative_checkpoint_path_still_resolves(self):
        _, payload = self._build(_event(
            trainingConfig={"mode": "evaluate", "checkpointPath": "checkpoints/model_300.pt"}))
        assert json.loads(payload["definition"])["trainingConfig"]["policyS3Uri"] == \
            "s3://asset-bucket/xid130a6/checkpoints/model_300.pt"

    def test_an_auto_discovered_policy_still_resolves(self):
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("no file config")
        paginator = MagicMock()
        paginator.paginate.return_value = [{"Contents": [
            {"Key": "xid130a6/checkpoints/model_499.pt", "LastModified": None},
        ]}]
        s3.get_paginator.return_value = paginator
        _, payload = self._build(_event(trainingConfig={"mode": "evaluate"}), s3=s3)
        assert json.loads(payload["definition"])["trainingConfig"]["policyS3Uri"] == \
            "s3://asset-bucket/xid130a6/checkpoints/model_499.pt"

    @pytest.mark.parametrize("case,field", [
        ("custom_environment_uri_outside_asset_bucket", "customEnvironmentS3Uri"),
        ("policy_uri_outside_asset_bucket", "policyS3Uri"),
    ])
    def test_the_rejection_names_the_field_and_both_buckets(self, case, field):
        _, sfn, error = _invoke(REJECTIONS[case])
        cause = sfn.send_task_failure.call_args.kwargs["cause"]
        assert field in cause
        assert "asset-bucket" in cause and "other-asset-bucket" in cause
        assert field in str(error)


@pytest.mark.unit
class TestTheBuilderGrantsTheCallback:
    """Without the grant the call raises AccessDeniedException, the handler logs it, and the task
    hangs exactly as before — the only difference is one log line.

    The grant must be attributed to the openPipeline function. A file-level search for
    `SendTaskFailure` passes on the strength of the handleError and vamsExecute grants in this same
    builder, which is why the receiver is resolved here rather than the file scanned.
    """

    def _states_failure_grant_receivers(self):
        source = open(_BUILDER, encoding="utf-8").read()
        calls = list(re.finditer(r"this\.(\w+)\.addToRolePolicy\(", source))
        receivers = set()
        for index, call in enumerate(calls):
            end = calls[index + 1].start() if index + 1 < len(calls) else len(source)
            if "states:SendTaskFailure" in source[call.end():end]:
                receivers.add(call.group(1))
        return receivers

    def test_the_open_pipeline_function_is_granted_send_task_failure(self):
        assert "openPipelineFunction" in self._states_failure_grant_receivers()

    def test_the_receiver_resolution_finds_the_grants_that_do_exist(self):
        # Positive control for the helper: a resolution bug that found nothing would make the
        # assertion above fail for the wrong reason.
        receivers = self._states_failure_grant_receivers()
        assert {"handleErrorFunction", "vamsExecuteFunction"} <= receivers
