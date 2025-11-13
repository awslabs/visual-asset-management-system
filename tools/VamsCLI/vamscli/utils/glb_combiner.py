"""GLB file combining utilities for VamsCLI spatial commands."""

import struct
import json
import os
import re
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path


class GLBCombineError(Exception):
    """Raised when GLB combination fails."""
    pass


def sanitize_node_name(name: str) -> str:
    """
    Remove special characters from node names for glTF compatibility.
    
    Args:
        name: Original node name
        
    Returns:
        Sanitized node name safe for glTF
    """
    if not name:
        return "node"
    
    # Replace problematic characters with underscores
    # Keep alphanumeric, underscores, hyphens, and spaces
    sanitized = re.sub(r'[^a-zA-Z0-9_\-\s]', '_', name)
    
    # Remove leading/trailing whitespace and underscores
    sanitized = sanitized.strip(' _')
    
    # Ensure name is not empty after sanitization
    if not sanitized:
        return "node"
    
    return sanitized


def validate_export_has_glbs(export_result: Dict[str, Any]) -> Tuple[bool, int]:
    """
    Validate that export contains at least one GLB file.
    
    Args:
        export_result: Export command result with assets
        
    Returns:
        Tuple of (has_glbs: bool, glb_count: int)
    """
    glb_count = 0
    
    assets = export_result.get('assets', [])
    for asset in assets:
        files = asset.get('files', [])
        for file in files:
            filename = file.get('fileName', '').lower()
            if filename.endswith('.glb'):
                glb_count += 1
    
    return (glb_count > 0, glb_count)


def build_transform_tree_from_export(export_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build complete glTF node hierarchy from export data.
    
    Creates transform nodes for ALL relationship instances in the hierarchy.
    When the same asset appears multiple times with different alias IDs, each
    instance gets its own transform node with a unique name (AssetName__AliasID).
    
    Args:
        export_result: Export command result with assets and relationships
        
    Returns:
        Dictionary with:
        - 'gltf': Complete glTF JSON structure with node hierarchy
        - 'glb_map': Maps node indices to their GLB file paths
        - 'instance_to_node': Maps (asset_id, alias_id) tuples to node indices
        
    Raises:
        GLBCombineError: If tree building fails
    """
    try:
        assets = export_result.get('assets', [])
        relationships = export_result.get('relationships', [])
        
        # Build asset lookup
        asset_lookup = {asset['assetid']: asset for asset in assets}
        
        # Build parent-child relationship map
        parent_child_map = {}
        for rel in relationships:
            parent_id = rel.get('parentAssetId')
            child_id = rel.get('childAssetId')
            
            if parent_id not in parent_child_map:
                parent_child_map[parent_id] = []
            
            parent_child_map[parent_id].append({
                'child_id': child_id,
                'metadata': rel.get('metadata', {}),
                'alias_id': rel.get('assetLinkAliasId')
            })
        
        # Find root asset
        root_asset_id = None
        for asset in assets:
            if asset.get('is_root_lookup_asset'):
                root_asset_id = asset['assetid']
                break
        
        if not root_asset_id:
            raise GLBCombineError("No root asset found in export result")
        
        # Initialize glTF structure
        gltf = {
            'asset': {'version': '2.0'},
            'scene': 0,
            'scenes': [{'nodes': []}],
            'nodes': [],
            'meshes': [],
            'materials': [],
            'textures': [],
            'images': [],
            'accessors': [],
            'bufferViews': [],
            'buffers': [{'byteLength': 0}]
        }
        
        glb_map = {}  # Maps node index to list of GLB file paths
        instance_to_node = {}  # Maps (asset_id, alias_id) tuples to node indices
        
        def build_node_for_instance(
            asset_id: str, 
            alias_id: Optional[str] = None,
            parent_node_idx: Optional[int] = None
        ) -> int:
            """
            Build node for a specific relationship instance.
            
            Each relationship creates a unique node, even if the same asset
            appears multiple times with different alias IDs.
            """
            # Create unique key for this instance
            instance_key = (asset_id, alias_id)
            
            # Check if this specific instance already processed
            if instance_key in instance_to_node:
                return instance_to_node[instance_key]
            
            asset = asset_lookup.get(asset_id)
            if not asset:
                return -1
            
            # Build node name with alias suffix if provided
            base_name = sanitize_node_name(asset.get('assetname', asset_id))
            if alias_id:
                sanitized_alias = sanitize_node_name(str(alias_id))
                node_name = f"{base_name}__{sanitized_alias}"
            else:
                node_name = base_name
            
            node = {
                'name': node_name
            }
            
            # Add node to glTF
            node_idx = len(gltf['nodes'])
            gltf['nodes'].append(node)
            instance_to_node[instance_key] = node_idx
            
            # Collect GLB files for this instance
            glb_files = []
            for file in asset.get('files', []):
                if file.get('fileName', '').lower().endswith('.glb'):
                    glb_files.append(file)
            
            if glb_files:
                glb_map[node_idx] = glb_files
            
            # Process children - each relationship creates a new instance
            children_indices = []
            for child_rel in parent_child_map.get(asset_id, []):
                child_id = child_rel['child_id']
                child_alias = child_rel.get('alias_id')
                child_metadata = child_rel['metadata']
                
                # Recursively build child instance
                child_node_idx = build_node_for_instance(
                    child_id,
                    child_alias,
                    node_idx
                )
                
                if child_node_idx >= 0:
                    children_indices.append(child_node_idx)
                    
                    # Apply transform matrix to child node
                    transform_matrix = build_transform_matrix_from_metadata(child_metadata)
                    
                    # Only add matrix if it's not identity
                    identity = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 
                               0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
                    if transform_matrix != identity:
                        gltf['nodes'][child_node_idx]['matrix'] = transform_matrix
            
            # Add children to node
            if children_indices:
                node['children'] = children_indices
            
            return node_idx
        
        # Build tree from root (no alias for root)
        root_node_idx = build_node_for_instance(root_asset_id, None)
        
        # Set root node in scene
        gltf['scenes'][0]['nodes'] = [root_node_idx]
        
        return {
            'gltf': gltf,
            'glb_map': glb_map,
            'instance_to_node': instance_to_node
        }
        
    except Exception as e:
        raise GLBCombineError(f"Failed to build transform tree: {e}")


def merge_glb_meshes_into_tree(tree_data: Dict[str, Any], temp_dir: str) -> Tuple[Dict[str, Any], bytes]:
    """
    Merge all GLB meshes into the transform tree.
    
    Takes the pre-built transform tree and merges all GLB file meshes into it,
    creating separate mesh objects for each GLB file and attaching them to the
    appropriate nodes.
    
    Args:
        tree_data: Tree data from build_transform_tree_from_export()
        temp_dir: Temporary directory containing downloaded GLB files
        
    Returns:
        Tuple of (final_gltf_json, combined_binary_data)
        
    Raises:
        GLBCombineError: If merging fails
    """
    try:
        gltf = tree_data['gltf'].copy()
        glb_map = tree_data['glb_map']
        
        combined_binary = b''
        
        # Process each node that has GLB files
        for node_idx, glb_files in glb_map.items():
            node = gltf['nodes'][node_idx]
            mesh_indices = []
            
            # Process each GLB file for this node
            for glb_file_info in glb_files:
                # Construct path to downloaded GLB file
                glb_key = glb_file_info.get('key', '')
                glb_path = os.path.join(temp_dir, glb_key)
                
                if not os.path.exists(glb_path):
                    # Try without asset ID prefix
                    glb_filename = glb_file_info.get('fileName', '')
                    glb_path = os.path.join(temp_dir, glb_filename)
                    
                    if not os.path.exists(glb_path):
                        continue  # Skip missing files
                
                # Read GLB file
                glb_data = read_glb_file(glb_path)
                child_json = glb_data['json']
                child_binary = glb_data['binary']
                
                # Get current offsets
                current_binary_offset = len(combined_binary)
                mesh_offset = len(gltf['meshes'])
                material_offset = len(gltf['materials'])
                texture_offset = len(gltf['textures'])
                image_offset = len(gltf['images'])
                accessor_offset = len(gltf['accessors'])
                buffer_view_offset = len(gltf['bufferViews'])
                
                # Update buffer views with new offset
                if 'bufferViews' in child_json:
                    for buffer_view in child_json['bufferViews']:
                        if 'byteOffset' in buffer_view:
                            buffer_view['byteOffset'] += current_binary_offset
                        else:
                            buffer_view['byteOffset'] = current_binary_offset
                
                # Update accessors
                if 'accessors' in child_json:
                    for accessor in child_json['accessors']:
                        if 'bufferView' in accessor:
                            accessor['bufferView'] += buffer_view_offset
                
                # Update meshes and set names
                glb_filename = os.path.splitext(glb_file_info.get('fileName', 'mesh'))[0]
                glb_filename = sanitize_node_name(glb_filename)
                
                if 'meshes' in child_json:
                    for mesh_idx, mesh in enumerate(child_json['meshes']):
                        # Set mesh name to filename
                        if len(child_json['meshes']) > 1:
                            mesh['name'] = f"{glb_filename}_{mesh_idx}"
                        else:
                            mesh['name'] = glb_filename
                        
                        # Update primitive references
                        if 'primitives' in mesh:
                            for primitive in mesh['primitives']:
                                if 'indices' in primitive:
                                    primitive['indices'] += accessor_offset
                                if 'attributes' in primitive:
                                    for attr_name, attr_idx in primitive['attributes'].items():
                                        primitive['attributes'][attr_name] = attr_idx + accessor_offset
                                if 'material' in primitive:
                                    primitive['material'] += material_offset
                        
                        # Add mesh and track its index
                        gltf['meshes'].append(mesh)
                        mesh_indices.append(len(gltf['meshes']) - 1)
                
                # Update materials
                if 'materials' in child_json:
                    for material in child_json['materials']:
                        # Update texture references
                        if 'pbrMetallicRoughness' in material:
                            pbr = material['pbrMetallicRoughness']
                            if 'baseColorTexture' in pbr and 'index' in pbr['baseColorTexture']:
                                pbr['baseColorTexture']['index'] += texture_offset
                            if 'metallicRoughnessTexture' in pbr and 'index' in pbr['metallicRoughnessTexture']:
                                pbr['metallicRoughnessTexture']['index'] += texture_offset
                        
                        if 'normalTexture' in material and 'index' in material['normalTexture']:
                            material['normalTexture']['index'] += texture_offset
                        if 'occlusionTexture' in material and 'index' in material['occlusionTexture']:
                            material['occlusionTexture']['index'] += texture_offset
                        if 'emissiveTexture' in material and 'index' in material['emissiveTexture']:
                            material['emissiveTexture']['index'] += texture_offset
                        
                        gltf['materials'].append(material)
                
                # Update textures
                if 'textures' in child_json:
                    for texture in child_json['textures']:
                        if 'source' in texture:
                            texture['source'] += image_offset
                        gltf['textures'].append(texture)
                
                # Update images
                if 'images' in child_json:
                    for image in child_json['images']:
                        if 'bufferView' in image:
                            image['bufferView'] += buffer_view_offset
                        gltf['images'].append(image)
                
                # Merge resources
                for resource_type in ['accessors', 'bufferViews']:
                    if resource_type in child_json:
                        gltf[resource_type].extend(child_json[resource_type])
                
                # Append binary data
                combined_binary += child_binary
            
            # Attach all meshes to this node
            if mesh_indices:
                if len(mesh_indices) == 1:
                    node['mesh'] = mesh_indices[0]
                else:
                    # Multiple meshes - attach first one, others need separate child nodes
                    node['mesh'] = mesh_indices[0]
                    
                    # Create child nodes for additional meshes
                    if 'children' not in node:
                        node['children'] = []
                    
                    for mesh_idx in mesh_indices[1:]:
                        mesh_node = {
                            'name': gltf['meshes'][mesh_idx]['name'],
                            'mesh': mesh_idx
                        }
                        gltf['nodes'].append(mesh_node)
                        node['children'].append(len(gltf['nodes']) - 1)
        
        # Update buffer length
        if gltf['buffers']:
            gltf['buffers'][0]['byteLength'] = len(combined_binary)
        
        return (gltf, combined_binary)
        
    except Exception as e:
        raise GLBCombineError(f"Failed to merge GLB meshes: {e}")


def write_combined_glb(output_path: str, gltf_json: Dict[str, Any], binary_data: bytes) -> None:
    """
    Write final combined GLB file.
    
    Args:
        output_path: Path for output GLB file
        gltf_json: Complete glTF JSON structure
        binary_data: Combined binary buffer data
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    write_glb_file(output_path, gltf_json, binary_data)


def read_glb_file(file_path: str) -> Dict[str, Any]:
    """
    Read a GLB file and extract its JSON and binary data.
    
    Args:
        file_path: Path to GLB file
        
    Returns:
        Dictionary with 'json' and 'binary' data
        
    Raises:
        GLBCombineError: If file is invalid or unsupported
    """
    with open(file_path, 'rb') as f:
        # Read GLB header
        magic = f.read(4)
        if magic != b'glTF':
            raise GLBCombineError(f"Invalid GLB file: {file_path}")
        
        version = struct.unpack('<I', f.read(4))[0]
        if version != 2:
            raise GLBCombineError(f"Unsupported GLB version: {version}")
        
        total_length = struct.unpack('<I', f.read(4))[0]
        
        # Read JSON chunk
        json_length = struct.unpack('<I', f.read(4))[0]
        json_type = f.read(4)
        if json_type != b'JSON':
            raise GLBCombineError("Expected JSON chunk")
        
        json_data = json.loads(f.read(json_length).decode('utf-8'))
        
        # Read binary chunk (if exists)
        binary_data = b''
        if f.tell() < total_length:
            try:
                bin_length = struct.unpack('<I', f.read(4))[0]
                bin_type = f.read(4)
                if bin_type == b'BIN\x00':
                    binary_data = f.read(bin_length)
            except struct.error:
                # No binary chunk
                pass
        
        return {
            'json': json_data,
            'binary': binary_data
        }


def write_glb_file(file_path: str, json_data: Dict[str, Any], binary_data: bytes = b'') -> None:
    """
    Write a GLB file with JSON and binary data.
    
    Args:
        file_path: Output file path
        json_data: glTF JSON data
        binary_data: Binary buffer data
    """
    # Convert JSON to bytes
    json_str = json.dumps(json_data, separators=(',', ':'))
    json_bytes = json_str.encode('utf-8')
    
    # Pad JSON to 4-byte alignment
    while len(json_bytes) % 4 != 0:
        json_bytes += b' '
    
    # Pad binary data to 4-byte alignment
    while len(binary_data) % 4 != 0:
        binary_data += b'\x00'
    
    # Calculate total length
    header_length = 12
    json_chunk_header = 8
    bin_chunk_header = 8 if binary_data else 0
    total_length = header_length + json_chunk_header + len(json_bytes) + bin_chunk_header + len(binary_data)
    
    with open(file_path, 'wb') as f:
        # Write GLB header
        f.write(b'glTF')  # magic
        f.write(struct.pack('<I', 2))  # version
        f.write(struct.pack('<I', total_length))  # total length
        
        # Write JSON chunk
        f.write(struct.pack('<I', len(json_bytes)))  # chunk length
        f.write(b'JSON')  # chunk type
        f.write(json_bytes)
        
        # Write binary chunk (if exists)
        if binary_data:
            f.write(struct.pack('<I', len(binary_data)))  # chunk length
            f.write(b'BIN\x00')  # chunk type
            f.write(binary_data)


def build_transform_matrix_from_metadata(metadata: Dict[str, Any]) -> List[float]:
    """
    Build 4x4 transform matrix from relationship metadata.
    
    Supports multiple input formats and automatically converts to glTF standard (column-major).
    
    Priority:
    1. Use 'Matrix' if provided (supports 2D arrays, 1D arrays, space-separated strings)
    2. Build from Transform/Translation, Rotation, Scale components (with defaults)
    3. Default to identity matrix
    
    Args:
        metadata: Relationship metadata dictionary
        
    Returns:
        16-element transformation matrix in column-major order (glTF standard)
    """
    # Priority 1: Check for direct matrix
    if 'Matrix' in metadata:
        matrix_value = metadata['Matrix'].get('value')
        parsed_matrix = _parse_matrix_value(matrix_value)
        if parsed_matrix:
            return parsed_matrix
    
    # Priority 2: Build from components (Transform/Translation, Rotation, Scale)
    translation = [0.0, 0.0, 0.0]
    rotation = [0.0, 0.0, 0.0, 1.0]  # Identity quaternion (x, y, z, w)
    scale = [1.0, 1.0, 1.0]
    
    # Parse Translation (or Transform as alias)
    if 'Translation' in metadata:
        translation = _parse_vector3(metadata['Translation'].get('value'))
    elif 'Transform' in metadata:
        translation = _parse_vector3(metadata['Transform'].get('value'))
    
    # Parse Rotation (quaternion or euler angles)
    if 'Rotation' in metadata:
        rotation = _parse_rotation(metadata['Rotation'].get('value'))
    
    # Parse Scale
    if 'Scale' in metadata:
        scale = _parse_vector3(metadata['Scale'].get('value'), default=[1.0, 1.0, 1.0])
    
    # Build matrix from components
    return _build_matrix_from_trs(translation, rotation, scale)


def _parse_matrix_value(matrix_value: Any) -> Optional[List[float]]:
    """
    Parse matrix from various formats and convert to column-major.
    
    Supports:
    - Space-separated string: "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"
    - 1D JSON array: [1, 0, 0, 0, ...]
    - 2D JSON array (row-major): [[1,0,0,0], [0,1,0,0], ...]
    - 2D JSON array (column-major): [[1,0,0,0], [0,1,0,0], ...]
    
    Args:
        matrix_value: Matrix value in any supported format
        
    Returns:
        16-element column-major matrix or None if parsing fails
    """
    # Format 1: Space-separated string
    if isinstance(matrix_value, str) and ' ' in matrix_value and '[' not in matrix_value:
        try:
            matrix = [float(x) for x in matrix_value.split()]
            if len(matrix) == 16:
                return matrix  # Assume column-major
        except ValueError:
            pass
    
    # Format 2: JSON string (could be 1D or 2D array)
    if isinstance(matrix_value, str):
        try:
            parsed = json.loads(matrix_value)
            return _parse_matrix_array(parsed)
        except json.JSONDecodeError:
            pass
    
    # Format 3: Direct list
    if isinstance(matrix_value, list):
        return _parse_matrix_array(matrix_value)
    
    return None


def _parse_matrix_array(matrix_array: Any) -> Optional[List[float]]:
    """
    Parse matrix from array format (1D or 2D).
    
    Handles both row-major and column-major 2D arrays and converts to glTF column-major format.
    
    glTF column-major format stores matrix as 4 columns:
    [m00, m10, m20, m30,  m01, m11, m21, m31,  m02, m12, m22, m32,  m03, m13, m23, m33]
    Where column 3 (indices 12-15) contains translation: [tx, ty, tz, 1]
    
    Args:
        matrix_array: List that could be 1D (16 elements) or 2D (4x4)
        
    Returns:
        16-element column-major matrix or None
    """
    if not isinstance(matrix_array, list):
        return None
    
    # 1D array (16 elements)
    if len(matrix_array) == 16 and all(isinstance(x, (int, float)) for x in matrix_array):
        return [float(x) for x in matrix_array]  # Assume column-major
    
    # 2D array (4x4 matrix)
    if len(matrix_array) == 4:
        if all(isinstance(row, list) and len(row) == 4 for row in matrix_array):
            # Detect if row-major or column-major
            # Row-major 2D: [[m00, m01, m02, m03], [m10, m11, m12, m13], [m20, m21, m22, m23], [tx, ty, tz, 1]]
            # Column-major 2D: [[m00, m10, m20, m30], [m01, m11, m21, m31], [m02, m12, m22, m32], [tx, ty, tz, 1]]
            
            last_row = matrix_array[3]
            
            # Check if last row looks like translation (last element is 1.0)
            if last_row[3] == 1.0:
                # Row-major format - need to transpose
                # Input row-major: rows are [row0, row1, row2, row3]
                # Output column-major: columns stored sequentially
                # glTF column-major: [col0[0-3], col1[0-3], col2[0-3], col3[0-3]]
                # To transpose: column i of output = row i of input
                return [
                    # Column 0 (from row 0)
                    float(matrix_array[0][0]), float(matrix_array[0][1]), 
                    float(matrix_array[0][2]), float(matrix_array[0][3]),
                    # Column 1 (from row 1)
                    float(matrix_array[1][0]), float(matrix_array[1][1]), 
                    float(matrix_array[1][2]), float(matrix_array[1][3]),
                    # Column 2 (from row 2)
                    float(matrix_array[2][0]), float(matrix_array[2][1]), 
                    float(matrix_array[2][2]), float(matrix_array[2][3]),
                    # Column 3 (from row 3 - translation)
                    float(matrix_array[3][0]), float(matrix_array[3][1]), 
                    float(matrix_array[3][2]), float(matrix_array[3][3])
                ]
            else:
                # Column-major format - flatten directly (row by row becomes column by column)
                flattened = []
                for row in matrix_array:
                    flattened.extend([float(x) for x in row])
                return flattened
    
    return None


def _parse_vector3(value: Any, default: List[float] = None) -> List[float]:
    """
    Parse a 3D vector from various formats.
    
    Args:
        value: Vector value (JSON string or list)
        default: Default value if parsing fails
        
    Returns:
        3-element list [x, y, z]
    """
    if default is None:
        default = [0.0, 0.0, 0.0]
    
    if isinstance(value, str):
        try:
            vec_dict = json.loads(value)
            return [
                float(vec_dict.get('x', default[0])),
                float(vec_dict.get('y', default[1])),
                float(vec_dict.get('z', default[2]))
            ]
        except (json.JSONDecodeError, ValueError, KeyError):
            pass
    elif isinstance(value, list) and len(value) == 3:
        return [float(x) for x in value]
    elif isinstance(value, dict):
        return [
            float(value.get('x', default[0])),
            float(value.get('y', default[1])),
            float(value.get('z', default[2]))
        ]
    
    return default


def _parse_rotation(value: Any) -> List[float]:
    """
    Parse rotation from various formats.
    
    Supports:
    - Quaternion: {"x": 0, "y": 0, "z": 0, "w": 1}
    - Euler angles: {"x": 0, "y": 0, "z": 0} (degrees or radians)
    
    Args:
        value: Rotation value
        
    Returns:
        4-element quaternion [x, y, z, w]
    """
    identity_quat = [0.0, 0.0, 0.0, 1.0]
    
    if isinstance(value, str):
        try:
            rot_dict = json.loads(value)
            
            # Check if quaternion (has 'w' component)
            if 'w' in rot_dict:
                return [
                    float(rot_dict.get('x', 0)),
                    float(rot_dict.get('y', 0)),
                    float(rot_dict.get('z', 0)),
                    float(rot_dict.get('w', 1))
                ]
            else:
                # Euler angles - for now, return identity
                # TODO: Implement euler to quaternion conversion if needed
                return identity_quat
                
        except (json.JSONDecodeError, ValueError, KeyError):
            pass
    elif isinstance(value, list) and len(value) == 4:
        return [float(x) for x in value]
    elif isinstance(value, dict):
        if 'w' in value:
            return [
                float(value.get('x', 0)),
                float(value.get('y', 0)),
                float(value.get('z', 0)),
                float(value.get('w', 1))
            ]
    
    return identity_quat


def _build_matrix_from_trs(translation: List[float], rotation: List[float], scale: List[float]) -> List[float]:
    """
    Build a 4x4 transformation matrix from translation, rotation (quaternion), and scale.
    
    Args:
        translation: [x, y, z]
        rotation: [x, y, z, w] quaternion
        scale: [x, y, z]
        
    Returns:
        16-element column-major transformation matrix
    """
    # Extract quaternion components
    qx, qy, qz, qw = rotation
    
    # Build rotation matrix from quaternion
    # Reference: https://www.euclideanspace.com/maths/geometry/rotations/conversions/quaternionToMatrix/
    xx = qx * qx
    xy = qx * qy
    xz = qx * qz
    xw = qx * qw
    yy = qy * qy
    yz = qy * qz
    yw = qy * qw
    zz = qz * qz
    zw = qz * qw
    
    # Build matrix (column-major order for glTF)
    # Column 0
    m00 = (1.0 - 2.0 * (yy + zz)) * scale[0]
    m01 = (2.0 * (xy + zw)) * scale[0]
    m02 = (2.0 * (xz - yw)) * scale[0]
    m03 = 0.0
    
    # Column 1
    m10 = (2.0 * (xy - zw)) * scale[1]
    m11 = (1.0 - 2.0 * (xx + zz)) * scale[1]
    m12 = (2.0 * (yz + xw)) * scale[1]
    m13 = 0.0
    
    # Column 2
    m20 = (2.0 * (xz + yw)) * scale[2]
    m21 = (2.0 * (yz - xw)) * scale[2]
    m22 = (1.0 - 2.0 * (xx + yy)) * scale[2]
    m23 = 0.0
    
    # Column 3 (translation)
    m30 = translation[0]
    m31 = translation[1]
    m32 = translation[2]
    m33 = 1.0
    
    return [
        m00, m01, m02, m03,
        m10, m11, m12, m13,
        m20, m21, m22, m23,
        m30, m31, m32, m33
    ]


def combine_glb_files(parent_glb_path: str, child_glb_path: str,
                     output_path: str, 
                     child_transform_matrix: Optional[List[float]] = None,
                     child_asset_name: Optional[str] = None,
                     parent_asset_name: Optional[str] = None,
                     add_parent_transform_node: bool = False,
                     verbose: bool = False) -> bool:
    """
    Combine two GLB files using basic file operations.
    
    NOTE: This function is kept for backward compatibility but is no longer
    used by the new tree-first combining approach.
    
    Args:
        parent_glb_path: Path to parent GLB file (can be None for first combine)
        child_glb_path: Path to child GLB file
        output_path: Path for output GLB file
        child_transform_matrix: Optional 16-element transformation matrix
        child_asset_name: Optional asset name for the child transform node
        parent_asset_name: Optional asset name for the parent transform node
        add_parent_transform_node: Whether to add a parent transform node at scene level
        verbose: Whether to print progress messages
        
    Returns:
        bool: True if successful, False otherwise
        
    Raises:
        GLBCombineError: If combination fails
    """
    try:
        if verbose:
            print("Reading parent GLB file...")
        parent_data = read_glb_file(parent_glb_path)
        
        if verbose:
            print("Reading child GLB file...")
        child_data = read_glb_file(child_glb_path)
        
        if verbose:
            print("Combining GLB data...")
        
        # Start with parent data
        combined_json = parent_data['json'].copy()
        combined_binary = parent_data['binary']
        
        # Get offsets for child data
        parent_binary_length = len(parent_data['binary'])
        
        # Combine binary data
        combined_binary += child_data['binary']
        
        # Update buffer references in child JSON
        child_json = child_data['json'].copy()
        
        # Offset child buffer references
        if 'bufferViews' in child_json:
            for buffer_view in child_json['bufferViews']:
                if 'byteOffset' in buffer_view:
                    buffer_view['byteOffset'] += parent_binary_length
        
        # Get current counts for offsetting indices
        node_offset = len(combined_json.get('nodes', []))
        mesh_offset = len(combined_json.get('meshes', []))
        material_offset = len(combined_json.get('materials', []))
        accessor_offset = len(combined_json.get('accessors', []))
        buffer_view_offset = len(combined_json.get('bufferViews', []))
        
        # Update child node references
        if 'nodes' in child_json:
            for node in child_json['nodes']:
                if 'mesh' in node:
                    node['mesh'] += mesh_offset
                if 'children' in node:
                    node['children'] = [child_idx + node_offset for child_idx in node['children']]
        
        # Update child mesh references and set mesh names based on filename
        child_filename = os.path.splitext(os.path.basename(child_glb_path))[0]
        if 'meshes' in child_json:
            for mesh_idx, mesh in enumerate(child_json['meshes']):
                # Set mesh name to filename (or filename_N for multiple meshes)
                if len(child_json['meshes']) > 1:
                    mesh['name'] = f"{child_filename}_{mesh_idx}"
                else:
                    mesh['name'] = child_filename
                
                if 'primitives' in mesh:
                    for primitive in mesh['primitives']:
                        if 'indices' in primitive:
                            primitive['indices'] += accessor_offset
                        if 'attributes' in primitive:
                            for attr_name, attr_idx in primitive['attributes'].items():
                                primitive['attributes'][attr_name] = attr_idx + accessor_offset
                        if 'material' in primitive:
                            primitive['material'] += material_offset
        
        # Update child accessor references
        if 'accessors' in child_json:
            for accessor in child_json['accessors']:
                if 'bufferView' in accessor:
                    accessor['bufferView'] += buffer_view_offset
        
        # Merge resources into combined JSON
        for resource_type in ['nodes', 'meshes', 'materials', 'textures', 'images', 'accessors', 'bufferViews']:
            if resource_type in child_json:
                if resource_type not in combined_json:
                    combined_json[resource_type] = []
                combined_json[resource_type].extend(child_json[resource_type])
                if verbose:
                    print(f"Added {len(child_json[resource_type])} {resource_type}")
        
        # Update buffer info
        if 'buffers' in combined_json and len(combined_json['buffers']) > 0:
            combined_json['buffers'][0]['byteLength'] = len(combined_binary)
        
        # Add child nodes to main scene
        if 'scenes' in combined_json and len(combined_json['scenes']) > 0:
            main_scene = combined_json['scenes'][0]
            if 'nodes' not in main_scene:
                main_scene['nodes'] = []
            
            # Create a transform node for child content
            # Use asset name if provided, otherwise use filename
            node_name = child_asset_name if child_asset_name else os.path.splitext(os.path.basename(child_glb_path))[0]
            transform_node = {
                'name': node_name,
                'children': []
            }
            
            # Apply transformation matrix if provided
            if child_transform_matrix and child_transform_matrix != [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]:
                transform_node['matrix'] = child_transform_matrix
                if verbose:
                    print(f"Applied transformation matrix: {child_transform_matrix}")
            
            # Add child scene nodes to transform node
            if 'scenes' in child_json and len(child_json['scenes']) > 0:
                child_scene = child_json['scenes'][0]
                if 'nodes' in child_scene:
                    transform_node['children'] = [node_idx + node_offset for node_idx in child_scene['nodes']]
            
            # Add transform node to combined JSON and main scene
            combined_json['nodes'].append(transform_node)
            main_scene['nodes'].append(len(combined_json['nodes']) - 1)
            
            if verbose:
                print(f"Added transform node with {len(transform_node['children'])} child nodes")
        
        if verbose:
            print("Writing combined GLB file...")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        write_glb_file(output_path, combined_json, combined_binary)
        
        if verbose:
            print(f"✓ Successfully combined GLB files!")
            print(f"Output: {output_path} ({os.path.getsize(output_path)} bytes)")
        
        return True
        
    except Exception as e:
        if verbose:
            print(f"Error combining GLB files: {e}")
        raise GLBCombineError(f"Failed to combine GLB files: {e}")


def combine_multiple_glbs(glb_paths: List[str], output_path: str,
                         transforms: Optional[List[List[float]]] = None,
                         verbose: bool = False) -> bool:
    """
    Combine multiple GLB files into a single GLB.
    
    NOTE: This function is kept for backward compatibility but is no longer
    used by the new tree-first combining approach.
    
    Args:
        glb_paths: List of GLB file paths to combine
        output_path: Path for output combined GLB
        transforms: Optional list of transformation matrices (one per GLB)
        verbose: Whether to print progress messages
        
    Returns:
        bool: True if successful, False otherwise
        
    Raises:
        GLBCombineError: If combination fails
    """
    if not glb_paths:
        raise GLBCombineError("No GLB files provided")
    
    if len(glb_paths) == 1:
        # Single file, just copy
        import shutil
        shutil.copy2(glb_paths[0], output_path)
        return True
    
    # Start with first GLB as base
    current_glb = glb_paths[0]
    temp_dir = Path(output_path).parent / "temp_combine"
    temp_dir.mkdir(exist_ok=True)
    
    try:
        # Combine each subsequent GLB
        for i, next_glb in enumerate(glb_paths[1:], 1):
            temp_output = temp_dir / f"temp_{i}.glb"
            transform = transforms[i] if transforms and i < len(transforms) else None
            
            success = combine_glb_files(
                current_glb, next_glb, str(temp_output),
                transform, verbose=verbose
            )
            
            if not success:
                return False
            
            current_glb = str(temp_output)
        
        # Move final result to output
        import shutil
        shutil.move(current_glb, output_path)
        
        return True
        
    finally:
        # Cleanup temp directory
        import shutil
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def add_root_transform_node(glb_path: str, output_path: str, root_node_name: str) -> bool:
    """
    Add a root transform node at the scene level to wrap existing content.
    
    NOTE: This function is kept for backward compatibility but is no longer
    used by the new tree-first combining approach.
    
    Args:
        glb_path: Path to input GLB file
        output_path: Path for output GLB file
        root_node_name: Name for the root transform node
        
    Returns:
        bool: True if successful
        
    Raises:
        GLBCombineError: If operation fails
    """
    try:
        # Read the GLB
        glb_data = read_glb_file(glb_path)
        combined_json = glb_data['json'].copy()
        
        # Get the main scene
        if 'scenes' not in combined_json or len(combined_json['scenes']) == 0:
            raise GLBCombineError("No scenes found in GLB")
        
        main_scene = combined_json['scenes'][0]
        existing_scene_nodes = main_scene.get('nodes', [])
        
        # Create root transform node at identity (0,0,0)
        root_transform_node = {
            'name': root_node_name,
            'children': existing_scene_nodes  # All existing scene nodes become children
        }
        
        # Add root node to nodes array
        if 'nodes' not in combined_json:
            combined_json['nodes'] = []
        
        combined_json['nodes'].append(root_transform_node)
        root_node_index = len(combined_json['nodes']) - 1
        
        # Update scene to only reference the root node
        main_scene['nodes'] = [root_node_index]
        
        # Write the modified GLB
        write_glb_file(output_path, combined_json, glb_data['binary'])
        
        return True
        
    except Exception as e:
        raise GLBCombineError(f"Failed to add root transform node: {e}")


def create_empty_glb(output_path: str) -> str:
    """
    Create an empty GLB file with minimal structure.
    
    NOTE: This function is kept for backward compatibility but is no longer
    used by the new tree-first combining approach.
    
    Args:
        output_path: Path where to create the empty GLB
        
    Returns:
        Path to the created empty GLB file
    """
    empty_gltf = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": []}],
        "nodes": [],
        "meshes": [],
        "materials": [],
        "accessors": [],
        "bufferViews": [],
        "buffers": []
    }
    
    write_glb_file(output_path, empty_gltf, b'')
    return output_path


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        Formatted size string (e.g., "1.5 MB")
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"
