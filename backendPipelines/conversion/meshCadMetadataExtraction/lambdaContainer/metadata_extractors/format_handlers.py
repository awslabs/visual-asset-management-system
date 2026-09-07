# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Format handlers for different file types.
Determines which extractor to use based on file extension.
"""

import os
from typing import Dict, Callable, Any, Optional

# Define supported formats. Every mesh format listed is one trimesh loads with the container's
# pinned dependencies (requirements.txt): COLLADA (`.dae`) needs pycollada and `.3mf` / `.xaml` /
# `.3dxml` need lxml and networkx, and with none of those installed trimesh registers an exception
# wrapper in place of each loader. CADQuery's own dependency closure supplies neither pair. The
# registered inputFileFilters in vamsSchema/pipeline.json and the workflow trigger declare this same
# list to VAMS.
CAD_FORMATS = ['.step', '.stp', '.dxf']
MESH_FORMATS = ['.stl', '.obj', '.ply', '.gltf', '.glb', '.xyz']
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
