#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pytest configuration for the SYSTEM GenAI metadata pipeline's media container tests.

The container directory goes on sys.path so `media_extractors` imports as it does inside the image. The
`customLogging` package is a per-container copy of the powertools logger; it is stubbed here so the handler
loads without powertools installed, the same way every other pipeline suite stubs it.
"""

import os
import sys
import types
from unittest.mock import MagicMock

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)

if "customLogging" not in sys.modules:
    _package = types.ModuleType("customLogging")
    _logger = types.ModuleType("customLogging.logger")
    _logger.safeLogger = lambda **kwargs: MagicMock()
    _package.logger = _logger
    sys.modules["customLogging"] = _package
    sys.modules["customLogging.logger"] = _logger

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")
    config.addinivalue_line(
        "markers", "temporary: pins one past change rather than a durable rule (root CLAUDE.md Rule 13)")
