# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
What an asset's stored location (``assetLocation.Key``) covers in its bucket.

A location that ends in ``/`` is a folder: it holds every object whose key starts
with it. Any other location names one object, the file of an asset created with a
``bucketExistingKey`` that points at an object rather than a folder (a single-file
asset). A single-file asset holds exactly:

- the object itself;
- everything under ``{file key}/``, its companion folder;
- its own previews ``{file key}.previewFile.{ext}``.

S3 ``Prefix`` matching is not path-aware, so a single-file location used as a prefix
also matches longer keys that only share its characters (``projects/model.glb.bak``,
``projects/model.glbx/...``). Those are never part of the asset. Code that lists,
archives or deletes an asset's objects decides membership with ``contains`` rather
than with the prefix match alone. Code that decides whether two locations overlap asks
``locations_share_key``: two locations can hold a common key that neither location key
names (``a/x`` and ``a/x.previewFile`` both hold ``a/x.previewFile.previewFile.z``).

``AssetFilePaths`` is the one mapping between asset-relative file paths and S3 keys
that every handler uses.

Pure functions, no AWS calls (``AssetFilePaths`` takes its one S3 read as a callable).
"""

from typing import Callable, List, Optional, Tuple

from common.s3PathPatterns import PREVIEW_FILE_PATTERN

# The segment that starts a file's viewer data in a stream path ``{file}/preview/...``
VIEWER_DATA_SEGMENT = "/preview/"


def normalize_location(key: Optional[str]) -> str:
    """The location key in its stored S3 form: no leading ``/``; empty for no location."""
    return (key or "").lstrip("/")


def is_folder_location(key: Optional[str]) -> bool:
    """Whether a location is a folder (ends in ``/``). Any other non-empty location is a single-file asset."""
    return normalize_location(key).endswith("/")


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


# The kinds of key set a location is made of (``_held_sets``)
_ONE_KEY, _PREFIX, _PREVIEWS = "key", "prefix", "previews"


def _held_sets(location: str) -> List[Tuple[str, str]]:
    """The key sets whose union is what a normalized, non-empty location holds (what ``contains`` accepts).

    ``(_ONE_KEY, k)`` is the key ``k``; ``(_PREFIX, p)`` is every key that starts with ``p``, ``p`` included;
    ``(_PREVIEWS, s)`` is every key ``s{ext}`` with a non-empty ``ext`` that has no ``/``. A folder is one
    prefix. A single file is its key, its companion folder ``{key}/`` and its preview stem ``{key}.previewFile.``.
    """
    if location.endswith("/"):
        return [(_PREFIX, location)]
    return [(_ONE_KEY, location), (_PREFIX, location + "/"), (_PREVIEWS, location + PREVIEW_FILE_PATTERN)]


def _set_holds(held: Tuple[str, str], key: str) -> bool:
    kind, text = held
    if kind == _ONE_KEY:
        return key == text
    if not key.startswith(text):
        return False
    rest = key[len(text):]
    return kind == _PREFIX or (bool(rest) and "/" not in rest)


def _sets_meet(first: Tuple[str, str], second: Tuple[str, str]) -> bool:
    """Whether two of the key sets ``_held_sets`` returns have a key in common."""
    if first[0] == _ONE_KEY:
        return _set_holds(second, first[1])
    if second[0] == _ONE_KEY:
        return _set_holds(first, second[1])
    # Two open sets, each its stem followed by more characters. They can meet only when one stem starts with
    # the other. The set with the longer stem then holds keys made of the shorter stem and the extra characters
    # followed by more: a prefix set with the shorter stem holds all of them, and a preview set with the shorter
    # stem holds some (the longer stem followed by ``z``, say) exactly when the extra characters have no ``/``.
    (shorter_kind, shorter), (_, longer) = sorted((first, second), key=lambda held: len(held[1]))
    if not longer.startswith(shorter):
        return False
    return shorter_kind == _PREFIX or "/" not in longer[len(shorter):]


def locations_share_key(first: Optional[str], second: Optional[str]) -> bool:
    """Whether two asset locations hold at least one S3 key in common (a key ``contains`` accepts for both).

    Decided over the keys each location holds (a folder: every key under it; a single file: its key, every
    key under ``{key}/`` and its previews ``{key}.previewFile.{ext}``), not over the two location keys alone,
    so it also finds a common key that neither location key names: ``a/x`` and ``a/x.previewFile`` both hold
    ``a/x.previewFile.previewFile.z``, the preview of ``a/x`` with extension ``previewFile.z`` and the preview
    of ``a/x.previewFile`` with extension ``z``. An empty location holds nothing and shares nothing.
    """
    a = normalize_location(first)
    b = normalize_location(second)
    if not a or not b:
        return False
    return any(_sets_meet(x, y) for x in _held_sets(a) for y in _held_sets(b))


# ---------------------------------------------------------------------------
# Asset-relative file paths and S3 keys
# ---------------------------------------------------------------------------


def _file_name(location: str) -> str:
    return location.rsplit("/", 1)[-1]


def companion_key(location: Optional[str]) -> Optional[str]:
    """``{file key}/{file name}``, the companion entry named like a single-file location's object; None otherwise."""
    loc = normalize_location(location)
    if not loc or loc.endswith("/"):
        return None
    return loc + "/" + _file_name(loc)


def _has_traversal_segment(value: str) -> bool:
    return ".." in value.split("/")


class AssetFilePaths:
    """How asset-relative file paths and S3 keys map in one asset location, for one request.

    A key's path is the key after the folder (single file: after ``{file key}``), except that a single-file
    location's object and previews are at ``/{file name}`` and ``/{file name}.previewFile.{ext}`` unless the
    companion entry ``{file key}/{file name}`` exists; that entry then keeps ``/{file name}``, as before.
    ``key_exists`` answers that once per instance (without it the entry is taken to exist). A path with a
    ``..`` segment names nothing, and every key returned is one ``contains`` accepts.
    """

    def __init__(self, location: Optional[str], key_exists: Optional[Callable[[str], bool]] = None):
        self.location = normalize_location(location)
        self._key_exists = key_exists
        self._object_listed: Optional[bool] = None

    @property
    def is_single_file(self) -> bool:
        return bool(self.location) and not self.location.endswith("/")

    def object_listed(self) -> bool:
        """Whether a single-file location's object is listed at ``/{file name}`` (no companion entry of that name)."""
        if not self.is_single_file:
            return False
        if self._object_listed is None:
            self._object_listed = self._key_exists is not None and not self._key_exists(companion_key(self.location))
        return self._object_listed

    def _held(self, key: Optional[str]) -> Optional[str]:
        return key if key and contains(self.location, key) else None

    def _key_for_relative(self, rel: str) -> Optional[str]:
        loc = self.location
        if loc.endswith("/"):
            return self._held(loc + rel)
        name = _file_name(loc)
        if rel and (rel == name or _is_preview_of(name, rel)) and self.object_listed():
            return self._held(loc + rel[len(name):])
        return self._held(loc + "/" + rel)

    def key_for(self, relative_path: Optional[str]) -> Optional[str]:
        """The S3 key an asset-relative path (leading ``/`` dropped) names, or None."""
        raw = relative_path or ""
        if not self.location or _has_traversal_segment(raw):
            return None
        return self._key_for_relative(raw.lstrip("/"))

    def request_key_for(self, value: Optional[str]) -> Optional[str]:
        """The S3 key a file route's path names, or None: after one leading ``/`` is dropped, a key under the
        location folder as given, the own key (no leading ``/``) for the listed object, else ``key_for``."""
        raw = value or ""
        loc = self.location
        if not loc or _has_traversal_segment(raw):
            return None
        if raw == loc and self.object_listed():
            return loc
        rel = raw[1:] if raw.startswith("/") else raw
        if rel.startswith(loc if loc.endswith("/") else loc + "/"):
            return self._held(rel)
        return self._key_for_relative(rel)

    def download_key_for(self, value: Optional[str]) -> Optional[str]:
        """The S3 key the download route's ``key`` names, or None: the location key and keys under its folder
        as given, a value that only shares the location's characters refused, else ``key_for``."""
        raw = value or ""
        loc = self.location
        if not loc or _has_traversal_segment(raw):
            return None
        stem = loc.rstrip("/")
        if raw == loc or raw.startswith(stem + "/"):
            return self._held(raw)
        if not raw.startswith("/") and raw.startswith(stem):
            return None
        return self.key_for(raw)

    def path_for(self, key: Optional[str]) -> Optional[str]:
        """The asset-relative path (leading ``/``) of a key the location holds, or None."""
        loc = self.location
        if not key or not contains(loc, key):
            return None
        if loc.endswith("/"):
            return "/" + key[len(loc):]
        if key == loc or _is_preview_of(loc, key):
            return "/" + (_file_name(loc) if self.object_listed() else "") + key[len(loc):]
        return "/" + key[len(loc) + 1:]

    def names_object(self, key: Optional[str]) -> bool:
        """Whether a key is a single-file location's object (the location key itself)."""
        return self.is_single_file and key == self.location

    def names_held_file_data(self, value: Optional[str]) -> bool:
        """Whether a stream path is ``{file key}/preview/...`` for the full S3 key of a file the location holds.

        A folder location's files are ``{location}{name}``. A single-file location's are its object and the
        companion entries ``{file key}/...``; its own previews ``{file key}.previewFile.{ext}`` are not read this
        way, so every viewer data key stays under ``{location}/``. The file part ends at the first ``/preview/`` at
        or after the end of the location key, so a location key with a ``preview`` folder segment
        (``scans/preview/site.laz``) is read whole. A folder location's own marker names no file.
        """
        loc = self.location
        raw = value or ""
        if not loc or not raw.startswith(loc if loc.endswith("/") else loc + "/"):
            return False
        index = raw.find(VIEWER_DATA_SEGMENT, len(loc))
        if index == -1:
            return False
        file_key = raw[:index]
        return contains(loc, file_key) and not (loc.endswith("/") and file_key == loc)

    def aux_stream_key_for(self, database_id: Optional[str], value: Optional[str]) -> Optional[str]:
        """The auxiliary key of a viewer data path ``{file}/preview/...`` in this location, or None.

        A file's viewer data is ``{databaseId}/{file key}/preview/...``. After one leading ``/`` is dropped, the
        path is read in this order:

        - the file's full S3 key (the ``key`` the file list returns, the form the web viewers send), when the
          path starts with the key of a file the location holds followed by ``/preview/``: used as given under
          ``{databaseId}/`` (``names_held_file_data``);
        - the auxiliary key itself, ``{databaseId}/{location}/...``: used as given;
        - any other path is the file's asset-relative path, joined under ``{databaseId}/{location}/``.

        Every result is under ``{databaseId}/{location}/``, so it is ``{databaseId}/`` plus a key the location
        holds, and the full key reaches only keys the asset-relative path already reached. A path with a ``..``
        segment, an empty location and a path that names nothing under the location give None.
        """
        raw = value or ""
        loc = self.location
        if not loc or _has_traversal_segment(raw):
            return None
        database = (database_id or '').strip('/')
        base = f"{database}/{loc.strip('/')}/"
        rel = raw[1:] if raw.startswith("/") else raw
        if self.names_held_file_data(rel):
            key = f"{database}/{rel}"
        elif rel.startswith(base):
            key = rel
        else:
            key = base + rel
        return key if key.startswith(base) and len(key) > len(base) else None


def own_aux_scope(database_id: Optional[str], location: Optional[str],
                  file_key: Optional[str]) -> Tuple[str, Optional[Callable[[str], bool]]]:
    """``(prefix, keep)`` of one file's (or folder's) own auxiliary data under ``{databaseId}/{file key}/``.

    A single-file object owns only that base's ``preview/`` folder. In it, ``{entry}/preview/...`` keys belong
    to companion entries in a ``preview/`` folder, which ``keep`` tells apart. The companion folder ``{file key}/``
    holds everything else under the base. Otherwise ``keep`` is None.
    """
    loc = normalize_location(location)
    key = (file_key or "").strip("/")
    db = (database_id or "").strip("/")
    prefix = f"{db}/{key}/" if key else f"{db}/"
    if not loc or loc.endswith("/"):
        return prefix, None
    is_object = (file_key or "").lstrip("/") == loc
    if is_object:
        prefix += "preview/"
    shared = f"{db}/{loc}/preview/"
    if shared.startswith(prefix) and not is_object:
        return prefix, lambda aux_key: not aux_key.startswith(shared) or "/preview/" in aux_key[len(shared):]
    if not prefix.startswith(shared):
        return prefix, None
    return prefix, lambda aux_key: ("/preview/" in aux_key[len(shared):]) != is_object
