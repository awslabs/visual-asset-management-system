# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Make the container's handler importable without installing the container's own dependencies.

`requirements.txt` here declares `pydantic>=2.0`, plus pyproj, open3d, pye57, laspy and structlog. The
repository's backend runs on pydantic **1.10.13** in the same interpreter, so installing this container's
requirements alongside it breaks every backend test. These tests therefore run against the shared
interpreter and stub exactly one module: `coord_xform.pipeline`, the only place the heavy geospatial chain
is reached.

What is deliberately NOT stubbed is `coord_xform.config` — it imports cleanly under pydantic v1, so the
handler's real `PipelineConfig` construction (including the `OnMismatch` coercion this fix turns on) is
exercised rather than mocked. Stubbing it would have hidden whether the config the handler builds is even
valid.

`test_pipeline_contract_is_unstubbed` guards the stub: it reads the real `coord_xform/pipeline.py` as text
and asserts the two properties the stub stands in for. Without that check, renaming `run_pipeline` or
changing `OnMismatch.ERROR` to stop raising would leave these tests green against a contract that no
longer exists.
"""

import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTAINER = os.path.dirname(_HERE)
sys.path.insert(0, _CONTAINER)


def _install_pipeline_stub():
    """Register a stand-in for coord_xform.pipeline so the local import in core.py resolves.

    `core.py` imports `run_pipeline` inside `_run_transform_stage`, at call time rather than at module
    import, so a sys.modules entry placed here is what that import picks up. Tests replace
    `run_pipeline` on this module object to choose the behaviour under test.
    """
    module = types.ModuleType("coord_xform.pipeline")

    def _unset(config, inputs):
        raise AssertionError(
            "coord_xform.pipeline.run_pipeline was called without a test setting its behaviour"
        )

    module.run_pipeline = _unset
    sys.modules["coord_xform.pipeline"] = module
    return module


PIPELINE_STUB = _install_pipeline_stub()
