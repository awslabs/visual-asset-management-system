# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Metadata extractors for CAD and Mesh files.
"""

from .cad_extractor import extract_cad_metadata
from .mesh_extractor import extract_mesh_metadata
from .format_handlers import get_handler_for_format

__all__ = ["extract_cad_metadata", "extract_mesh_metadata", "get_handler_for_format"]
