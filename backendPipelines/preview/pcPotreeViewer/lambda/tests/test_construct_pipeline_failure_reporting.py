#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""``constructPipeline`` runs inside the pipeline's own state machine, whose ConstructPipelineTask
carries NO catch and is followed by a choice on ``$.currentStageType``.

Two consequences drive these tests. A ``{"error": ...}`` return has no ``currentStageType``, so the
choice fails with ``States.Runtime`` -- which ``States.ALL`` cannot catch -- and the execution ends
without ever reaching ``pipelineEnd``, the only state that reports the workflow's task token. The
VAMS workflow task then waits out the pipeline's full ``taskTimeout`` (14400 seconds in
``vamsSchema/pipeline.json``). The handler therefore has to report the token itself and then fail.

The trigger is not exotic: ``openPipeline`` lowercases the extension before its allowed-extension
check and the execute API matches ``inputFileFilters`` case-insensitively, so an uppercase ``.E57``
is accepted everywhere upstream and reached a case-sensitive dispatch here."""

import os
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

for k, v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
}.items():
    os.environ.setdefault(k, v)


def _load():
    if "constructPipeline" in sys.modules:
        return importlib.reload(sys.modules["constructPipeline"])
    return importlib.import_module("constructPipeline")


def _event(file_path, task_token="tok-123"):
    return {
        "jobName": "PipelineJob_x",
        "inputS3AssetFilePath": file_path,
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux-bkt/dbM/xidM/scan.e57/preview/PotreeViewer",
        "inputMetadataS3Location": "s3://abkt/.../metadata.json",
        "inputConfigurationS3Location": "s3://abkt/.../config.json",
        "externalSfnTaskToken": task_token,
    }


@pytest.mark.unit
class TestExtensionDispatch:
    @pytest.mark.parametrize("file_path,expected_stage", [
        ("s3://abkt/xidM/scan.e57", "PDAL"),
        ("s3://abkt/xidM/scan.ply", "PDAL"),
        ("s3://abkt/xidM/scan.las", "POTREE"),
        ("s3://abkt/xidM/scan.laz", "POTREE"),
    ])
    def test_lowercase_extensions_dispatch_unchanged(self, file_path, expected_stage):
        """No-regression for the extensions that already worked."""
        mod = _load()
        out = mod.lambda_handler(_event(file_path), MagicMock())
        assert out["currentStageType"] == expected_stage

    @pytest.mark.parametrize("file_path,expected_stage", [
        ("s3://abkt/xidM/SCAN.E57", "PDAL"),
        ("s3://abkt/xidM/Scan.Ply", "PDAL"),
        ("s3://abkt/xidM/SCAN.LAS", "POTREE"),
        ("s3://abkt/xidM/SCAN.LAZ", "POTREE"),
    ])
    def test_uppercase_extensions_dispatch(self, file_path, expected_stage):
        """Against the unfixed handler every one of these falls through to the error return, which
        has no ``currentStageType`` at all -- so this assertion raises KeyError there."""
        mod = _load()
        out = mod.lambda_handler(_event(file_path), MagicMock())
        assert out["currentStageType"] == expected_stage
        assert out["status"] == "STARTING"


@pytest.mark.unit
class TestFailureReporting:
    def test_unsupported_type_fails_the_task_token_and_raises(self):
        """Unfixed, this returns a dict with no callback and no exception: the state machine then
        fails uncatchably in the following choice and the workflow task waits out its taskTimeout."""
        mod = _load()
        send_failure = MagicMock()
        with patch.object(mod.sfn, "send_task_failure", send_failure):
            with pytest.raises(ValueError):
                mod.lambda_handler(_event("s3://abkt/xidM/notes.txt"), MagicMock())
        assert send_failure.call_args.kwargs["taskToken"] == "tok-123"
        assert "Unsupported file type" in send_failure.call_args.kwargs["cause"]

    def test_no_token_still_raises_and_issues_no_callback(self):
        """A direct invoke carries no token; the handler must not call into Step Functions."""
        mod = _load()
        send_failure = MagicMock()
        with patch.object(mod.sfn, "send_task_failure", send_failure):
            with pytest.raises(ValueError):
                mod.lambda_handler(_event("s3://abkt/xidM/notes.txt", task_token=""), MagicMock())
        send_failure.assert_not_called()

    def test_malformed_event_fails_the_task_token(self):
        """A missing ``inputS3AssetFilePath`` raised KeyError with no callback before this fix, which
        stranded the task the same way -- the task has no catch, so an unhandled Lambda error is not
        reported to the workflow either."""
        mod = _load()
        event = _event("s3://abkt/xidM/scan.e57")
        event.pop("inputS3AssetFilePath")
        send_failure = MagicMock()
        with patch.object(mod.sfn, "send_task_failure", send_failure):
            with pytest.raises(KeyError):
                mod.lambda_handler(event, MagicMock())
        send_failure.assert_called_once()
        assert send_failure.call_args.kwargs["taskToken"] == "tok-123"

    def test_callback_failure_does_not_mask_the_original_error(self):
        """A token that already timed out makes SendTaskFailure raise; the original cause must still
        reach CloudWatch and still fail the task."""
        mod = _load()
        with patch.object(mod.sfn, "send_task_failure",
                          MagicMock(side_effect=Exception("TaskTimedOut"))):
            with pytest.raises(ValueError):
                mod.lambda_handler(_event("s3://abkt/xidM/notes.txt"), MagicMock())
