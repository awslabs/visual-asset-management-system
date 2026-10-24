# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Analysis of one local 3D file: load it with the thumbnail pipeline's format handlers, extract
``sys_*`` attributes, and render still frames for the vision model.

Extensions fall into three groups. Renderable ones load to a PolyData and are rendered unless the
caller asks for attributes only. Attributes-only ones (2D DXF, IFC, Gaussian-splat containers) have
no renderable geometry here and report ``renderSkipped: "unsupported"``. Mesh containers no library
in this image reads yield file attributes only, also ``unsupported``. A render fault after a successful
load degrades to attributes-only with ``renderSkipped: "error"``; a load fault propagates.
"""

import dataclasses
import os
from typing import Dict, List, Optional

import numpy as np

from .. import core, renderer
from ..format_handlers import cad_handler, mesh_handler, pointcloud_handler, usd_handler
from ..utils.logging import get_logger
from . import attributes, display, headers

logger = get_logger()

RENDER_SKIPPED_UNSUPPORTED = "unsupported"
RENDER_SKIPPED_ERROR = "error"

# trimesh reads these (.3mf through lxml + networkx); DracoPy decodes .drc.
MESH_EXTENSIONS = {".ply", ".stl", ".obj", ".glb", ".gltf", ".drc", ".off", ".3mf"}
POINTCLOUD_EXTENSIONS = {".las", ".laz", ".e57", ".ptx", ".pcd", ".xyz"}
CAD_EXTENSIONS = set(cad_handler.SHAPE_EXTENSIONS)
USD_EXTENSIONS = set(usd_handler.SUPPORTED_EXTENSIONS)
RENDERABLE_EXTENSIONS = MESH_EXTENSIONS | POINTCLOUD_EXTENSIONS | CAD_EXTENSIONS | USD_EXTENSIONS

DXF_EXTENSIONS = {".dxf"}
IFC_EXTENSIONS = {".ifc", ".ifczip"}
SPLAT_EXTENSIONS = {".spz", ".sog", ".splat", ".lcc"}
ATTRIBUTES_ONLY_EXTENSIONS = DXF_EXTENSIONS | IFC_EXTENSIONS | SPLAT_EXTENSIONS

# Mesh containers no library in this image reads: file attributes only.
UNLOADABLE_MESH_EXTENSIONS = {".3ds", ".wrl", ".amf", ".3dm", ".bim"}

# Engineered models are framed on their full bounds rather than the percentile crop.
FULL_BOUNDS_EXTENSIONS = USD_EXTENSIONS | CAD_EXTENSIONS


@dataclasses.dataclass
class AnalysisResult:
    attributes: Dict[str, dict]
    facts: Dict[str, str]
    frames: list
    render_skipped: Optional[str]
    warnings: List[str]
    loader: Optional[str]


def analyze_local_file(
    local_path: str,
    ext: str,
    *,
    render: bool = True,
    n_views: int = renderer.DEFAULT_STILL_VIEWS,
    max_points: int = pointcloud_handler.MAX_POINTS_FOR_RENDER,
    work_dir: Optional[str] = None,
    still_resolution: tuple = renderer.DEFAULT_STILL_RESOLUTION,
) -> AnalysisResult:
    """Attributes, facts and still frames for the file at ``local_path`` (extension ``ext``, taken from
    the file name when empty). ``work_dir`` receives any file the analysis has to unpack."""
    ext = ("." + ext.lower().lstrip(".")) if ext else os.path.splitext(local_path)[1].lower()
    work_dir = work_dir or os.path.dirname(local_path)
    warnings: List[str] = []

    if ext in DXF_EXTENSIONS:
        return _attributes_only(attributes.dxf_attributes(local_path), "ezdxf", warnings)
    if ext in IFC_EXTENSIONS:
        return _attributes_only(attributes.ifc_attributes(local_path, work_dir), "ifcopenshell", warnings)
    if ext in SPLAT_EXTENSIONS:
        if ext == ".lcc":
            warnings.append("The .lcc container is proprietary and carries no readable header; "
                            "file attributes only")
            return _attributes_only({}, None, warnings)
        return _attributes_only(attributes.splat_attributes(ext, local_path), "header", warnings)

    ply_header = None
    if ext == ".ply":
        ply_header = headers.read_ply_header(local_path)
        if headers.ply_kind(ply_header) == "splat":
            return _attributes_only(attributes.splat_attributes(".ply", local_path), "header", warnings)

    if ext in UNLOADABLE_MESH_EXTENSIONS or ext not in RENDERABLE_EXTENSIONS:
        warnings.append(f"No loader for {ext} in the 3D render image; file attributes only")
        return _attributes_only({}, None, warnings)

    pv_data, attrs, loader = _load_and_extract(local_path, ext, max_points, ply_header)

    frames: list = []
    render_skipped = None
    if render:
        try:
            display.ensure_display()
            pv_data = core.normalize_up_axis(pv_data, ext)
            frames = renderer.generate_still_frames(
                pv_data, n_views=n_views, resolution=still_resolution,
                use_full_bounds=ext in FULL_BOUNDS_EXTENSIONS)
        except Exception as exc:
            # A render fault leaves the attributes standing; the manifest records the skip.
            logger.exception(f"Render failed for {local_path}: {exc}")
            warnings.append(f"Render failed: {exc}")
            frames = []
            render_skipped = RENDER_SKIPPED_ERROR

    attrs = attributes.json_safe(attrs)
    return AnalysisResult(attributes=attrs, facts=attributes.facts_for(attrs), frames=frames,
                          render_skipped=render_skipped, warnings=warnings, loader=loader)


def _attributes_only(attrs: dict, loader: Optional[str], warnings: List[str]) -> AnalysisResult:
    attrs = attributes.json_safe(attrs)
    return AnalysisResult(attributes=attrs, facts=attributes.facts_for(attrs), frames=[],
                          render_skipped=RENDER_SKIPPED_UNSUPPORTED, warnings=warnings, loader=loader)


def _load_and_extract(local_path: str, ext: str, max_points: int, ply_header):
    """``(pv_data, attributes, loader)`` for a renderable extension."""
    if ext in CAD_EXTENSIONS:
        shape = cad_handler.load_shape(local_path)
        pv_data = cad_handler.tessellate_shape(shape)
        return pv_data, attributes.cad_shape_attributes(shape, ext, local_path, pv_data), "OCP"

    if ext in POINTCLOUD_EXTENSIONS:
        header = attributes.pointcloud_header(ext, local_path)
        points, colors = pointcloud_handler.load_points(local_path, max_points=max_points)
        pv_data = pointcloud_handler.to_polydata(points, colors)
        loader = pointcloud_handler.LOADERS[ext]
        return pv_data, attributes.pointcloud_attributes(ext, points, colors, header, loader), loader

    if ext in USD_EXTENSIONS:
        pv_data = usd_handler.load(local_path)
        attrs = attributes.merge_groups(attributes.polydata_attributes(pv_data, ext, "usd-core"),
                                        attributes.usd_scene_attributes(local_path))
        return pv_data, attrs, "usd-core"

    pv_data = mesh_handler.load(local_path)
    loader = "DracoPy" if ext == ".drc" else "trimesh"
    if ply_header is not None and headers.ply_kind(ply_header) == "pointcloud":
        # A PLY point cloud is bounded like the other clouds and described as one.
        points = np.asarray(pv_data.points, dtype=np.float64).reshape(-1, 3)
        colors = pv_data.point_data["RGB"] if "RGB" in pv_data.point_data else None
        points, colors = pointcloud_handler.cap_points(points, colors, max_points)
        pv_data = pointcloud_handler.to_polydata(points, colors)
        vertex = (ply_header.get("elements") or {}).get("vertex") or {}
        attrs = attributes.pointcloud_attributes(
            ".ply", points, colors,
            {"points": vertex.get("count", len(points)), "details": {"plyFormat": ply_header.get("format")}},
            loader)
        return pv_data, attrs, loader

    return pv_data, attributes.polydata_attributes(pv_data, ext, loader), loader
