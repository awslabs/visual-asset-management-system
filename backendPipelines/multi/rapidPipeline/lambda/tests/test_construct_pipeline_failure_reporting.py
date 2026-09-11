#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""rapidPipeline ``constructPipeline`` reports every failure route against the workflow's task token.

The twin of ``backendPipelines/multi/modelOps/lambda/tests/test_construct_pipeline_failure_reporting.py``.
Finding `S4-PIPELINES-007` named this file as a deliverable and the owner's ruling was to fix BOTH
pipelines under one finding; the code fix landed in both, but only modelOps got a suite. So the
rapidPipeline half of the ruling had no test at all — its behaviour was covered by
``test_open_pipeline_function_error.py`` only in name, which exercises ``openPipeline``, a different
lambda with a deliberately opposite contract (it does NOT wrap its callback).

What the tests pin, both consequences of this state carrying no ``.addCatch`` in the pipeline's own
state machine:

*   **A failure route that hands a value back is not a failure.** The ECS task resolves its container
    command from this lambda's output, so a returned value is carried forward and run; the job reads as
    STARTING and nothing ever reports the token, leaving the VAMS workflow RUNNING for its full
    ``taskTimeout``.
*   **Raising alone is not sufficient either.** With no catch, a raise ends the sub-state-machine
    before ``pipelineEnd`` — the only other holder of the token — can run. The handler must report the
    token itself.

The reachable failure route is ``manifestHelper.fetch_input_configuration``, which RAISES
``InputConfigurationError`` when the rp_config body was fetched but is not a JSON object, and returns a
falsy ``{}`` when it could not be fetched at all. Only the first ends the run, so the tests treat the
two separately rather than assuming both fail.

Assertions are on what the stubbed Step Functions client RECORDED, never on the return value alone: a
stub standing in for a reporter returns success whether or not it was ever called.
"""

import os
import sys
import json
import types
import importlib.util
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

# Stub customLogging so the lambda imports without aws_lambda_powertools.
if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for _k, _v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1"}.items():
    os.environ.setdefault(_k, _v)

TASK_TOKEN = "tok-rapid-123"

_CONSTRUCT_PIPELINE = os.path.join(_LAMBDA_DIR, "constructPipeline.py")
# Deliberately NOT the modelOps suite's module name, and deliberately not a bare import.
_MODULE_NAME = "_construct_pipeline_for_rapidpipeline_failure_reporting"


def _load():
    """This pipeline's own ``constructPipeline``, loaded by path under a name no other suite uses.

    Twelve pipelines ship a top-level ``constructPipeline.py`` and each suite puts its own lambda
    directory on ``sys.path``, so a bare-name import binds to whichever copy reached ``sys.modules``
    first. That is not hypothetical here: the two ``test_pipeline_end_token_routes.py`` files were
    byte-identical, so one suite silently asserted against the OTHER pipeline's file and a one-sided
    edit was invisible. ``sys.path`` is NOT reordered — that mutates the shared ``sys.modules`` slot
    and breaks the sibling suite — and the ``__file__`` assertion below is in-band so a wrong bind
    fails loudly instead of passing against the wrong pipeline.
    """
    module = sys.modules.get(_MODULE_NAME)
    if module is None:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, _CONSTRUCT_PIPELINE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        spec.loader.exec_module(module)
    assert module.__file__ == _CONSTRUCT_PIPELINE, \
        f"loaded {module.__file__}, expected this pipeline's own copy at {_CONSTRUCT_PIPELINE}"
    return module


def _event(task_token=TASK_TOKEN):
    return {
        "jobName": "PipelineJob_rapid",
        "inputS3AssetFilePath": "s3://abkt/xidR/sub/model.glb",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/RJOB/output/E1/files/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidR/model.glb/rapidPipeline",
        "inputMetadataS3Location": "s3://abkt/xidR/metadata.json",
        "inputConfigurationS3Location": "s3://abkt/xidR/config.json",
        "externalSfnTaskToken": task_token,
    }


def _s3_returning(body):
    """An S3 stub whose configuration object has the given raw body."""
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: body.encode("utf-8"))}
    return s3


def _run(s3, event):
    """Run the handler with both AWS clients stubbed; return (raised or None, output, sfn stub).

    Note the client attribute is ``sfn_client`` here, not modelOps' ``sfn``. Patching the wrong name
    would leave the real boto3 client in place and the callback assertions would be measuring nothing,
    so this is the one difference between the twins that matters.
    """
    mod = _load()
    sfn = MagicMock()
    raised, out = None, None
    with patch.object(mod, "s3", s3), patch.object(mod, "sfn_client", sfn):
        try:
            out = mod.lambda_handler(event, MagicMock())
        except Exception as e:  # noqa: BLE001 - the assertion is about what is raised
            raised = e
    return raised, out, sfn


@pytest.mark.unit
class TestTheClientNameIsTheOneTheHandlerUses:
    """A harness check, and not a formality: if this module ever renames ``sfn_client``, every
    callback assertion below would patch a name that does not exist. ``patch.object`` raises on a
    missing attribute, so that would fail loudly — but only if the attribute is read at least once.
    """

    def test_the_handler_module_exposes_sfn_client_and_s3(self):
        mod = _load()
        assert hasattr(mod, "sfn_client"), "constructPipeline no longer exposes sfn_client"
        assert hasattr(mod, "s3"), "constructPipeline no longer exposes s3"


@pytest.mark.unit
class TestAMalformedConfigurationFailsTheTask:
    """``fetch_input_configuration`` raises ``InputConfigurationError`` when the body WAS fetched but
    is not a JSON object. That is the route reachable from ordinary bad input rather than an outage."""

    def test_no_command_value_is_handed_back(self):
        raised, out, _ = _run(_s3_returning("not json at all"), _event())
        assert raised is not None, "a malformed input configuration returned normally"
        assert out is None, \
            f"a value was handed back for the ECS task to run as its command: {out!r}"

    def test_the_task_token_is_reported_failed_exactly_once(self):
        _raised, _out, sfn = _run(_s3_returning("not json at all"), _event())
        assert sfn.send_task_failure.call_count == 1
        kwargs = sfn.send_task_failure.call_args.kwargs
        assert kwargs["taskToken"] == TASK_TOKEN
        # The cause carries the reason, and stays inside the 256-character Step Functions limit this
        # handler truncates to.
        assert kwargs["cause"]
        assert len(kwargs["cause"]) <= 256
        assert sfn.send_task_success.call_count == 0

    def test_a_json_array_body_also_reports_the_token(self):
        # An array is valid JSON but not an object, which is the second half of the raising condition.
        _raised, _out, sfn = _run(_s3_returning(json.dumps([{"outputType": ".glb"}])), _event())
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == TASK_TOKEN

    def test_a_direct_invoke_with_no_token_reports_nothing(self):
        """A direct/local invoke carries no token. The failure must still surface, but the callback
        must not be attempted — there is nothing waiting on it, and calling with an empty token is an
        error in its own right."""
        raised, _out, sfn = _run(_s3_returning("not json at all"), _event(task_token=""))
        assert raised is not None
        sfn.send_task_failure.assert_not_called()


@pytest.mark.unit
class TestTheOriginalErrorStillReachesCloudWatch:

    def test_a_denied_callback_does_not_replace_the_original_error(self):
        """The callback needs an IAM grant on this function. When it is missing the call raises
        AccessDenied, and that must not become the error the execution records — the original failure
        is the one that names the cause. ``abort_external_workflow`` swallows its own failure for
        exactly this reason."""
        mod = _load()
        sfn = MagicMock()
        sfn.send_task_failure.side_effect = Exception("AccessDeniedException")
        with patch.object(mod, "s3", _s3_returning("not json at all")), \
                patch.object(mod, "sfn_client", sfn):
            with pytest.raises(Exception) as excinfo:
                mod.lambda_handler(_event(), MagicMock())
        assert "AccessDenied" not in str(excinfo.value), \
            "the callback's own failure replaced the error the execution records"


@pytest.mark.unit
class TestSuccessRouteUnchanged:
    """Control for every failure test above. Without it, a change that failed EVERY execution would
    satisfy all of them — and the owner's ruling explicitly requires the success route to be provably
    unchanged."""

    def test_a_valid_configuration_emits_a_command_list_and_no_callback(self):
        raised, out, sfn = _run(_s3_returning(json.dumps({"outputType": ".glb"})), _event())
        assert raised is None, f"a valid configuration failed: {raised!r}"
        assert isinstance(out["commands"], list) and out["commands"], out
        assert out["status"] == "STARTING"
        assert out["externalSfnTaskToken"] == TASK_TOKEN
        sfn.send_task_failure.assert_not_called()
        sfn.send_task_success.assert_not_called()

    def test_an_unfetchable_configuration_is_NOT_a_failure_route(self):
        """Deliberately asserts the OPPOSITE of the modelOps twin, because the two pipelines differ
        here and assuming symmetry would encode a false contract.

        ``fetch_input_configuration`` returns a falsy ``{}`` when the object could not be fetched, and
        rapidPipeline's ``construct_rapidPipeline_definition`` applies ``or {}`` and carries on with
        its defaults — an absent configuration file is a legitimate call. So this route must SUCCEED,
        and a test that expected a callback here would be asserting a defect into existence.
        """
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("AccessDenied")
        raised, out, sfn = _run(s3, _event())
        assert raised is None, f"an unfetchable configuration ended the run: {raised!r}"
        assert isinstance(out["commands"], list) and out["commands"], out
        sfn.send_task_failure.assert_not_called()
