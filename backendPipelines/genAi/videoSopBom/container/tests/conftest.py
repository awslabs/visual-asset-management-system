# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Make the `video_sop_bom_pipeline` package importable in the shared interpreter.

The container directory is the package root (`ENTRYPOINT ["python", "-m", "video_sop_bom_pipeline"]`),
so it goes on `sys.path` and the package is imported by name. The contracts under test (vocabulary,
JSON Schemas, prompt files, the vendored path helper) need only `jsonschema`, which the repository's
interpreter already carries. Nothing here reaches AWS. The container's runtime tests append their fake
clients and media seams below this block; the path setup and the marker registration stay as they are.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTAINER = os.path.dirname(_HERE)
if _CONTAINER not in sys.path:
    sys.path.insert(0, _CONTAINER)


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")
