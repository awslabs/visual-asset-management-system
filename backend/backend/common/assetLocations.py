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
than with the prefix match alone. Code that decides whether two locations overlap asks
``locations_share_key``: two locations can hold a common key that neither location key
names (``a/x`` and ``a/x.previewFile`` both hold ``a/x.previewFile.previewFile.z``).

A file belongs to the deepest stored location that contains it. A location of another asset can lie
inside an asset's location (releases before the create-time conflict check allowed it): the keys that
deeper location holds are that asset's, so the containing asset's file operations leave them alone.
``deeper_holder`` and ``held_by`` decide it for a key given the nested locations (pure);
``nested_locations_of`` reads those locations for one asset location (index or registration walk).

The functions above are pure: no AWS calls.

The location index. Every asset row, live and archived, also carries two attributes, written
when the asset is created and by the v2.6-to-v2.7 `assetLocationIndex` migration step:

- ``locationBucket``: the S3 bucket name of the asset's bucket registration (the name, not the
  ``bucketId``, so every registration of one bucket shares one index partition);
- ``locationKey``: the location without a leading ``/``.

``AssetLocationGSI`` on the asset table indexes them (partition ``locationBucket``, sort
``locationKey``, projecting ``bucketId`` beside the table keys). Rows whose location holds no key
(no location, or the bucket root, stored as an empty location) and rows with no bucket
registration carry no attributes. Until the backfill has written its completion marker the index
does not cover every row whose location holds keys, so no decision may depend on it before
``index_ready()`` answers True; until then callers keep the checks that do not use it. Which
asset owns a key is decided with ``contains`` above, and which locations conflict with
``locations_share_key``.

Depth limit. A key has about two candidate locations per folder level (the folder ``X/`` and the
single file ``X``), plus the key and its preview bases. A key or location with more than
``MAX_CANDIDATE_LOCATIONS`` (64) candidates, about 31 folder levels, is not looked up:
``resolve_owner`` answers ``RESOLVED_TOO_DEEP`` and ``find_conflicts`` raises
``LocationTooDeepError``. A caller keeps the checks that do not use the index for such a key.

The index functions build no AWS client at import. Index reads take the caller's asset table
handle; ``index_ready()`` builds its SSM client on first use unless one is passed.
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, Iterator, List, NamedTuple, Optional, Tuple

import boto3
from botocore.config import Config

from common.s3PathPatterns import PREVIEW_FILE_PATTERN
from customLogging.logger import safeLogger

logger = safeLogger()


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
# The answer to an upload, a workflow output or a file operation whose target path lies in the location of
# another asset nested in the asset's location: those keys are that asset's.
NESTED_PATH_REFUSAL = "This path is not available in this asset"

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


def is_nested_in(location: Optional[str], other: Optional[str]) -> bool:
    """Whether ``other`` is a stored location nested in ``location``: they hold a common key and ``other`` is
    the deeper (longer) one, so every key they share is ``other``'s. A location is not nested in itself."""
    loc = normalize_location(location)
    nested = normalize_location(other)
    return len(nested) > len(loc) and locations_share_key(loc, nested)


def deeper_holder(location: Optional[str], nested_locations, object_key: Optional[str]) -> Optional[str]:
    """The location among ``nested_locations`` (a set of locations nested in ``location``) that holds
    ``object_key`` and is deeper than ``location``, the deepest one when several do; None when no nested
    location holds the key. Reads only the key's candidate locations, so the cost does not grow with the
    number of nested locations."""
    if not nested_locations:
        return None
    depth = len(normalize_location(location))
    key = normalize_location(object_key)
    for candidate in candidate_locations(key):
        if len(candidate) <= depth:
            return None
        if candidate in nested_locations and contains(candidate, key):
            return candidate
    return None


def held_by(location: Optional[str], nested_locations, object_key: Optional[str]) -> bool:
    """Whether ``object_key`` is the file of the asset stored at ``location``: the location contains it and
    no location nested in it (``nested_locations``) holds it."""
    return contains(location, object_key) and deeper_holder(location, nested_locations, object_key) is None


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


LOCATION_INDEX_NAME = "AssetLocationGSI"
LOCATION_BUCKET_ATTRIBUTE = "locationBucket"
LOCATION_KEY_ATTRIBUTE = "locationKey"
ARCHIVED_DATABASE_SUFFIX = "#deleted"

RESOLVED_FOUND = "found"
RESOLVED_NOT_FOUND = "notFound"
RESOLVED_AMBIGUOUS = "ambiguous"
RESOLVED_TOO_DEEP = "tooDeep"

# Per-container cache of one index lookup (bucket, candidate location) -> the index rows there.
LOOKUP_HIT_TTL_SECONDS = 60
LOOKUP_MISS_TTL_SECONDS = 10
LOOKUP_CACHE_MAX_ENTRIES = 4096
# Candidate lookups run in parallel, at most this many at once.
LOOKUP_POOL_SIZE = 8
# Pages read for one exact-location query before it is treated as unreadable. One location holds
# one row in a consistent table; a few more exist only while an asset is archived or unarchived.
MAX_PAGES_PER_LOOKUP = 5
# Candidate locations one key may have (two per folder level, plus the key and its preview bases).
# A key with more is not resolved: resolve_owner answers TOO_DEEP and find_conflicts refuses it.
MAX_CANDIDATE_LOCATIONS = 64
# Rows read per page while looking for a location among a single file's own previews.
PREVIEW_LOOKUP_PAGE_SIZE = 25

# The completion marker the v2.6-to-v2.7 `assetLocationIndex` migration step writes last: an SSM
# String parameter under the deployment's resource-name prefix, so every Lambda can read it with
# the parameter grant it already holds. The value is JSON with `version` and `status`.
INDEX_READY_PARAMETER_SUFFIX = "dataMigrations/assetLocationIndex"
INDEX_READY_MARKER_VERSION = 1
INDEX_READY_STATUS_COMPLETE = "complete"
INDEX_READY_CACHE_TTL_SECONDS = 300

_SSM_CLIENT_CONFIG = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})


#######################
# Index attributes
#######################

def normalize_base_prefix(base_assets_prefix: Optional[str]) -> str:
    """A bucket registration's `baseAssetsPrefix` in canonical form: a trailing `/`, no leading
    `/`, and '' for the bucket root. Registrations spell one prefix as `assets`, `assets/` or
    `/assets/`; every comparison of a prefix goes through this one normalization. Repeated slashes at
    either end are dropped too, so `//` is the bucket root."""
    trimmed = (base_assets_prefix or '').strip('/')
    return f"{trimmed}/" if trimmed else ''


def derive_location(registration: Dict[str, Any], database_id: str, asset_id: str) -> str:
    """The location of a new asset that names none: its folder under the registration's prefix.

    Today the layout is `{baseAssetsPrefix}{assetId}/` for every registration; the database id
    is not part of the key."""
    return normalize_base_prefix((registration or {}).get('baseAssetsPrefix')) + asset_id + '/'


def legacy_location_in_key(object_key: Optional[str], asset_id: str) -> str:
    """For an asset row that stores no location: its folder `{baseAssetsPrefix}{assetId}/` as it appears
    in one of its object keys, the key up to and including the first segment equal to the asset id.
    '' when no segment of the key is the asset id."""
    key = normalize_location(object_key)
    folder = f"{asset_id}/"
    if not asset_id:
        return ''
    if key.startswith(folder):
        return folder
    index = key.find('/' + folder)
    return key[:index + 1 + len(folder)] if index >= 0 else ''


def location_attributes(bucket_name: Optional[str], location_key: Optional[str]) -> Dict[str, str]:
    """The index attributes for an asset row, or {} when the row cannot be indexed (no bucket
    name, or a location at the bucket root, which is stored as an empty location and holds no
    key)."""
    location = normalize_location(location_key)
    if not bucket_name or not location:
        return {}
    return {LOCATION_BUCKET_ATTRIBUTE: bucket_name, LOCATION_KEY_ATTRIBUTE: location}


def has_location_attributes(row: Optional[Dict[str, Any]]) -> bool:
    """Whether an asset row carries both index attributes."""
    return bool(row) and bool(row.get(LOCATION_BUCKET_ATTRIBUTE)) and bool(row.get(LOCATION_KEY_ATTRIBUTE))


def fill_location_attributes(row: Dict[str, Any], bucket_name: Optional[str]) -> Dict[str, Any]:
    """Give a whole asset row that is about to be written back the index attributes it lacks.

    A row read before the backfill reached it has none, and the backfill's consistent verification
    scan is not a snapshot: a copy written behind its cursor (an archive or unarchive) would stay out
    of the index while the marker says complete. So each writer that puts a whole row back calls this
    with the bucket name of the row's registration. A row that already carries both attributes is
    left as it is, and a row whose location holds no key gets none. Returns `row`.
    """
    if has_location_attributes(row):
        return row
    location = row.get('assetLocation')
    row.update(location_attributes(bucket_name, location.get('Key') if isinstance(location, dict) else None))
    return row


def candidate_locations(object_key: Optional[str]) -> List[str]:
    """The stored locations that could contain `object_key`, deepest first.

    - the key itself;
    - for a key ending in `/`, the single-file location whose companion folder marker it is
      (`a/b/` gives `a/b`);
    - for an own-preview key `{base}.previewFile.{ext}`, its base `{base}`;
    - each ancestor folder `X/`, and the single-file location `X` whose companion folder it is.

    `a/b/c.glb` gives `a/b/c.glb`, `a/b/`, `a/b`, `a/`, `a`. Every candidate is a prefix of the key,
    so longest first is deepest first. Whether a stored location there really holds the key is
    decided by `contains`.
    """
    key = normalize_location(object_key)
    if not key:
        return []
    candidates = {key}
    if key.endswith('/'):
        candidates.add(key[:-1])
    last_segment_start = key.rfind('/', 0, len(key) - 1) + 1
    position = key.find(PREVIEW_FILE_PATTERN, last_segment_start)
    while position > 0:
        if _is_preview_of(key[:position], key):
            candidates.add(key[:position])
        position = key.find(PREVIEW_FILE_PATTERN, position + 1)
    position = len(key) - 1 if key.endswith('/') else len(key)
    while True:
        position = key.rfind('/', 0, position)
        if position <= 0:
            break
        candidates.add(key[:position + 1])
        candidates.add(key[:position])
    candidates.discard('')
    return sorted(candidates, key=lambda c: (-len(c), c))


def _preview_partner_locations(location: Optional[str]) -> List[str]:
    """The single-file locations that share a key with `location` although neither contains the other.

    For a single file `S` they are `S.previewFile` above it (both hold `S.previewFile.previewFile.z`),
    and the single file whose `.previewFile` or `.previewFile.` partner `S` is (`a/x` for
    `a/x.previewFile` and for `a/x.previewFile.`). `S.previewFile.` itself sits under the preview stem
    `S.previewFile.`, which `find_conflicts` reads with a prefix lookup. A folder has none: it holds
    no preview set, and every location that shares a key with it contains it or lies under it.
    """
    loc = normalize_location(location)
    if not loc or loc.endswith('/'):
        return []
    stem = PREVIEW_FILE_PATTERN[:-1]                     # ".previewFile"
    partners = [loc + stem]
    for suffix in (stem, PREVIEW_FILE_PATTERN):
        if loc.endswith(suffix) and len(loc) > len(suffix):
            partners.append(loc[:-len(suffix)])
    return partners


class LocationTooDeepError(ValueError):
    """A key or location has more candidate locations than MAX_CANDIDATE_LOCATIONS."""


#######################
# Index reads
#######################

class LocationEntry(NamedTuple):
    """One asset row as the location index holds it."""
    database_id: str
    asset_id: str
    bucket_id: str
    location: str

    @property
    def archived(self) -> bool:
        return self.database_id.endswith(ARCHIVED_DATABASE_SUFFIX)


class Resolution(NamedTuple):
    """The answer of `resolve_owner`. `row` is the asset row as read strongly consistent."""
    status: str
    entry: Optional[LocationEntry] = None
    row: Optional[Dict[str, Any]] = None
    candidates: Tuple[LocationEntry, ...] = ()


_lookup_cache: Dict[Tuple[str, str], Tuple[float, Tuple[LocationEntry, ...]]] = {}
_lookup_cache_lock = threading.Lock()


def clear_location_cache() -> None:
    """Forget every cached index lookup in this container (after this container created,
    archived or deleted an asset, and in tests)."""
    with _lookup_cache_lock:
        _lookup_cache.clear()


def _cached_lookup(bucket: str, location: str, now: float) -> Optional[Tuple[LocationEntry, ...]]:
    with _lookup_cache_lock:
        cached = _lookup_cache.get((bucket, location))
    if cached is None:
        return None
    stored_at, entries = cached
    ttl = LOOKUP_HIT_TTL_SECONDS if entries else LOOKUP_MISS_TTL_SECONDS
    if now - stored_at >= ttl:
        return None
    return entries


def _store_lookup(bucket: str, location: str, entries: Tuple[LocationEntry, ...], now: float) -> None:
    with _lookup_cache_lock:
        if len(_lookup_cache) >= LOOKUP_CACHE_MAX_ENTRIES:
            for cache_key, (stored_at, cached_entries) in list(_lookup_cache.items()):
                ttl = LOOKUP_HIT_TTL_SECONDS if cached_entries else LOOKUP_MISS_TTL_SECONDS
                if now - stored_at >= ttl:
                    del _lookup_cache[cache_key]
            while len(_lookup_cache) >= LOOKUP_CACHE_MAX_ENTRIES:
                del _lookup_cache[next(iter(_lookup_cache))]
        _lookup_cache[(bucket, location)] = (now, entries)


def _entry_from_item(item: Dict[str, Any]) -> LocationEntry:
    return LocationEntry(
        database_id=str(item.get('databaseId', '')),
        asset_id=str(item.get('assetId', '')),
        bucket_id=str(item.get('bucketId', '')),
        location=str(item.get(LOCATION_KEY_ATTRIBUTE, '')),
    )


def _index_query_kwargs(table_name: str, key_condition: str, values: Dict[str, Any]) -> Dict[str, Any]:
    """`client` below is the asset table's `meta.client`, which takes and returns plain Python values
    (the resource layer serializes them), so it is safe to share across the lookup threads."""
    return {
        'TableName': table_name,
        'IndexName': LOCATION_INDEX_NAME,
        'KeyConditionExpression': key_condition,
        'ExpressionAttributeNames': {'#b': LOCATION_BUCKET_ATTRIBUTE, '#k': LOCATION_KEY_ATTRIBUTE},
        'ExpressionAttributeValues': values,
    }


def _lookup_exact(client, table_name: str, bucket: str, location: str) -> Tuple[LocationEntry, ...]:
    """Every index row at exactly `location`, paged on the presence of LastEvaluatedKey."""
    query_kwargs = _index_query_kwargs(table_name, '#b = :b AND #k = :k', {':b': bucket, ':k': location})
    entries: List[LocationEntry] = []
    for _ in range(MAX_PAGES_PER_LOOKUP):
        response = client.query(**query_kwargs)
        entries.extend(_entry_from_item(item) for item in response.get('Items', []))
        if 'LastEvaluatedKey' not in response:
            return tuple(entries)
        query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    raise RuntimeError(f"Location index query did not finish within {MAX_PAGES_PER_LOOKUP} pages")


def _first_under(client, table_name: str, bucket: str, prefix: str, accept: Callable[[LocationEntry], bool],
                 page_size: int, max_pages: int) -> Optional[LocationEntry]:
    """The first index row whose location begins with `prefix` and that `accept` admits, or None.
    Pages on the presence of LastEvaluatedKey; a walk still unfinished after `max_pages` raises."""
    query_kwargs = _index_query_kwargs(table_name, '#b = :b AND begins_with(#k, :p)',
                                       {':b': bucket, ':p': prefix})
    query_kwargs['Limit'] = page_size
    for _ in range(max_pages):
        response = client.query(**query_kwargs)
        for item in response.get('Items', []):
            entry = _entry_from_item(item)
            if accept(entry):
                return entry
        if 'LastEvaluatedKey' not in response:
            return None
        query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    raise RuntimeError(f"Location index query did not finish within {max_pages} pages")


def _lookup_all(table, bucket: str, locations: List[str], use_cache: bool) -> Dict[str, Tuple[LocationEntry, ...]]:
    """Index rows at each of `locations` (exact matches), the uncached ones read in parallel."""
    client = table.meta.client
    table_name = table.name
    now = time.monotonic()
    found: Dict[str, Tuple[LocationEntry, ...]] = {}
    pending: List[str] = []
    for location in locations:
        cached = _cached_lookup(bucket, location, now) if use_cache else None
        if cached is None:
            pending.append(location)
        else:
            found[location] = cached
    if len(pending) == 1:
        found[pending[0]] = _lookup_exact(client, table_name, bucket, pending[0])
    elif pending:
        with ThreadPoolExecutor(max_workers=min(LOOKUP_POOL_SIZE, len(pending))) as pool:
            results = pool.map(lambda loc: _lookup_exact(client, table_name, bucket, loc), pending)
            for location, entries in zip(pending, results):
                found[location] = entries
    if use_cache:
        stored_at = time.monotonic()
        for location in pending:
            _store_lookup(bucket, location, found[location], stored_at)
    return found


def _confirm(table, entry: LocationEntry, bucket: str) -> Optional[Dict[str, Any]]:
    """The asset row behind an index entry, read strongly consistent, when it still holds that
    location in that bucket; otherwise None."""
    row = table.get_item(Key={'databaseId': entry.database_id, 'assetId': entry.asset_id},
                         ConsistentRead=True).get('Item')
    if not row:
        return None
    if row.get(LOCATION_BUCKET_ATTRIBUTE) != bucket or row.get(LOCATION_KEY_ATTRIBUTE) != entry.location:
        return None
    return row


def resolve_owner(table, bucket: str, object_key: str, include_archived: bool = False,
                  use_cache: bool = True) -> Resolution:
    """The asset whose stored location contains `object_key` in `bucket`.

    Candidates are `candidate_locations(object_key)`: the key, its ancestor folders, and the
    single-file locations whose companion folder or own previews would hold it, deepest first; one
    exact Query each on the location index, run in parallel; the deepest candidate holding a row
    that `contains` the key wins (longest match). Archived rows count only with `include_archived`,
    and a live row at a location wins over an archived one there. Two live rows on one location
    answer AMBIGUOUS and attribute nothing. A key with more than MAX_CANDIDATE_LOCATIONS
    candidates answers TOO_DEEP without a read. A FOUND entry is confirmed with a ConsistentRead
    GetItem on the asset row: a row that is gone or no longer holds the location answers NOT_FOUND,
    and that lookup is dropped from the cache.

    Callers must not act on the answer before `index_ready()` is True.
    """
    bucket = bucket or ''
    key = normalize_location(object_key)
    candidates = candidate_locations(key)
    if not bucket or not candidates:
        return Resolution(RESOLVED_NOT_FOUND)
    if len(candidates) > MAX_CANDIDATE_LOCATIONS:
        logger.warning(f"Key in bucket {bucket} has {len(candidates)} candidate locations, more than "
                       f"{MAX_CANDIDATE_LOCATIONS}; nothing is attributed")
        return Resolution(RESOLVED_TOO_DEEP)
    lookups = _lookup_all(table, bucket, candidates, use_cache)
    for location in candidates:
        if not contains(location, key):
            continue
        entries = [e for e in lookups.get(location, ()) if include_archived or not e.archived]
        if not entries:
            continue
        live = [e for e in entries if not e.archived]
        chosen = live or entries
        if len(chosen) > 1:
            logger.error(f"Location {location} in bucket {bucket} is held by {len(chosen)} "
                         f"{'live' if live else 'archived'} asset rows; nothing is attributed")
            return Resolution(RESOLVED_AMBIGUOUS, candidates=tuple(chosen))
        entry = chosen[0]
        row = _confirm(table, entry, bucket)
        if row is None or (not include_archived and row.get('status') == 'archived'):
            with _lookup_cache_lock:
                _lookup_cache.pop((bucket, location), None)
            return Resolution(RESOLVED_NOT_FOUND)
        return Resolution(RESOLVED_FOUND, entry=entry, row=row)
    return Resolution(RESOLVED_NOT_FOUND)


def find_conflicts(table, bucket: str, location: str, max_descendant_pages: int = MAX_PAGES_PER_LOOKUP
                   ) -> List[LocationEntry]:
    """Index rows, live and archived, that share a key with `location` in `bucket`
    (`locations_share_key`). An empty list means no conflict.

    Two locations share a key when one contains the other's own key, and in one more case: a single
    file `S` and the single file `S.previewFile` or `S.previewFile.` hold a common preview key that
    neither location key names (`S.previewFile.previewFile.z`). So this reads, keeping every row
    where `locations_share_key` holds:

    - the stored locations that contain the new one: exact lookups of `candidate_locations(location)`
      (uncached). For a single file `S` that is a folder above it, a single file whose companion
      folder or previews hold it, and `S` itself;
    - the preview partners of a single file (`_preview_partner_locations`): `S.previewFile` above
      it and, for an `S` that ends in `.previewFile` or `.previewFile.`, the single file it is the
      partner of; one exact lookup each;
    - one stored location inside the new one: the first row under its folder form (`F/`, or the
      companion folder `S/` of a single file) and, for a single file, the first stored location
      under its preview stem `S.previewFile.` that shares a key with it (its own previews, and
      `S.previewFile.` itself). So a new folder conflicts with a single file whose object, companion
      folder or previews fall under it, and a new single file with anything under `S/` or at one of
      its preview keys.

    A location with more than MAX_CANDIDATE_LOCATIONS candidates raises LocationTooDeepError, so a
    caller refuses it. Callers must not act on the answer before `index_ready()` is True.
    """
    location = normalize_location(location)
    if not bucket or not location:
        return []
    candidates = candidate_locations(location)
    if len(candidates) > MAX_CANDIDATE_LOCATIONS:
        raise LocationTooDeepError(
            f"Location has {len(candidates)} candidate locations, more than {MAX_CANDIDATE_LOCATIONS}")
    exact = candidates + [p for p in _preview_partner_locations(location) if p not in candidates]
    lookups = _lookup_all(table, bucket, exact, use_cache=False)
    conflicts: List[LocationEntry] = []
    for candidate in exact:
        for entry in lookups.get(candidate, ()):
            if locations_share_key(entry.location, location) and entry not in conflicts:
                conflicts.append(entry)
    client, table_name = table.meta.client, table.name
    folder_form = location if location.endswith('/') else location + '/'
    inside = [_first_under(client, table_name, bucket, folder_form, lambda entry: True,
                           page_size=1, max_pages=max_descendant_pages)]
    if not location.endswith('/'):
        inside.append(_first_under(client, table_name, bucket, location + PREVIEW_FILE_PATTERN,
                                   lambda entry: locations_share_key(location, entry.location),
                                   page_size=PREVIEW_LOOKUP_PAGE_SIZE, max_pages=max_descendant_pages))
    for entry in inside:
        if entry is not None and entry not in conflicts:
            conflicts.append(entry)
    return conflicts


class BucketLocations:
    """Every index row of one bucket, held in memory for bulk attribution (a reindex walk).

    `owner(key)` answers like `resolve_owner` without a read per key: the deepest candidate location
    holding a row that `contains` the key wins, a live row wins over an archived one there, two rows of
    the chosen state on one location answer AMBIGUOUS, and a key with too many candidates answers
    TOO_DEEP. The rows are not confirmed against the table; a bulk walk reads a snapshot.
    """

    def __init__(self, entries):
        self._by_location: Dict[str, List[LocationEntry]] = {}
        for entry in entries:
            self._by_location.setdefault(entry.location, []).append(entry)

    def __len__(self) -> int:
        return sum(len(entries) for entries in self._by_location.values())

    def owner(self, object_key: Optional[str], include_archived: bool = False) -> Resolution:
        key = normalize_location(object_key)
        candidates = candidate_locations(key)
        if not candidates:
            return Resolution(RESOLVED_NOT_FOUND)
        if len(candidates) > MAX_CANDIDATE_LOCATIONS:
            return Resolution(RESOLVED_TOO_DEEP)
        for location in candidates:
            if not contains(location, key):
                continue
            entries = [e for e in self._by_location.get(location, ()) if include_archived or not e.archived]
            if not entries:
                continue
            live = [e for e in entries if not e.archived]
            chosen = live or entries
            if len(chosen) > 1:
                return Resolution(RESOLVED_AMBIGUOUS, candidates=tuple(chosen))
            return Resolution(RESOLVED_FOUND, entry=chosen[0])
        return Resolution(RESOLVED_NOT_FOUND)


def load_bucket_locations(table, bucket: str) -> BucketLocations:
    """Every index row of `bucket` (one Query on its `AssetLocationGSI` partition, paged on the
    presence of LastEvaluatedKey to the end), live and archived, for in-memory attribution.
    Callers must not act on it before `index_ready()` is True."""
    if not bucket:
        return BucketLocations(())
    query_kwargs = _index_query_kwargs(table.name, '#b = :b', {':b': bucket})
    query_kwargs['ExpressionAttributeNames'] = {'#b': LOCATION_BUCKET_ATTRIBUTE}
    entries: List[LocationEntry] = []
    client = table.meta.client
    while True:
        response = client.query(**query_kwargs)
        entries.extend(_entry_from_item(item) for item in response.get('Items', []))
        if 'LastEvaluatedKey' not in response:
            return BucketLocations(entries)
        query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


#######################
# Nested locations
#######################

# Pages read for the locations nested in one asset location before the index is treated as unable to
# answer for it (the caller keeps the rule that does not use the index).
MAX_NESTED_LOCATION_PAGES = 50
# How long a read operation reuses the nested locations it read for a location in this container.
NESTED_LOCATIONS_TTL_SECONDS = 10
BUCKET_ID_INDEX_NAME = "BucketIdGSI"


class NestedLocations:
    """The stored locations of other assets, live and archived, nested in one asset location
    (``is_nested_in``). ``holds(key)`` says whether a key is the asset's own file; ``keep(key)`` whether a
    key the location already contains is (no nested location holds it). Empty when nothing is nested, or
    when the caller keeps the behaviour that does not look for nested locations."""

    def __init__(self, location: Optional[str], entries: Iterable[LocationEntry] = (), source: str = "none"):
        self.location = normalize_location(location)
        self.entries: Tuple[LocationEntry, ...] = tuple(
            e for e in entries if is_nested_in(self.location, e.location))
        self.locations = frozenset(e.location for e in self.entries)
        self.source = source

    def __bool__(self) -> bool:
        return bool(self.locations)

    def __len__(self) -> int:
        return len(self.locations)

    def holder(self, object_key: Optional[str]) -> Optional[str]:
        """The nested location that holds the key, or None."""
        return deeper_holder(self.location, self.locations, object_key)

    def holds(self, object_key: Optional[str]) -> bool:
        return held_by(self.location, self.locations, object_key)

    def keep(self, object_key: Optional[str]) -> bool:
        return self.holder(object_key) is None

    def keep_aux(self, database_id: Optional[str], aux_key: Optional[str]) -> bool:
        """Whether an auxiliary-bucket key under this asset's ``{databaseId}/`` base is not a nested asset's:
        a nested asset of the same database keeps the auxiliary data under its own file bases."""
        live = (database_id or "").replace(ARCHIVED_DATABASE_SUFFIX, "")
        key = aux_key or ""
        return not any(entry.database_id.replace(ARCHIVED_DATABASE_SUFFIX, "") == live
                       and key.startswith(aux_bucket_file_base(live, entry.location))
                       for entry in self.entries)


def _nested_prefixes(location: str) -> List[str]:
    """The index prefixes under which every location nested in ``location`` sorts: the folder itself, or for a
    single file its companion folder and its preview stem (its ``.previewFile`` partners included)."""
    if location.endswith('/'):
        return [location]
    return [location + '/', location + PREVIEW_FILE_PATTERN[:-1]]


def nested_locations_in_index(table, bucket: str, location: Optional[str],
                              max_pages: Optional[int] = None) -> NestedLocations:
    """The locations nested in ``location`` in ``bucket``, from the location index (live and archived rows),
    read to the end of each prefix. Raises RuntimeError when a prefix does not finish within ``max_pages``
    (default MAX_NESTED_LOCATION_PAGES). Callers must not act on it before ``index_ready()`` is True."""
    max_pages = max_pages or MAX_NESTED_LOCATION_PAGES
    loc = normalize_location(location)
    if not bucket or not loc:
        return NestedLocations(loc, source="index")
    client, table_name = table.meta.client, table.name
    entries: List[LocationEntry] = []
    for prefix in _nested_prefixes(loc):
        query_kwargs = _index_query_kwargs(table_name, '#b = :b AND begins_with(#k, :p)',
                                           {':b': bucket, ':p': prefix})
        for _ in range(max_pages):
            response = client.query(**query_kwargs)
            entries.extend(_entry_from_item(item) for item in response.get('Items', []))
            if 'LastEvaluatedKey' not in response:
                break
            query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
        else:
            raise RuntimeError(f"Nested location query did not finish within {max_pages} pages")
    return NestedLocations(loc, entries, source="index")


def nested_locations_in_rows(rows: Iterable[Dict[str, Any]], location: Optional[str]) -> NestedLocations:
    """The locations nested in ``location`` among asset rows (live and archived) of the registrations of its
    bucket, by their stored ``assetLocation.Key``. Used before the index is ready."""
    entries = []
    for row in rows or ():
        stored = normalize_location(((row or {}).get('assetLocation') or {}).get('Key'))
        if stored:
            entries.append(LocationEntry(database_id=str(row.get('databaseId', '')),
                                         asset_id=str(row.get('assetId', '')),
                                         bucket_id=str(row.get('bucketId', '')), location=stored))
    return NestedLocations(location, entries, source="registrationWalk")


def registration_rows(table, bucket_ids: Iterable[str]) -> Iterator[Dict[str, Any]]:
    """Every asset row, live and archived, of each bucket registration (``BucketIdGSI``), paged to the end."""
    client = table.meta.client
    for bucket_id in dict.fromkeys(b for b in (bucket_ids or ()) if b):
        query_kwargs = {
            'TableName': table.name,
            'IndexName': BUCKET_ID_INDEX_NAME,
            'KeyConditionExpression': '#b = :b',
            'ExpressionAttributeNames': {'#b': 'bucketId'},
            'ExpressionAttributeValues': {':b': bucket_id},
        }
        while True:
            response = client.query(**query_kwargs)
            yield from response.get('Items', [])
            if 'LastEvaluatedKey' not in response:
                break
            query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


_nested_cache: Dict[Tuple[str, str, str], Tuple[float, NestedLocations]] = {}
_nested_cache_lock = threading.Lock()


def clear_nested_locations_cache() -> None:
    with _nested_cache_lock:
        _nested_cache.clear()


def nested_locations_of(table, bucket: str, location: Optional[str], *, asset_table_name: Optional[str] = None,
                        destructive: bool = False, bucket_ids: Iterable[str] = (),
                        clock: Callable[[], float] = time.monotonic) -> NestedLocations:
    """The locations of other assets nested in one asset's ``location`` in ``bucket``: the keys they hold are
    not that asset's files.

    Once ``index_ready()`` is True they come from the location index. Before, a ``destructive`` operation
    (archive, unarchive or permanent delete of the asset, a folder delete, archive or move) walks the asset
    rows of ``bucket_ids``, the registrations of the bucket; any other operation finds none, as before the
    index. A location the index cannot answer for within ``MAX_NESTED_LOCATION_PAGES`` takes the same
    pre-index rule. A non-destructive answer is reused for NESTED_LOCATIONS_TTL_SECONDS in this container;
    a destructive one is always read fresh. Other read errors are raised.
    """
    loc = normalize_location(location)
    if not bucket or not loc:
        return NestedLocations(loc)
    table_name = asset_table_name or getattr(table, 'name', '')
    ready = index_ready(table_name)
    if not ready and not (destructive and bucket_ids):
        return NestedLocations(loc)
    cache_key = (table_name, bucket, loc)
    if not destructive:
        with _nested_cache_lock:
            cached = _nested_cache.get(cache_key)
        if cached and clock() - cached[0] < NESTED_LOCATIONS_TTL_SECONDS:
            return cached[1]
    nested = None
    if ready:
        try:
            nested = nested_locations_in_index(table, bucket, loc)
        except RuntimeError as e:
            logger.warning(f"The location index cannot list the locations nested in {loc} ({e}); "
                           "keeping the rule that does not use it")
    if nested is None:
        nested = (nested_locations_in_rows(registration_rows(table, bucket_ids), loc)
                  if destructive and bucket_ids else NestedLocations(loc))
    if not destructive:
        with _nested_cache_lock:
            if len(_nested_cache) >= LOOKUP_CACHE_MAX_ENTRIES:
                _nested_cache.clear()
            _nested_cache[cache_key] = (clock(), nested)
    if nested:
        logger.info(f"{len(nested)} other asset location(s) lie inside {loc}; their files are left out")
    return nested


#######################
# Readiness
#######################

_index_ready_lock = threading.Lock()
_index_ready_cache: Dict[str, Tuple[bool, float]] = {}
_ssm_client = None


def index_ready_parameter_name(resource_param_prefix: Optional[str] = None) -> Optional[str]:
    """The SSM name of the completion marker for this deployment, or None without a prefix."""
    prefix = resource_param_prefix or os.environ.get("VAMS_RESOURCE_PARAM_PREFIX", "")
    prefix = prefix.rstrip('/')
    if not prefix:
        return None
    return f"{prefix}/{INDEX_READY_PARAMETER_SUFFIX}"


def marker_says_complete(value: Optional[str], asset_table_name: str) -> bool:
    """Whether a marker parameter value records a completed backfill of this marker version for
    `asset_table_name`. A marker written for another table (a deployment removed and created again
    under the same name, a restored table) does not count."""
    if not value or not asset_table_name:
        return False
    try:
        marker = json.loads(value)
    except (TypeError, ValueError):
        return False
    return (isinstance(marker, dict)
            and marker.get('version') == INDEX_READY_MARKER_VERSION
            and marker.get('status') == INDEX_READY_STATUS_COMPLETE
            and marker.get('assetTable') == asset_table_name)


def reset_index_ready_cache() -> None:
    """Forget the cached readiness answers (tests, and a caller that must re-read now)."""
    with _index_ready_lock:
        _index_ready_cache.clear()


def index_ready(asset_table_name: str, ssm_client=None, clock: Callable[[], float] = time.monotonic) -> bool:
    """Whether the location index covers every row of `asset_table_name` whose location holds keys:
    the backfill's completion marker exists, records `complete` and names that table. Until it does,
    every decision keeps the logic that does not read the index.

    Rows the backfill leaves out hold no key the index could attribute: no location, the bucket
    root (an empty location), no `bucketId`, or a bucket registration that no longer exists. A row
    at its registration's own prefix is indexed. The step's report lists both kinds.

    The answer is cached per container and table for INDEX_READY_CACHE_TTL_SECONDS either way. A
    marker that cannot be read (no prefix, a missing parameter, AccessDenied, throttling) answers
    False, so a failure falls back to the checks that do not depend on the index.
    """
    global _ssm_client
    now = clock()
    with _index_ready_lock:
        cached = _index_ready_cache.get(asset_table_name)
        if cached is not None and now - cached[1] < INDEX_READY_CACHE_TTL_SECONDS:
            return cached[0]
    name = index_ready_parameter_name()
    ready = False
    if name and asset_table_name:
        try:
            if ssm_client is None:
                if _ssm_client is None:
                    _ssm_client = boto3.client('ssm', config=_SSM_CLIENT_CONFIG)
                ssm_client = _ssm_client
            value = ssm_client.get_parameter(Name=name)['Parameter']['Value']
            ready = marker_says_complete(value, asset_table_name)
            if not ready:
                logger.warning(f"Location index marker {name} does not record a completed backfill "
                               f"of {asset_table_name}")
        except Exception as e:
            code = getattr(e, 'response', {}).get('Error', {}).get('Code', type(e).__name__)
            if code != 'ParameterNotFound':
                logger.warning(f"Location index marker {name} could not be read ({code}); "
                               "the index is treated as not ready")
    with _index_ready_lock:
        _index_ready_cache[asset_table_name] = (ready, now)
    return ready
