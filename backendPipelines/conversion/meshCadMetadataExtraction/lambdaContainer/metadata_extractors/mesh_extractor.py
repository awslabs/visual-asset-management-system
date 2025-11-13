# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Mesh metadata extraction using Trimesh.
"""

import os
import json
import datetime
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
import trimesh
from common.logger import safeLogger

logger = safeLogger(service="cadMetadataExtraction_MeshExtractor")

def extract_mesh_metadata(file_path: str) -> Dict[str, Any]:
    """
    Extract metadata from mesh files using Trimesh.
    
    Args:
        file_path: Path to the mesh file
        
    Returns:
        Dictionary containing extracted metadata
    """
    logger.info(f"Extracting mesh metadata from {file_path}")
    
    file_name = os.path.basename(file_path)
    file_extension = os.path.splitext(file_name)[1].lower()
    
    try:
        # Load the mesh
        mesh = trimesh.load(file_path)
        
        # Initialize metadata dictionary
        metadata = {}
        
        # Extract geometric metadata
        metadata['AB_geometric_metadata'] = extract_geometric_metadata(mesh)
        
        # Extract mesh statistics
        metadata['AB_mesh_statistics'] = extract_mesh_statistics(mesh)
        
        # Extract format-specific metadata
        metadata['AB_format_specific'] = extract_format_specific_metadata(mesh, file_extension)
        
        # Extract visual metadata (textures, materials)
        metadata['AB_visual_metadata'] = extract_visual_metadata(mesh)
        
        # Extract animation data if available
        if hasattr(mesh, 'animation'):
            metadata['AB_animation_data'] = extract_animation_data(mesh)
        
        # Extract scene hierarchy if it's a scene
        if isinstance(mesh, trimesh.Scene):
            metadata['AB_scene_hierarchy'] = extract_scene_hierarchy(mesh)
        
        
        return metadata
        
    except Exception as e:
        logger.exception(f"Error extracting mesh metadata: {str(e)}")
        # Return basic file info even if extraction fails
        return {
        }

def extract_geometric_metadata(mesh) -> Dict[str, Any]:
    """
    Extract geometric metadata from a mesh.
    
    Args:
        mesh: Trimesh mesh or scene
        
    Returns:
        Dictionary containing geometric metadata
    """
    try:
        # Handle both single meshes and scenes
        if isinstance(mesh, trimesh.Scene):
            # For scenes, get the bounds of all geometry
            bounds = mesh.bounds
            # Get total volume and area if possible
            volume = sum(g.volume for g in mesh.geometry.values() if hasattr(g, 'volume'))
            area = sum(g.area for g in mesh.geometry.values() if hasattr(g, 'area'))
        else:
            # For single meshes
            bounds = mesh.bounds
            volume = mesh.volume if hasattr(mesh, 'volume') else None
            area = mesh.area if hasattr(mesh, 'area') else None
        
        # Calculate dimensions
        dimensions = {
            'width': float(bounds[1][0] - bounds[0][0]),
            'height': float(bounds[1][1] - bounds[0][1]),
            'depth': float(bounds[1][2] - bounds[0][2])
        }
        
        # Determine units (if possible)
        units = determine_units(dimensions)
        
        # Get center of mass
        if isinstance(mesh, trimesh.Scene):
            # For scenes, use the centroid of the bounds
            center = (bounds[0] + bounds[1]) / 2
            com = {'x': float(center[0]), 'y': float(center[1]), 'z': float(center[2])}
        else:
            # For single meshes
            if hasattr(mesh, 'center_mass'):
                center = mesh.center_mass
                com = {'x': float(center[0]), 'y': float(center[1]), 'z': float(center[2])}
            else:
                com = {'x': 0, 'y': 0, 'z': 0}
        
        return {
            'bounding_box': {
                'min': {'x': float(bounds[0][0]), 'y': float(bounds[0][1]), 'z': float(bounds[0][2])},
                'max': {'x': float(bounds[1][0]), 'y': float(bounds[1][1]), 'z': float(bounds[1][2])}
            },
            'dimensions': dimensions,
            'volume': float(volume) if volume is not None else None,
            'surface_area': float(area) if area is not None else None,
            'units': units,
            'center_of_mass': com
        }
    except Exception as e:
        logger.warning(f"Error extracting geometric metadata: {str(e)}")
        return {
            'extraction_error': str(e)
        }

def extract_mesh_statistics(mesh) -> Dict[str, Any]:
    """
    Extract mesh statistics.
    
    Args:
        mesh: Trimesh mesh or scene
        
    Returns:
        Dictionary containing mesh statistics
    """
    try:
        if isinstance(mesh, trimesh.Scene):
            # Aggregate statistics for all meshes in the scene
            stats = {
                'mesh_count': len(mesh.geometry),
                'total_faces': sum(len(g.faces) for g in mesh.geometry.values() if hasattr(g, 'faces')),
                'total_vertices': sum(len(g.vertices) for g in mesh.geometry.values() if hasattr(g, 'vertices')),
                'total_edges': sum(len(g.edges) for g in mesh.geometry.values() if hasattr(g, 'edges')),
                'watertight': all(g.is_watertight for g in mesh.geometry.values() if hasattr(g, 'is_watertight')),
                'manifold': all(g.is_volume for g in mesh.geometry.values() if hasattr(g, 'is_volume'))
            }
        else:
            # Statistics for a single mesh
            stats = {
                'faces': len(mesh.faces) if hasattr(mesh, 'faces') else 0,
                'vertices': len(mesh.vertices) if hasattr(mesh, 'vertices') else 0,
                'edges': len(mesh.edges) if hasattr(mesh, 'edges') else 0,
                'watertight': mesh.is_watertight if hasattr(mesh, 'is_watertight') else False,
                'manifold': mesh.is_volume if hasattr(mesh, 'is_volume') else False
            }
            
            # Check for duplicate vertices
            if hasattr(mesh, 'vertices'):
                unique_verts = len(np.unique(mesh.vertices, axis=0))
                stats['duplicate_vertices'] = len(mesh.vertices) - unique_verts
            
            # Check for degenerate faces
            if hasattr(mesh, 'faces'):
                degenerate = 0
                for face in mesh.faces:
                    if len(np.unique(face)) < 3:
                        degenerate += 1
                stats['degenerate_faces'] = degenerate
        
        return stats
    except Exception as e:
        logger.warning(f"Error extracting mesh statistics: {str(e)}")
        return {
            'extraction_error': str(e)
        }

def extract_format_specific_metadata(mesh, file_extension: str) -> Dict[str, Any]:
    """
    Extract format-specific metadata.
    
    Args:
        mesh: Trimesh mesh or scene
        file_extension: File extension
        
    Returns:
        Dictionary containing format-specific metadata
    """
    metadata = {}
    
    try:
        # GLTF/GLB specific metadata
        if file_extension in ['.gltf', '.glb']:
            # Check for DRACO compression
            metadata['has_draco_compression'] = check_for_draco(mesh)
            
            # Check for 3D tiles
            metadata['has_3d_tiles'] = check_for_3d_tiles(mesh)
            
            # Extract embedded extensions
            if hasattr(mesh, 'metadata') and 'gltf' in mesh.metadata:
                gltf_meta = mesh.metadata['gltf']
                if 'extensions' in gltf_meta:
                    metadata['extensions'] = list(gltf_meta['extensions'].keys())
                if 'extras' in gltf_meta:
                    metadata['extras'] = gltf_meta['extras']
        
        # OBJ specific metadata
        elif file_extension == '.obj':
            if hasattr(mesh, 'metadata') and 'material_library' in mesh.metadata:
                metadata['material_library'] = mesh.metadata['material_library']
        
        # STL specific metadata
        elif file_extension == '.stl':
            if hasattr(mesh, 'metadata') and 'file_type' in mesh.metadata:
                metadata['file_type'] = mesh.metadata['file_type']  # 'ascii' or 'binary'
        
        # PLY specific metadata
        elif file_extension == '.ply':
            if hasattr(mesh, 'metadata') and 'ply_raw' in mesh.metadata:
                ply_raw = mesh.metadata['ply_raw']
                if 'comments' in ply_raw:
                    metadata['comments'] = ply_raw['comments']
                if 'vertex_property' in ply_raw:
                    metadata['vertex_properties'] = list(ply_raw['vertex_property'].keys())
        
        return metadata
    except Exception as e:
        logger.warning(f"Error extracting format-specific metadata: {str(e)}")
        return {
            'extraction_error': str(e)
        }

def extract_visual_metadata(mesh) -> Dict[str, Any]:
    """
    Extract visual metadata (textures, materials).
    
    Args:
        mesh: Trimesh mesh or scene
        
    Returns:
        Dictionary containing visual metadata
    """
    try:
        visual_data = {}
        
        # Handle both single meshes and scenes
        if isinstance(mesh, trimesh.Scene):
            # For scenes, collect materials and textures from all meshes
            materials = []
            textures = []
            
            for name, geom in mesh.geometry.items():
                if hasattr(geom, 'visual') and hasattr(geom.visual, 'material'):
                    mat = geom.visual.material
                    if mat is not None:
                        materials.append({
                            'geometry_name': name,
                            'material_name': getattr(mat, 'name', f"material_{len(materials)}"),
                            'properties': extract_material_properties(mat)
                        })
                
                # Extract textures
                if hasattr(geom, 'visual') and hasattr(geom.visual, 'texture'):
                    if geom.visual.texture is not None:
                        textures.append({
                            'geometry_name': name,
                            'texture_name': getattr(geom.visual.texture, 'name', f"texture_{len(textures)}"),
                            'properties': extract_texture_properties(geom.visual.texture)
                        })
            
            visual_data['materials'] = materials
            visual_data['textures'] = textures
            
        else:
            # For single meshes
            if hasattr(mesh, 'visual') and hasattr(mesh.visual, 'material'):
                mat = mesh.visual.material
                if mat is not None:
                    visual_data['material'] = {
                        'name': getattr(mat, 'name', "material"),
                        'properties': extract_material_properties(mat)
                    }
            
            # Extract texture
            if hasattr(mesh, 'visual') and hasattr(mesh.visual, 'texture'):
                if mesh.visual.texture is not None:
                    visual_data['texture'] = {
                        'name': getattr(mesh.visual.texture, 'name', "texture"),
                        'properties': extract_texture_properties(mesh.visual.texture)
                    }
        
        # Extract vertex colors if available
        if not isinstance(mesh, trimesh.Scene) and hasattr(mesh, 'visual') and hasattr(mesh.visual, 'vertex_colors'):
            if mesh.visual.vertex_colors is not None:
                visual_data['has_vertex_colors'] = True
                # Count unique colors
                unique_colors = np.unique(mesh.visual.vertex_colors, axis=0)
                visual_data['unique_vertex_colors'] = len(unique_colors)
        
        return visual_data
    except Exception as e:
        logger.warning(f"Error extracting visual metadata: {str(e)}")
        return {
            'extraction_error': str(e)
        }

def extract_material_properties(material) -> Dict[str, Any]:
    """
    Extract properties from a material.
    
    Args:
        material: Trimesh material
        
    Returns:
        Dictionary containing material properties
    """
    properties = {}
    
    # Common material properties
    for prop in ['ambient', 'diffuse', 'specular', 'glossiness', 'roughness', 'metallic']:
        if hasattr(material, prop):
            value = getattr(material, prop)
            if isinstance(value, np.ndarray):
                properties[prop] = value.tolist()
            else:
                properties[prop] = value
    
    return properties

def extract_texture_properties(texture) -> Dict[str, Any]:
    """
    Extract properties from a texture.
    
    Args:
        texture: Trimesh texture
        
    Returns:
        Dictionary containing texture properties
    """
    properties = {}
    
    # Basic texture properties
    if hasattr(texture, 'image'):
        if texture.image is not None:
            properties['dimensions'] = {
                'width': texture.image.shape[1],
                'height': texture.image.shape[0]
            }
            properties['channels'] = texture.image.shape[2] if len(texture.image.shape) > 2 else 1
    
    # Check if texture is embedded or referenced
    properties['embedded'] = not (hasattr(texture, 'name') and texture.name.startswith('file://'))
    
    # Get texture format if available
    if hasattr(texture, 'format'):
        properties['format'] = texture.format
    
    return properties

def extract_animation_data(mesh) -> Dict[str, Any]:
    """
    Extract animation data from a mesh.
    
    Args:
        mesh: Trimesh mesh or scene with animation
        
    Returns:
        Dictionary containing animation data
    """
    try:
        animation_data = {}
        
        if hasattr(mesh, 'animation'):
            anim = mesh.animation
            
            # Basic animation properties
            animation_data['frame_count'] = len(anim.frames)
            animation_data['duration'] = anim.duration
            animation_data['fps'] = anim.fps
            
            # Track information
            if hasattr(anim, 'tracks'):
                tracks = []
                for track_name, track in anim.tracks.items():
                    tracks.append({
                        'name': track_name,
                        'target': track.target if hasattr(track, 'target') else None,
                        'keyframe_count': len(track.keyframes) if hasattr(track, 'keyframes') else 0
                    })
                animation_data['tracks'] = tracks
        
        return animation_data
    except Exception as e:
        logger.warning(f"Error extracting animation data: {str(e)}")
        return {
            'extraction_error': str(e)
        }

def extract_scene_hierarchy(scene) -> Dict[str, Any]:
    """
    Extract scene hierarchy from a Trimesh scene.
    
    Args:
        scene: Trimesh scene
        
    Returns:
        Dictionary containing scene hierarchy
    """
    try:
        hierarchy = {
            'node_count': len(scene.graph.nodes),
            'nodes': []
        }
        
        # Extract node information
        for node_name in scene.graph.nodes_geometry:
            # Get transformation matrix
            transform = scene.graph.get(node_name)[0]
            
            # Get geometry reference
            geom_name = scene.graph.get(node_name)[1]
            
            # Get geometry if available
            if geom_name in scene.geometry:
                geom = scene.geometry[geom_name]
                
                # Extract node data
                node_data = {
                    'name': node_name,
                    'geometry_name': geom_name,
                    'transform': transform.tolist(),
                    'geometry_type': type(geom).__name__,
                }
                
                # Add basic geometry stats
                if hasattr(geom, 'vertices') and hasattr(geom, 'faces'):
                    node_data['vertex_count'] = len(geom.vertices)
                    node_data['face_count'] = len(geom.faces)
                
                hierarchy['nodes'].append(node_data)
        
        return hierarchy
    except Exception as e:
        logger.warning(f"Error extracting scene hierarchy: {str(e)}")
        return {
            'extraction_error': str(e)
        }

def check_for_draco(mesh) -> bool:
    """
    Check if a GLTF/GLB mesh uses DRACO compression.
    
    Args:
        mesh: Trimesh mesh or scene
        
    Returns:
        Boolean indicating if DRACO compression is used
    """
    # Check for DRACO extension in metadata
    if hasattr(mesh, 'metadata') and 'gltf' in mesh.metadata:
        gltf_meta = mesh.metadata['gltf']
        if 'extensions' in gltf_meta and 'KHR_draco_mesh_compression' in gltf_meta['extensions']:
            return True
    
    return False

def check_for_3d_tiles(mesh) -> bool:
    """
    Check if a GLTF/GLB mesh has 3D tiles data.
    
    Args:
        mesh: Trimesh mesh or scene
        
    Returns:
        Boolean indicating if 3D tiles data is present
    """
    # Check for 3D tiles extension in metadata
    if hasattr(mesh, 'metadata') and 'gltf' in mesh.metadata:
        gltf_meta = mesh.metadata['gltf']
        if 'extensions' in gltf_meta and '3DTILES_content' in gltf_meta['extensions']:
            return True
    
    return False

def determine_units(dimensions: Dict[str, float]) -> str:
    """
    Try to determine the units of measurement based on dimensions.
    
    Args:
        dimensions: Dictionary of dimensions
        
    Returns:
        String representing the likely units
    """
    # This is a heuristic approach - not always accurate
    max_dim = max(dimensions.values())
    
    if max_dim < 1:
        return "meters (estimated)"
    elif max_dim < 100:
        return "centimeters (estimated)"
    elif max_dim < 1000:
        return "millimeters (estimated)"
    else:
        return "unknown"
