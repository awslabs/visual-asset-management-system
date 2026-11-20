# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
What an asset's stored location (``assetLocation.Key``) covers in its bucket.

A location that ends in ``/`` is a folder: it holds every object whose key starts
with it. Any other location names one object, the file of an asset created with a
``bucketExistingKey`` that points at an object rather than a folder (a single-file
asset). A single-file asset holds exactly:

- the object itself, at the asset-relative path ``/{file name}``;
- everything under ``{file key}/``, its companion folder, at the path after the file
  key (``{file key}/sub/x.obj`` is ``/sub/x.obj``);
- its own previews ``{file key}.previewFile.{ext}``, at ``/{file name}.previewFile.{ext}``.

S3 ``Prefix`` matching is not path-aware, so a single-file location used as a prefix
also matches longer keys that only share its characters (``projects/model.glb.bak``,
``projects/model.glbx/...``). Those are never part of the asset. Code that lists, reads,
archives or deletes an asset's objects decides membership with ``contains`` rather
than with the prefix match alone.

A companion entry whose path is the object's own path (``{file key}/{file name}``) or
one of its previews' paths is shadowed: the object and its previews win that path.

Pure functions, no AWS calls.
"""

from typing import Optional

from common.s3PathPatterns import PREVIEW_FILE_PATTERN


def normalize_location(key: Optional[str]) -> str:
    """The location key in its stored S3 form: no leading ``/``; empty for no location."""
    return (key or "").lstrip("/")


def is_folder_location(key: Optional[str]) -> bool:
    """Whether a location is a folder (ends in ``/``). Any other non-empty location is a single-file asset."""
    return normalize_location(key).endswith("/")


def _file_name(location: str) -> str:
    return location.rsplit("/", 1)[-1]


def _is_preview_of(base_key: str, object_key: str) -> bool:
    """Whether ``object_key`` is ``{base_key}.previewFile.{ext}``, a preview directly beside the base key."""
    stem = base_key + PREVIEW_FILE_PATTERN
    if not object_key.startswith(stem):
        return False
    extension = object_key[len(stem):]
    return bool(extension) and "/" not in extension


def contains(location: Optional[str], object_key: Optional[str]) -> bool:
    """Whether an S3 object key belongs to an asset location.

    A folder location contains every key that starts with it, the folder marker
    itself included. A single-file location contains its own key, every key under
    ``{file key}/`` and its own previews. An empty location contains nothing.
    """
    loc = normalize_location(location)
    if not loc or not object_key:
        return False
    if loc.endswith("/"):
        return object_key.startswith(loc)
    return object_key == loc or object_key.startswith(loc + "/") or _is_preview_of(loc, object_key)


def relative_path_in(location: Optional[str], object_key: Optional[str]) -> Optional[str]:
    """The asset-relative path (leading ``/``) of an object key, or None when the location does not contain it.

    In a folder location the path is the key after the folder (``/`` for the folder
    marker). In a single-file location the object is ``/{file name}``, a companion
    entry is the key after ``{file key}`` (``/`` for the companion folder marker) and
    an own preview is ``/{file name}.previewFile.{ext}``.
    """
    if not contains(location, object_key):
        return None
    loc = normalize_location(location)
    if loc.endswith("/"):
        return "/" + object_key[len(loc):]
    if object_key == loc:
        return "/" + _file_name(loc)
    if object_key.startswith(loc + "/"):
        return object_key[len(loc):]
    return "/" + _file_name(loc) + object_key[len(loc):]


def is_shadowed(location: Optional[str], object_key: Optional[str]) -> bool:
    """Whether a single-file location's companion entry has the path of the object or of one of its previews.

    The object and its own previews win their paths, so a listing leaves such an entry
    out. It still belongs to the asset (``contains`` is true).
    """
    loc = normalize_location(location)
    if not loc or loc.endswith("/") or not object_key or not object_key.startswith(loc + "/"):
        return False
    shadow = loc + "/" + _file_name(loc)
    return object_key == shadow or _is_preview_of(shadow, object_key)


def names_own_object(location: Optional[str], relative_path: Optional[str]) -> bool:
    """Whether an asset-relative path names a single-file location's object itself (``/{file name}`` or its key)."""
    loc = normalize_location(location)
    if not loc or loc.endswith("/"):
        return False
    return object_key_for(loc, relative_path) == loc


def object_key_for(location: Optional[str], relative_path: Optional[str]) -> Optional[str]:
    """The S3 key an asset-relative path names in a location, or None when it names no single key there.

    A folder location joins the path under the folder. In a single-file location the
    file name (or the object's own key) names the object, ``/{file name}.previewFile.{ext}``
    (or the preview's own key) names an own preview, a path already under ``{file key}/``
    is used as given, and any other path names the companion entry ``{file key}/{path}``.
    ``/`` names the single-file asset as a whole, not one key, and resolves to None.
    """
    loc = normalize_location(location)
    if not loc:
        return None
    relative = (relative_path or "").lstrip("/")
    if loc.endswith("/"):
        return loc + relative
    if not relative:
        return None
    name = _file_name(loc)
    if relative in (loc, name):
        return loc
    if relative.startswith(loc + "/"):
        return relative
    for base in (loc, name):
        if _is_preview_of(base, relative):
            return loc + relative[len(base):]
    return loc + "/" + relative
