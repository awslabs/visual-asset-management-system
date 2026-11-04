# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
CAD format handler for .stp / .step files.

The STEP file is read through XCAF (``STEPCAFControl_Reader`` into an ``XCAFDoc`` document), so the
assembly structure, each component's placement and the colours set on parts, components and faces
are kept. Every face of every part is tessellated -- solids, shells and free surfaces alike -- with a
linear deflection relative to the model's bounding-box diagonal plus an angular deflection, so the
detail does not depend on the model's units. Each face's triangulation is placed by its
``TopLoc_Location`` and its colour travels on its triangles.

Orientation is left as authored: STEP carries no up-axis metadata.

A file XCAF cannot read falls back to ``STEPControl_Reader`` (geometry only, same tessellation).

An assembly may place the same component many times from every level, so a file of a few kilobytes
can describe 2^depth placements, and OCCT's transfer (either reader) visits every one of them: a
30-level doubling runs for minutes inside ``Transfer`` before any Python runs. The placements
are therefore counted from the file's ``NEXT_ASSEMBLY_USAGE_OCCURRENCE`` entities first
(``assembly_occurrences``, one linear pass over the text), and a file over the ``SceneLimit`` node
bound is refused before either reader transfers it. The walk counts its placements, and the
tessellation its placed geometry, against the same limit.
"""

import mmap
import os
import re

import numpy as np
import pyvista as pv

from ..utils.logging import get_logger
from .scene_limits import SceneLimit

logger = get_logger()

SUPPORTED_EXTENSIONS = {".stp", ".step"}

# Linear deflection as a share of the bounding-box diagonal, and the angular deflection in radians.
RELATIVE_LINEAR_DEFLECTION = 0.002
ANGULAR_DEFLECTION = 0.35

# The colour of a face no part, component or face colour reaches (the renderer's mesh colour).
DEFAULT_RGB = (173, 216, 230)

# NEXT_ASSEMBLY_USAGE_OCCURRENCE(id, name, description, relating, related, ...): the parent and the
# child product definition of one component placement. Quoted strings may hold '' escapes.
_STRING = rb"'(?:[^']|'')*'"
_NAUO = re.compile(rb"NEXT_ASSEMBLY_USAGE_OCCURRENCE\s*\(\s*" + _STRING + rb"\s*,\s*" + _STRING
                   + rb"\s*,\s*(?:" + _STRING + rb"|\$)\s*,\s*#(\d+)\s*,\s*#(\d+)", re.IGNORECASE)


def can_handle(extension: str) -> bool:
    return extension.lower() in SUPPORTED_EXTENSIONS


def load(file_path: str, max_triangles: int = None) -> pv.PolyData:
    """
    Load a STEP file and return a PyVista PolyData object for rendering, with per-cell ``RGB`` when the
    file sets any colour. A file whose assembly places more components than the ``SceneLimit`` node
    bound is refused before it is transferred; the walk and the tessellation stop at that limit and
    at ``max_triangles``.
    """
    ext = os.path.splitext(file_path)[1].lower()
    logger.info(f"Loading CAD file: {file_path} (format: {ext})")

    limit = SceneLimit("STEP assembly", max_triangles)
    if not limit.expect(assembly_occurrences(file_path, limit.max_nodes)):
        raise ValueError(f"STEP assembly places more than {limit.max_nodes:,} components")
    try:
        parts = read_step_parts(file_path, limit)
    except Exception as e:
        if limit.reached:
            # The geometry-only reader would transfer the same assembly with no limit at all.
            raise ValueError("STEP assembly expands past the scene limit before reaching a part") from e
        logger.warning(f"XCAF read failed ({e}); reading geometry only")
        from OCP.TopLoc import TopLoc_Location

        parts = [(read_step_shape(file_path), TopLoc_Location(), None, {})]
    return tessellate_parts(parts, limit)


def assembly_occurrences(file_path: str, max_occurrences: int) -> int:
    """The component placements a STEP file's assembly expands to: every
    ``NEXT_ASSEMBLY_USAGE_OCCURRENCE`` counted once per placement of its parent, read from the
    file's text in one pass. Counting stops at ``max_occurrences + 1``; a cycle counts as that."""
    children = {}
    related = set()
    if os.path.getsize(file_path):
        with open(file_path, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as text:
            for match in _NAUO.finditer(text):
                parent, child = int(match.group(1)), int(match.group(2))
                children.setdefault(parent, []).append(child)
                related.add(child)
    over = max_occurrences + 1
    counted = {}
    total = 0
    for root in children:
        if root not in related:
            total += _occurrences_below(root, children, counted, over)
        if total >= over:
            return over
    if children and not total:
        return over  # every definition is someone's child: the assembly is a cycle
    return total


def _occurrences_below(start, children, counted, over):
    """Placements under the product definition ``start``, at most ``over``, memoised in ``counted``.
    Iterative, so a deep chain cannot exhaust the interpreter's recursion limit; a definition met
    again inside its own subtree is a cycle and counts as ``over``."""
    in_progress = -1
    counted[start] = in_progress
    stack = [[start, 0, 0]]  # definition, next child index, placements so far
    while stack:
        frame = stack[-1]
        definition, index, below = frame
        kids = children.get(definition, ())
        if below >= over or index >= len(kids):
            stack.pop()
            counted[definition] = min(below, over)
            if stack:
                stack[-1][2] += 1 + counted[definition]
            continue
        frame[1] += 1
        child = kids[index]
        known = counted.get(child)
        if known is None:
            counted[child] = in_progress
            stack.append([child, 0, 0])
        elif known == in_progress:
            frame[2] = over
        else:
            frame[2] += 1 + known
    return counted[start]


# --------------------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------------------

def read_step_shape(file_path: str):
    """The file's geometry as one shape, without colours (``STEPControl_Reader``)."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    status = reader.ReadFile(file_path)
    if status != IFSelect_RetDone:
        raise ValueError(f"Failed to read STEP file, status: {status}")
    reader.TransferRoots()
    return reader.OneShape()


def _colour_of(colour_tool, label):
    """The label's surface or generic colour as sRGB uint8, or None."""
    from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB
    from OCP.XCAFDoc import XCAFDoc_ColorGen, XCAFDoc_ColorSurf, XCAFDoc_ColorTool

    colour = Quantity_Color()
    for kind in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
        if XCAFDoc_ColorTool.GetColor_s(label, kind, colour):
            values = colour.Values(Quantity_TOC_sRGB)
            return tuple(int(round(min(max(v, 0.0), 1.0) * 255)) for v in values)
    return None


def face_map(shape):
    """The shape's faces, indexed 1..n in a stable order (``TopExp.MapShapes``)."""
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, faces)
    return faces


def read_step_parts(file_path: str, limit: SceneLimit = None):
    """The file's parts as ``(shape, location, colour, face_colours)``: each part's unplaced shape,
    the placement composed from every assembly level above it, the colour inherited from the nearest
    coloured component or part, and the colours its own sub-shapes (faces, or solids/shells holding
    faces) set, keyed by the face's index in ``face_map(shape)``. The walk stops at ``limit``."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDF import TDF_Label, TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool

    document = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    reader.SetLayerMode(False)
    if reader.ReadFile(file_path) != IFSelect_RetDone:
        raise ValueError("STEPCAFControl_Reader could not read the file")
    if not reader.Transfer(document):
        raise ValueError("STEPCAFControl_Reader could not transfer the file")

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    colour_tool = XCAFDoc_DocumentTool.ColorTool_s(document.Main())
    limit = limit if limit is not None else SceneLimit("STEP assembly")
    parts = []

    def walk(label, location, inherited, depth):
        if depth > 64:
            logger.warning("STEP assembly nesting deeper than 64 levels; skipped below")
            return
        colour = _colour_of(colour_tool, label) or inherited
        if XCAFDoc_ShapeTool.IsReference_s(label):
            if not limit.visit():
                return
            referred = TDF_Label()
            XCAFDoc_ShapeTool.GetReferredShape_s(label, referred)
            walk(referred, location.Multiplied(XCAFDoc_ShapeTool.GetLocation_s(label)), colour, depth + 1)
            return
        if XCAFDoc_ShapeTool.IsAssembly_s(label):
            components = TDF_LabelSequence()
            XCAFDoc_ShapeTool.GetComponents_s(label, components, False)
            for index in range(1, components.Length() + 1):
                if limit.reached:
                    return
                walk(components.Value(index), location, colour, depth + 1)
            return
        shape = XCAFDoc_ShapeTool.GetShape_s(label)
        if shape.IsNull():
            return
        face_colours = {}
        sub_labels = TDF_LabelSequence()
        XCAFDoc_ShapeTool.GetSubShapes_s(label, sub_labels)
        faces = face_map(shape) if sub_labels.Length() else None
        for index in range(1, sub_labels.Length() + 1):
            sub = sub_labels.Value(index)
            sub_colour = _colour_of(colour_tool, sub)
            if sub_colour is None:
                continue
            explorer = TopExp_Explorer(XCAFDoc_ShapeTool.GetShape_s(sub), TopAbs_FACE)
            while explorer.More():
                face_index = faces.FindIndex(explorer.Current())
                if face_index:
                    face_colours.setdefault(face_index, sub_colour)
                explorer.Next()
        parts.append((shape, location, colour, face_colours))

    free = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free)
    for index in range(1, free.Length() + 1):
        if limit.reached:
            break
        walk(free.Value(index), TopLoc_Location(), None, 0)
    if not parts:
        raise ValueError("No shapes found in STEP file")
    logger.info(f"Read STEP through XCAF: {len(parts)} part(s), "
                f"{sum(1 for _, _, c, f in parts if c or f)} with colours")
    return parts


# --------------------------------------------------------------------------------------------------
# Tessellation
# --------------------------------------------------------------------------------------------------

def _diagonal(parts) -> float:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    for shape, location, _, _ in parts:
        BRepBndLib.Add_s(shape.Moved(location) if not location.IsIdentity() else shape, box, True)
    if box.IsVoid():
        return 0.0
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return float(np.linalg.norm([xmax - xmin, ymax - ymin, zmax - zmin]))


def linear_deflection(diagonal: float) -> float:
    """The tessellation's linear deflection for a model of this bounding-box diagonal."""
    return max(diagonal * RELATIVE_LINEAR_DEFLECTION, 1e-7)


def _transform_matrix(location):
    """A 3x4 matrix of the location's transformation (rotation | translation)."""
    trsf = location.Transformation()
    return np.array([[trsf.Value(row, column) for column in range(1, 5)] for row in range(1, 4)],
                    dtype=np.float64)


def face_triangles(face):
    """(points, triangles) of a meshed face in its shape's coordinates (the face's own location
    applied), wound by the face's orientation, or None when the face has no triangulation."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopLoc import TopLoc_Location

    location = TopLoc_Location()
    triangulation = BRep_Tool.Triangulation_s(face, location)
    if triangulation is None or triangulation.NbTriangles() == 0:
        return None
    n_nodes = triangulation.NbNodes()
    nodes = np.array([triangulation.Node(i).Coord() for i in range(1, n_nodes + 1)], dtype=np.float64)
    triangles = np.array([triangulation.Triangle(i).Get() for i in range(1, triangulation.NbTriangles() + 1)],
                         dtype=np.int64) - 1
    if not location.IsIdentity():
        matrix = _transform_matrix(location)
        nodes = nodes @ matrix[:, :3].T + matrix[:, 3]
    if face.Orientation() == TopAbs_REVERSED:
        triangles = triangles[:, ::-1]
    return nodes, triangles


def tessellate_parts(parts, limit: SceneLimit = None) -> pv.PolyData:
    """One PolyData of every face of every part, each part placed by its location, with per-cell RGB
    when any colour is set. A part instanced several times is meshed once (the mesh lives on the
    shared shape) and its triangles are placed per instance, until ``limit`` is reached."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopoDS import TopoDS

    limit = limit if limit is not None else SceneLimit("STEP assembly")
    deflection = linear_deflection(_diagonal(parts))
    logger.info(f"Tessellating with linear deflection {deflection:.6g} (diagonal x "
                f"{RELATIVE_LINEAR_DEFLECTION}) and angular deflection {ANGULAR_DEFLECTION} rad")

    coloured = any(colour or face_colours for _, _, colour, face_colours in parts)
    points, cells, colours = [], [], []
    offset = 0
    for shape, location, colour, face_colours in parts:
        if limit.reached:
            break
        BRepMesh_IncrementalMesh(shape, deflection, False, ANGULAR_DEFLECTION, True)
        matrix = None if location.IsIdentity() else _transform_matrix(location)
        faces = face_map(shape)
        for index in range(1, faces.Extent() + 1):
            if limit.reached:
                break
            meshed = face_triangles(TopoDS.Face_s(faces.FindKey(index)))
            if meshed is None:
                continue
            nodes, triangles = meshed
            if matrix is not None:
                nodes = nodes @ matrix[:, :3].T + matrix[:, 3]
            points.append(nodes)
            cells.append(triangles + offset)
            offset += len(nodes)
            if coloured:
                rgb = face_colours.get(index) or colour or DEFAULT_RGB
                colours.append(np.tile(np.asarray(rgb, dtype=np.uint8), (len(triangles), 1)))
            limit.add(len(nodes), len(triangles))

    if not points:
        raise ValueError("No geometry found in STEP file after tessellation")
    vertices = np.vstack(points)
    triangles = np.vstack(cells)
    pv_faces = np.column_stack([np.full(len(triangles), 3, dtype=np.int64), triangles]).ravel()
    mesh = pv.PolyData(vertices, pv_faces)
    if coloured:
        mesh.cell_data["RGB"] = np.vstack(colours)
    logger.info(f"Tessellated CAD model: {len(vertices)} vertices, {len(triangles)} faces"
                f"{', coloured' if coloured else ''}")
    return mesh
