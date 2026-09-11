# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Make the Blender container's pipeline importable in the shared interpreter.

`main/` is the container's package root (`ENTRYPOINT ["python3", "-m", "main"]`), so the container
directory goes on `sys.path` and the pipeline is imported as `main.pipelines.blenderRenderer.pipeline`.
It needs only boto3, which the repository already carries.

Neither Blender nor AWS is reached. `renderScene.py` cannot be imported here at all -- it is a Blender
script whose module body renders a scene -- so its import-dispatch block is extracted from the source
and executed against a recording stand-in for `bpy`, which exposes only the operators Blender really
has. `allconvert_blenderrenderer_pipeline` is exercised with `subprocess.run` replaced.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTAINER = os.path.dirname(_HERE)
if _CONTAINER not in sys.path:
    sys.path.insert(0, _CONTAINER)


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")
