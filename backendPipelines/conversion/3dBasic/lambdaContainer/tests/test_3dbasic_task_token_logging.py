#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The handler logs identifiers, never the event, and nothing at all before it has checked for a task
token (#509).

This pipeline is synchronous and rejects a request carrying ``TaskToken``. A pipeline registered with
a callback by mistake still receives one, and this container's logger (``common/logger.py``, outside
the pipeline logger byte-identity set) redacts only ``authorization`` -- so the handler is what has to
keep the token out of Amazon CloudWatch. Every argument handed to the logger is scanned for the token
and for a sentinel planted elsewhere in the event.
"""

import json
import os
import sys
import types
import importlib.util
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

# Stub common.logger + trimesh so the module imports without powertools / the mesh library.
if "common" not in sys.modules:
    _common_pkg = types.ModuleType("common")
    _common_logger = types.ModuleType("common.logger")
    _common_logger.safeLogger = lambda **kw: MagicMock()
    _common_pkg.logger = _common_logger
    sys.modules["common"] = _common_pkg
    sys.modules["common.logger"] = _common_logger
if "trimesh" not in sys.modules:
    sys.modules["trimesh"] = MagicMock()

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")

TOKEN = "AQCEAAAAKgAAAAMAAAAAAAAAAexampleTaskTokenValue" * 8
SENTINEL = "sentinel-request-context-value"


def _load():
    spec = importlib.util.spec_from_file_location(
        "trimesh_pipeline_lambda_token_logging", os.path.join(_LAMBDA_DIR, "lambda.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
        spec.loader.exec_module(module)
    return module


def _logged_text(logger):
    """Every positional and keyword argument of every call made on the logger, as one string."""
    parts = []
    for call in logger.mock_calls:
        _, args, kwargs = call
        parts.extend(repr(a) for a in args)
        parts.extend(repr(v) for v in kwargs.values())
    return "\n".join(parts)


def _body(**extra):
    body = {
        "inputManifestS3Location": "s3://abkt/pipelines/c/JOB/exec/E1/inputs/manifest.json",
        "executingRequestContext": {"marker": SENTINEL},
    }
    body.update(extra)
    return body


@pytest.mark.unit
class TestTaskTokenIsNeverLogged:
    @pytest.mark.parametrize("as_string", [True, False])
    def test_a_request_with_a_task_token_is_rejected_and_the_token_is_not_logged(self, as_string):
        mod = _load()
        logger = MagicMock()
        body = _body(TaskToken=TOKEN)
        with patch.object(mod, "logger", logger), patch.object(mod, "s3_client", MagicMock()) as s3:
            with pytest.raises(Exception, match="TaskToken found in pipeline input"):
                mod.lambda_handler({"body": json.dumps(body) if as_string else body}, MagicMock())
        assert TOKEN not in _logged_text(logger)
        assert SENTINEL not in _logged_text(logger)
        # The rejection comes before any work: nothing is read from Amazon S3.
        s3.get_object.assert_not_called()

    def test_the_rejection_message_does_not_carry_the_token(self):
        mod = _load()
        with patch.object(mod, "logger", MagicMock()):
            with pytest.raises(Exception) as raised:
                mod.lambda_handler({"body": _body(TaskToken=TOKEN)}, MagicMock())
        assert TOKEN not in str(raised.value)


@pytest.mark.unit
class TestTheEventIsNeverLogged:
    def test_a_normal_run_logs_identifiers_only(self):
        mod = _load()
        logger = MagicMock()
        body = _body()
        with patch.object(mod, "logger", logger), \
                patch.object(mod, "fetch_input_configuration", MagicMock(return_value={"outputType": ".glb"})), \
                patch.object(mod, "resolve_inputs_from_manifest",
                             MagicMock(return_value=("s3://abkt/x/m.stl", "s3://obkt/f/", ""))), \
                patch.object(mod, "convert_input_output", MagicMock()):
            response = mod.lambda_handler({"body": body, "headers": {"marker": SENTINEL}}, MagicMock())
        assert response["statusCode"] == 200
        text = _logged_text(logger)
        # In-band control: the identifiers ARE logged, so the absence below is not a silent logger.
        assert body["inputManifestS3Location"] in text
        assert SENTINEL not in text

    @pytest.mark.parametrize("event", [{}, {"body": ""}, {"body": None}, "not-a-dict"])
    def test_a_missing_body_is_rejected(self, event):
        mod = _load()
        with patch.object(mod, "logger", MagicMock()):
            with pytest.raises(ValueError, match="Request body is required"):
                mod.lambda_handler(event, MagicMock())

    @pytest.mark.parametrize("body", ["[1, 2]", "\"text\"", [1, 2]])
    def test_a_body_that_is_not_an_object_is_rejected(self, body):
        mod = _load()
        with patch.object(mod, "logger", MagicMock()):
            with pytest.raises(ValueError, match="must be a JSON object"):
                mod.lambda_handler({"body": body}, MagicMock())
