#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""modelOps ``constructPipeline`` reports every failure route against the workflow's task token.

The pipeline registers with ``waitForCallback: "Enabled"`` and a ``taskTimeout`` of 14400 seconds, so
the workflow's task stays RUNNING until something reports against its token. ``constructPipeline`` is
the first task in the pipeline's own state machine and carries **no** catch, and the ECS task that
follows it resolves its container command from this lambda's output (``command:
JsonPath.listAt("$.commands")``). Two consequences the tests below pin:

*   A failure route that hands a value back in ``commands`` is not a failure. It is carried forward as
    the container command, the run reads as STARTING, and nothing ever reports the token.
*   Raising is not sufficient on its own either. The task has no catch, so the sub-state-machine
    simply fails and ``pipelineEnd`` - the only other holder of the token - never runs. The handler
    must report the token itself.

Both config failure routes are covered: an unreadable configuration (``fetch_input_configuration``
returns ``{}``) and a configuration whose body is not a JSON object (it raises).

The tests assert on what the stubbed Step Functions client RECORDED, not on the return value alone,
because a stub standing in for a reporter returns success whether or not it was called.
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

# Stub customLogging so the lambdas import without aws_lambda_powertools.
if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for k, v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
}.items():
    os.environ.setdefault(k, v)

TASK_TOKEN = "tok-123"

_CONSTRUCT_PIPELINE = os.path.join(_LAMBDA_DIR, "constructPipeline.py")
_MODULE_NAME = "_construct_pipeline_for_modelops_failure_reporting"


def _load():
    """This pipeline's own ``constructPipeline``, loaded by path under a name no other suite uses.

    Twelve pipelines ship a ``constructPipeline.py`` and each suite puts its own lambda directory on
    ``sys.path``, so a bare-name import binds to whichever copy reached ``sys.modules`` first. Only
    this one reports the task token, so the assertions below are about this file specifically.
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
        "jobName": "PipelineJob_x",
        "inputS3AssetFilePath": "s3://abkt/xidM/sub/model.glb",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/model.glb/modelOps",
        "inputMetadataS3Location": "s3://abkt/.../metadata.json",
        "inputConfigurationS3Location": "s3://abkt/.../config.json",
        "externalSfnTaskToken": task_token,
    }


def _s3_returning(body):
    """An S3 stub whose configuration object has the given raw body."""
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: body.encode("utf-8"))}
    return s3


def _s3_unreadable():
    """An S3 stub whose configuration object cannot be fetched, which is the route
    ``fetch_input_configuration`` answers with a falsy ``{}``."""
    s3 = MagicMock()
    s3.get_object.side_effect = Exception("AccessDenied")
    return s3


def _run(s3, event):
    """Run the handler with both AWS clients stubbed; return (raised exception or None, output, sfn)."""
    mod = _load()
    sfn = MagicMock()
    raised = None
    out = None
    with patch.object(mod, "s3", s3), patch.object(mod, "sfn", sfn):
        try:
            out = mod.lambda_handler(event, MagicMock())
        except Exception as e:  # noqa: BLE001 - the assertion is about what is raised
            raised = e
    return raised, out, sfn


@pytest.mark.unit
class TestAnUnreadableConfigurationFailsTheTask:

    def test_no_command_value_is_handed_back(self):
        """The route this covers: the ECS task's command comes from ``$.commands``, so a failure that
        populates that field starts a container with the error as its command line instead of ending
        the run."""
        raised, out, _ = _run(_s3_unreadable(), _event())
        assert raised is not None, "an unreadable input configuration returned normally"
        assert out is None, f"a value was handed back for the ECS task to run as its command: {out!r}"

    def test_the_task_token_is_reported_failed_exactly_once(self):
        raised, _, sfn = _run(_s3_unreadable(), _event())
        assert sfn.send_task_failure.call_count == 1
        kwargs = sfn.send_task_failure.call_args.kwargs
        assert kwargs["taskToken"] == TASK_TOKEN
        # The cause carries the reason rather than a bare code, and stays inside the 256-character
        # limit the peer implementations truncate to.
        assert kwargs["cause"]
        assert len(kwargs["cause"]) <= 256
        assert sfn.send_task_success.call_count == 0

    def test_a_direct_invoke_with_no_token_reports_nothing(self):
        """A direct/local invoke carries no token. The failure must still surface, but the callback
        must not be attempted - there is nothing waiting on it."""
        raised, _, sfn = _run(_s3_unreadable(), _event(task_token=""))
        assert raised is not None
        sfn.send_task_failure.assert_not_called()


@pytest.mark.unit
class TestAMalformedConfigurationFailsTheTask:
    """``fetch_input_configuration`` raises (rather than returning ``{}``) when the body was read but
    is not a JSON object, so this is a second, independent route into the same callback."""

    def test_a_non_json_body_reports_the_token(self):
        raised, _, sfn = _run(_s3_returning("not json at all"), _event())
        assert raised is not None
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == TASK_TOKEN

    def test_a_json_array_body_reports_the_token(self):
        raised, _, sfn = _run(_s3_returning(json.dumps([{"outputType": ".glb"}])), _event())
        assert raised is not None
        assert sfn.send_task_failure.call_count == 1


@pytest.mark.unit
class TestTheOriginalErrorStillReachesCloudWatch:

    def test_a_denied_callback_does_not_replace_the_original_error(self):
        """The callback needs an IAM grant on this function. When it is missing the call raises
        AccessDenied, and that must not become the error the execution records - the original failure
        is what names the cause."""
        mod = _load()
        sfn = MagicMock()
        sfn.send_task_failure.side_effect = Exception("AccessDeniedException")
        with patch.object(mod, "s3", _s3_unreadable()), patch.object(mod, "sfn", sfn):
            with pytest.raises(Exception) as excinfo:
                mod.lambda_handler(_event(), MagicMock())
        assert "AccessDenied" not in str(excinfo.value)
        assert "configuration" in str(excinfo.value).lower()


@pytest.mark.unit
class TestSuccessRouteUnchanged:
    """Control for the failure tests: a readable configuration must still emit the command list and
    report nothing. Without it a fix that failed every execution would satisfy every test above."""

    def test_a_valid_configuration_emits_a_command_list_and_no_callback(self):
        raised, out, sfn = _run(_s3_returning(json.dumps({"outputType": ".glb"})), _event())
        assert raised is None
        assert isinstance(out["commands"], list)
        assert out["commands"][0] == "/bin/bash"
        assert out["status"] == "STARTING"
        assert out["externalSfnTaskToken"] == TASK_TOKEN
        sfn.send_task_failure.assert_not_called()
