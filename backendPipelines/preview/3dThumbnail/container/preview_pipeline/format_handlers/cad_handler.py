# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
CAD format handler for .stp / .step files.
Loads STEP geometry using cadquery/OCP (Open CASCADE) and tessellates to mesh for rendering.
"""

import os
import numpy as np
import pyvista as pv
from ..utils.logging import get_logger

logger = get_logger()

SUPPORTED_EXTENSIONS = {".stp", ".step"}


def can_handle(extension: str) -> bool:
    return extension.lower() in SUPPORTED_EXTENSIONS


def load(file_path: str) -> pv.PolyData:
    """
    Load a STEP file and return a PyVista PolyData object for rendering.
    Uses cadquery to import the STEP file and tessellate to triangles.
    """
    ext = os.path.splitext(file_path)[1].lower()
    logger.info(f"Loading CAD file: {file_path} (format: {ext})")

    try:
        return _load_with_cadquery(file_path)
    except ImportError:
        logger.warning("cadquery not available, attempting OCP direct import...")
        return _load_with_ocp(file_path)


def _load_with_cadquery(file_path: str) -> pv.PolyData:
    """Load STEP file using cadquery and tessellate."""
    import cadquery as cq

    result = cq.importers.importStep(file_path)
    logger.info(f"Loaded STEP file with cadquery")

    # Tessellate the shape to get triangles
    # Try solids first, then shells, then faces (some STEP files only have surface geometry)
    vertices_list = []
    faces_list = []
    vertex_offset = 0

    shapes_to_tessellate = result.solids().vals()
    if not shapes_to_tessellate:
        logger.info("No solids found, trying shells...")
        shapes_to_tessellate = result.shells().vals()
    if not shapes_to_tessellate:
        logger.info("No shells found, trying faces...")
        shapes_to_tessellate = result.faces().vals()

    for shape in shapes_to_tessellate:
        tess = shape.tessellate(tolerance=0.1)
        verts, tri_faces = tess

        for v in verts:
            vertices_list.append([v.x, v.y, v.z])

        for face in tri_faces:
            faces_list.append([
                face[0] + vertex_offset,
                face[1] + vertex_offset,
                face[2] + vertex_offset,
            ])

        vertex_offset += len(verts)

    if not vertices_list:
        raise ValueError("No geometry found in STEP file after tessellation")

    vertices = np.array(vertices_list, dtype=np.float64)
    faces = np.array(faces_list, dtype=np.int64)

    n_faces = len(faces)
    pv_faces = np.column_stack([np.full(n_faces, 3), faces]).ravel()
    pv_mesh = pv.PolyData(vertices, pv_faces)

    logger.info(f"Tessellated CAD model: {len(vertices)} vertices, {n_faces} faces")
    return pv_mesh


def _load_with_ocp(file_path: str) -> pv.PolyData:
    """
    Load STEP file using OCP (Open CASCADE) directly.
    Fallback if cadquery high-level API is not available.
    """
    from OCP.STEPControl import STEPControl_Reader
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopLoc import TopLoc_Location

    reader = STEPControl_Reader()
    status = reader.ReadFile(file_path)
    if status != 1:
        raise ValueError(f"Failed to read STEP file, status: {status}")

    reader.TransferRoots()
    shape = reader.OneShape()

    # Tessellate the shape
    mesh = BRepMesh_IncrementalMesh(shape, 0.1)
    mesh.Perform()

    vertices_list = []
    faces_list = []
    vertex_offset = 0

    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = explorer.Current()
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)

        if triangulation is not None:
            # Extract vertices
            for i in range(1, triangulation.NbNodes() + 1):
                node = triangulation.Node(i)
                vertices_list.append([node.X(), node.Y(), node.Z()])

            # Extract triangles
            for i in range(1, triangulation.NbTriangles() + 1):
                tri = triangulation.Triangle(i)
                n1, n2, n3 = tri.Get()
                faces_list.append([
                    n1 - 1 + vertex_offset,
                    n2 - 1 + vertex_offset,
                    n3 - 1 + vertex_offset,
                ])

            vertex_offset += triangulation.NbNodes()

        explorer.Next()

    if not vertices_list:
        raise ValueError("No geometry found in STEP file after tessellation")

    vertices = np.array(vertices_list, dtype=np.float64)
    faces = np.array(faces_list, dtype=np.int64)

    n_faces = len(faces)
    pv_faces = np.column_stack([np.full(n_faces, 3), faces]).ravel()
    pv_mesh = pv.PolyData(vertices, pv_faces)

    logger.info(f"Tessellated CAD model (OCP): {len(vertices)} vertices, {n_faces} faces")
    return pv_mesh
