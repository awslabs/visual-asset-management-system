# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
PLY header parsing and content routing.

A ``.ply`` file can hold a mesh, a point cloud or a 3D Gaussian Splatting scene. The header decides:

* a ``vertex`` element with ``f_dc_0``, ``opacity``, ``scale_*`` and ``rot_*`` -> Gaussian splats;
* a ``face`` element with at least one face -> mesh;
* otherwise (vertices only) -> point cloud.

A binary PLY's vertex block is exposed as a structured ``np.memmap`` so a reader takes only the
columns and rows it needs.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

KIND_SPLAT = "splat"
KIND_COMPRESSED_SPLAT = "compressed_splat"
KIND_MESH = "mesh"
KIND_POINTS = "points"

MAX_HEADER_BYTES = 1024 * 1024

_TYPES = {
    "char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1",
    "short": "i2", "int16": "i2", "ushort": "u2", "uint16": "u2",
    "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
    "float": "f4", "float32": "f4", "double": "f8", "float64": "f8",
}

_SPLAT_PROPERTIES = ("f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2",
                     "rot_0", "rot_1", "rot_2", "rot_3")


@dataclass
class Element:
    name: str
    count: int
    properties: List[Tuple[str, str]] = field(default_factory=list)  # (name, numpy type code)
    has_list: bool = False


@dataclass
class PlyHeader:
    format: str
    elements: List[Element]
    header_bytes: int

    def element(self, name: str) -> Optional[Element]:
        return next((element for element in self.elements if element.name == name), None)

    @property
    def kind(self) -> str:
        vertex = self.element("vertex")
        names = {name for name, _ in vertex.properties} if vertex else set()
        if vertex and set(_SPLAT_PROPERTIES) <= names:
            return KIND_SPLAT
        if vertex and self.element("chunk") and "packed_position" in names:
            return KIND_COMPRESSED_SPLAT
        face = self.element("face")
        if face and face.count > 0:
            return KIND_MESH
        return KIND_POINTS

    def vertex_dtype(self) -> np.dtype:
        vertex = self.element("vertex")
        order = ">" if self.format == "binary_big_endian" else "<"
        return np.dtype([(name, order + code) for name, code in vertex.properties])


def read_header(path: str) -> PlyHeader:
    """The header of a PLY file. Raises ValueError when the file is not a PLY."""
    with open(path, "rb") as handle:
        if handle.read(4).rstrip(b"\r\n") != b"ply":
            raise ValueError("not a PLY file")
        handle.seek(0)
        raw = b""
        while True:
            line = handle.readline()
            if not line:
                raise ValueError("PLY header has no end_header")
            raw += line
            if len(raw) > MAX_HEADER_BYTES:
                raise ValueError("PLY header is too long")
            if line.strip() == b"end_header":
                break
    fmt, elements = None, []
    for line in raw.decode("ascii", errors="replace").splitlines():
        tokens = line.split()
        if not tokens:
            continue
        if tokens[0] == "format" and len(tokens) >= 2:
            fmt = tokens[1]
        elif tokens[0] == "element" and len(tokens) >= 3:
            elements.append(Element(tokens[1], int(tokens[2])))
        elif tokens[0] == "property" and elements:
            if len(tokens) >= 5 and tokens[1] == "list":
                elements[-1].has_list = True
                elements[-1].properties.append((tokens[4], "list"))
            elif len(tokens) >= 3 and tokens[1] in _TYPES:
                elements[-1].properties.append((tokens[2], _TYPES[tokens[1]]))
    if fmt not in ("ascii", "binary_little_endian", "binary_big_endian"):
        raise ValueError(f"unsupported PLY format: {fmt}")
    return PlyHeader(fmt, elements, len(raw))


def ply_kind(path: str) -> str:
    """splat, mesh or points, from the header; mesh when the header cannot be read."""
    try:
        return read_header(path).kind
    except (OSError, ValueError):
        return KIND_MESH


def vertex_memmap(path: str, header: PlyHeader) -> np.memmap:
    """The vertex block of a binary PLY whose vertex element is first and has no list properties."""
    vertex = header.element("vertex")
    if header.format == "ascii" or vertex is None or vertex.has_list or header.elements[0] is not vertex:
        raise ValueError("vertex block cannot be memory-mapped")
    return np.memmap(path, dtype=header.vertex_dtype(), mode="r", offset=header.header_bytes,
                     shape=(vertex.count,))
