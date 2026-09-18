#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""File classification for the SYSTEM GenAI metadata pipeline.

``ALLOW_LIST`` is every ``supportedExtensions`` entry of every viewer with ``enabled: true`` in
``web/src/visualizerPlugin/config/viewerConfig.json`` — viewers gated by ``featuresEnabledRestriction``
included, the preview viewer's ``"*"`` wildcard excluded — minus ``EXCLUDED_EXTENSIONS``, plus
``ADDITIONAL_EXTENSIONS`` (office formats no viewer renders, admitted for their text). It is stored
here as an explicit sorted list because a Lambda cannot read the web catalog at run time; the test
suite re-derives it from the catalog and fails when the two drift.

``classify`` maps a lower-cased dotted extension to ``(fileClass, renderBranch)``. Two extensions are
ambiguous by extension alone and are decided from the object's leading bytes: a ``.ply`` is a mesh
(``element face`` above zero), a Gaussian splat (an ``f_dc_0`` property) or a point cloud; a
``.json`` is a 3D Tiles tileset (root ``asset`` + ``geometricError``), a GeoJSON document (root
``type`` in ``GEOJSON_ROOT_TYPES`` carrying that type's member — ``coordinates``, ``geometries``,
``geometry`` or ``features`` — classified ``data``; the media branch writes ``sys_geo`` for it; a
bare ``"type": "Point"`` is text), text, or ``other`` when it is not UTF-8. The media branch applies the same four ``.json``
outcomes when it reads the file in full, so the state and the manifest agree. ``other`` is the
fallback for a sniff that fits no class and is never an allow-list entry.

``FILE_CLASS_PHRASES`` names each class the way a user asks for it (``3D model (mesh)``,
``video (footage)``); the embedding step puts the phrase in every file's source text, and the
backend's query-side intent module carries the same dict.
"""

import json
import struct
from typing import Callable, Dict, List, Optional, Set, Tuple

CLASS_IMAGE = "image"
CLASS_VIDEO = "video"
CLASS_AUDIO = "audio"
CLASS_DOCUMENT = "document"
CLASS_TEXT = "text"
CLASS_DATA = "data"
CLASS_TILES3D = "tiles3d"
CLASS_MESH = "mesh"
CLASS_USD = "usd"
CLASS_CAD = "cad"
CLASS_POINTCLOUD = "pointcloud"
CLASS_SPLAT = "splat"
CLASS_IFC = "ifc"
CLASS_OTHER = "other"
FILE_CLASSES = (CLASS_IMAGE, CLASS_VIDEO, CLASS_AUDIO, CLASS_DOCUMENT, CLASS_TEXT, CLASS_DATA,
                CLASS_TILES3D, CLASS_MESH, CLASS_USD, CLASS_CAD, CLASS_POINTCLOUD, CLASS_SPLAT,
                CLASS_IFC, CLASS_OTHER)

# The words a user types when asking for a kind of file, per class. Embedded in every file's source
# text and matched on the query side, so a type word in a search favours files of that kind.
FILE_CLASS_PHRASES: Dict[str, str] = {
    CLASS_IMAGE: "image (photo or picture)",
    CLASS_VIDEO: "video (footage)",
    CLASS_AUDIO: "audio recording",
    CLASS_DOCUMENT: "document (PDF or office)",
    CLASS_TEXT: "text file",
    CLASS_DATA: "data table (spreadsheet)",
    CLASS_TILES3D: "3D Tiles tileset",
    CLASS_MESH: "3D model (mesh)",
    CLASS_USD: "3D model (USD scene)",
    CLASS_CAD: "CAD model",
    CLASS_POINTCLOUD: "point cloud (LiDAR scan)",
    CLASS_SPLAT: "3D Gaussian splat",
    CLASS_IFC: "BIM building model (IFC)",
    CLASS_OTHER: "file",
}

BRANCH_BLENDER = "BLENDER"
BRANCH_RENDER3D = "RENDER3D"
BRANCH_MEDIA = "MEDIA"
BRANCH_FARGATE = "FARGATE"
BRANCH_NONE = "NONE"
RENDER_BRANCHES = (BRANCH_BLENDER, BRANCH_RENDER3D, BRANCH_MEDIA, BRANCH_FARGATE, BRANCH_NONE)

# Viewer extensions deliberately withheld from the pipeline. Empty at release.
EXCLUDED_EXTENSIONS: Set[str] = set()

# Office formats no viewer renders, admitted to the allow list for their text: document, data and document
# on the MEDIA branch, never rasterised.
ADDITIONAL_EXTENSIONS = (".docx", ".xlsx", ".pptx")

# Leading bytes the two sniffed formats are decided from. A PLY header and a tileset root fit in
# this window; a .json whose root does not fit is text as far as this pipeline is concerned.
SNIFF_BYTES = 65536

# Proprietary CAD formats no open library reads: attributes are sys_file only and no branch runs.
PROPRIETARY_CAD_EXTENSIONS = (".asm", ".catpart", ".catproduct", ".iam", ".ipt", ".jt", ".par",
                              ".prt", ".sldasm", ".sldprt", ".x_b", ".x_t")

ALLOW_LIST: List[str] = [
    ".3dm", ".3ds", ".3mf", ".aac", ".amf", ".asm", ".avi", ".bim", ".brep", ".catpart",
    ".catproduct", ".cfg", ".csv", ".dae", ".docx", ".e57", ".fbx", ".fcs", ".flac", ".flv", ".gif",
    ".glb", ".gltf", ".htm", ".html", ".iam", ".ifc", ".ifczip", ".iges", ".igs", ".inf", ".ini",
    ".ipt", ".ipynb", ".jpeg", ".jpg", ".js", ".json", ".jt", ".las", ".laz", ".lcc", ".log", ".m4a",
    ".m4v", ".md", ".mkv", ".mov", ".mp3", ".mp4", ".obj", ".off", ".ogg", ".par", ".pdf", ".ply",
    ".png", ".pptx", ".prt", ".ps1", ".py", ".sh", ".sldasm", ".sldprt", ".sog", ".splat", ".spz",
    ".sql", ".step", ".stl", ".stp", ".svg", ".toml", ".ts", ".txt", ".usd", ".usda", ".usdc", ".usdz",
    ".wav", ".webm", ".wmv", ".wrl", ".x_b", ".x_t", ".xlsx", ".xml", ".yaml", ".yml",
]

# Extension -> (fileClass, renderBranch) for every allow-listed extension the extension alone
# decides. .ply and .json are absent on purpose: classify() sniffs them.
EXTENSION_CLASSES: Dict[str, Tuple[str, str]] = {}
for _extension in (".png", ".jpg", ".jpeg", ".gif", ".svg"):
    EXTENSION_CLASSES[_extension] = (CLASS_IMAGE, BRANCH_MEDIA)
for _extension in (".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".m4v"):
    EXTENSION_CLASSES[_extension] = (CLASS_VIDEO, BRANCH_MEDIA)
for _extension in (".mp3", ".wav", ".ogg", ".aac", ".flac", ".m4a"):
    EXTENSION_CLASSES[_extension] = (CLASS_AUDIO, BRANCH_MEDIA)
EXTENSION_CLASSES[".pdf"] = (CLASS_DOCUMENT, BRANCH_MEDIA)
for _extension in (".docx", ".pptx"):
    EXTENSION_CLASSES[_extension] = (CLASS_DOCUMENT, BRANCH_MEDIA)
for _extension in (".txt", ".md", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".inf", ".log",
                   ".py", ".js", ".ts", ".sql", ".sh", ".ps1", ".ipynb", ".html", ".htm"):
    EXTENSION_CLASSES[_extension] = (CLASS_TEXT, BRANCH_MEDIA)
for _extension in (".csv", ".fcs", ".xlsx"):
    EXTENSION_CLASSES[_extension] = (CLASS_DATA, BRANCH_MEDIA)
for _extension in (".glb", ".gltf", ".fbx", ".obj", ".dae", ".stl"):
    EXTENSION_CLASSES[_extension] = (CLASS_MESH, BRANCH_BLENDER)
for _extension in (".3ds", ".3mf", ".wrl", ".off", ".amf", ".3dm", ".bim"):
    EXTENSION_CLASSES[_extension] = (CLASS_MESH, BRANCH_RENDER3D)
for _extension in (".usd", ".usda", ".usdc", ".usdz"):
    EXTENSION_CLASSES[_extension] = (CLASS_USD, BRANCH_BLENDER)
for _extension in (".stp", ".step", ".iges", ".igs", ".brep"):
    EXTENSION_CLASSES[_extension] = (CLASS_CAD, BRANCH_RENDER3D)
for _extension in PROPRIETARY_CAD_EXTENSIONS:
    EXTENSION_CLASSES[_extension] = (CLASS_CAD, BRANCH_NONE)
for _extension in (".las", ".laz", ".e57"):
    EXTENSION_CLASSES[_extension] = (CLASS_POINTCLOUD, BRANCH_RENDER3D)
for _extension in (".spz", ".sog", ".splat", ".lcc"):
    EXTENSION_CLASSES[_extension] = (CLASS_SPLAT, BRANCH_RENDER3D)
for _extension in (".ifc", ".ifczip"):
    EXTENSION_CLASSES[_extension] = (CLASS_IFC, BRANCH_RENDER3D)
del _extension

# .ply -> mesh | splat | pointcloud | other; .json -> tiles3d | data (GeoJSON) | text | other. The
# media branch re-derives the .json outcome from the whole file with the same rules.
SNIFFED_EXTENSIONS = (".ply", ".json")

# Root "type" values that make a .json a GeoJSON document (RFC 7946 §1.4): the seven geometry types
# plus Feature and FeatureCollection. Consulted by the media branch's text extractor as well.
GEOJSON_ROOT_TYPES = frozenset({"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon",
                                "MultiPolygon", "GeometryCollection", "Feature", "FeatureCollection"})
_GEOJSON_HEAD_MARKERS = ('"type": "FeatureCollection"', '"type":"FeatureCollection"',
                         '"type": "Feature"', '"type":"Feature"')
# The member a GeoJSON root carries beside its type (RFC 7946 §3); a bare type word is not GeoJSON.
_GEOJSON_MEMBER_KEYS = {"FeatureCollection": "features", "Feature": "geometry", "GeometryCollection": "geometries"}


def parse_ply_header(header_bytes: bytes) -> dict:
    """``{"elements": {name: count}, "properties": {name}}`` from a PLY header, ``{}`` when the bytes
    are not a PLY header or ``end_header`` is not within them."""
    text = (header_bytes or b"").decode("ascii", errors="ignore")
    if not text.startswith("ply"):
        return {}
    end = text.find("end_header")
    if end < 0:
        return {}
    elements: Dict[str, int] = {}
    properties: Set[str] = set()
    for line in text[:end].splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "element":
            try:
                elements[parts[1]] = int(parts[2])
            except ValueError:
                elements[parts[1]] = 0
        elif len(parts) >= 3 and parts[0] == "property":
            properties.add(parts[-1])
    return {"elements": elements, "properties": properties}


def classify_ply(header_bytes: bytes) -> Tuple[str, str]:
    header = parse_ply_header(header_bytes)
    if not header:
        return CLASS_OTHER, BRANCH_NONE
    if header["elements"].get("face", 0) > 0:
        return CLASS_MESH, BRANCH_BLENDER
    if "f_dc_0" in header["properties"]:
        return CLASS_SPLAT, BRANCH_RENDER3D
    return CLASS_POINTCLOUD, BRANCH_RENDER3D


def classify_json(head_bytes: bytes) -> Tuple[str, str]:
    try:
        text = (head_bytes or b"").decode("utf-8")
    except UnicodeDecodeError:
        return CLASS_OTHER, BRANCH_NONE
    try:
        root = json.loads(text)
    except ValueError:
        # Not a complete document within the sniff window. A tileset root names both keys early; a
        # GeoJSON collection or feature names its type first.
        if '"asset"' in text and '"geometricError"' in text:
            return CLASS_TILES3D, BRANCH_MEDIA
        if any(marker in text for marker in _GEOJSON_HEAD_MARKERS):
            return CLASS_DATA, BRANCH_MEDIA
        return CLASS_TEXT, BRANCH_MEDIA
    if isinstance(root, dict) and "asset" in root and "geometricError" in root:
        return CLASS_TILES3D, BRANCH_MEDIA
    if isinstance(root, dict) and root.get("type") in GEOJSON_ROOT_TYPES:
        if _GEOJSON_MEMBER_KEYS.get(root["type"], "coordinates") in root:
            return CLASS_DATA, BRANCH_MEDIA
    return CLASS_TEXT, BRANCH_MEDIA


def classify(extension: str, sniff: Callable[[int], bytes]) -> Tuple[str, str]:
    """``(fileClass, renderBranch)`` for an extension. ``sniff(nbytes)`` returns the object's first
    ``nbytes`` bytes and is called only for the extensions the header decides."""
    ext = (extension or "").lower()
    if ext in EXTENSION_CLASSES:
        return EXTENSION_CLASSES[ext]
    if ext == ".ply":
        return classify_ply(sniff(SNIFF_BYTES))
    if ext == ".json":
        return classify_json(sniff(SNIFF_BYTES))
    return CLASS_OTHER, BRANCH_NONE


def las_point_count(header_bytes: bytes) -> Optional[int]:
    """The point record count from a LAS/LAZ public header: the LAS 1.4 64-bit count when present
    and non-zero, else the legacy 32-bit count. ``None`` when the bytes are not a LAS header."""
    data = header_bytes or b""
    if len(data) < 111 or data[:4] != b"LASF":
        return None
    legacy = struct.unpack_from("<I", data, 107)[0]
    version_minor = data[25]
    if version_minor >= 4 and len(data) >= 255:
        extended = struct.unpack_from("<Q", data, 247)[0]
        if extended:
            return int(extended)
    return int(legacy)


def point_count_from_header(extension: str, header_bytes: bytes) -> Optional[int]:
    """A point count the header states, for the point-cloud size gate. E57 keeps its counts in a
    trailing XML section, so it yields ``None`` and the render branch's own cap applies."""
    ext = (extension or "").lower()
    if ext in (".las", ".laz"):
        return las_point_count(header_bytes)
    if ext == ".ply":
        header = parse_ply_header(header_bytes)
        if header and "vertex" in header["elements"]:
            return int(header["elements"]["vertex"])
    return None
