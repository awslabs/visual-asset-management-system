# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""3D Tiles tilesets (a `.json` whose root carries `asset` and `geometricError`): geometric error, bounding
volume and tile counts -> sys_tiles3d. The attribute text is the analysis input; nothing local renders."""

import json
import math
import os
from typing import Dict, List, Optional, Tuple

from .common import CLASS_TILES3D, BranchResult, ExtractContext, human_count, truncate_text

_MAX_TILES_WALKED = 200_000
_BOUNDING_VOLUME_KINDS = ("region", "box", "sphere")
_MAX_PROPERTY_KEYS = 50


def is_tileset(obj) -> bool:
    return isinstance(obj, dict) and isinstance(obj.get("asset"), dict) and "geometricError" in obj


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def region_of(bounding_volume) -> Tuple[Optional[List[float]], Optional[str]]:
    """(`region` as six floats `[west, south, east, north, minHeight, maxHeight]` -- radians and metres, as 3D
    Tiles stores them -- or None, warning or None). A box or sphere volume has no region and no warning; a
    `region` member that is not six finite numbers is recorded as null with the reason."""
    region = bounding_volume.get("region") if isinstance(bounding_volume, dict) else None
    if region is None:
        return None, None
    if isinstance(region, list) and len(region) == 6 and all(_is_number(value) for value in region):
        return [float(value) for value in region], None
    return None, f"Root boundingVolume.region {region!r} is not six finite numbers; region recorded as null"


def walk_tiles(root: dict) -> Dict[str, object]:
    """Tile count, depth, content counts and content formats over the in-file tile tree. External tilesets
    (`.json` contents) are counted, not followed."""
    if not isinstance(root, dict) or not root:
        return {"tileCount": 0, "maxDepth": 0, "contentCount": 0, "externalTilesets": 0,
                "contentFormats": {}, "refine": [], "walkCapped": False}
    stack = [(root, 1)]
    count = depth = contents = external = 0
    refine = set()
    formats: Dict[str, int] = {}
    capped = False
    while stack:
        if count >= _MAX_TILES_WALKED:
            capped = True
            break
        tile, level = stack.pop()
        if not isinstance(tile, dict):
            continue
        count += 1
        depth = max(depth, level)
        if tile.get("refine"):
            refine.add(str(tile["refine"]).upper())
        entries = []
        if isinstance(tile.get("content"), dict):
            entries.append(tile["content"])
        if isinstance(tile.get("contents"), list):
            entries.extend(entry for entry in tile["contents"] if isinstance(entry, dict))
        for entry in entries:
            uri = entry.get("uri") or entry.get("url")
            if not uri:
                continue
            contents += 1
            extension = os.path.splitext(str(uri).split("?", 1)[0])[1].lower() or "(none)"
            formats[extension] = formats.get(extension, 0) + 1
            if extension == ".json":
                external += 1
        children = tile.get("children")
        if isinstance(children, list):
            stack.extend((child, level + 1) for child in children)
    return {"tileCount": count, "maxDepth": depth, "contentCount": contents, "externalTilesets": external,
            "contentFormats": formats, "refine": sorted(refine), "walkCapped": capped}


def extract_tiles3d(tileset: dict, ctx: ExtractContext) -> BranchResult:
    result = BranchResult(file_class=CLASS_TILES3D)
    asset = tileset.get("asset") if isinstance(tileset.get("asset"), dict) else {}
    root = tileset.get("root") if isinstance(tileset.get("root"), dict) else {}
    bounding_volume = root.get("boundingVolume") if isinstance(root.get("boundingVolume"), dict) else {}
    properties = tileset.get("properties") if isinstance(tileset.get("properties"), dict) else {}
    sys_tiles3d: Dict[str, object] = {
        "specVersion": asset.get("version"),
        "tilesetVersion": asset.get("tilesetVersion"),
        "geometricError": tileset.get("geometricError"),
        "rootGeometricError": root.get("geometricError"),
        "boundingVolumeType": next((kind for kind in _BOUNDING_VOLUME_KINDS if kind in bounding_volume), None),
        "boundingVolume": bounding_volume or None,
        "hasRootTransform": isinstance(root.get("transform"), list),
        "extensionsUsed": tileset.get("extensionsUsed") or None,
        "extensionsRequired": tileset.get("extensionsRequired") or None,
        "propertyKeys": sorted(properties.keys())[:_MAX_PROPERTY_KEYS] or None,
    }
    sys_tiles3d.update(walk_tiles(root))
    sys_tiles3d = {key: value for key, value in sys_tiles3d.items() if value is not None}
    # `region` is a promotion source (metadataCatalog reads it) and is always present: a list of six floats or null.
    region, region_warning = region_of(bounding_volume)
    sys_tiles3d["region"] = region
    if region_warning:
        result.warnings.append(region_warning)
    result.attributes["sys_tiles3d"] = sys_tiles3d
    result.facts["tiles"] = human_count(int(sys_tiles3d.get("tileCount", 0)), "tile")
    geometric_error = sys_tiles3d.get("geometricError")
    if isinstance(geometric_error, (int, float)):
        result.facts["geometricError"] = f"{geometric_error:g}"
    elif geometric_error is not None:
        result.facts["geometricError"] = str(geometric_error)
    if sys_tiles3d.get("boundingVolumeType"):
        result.facts["boundingVolume"] = str(sys_tiles3d["boundingVolumeType"])
    if asset.get("version"):
        result.facts["tilesFormat"] = f"3D Tiles {asset['version']}"
    summary = {
        "asset": asset,
        "geometricError": tileset.get("geometricError"),
        "root": {key: root[key] for key in ("boundingVolume", "geometricError", "refine") if key in root},
        "extensionsUsed": tileset.get("extensionsUsed"),
    }
    result.text_excerpt = truncate_text(json.dumps(summary, default=str), ctx.max_text_chars)
    return result
