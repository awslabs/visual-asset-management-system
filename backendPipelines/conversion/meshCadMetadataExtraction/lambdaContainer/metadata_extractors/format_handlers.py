# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Format handlers for different file types.
Determines which extractor to use based on file extension.
"""

import os
from typing import Dict, Callable, Any, Optional

# Define supported formats
CAD_FORMATS = ['.step', '.stp', '.dxf']
MESH_FORMATS = ['.stl', '.obj', '.ply', '.gltf', '.glb', '.3mf', '.xaml', '.3dxml', '.dae', '.xyz']
SUPPORTED_FORMATS = CAD_FORMATS + MESH_FORMATS

def get_handler_for_format(file_path: str) -> Optional[str]:
    """
    Determine which handler to use based on file extension.
    
    Args:
        file_path: Path to the file
        
    Returns:
        String indicating the handler type: 'cad', 'mesh', or None if unsupported
    """
    _, file_extension = os.path.splitext(file_path.lower())
    
    if file_extension in CAD_FORMATS:
        return 'cad'
    elif file_extension in MESH_FORMATS:
        return 'mesh'
    else:
        return None
