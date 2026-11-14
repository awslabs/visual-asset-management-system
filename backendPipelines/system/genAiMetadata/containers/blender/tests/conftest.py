# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Make the Blender Lambda image's Python modules importable in the shared interpreter.

The container directory is the image's `${LAMBDA_TASK_ROOT}`, so it goes on `sys.path` and the
handler-side modules import as `handler`, `meshAttributes`, `formatUnits`, `containerLogger`.
`renderScene.py` is a Blender script that imports `bpy` at the top, so no test imports it: the tests read
it with `ast` and execute only the nodes they need against a recording stand-in for `bpy`.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTAINER = os.path.dirname(_HERE)
if _CONTAINER not in sys.path:
    sys.path.insert(0, _CONTAINER)


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")
