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

A single-file asset behaves like a folder asset; nothing it holds is hidden. The one
companion entry whose natural path would be the object's own path, ``{file key}/{file name}``,
is listed one level down, at ``/{file name}/{file name}``, and its previews at
``/{file name}/{file name}.previewFile.{ext}``. The rule is applied along the whole chain of
keys named only after the file, so ``{file key}/{file name}/{file name}`` is
``/{file name}/{file name}/{file name}``: each path names one key and each key has one path.
Every other key keeps the path after the file key.

S3 ``Prefix`` matching is not path-aware, so a single-file location used as a prefix
also matches longer keys that only share its characters (``projects/model.glb.bak``,
``projects/model.glbx/...``). Those are never part of the asset. Code that lists, reads,
archives or deletes an asset's objects decides membership with ``contains`` rather
than with the prefix match alone.

Pure functions, no AWS calls.
"""

from typing import Optional, Tuple

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


def _split_preview(key: str) -> Tuple[str, str]:
    """``(base, '.previewFile.{ext}')`` for a preview key, else ``(key, '')``."""
    index = key.rfind(PREVIEW_FILE_PATTERN)
    if index > 0 and _is_preview_of(key[:index], key):
        return key[:index], key[index:]
    return key, ""


def _chain_depth(location: str, key: str) -> Optional[int]:
    """``n`` when ``key`` is the location followed by ``n`` segments named like its file, else None."""
    name = _file_name(location)
    if key == location:
        return 0
    if not key.startswith(location + "/"):
        return None
    segments = key[len(location) + 1:].split("/")
    return len(segments) if all(segment == name for segment in segments) else None


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
    marker). In a single-file location a key in the chain named only after the file
    (the object, ``{file key}/{file name}``, ...) and its previews are one level deeper
    than their natural path; every other key is the path after ``{file key}``
    (``/`` for the companion folder marker).
    """
    if not contains(location, object_key):
        return None
    loc = normalize_location(location)
    if loc.endswith("/"):
        return "/" + object_key[len(loc):]
    body, preview = _split_preview(object_key)
    depth = _chain_depth(loc, body)
    if depth is not None:
        return "/" + "/".join([_file_name(loc)] * (depth + 1)) + preview
    return object_key[len(loc):]


def names_own_object(location: Optional[str], relative_path: Optional[str]) -> bool:
    """Whether an asset-relative path names a single-file location's object itself (``/{file name}`` or its key)."""
    loc = normalize_location(location)
    if not loc or loc.endswith("/"):
        return False
    return object_key_for(loc, relative_path) == loc


def object_key_for(location: Optional[str], relative_path: Optional[str]) -> Optional[str]:
    """The S3 key an asset-relative path names in a location, or None when it names no single key there.

    A folder location joins the path under the folder. In a single-file location the
    object's own key, a key under ``{file key}/`` and an own preview's key are used as
    given; a path made only of the file's name (``/{file name}``, ``/{file name}/{file name}``,
    ...), with or without a preview suffix, names the key one level up its chain (the object,
    ``{file key}/{file name}``, ...); any other path names ``{file key}/{path}``.
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
    if relative == loc or relative.startswith(loc + "/") or _is_preview_of(loc, relative):
        return relative
    name = _file_name(loc)
    body, preview = _split_preview(relative)
    segments = body.split("/")
    if all(segment == name for segment in segments):
        return loc + "/".join([""] + [name] * (len(segments) - 1)) + preview
    return loc + "/" + relative


def folder_listing_prefix(location: Optional[str], relative_folder: Optional[str]) -> str:
    """The S3 ``Prefix`` under which every key of an asset-relative folder path is listed.

    A folder location lists ``{folder}{path}``. In a single-file location a folder path
    made only of the file's name (``/{file name}/``, ...) also holds the chain key one
    level up and its previews, so the listing drops the trailing ``/``; filter what it
    returns with ``in_folder``.
    """
    loc = normalize_location(location)
    relative = (relative_folder or "").lstrip("/")
    if not relative.endswith("/"):
        relative += "/"
    if loc.endswith("/"):
        return loc + relative
    name = _file_name(loc)
    if all(segment == name for segment in relative[:-1].split("/")):
        return loc + "/" + relative[:-1]
    return loc + "/" + relative


def in_folder(location: Optional[str], relative_folder: Optional[str], object_key: Optional[str]) -> bool:
    """Whether a key of the location sits under an asset-relative folder path (``/sub/``)."""
    path = relative_path_in(location, object_key)
    folder = "/" + (relative_folder or "").strip("/") + "/"
    return path is not None and folder != "//" and path.startswith(folder)
