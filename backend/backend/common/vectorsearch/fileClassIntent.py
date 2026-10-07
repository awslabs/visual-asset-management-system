# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""File-type intent in a natural-language query.

Every embedding carries its file-type phrase, so the vector distance already favours the kind a query
names; this module makes that preference deterministic by naming the classes a query mentions. The
result is a soft boost in the handler (matching classes are ordered first), never a filter.

``FILE_CLASS_PHRASES`` is the same dict the system GenAI metadata pipeline's ``fileClassifier`` embeds
into every file's source text; the pipeline's tests pin the two copies to each other.
"""

import re
from typing import Dict, List, Pattern, Tuple

# fileClass id -> the type phrase the indexer prepends to every embedded text, in classifier order.
FILE_CLASS_PHRASES: Dict[str, str] = {
    "image": "image (photo or picture)",
    "video": "video (footage)",
    "audio": "audio recording",
    "document": "document (PDF or office)",
    "text": "text file",
    "data": "data table (spreadsheet)",
    "tiles3d": "3D Tiles tileset",
    "mesh": "3D model (mesh)",
    "usd": "3D model (USD scene)",
    "cad": "CAD model",
    "pointcloud": "point cloud (LiDAR scan)",
    "splat": "3D Gaussian splat",
    "ifc": "BIM building model (IFC)",
    "other": "file",
}
FILE_CLASSES: Tuple[str, ...] = tuple(FILE_CLASS_PHRASES)

# Words and phrases a person uses for each class; the class's own phrase is added at import. ``other``
# has none: its phrase is the word "file", which names no kind.
_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "image": ("image", "photo", "photograph", "picture"),
    "video": ("video", "footage", "clip", "movie"),
    "audio": ("audio", "recording", "sound"),
    "document": ("document", "pdf", "report", "manual"),
    "text": ("text file", "markdown", "readme", "source code", "log file"),
    "data": ("spreadsheet", "table", "csv", "dataset"),
    "tiles3d": ("tileset", "3d tiles"),
    "mesh": ("3d model", "mesh", "glb", "fbx"),
    "usd": ("usd", "usd scene", "universal scene description"),
    "cad": ("cad", "step", "iges", "drawing"),
    "pointcloud": ("point cloud", "lidar", "scan"),
    "splat": ("splat", "gaussian splat"),
    "ifc": ("bim", "ifc", "building model"),
    "other": (),
}
INTENT_WORDS: Dict[str, Tuple[str, ...]] = {
    cls: tuple(dict.fromkeys((*words, FILE_CLASS_PHRASES[cls].lower()))) if words else ()
    for cls, words in _SYNONYMS.items()
}


def _pattern(words: Tuple[str, ...]) -> Pattern[str]:
    alternatives = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])", re.IGNORECASE)


_PATTERNS: Dict[str, Pattern[str]] = {cls: _pattern(words) for cls, words in INTENT_WORDS.items() if words}


def detect(query: str) -> List[str]:
    """The fileClass ids whose intent words occur in ``query`` as whole words, in FILE_CLASSES order."""
    if not query:
        return []
    return [cls for cls in FILE_CLASSES if cls in _PATTERNS and _PATTERNS[cls].search(query)]
