# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
What an asset's stored location (``assetLocation.Key``) covers in its bucket.

A location that ends in ``/`` is a folder: it holds every object whose key starts
with it. Any other location names exactly one object, the single file of an asset
created with a ``bucketExistingKey`` that points at an object rather than a folder.
S3 ``Prefix`` matching is not path-aware, so a single-file location used as a prefix
also matches every longer key that starts with the same characters
(``projects/model.glb`` matches ``projects/model.glb.bak``). Code that lists, reads,
archives or deletes an asset's objects decides membership with ``contains`` rather
than with the prefix match alone.

A single-file asset's one object has the asset-relative path ``/{file name}``, the
path a folder asset gives a file of the same name at its root.

Pure functions, no AWS calls.
"""

from typing import Optional


def normalize_location(key: Optional[str]) -> str:
    """The location key in its stored S3 form: no leading ``/``; empty for no location."""
    return (key or "").lstrip("/")


def is_folder_location(key: Optional[str]) -> bool:
    """Whether a location is a folder (ends in ``/``). Any other non-empty location is one object."""
    return normalize_location(key).endswith("/")


def contains(location: Optional[str], object_key: Optional[str]) -> bool:
    """Whether an S3 object key belongs to an asset location.

    A folder location contains every key that starts with it, the folder marker
    itself included. A single-file location contains only its own key. An empty
    location contains nothing.
    """
    loc = normalize_location(location)
    if not loc or not object_key:
        return False
    if loc.endswith("/"):
        return object_key.startswith(loc)
    return object_key == loc


def relative_path_in(location: Optional[str], object_key: Optional[str]) -> Optional[str]:
    """The asset-relative path (leading ``/``) of an object key, or None when the location does not contain it.

    In a folder location the path is the key after the folder (``/`` for the folder
    marker). A single-file location's own object is ``/{file name}``.
    """
    if not contains(location, object_key):
        return None
    loc = normalize_location(location)
    if loc.endswith("/"):
        return "/" + object_key[len(loc):]
    return "/" + loc.rsplit("/", 1)[-1]


def object_key_for(location: Optional[str], relative_path: Optional[str]) -> Optional[str]:
    """The S3 key an asset-relative path names in a location, or None when it names nothing there.

    A folder location joins the path under the folder. A single-file location resolves
    only its own object, named by its file name (``/model.glb``) or by its own key;
    every other path, ``/`` included, names nothing in it.
    """
    loc = normalize_location(location)
    if not loc:
        return None
    relative = (relative_path or "").lstrip("/")
    if loc.endswith("/"):
        return loc + relative
    if relative and relative in (loc, loc.rsplit("/", 1)[-1]):
        return loc
    return None
