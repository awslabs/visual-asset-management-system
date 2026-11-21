# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Which asset an S3 object belongs to, for every consumer of an S3 record.

Rule: a file belongs to the asset whose stored location (``assetLocation.Key``) contains it, in the
same bucket. ``attribute_object`` answers it in this order:

1. The record's ``vamsBinding``, the answer bucket sync forwards on the file-indexer topic and on the
   ``asset.file.uploaded`` event. It is accepted only when its ``bucketId`` is a registration of the
   event's bucket, the asset row exists (strongly consistent read) in that registration, and the row's
   stored location contains the key. A binding that fails a check is logged and not used.
2. The location index (``assetLocations.resolve_owner``). A hit is used at any time; a miss is final
   only once ``index_ready()`` is True. A key the index cannot answer for (more candidate locations
   than it resolves, or a lookup that does not finish within its page limit) goes on to step 3, as
   before the index.
3. Before the index is ready: the stamp metadata (``databaseid``/``assetid``) the caller read from the
   object, then the first folder under a registered prefix (``assetIdGSI``), each accepted only when
   the asset row's registration is in the event's bucket and its stored location contains the key. A
   stamped file outside its asset's location, or in another bucket, is not attributed to that asset.

Two live assets on one location, or two databases with the asset id of the first folder, attribute
nothing and are logged at ERROR. The stamp's change source (``vams-changesource``) never decides
attribution; consumers that read it keep reading it themselves.

The module builds no AWS client: callers pass their asset table and bucket registration table handles
(boto3 ``Table`` resources). Registrations are cached per container for
REGISTRATION_CACHE_TTL_SECONDS.
"""

import threading
import time
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

from boto3.dynamodb.conditions import Key
from common.assetLocations import (
    ARCHIVED_DATABASE_SUFFIX,
    RESOLVED_AMBIGUOUS,
    RESOLVED_FOUND,
    RESOLVED_TOO_DEEP,
    NestedLocations,
    Resolution,
    derive_location,
    index_ready,
    nested_locations_of,
    normalize_base_prefix,
    normalize_location,
    relative_path_in,
    resolve_owner,
)
from common.s3MetadataKeys import ASSET_ID_METADATA_KEY, DATABASE_ID_METADATA_KEY
from customLogging.logger import safeLogger

logger = safeLogger()

VAMS_BINDING_FIELD = "vamsBinding"

# How an attribution was decided. Bucket sync writes the first two into `vamsBinding.resolvedBy`.
RESOLVED_BY_FAST_PATH = "fastPath"
RESOLVED_BY_LOCATION_INDEX = "locationIndex"
RESOLVED_BY_BINDING = "binding"
RESOLVED_BY_STAMP = "stamp"
RESOLVED_BY_FIRST_FOLDER = "firstFolder"
# Bucket sync: an object listed when its folder was imported, attributed to the importing asset.
RESOLVED_BY_IMPORT = "import"

ATTRIBUTED = "attributed"
NOT_ATTRIBUTED = "notAttributed"
AMBIGUOUS = "ambiguous"

REGISTRATION_CACHE_TTL_SECONDS = 300


class FileAttribution(NamedTuple):
    """The answer of `attribute_object`. `database_id` is the live id, also for an archived asset;
    `asset_row` is the row as read (from `{databaseId}#deleted` when `archived`)."""
    status: str
    database_id: str = ''
    asset_id: str = ''
    bucket_id: str = ''
    relative_path: str = ''
    resolved_by: str = ''
    location: str = ''
    asset_row: Optional[Dict[str, Any]] = None
    archived: bool = False

    @property
    def attributed(self) -> bool:
        return self.status == ATTRIBUTED


NOT_ATTRIBUTED_RESULT = FileAttribution(NOT_ATTRIBUTED)
AMBIGUOUS_RESULT = FileAttribution(AMBIGUOUS)


class RegistrationUnavailableError(RuntimeError):
    """A bucket registration could not be read (throttling, a timeout, access denied). The record can
    be attributed once the read succeeds, so callers retry it rather than treat it as unattributed."""


def live_database_id(database_id: Optional[str]) -> str:
    """A database id without the archived-partition suffix."""
    database_id = database_id or ''
    if database_id.endswith(ARCHIVED_DATABASE_SUFFIX):
        return database_id[:-len(ARCHIVED_DATABASE_SUFFIX)]
    return database_id


def binding_of(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The record's `vamsBinding` when it names a database, an asset and a bucket registration."""
    binding = (record or {}).get(VAMS_BINDING_FIELD)
    if not isinstance(binding, dict):
        return None
    if not all(isinstance(binding.get(f), str) and binding.get(f) for f in ('databaseId', 'assetId', 'bucketId')):
        return None
    return binding


#######################
# Registrations
#######################

_registration_cache: Dict[Tuple[str, str], Tuple[float, List[Dict[str, Any]]]] = {}
_registration_cache_lock = threading.Lock()


def clear_registration_cache() -> None:
    """Forget the cached bucket registrations in this container (tests)."""
    with _registration_cache_lock:
        _registration_cache.clear()


def _query_all(table, **query_kwargs) -> List[Dict[str, Any]]:
    """Every item of a query, paged on the presence of LastEvaluatedKey."""
    items: List[Dict[str, Any]] = []
    while True:
        response = table.query(**query_kwargs)
        items.extend(response.get('Items', []))
        if 'LastEvaluatedKey' not in response:
            return items
        query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


def _cached_registrations(buckets_table, cache_key: Tuple[str, str], load: Callable[[], List[Dict[str, Any]]]
                          ) -> List[Dict[str, Any]]:
    now = time.monotonic()
    with _registration_cache_lock:
        cached = _registration_cache.get(cache_key)
    if cached is not None and now - cached[0] < REGISTRATION_CACHE_TTL_SECONDS:
        return cached[1]
    rows = load()
    with _registration_cache_lock:
        _registration_cache[cache_key] = (now, rows)
    return rows


def registrations_of_bucket_id(buckets_table, bucket_id: str) -> List[Dict[str, Any]]:
    """The registration rows with this `bucketId` (one in a consistent table)."""
    if not bucket_id:
        return []
    return _cached_registrations(buckets_table, ('id', bucket_id), lambda: _query_all(
        buckets_table, KeyConditionExpression=Key('bucketId').eq(bucket_id)))


def registrations_of_bucket(buckets_table, bucket_name: str) -> List[Dict[str, Any]]:
    """Every registration row of one S3 bucket (`bucketNameGSI`), whatever its prefix."""
    if not bucket_name:
        return []
    return _cached_registrations(buckets_table, ('name', bucket_name), lambda: _query_all(
        buckets_table, IndexName='bucketNameGSI', KeyConditionExpression=Key('bucketName').eq(bucket_name)))


def registration_in_bucket(buckets_table, bucket_id: str, bucket_name: str,
                           get_registration: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None
                           ) -> Optional[Dict[str, Any]]:
    """The registration `bucket_id` when it is one of `bucket_name`'s, else None. `get_registration`
    replaces the registration read (a caller's own cached lookup by `bucketId`)."""
    if not bucket_id:
        return None
    for registration in read_registrations(buckets_table, bucket_id, get_registration):
        if registration.get('bucketName') == bucket_name:
            return registration
    return None


def read_registrations(buckets_table, bucket_id: str,
                       get_registration: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None
                       ) -> List[Dict[str, Any]]:
    """The registration rows of `bucket_id` ([] when none exists). A read that fails raises
    RegistrationUnavailableError."""
    if not bucket_id:
        return []
    try:
        if get_registration:
            registration = get_registration(bucket_id)
            return [registration] if registration else []
        return registrations_of_bucket_id(buckets_table, bucket_id)
    except Exception as e:
        raise RegistrationUnavailableError(f"Bucket registration {bucket_id} could not be read: "
                                           f"{type(e).__name__}") from e


#######################
# Asset rows
#######################

def stored_location(asset_row: Optional[Dict[str, Any]], registration: Optional[Dict[str, Any]]) -> str:
    """The asset's stored location (no leading `/`), or the folder derived from its id under the
    registration's prefix for a legacy row that carries none."""
    row = asset_row or {}
    location = normalize_location((row.get('assetLocation') or {}).get('Key'))
    if location:
        return location
    asset_id = str(row.get('assetId') or '')
    return derive_location(registration or {}, live_database_id(row.get('databaseId')), asset_id) if asset_id else ''


def read_asset_row(asset_table, database_id: str, asset_id: str, include_archived: bool
                   ) -> Tuple[Optional[Dict[str, Any]], bool]:
    """(row, archived) for an asset id pair, live partition first, strongly consistent."""
    live_id = live_database_id(database_id)
    row = asset_table.get_item(Key={'databaseId': live_id, 'assetId': asset_id}, ConsistentRead=True).get('Item')
    if row and row.get('status') != 'archived':
        return row, False
    if include_archived:
        archived = asset_table.get_item(Key={'databaseId': live_id + ARCHIVED_DATABASE_SUFFIX, 'assetId': asset_id},
                                        ConsistentRead=True).get('Item')
        if archived:
            return archived, True
        if row:
            return row, True
    return None, False


def _attribution_from_row(row: Dict[str, Any], archived: bool, registration: Dict[str, Any], object_key: str,
                          resolved_by: str, database_id: str = '', asset_id: str = ''
                          ) -> Optional[FileAttribution]:
    """The attribution of `object_key` to `row`, or None when the row's location does not contain it.
    `database_id` and `asset_id` name the row when it was read by its key."""
    row = {**row, 'databaseId': row.get('databaseId') or database_id, 'assetId': row.get('assetId') or asset_id}
    location = stored_location(row, registration)
    relative = relative_path_in(location, object_key)
    if relative is None:
        return None
    return FileAttribution(
        status=ATTRIBUTED,
        database_id=live_database_id(row.get('databaseId')),
        asset_id=str(row.get('assetId') or ''),
        bucket_id=str(row.get('bucketId') or registration.get('bucketId') or ''),
        relative_path=relative,
        resolved_by=resolved_by,
        location=location,
        asset_row=row,
        archived=archived,
    )


def _from_named_asset(readers: '_Readers', bucket_name: str, object_key: str, database_id: str,
                      asset_id: str, include_archived: bool, resolved_by: str,
                      expected_bucket_id: Optional[str] = None) -> Optional[FileAttribution]:
    """An asset named by a binding or a stamp, accepted only in the event's bucket and when its stored
    location contains the key."""
    row, archived = readers.get_row(database_id, asset_id, include_archived)
    if not row:
        return None
    row_bucket_id = str(row.get('bucketId') or '')
    if expected_bucket_id and row_bucket_id and row_bucket_id != expected_bucket_id:
        logger.warning(f"Asset {asset_id} in database {live_database_id(database_id)} is in registration "
                       f"{row_bucket_id}, not {expected_bucket_id}; {resolved_by} not used for {object_key}")
        return None
    registration_id = row_bucket_id or expected_bucket_id or ''
    registrations = readers.registrations(registration_id)
    if not registrations:
        logger.error(f"Asset {asset_id} in database {live_database_id(database_id)} names bucket registration "
                     f"{registration_id or '(none)'}, which does not exist; {resolved_by} not used for {object_key}")
        return None
    registration = next((r for r in registrations if r.get('bucketName') == bucket_name), None)
    if registration is None:
        logger.warning(f"Asset {asset_id} in database {live_database_id(database_id)} is not in bucket "
                       f"{bucket_name}; {resolved_by} not used for {object_key}")
        return None
    attribution = _attribution_from_row(row, archived, registration, object_key, resolved_by,
                                        database_id=live_database_id(database_id), asset_id=asset_id)
    if attribution is None:
        logger.warning(f"Asset {asset_id} in database {live_database_id(database_id)} does not hold "
                       f"{object_key} at its stored location; {resolved_by} not used")
    return attribution


def _from_first_folder(asset_table, buckets_table, bucket_name: str, object_key: str,
                       include_archived: bool) -> FileAttribution:
    """Today's attribution by the first folder under a registered prefix, with the bucket and
    containment checks: the asset id is looked up across databases (`assetIdGSI`) and kept only in a
    registration of this bucket whose prefix the key is under and whose stored location holds it."""
    try:
        bucket_registrations = registrations_of_bucket(buckets_table, bucket_name)
    except Exception as e:
        raise RegistrationUnavailableError(f"The registrations of bucket {bucket_name} could not be read: "
                                           f"{type(e).__name__}") from e
    registrations = sorted(bucket_registrations,
                           key=lambda r: -len(normalize_base_prefix(r.get('baseAssetsPrefix'))))
    for registration in registrations:
        prefix = normalize_base_prefix(registration.get('baseAssetsPrefix'))
        if prefix and not object_key.startswith(prefix):
            continue
        first_folder, separator, _ = object_key[len(prefix):].partition('/')
        if not first_folder or not separator:
            continue
        rows = _query_all(asset_table, IndexName='assetIdGSI', KeyConditionExpression=Key('assetId').eq(first_folder))
        live: List[FileAttribution] = []
        archived: List[FileAttribution] = []
        for row in rows:
            if str(row.get('bucketId') or '') != str(registration.get('bucketId') or ''):
                continue
            is_archived = str(row.get('databaseId') or '').endswith(ARCHIVED_DATABASE_SUFFIX) \
                or row.get('status') == 'archived'
            attribution = _attribution_from_row(row, is_archived, registration, object_key,
                                                RESOLVED_BY_FIRST_FOLDER)
            if attribution is not None:
                (archived if is_archived else live).append(attribution)
        # As before the location index: with archived rows counted, an asset id held by a live and an
        # archived asset of the registration is as ambiguous as one held by two live ones.
        counted = live + archived if include_archived else live
        if len({a.database_id for a in counted}) > 1:
            logger.error(f"Key {object_key} in bucket {bucket_name}: asset id {first_folder} is held in "
                         f"{len({a.database_id for a in counted})} databases; nothing is attributed")
            return AMBIGUOUS_RESULT
        chosen = live or (archived if include_archived else [])
        if chosen:
            return chosen[0]
        return NOT_ATTRIBUTED_RESULT
    return NOT_ATTRIBUTED_RESULT


class _Readers:
    """The row and registration reads `attribute_object` makes, from the tables or the caller's own."""

    def __init__(self, asset_table, buckets_table, get_row, get_registration):
        self.asset_table = asset_table
        self.buckets_table = buckets_table
        self._get_row = get_row
        self._get_registration = get_registration

    def get_row(self, database_id: str, asset_id: str, include_archived: bool):
        if self._get_row:
            return self._get_row(live_database_id(database_id), asset_id, include_archived)
        return read_asset_row(self.asset_table, database_id, asset_id, include_archived)

    def registrations(self, bucket_id: str) -> List[Dict[str, Any]]:
        return read_registrations(self.buckets_table, bucket_id, self._get_registration)

    def registration_in_bucket(self, bucket_id: str, bucket_name: str) -> Optional[Dict[str, Any]]:
        return registration_in_bucket(self.buckets_table, bucket_id, bucket_name, self._get_registration)


#######################
# Attribution
#######################

def attribute_object(bucket_name: str, object_key: str, *, asset_table, buckets_table,
                     binding: Optional[Dict[str, Any]] = None, include_archived: bool = False,
                     read_stamp: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
                     use_index_cache: bool = True, asset_table_name: Optional[str] = None,
                     get_row: Optional[Callable[[str, str, bool], Tuple[Optional[Dict[str, Any]], bool]]] = None,
                     get_registration: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None
                     ) -> FileAttribution:
    """The asset `object_key` in `bucket_name` belongs to (see the module docstring for the order).

    Args:
        binding: the record's `vamsBinding` (`binding_of(record)`), or None.
        include_archived: also attribute to an archived asset (delete and archive events).
        read_stamp: returns the object's S3 metadata, or None when it cannot be read. Called only
            before the index is ready and only when nothing else attributed the key.
        use_index_cache: False to resolve without the per-container index cache.
        asset_table_name: the asset table's name for `index_ready` (default `asset_table.name`).
        get_row: `(live database id, asset id, include_archived) -> (row, archived)`, replacing the
            strongly consistent asset-row reads of a binding or a stamp.
        get_registration: `bucketId -> registration row or None`, replacing the registration read.

    Raises:
        RegistrationUnavailableError: a bucket registration the answer depends on could not be read;
            the caller retries the record.
    """
    readers = _Readers(asset_table, buckets_table, get_row, get_registration)
    key = normalize_location(object_key)
    if not bucket_name or not key:
        return NOT_ATTRIBUTED_RESULT

    if binding:
        attribution = _from_named_asset(readers, bucket_name, key, binding['databaseId'],
                                        binding['assetId'], include_archived, RESOLVED_BY_BINDING,
                                        expected_bucket_id=binding['bucketId'])
        if attribution is not None:
            return attribution
        logger.warning(f"The binding of {key} in bucket {bucket_name} to "
                       f"{binding.get('databaseId')}:{binding.get('assetId')} was not accepted; resolving again")

    try:
        resolution = resolve_owner(asset_table, bucket_name, key, include_archived=include_archived,
                                   use_cache=use_index_cache)
    except RuntimeError as e:
        logger.warning(f"The location index cannot resolve {key} in bucket {bucket_name} ({e}); "
                       "attributing it as before the index")
        resolution = Resolution(RESOLVED_TOO_DEEP)
    if resolution.status == RESOLVED_AMBIGUOUS:
        logger.error(f"Key {key} in bucket {bucket_name} is in a location several assets claim; "
                     "nothing is attributed")
        return AMBIGUOUS_RESULT
    if resolution.status == RESOLVED_FOUND:
        registration = readers.registration_in_bucket(resolution.entry.bucket_id, bucket_name) or {}
        attribution = _attribution_from_row(resolution.row, resolution.entry.archived, registration, key,
                                            RESOLVED_BY_LOCATION_INDEX)
        if attribution is not None:
            return attribution

    if resolution.status != RESOLVED_TOO_DEEP and index_ready(asset_table_name or asset_table.name):
        return NOT_ATTRIBUTED_RESULT

    stamp = read_stamp() if read_stamp else None
    if stamp:
        stamped_database_id = stamp.get(DATABASE_ID_METADATA_KEY)
        stamped_asset_id = stamp.get(ASSET_ID_METADATA_KEY)
        if stamped_database_id and stamped_asset_id:
            attribution = _from_named_asset(readers, bucket_name, key, stamped_database_id,
                                            stamped_asset_id, include_archived, RESOLVED_BY_STAMP)
            if attribution is not None:
                return attribution

    return _from_first_folder(asset_table, buckets_table, bucket_name, key, include_archived)


#######################
# Nested asset locations
#######################

def nested_locations_of_asset(asset_table, buckets_table, asset: Optional[Dict[str, Any]], bucket_name: str,
                              location: Optional[str] = None, *, destructive: bool = False,
                              asset_table_name: Optional[str] = None) -> NestedLocations:
    """The stored locations of other assets nested in `asset`'s location (`location`, else its stored one) in
    `bucket_name`: the files they hold are theirs, and `asset`'s own file operations leave them alone.

    See `assetLocations.nested_locations_of`. Before the location index is ready a `destructive` operation
    walks the rows of every registration of the bucket (the asset's own registration first); the
    registrations are read only when that walk runs. A destructive answer also names the other live assets
    stored at `asset`'s own location, whose keys are left alone too."""
    row = asset or {}
    if location is None:
        location = (row.get('assetLocation') or {}).get('Key')

    def bucket_ids():
        yield row.get('bucketId')
        try:
            for registration in registrations_of_bucket(buckets_table, bucket_name):
                yield registration.get('bucketId')
        except Exception as e:
            logger.warning(f"The registrations of bucket {bucket_name} could not be read for the nested "
                           f"location walk; walking the asset's own registration: {type(e).__name__}")

    owner = (str(row.get('databaseId') or ''), str(row.get('assetId') or '')) if row.get('assetId') else None
    return nested_locations_of(asset_table, bucket_name, location, asset_table_name=asset_table_name,
                               destructive=destructive, bucket_ids=bucket_ids(), owner=owner)


def nested_locations_for_asset_type(asset_table, asset: Optional[Dict[str, Any]], bucket_name: str,
                                    location: Optional[str], *,
                                    asset_table_name: Optional[str] = None) -> Optional[NestedLocations]:
    """The nested locations whose keys an asset-type classification of `asset` leaves out, so the type comes
    from the keys the asset owns. A read (no registration walk). A lookup that fails returns None and the type
    is classified from every key the location holds, as before the rule: the type is a label, and the upload
    or copy step that sets it must not fail because of it."""
    try:
        return nested_locations_of_asset(asset_table, None, asset, bucket_name, location,
                                         asset_table_name=asset_table_name)
    except Exception as e:
        logger.warning(f"The locations nested in {location} could not be read for the asset type; classifying "
                       f"every key the location holds: {type(e).__name__}")
        return None
