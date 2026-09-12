#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pytest configuration for the genAi/videoSopBom pipeline lambda tests.

Every handler in ../ reads its environment at import (module-level boto3 clients and the caps it
enforces), so the variables are set here — conftest executes before any test module is imported —
and the lambda directory is put on sys.path so the handlers import under their deployed module
names. customLogging is stubbed so the handlers import without a live aws_lambda_powertools
configuration; the redaction test loads the real logger module by path."""

import os
import sys
import types
from unittest.mock import MagicMock

LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if LAMBDA_DIR not in sys.path:
    sys.path.insert(0, LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

# The values the CDK builders set (master plan, Lambda environment variables). Applied
# unconditionally: a value exported in the shell must not change a test outcome. A test that needs
# another value patches the module attribute, or monkeypatches the variable and reloads the module,
# then restores it.
VIDEO_SOP_BOM_TEST_ENV = {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".mp4,.mov,.m4v,.webm,.mkv",
    "VIDEO_SOP_BOM_MAX_VIDEO_FILES": "4",
    "VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB": "4096",
    "VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB": "16384",
    "VIDEO_SOP_BOM_MAX_TOTAL_DURATION_MINUTES": "240",
    "VIDEO_SOP_BOM_MAX_KEY_FRAMES_CEILING": "200",
    "BEDROCK_MODEL_ID": "global.anthropic.claude-sonnet-5",
    "KMS_KEY_ARN": "",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:VideoSopBom",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/VAMSStateMachine-VideoSopBom",
    "STATE_MACHINE_LOG_GROUP_ARN":
        "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/VAMSStateMachine-VideoSopBom:*",
}
os.environ.update(VIDEO_SOP_BOM_TEST_ENV)


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")
