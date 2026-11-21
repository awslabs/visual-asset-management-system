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

Pure functions, no AWS calls.
"""

from typing import List, Optional, Tuple

from common.s3PathPatterns import PREVIEW_FILE_PATTERN


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
