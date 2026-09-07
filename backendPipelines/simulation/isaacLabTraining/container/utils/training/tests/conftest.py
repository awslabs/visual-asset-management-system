#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pytest configuration for the isaacLabTraining container config tests."""

import os
import sys

_CONTAINER_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")
