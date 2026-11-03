# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The four image-to-3D model adapters.

Importing this package is safe in every image: an adapter module pulls in only the shared helpers,
and the upstream model library is imported inside the driver script that runs in the upstream repo.
An image therefore carries all four adapter classes and exactly one installed model, which is what
lets ``ModelAdapter.is_installed`` name the mismatch when a run reaches the wrong image.
"""

from .base import GenerationResult, ModelAdapter
from .sam3d import Sam3dAdapter
from .step1x3d import Step1X3dAdapter
from .trellis2 import Trellis2Adapter
from .triposr import TripoSRAdapter

__all__ = [
    "GenerationResult",
    "ModelAdapter",
    "Sam3dAdapter",
    "Step1X3dAdapter",
    "Trellis2Adapter",
    "TripoSRAdapter",
]
