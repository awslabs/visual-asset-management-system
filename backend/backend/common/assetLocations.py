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

The functions above are pure: no AWS calls.

The location index. Every asset row, live and archived, also carries two attributes, written
when the asset is created and by the v2.6-to-v2.7 `assetLocationIndex` migration step:

- ``locationBucket``: the S3 bucket name of the asset's bucket registration (the name, not the
  ``bucketId``, so every registration of one bucket shares one index partition);
- ``locationKey``: the location without a leading ``/``.

``AssetLocationGSI`` on the asset table indexes them (partition ``locationBucket``, sort
``locationKey``, projecting ``bucketId`` beside the table keys). Until the backfill has written its
completion marker the index does not cover every asset, so no decision may depend on it before
``index_ready()`` answers True; until then callers keep the checks that do not use it.

The index functions build no AWS client at import. Index reads take the caller's asset table
handle; ``index_ready()`` builds its SSM client on first use unless one is passed.
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

import boto3
from botocore.config import Config
from customLogging.logger import safeLogger

logger = safeLogger(service_name="AssetLocations")

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


LOCATION_INDEX_NAME = "AssetLocationGSI"
LOCATION_BUCKET_ATTRIBUTE = "locationBucket"
LOCATION_KEY_ATTRIBUTE = "locationKey"
ARCHIVED_DATABASE_SUFFIX = "#deleted"

RESOLVED_FOUND = "found"
RESOLVED_NOT_FOUND = "notFound"
RESOLVED_AMBIGUOUS = "ambiguous"

# Per-container cache of one index lookup (bucket, candidate location) -> the index rows there.
LOOKUP_HIT_TTL_SECONDS = 60
LOOKUP_MISS_TTL_SECONDS = 10
LOOKUP_CACHE_MAX_ENTRIES = 4096
# Candidate lookups run in parallel, at most this many at once.
LOOKUP_POOL_SIZE = 8
# Pages read for one exact-location query before it is treated as unreadable. One location holds
# one row in a consistent table; a few more exist only while an asset is archived or unarchived.
MAX_PAGES_PER_LOOKUP = 5

# The completion marker the v2.6-to-v2.7 `assetLocationIndex` migration step writes last: an SSM
# String parameter under the deployment's resource-name prefix, so every Lambda can read it with
# the parameter grant it already holds. The value is JSON with `version` and `status`.
INDEX_READY_PARAMETER_SUFFIX = "dataMigrations/assetLocationIndex"
INDEX_READY_MARKER_VERSION = 1
INDEX_READY_STATUS_COMPLETE = "complete"
INDEX_READY_CACHE_TTL_SECONDS = 300

_SSM_CLIENT_CONFIG = Config(retries={'max_attempts': 3, 'mode': 'adaptive'})


#######################
# Index attributes
#######################

def normalize_base_prefix(base_assets_prefix: Optional[str]) -> str:
    """A bucket registration's `baseAssetsPrefix` in canonical form: a trailing `/`, no leading
    `/`, and '' for the bucket root. Registrations spell one prefix as `assets`, `assets/` or
    `/assets/`; every comparison of a prefix goes through this one normalization."""
    if not base_assets_prefix:
        return ''
    if not base_assets_prefix.endswith('/'):
        base_assets_prefix += '/'
    if base_assets_prefix.startswith('/'):
        base_assets_prefix = base_assets_prefix[1:]
    return base_assets_prefix


def derive_location(registration: Dict[str, Any], database_id: str, asset_id: str) -> str:
    """The location of a new asset that names none: its folder under the registration's prefix.

    Today the layout is `{baseAssetsPrefix}{assetId}/` for every registration; the database id
    is not part of the key."""
    return normalize_base_prefix((registration or {}).get('baseAssetsPrefix')) + asset_id + '/'


def location_attributes(bucket_name: Optional[str], location_key: Optional[str]) -> Dict[str, str]:
    """The index attributes for an asset row, or {} when the row cannot be indexed (no bucket
    name, or a location at the bucket root, which an index key cannot hold)."""
    location = normalize_location(location_key)
    if not bucket_name or not location:
        return {}
    return {LOCATION_BUCKET_ATTRIBUTE: bucket_name, LOCATION_KEY_ATTRIBUTE: location}


def candidate_locations(object_key: Optional[str]) -> List[str]:
    """The locations that could own `object_key`, deepest first: the key itself, then each
    ancestor folder up to the first segment (`a/b/c.glb` gives `a/b/c.glb`, `a/b/`, `a/`)."""
    key = normalize_location(object_key)
    if not key:
        return []
    candidates = [key]
    position = len(key.rstrip('/'))
    while True:
        position = key.rfind('/', 0, position)
        if position <= 0:
            break
        candidates.append(key[:position + 1])
    return candidates


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


def _query_index(client, table_name: str, key_condition: str, values: Dict[str, Any],
                 limit: Optional[int] = None, max_pages: int = MAX_PAGES_PER_LOOKUP) -> List[LocationEntry]:
    """Every index row the key condition selects, paged on the presence of LastEvaluatedKey. With
    `limit`, stop once that many rows are in hand.

    `client` is the asset table's `meta.client`, which takes and returns plain Python values (the
    resource layer serializes them), so it is safe to share across the lookup threads."""
    query_kwargs = {
        'TableName': table_name,
        'IndexName': LOCATION_INDEX_NAME,
        'KeyConditionExpression': key_condition,
        'ExpressionAttributeNames': {'#b': LOCATION_BUCKET_ATTRIBUTE, '#k': LOCATION_KEY_ATTRIBUTE},
        'ExpressionAttributeValues': values,
    }
    if limit is not None:
        query_kwargs['Limit'] = limit
    entries: List[LocationEntry] = []
    for _ in range(max_pages):
        response = client.query(**query_kwargs)
        entries.extend(_entry_from_item(item) for item in response.get('Items', []))
        if limit is not None and len(entries) >= limit:
            return entries[:limit]
        if 'LastEvaluatedKey' not in response:
            return entries
        query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    raise RuntimeError(f"Location index query did not finish within {max_pages} pages")


def _lookup_exact(client, table_name: str, bucket: str, location: str) -> Tuple[LocationEntry, ...]:
    return tuple(_query_index(
        client, table_name, '#b = :b AND #k = :k',
        {':b': bucket, ':k': location}))


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

    Candidates are the key itself and each ancestor folder, deepest first; one exact Query each
    on the location index, run in parallel; the deepest candidate holding a row wins (longest
    match). Archived rows count only with `include_archived`, and a live row at a location wins
    over an archived one there. Two live rows on one location answer AMBIGUOUS and attribute
    nothing. A FOUND entry is confirmed with a ConsistentRead GetItem on the asset row: a row that
    is gone or no longer holds the location answers NOT_FOUND, and that lookup is dropped from
    the cache.

    Callers must not act on the answer before `index_ready()` is True.
    """
    bucket = bucket or ''
    candidates = candidate_locations(object_key)
    if not bucket or not candidates:
        return Resolution(RESOLVED_NOT_FOUND)
    lookups = _lookup_all(table, bucket, candidates, use_cache)
    for location in candidates:
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


def _conflict_candidates(location: str) -> List[str]:
    """Every stored location that equals or contains `location` in the sense of a folder: each
    ancestor folder and the location itself, each in its folder form and its single-object form
    (`a/b/` and `a/b`), the same sets an existing-key ownership check compares."""
    folder_form = location if location.endswith('/') else location + '/'
    forms: List[str] = []
    for candidate in candidate_locations(folder_form):
        for form in (candidate, candidate.rstrip('/')):
            if form and form not in forms:
                forms.append(form)
    return forms


def find_conflicts(table, bucket: str, location: str, max_descendant_pages: int = MAX_PAGES_PER_LOOKUP
                   ) -> List[LocationEntry]:
    """Index rows, live and archived, whose location equals, contains or lies under `location` in
    `bucket`: the ancestors (exact lookups, uncached) plus the first row a `begins_with` query
    finds under the location's folder form. An empty list means no conflict.

    Callers must not act on the answer before `index_ready()` is True.
    """
    location = normalize_location(location)
    if not bucket or not location:
        return []
    lookups = _lookup_all(table, bucket, _conflict_candidates(location), use_cache=False)
    conflicts: List[LocationEntry] = []
    for entries in lookups.values():
        for entry in entries:
            if entry not in conflicts:
                conflicts.append(entry)
    folder_form = location if location.endswith('/') else location + '/'
    descendants = _query_index(
        table.meta.client, table.name, '#b = :b AND begins_with(#k, :p)',
        {':b': bucket, ':p': folder_form},
        limit=1, max_pages=max_descendant_pages)
    for entry in descendants:
        if entry not in conflicts:
            conflicts.append(entry)
    return conflicts


#######################
# Readiness
#######################

_index_ready_lock = threading.Lock()
_index_ready_cache: Dict[str, Any] = {'value': None, 'at': 0.0}
_ssm_client = None


def index_ready_parameter_name(resource_param_prefix: Optional[str] = None) -> Optional[str]:
    """The SSM name of the completion marker for this deployment, or None without a prefix."""
    prefix = resource_param_prefix or os.environ.get("VAMS_RESOURCE_PARAM_PREFIX", "")
    prefix = prefix.rstrip('/')
    if not prefix:
        return None
    return f"{prefix}/{INDEX_READY_PARAMETER_SUFFIX}"


def marker_says_complete(value: Optional[str]) -> bool:
    """Whether a marker parameter value records a completed backfill of this marker version."""
    if not value:
        return False
    try:
        marker = json.loads(value)
    except (TypeError, ValueError):
        return False
    return (isinstance(marker, dict)
            and marker.get('version') == INDEX_READY_MARKER_VERSION
            and marker.get('status') == INDEX_READY_STATUS_COMPLETE)


def reset_index_ready_cache() -> None:
    """Forget the cached readiness answer (tests, and a caller that must re-read it now)."""
    with _index_ready_lock:
        _index_ready_cache['value'] = None
        _index_ready_cache['at'] = 0.0


def index_ready(ssm_client=None, clock: Callable[[], float] = time.monotonic) -> bool:
    """Whether the location index covers every asset row: the backfill's completion marker
    exists and records `complete`. Until it does, every decision keeps the logic that does not
    read the index.

    The answer is cached per container for INDEX_READY_CACHE_TTL_SECONDS either way. A marker
    that cannot be read (no prefix, a missing parameter, AccessDenied, throttling) answers False,
    so a failure falls back to the checks that do not depend on the index.
    """
    global _ssm_client
    now = clock()
    with _index_ready_lock:
        cached = _index_ready_cache['value']
        if cached is not None and now - _index_ready_cache['at'] < INDEX_READY_CACHE_TTL_SECONDS:
            return cached
    name = index_ready_parameter_name()
    ready = False
    if name:
        try:
            if ssm_client is None:
                if _ssm_client is None:
                    _ssm_client = boto3.client('ssm', config=_SSM_CLIENT_CONFIG)
                ssm_client = _ssm_client
            value = ssm_client.get_parameter(Name=name)['Parameter']['Value']
            ready = marker_says_complete(value)
            if not ready:
                logger.warning(f"Location index marker {name} does not record a completed backfill")
        except Exception as e:
            code = getattr(e, 'response', {}).get('Error', {}).get('Code', type(e).__name__)
            if code != 'ParameterNotFound':
                logger.warning(f"Location index marker {name} could not be read ({code}); "
                               "the index is treated as not ready")
    with _index_ready_lock:
        _index_ready_cache['value'] = ready
        _index_ready_cache['at'] = now
    return ready
