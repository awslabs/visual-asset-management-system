# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
XGRIDS LCC scene descriptions: the level of detail a thumbnail reads, and the data files that level
needs. Pure Python (no pyvista), shared by the referenced-file fetch and the splat handler.

* An ``.lcc`` (LCC 1) scene keeps every level in one ``data.bin`` beside a ``meta.lcc`` JSON whose
  ``splats`` array holds the splat count per level, LOD 0 (the finest) first.
* An ``.lcc2`` (LCC 2) scene is a JSON octree whose ``root.splatFiles`` lists chunk files (``.sog``,
  ``.spz`` or ``.ply``, usually under ``data/3dgs/``); each node's ``data.3dgs.name`` indexes that
  list and its LOD is ``totalLevels - depth`` (the deepest nodes are LOD 0). The older field names
  (``total_splats``, ``lod_3dgs_info``, ``lod_level``, ``root.files``, ``child_num``) are read too, as
  splat-transform reads them.

A thumbnail reads one level: the finest whose splat count is at most ``LOD_SPLAT_FACTOR`` times the
splat budget, else the coarsest. For an LCC 2 scene only that level's chunk files are fetched.

The description is untrusted input (ARCC BSC1): it is read up to a size limit, and a chunk file name
is accepted only as a relative path that stays beside the scene; the caller resolves it against the
asset with the referenced-file rules and refuses the conversion when any name is not accepted.
"""

import json
import os
from typing import Dict, List, Optional

# A scene description is a JSON document; more than this is not one.
MAX_META_BYTES = 64 * 1024 ** 2
# The splat budget used when the caller names none (splat_handler.DEFAULT_MAX_SPLATS).
DEFAULT_MAX_SPLATS = 4_000_000
# A level with up to this many times the splat budget is read whole and sampled down to the budget.
LOD_SPLAT_FACTOR = 2
# Octree nodes walked before the description is refused.
MAX_NODES = 1_000_000


class LccMetaError(ValueError):
    pass


def _strip_trailing_commas(text: str) -> str:
    """Drop commas before ``}`` or ``]`` outside string literals (some LCC 2 exporters write them)."""
    out, in_string, escaped, pending = [], False, False, None
    for ch in text:
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if pending is not None:
            if ch.isspace():
                pending.append(ch)
                continue
            if ch not in "}]":
                out.append(",")
            out.extend(pending)
            pending = None
        if ch == ",":
            pending = []
            continue
        if ch == '"':
            in_string = True
        out.append(ch)
    if pending is not None:
        out.append(",")
        out.extend(pending)
    return "".join(out)


def read_meta(path: str) -> dict:
    """The scene description as a dict, or LccMetaError."""
    if os.path.getsize(path) > MAX_META_BYTES:
        raise LccMetaError(f"LCC scene description is larger than {MAX_META_BYTES} bytes")
    with open(path, "rb") as handle:
        text = handle.read(MAX_META_BYTES).decode("utf-8", errors="replace")
    try:
        meta = json.loads(text)
    except ValueError:
        try:
            meta = json.loads(_strip_trailing_commas(text))
        except ValueError as e:
            raise LccMetaError(f"LCC scene description is not JSON: {e}")
    if not isinstance(meta, dict):
        raise LccMetaError("LCC scene description is not a JSON object")
    return meta


def select_level(counts: List[int], max_splats: int) -> int:
    """The finest level (index 0 = finest) with at most LOD_SPLAT_FACTOR * max_splats splats, else the
    coarsest level."""
    if not counts:
        return 0
    limit = LOD_SPLAT_FACTOR * max(1, int(max_splats))
    for level, count in enumerate(counts):
        if count <= limit:
            return level
    return len(counts) - 1


# --------------------------------------------------------------------------------------------------
# LCC 1
# --------------------------------------------------------------------------------------------------

def lcc_level_counts(meta: dict) -> List[int]:
    counts = meta.get("splats")
    if not isinstance(counts, list) or not all(isinstance(c, int) and c >= 0 for c in counts):
        return []
    return counts


# --------------------------------------------------------------------------------------------------
# LCC 2
# --------------------------------------------------------------------------------------------------

def _children(node) -> list:
    child = node.get("child") if isinstance(node, dict) else None
    if isinstance(child, list):
        return [c for c in child if isinstance(c, dict)]
    if isinstance(child, dict):
        return [c for c in child.values() if isinstance(c, dict)]
    return []


def lcc2_layout(meta: dict) -> dict:
    """``{"total_levels", "splat_files", "levels": {level: {file_index: count or None}}}``.

    Raises LccMetaError when the required fields are missing.
    """
    root = meta.get("root")
    if not isinstance(root, dict):
        raise LccMetaError("LCC 2 scene description has no root node")
    legacy = all(key in meta for key in ("total_splats", "lod_3dgs_info", "lod_level"))
    if legacy:
        total_levels = meta.get("lod_level")
        files = []
        for name in root.get("files") or []:
            if not isinstance(name, str):
                raise LccMetaError("LCC 2 root.files holds a non-string entry")
            name = name[1:] if name.startswith("/") else name
            files.append(name if name.endswith(".sog") else name + ".sog")
    else:
        total_levels = meta.get("totalLevels")
        files = root.get("splatFiles")
        if not isinstance(files, list) or not all(isinstance(name, str) for name in files):
            raise LccMetaError("LCC 2 scene description has no root.splatFiles list")
    if not isinstance(total_levels, int) or total_levels <= 0:
        raise LccMetaError("LCC 2 scene description has no level count")
    env = (root.get("data") or {}).get("env") if isinstance(root.get("data"), dict) else None
    env_index = env.get("name") if isinstance(env, dict) else None

    levels: Dict[int, Dict[int, Optional[int]]] = {}
    walked = 0
    stack = [(child, 1) for child in _children(root)]
    while stack:
        node, depth = stack.pop()
        walked += 1
        if walked > MAX_NODES:
            raise LccMetaError(f"LCC 2 octree has more than {MAX_NODES} nodes")
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        gs = data.get("3dgs") if isinstance(data.get("3dgs"), dict) else None
        if gs is not None and gs.get("name") != env_index:
            index = gs.get("name")
            if not isinstance(index, int) or not 0 <= index < len(files):
                raise LccMetaError(f"LCC 2 node names chunk file {index!r}, outside root.splatFiles")
            level = total_levels - depth
            files_at_level = levels.setdefault(level, {})
            count = gs.get("count")
            if not isinstance(count, int) or count < 0 or files_at_level.get(index, 0) is None:
                files_at_level[index] = None
            else:
                files_at_level[index] = files_at_level.get(index, 0) + count
        stack.extend((child, depth + 1) for child in _children(node))
    return {"total_levels": total_levels, "splat_files": files, "levels": levels}


def lcc2_level_counts(meta: dict, layout: Optional[dict] = None) -> List[int]:
    """Splat count per level, LOD 0 first: from the octree's node counts where every node of a level
    has one, else from ``lodSplats`` (``lod_3dgs_info`` reversed in the older protocol)."""
    layout = layout or lcc2_layout(meta)
    declared = meta.get("lodSplats")
    if not isinstance(declared, list) and isinstance(meta.get("lod_3dgs_info"), list):
        declared = list(reversed(meta["lod_3dgs_info"]))
    counts = []
    for level in range(layout["total_levels"]):
        files = layout["levels"].get(level, {})
        if files and all(count is not None for count in files.values()):
            counts.append(sum(files.values()))
        elif isinstance(declared, list) and level < len(declared) and isinstance(declared[level], int):
            counts.append(declared[level])
        else:
            counts.append(0 if not files else 2 ** 62)
    return counts


def lcc2_level_files(meta: dict, max_splats: int) -> tuple:
    """(level, [chunk file names for that level]) for a thumbnail within ``max_splats``."""
    layout = lcc2_layout(meta)
    counts = lcc2_level_counts(meta, layout)
    readable = [count if layout["levels"].get(level) else 2 ** 62 for level, count in enumerate(counts)]
    level = select_level(readable, max_splats)
    names = [layout["splat_files"][index] for index in sorted(layout["levels"].get(level, {}))]
    if not names:
        raise LccMetaError("LCC 2 scene description names no chunk files")
    return level, names


def all_lcc2_files(meta: dict) -> List[str]:
    """Every chunk file name the description lists (checked before any is handed to the converter)."""
    return list(lcc2_layout(meta)["splat_files"])
