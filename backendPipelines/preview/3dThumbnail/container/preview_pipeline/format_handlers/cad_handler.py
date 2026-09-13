# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
CAD format handler for .stp / .step files, plus B-rep helpers for .iges / .igs / .brep.
Loads geometry using cadquery/OCP (Open CASCADE) and tessellates to mesh for rendering.
"""

import os
import re
import numpy as np
import pyvista as pv
from ..utils.logging import get_logger

logger = get_logger()

SUPPORTED_EXTENSIONS = {".stp", ".step"}

# Every B-rep container the OCP readers open; the thumbnail gate above is a subset of it.
SHAPE_EXTENSIONS = {".stp", ".step", ".iges", ".igs", ".brep"}

# STEP declares its length unit as an SI_UNIT with an optional prefix, or as a named conversion unit.
_STEP_SI_UNIT = re.compile(r"SI_UNIT\s*\(\s*(\.\w+\.|\$)\s*,\s*\.METRE\.\s*\)", re.IGNORECASE)
_STEP_CONVERSION_UNIT = re.compile(r"CONVERSION_BASED_UNIT\s*\(\s*'([^']+)'", re.IGNORECASE)
_SI_PREFIX_NAMES = {".MILLI.": "millimetre", ".CENTI.": "centimetre", ".MICRO.": "micrometre",
                    ".KILO.": "kilometre", ".DECI.": "decimetre", "$": "metre"}


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
    return tessellate_shape(load_shape(file_path))


def load_shape(file_path: str):
    """Read a B-rep file into an OCP ``TopoDS_Shape``: STEP through ``STEPControl_Reader``, IGES through
    ``IGESControl_Reader``, BREP through ``BRepTools.Read_s``."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".stp", ".step"):
        from OCP.STEPControl import STEPControl_Reader
        reader = STEPControl_Reader()
        status = reader.ReadFile(file_path)
        if status != 1:
            raise ValueError(f"Failed to read STEP file, status: {status}")
        reader.TransferRoots()
        return reader.OneShape()
    if ext in (".iges", ".igs"):
        from OCP.IGESControl import IGESControl_Reader
        reader = IGESControl_Reader()
        status = reader.ReadFile(file_path)
        if status != 1:
            raise ValueError(f"Failed to read IGES file, status: {status}")
        reader.TransferRoots()
        return reader.OneShape()
    if ext == ".brep":
        from OCP.BRep import BRep_Builder
        from OCP.BRepTools import BRepTools
        from OCP.TopoDS import TopoDS_Shape
        shape = TopoDS_Shape()
        if not BRepTools.Read_s(shape, file_path, BRep_Builder()):
            raise ValueError(f"Failed to read BREP file: {file_path}")
        return shape
    raise ValueError(f"Unsupported CAD format: {ext}")


def tessellate_shape(shape, tolerance: float = 0.1) -> pv.PolyData:
    """Triangulate every face of the shape at ``tolerance`` and assemble one PolyData."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopLoc import TopLoc_Location

    mesh = BRepMesh_IncrementalMesh(shape, tolerance)
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
            for i in range(1, triangulation.NbNodes() + 1):
                node = triangulation.Node(i)
                vertices_list.append([node.X(), node.Y(), node.Z()])

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
        raise ValueError("No geometry found in CAD file after tessellation")

    vertices = np.array(vertices_list, dtype=np.float64)
    faces = np.array(faces_list, dtype=np.int64)

    n_faces = len(faces)
    pv_faces = np.column_stack([np.full(n_faces, 3), faces]).ravel()
    pv_mesh = pv.PolyData(vertices, pv_faces)

    logger.info(f"Tessellated CAD model (OCP): {len(vertices)} vertices, {n_faces} faces")
    return pv_mesh


def shape_statistics(shape) -> dict:
    """Counts of solids, shells, faces, wires, edges and vertices in the shape."""
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import (TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE, TopAbs_WIRE, TopAbs_EDGE,
                            TopAbs_VERTEX)

    counts = {}
    for name, kind in (("solids", TopAbs_SOLID), ("shells", TopAbs_SHELL), ("faces", TopAbs_FACE),
                       ("wires", TopAbs_WIRE), ("edges", TopAbs_EDGE), ("vertices", TopAbs_VERTEX)):
        explorer = TopExp_Explorer(shape, kind)
        count = 0
        while explorer.More():
            count += 1
            explorer.Next()
        counts[name] = count
    return counts


def shape_geometry(shape) -> dict:
    """Axis-aligned bounds, dimensions, volume, surface area and centre of mass in the file's units."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box, True)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()

    volume_props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, volume_props)
    centre = volume_props.CentreOfMass()
    surface_props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, surface_props)

    return {
        "boundsMin": [float(xmin), float(ymin), float(zmin)],
        "boundsMax": [float(xmax), float(ymax), float(zmax)],
        "dimensions": [float(xmax - xmin), float(ymax - ymin), float(zmax - zmin)],
        "volume": float(volume_props.Mass()),
        "surfaceArea": float(surface_props.Mass()),
        "centerOfMass": [float(centre.X()), float(centre.Y()), float(centre.Z())],
    }


def declared_step_units(file_path: str, scan_bytes: int = 2_000_000):
    """The length unit an ASCII STEP file declares (``millimetre``, ``metre``, ``inch``, ...), read from
    its unit entities within the first ``scan_bytes``; None when none is declared there."""
    with open(file_path, "rb") as handle:
        text = handle.read(scan_bytes).decode("latin-1", "replace")
    conversion = _STEP_CONVERSION_UNIT.search(text)
    if conversion:
        return conversion.group(1).strip().lower()
    si_unit = _STEP_SI_UNIT.search(text)
    if si_unit:
        prefix = si_unit.group(1).upper()
        return _SI_PREFIX_NAMES.get(prefix, prefix.strip(".").lower() + "metre")
    return None


def estimated_units(max_dimension: float) -> str:
    """A unit guess from the model's largest extent, for files that declare none."""
    if max_dimension <= 0:
        return "unknown"
    if max_dimension < 1:
        return "meters (estimated)"
    if max_dimension < 100:
        return "centimeters (estimated)"
    if max_dimension < 1000:
        return "millimeters (estimated)"
    return "unknown"
