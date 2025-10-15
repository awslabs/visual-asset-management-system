# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
CAD metadata extraction using CADQuery.
"""

import os
import json
import datetime
from typing import Dict, Any, List, Optional, Tuple
import cadquery as cq
from common.logger import safeLogger

logger = safeLogger(service="cadMetadataExtraction_CADExtractor")

def extract_cad_metadata(file_path: str) -> Dict[str, Any]:
    """
    Extract metadata from CAD files using CADQuery.
    
    Args:
        file_path: Path to the CAD file
        
    Returns:
        Dictionary containing extracted metadata
    """
    logger.info(f"Extracting CAD metadata from {file_path}")
    
    file_name = os.path.basename(file_path)
    file_extension = os.path.splitext(file_name)[1].lower()
    
    try:
        # Load the CAD model based on file extension
        if file_extension in ['.step', '.stp']:
            model = cq.importers.importStep(file_path)
            metadata = extract_step_metadata(model, file_path)
        elif file_extension == '.dxf':
            model = cq.importers.importDXF(file_path).wires()
            metadata = extract_dxf_metadata(model, file_path)
        else:
            raise ValueError(f"Unsupported CAD format: {file_extension}")
        
        return metadata
        
    except Exception as e:
        logger.exception(f"Error extracting CAD metadata: {str(e)}")
        # Return basic file info even if extraction fails
        return {
        }

def extract_step_metadata(model: cq.Shape, file_path: str) -> Dict[str, Any]:
    """
    Extract metadata from STEP files.
    
    Args:
        model: CADQuery model loaded from STEP file
        file_path: Path to the STEP file
        
    Returns:
        Dictionary containing STEP metadata
    """
    metadata = {}
    
    # Extract geometric metadata
    metadata['AB_geometric_metadata'] = extract_geometric_metadata(model)
    
    # Extract assembly hierarchy if it's a compound
    if model.ShapeType() == 'Compound':
        metadata['AB_assembly_hierarchy'] = extract_assembly_hierarchy(model)
    
    # Extract shape statistics
    metadata['AB_shape_statistics'] = extract_shape_statistics(model)
    
    # Try to extract materials
    try:
        metadata['AB_materials'] = extract_materials(model)
    except:
        pass
    
    # Try to extract custom metadata
    try:
        metadata['AB_custom_metadata'] = extract_custom_metadata(model)
    except:
        pass
    
    return metadata

def extract_dxf_metadata(model: cq.Workplane, file_path: str) -> Dict[str, Any]:
    """
    Extract metadata from DXF files.
    
    Args:
        model: CADQuery model loaded from DXF file
        file_path: Path to the DXF file
        
    Returns:
        Dictionary containing DXF metadata
    """
    metadata = {}
    
    # Extract 2D geometric metadata
    metadata['AB_geometric_metadata'] = extract_2d_geometric_metadata(model)
    
    # Extract layers if available
    try:
        metadata['AB_layers'] = extract_layers(model)
    except:
        pass
    
    # Extract entity counts
    metadata['AB_entity_statistics'] = extract_entity_statistics(model)
    
    return metadata

def extract_geometric_metadata(model: cq.Shape) -> Dict[str, Any]:
    """
    Extract geometric metadata from a 3D model.
    
    Args:
        model: CADQuery model
        
    Returns:
        Dictionary containing geometric metadata
    """
    try:
        # Get bounding box
        bbox = model.BoundingBox()
        
        # Calculate dimensions
        dimensions = {
            'width': bbox.xmax - bbox.xmin,
            'height': bbox.ymax - bbox.ymin,
            'depth': bbox.zmax - bbox.zmin
        }
        
        # Calculate volume and surface area
        try:
            volume = model.Volume()
        except:
            volume = None
            
        try:
            surface_area = model.Area()
        except:
            surface_area = None
        
        # Determine units (if possible)
        units = determine_units(dimensions)
        
        return {
            'bounding_box': {
                'min': {'x': bbox.xmin, 'y': bbox.ymin, 'z': bbox.zmin},
                'max': {'x': bbox.xmax, 'y': bbox.ymax, 'z': bbox.zmax}
            },
            'dimensions': dimensions,
            'volume': volume,
            'surface_area': surface_area,
            'units': units,
            'center_of_mass': get_center_of_mass(model)
        }
    except Exception as e:
        logger.warning(f"Error extracting geometric metadata: {str(e)}")
        return {
            'extraction_error': str(e)
        }

def extract_2d_geometric_metadata(model: cq.Workplane) -> Dict[str, Any]:
    """
    Extract geometric metadata from a 2D model.
    
    Args:
        model: CADQuery Workplane with 2D entities
        
    Returns:
        Dictionary containing 2D geometric metadata
    """
    try:
        # Get bounding box
        bbox = model.val().BoundingBox()
        
        # Calculate dimensions
        dimensions = {
            'width': bbox.xmax - bbox.xmin,
            'height': bbox.ymax - bbox.ymin
        }
        
        # Calculate area
        try:
            # For 2D, we can try to get the area of the face
            area = sum(wire.Area() for wire in model.wires().vals())
        except:
            area = None
        
        # Determine units (if possible)
        units = determine_units(dimensions)
        
        return {
            'bounding_box': {
                'min': {'x': bbox.xmin, 'y': bbox.ymin},
                'max': {'x': bbox.xmax, 'y': bbox.ymax}
            },
            'dimensions': dimensions,
            'area': area,
            'units': units
        }
    except Exception as e:
        logger.warning(f"Error extracting 2D geometric metadata: {str(e)}")
        return {
            'extraction_error': str(e)
        }

def extract_assembly_hierarchy(model: cq.Shape) -> List[Dict[str, Any]]:
    """
    Extract assembly hierarchy from a compound model.
    
    Args:
        model: CADQuery model (compound)
        
    Returns:
        List of components in the assembly
    """
    components = []
    
    try:
        # Get all solids in the compound
        solids = model.Solids()
        
        for i, solid in enumerate(solids):
            # Try to get name from metadata
            name = f"Component_{i+1}"
            
            # Get geometric data for this component
            geo_data = extract_geometric_metadata(solid)
            
            components.append({
                'name': name,
                'index': i,
                'geometry': geo_data,
                'type': solid.ShapeType()
            })
        
        return components
    except Exception as e:
        logger.warning(f"Error extracting assembly hierarchy: {str(e)}")
        return []

def extract_shape_statistics(model: cq.Shape) -> Dict[str, Any]:
    """
    Extract shape statistics from a model.
    
    Args:
        model: CADQuery model
        
    Returns:
        Dictionary containing shape statistics
    """
    try:
        # Count different shape types
        stats = {
            'solids': len(model.Solids()),
            'shells': len(model.Shells()),
            'faces': len(model.Faces()),
            'wires': len(model.Wires()),
            'edges': len(model.Edges()),
            'vertices': len(model.Vertices())
        }
        
        # Count face types
        face_types = {}
        for face in model.Faces():
            face_type = face.geomType()
            face_types[face_type] = face_types.get(face_type, 0) + 1
        
        stats['face_types'] = face_types
        
        # Count edge types
        edge_types = {}
        for edge in model.Edges():
            edge_type = edge.geomType()
            edge_types[edge_type] = edge_types.get(edge_type, 0) + 1
        
        stats['edge_types'] = edge_types
        
        return stats
    except Exception as e:
        logger.warning(f"Error extracting shape statistics: {str(e)}")
        return {
            'extraction_error': str(e)
        }

def extract_entity_statistics(model: cq.Workplane) -> Dict[str, Any]:
    """
    Extract entity statistics from a 2D model.
    
    Args:
        model: CADQuery Workplane with 2D entities
        
    Returns:
        Dictionary containing entity statistics
    """
    try:
        # Count different entity types
        stats = {
            'wires': len(model.wires().vals()),
            'edges': len(model.edges().vals()),
            'vertices': len(model.vertices().vals())
        }
        
        # Count edge types
        edge_types = {}
        for edge in model.edges().vals():
            edge_type = edge.geomType()
            edge_types[edge_type] = edge_types.get(edge_type, 0) + 1
        
        stats['edge_types'] = edge_types
        
        return stats
    except Exception as e:
        logger.warning(f"Error extracting entity statistics: {str(e)}")
        return {
            'extraction_error': str(e)
        }

def extract_materials(model: cq.Shape) -> List[Dict[str, Any]]:
    """
    Extract materials from a model.
    
    Args:
        model: CADQuery model
        
    Returns:
        List of materials in the model
    """
    # This is a placeholder - CADQuery doesn't have direct material extraction
    # In a real implementation, this would use the OCCT API to extract material properties
    return []

def extract_custom_metadata(model: cq.Shape) -> Dict[str, Any]:
    """
    Extract custom metadata from a model.
    
    Args:
        model: CADQuery model
        
    Returns:
        Dictionary containing custom metadata
    """
    # This is a placeholder - CADQuery doesn't have direct custom metadata extraction
    # In a real implementation, this would use the OCCT API to extract custom properties
    return {}

def extract_layers(model: cq.Workplane) -> List[Dict[str, Any]]:
    """
    Extract layers from a DXF model.
    
    Args:
        model: CADQuery Workplane with DXF entities
        
    Returns:
        List of layers in the model
    """
    # This is a placeholder - CADQuery doesn't have direct layer extraction
    # In a real implementation, this would extract layer information from the DXF
    return []

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

def get_center_of_mass(model: cq.Shape) -> Dict[str, float]:
    """
    Get the center of mass of a model.
    
    Args:
        model: CADQuery model
        
    Returns:
        Dictionary containing x, y, z coordinates
    """
    try:
        com = model.Center()
        return {'x': com.x, 'y': com.y, 'z': com.z}
    except:
        return {'x': 0, 'y': 0, 'z': 0}
