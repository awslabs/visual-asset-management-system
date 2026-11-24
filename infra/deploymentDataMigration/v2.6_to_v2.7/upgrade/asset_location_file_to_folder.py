#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The ``assetLocationFileToFolder`` step of the v2.6 to v2.7 migration.

From v2.7 an asset is created on a folder only: ``POST /assets`` refuses a ``bucketExistingKey`` that names a
file. This step converts every existing asset whose ``assetLocation.Key`` names a file (no trailing ``/``,
a single-file location) into a folder asset. It runs after ``assetLocationFolderKeys``, which appends the
``/`` to keys that already name a folder and moves the records of a companion copy named like the file.

A single-file location ``K = P + name`` holds the object ``K``, its own previews ``K.previewFile.{ext}`` and
its companion folder ``K/``. Each asset is planned, then converted only when its plan allows it:

- ``in-place`` (the default): the location becomes the parent folder ``P`` when ``P`` holds only the asset's
  own keys and no other asset's location is ``P`` or shares a key with it (an ancestor folder of ``P``, and a
  location already nested inside ``K/``, are allowed). No Amazon S3 object is written. The object, its
  previews and the chain of keys named after the file keep their paths; every other companion entry moves
  from ``/{path}`` to ``/{name}/{path}``, and its records move with it in the path-keyed tables.
- ``append-slash``: nothing is stored at ``K`` (no version, no delete marker), so ``K`` is not a file; the
  location becomes ``K/``.
- ``move`` (opt-in, per asset, from the resolutions file): the object and its previews are copied, every
  version in order, into the companion folder (``K/{name}``, ``K/{name}.previewFile.{ext}``), the originals
  get a delete marker (never a version delete), and the location becomes ``K/``. Every path stays the same;
  the S3 version ids in the version records are rewritten. The file's S3 key changes, so a tool that reads
  the old key no longer finds it there.
- ``include-siblings`` (opt-in, per asset, from the resolutions file): as ``in-place``, but the other files
  in ``P`` join the asset. Allowed only when no other asset's location holds a key under ``P``.

Every other layout is refused and reported with its reason, and the row is left unchanged. A dry run (the
default) writes nothing. A real run marks each converted row with ``assetLocationConversion`` (``from``,
``to``, ``mode``, ``state``): the row is flipped and marked ``pending`` in one conditional write, the records
(and, for a move, the objects) follow, and the row is marked ``complete`` last. A complete row is never read
again, so a later run never undoes a change a user made after the conversion. A ``pending`` row is resumed:
records are looked up only at the old paths the asset's keys named, a record is never written over one that
is already at its new path, and a record whose old path now names another file of the new location is left
in place and reported. A moved object's copies carry the source version id (``vams-changeassetfileversionfrom``,
the key the file move route writes), so a resumed move continues where it stopped and stops at a version it
did not write.

The location rules below mirror ``backend/backend/common/assetLocations.py`` (``contains``,
``relative_path_in``, ``object_key_for``, ``locations_share_key``); the step runs on an operator machine and
does not import backend code.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Callable, Dict, Iterator, List, NamedTuple, Optional, Set, Tuple

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

STEP = 'assetLocationFileToFolder'
CONVERSION_ATTRIBUTE = 'assetLocationConversion'
# Written by assetLocationFolderKeys on each single-file row it finished; a single-file row without it still
# has its companion copy's records at the paths v2.6 used.
COPY_DONE_ATTRIBUTE = 'assetLocationCopyRecordsMovedAt'
LOCATION_BUCKET_ATTRIBUTE = 'locationBucket'
LOCATION_KEY_ATTRIBUTE = 'locationKey'
ARCHIVED_SUFFIX = '#deleted'
PREVIEW_FILE_PATTERN = '.previewFile.'

STATE_PENDING = 'pending'
STATE_COMPLETE = 'complete'

MODE_IN_PLACE = 'in-place'
MODE_APPEND_SLASH = 'append-slash'
MODE_MOVE = 'move'
MODE_INCLUDE_SIBLINGS = 'include-siblings'
RESOLUTION_MODES = (MODE_MOVE, MODE_INCLUDE_SIBLINGS)
CONVERSION_MODES = (MODE_IN_PLACE, MODE_APPEND_SLASH, MODE_MOVE, MODE_INCLUDE_SIBLINGS)

# Refusals that only an operator decision resolves (a resolution, or work by hand); the run still exits 0.
LAYOUT_REFUSALS = ('parent-is-root', 'siblings', 'shared-parent', 'location-in-use', 'location-conflict',
                   'path-collision', 'move-blocked', 'include-siblings-blocked')
# Outcomes that make the run exit non-zero: re-run once the cause is resolved.
FAILURE_OUTCOMES = ('folder-keys-pending', 'unreadable', 'changed', 'record-clash', 'errors', 'pending')

SAMPLE_LIMIT = 20
PAGE_SIZE = 100
PACE_SECONDS = 0.05
MAX_KEY_BYTES = 1024
# Storage classes CopyObject reads without a restore.
READABLE_STORAGE_CLASSES = frozenset(('STANDARD', 'REDUCED_REDUNDANCY', 'STANDARD_IA', 'ONEZONE_IA',
                                      'INTELLIGENT_TIERING', 'GLACIER_IR', 'OUTPOSTS', 'EXPRESS_ONEZONE'))

# The object metadata the file move route writes (backend common/s3MetadataKeys.py), so bucket sync records a
# copy as a move by the system user, and so a resumed move can map each copy to its source version.
CHANGE_SOURCE_METADATA_KEY = 'vams-changesource'
CHANGE_USER_ID_METADATA_KEY = 'vams-changeuserid'
CHANGE_DATABASE_ID_FROM_METADATA_KEY = 'vams-changedatabaseidfrom'
CHANGE_ASSET_ID_FROM_METADATA_KEY = 'vams-changeassetidfrom'
CHANGE_FILE_PATH_FROM_METADATA_KEY = 'vams-changeassetfilepathfrom'
CHANGE_FILE_VERSION_FROM_METADATA_KEY = 'vams-changeassetfileversionfrom'
CHANGE_SOURCE_FILE_MOVE = 'fileMove'
SYSTEM_USER = 'SYSTEM_USER'
# The asset history change source of a location change (the one the web app labels "Edited").
HISTORY_CHANGE_SOURCE = 'edit'

_FILE_PATH_INDEX = 'DatabaseIdAssetIdFilePathIndex'
_VERSIONS_BY_ASSET_INDEX = 'databaseIdAssetIdIndex'
_HISTORY_VERSION_ID_PREFIXES = ('unarchive:',)
_NOT_FOUND_CODES = ('404', 'NoSuchKey', 'NotFound')
_COPIED_HEADERS = ('ContentType', 'CacheControl', 'ContentDisposition', 'ContentEncoding', 'ContentLanguage')

COUNTERS = ('scanned', 'candidates', 'skipped-complete', 'would-convert', 'converted', 'resumed', 'paths-moved',
            'records-moved', 'records-gone', 'records-left', 'versions-rewritten', 'objects-copied',
            'auxiliary-moved') + tuple(f"mode:{m}" for m in CONVERSION_MODES) + LAYOUT_REFUSALS + FAILURE_OUTCOMES


#######################
# Location rules (mirror of backend common/assetLocations.py)
#######################

def normalize_location(key: Optional[str]) -> str:
    return (key or '').lstrip('/')


def normalize_base_prefix(base_assets_prefix: Optional[str]) -> str:
    """A registration's base prefix as a folder ('' for the bucket root)."""
    base = (base_assets_prefix or '').strip()
    if base in ('', '/'):
        return ''
    base = base.lstrip('/')
    return base if base.endswith('/') else base + '/'


def _file_name(location: str) -> str:
    return location.rsplit('/', 1)[-1]


def _is_preview_of(base_key: str, object_key: str) -> bool:
    stem = base_key + PREVIEW_FILE_PATTERN
    if not object_key.startswith(stem):
        return False
    extension = object_key[len(stem):]
    return bool(extension) and '/' not in extension


def _split_preview(key: str) -> Tuple[str, str]:
    index = key.rfind(PREVIEW_FILE_PATTERN)
    if index > 0 and _is_preview_of(key[:index], key):
        return key[:index], key[index:]
    return key, ''


def _chain_depth(location: str, key: str) -> Optional[int]:
    name = _file_name(location)
    if key == location:
        return 0
    if not key.startswith(location + '/'):
        return None
    segments = key[len(location) + 1:].split('/')
    return len(segments) if all(segment == name for segment in segments) else None


def contains(location: Optional[str], object_key: Optional[str]) -> bool:
    loc = normalize_location(location)
    if not loc or not object_key:
        return False
    if loc.endswith('/'):
        return object_key.startswith(loc)
    return object_key == loc or object_key.startswith(loc + '/') or _is_preview_of(loc, object_key)


def relative_path_in(location: Optional[str], object_key: Optional[str]) -> Optional[str]:
    if not contains(location, object_key):
        return None
    loc = normalize_location(location)
    if loc.endswith('/'):
        return '/' + object_key[len(loc):]
    name = _file_name(loc)
    depth = _chain_depth(loc, object_key)
    if depth is not None:
        return '/' + '/'.join([name] * (depth + 1))
    body, preview = _split_preview(object_key)
    depth = _chain_depth(loc, body)
    if depth is not None:
        return '/' + '/'.join([name] * (depth + 1)) + preview
    return object_key[len(loc):]


def object_key_for(location: Optional[str], relative_path: Optional[str]) -> Optional[str]:
    loc = normalize_location(location)
    if not loc:
        return None
    relative = (relative_path or '').lstrip('/')
    if loc.endswith('/'):
        return loc + relative
    if not relative:
        return None
    if relative == loc or relative.startswith(loc + '/') or _is_preview_of(loc, relative):
        return relative
    name = _file_name(loc)
    for body, preview in ((relative, ''), _split_preview(relative)):
        segments = body.split('/')
        if all(segment == name for segment in segments):
            return loc + '/'.join([''] + [name] * (len(segments) - 1)) + preview
    return loc + '/' + relative


_ONE_KEY, _PREFIX, _PREVIEWS = 'key', 'prefix', 'previews'


def _held_sets(location: str) -> List[Tuple[str, str]]:
    if location.endswith('/'):
        return [(_PREFIX, location)]
    return [(_ONE_KEY, location), (_PREFIX, location + '/'), (_PREVIEWS, location + PREVIEW_FILE_PATTERN)]


def _set_holds(held: Tuple[str, str], key: str) -> bool:
    kind, text = held
    if kind == _ONE_KEY:
        return key == text
    if not key.startswith(text):
        return False
    rest = key[len(text):]
    return kind == _PREFIX or (bool(rest) and '/' not in rest)


def _sets_meet(first: Tuple[str, str], second: Tuple[str, str]) -> bool:
    if first[0] == _ONE_KEY:
        return _set_holds(second, first[1])
    if second[0] == _ONE_KEY:
        return _set_holds(first, second[1])
    (shorter_kind, shorter), (_, longer) = sorted((first, second), key=lambda held: len(held[1]))
    if not longer.startswith(shorter):
        return False
    return shorter_kind == _PREFIX or '/' not in longer[len(shorter):]


def locations_share_key(first: Optional[str], second: Optional[str]) -> bool:
    a, b = normalize_location(first), normalize_location(second)
    if not a or not b:
        return False
    return any(_sets_meet(x, y) for x in _held_sets(a) for y in _held_sets(b))


def parent_folder(key: str) -> str:
    """``dirname(key) + '/'``, or '' for a key at the bucket root."""
    return key.rsplit('/', 1)[0] + '/' if '/' in key else ''


def is_own_direct_key(file_key: str, key: str) -> bool:
    """Whether a key directly in the file's parent folder is the file's own: the file, the folder marker of
    the parent, or one of the file's previews."""
    return key == file_key or key == parent_folder(file_key) or _is_preview_of(file_key, key)


def path_map(file_key: str, target: str, keys: Iterator[str]) -> Dict[str, str]:
    """Old path (single-file rules at ``file_key``) to new path (folder ``target``) of every key that has a
    path in both and whose path changes."""
    moves = {}
    for key in keys:
        old = relative_path_in(file_key, key)
        if old is None or old == '/' or not key.startswith(target):
            continue
        new = '/' + key[len(target):]
        if new != old:
            moves[old] = new
    return moves


def path_collisions(target: str, moves: Dict[str, str], keys_with_versions: Set[str]) -> List[str]:
    """The old paths that name another stored key under the folder rules of ``target``: a record left there
    would describe that key, and a record moved away could not be told from one written for it."""
    return sorted(old for old in moves if target + old.lstrip('/') in keys_with_versions)


#######################
# Wire-format helpers
#######################

def _s(item: Optional[Dict], name: str) -> str:
    value = (item or {}).get(name) or {}
    return value.get('S', '') if isinstance(value, dict) else ''


def _m(item: Optional[Dict], name: str) -> Dict:
    value = (item or {}).get(name) or {}
    return value.get('M', {}) if isinstance(value, dict) else {}


def _plain_map(wire: Dict) -> Dict:
    """A wire-format map of strings and string lists as plain values."""
    plain = {}
    for name, value in wire.items():
        if isinstance(value, dict) and 'S' in value:
            plain[name] = value['S']
        elif isinstance(value, dict) and 'L' in value:
            plain[name] = [entry.get('S', '') for entry in value['L'] if isinstance(entry, dict)]
    return plain


def _location_of(row: Dict) -> str:
    return normalize_location(_s(_m(row, 'assetLocation'), 'Key'))


def _live_database_id(database_id: str) -> str:
    return database_id[:-len(ARCHIVED_SUFFIX)] if database_id.endswith(ARCHIVED_SUFFIX) else database_id


def _paginate(read: Callable[..., Dict], **kwargs) -> Iterator[Dict]:
    while True:
        page = read(**kwargs)
        yield from page.get('Items', [])
        if not page.get('LastEvaluatedKey'):
            return
        kwargs['ExclusiveStartKey'] = page['LastEvaluatedKey']


def _query_all(dynamodb_client, table: str, key_name: str, value: str, index: Optional[str] = None) -> List[Dict]:
    kwargs = {'TableName': table, 'KeyConditionExpression': '#p = :v',
              'ExpressionAttributeNames': {'#p': key_name}, 'ExpressionAttributeValues': {':v': {'S': value}}}
    if index:
        kwargs['IndexName'] = index
    return list(_paginate(dynamodb_client.query, **kwargs))


def _error_code(error: ClientError) -> str:
    return str(error.response.get('Error', {}).get('Code', ''))


#######################
# Amazon S3 reads
#######################

def list_versions(s3_client, bucket: str, prefix: str) -> Iterator[Dict]:
    """Every version and delete marker under ``prefix`` as ``{'Key', 'VersionId', 'IsLatest', 'StorageClass',
    'Marker', 'LastModified'}``."""
    kwargs = {'Bucket': bucket, 'Prefix': prefix}
    while True:
        page = s3_client.list_object_versions(**kwargs)
        for entry in page.get('Versions', []):
            yield dict(entry, Marker=False)
        for entry in page.get('DeleteMarkers', []):
            yield dict(entry, Marker=True)
        if not page.get('IsTruncated'):
            return
        kwargs['KeyMarker'] = page.get('NextKeyMarker')
        kwargs['VersionIdMarker'] = page.get('NextVersionIdMarker')


def exact_versions(s3_client, bucket: str, key: str) -> List[Dict]:
    """The versions and delete markers of exactly ``key``: the versions newest first (the order Amazon S3 lists
    them in), then the delete markers. The listing names ``key`` as its prefix, so it starts at ``key`` (the
    shortest key under it) and stops at the first other key."""
    versions, markers = [], []
    kwargs = {'Bucket': bucket, 'Prefix': key}
    while True:
        page = s3_client.list_object_versions(**kwargs)
        versions.extend(dict(e, Marker=False) for e in page.get('Versions', []) if e['Key'] == key)
        markers.extend(dict(e, Marker=True) for e in page.get('DeleteMarkers', []) if e['Key'] == key)
        listed = page.get('Versions', []) + page.get('DeleteMarkers', [])
        if any(e['Key'] != key for e in listed) or not page.get('IsTruncated'):
            return versions + markers
        kwargs['KeyMarker'] = page.get('NextKeyMarker')
        kwargs['VersionIdMarker'] = page.get('NextVersionIdMarker')


def latest_is_marker(entries: List[Dict]) -> bool:
    """Whether the newest entry of a key (``exact_versions``) is a delete marker: an archived file."""
    return any(e['Marker'] and e.get('IsLatest') for e in entries)


def own_files(s3_client, bucket: str, file_key: str) -> List[str]:
    """The file key and its own previews that have any version or delete marker."""
    keys = [file_key] if exact_versions(s3_client, bucket, file_key) else []
    keys += sorted({e['Key'] for e in list_versions(s3_client, bucket, file_key + PREVIEW_FILE_PATTERN)
                    if _is_preview_of(file_key, e['Key'])})
    return keys


def keys_with_versions(s3_client, bucket: str, prefix: str) -> Set[str]:
    return {entry['Key'] for entry in list_versions(s3_client, bucket, prefix)}


def direct_entries(s3_client, bucket: str, folder: str) -> Tuple[Set[str], Set[str]]:
    """The keys (with any version or delete marker) directly in ``folder`` and its sub-folder prefixes."""
    keys, prefixes = set(), set()
    kwargs = {'Bucket': bucket, 'Prefix': folder, 'Delimiter': '/'}
    while True:
        page = s3_client.list_object_versions(**kwargs)
        keys.update(e['Key'] for e in page.get('Versions', []) + page.get('DeleteMarkers', []))
        prefixes.update(p['Prefix'] for p in page.get('CommonPrefixes', []))
        if not page.get('IsTruncated'):
            return keys, prefixes
        kwargs['KeyMarker'] = page.get('NextKeyMarker')
        kwargs['VersionIdMarker'] = page.get('NextVersionIdMarker')


def _sample(items) -> Tuple[List[str], int]:
    ordered = sorted(items)
    return ordered[:SAMPLE_LIMIT], len(ordered)


#######################
# Plans
#######################

class Candidate(NamedTuple):
    database_id: str
    asset_id: str
    bucket_id: str
    location: str
    copy_done: bool
    conversion: Dict[str, str]


class Plan(NamedTuple):
    outcome: str                 # a conversion mode, a refusal class, or 'resume'
    target: str = ''
    detail: str = ''
    samples: Tuple[str, ...] = ()
    sample_count: int = 0
    moves: Optional[Dict[str, str]] = None
    keys: Optional[Set[str]] = None
    resolution: str = ''
    archived: Tuple[str, ...] = ()

    @property
    def converts(self) -> bool:
        return self.outcome in CONVERSION_MODES or self.outcome == 'resume'


class Context(NamedTuple):
    """What the planner needs to know about the whole deployment."""
    registrations: Dict[str, Tuple[str, str]]        # bucketId -> (bucket name, base prefix)
    locations: Dict[str, List[Tuple[str, str, str]]]  # bucket name -> [(location, databaseId, assetId)]
    candidate_keys: Dict[str, Set[str]]              # bucket name -> file keys of every candidate row
    resolutions: Dict[Tuple[str, str], Dict[str, str]]


def load_resolutions(path: Optional[str]) -> Dict[Tuple[str, str], Dict[str, str]]:
    """The operator's per-asset choices: one JSON object per line with ``databaseId``, ``assetId``, ``fromKey``
    (the location the choice was made for, as the report wrote it) and ``resolution`` (``move`` or
    ``include-siblings``). A line that is not such an object fails the step before it reads anything."""
    if not path:
        return {}
    resolutions = {}
    with open(path, 'r', encoding='utf-8') as f:
        for number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path} line {number} is not JSON: {e}") from e
            fields = ('databaseId', 'assetId', 'fromKey', 'resolution')
            if not isinstance(entry, dict) or any(not isinstance(entry.get(k), str) or not entry.get(k)
                                                  for k in fields):
                raise ValueError(f"{path} line {number} needs the string fields {', '.join(fields)}")
            if entry['resolution'] not in RESOLUTION_MODES:
                raise ValueError(f"{path} line {number}: resolution must be one of {', '.join(RESOLUTION_MODES)}")
            identity = (entry['databaseId'], entry['assetId'])
            if identity in resolutions:
                raise ValueError(f"{path} line {number} repeats {identity[0]} / {identity[1]}")
            resolutions[identity] = {k: entry[k] for k in fields}
    return resolutions


def _others(context: Context, bucket: str, candidate: Candidate) -> Iterator[str]:
    for location, database_id, asset_id in context.locations.get(bucket, []):
        if (database_id, asset_id) != (candidate.database_id, candidate.asset_id):
            yield location


def _conflicts(context: Context, bucket: str, candidate: Candidate, folder: str) -> Tuple[List[str], List[str],
                                                                                          List[str]]:
    """``(equal, ancestors, conflicting)`` other locations for ``folder`` becoming the location: equal to it,
    an ancestor folder of it (allowed, the asset is nested in it), and any other location that holds one of
    its keys and is not already nested in the file's own location."""
    equal, ancestors, conflicting = [], [], []
    for location in _others(context, bucket, candidate):
        if location == folder:
            equal.append(location)
        elif not locations_share_key(folder, location):
            continue
        elif location.endswith('/') and folder.startswith(location):
            ancestors.append(location)
        elif len(location) > len(candidate.location) and locations_share_key(candidate.location, location):
            continue
        else:
            conflicting.append(location)
    return equal, ancestors, conflicting


def _below_base(folder: str, base: str) -> bool:
    return bool(folder) and folder != base and folder.startswith(base)


def plan_candidate(s3_client, context: Context, candidate: Candidate, dry_run: bool) -> Plan:
    """Decide what to do with one row whose location names no folder, or whose conversion is pending."""
    key = candidate.location
    if candidate.conversion.get('state') == STATE_PENDING:
        return Plan('resume', target=candidate.conversion.get('to', ''), detail=candidate.conversion.get('mode', ''))
    if candidate.conversion.get('state') == STATE_COMPLETE:
        return Plan('changed', detail='the row was converted before and its location changed since')
    bucket, base = context.registrations.get(candidate.bucket_id, ('', ''))
    if not bucket:
        return Plan('unreadable', detail='the asset bucket is not registered in the S3 asset buckets table')
    base = normalize_base_prefix(base)
    try:
        own = exact_versions(s3_client, bucket, key)
    except ClientError as e:
        return Plan('unreadable', detail=f"ListObjectVersions: {_error_code(e) or e}")
    if not own:
        return _plan_append_slash(s3_client, context, candidate, bucket)
    if not candidate.copy_done and not dry_run:
        return Plan('folder-keys-pending', detail='run assetLocationFolderKeys for this asset first')
    resolution = context.resolutions.get((candidate.database_id, candidate.asset_id))
    chosen = ''
    if resolution and resolution['fromKey'] == key:
        chosen = resolution['resolution']
    try:
        if chosen == MODE_MOVE:
            plan = _plan_move(s3_client, context, candidate, bucket)
        elif chosen == MODE_INCLUDE_SIBLINGS:
            plan = _plan_include_siblings(s3_client, context, candidate, bucket, base)
        else:
            plan = _plan_in_place(s3_client, context, candidate, bucket, base)
    except ClientError as e:
        return Plan('unreadable', detail=f"{_error_code(e) or e}")
    if resolution and not chosen:
        plan = plan._replace(detail=(plan.detail + '; ' if plan.detail else '') +
                             'the resolution names another fromKey and was not used')
    if not candidate.copy_done:
        plan = plan._replace(detail=(plan.detail + '; ' if plan.detail else '') +
                             'after assetLocationFolderKeys has run for this asset')
    return plan._replace(resolution=chosen)


def _plan_append_slash(s3_client, context: Context, candidate: Candidate, bucket: str) -> Plan:
    target = candidate.location + '/'
    equal, _, _ = _conflicts(context, bucket, candidate, target)
    if equal:
        samples, count = _sample(equal)
        return Plan('location-in-use', target=target, samples=tuple(samples), sample_count=count,
                    detail='another asset is stored at the folder')
    try:
        keys = keys_with_versions(s3_client, bucket, target)
    except ClientError as e:
        return Plan('unreadable', detail=f"ListObjectVersions: {_error_code(e) or e}")
    moves = path_map(candidate.location, target, iter(keys))
    collisions = path_collisions(target, moves, keys)
    if collisions:
        samples, count = _sample(collisions)
        return Plan('path-collision', target=target, samples=tuple(samples), sample_count=count,
                    detail='an old path names another file in the folder')
    return Plan(MODE_APPEND_SLASH, target=target, moves=moves, keys=keys,
                detail='nothing is stored at the key')


def _sibling_check(s3_client, context: Context, candidate: Candidate, bucket: str, parent: str
                   ) -> Tuple[List[str], List[str]]:
    """``(siblings, other candidates)`` directly in ``parent``: every key or sub-folder that is not the
    file's own, and the file keys of other rows the step converts that sit there."""
    keys, prefixes = direct_entries(s3_client, bucket, parent)
    key = candidate.location
    siblings = [k for k in keys if not is_own_direct_key(key, k)] + [p for p in prefixes if p != key + '/']
    others = [k for k in context.candidate_keys.get(bucket, set()) if k != key and parent_folder(k) == parent]
    return siblings, others


def _plan_in_place(s3_client, context: Context, candidate: Candidate, bucket: str, base: str) -> Plan:
    key = candidate.location
    parent = parent_folder(key)
    if not _below_base(parent, base):
        return Plan('parent-is-root', target=parent, detail='the file is at the root of the bucket location')
    siblings, others = _sibling_check(s3_client, context, candidate, bucket, parent)
    if others:
        samples, count = _sample(others)
        return Plan('shared-parent', target=parent, samples=tuple(samples), sample_count=count,
                    detail='other file-located assets are in the same folder')
    if siblings:
        samples, count = _sample(siblings)
        return Plan('siblings', target=parent, samples=tuple(samples), sample_count=count,
                    detail='the folder holds other files')
    equal, ancestors, conflicting = _conflicts(context, bucket, candidate, parent)
    if equal:
        samples, count = _sample(equal)
        return Plan('location-in-use', target=parent, samples=tuple(samples), sample_count=count,
                    detail='another asset is stored at the folder')
    if conflicting:
        samples, count = _sample(conflicting)
        return Plan('location-conflict', target=parent, samples=tuple(samples), sample_count=count,
                    detail='another asset location holds keys in the folder')
    keys = keys_with_versions(s3_client, bucket, parent)
    moves = path_map(key, parent, iter(keys))
    collisions = path_collisions(parent, moves, keys)
    if collisions:
        samples, count = _sample(collisions)
        return Plan('path-collision', target=parent, samples=tuple(samples), sample_count=count,
                    detail='an old path names another file in the folder')
    detail = f"nested in {', '.join(sorted(ancestors))}" if ancestors else ''
    return Plan(MODE_IN_PLACE, target=parent, moves=moves, keys=keys, detail=detail)


def _plan_include_siblings(s3_client, context: Context, candidate: Candidate, bucket: str, base: str) -> Plan:
    key = candidate.location
    parent = parent_folder(key)
    if not _below_base(parent, base):
        return Plan('include-siblings-blocked', target=parent, detail='the file is at the root of the bucket location')
    siblings, others = _sibling_check(s3_client, context, candidate, bucket, parent)
    if others:
        samples, count = _sample(others)
        return Plan('include-siblings-blocked', target=parent, samples=tuple(samples), sample_count=count,
                    detail='other file-located assets are in the same folder')
    equal, _, conflicting = _conflicts(context, bucket, candidate, parent)
    if equal or conflicting:
        samples, count = _sample(equal + conflicting)
        return Plan('include-siblings-blocked', target=parent, samples=tuple(samples), sample_count=count,
                    detail='another asset location holds keys in the folder')
    keys = keys_with_versions(s3_client, bucket, parent)
    moves = path_map(key, parent, iter(keys))
    collisions = path_collisions(parent, moves, keys)
    if collisions:
        samples, count = _sample(collisions)
        return Plan('include-siblings-blocked', target=parent, samples=tuple(samples), sample_count=count,
                    detail='an old path names a sibling file')
    samples, count = _sample(siblings)
    return Plan(MODE_INCLUDE_SIBLINGS, target=parent, moves=moves, keys=keys, samples=tuple(samples),
                sample_count=count, detail=f"{count} sibling entr{'y' if count == 1 else 'ies'} join the asset")


def _plan_move(s3_client, context: Context, candidate: Candidate, bucket: str) -> Plan:
    key = candidate.location
    target = key + '/'
    name = _file_name(key)
    moved = f"{target}{name}"

    def blocked(reason: str, samples: List[str] = ()) -> Plan:
        sample, count = _sample(samples)
        return Plan('move-blocked', target=target, detail=reason, samples=tuple(sample), sample_count=count)

    if candidate.database_id.endswith(ARCHIVED_SUFFIX):
        return blocked('the asset is archived; unarchive it first')
    if s3_client.get_bucket_versioning(Bucket=bucket).get('Status') != 'Enabled':
        return blocked('S3 Versioning is not enabled on the bucket')
    sources = [e for source in own_files(s3_client, bucket, key) for e in exact_versions(s3_client, bucket, source)]
    cold = sorted({e['Key'] for e in sources if not e['Marker'] and
                   (e.get('StorageClass') or 'STANDARD') not in READABLE_STORAGE_CLASSES})
    if cold:
        return blocked('a version is in a storage class that needs a restore first', cold)
    taken = sorted({e['Key'] for e in list_versions(s3_client, bucket, moved)
                    if e['Key'] == moved or _is_preview_of(moved, e['Key']) or e['Key'].startswith(moved + '/')})
    if taken:
        return blocked('the target key in the companion folder is taken', taken)
    longest = max((len((moved + k[len(key):]).encode('utf-8')) for k in {e['Key'] for e in sources}), default=0)
    if longest > MAX_KEY_BYTES:
        return blocked('the moved key would exceed 1024 bytes')
    equal, _, _ = _conflicts(context, bucket, candidate, target)
    if equal:
        return blocked('another asset is stored at the companion folder', equal)
    holders = [loc for loc in _others(context, bucket, candidate) if locations_share_key(moved, loc)]
    if holders:
        return blocked('another asset location holds the target key', holders)
    archived = tuple(source for source in own_files(s3_client, bucket, key)
                     if latest_is_marker(exact_versions(s3_client, bucket, source)))
    return Plan(MODE_MOVE, target=target, moves={}, keys=set(), archived=archived)


#######################
# Writes
#######################

def flip_row(dynamodb_client, asset_table: str, candidate: Candidate, bucket: str, plan: Plan, started: str) -> str:
    """Set the folder location and mark the conversion pending, in one write conditional on the location
    still being the file key and the row carrying no conversion. Returns 'flipped' or 'changed'."""
    conversion = {'from': {'S': candidate.location}, 'to': {'S': plan.target}, 'mode': {'S': plan.outcome},
                  'state': {'S': STATE_PENDING}, 'startedAt': {'S': started},
                  'archived': {'L': [{'S': key} for key in plan.archived]}}
    try:
        dynamodb_client.update_item(
            TableName=asset_table,
            Key={'databaseId': {'S': candidate.database_id}, 'assetId': {'S': candidate.asset_id}},
            UpdateExpression='SET #al.#k = :to, #lb = :bucket, #lk = :to, #c = :conv',
            ConditionExpression='attribute_exists(assetId) AND #al.#k = :from AND attribute_not_exists(#c)',
            ExpressionAttributeNames={'#al': 'assetLocation', '#k': 'Key', '#lb': LOCATION_BUCKET_ATTRIBUTE,
                                      '#lk': LOCATION_KEY_ATTRIBUTE, '#c': CONVERSION_ATTRIBUTE},
            ExpressionAttributeValues={':to': {'S': plan.target}, ':bucket': {'S': bucket},
                                       ':conv': {'M': conversion}, ':from': {'S': candidate.location}},
        )
        return 'flipped'
    except ClientError as e:
        if _error_code(e) == 'ConditionalCheckFailedException':
            return 'changed'
        raise


def complete_row(dynamodb_client, asset_table: str, candidate: Candidate) -> str:
    try:
        dynamodb_client.update_item(
            TableName=asset_table,
            Key={'databaseId': {'S': candidate.database_id}, 'assetId': {'S': candidate.asset_id}},
            UpdateExpression='SET #c.#s = :done, #c.#t = :now',
            ConditionExpression='#c.#s = :pending',
            ExpressionAttributeNames={'#c': CONVERSION_ATTRIBUTE, '#s': 'state', '#t': 'completedAt'},
            ExpressionAttributeValues={':done': {'S': STATE_COMPLETE}, ':pending': {'S': STATE_PENDING},
                                       ':now': {'S': datetime.now(timezone.utc).isoformat()}},
        )
        return 'completed'
    except ClientError as e:
        if _error_code(e) == 'ConditionalCheckFailedException':
            return 'changed'
        raise


def write_history(dynamodb_client, history_table: Optional[str], row: Dict, started: str, target: str) -> None:
    """One asset history record for the location change, keyed by the conversion's start so a resumed run
    writes it once."""
    if not history_table:
        return
    database_id = _live_database_id(_s(row, 'databaseId'))
    asset_id = _s(row, 'assetId')
    snapshot = {'assetLocationKey': {'S': target}}
    for name in ('assetName', 'description', 'bucketId'):
        if _s(row, name):
            snapshot[name] = {'S': _s(row, name)}
    for name in ('isDistributable', 'tags'):
        if name in row:
            snapshot[name] = row[name]
    item = {'databaseId:assetId': {'S': f"{database_id}:{asset_id}"},
            'historyRecordId': {'S': f"{started}#{STEP}"},
            'databaseId': {'S': database_id}, 'assetId': {'S': asset_id}, 'recordDate': {'S': started},
            'changeSource': {'S': HISTORY_CHANGE_SOURCE}, 'changeUserId': {'S': SYSTEM_USER},
            'assetSnapshot': {'M': snapshot}}
    try:
        dynamodb_client.put_item(TableName=history_table, Item=item,
                                 ConditionExpression='attribute_not_exists(historyRecordId)')
    except ClientError as e:
        if _error_code(e) != 'ConditionalCheckFailedException':
            raise


def move_record(dynamodb_client, table: str, item: Dict, key_names: Tuple[str, ...], new_item: Dict,
                dry_run: bool) -> str:
    """Move one record to its new key in one transaction: the put needs no record at the new key and the
    delete needs the old one to still be there. Returns 'moved' ('would move'), 'clash' (a record is already
    at the new key; nothing changes) or 'gone' (the old record was removed meanwhile)."""
    target_key = {name: new_item[name] for name in key_names}
    names = {'#k': key_names[0]}
    if dry_run:
        found = dynamodb_client.get_item(TableName=table, Key=target_key)
        return 'clash' if 'Item' in found else 'would move'
    try:
        dynamodb_client.transact_write_items(TransactItems=[
            {'Put': {'TableName': table, 'Item': new_item, 'ConditionExpression': 'attribute_not_exists(#k)',
                     'ExpressionAttributeNames': names}},
            {'Delete': {'TableName': table, 'Key': {name: item[name] for name in key_names},
                        'ConditionExpression': 'attribute_exists(#k)', 'ExpressionAttributeNames': names}},
        ])
        return 'moved'
    except ClientError as e:
        if _error_code(e) != 'TransactionCanceledException':
            raise
    return 'clash' if 'Item' in dynamodb_client.get_item(TableName=table, Key=target_key) else 'gone'


class RecordMover:
    """Moves one asset's records from old paths to new paths in the path-keyed tables, or rewrites the S3
    version ids of one path's version records. Each outcome is counted and reported."""

    def __init__(self, dynamodb_client, cfg: Dict, database_id: str, asset_id: str, dry_run: bool,
                 counts: Dict[str, int], report: Callable[[Dict], None], base: Dict):
        self.client = dynamodb_client
        self.cfg = cfg
        self.asset = f"{_live_database_id(database_id)}:{asset_id}"
        self.asset_id = asset_id
        self.dry_run = dry_run
        self.counts = counts
        self.report = report
        self.base = base

    def _outcome(self, label: str, old: str, new: str, outcome: str, detail: str = '') -> None:
        key = {'moved': 'records-moved', 'would move': 'records-moved', 'clash': 'record-clash',
               'gone': 'records-gone', 'left': 'records-left', 'rewritten': 'versions-rewritten',
               'would rewrite': 'versions-rewritten', 'failed': 'errors'}[outcome]
        self.counts[key] += 1
        record = dict(self.base, part='records', table=label, **{'from': old, 'to': new}, action=outcome)
        if detail:
            record['detail'] = detail
        self.report(record)

    def _move(self, label: str, table: str, item: Dict, key_names: Tuple[str, ...], changes: Dict[str, str],
              old: str, new: str) -> None:
        new_item = dict(item)
        new_item.update({attr: {'S': value} for attr, value in changes.items()})
        try:
            outcome = move_record(self.client, table, item, key_names, new_item, self.dry_run)
        except ClientError as e:
            self._outcome(label, old, new, 'failed', str(e))
            return
        self._outcome(label, old, new, outcome)

    def move_paths(self, moves: Dict[str, str], guard: Callable[[str], bool]) -> None:
        """Move every record at an old path of ``moves`` to its new path. ``guard(old)`` is True when the old
        path now names a stored key of the new location; such a path's records are left and reported."""
        forms = {}
        for old, new in moves.items():
            if guard(old):
                self._outcome('all tables', old, new, 'left', 'the old path names a file in the new location')
                continue
            forms[old] = new
            forms[old.lstrip('/')] = new.lstrip('/')
        for label, table_key, item_key in (('file metadata', 'asset_file_metadata_storage_table_name', 'metadataKey'),
                                           ('file attributes', 'file_attribute_storage_table_name', 'attributeKey')):
            table = self.cfg.get(table_key)
            if not table:
                continue
            for old, new in forms.items():
                for item in _query_all(self.client, table, 'databaseId:assetId:filePath', f"{self.asset}:{old}",
                                       _FILE_PATH_INDEX):
                    changes = {'databaseId:assetId:filePath': f"{self.asset}:{new}"}
                    if _s(item, 'filePath') == old:
                        changes['filePath'] = new
                    self._move(label, table, item, (item_key, 'databaseId:assetId:filePath'), changes, old, new)
        table = self.cfg.get('asset_file_versions_storage_table_name')
        if table:
            for item in _query_all(self.client, table, 'databaseId:assetId', self.asset, _VERSIONS_BY_ASSET_INDEX):
                old = _s(item, 'fileKey')
                if old in forms:
                    self._move('asset version files', table, item, ('databaseId:assetId:assetVersionId', 'fileKey'),
                               {'fileKey': forms[old]}, old, forms[old])
        legacy = self.cfg.get('legacy_asset_file_versions_storage_table_name')
        versions_table = self.cfg.get('asset_versions_storage_table_name')
        if legacy and versions_table:
            for version_row in _query_all(self.client, versions_table, 'databaseId:assetId', self.asset):
                version = _s(version_row, 'assetVersionId')
                for item in _query_all(self.client, legacy, 'assetId:assetVersionId', f"{self.asset_id}:{version}"):
                    old = _s(item, 'fileKey')
                    if old in forms:
                        self._move('asset version files (legacy)', legacy, item,
                                   ('assetId:assetVersionId', 'fileKey'), {'fileKey': forms[old]}, old, forms[old])
        table = self.cfg.get('asset_file_metadata_versions_storage_table_name')
        if table:
            sort_name = 'type:filePath:metadataKey'
            for item in _query_all(self.client, table, 'databaseId:assetId', self.asset, _VERSIONS_BY_ASSET_INDEX):
                kind, _, rest = _s(item, sort_name).partition(':')
                matches = [rest[:i] for i, char in enumerate(rest) if char == ':' and rest[:i] in forms]
                if not matches:
                    continue
                old = max(matches, key=len)
                self._move('metadata versions', table, item, ('databaseId:assetId:assetVersionId', sort_name),
                           {sort_name: f"{kind}:{forms[old]}:{rest[len(old) + 1:]}"}, old, forms[old])
        table = self.cfg.get('asset_file_version_history_storage_table_name')
        if table:
            for old, new in forms.items():
                for item in _query_all(self.client, table, 'databaseId:assetId:filePath', f"{self.asset}:{old}"):
                    changes = {'databaseId:assetId:filePath': f"{self.asset}:{new}"}
                    if _s(item, 'filePath') == old:
                        changes['filePath'] = new
                    self._move('file version history', table, item, ('databaseId:assetId:filePath', 'versionId'),
                               changes, old, new)

    def rewrite_versions(self, path: str, version_map: Dict[str, str]) -> None:
        """Replace the S3 version ids of ``path`` (old to new) in its version records and history."""
        if not version_map:
            return
        forms = (path, path.lstrip('/'))

        def mapped(version_id: str) -> Optional[str]:
            for prefix in ('',) + _HISTORY_VERSION_ID_PREFIXES:
                if version_id.startswith(prefix) and version_id[len(prefix):] in version_map:
                    return prefix + version_map[version_id[len(prefix):]]
            return None

        def rewrite(label: str, table: str, item: Dict, key: Dict) -> None:
            old, new = _s(item, 'versionId'), mapped(_s(item, 'versionId'))
            if new is None:
                return
            if self.dry_run:
                self._outcome(label, f"{path}@{old}", f"{path}@{new}", 'would rewrite')
                return
            try:
                self.client.update_item(TableName=table, Key=key, UpdateExpression='SET versionId = :new',
                                        ConditionExpression='versionId = :old',
                                        ExpressionAttributeValues={':new': {'S': new}, ':old': {'S': old}})
                self._outcome(label, f"{path}@{old}", f"{path}@{new}", 'rewritten')
            except ClientError as e:
                if _error_code(e) == 'ConditionalCheckFailedException':
                    self._outcome(label, f"{path}@{old}", f"{path}@{new}", 'gone')
                else:
                    self._outcome(label, f"{path}@{old}", f"{path}@{new}", 'failed', str(e))

        table = self.cfg.get('asset_file_versions_storage_table_name')
        if table:
            for item in _query_all(self.client, table, 'databaseId:assetId', self.asset, _VERSIONS_BY_ASSET_INDEX):
                if _s(item, 'fileKey') in forms:
                    rewrite('asset version files', table, item, {
                        'databaseId:assetId:assetVersionId': item['databaseId:assetId:assetVersionId'],
                        'fileKey': item['fileKey']})
        legacy = self.cfg.get('legacy_asset_file_versions_storage_table_name')
        versions_table = self.cfg.get('asset_versions_storage_table_name')
        if legacy and versions_table:
            for version_row in _query_all(self.client, versions_table, 'databaseId:assetId', self.asset):
                version = _s(version_row, 'assetVersionId')
                for form in forms:
                    item = self.client.get_item(TableName=legacy, Key={
                        'assetId:assetVersionId': {'S': f"{self.asset_id}:{version}"},
                        'fileKey': {'S': form}}).get('Item')
                    if item:
                        rewrite('asset version files (legacy)', legacy, item, {
                            'assetId:assetVersionId': item['assetId:assetVersionId'], 'fileKey': item['fileKey']})
        table = self.cfg.get('asset_file_version_history_storage_table_name')
        if table:
            for form in forms:
                for item in _query_all(self.client, table, 'databaseId:assetId:filePath', f"{self.asset}:{form}"):
                    new = mapped(_s(item, 'versionId'))
                    if new is not None:
                        self._move('file version history', table, item, ('databaseId:assetId:filePath', 'versionId'),
                                   {'versionId': new}, f"{form}@{_s(item, 'versionId')}", f"{form}@{new}")


#######################
# The move of the object into its companion folder
#######################

def _copy_metadata(head: Dict, database_id: str, asset_id: str, path: str, source_version: str) -> Dict[str, str]:
    metadata = dict(head.get('Metadata') or {})
    metadata.update({
        CHANGE_SOURCE_METADATA_KEY: CHANGE_SOURCE_FILE_MOVE,
        CHANGE_USER_ID_METADATA_KEY: SYSTEM_USER,
        CHANGE_DATABASE_ID_FROM_METADATA_KEY: database_id,
        CHANGE_ASSET_ID_FROM_METADATA_KEY: asset_id,
        CHANGE_FILE_PATH_FROM_METADATA_KEY: path,
        CHANGE_FILE_VERSION_FROM_METADATA_KEY: source_version,
    })
    return metadata


def copied_versions(s3_client, bucket: str, target_key: str, sources: Set[str]) -> Tuple[Dict[str, str], List[str]]:
    """``(source version -> target version, foreign target versions)`` of the copies already at
    ``target_key``. A version that carries no source version id of ``sources`` was not written by the step."""
    mapping, foreign = {}, []
    for entry in exact_versions(s3_client, bucket, target_key):
        if entry['Marker']:
            continue
        head = s3_client.head_object(Bucket=bucket, Key=target_key, VersionId=entry['VersionId'])
        source = (head.get('Metadata') or {}).get(CHANGE_FILE_VERSION_FROM_METADATA_KEY, '')
        if source in sources:
            mapping[source] = entry['VersionId']
        else:
            foreign.append(entry['VersionId'])
    return mapping, foreign


def move_object(s3_client, bucket: str, source_key: str, target_key: str, database_id: str, asset_id: str,
                path: str, archived: bool) -> Tuple[str, Dict[str, str]]:
    """Copy every version of ``source_key`` to ``target_key``, oldest first, with the file move route's
    metadata; put a delete marker on the target when the source was archived (``archived``, read before the
    conversion started) and one on the source. Returns ``(outcome, source version -> target version)``; the
    outcome is 'moved', or 'changed' when the target holds a version the step did not write."""
    versions = [e for e in exact_versions(s3_client, bucket, source_key) if not e['Marker']][::-1]
    mapping, foreign = copied_versions(s3_client, bucket, target_key, {e['VersionId'] for e in versions})
    if foreign:
        return 'changed', mapping
    for entry in versions:
        if entry['VersionId'] in mapping:
            continue
        head = s3_client.head_object(Bucket=bucket, Key=source_key, VersionId=entry['VersionId'])
        extra = {'Metadata': _copy_metadata(head, database_id, asset_id, path, entry['VersionId']),
                 'MetadataDirective': 'REPLACE'}
        extra.update({name: head[name] for name in _COPIED_HEADERS if head.get(name)})
        s3_client.copy({'Bucket': bucket, 'Key': source_key, 'VersionId': entry['VersionId']}, bucket, target_key,
                       ExtraArgs=extra)
        written = s3_client.head_object(Bucket=bucket, Key=target_key)
        if (written.get('Metadata') or {}).get(CHANGE_FILE_VERSION_FROM_METADATA_KEY) != entry['VersionId']:
            return 'changed', mapping
        mapping[entry['VersionId']] = written['VersionId']
        tags = s3_client.get_object_tagging(Bucket=bucket, Key=source_key, VersionId=entry['VersionId']).get('TagSet')
        if tags:
            s3_client.put_object_tagging(Bucket=bucket, Key=target_key, VersionId=written['VersionId'],
                                         Tagging={'TagSet': tags})
    if archived and not latest_is_marker(exact_versions(s3_client, bucket, target_key)):
        s3_client.delete_object(Bucket=bucket, Key=target_key)
    source_now = exact_versions(s3_client, bucket, source_key)
    if source_now and not latest_is_marker(source_now):
        s3_client.delete_object(Bucket=bucket, Key=source_key)
    return 'moved', mapping


def move_auxiliary(s3_client, aux_bucket: str, database_id: str, file_key: str, dry_run: bool) -> int:
    """Move the object's own viewer and preview data from ``{db}/{K}/preview/`` to ``{db}/{K}/{name}/preview/``.
    A key whose rest has a ``/preview/`` segment belongs to a companion entry in ``{K}/preview/`` and stays."""
    source = f"{database_id}/{file_key.strip('/')}/preview/"
    target = f"{database_id}/{file_key.strip('/')}/{_file_name(file_key)}/preview/"
    moved = 0
    kwargs = {'Bucket': aux_bucket, 'Prefix': source}
    while True:
        page = s3_client.list_objects_v2(**kwargs)
        for entry in page.get('Contents', []):
            rest = entry['Key'][len(source):]
            if '/preview/' in rest:
                continue
            moved += 1
            if not dry_run:
                s3_client.copy({'Bucket': aux_bucket, 'Key': entry['Key']}, aux_bucket, target + rest)
                s3_client.delete_object(Bucket=aux_bucket, Key=entry['Key'])
        if not page.get('IsTruncated'):
            return moved
        kwargs['ContinuationToken'] = page['NextContinuationToken']


#######################
# The step
#######################

def _files(output_dir: str, dry_run: bool, started: datetime) -> Tuple[str, str]:
    mode = 'dry-run' if dry_run else 'execute'
    stamp = started.strftime('%Y%m%dT%H%M%SZ')
    return (os.path.join(output_dir, f"{STEP}-{mode}-report-{stamp}.jsonl"),
            os.path.join(output_dir, f"{STEP}-{mode}-checkpoint.json"))


def _read_checkpoint(path: str, asset_table: str) -> Optional[Dict]:
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        checkpoint = json.load(f)
    if checkpoint.get('assetTable') != asset_table:
        raise ValueError(f"The checkpoint {path} belongs to table '{checkpoint.get('assetTable')}', not "
                         f"'{asset_table}'. Remove it or point the step at its table.")
    return checkpoint


def _write_checkpoint(path: str, checkpoint: Dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(checkpoint, f)
    os.replace(tmp, path)


def read_context(dynamodb_client, cfg: Dict, resolutions: Dict) -> Tuple[Context, List[Candidate], int]:
    """Pass 1: the bucket registrations, every stored location per bucket name (live and archived rows) and
    the candidate rows, sorted. Returns ``(context, candidates, rows scanned)``."""
    registrations = {}
    for item in _paginate(dynamodb_client.scan, TableName=cfg['s3_asset_buckets_storage_table_name'],
                          ProjectionExpression='bucketId, bucketName, baseAssetsPrefix'):
        if _s(item, 'bucketId') and _s(item, 'bucketName'):
            registrations[_s(item, 'bucketId')] = (_s(item, 'bucketName'), _s(item, 'baseAssetsPrefix'))
    locations: Dict[str, List[Tuple[str, str, str]]] = {}
    candidate_keys: Dict[str, Set[str]] = {}
    candidates = []
    scanned = 0
    for row in _paginate(dynamodb_client.scan, TableName=cfg['asset_storage_table_name'], ConsistentRead=True,
                         ProjectionExpression='databaseId, assetId, bucketId, assetLocation, #done, #conv',
                         ExpressionAttributeNames={'#done': COPY_DONE_ATTRIBUTE, '#conv': CONVERSION_ATTRIBUTE}):
        scanned += 1
        location = _location_of(row)
        bucket = registrations.get(_s(row, 'bucketId'), ('', ''))[0]
        if bucket and location:
            locations.setdefault(bucket, []).append((location, _s(row, 'databaseId'), _s(row, 'assetId')))
        conversion = _plain_map(_m(row, CONVERSION_ATTRIBUTE))
        if (location and not location.endswith('/')) or conversion.get('state') == STATE_PENDING:
            candidates.append(Candidate(_s(row, 'databaseId'), _s(row, 'assetId'), _s(row, 'bucketId'), location,
                                        bool(_s(row, COPY_DONE_ATTRIBUTE)), conversion))
            if bucket and location and not location.endswith('/'):
                candidate_keys.setdefault(bucket, set()).add(location)
    candidates.sort(key=lambda c: (c.database_id, c.asset_id))
    return Context(registrations, locations, candidate_keys, resolutions), candidates, scanned


def _execute(dynamodb_client, s3_client, cfg: Dict, context: Context, candidate: Candidate, plan: Plan,
             counts: Dict[str, int], report: Callable[[Dict], None], base: Dict) -> None:
    """Carry out a conversion plan (or resume a pending one) on a real run."""
    asset_table = cfg['asset_storage_table_name']
    bucket = context.registrations.get(candidate.bucket_id, ('', ''))[0]
    errors_before = counts['errors'] + counts['record-clash']
    if plan.outcome == 'resume':
        file_key = candidate.conversion.get('from', '')
        mode = candidate.conversion.get('mode', '')
        target = candidate.conversion.get('to', '')
        started = candidate.conversion.get('startedAt', '')
        counts['resumed'] += 1
        if not (file_key and target and mode in CONVERSION_MODES and bucket):
            counts['changed'] += 1
            report(dict(base, action='left pending', detail='the conversion record is incomplete'))
            return
        keys = set() if mode == MODE_MOVE else keys_with_versions(s3_client, bucket, target)
        moves = path_map(file_key, target, iter(keys))
    else:
        file_key, mode, target = candidate.location, plan.outcome, plan.target
        started = datetime.now(timezone.utc).isoformat()
        moves, keys = plan.moves or {}, plan.keys or set()
        outcome = flip_row(dynamodb_client, asset_table, candidate, bucket, plan, started)
        if outcome == 'changed':
            counts['changed'] += 1
            report(dict(base, action='skipped: the row changed since it was read'))
            return
    database_id = _live_database_id(candidate.database_id)
    mover = RecordMover(dynamodb_client, cfg, candidate.database_id, candidate.asset_id, False, counts, report, base)
    if mode == MODE_MOVE:
        name = _file_name(file_key)
        archived = set(candidate.conversion.get('archived') or ()) if plan.outcome == 'resume' else set(plan.archived)
        for source in own_files(s3_client, bucket, file_key):
            destination = f"{target}{name}{source[len(file_key):]}"
            path = '/' + destination[len(target):]
            outcome, mapping = move_object(s3_client, bucket, source, destination, database_id, candidate.asset_id,
                                           path, source in archived)
            report(dict(base, part='objects', **{'from': source, 'to': destination}, action=outcome,
                        versions=len(mapping)))
            if outcome == 'changed':
                counts['changed'] += 1
                return
            counts['objects-copied'] += len(mapping)
            mover.rewrite_versions(path, mapping)
        aux_bucket = cfg.get('asset_auxiliary_bucket_name')
        if aux_bucket:
            moved = move_auxiliary(s3_client, aux_bucket, database_id, file_key, False)
            counts['auxiliary-moved'] += moved
            report(dict(base, part='auxiliary', action='moved', objects=moved))
    else:
        counts['paths-moved'] += len(moves)
        target_keys = keys

        def guard(old: str) -> bool:
            return target + old.lstrip('/') in target_keys

        mover.move_paths(moves, guard)
    if counts['errors'] + counts['record-clash'] != errors_before:
        counts['pending'] += 1
        report(dict(base, action='left pending', detail='a record could not be moved; resolve it and run again'))
        return
    row = dynamodb_client.get_item(TableName=asset_table, ConsistentRead=True, Key={
        'databaseId': {'S': candidate.database_id}, 'assetId': {'S': candidate.asset_id}}).get('Item') or {}
    write_history(dynamodb_client, cfg.get('asset_history_storage_table_name'), row, started, target)
    outcome = complete_row(dynamodb_client, asset_table, candidate)
    if outcome == 'changed':
        counts['changed'] += 1
        report(dict(base, action='skipped: the conversion record changed', mode=mode))
        return
    counts['converted'] += 1
    counts[f"mode:{mode}"] += 1
    report(dict(base, action='converted', mode=mode, to=target))


def run_file_to_folder(dynamodb_client, s3_client, cfg: Dict, dry_run: bool, *, resume: bool = False,
                       limit: Optional[int] = None, pace_seconds: float = PACE_SECONDS,
                       resolutions: Optional[Dict] = None, started: Optional[datetime] = None) -> Dict[str, int]:
    """Plan every candidate row and, on a real run, convert the ones whose plan allows it. One report line per
    finding; the position is saved after every ``PAGE_SIZE`` candidates and ``resume`` continues from it. A
    finished run removes its checkpoint."""
    started = started or datetime.now(timezone.utc)
    asset_table = cfg['asset_storage_table_name']
    os.makedirs(cfg['output_dir'], exist_ok=True)
    report_path, checkpoint_path = _files(cfg['output_dir'], dry_run, started)
    counts = {name: 0 for name in COUNTERS}
    checkpoint = _read_checkpoint(checkpoint_path, asset_table) if resume else None
    last = None
    if checkpoint:
        last = tuple(checkpoint.get('last') or ()) or None
        counts.update(checkpoint.get('counts') or {})
        report_path = checkpoint.get('report') or report_path
        logger.info(f"Resuming from {checkpoint_path} after {last[0]} / {last[1]}." if last else
                    f"Resuming from {checkpoint_path}.")
    elif resume:
        logger.info(f"No checkpoint at {checkpoint_path}; starting from the first row.")
    context, candidates, scanned = read_context(dynamodb_client, cfg, resolutions or {})
    counts['scanned'] = scanned
    processed = 0
    with open(report_path, 'a' if checkpoint else 'w', encoding='utf-8') as out:
        def write(record: Dict) -> None:
            out.write(json.dumps(record) + '\n')
            out.flush()

        for candidate in candidates:
            if checkpoint and last and (candidate.database_id, candidate.asset_id) <= last:
                continue
            if limit is not None and processed >= limit:
                logger.info(f"Limit of {limit} candidate row(s) reached; stopping. --resume continues.")
                _write_checkpoint(checkpoint_path, {'assetTable': asset_table, 'report': report_path,
                                                    'last': list(last) if last else None, 'counts': counts})
                break
            processed += 1
            counts['candidates'] += 1
            bucket = context.registrations.get(candidate.bucket_id, ('', ''))[0]
            plan = plan_candidate(s3_client, context, candidate, dry_run)
            base = {'database_id': candidate.database_id, 'asset_id': candidate.asset_id, 'bucket': bucket,
                    'from': candidate.location if plan.outcome != 'resume' else candidate.conversion.get('from', ''),
                    'to': plan.target}
            finding = dict(base, **{'class': plan.outcome}, detail=plan.detail)
            if plan.samples:
                finding.update(samples=list(plan.samples), sampleCount=plan.sample_count)
            if plan.resolution:
                finding['resolution'] = plan.resolution
            if plan.moves:
                finding['pathsMoved'] = len(plan.moves)
            if not plan.converts:
                counts[plan.outcome] += 1
                write(dict(finding, action='report only'))
                logger.info(f"  {'[DRY RUN] ' if dry_run else ''}{candidate.database_id} / {candidate.asset_id}: "
                            f"'{candidate.location}' {plan.outcome}{f' - {plan.detail}' if plan.detail else ''}")
            elif dry_run:
                counts['would-convert'] += 1
                if plan.outcome in CONVERSION_MODES:
                    counts[f"mode:{plan.outcome}"] += 1
                else:
                    counts['pending'] += 1
                write(dict(finding, action='would convert' if plan.outcome != 'resume' else 'would resume'))
                logger.info(f"  [DRY RUN] {candidate.database_id} / {candidate.asset_id}: '{candidate.location}' -> "
                            f"'{plan.target}' ({plan.outcome})")
                if plan.outcome in (MODE_IN_PLACE, MODE_APPEND_SLASH, MODE_INCLUDE_SIBLINGS) and plan.moves:
                    counts['paths-moved'] += len(plan.moves)
                    RecordMover(dynamodb_client, cfg, candidate.database_id, candidate.asset_id, True, counts,
                                write, base).move_paths(plan.moves, lambda old: False)
            else:
                write(dict(finding, action='converting' if plan.outcome != 'resume' else 'resuming'))
                try:
                    _execute(dynamodb_client, s3_client, cfg, context, candidate, plan, counts, write, base)
                except ClientError as e:
                    counts['errors'] += 1
                    counts['pending'] += 1
                    write(dict(base, action='failed', detail=str(e)))
                logger.info(f"  {candidate.database_id} / {candidate.asset_id}: '{base['from']}' -> "
                            f"'{plan.target}' ({plan.outcome})")
            if pace_seconds:
                time.sleep(pace_seconds)
            last = (candidate.database_id, candidate.asset_id)
            if processed % PAGE_SIZE == 0:
                _write_checkpoint(checkpoint_path, {'assetTable': asset_table, 'report': report_path,
                                                    'last': list(last), 'counts': counts})
        else:
            if os.path.exists(checkpoint_path):
                os.remove(checkpoint_path)
    counts['report'] = report_path
    return counts


def exit_status(counts: Dict[str, int], dry_run: bool) -> int:
    """0 when nothing failed (layout refusals only need an operator decision), else 1. A dry run fails on an
    unreadable row only."""
    if dry_run:
        return 1 if counts.get('unreadable') or counts.get('errors') else 0
    return 1 if any(counts.get(name) for name in FAILURE_OUTCOMES) else 0


def summary_lines(counts: Dict[str, int], dry_run: bool) -> List[str]:
    """The closing summary the main script prints after the step."""
    modes = ', '.join('%s %d' % (mode, counts.get('mode:' + mode, 0)) for mode in CONVERSION_MODES)
    done = counts.get('would-convert' if dry_run else 'converted', 0)
    lines = [f"assetLocationFileToFolder: {counts.get('candidates', 0)} row(s) without a folder location; "
             f"{done} {'would be converted' if dry_run else 'converted'} ({modes})."]
    refused = {name: counts.get(name, 0) for name in LAYOUT_REFUSALS if counts.get(name)}
    if refused:
        lines.append("  Refused (row unchanged; see the report and the resolutions option): " +
                     ', '.join(f"{name} {count}" for name, count in refused.items()))
    failed = {name: counts.get(name, 0) for name in FAILURE_OUTCOMES if counts.get(name)}
    if failed:
        lines.append("  Needs a re-run: " + ', '.join(f"{name} {count}" for name, count in failed.items()))
    if counts.get('paths-moved'):
        lines.append(f"  {counts['paths-moved']} companion path(s) changed: run vectorBackfill with "
                     "clear_vectors set to true and the reindex utility with --clear-indexes, so search and "
                     "vectors carry no entry at an old path.")
    lines.append(f"  Report: {counts.get('report', '')}")
    return lines
