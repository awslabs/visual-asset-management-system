#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pytest configuration for the genAi/nvidia/cosmos/3 vamsSchema bundle tests.

The tests here read the shipped bundle files and run the REAL backend validators against them, so the
repo root's backend package is put on sys.path. They touch no AWS service and import nothing that
creates a boto3 client.
"""

import os
import sys

_BACKEND = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "..", "..", "..", "..", "backend", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")
