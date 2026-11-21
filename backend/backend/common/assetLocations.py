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

from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple

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
    name = _file_name(loc)
    # The whole key first: a file named like a preview (``x.previewFile.png``) is a chain key itself
    depth = _chain_depth(loc, object_key)
    if depth is not None:
        return "/" + "/".join([name] * (depth + 1))
    body, preview = _split_preview(object_key)
    depth = _chain_depth(loc, body)
    if depth is not None:
        return "/" + "/".join([name] * (depth + 1)) + preview
    return object_key[len(loc):]


# The refusal of a workflow output at a single-file asset's own file path. The upload completion answers it
# per file, and output processing repeats it in the run's error, since the user can act on it.
OWN_FILE_OUTPUT_REFUSAL = ("A workflow output cannot replace the file of a single-file asset. "
                           "Write the output under another file name or folder.")


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
    # The whole path first, as relative_path_in does, then the path without a preview suffix
    for body, preview in ((relative, ""), _split_preview(relative)):
        segments = body.split("/")
        if all(segment == name for segment in segments):
            return loc + "/".join([""] + [name] * (len(segments) - 1)) + preview
    return loc + "/" + relative


def listing_prefixes(location: Optional[str]) -> List[Tuple[str, Optional[int]]]:
    """``(prefix, key cap)`` pairs that list the keys a location holds, and no key that only shares its characters.

    A folder location is one uncapped prefix. A single-file location reads its own key with a one-key
    listing (the smallest key under that prefix is the key itself, when it exists), then its own
    previews ``{key}.previewFile.`` and its companion folder ``{key}/``. Callers still keep only the
    keys ``contains`` accepts.
    """
    loc = normalize_location(location)
    if not loc:
        return []
    if loc.endswith("/"):
        return [(loc, None)]
    return [(loc, 1), (loc + PREVIEW_FILE_PATTERN, None), (loc + "/", None)]


def held_objects(location: Optional[str],
                 list_objects: Callable[[str, Optional[int]], Iterable[Dict]]) -> Iterator[Dict]:
    """Each S3 object a location holds, once, from listings of its ``listing_prefixes``.

    ``list_objects(prefix, cap)`` returns the S3 object dicts (``Contents`` entries) under ``prefix``, at most
    ``cap`` when ``cap`` is set. The one-key read of a single-file location's own key counts only when the key
    it returns IS that key: when the object has no current version, the smallest key under the prefix is an
    own preview or a companion entry, which a later prefix lists again. Only keys ``contains`` accepts are
    yielded, each once.
    """
    loc = normalize_location(location)
    seen = set()
    for prefix, cap in listing_prefixes(loc):
        for obj in list_objects(prefix, cap) or []:
            key = obj.get("Key", "")
            if cap == 1 and not loc.endswith("/") and key != loc:
                continue
            if key in seen or not contains(loc, key):
                continue
            seen.add(key)
            yield obj


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


# ---------------------------------------------------------------------------
# Auxiliary (viewer and preview) data of one file
#
# Auxiliary objects live at ``{databaseId}/{file key}/...``; a file's own data is in its ``preview/``
# subfolder (``common.workflows.executionRecords.aux_preview_file_prefix``). In a single-file location the
# rest of ``{databaseId}/{key}/`` holds the data of the companion entries, and the data of a companion
# entry in a folder named ``preview`` (``{key}/preview/x.glb``) sits inside the object's ``preview/``
# folder, at ``{databaseId}/{key}/preview/x.glb/preview/...``.
# ---------------------------------------------------------------------------


def aux_bucket_file_base(database_id: Optional[str], file_key: Optional[str]) -> str:
    """``{databaseId}/{file key}/``, the auxiliary-bucket base of one file (or of a folder and all beneath it)."""
    key = (file_key or "").strip("/")
    base = (database_id or "").strip("/")
    return f"{base}/{key}/" if key else f"{base}/"


def is_location_object(location: Optional[str], object_key: Optional[str]) -> bool:
    """Whether a key is the object of a single-file location (the location key itself)."""
    loc = normalize_location(location)
    return bool(loc) and not loc.endswith("/") and (object_key or "").lstrip("/") == loc


def aux_file_prefix(database_id: Optional[str], location: Optional[str], object_key: Optional[str]) -> str:
    """The auxiliary prefix of one file's own data: ``aux_bucket_file_base``, or for the object of a single-file
    location its ``preview/`` subfolder, since the rest of that base belongs to the companion entries."""
    base = aux_bucket_file_base(database_id, object_key)
    return base + "preview/" if is_location_object(location, object_key) else base


def aux_owner_filter(database_id: Optional[str], location: Optional[str], target_key: Optional[str],
                     aux_prefix: Optional[str]) -> Optional[Callable[[str], bool]]:
    """None, or which auxiliary keys under ``aux_prefix`` belong to ``target_key`` in a single-file location.

    Under ``{databaseId}/{key}/preview/`` a key whose rest has a ``/preview/`` segment (``{entry}/preview/...``)
    belongs to a companion entry in ``{key}/preview/``, any other key to the object. The predicate keeps the
    object's keys for the object and the companion entries' keys for any other target.
    """
    loc = normalize_location(location)
    if not loc or loc.endswith("/"):
        return None
    shared = aux_bucket_file_base(database_id, loc) + "preview/"
    if not (aux_prefix or "").startswith(shared):
        return None
    is_object = is_location_object(loc, target_key)

    def keep(aux_key: str) -> bool:
        return ("/preview/" in aux_key[len(shared):]) != is_object
    return keep


def aux_scope(database_id: Optional[str], location: Optional[str],
              object_key: Optional[str]) -> Tuple[str, Optional[Callable[[str], bool]]]:
    """``(prefix, keep)`` of one file's own auxiliary data: ``aux_file_prefix`` and ``aux_owner_filter``.
    ``keep`` is None when every key under the prefix is the file's."""
    prefix = aux_file_prefix(database_id, location, object_key)
    return prefix, aux_owner_filter(database_id, location, object_key, prefix)
