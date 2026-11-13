#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""What openPipeline logs when it aborts the calling workflow.

The abort line names the action only, as every other pipeline's openPipeline does; the callback
value still goes to send_task_failure.
"""

import importlib
import os
import sys
import types
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

for k, v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:ImageTo3dPipeline",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".png,.jpg,.jpeg",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    # Static test credentials keep the module import offline (boto3.client() resolves credentials
    # eagerly); these tests reach no AWS service.
    "AWS_ACCESS_KEY_ID": "testing",
    # pragma: allowlist nextline secret
    "AWS_SECRET_ACCESS_KEY": "testing",  # nosec B105 - dummy value for offline client construction
    "AWS_SESSION_TOKEN": "testing",  # nosec B105 - dummy value for offline client construction
    "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
    "AWS_CONFIG_FILE": os.devnull,
    "AWS_EC2_METADATA_DISABLED": "true",
}.items():
    os.environ.setdefault(k, v)

for _profile_var in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
    os.environ.pop(_profile_var, None)

_CALLBACK_VALUE = "CALLBACK-VALUE-ABORT-5c2e"  # nosec B105 - test-only marker


def _load():
    if "openPipeline" in sys.modules:
        return importlib.reload(sys.modules["openPipeline"])
    return importlib.import_module("openPipeline")


@pytest.mark.unit
def test_abort_log_line_names_the_action_without_the_callback_value():
    mod = _load()
    send_failure = MagicMock()
    with patch.object(mod, "logger") as log, patch.object(mod.sfn, "send_task_failure", send_failure):
        mod.abort_external_workflow("input check failed", _CALLBACK_VALUE)

    logged = " ".join(str(call) for call in log.mock_calls)
    assert "Aborting external task" in logged
    assert _CALLBACK_VALUE not in logged
    send_failure.assert_called_once()
    assert send_failure.call_args.kwargs["taskToken"] == _CALLBACK_VALUE


@pytest.mark.unit
def test_abort_without_a_callback_value_logs_and_sends_nothing():
    mod = _load()
    send_failure = MagicMock()
    with patch.object(mod, "logger") as log, patch.object(mod.sfn, "send_task_failure", send_failure):
        mod.abort_external_workflow("input check failed", "")

    assert not log.error.called
    assert not send_failure.called
