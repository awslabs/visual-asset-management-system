#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import random
import time
from datetime import datetime, timedelta, timezone
import boto3
import json
from urllib.parse import quote
from botocore.config import Config
from botocore.exceptions import ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotocoreConnectionError
from common.validators import validate
from customLogging.logger import safeLogger
from common.constants import UNALLOWED_MIME_LIST, UNALLOWED_FILE_EXTENSION_LIST
from common.s3MetadataKeys import VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY, is_own_upload_object

logger = safeLogger()

# Presigned URLs for us-east-1 buckets carry the regional endpoint rather than the global one, so a
# URL signed for one Region is never redirected to another. Set before any S3 client is built.
os.environ.setdefault("AWS_S3_US_EAST_1_REGIONAL_ENDPOINT", "regional")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

# Concurrency the asset handlers use for parallel per-file S3 work. The connection pool of every
# Regional client covers the widest of those pools -- downloadAsset's whole-asset download runs
# DOWNLOAD_MAX_PARALLEL_S3_WORKERS threads (default 25) against the one client -- so parallel calls
# do not queue on botocore's default pool of 10 and threads do not serialize on connections.
MAX_PARALLEL_S3_WORKERS = 16
S3_CLIENT_MAX_POOL_CONNECTIONS = 50

# The one client configuration for asset-bucket traffic. SigV4 and path-style addressing make the
# signing Region part of every request, so a client is built per Region and a request for a bucket
# in another Region is signed for that Region rather than redirected or rejected.
S3_ASSET_CLIENT_CONFIG = Config(
    signature_version='s3v4',
    s3={'addressing_style': 'path', 'us_east_1_regional_endpoint': 'regional'},
    retries={'max_attempts': 5, 'mode': 'adaptive'},
    max_pool_connections=S3_CLIENT_MAX_POOL_CONNECTIONS
)

# The client for the final single-request copy of an upload or a workflow output (#1848): the asset
# configuration with ONE attempt. That copy is not safe to repeat: Amazon S3 can store the new version
# and still answer a retryable error (503 SlowDown under a burst on one key), and an SDK retry then
# stores a second, identical version. copy_s3_object_repeat_safe() retries it itself, after checking
# whether the failed attempt landed. Adaptive mode keeps the client-side rate limiter, which learns from
# the throttles. A recorded Rule 6 departure (DELIBERATE_DEPARTURES in
# tests/common/test_shared_client_retry_config.py); only that copy's CopyObject or PutObject uses it.
S3_ASSET_SINGLE_ATTEMPT_CLIENT_CONFIG = Config(
    signature_version='s3v4',
    s3={'addressing_style': 'path', 'us_east_1_regional_endpoint': 'regional'},
    retries={'total_max_attempts': 1, 'mode': 'adaptive'},
    max_pool_connections=S3_CLIENT_MAX_POOL_CONNECTIONS
)

# The presign-only configuration for an accelerated bucket (#1610): the asset configuration with
# the accelerate endpoint on. botocore drops path-style addressing for an accelerated request; the
# signing Region still comes from the client's Region. Never used for server-side calls.
S3_ASSET_ACCELERATED_CLIENT_CONFIG = Config(
    signature_version='s3v4',
    s3={'addressing_style': 'path', 'us_east_1_regional_endpoint': 'regional',
        'use_accelerate_endpoint': True},
    retries={'max_attempts': 5, 'mode': 'adaptive'},
    max_pool_connections=S3_CLIENT_MAX_POOL_CONNECTIONS
)

s3c = boto3.client('s3', config=retry_config)

# Per-call S3 list page sizes. These are pagination batch sizes (round-trip
# tuning), not result caps — the helpers below page to exhaustion.
S3_VERSIONS_PAGE_SIZE = 1000
S3_OBJECTS_PAGE_SIZE = 1000


def is_object_version_archived(bucket: str, key: str, version_id: str = None, client=None) -> bool:
    """Determine whether an S3 object (or a specific version) is archived.

    Uses a single head_object call rather than listing versions, which is O(1)
    regardless of how many versions the key has:
      - With version_id: heads that exact version. A delete marker returns
        405 (archived=True), whose error code botocore reports as '405' because a
        HeadObject error carries no body, or as 'MethodNotAllowed'; a live version
        returns 200 (archived=False); a missing version returns 404 (archived=False).
      - Without version_id: heads the current version. A live current version
        returns 200 (archived=False). A current version that is a delete marker
        returns 404, which is indistinguishable from a key that never existed, so
        that case falls back to a single-entry version listing to tell them apart.

    Args:
        bucket: The S3 bucket name
        key: The S3 object key
        version_id: Optional specific version ID to check
        client: Optional boto3 S3 client (defaults to the Region-routing asset client)

    Returns:
        True if the object/version is archived (delete marker), False otherwise.
        Best-effort: returns False on unexpected errors.
    """
    s3 = client or s3_asset_client
    try:
        if version_id:
            try:
                s3.head_object(Bucket=bucket, Key=key, VersionId=version_id)
                return False  # Version exists and is not a delete marker
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code')
                if error_code in ('405', 'MethodNotAllowed'):
                    return True  # This version is a delete marker
                if error_code in ('NoSuchKey', '404', 'NotFound'):
                    return False  # Version doesn't exist
                raise
        else:
            try:
                response = s3.head_object(Bucket=bucket, Key=key)
                return response.get('DeleteMarker', False)
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code')
                if error_code in ('NoSuchKey', '404', 'NotFound'):
                    # Current object missing; archived when the key's CURRENT version is a
                    # delete marker. Three properties of ListObjectVersions make one entry
                    # sufficient, and all three are load-bearing:
                    #   - Prefix is a prefix match, so the listing also carries longer sibling
                    #     keys (file.glb -> file.glb.previewFile.png); entries are matched back
                    #     to the key so a sibling's delete marker cannot answer for this key.
                    #   - Keys sort ascending and this key is a prefix of every longer sibling,
                    #     so its own entries lead the first page and cannot be paged past.
                    #   - A key's versions are returned newest first, so the single entry is
                    #     this key's current version. IsLatest is asserted rather than assumed:
                    #     an archived-then-restored key keeps its old delete marker in history,
                    #     and matching on the key alone would report it as archived.
                    versions_response = s3.list_object_versions(
                        Bucket=bucket, Prefix=key, MaxKeys=1)
                    return any(marker.get('Key') == key and marker.get('IsLatest')
                               for marker in versions_response.get('DeleteMarkers', []))
                raise
    except Exception as e:
        logger.warning(f"Error checking archive status for {key}: {e}")
        return False


def list_all_object_versions(bucket: str, prefix: str, client=None, max_keys: int = None,
                             key_marker: str = None, version_id_marker: str = None) -> dict:
    """List object versions and delete markers under a prefix.

    Pages through list_object_versions via KeyMarker/VersionIdMarker until the
    listing is exhausted (or until max_keys results are collected), so callers
    never miss versions beyond a single page. A start marker can be supplied to
    begin listing partway through a key's version history.

    Args:
        bucket: The S3 bucket name
        prefix: The S3 key prefix to list versions for
        client: Optional boto3 S3 client (defaults to the Region-routing asset client).
            Pass a retry-configured client to inherit the caller's retry behavior.
        max_keys: Optional cap on the total number of versions + delete markers to
            collect. When set, listing stops once this many entries are gathered.
        key_marker: Optional KeyMarker to start listing after (S3 is exclusive of
            the marker).
        version_id_marker: Optional VersionIdMarker to start listing after within
            key_marker's versions (S3 is exclusive of the marker).

    Returns:
        Dict with aggregated 'Versions' and 'DeleteMarkers' lists.
    """
    s3 = client or s3_asset_client
    versions = []
    delete_markers = []
    list_kwargs = {'Bucket': bucket, 'Prefix': prefix, 'MaxKeys': S3_VERSIONS_PAGE_SIZE}
    if key_marker is not None:
        list_kwargs['KeyMarker'] = key_marker
    if version_id_marker is not None:
        list_kwargs['VersionIdMarker'] = version_id_marker

    while True:
        response = s3.list_object_versions(**list_kwargs)
        versions.extend(response.get('Versions', []))
        delete_markers.extend(response.get('DeleteMarkers', []))

        if max_keys is not None and (len(versions) + len(delete_markers)) >= max_keys:
            break

        if response.get('IsTruncated'):
            list_kwargs['KeyMarker'] = response.get('NextKeyMarker')
            list_kwargs['VersionIdMarker'] = response.get('NextVersionIdMarker')
        else:
            break

    return {'Versions': versions, 'DeleteMarkers': delete_markers}


def list_key_versions_to_newest_version(bucket: str, key: str, client=None) -> dict:
    """One key's delete markers and versions, newest first, up to and including its newest version.

    A listing with ``Prefix=key`` returns the key's own entries first, newest first, then the entries
    of longer keys that share its characters. This pages (``S3_VERSIONS_PAGE_SIZE`` entries a call)
    until a page holds a version of the key, an entry of another key, or the listing ends, so a key
    whose newest version sits behind a page or more of newer delete markers still shows it: an
    archived file is never read as permanently deleted. Only entries of ``key`` itself are returned.

    Returns:
        Dict with the key's 'Versions' (at most up to its newest) and 'DeleteMarkers' lists.
    """
    s3 = client or s3_asset_client
    versions = []
    delete_markers = []
    list_kwargs = {'Bucket': bucket, 'Prefix': key, 'MaxKeys': S3_VERSIONS_PAGE_SIZE}
    while True:
        response = s3.list_object_versions(**list_kwargs)
        page_versions = response.get('Versions', []) or []
        page_markers = response.get('DeleteMarkers', []) or []
        own_versions = [v for v in page_versions if v.get('Key') == key]
        versions.extend(own_versions)
        delete_markers.extend(m for m in page_markers if m.get('Key') == key)
        other_key_seen = any(entry.get('Key') != key for entry in list(page_versions) + list(page_markers))
        # S3 always answers IsTruncated; only an explicit True continues the walk.
        if own_versions or other_key_seen or response.get('IsTruncated') is not True:
            return {'Versions': versions, 'DeleteMarkers': delete_markers}
        list_kwargs['KeyMarker'] = response.get('NextKeyMarker')
        list_kwargs['VersionIdMarker'] = response.get('NextVersionIdMarker')


def list_all_objects(bucket: str, prefix: str, client=None, max_objects: int = None) -> list:
    """List objects under a prefix, paging to exhaustion (or up to max_objects).

    Args:
        bucket: The S3 bucket name
        prefix: The S3 key prefix to list
        client: Optional boto3 S3 client (defaults to the Region-routing asset client).
        max_objects: Optional cap on the number of objects returned. When set,
            listing stops once this many objects are collected. Use for
            best-effort, non-critical reads (e.g. classification/sampling) where
            scanning an entire large asset would add unnecessary latency.

    Returns:
        List of S3 object dicts (as returned in 'Contents'), capped at max_objects
        when provided.
    """
    s3 = client or s3_asset_client
    objects = []
    pagination_config = {'PageSize': S3_OBJECTS_PAGE_SIZE}
    if max_objects is not None:
        pagination_config['MaxItems'] = max_objects
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(
        Bucket=bucket, Prefix=prefix,
        PaginationConfig=pagination_config
    ):
        objects.extend(page.get('Contents', []))
        if max_objects is not None and len(objects) >= max_objects:
            return objects[:max_objects]
    return objects


# How long a resolved bucket-name -> Region mapping is trusted before the asset buckets table is
# read again, so a bucket registered by a later deployment is picked up without a cold start.
BUCKET_REGION_CACHE_TTL_SECONDS = 600


def deployment_region() -> str:
    """The Region this Lambda runs in. Set by the Lambda runtime; never defaulted, because a default
    silently points at the wrong partition when the variable is missing."""
    return os.environ['AWS_REGION']


def lambdas_in_vpc() -> bool:
    """Whether the handler Lambdas run inside the VPC (VAMS_LAMBDAS_IN_VPC, set by the lambda
    builders). Inside the VPC a bucket in another Region is reached through an Amazon S3 interface
    endpoint, which does not serve CopyObject between Regions."""
    return os.environ.get('VAMS_LAMBDAS_IN_VPC', '').lower() == 'true'


_s3_clients_by_region = {}
_s3_resources_by_region = {}


def s3_client_for_region(region: str = None):
    """The S3 client for ``region`` (the deployment Region when None), built once per Region with
    S3_ASSET_CLIENT_CONFIG and no endpoint_url, so the SDK resolves the partition's endpoint."""
    region = region or deployment_region()
    client = _s3_clients_by_region.get(region)
    if client is None:
        client = boto3.client('s3', region_name=region, config=S3_ASSET_CLIENT_CONFIG)
        _s3_clients_by_region[region] = client
    return client


def s3_resource_for_region(region: str = None):
    """The S3 resource counterpart of s3_client_for_region, for the managed copy() paths."""
    region = region or deployment_region()
    resource = _s3_resources_by_region.get(region)
    if resource is None:
        resource = boto3.resource('s3', region_name=region, config=S3_ASSET_CLIENT_CONFIG)
        _s3_resources_by_region[region] = resource
    return resource


_s3_single_attempt_clients_by_region = {}


def s3_single_attempt_client_for_region(region: str = None):
    """The one-attempt S3 client for ``region`` (the deployment Region when None), built once per
    Region with S3_ASSET_SINGLE_ATTEMPT_CLIENT_CONFIG. Used only for the writes of
    copy_s3_object_repeat_safe(); every read keeps s3_client_for_region()'s SDK retries."""
    region = region or deployment_region()
    client = _s3_single_attempt_clients_by_region.get(region)
    if client is None:
        client = boto3.client('s3', region_name=region, config=S3_ASSET_SINGLE_ATTEMPT_CLIENT_CONFIG)
        _s3_single_attempt_clients_by_region[region] = client
    return client


# ---------------------------------------------------------------------------------------------
# S3 Transfer Acceleration (#1610). An asset bucket row carries ``isAccelerated`` (BOOL, a missing
# attribute is false). For a flagged bucket the presigned URLs a browser or the CLI uses are signed
# for ``<bucket>.s3-accelerate.amazonaws.com``; every other S3 call stays on the normal client.
# ---------------------------------------------------------------------------------------------

# How long a bucket's ``isAccelerated`` value is trusted. Unlike the Region (which never changes
# for a name) the flag is toggled by a deployment, so a warm Lambda must pick the change up.
BUCKET_ACCELERATE_CACHE_TTL_SECONDS = 600

# Presign operations that hand a URL to a browser or the CLI and so use the accelerate endpoint.
ACCELERATED_PRESIGN_METHODS = frozenset({'get_object', 'upload_part', 'put_object'})


class AccelerateNotSupportedError(ValueError):
    """Raised when an accelerated presign client is requested for a bucket name that Transfer
    Acceleration cannot serve (a name with a period, which the accelerate host's wildcard
    certificate does not cover)."""


_s3_accelerated_clients_by_region = {}


def s3_accelerated_client_for_region(region: str = None):
    """The S3 client that presigns for the accelerate endpoint, built once per Region from a copy of
    S3_ASSET_CLIENT_CONFIG plus ``use_accelerate_endpoint``. botocore drops path-style addressing
    for an accelerated request, so the signing Region still comes from the client's Region. Used
    only to presign; never for server-side calls."""
    region = region or deployment_region()
    client = _s3_accelerated_clients_by_region.get(region)
    if client is None:
        client = boto3.client('s3', region_name=region, config=S3_ASSET_ACCELERATED_CLIENT_CONFIG)
        _s3_accelerated_clients_by_region[region] = client
    return client


def accelerated_presign_client_for_bucket(bucket_name: str, region: str = None):
    """The accelerated presign client for ``bucket_name``, or AccelerateNotSupportedError when the
    name has a period (Transfer Acceleration does not support those buckets)."""
    if '.' in (bucket_name or ''):
        raise AccelerateNotSupportedError(
            f"Bucket {bucket_name} has a period in its name, which S3 Transfer Acceleration does "
            f"not support")
    return s3_accelerated_client_for_region(region or bucket_region_for_name(bucket_name))


def bucket_region(bucket_details) -> str:
    """The Region of an asset bucket from its details or table row: the stored ``bucketRegion``, or
    the deployment Region for a row written without one."""
    region = (bucket_details or {}).get('bucketRegion')
    return region if region else deployment_region()


def bucket_region_fields(bucket_row) -> dict:
    """The Region and owning account of a bucket row, in the shape every bucket-details helper
    returns: ``bucketRegion`` always set (deployment Region when the row carries none),
    ``bucketAccountId`` when the row carries one, ``isAccelerated`` only when the row says true.
    Also records the bucket name's Region and acceleration flag so a later call that holds only the
    name routes and presigns the same way without reading the table again."""
    fields = {'bucketRegion': bucket_region(bucket_row)}
    account_id = (bucket_row or {}).get('bucketAccountId')
    if account_id:
        fields['bucketAccountId'] = account_id
    bucket_name = (bucket_row or {}).get('bucketName')
    register_bucket_region(bucket_name, fields['bucketRegion'])
    if (bucket_row or {}).get('isAccelerated') is True:
        # Carried only when set, so details of a bucket without acceleration keep today's shape.
        fields['isAccelerated'] = True
    if bucket_row is not None and bucket_name and 'bucketName' in bucket_row:
        register_bucket_accelerated(bucket_name, bucket_row.get('isAccelerated') is True)
    return fields


def s3_client_for_bucket(bucket_details):
    """The S3 client for the Region of the bucket described by ``bucket_details``."""
    region = bucket_region(bucket_details)
    bucket_name = (bucket_details or {}).get('bucketName')
    if bucket_name:
        register_bucket_region(bucket_name, region)
    return s3_client_for_region(region)


def s3_resource_for_bucket(bucket_details):
    """The S3 resource for the Region of the bucket described by ``bucket_details``."""
    region = bucket_region(bucket_details)
    bucket_name = (bucket_details or {}).get('bucketName')
    if bucket_name:
        register_bucket_region(bucket_name, region)
    return s3_resource_for_region(region)


# Bucket name -> Region, for the call paths that hold only a bucket name. Filled from bucket
# details as handlers resolve them and from the asset buckets table (its bucketNameGSI) on a
# miss; a name the table does not carry (the auxiliary and artefacts buckets, an upload staging
# bucket) is in the deployment Region.
_bucket_regions_by_name = {}
_bucket_regions_misses = {}
_buckets_table = None


def _buckets_table_resource():
    global _buckets_table
    if _buckets_table is None:
        from common.resourceNames import get_table_name, ResourceKeys
        _buckets_table = boto3.resource('dynamodb', config=retry_config).Table(
            get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE))
    return _buckets_table


def register_bucket_region(bucket_name: str, region: str) -> None:
    """Record the Region of a bucket name so later name-only calls route to it without a lookup."""
    if bucket_name and region:
        _bucket_regions_by_name[bucket_name] = region


def _lookup_bucket_region(bucket_name: str) -> str:
    """Read the bucket's row through the asset buckets table's bucketNameGSI.

    Only an EMPTY result means "not an asset bucket" (the auxiliary and artefacts buckets, an upload
    staging bucket): that answer is the deployment Region and is remembered for the cache window. A
    read that FAILS -- AccessDeniedException because the Lambda's role lacks the table grant, a
    throttle, a missing table -- is a fault, not an answer: it is logged at error level with its
    code, resolves to the deployment Region for this call only, and is retried on the next call, so
    a permission defect stays visible instead of being cached as "not registered"."""
    now = time.monotonic()
    missed_at = _bucket_regions_misses.get(bucket_name)
    if missed_at is not None and now - missed_at < BUCKET_REGION_CACHE_TTL_SECONDS:
        return deployment_region()
    try:
        from boto3.dynamodb.conditions import Key
        response = _buckets_table_resource().query(
            IndexName='bucketNameGSI',
            KeyConditionExpression=Key('bucketName').eq(bucket_name),
            Limit=1)
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code', 'ClientError')
        logger.error(
            f"Could not read the Region of bucket {bucket_name} from the buckets table "
            f"({code}); signing this call for the deployment Region. A request for a bucket in "
            f"another Region fails until the read succeeds -- check the Lambda role's grant on the "
            f"buckets table.")
        return deployment_region()
    except Exception as e:
        logger.error(
            f"Could not read the Region of bucket {bucket_name} from the buckets table "
            f"({type(e).__name__}: {e}); signing this call for the deployment Region.")
        return deployment_region()
    items = response.get('Items') or []
    if items:
        region = items[0].get('bucketRegion') or deployment_region()
        _bucket_regions_by_name[bucket_name] = region
        register_bucket_accelerated(bucket_name, items[0].get('isAccelerated') is True)
        return region
    _bucket_regions_misses[bucket_name] = now
    return deployment_region()


def bucket_region_for_name(bucket_name: str) -> str:
    """The Region of a bucket known only by name: a Region registered for it, else the asset
    buckets table's row for it, else the deployment Region."""
    if not bucket_name:
        return deployment_region()
    region = _bucket_regions_by_name.get(bucket_name)
    if region:
        return region
    return _lookup_bucket_region(bucket_name)


_bucket_accelerated_by_name = {}

# How long a FAILED flag read is remembered as "unknown", so a missing grant costs one failing call
# per bucket per interval instead of two on every presign.
BUCKET_ACCELERATE_FAILURE_TTL_SECONDS = 30

# Problems logged once per bucket per cache window, so a fault stays visible without one log line
# per presigned URL.
_logged_at = {}


def _log_once(kind: str, bucket_name: str, message: str, level: str = 'error') -> None:
    key = (kind, bucket_name)
    now = time.monotonic()
    logged = _logged_at.get(key)
    if logged is not None and now - logged < BUCKET_ACCELERATE_CACHE_TTL_SECONDS:
        return
    _logged_at[key] = now
    getattr(logger, level)(message)


def register_bucket_accelerated(bucket_name: str, accelerated: bool) -> None:
    """Record a bucket's ``isAccelerated`` value, trusted for BUCKET_ACCELERATE_CACHE_TTL_SECONDS."""
    if bucket_name:
        _bucket_accelerated_by_name[bucket_name] = (bool(accelerated), time.monotonic())


def _remember_unknown_accelerated(bucket_name: str) -> bool:
    """A flag read failed. Keep the last known value (false when there is none) for only
    BUCKET_ACCELERATE_FAILURE_TTL_SECONDS, then read again."""
    previous = _bucket_accelerated_by_name.get(bucket_name)
    value = previous[0] if previous else False
    stored_at = time.monotonic() - BUCKET_ACCELERATE_CACHE_TTL_SECONDS + BUCKET_ACCELERATE_FAILURE_TTL_SECONDS
    _bucket_accelerated_by_name[bucket_name] = (value, stored_at)
    return value


def _query_bucket_accelerated(bucket_name: str):
    """The row's ``isAccelerated`` through bucketNameGSI: True/False for a registered bucket, None
    for a name the table does not carry."""
    from boto3.dynamodb.conditions import Key
    response = _buckets_table_resource().query(
        IndexName='bucketNameGSI',
        KeyConditionExpression=Key('bucketName').eq(bucket_name),
        Limit=1)
    items = response.get('Items') or []
    if not items:
        return None
    return items[0].get('isAccelerated') is True


def _is_auxiliary_bucket(bucket_name: str) -> bool:
    try:
        from common.resourceNames import get_bucket_name, ResourceKeys
        return bucket_name == get_bucket_name(ResourceKeys.ASSET_AUXILIARY_BUCKET)
    except Exception as e:
        _log_once('aux-name', bucket_name,
                  f"Could not resolve the auxiliary bucket name to check {bucket_name} "
                  f"({type(e).__name__}: {e}); treating it as not the auxiliary bucket.")
        return False


def _default_bucket_accelerated() -> bool:
    """Whether the effective default asset bucket (the row flagged ``isDefault``, which may be an
    external bucket) is accelerated. The auxiliary bucket has no row and follows this value."""
    from common.workflows.defaultBucket import resolve_default_bucket, DefaultBucketNotFoundError
    try:
        default_name = resolve_default_bucket(_buckets_table_resource())['bucketName']
    except DefaultBucketNotFoundError:
        return False
    return bool(_query_bucket_accelerated(default_name))


def bucket_is_accelerated(bucket_name: str) -> bool:
    """Whether presigned URLs for ``bucket_name`` use the S3 Transfer Acceleration endpoint.

    Read from the bucket's row (``isAccelerated``, missing is false) and cached for
    BUCKET_ACCELERATE_CACHE_TTL_SECONDS. The auxiliary bucket has no row and follows the effective
    default asset bucket; the artefacts bucket and any other name the table does not carry is not
    accelerated. A read that FAILS is a fault, not an answer: it is logged at error level (once per
    bucket per cache window) with its code, the last known value is kept (false when there is none)
    so the download or upload still works, and the read is tried again after
    BUCKET_ACCELERATE_FAILURE_TTL_SECONDS."""
    if not bucket_name:
        return False
    cached = _bucket_accelerated_by_name.get(bucket_name)
    if cached is not None and time.monotonic() - cached[1] < BUCKET_ACCELERATE_CACHE_TTL_SECONDS:
        return cached[0]
    try:
        accelerated = _query_bucket_accelerated(bucket_name)
        if accelerated is None:
            accelerated = _default_bucket_accelerated() if _is_auxiliary_bucket(bucket_name) else False
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code', 'ClientError')
        _log_once('flag-read', bucket_name,
                  f"Could not read the acceleration flag of bucket {bucket_name} from the buckets "
                  f"table ({code}); keeping the last known value. Check the Lambda role's grant on "
                  f"the buckets table.")
        return _remember_unknown_accelerated(bucket_name)
    except Exception as e:
        _log_once('flag-read', bucket_name,
                  f"Could not read the acceleration flag of bucket {bucket_name} from the buckets "
                  f"table ({type(e).__name__}: {e}); keeping the last known value.")
        return _remember_unknown_accelerated(bucket_name)
    register_bucket_accelerated(bucket_name, accelerated)
    return accelerated


def s3_client_for_bucket_name(bucket_name: str):
    """The S3 client for the Region of a bucket known only by name."""
    return s3_client_for_region(bucket_region_for_name(bucket_name))


def s3_resource_for_bucket_name(bucket_name: str):
    """The S3 resource for the Region of a bucket known only by name."""
    return s3_resource_for_region(bucket_region_for_name(bucket_name))


# Copy ExtraArgs that are also valid PutObject/upload arguments (everything but the directives).
_COPY_ARGS_NOT_FOR_PUT = frozenset({'MetadataDirective', 'TaggingDirective', 'CopySourceIfMatch',
                                    'CopySourceIfModifiedSince', 'CopySourceIfNoneMatch',
                                    'CopySourceIfUnmodifiedSince', 'CopySourceSSECustomerAlgorithm',
                                    'CopySourceSSECustomerKey', 'CopySourceSSECustomerKeyMD5'})
_COPIED_OBJECT_HEADERS = ('CacheControl', 'ContentDisposition', 'ContentEncoding',
                          'ContentLanguage', 'ContentType', 'Expires')


def copy_s3_object(source_bucket: str, source_key: str, dest_bucket: str, dest_key: str,
                   extra_args: dict = None, source_version_id: str = None) -> None:
    """Copy an object between asset buckets, whichever Regions they are in.

    The copy is issued by the destination Region's client (Amazon S3 pulls the source across
    Regions), with the source Region's client supplied for the size read a managed copy makes.
    Inside the VPC a bucket in another Region is reached through an Amazon S3 interface endpoint,
    and those do not serve CopyObject or UploadPartCopy between Regions, so that one case streams
    the object instead: GET from the source Region, multipart upload to the destination Region,
    carrying the same metadata, content headers and ACL the copy would have written.

    ``extra_args`` takes the managed-copy ExtraArgs shape (``MetadataDirective``/``Metadata``,
    content headers, ``ACL``).
    """
    source_region = bucket_region_for_name(source_bucket)
    dest_region = bucket_region_for_name(dest_bucket)
    source_client = s3_client_for_region(source_region)
    dest_client = s3_client_for_region(dest_region)
    copy_source = {'Bucket': source_bucket, 'Key': source_key}
    if source_version_id:
        copy_source['VersionId'] = source_version_id
    extra_args = dict(extra_args or {})

    if source_region == dest_region or not lambdas_in_vpc():
        dest_client.copy(CopySource=copy_source, Bucket=dest_bucket, Key=dest_key,
                         ExtraArgs=extra_args or None, SourceClient=source_client)
        return

    logger.info(f"Streaming cross-Region copy {source_region} -> {dest_region} for {dest_key}")
    put_args = {key: value for key, value in extra_args.items() if key not in _COPY_ARGS_NOT_FOR_PUT}
    source_object = source_client.get_object(**copy_source)
    if extra_args.get('MetadataDirective') != 'REPLACE':
        # A plain copy keeps the source object's user metadata and content headers.
        put_args.setdefault('Metadata', source_object.get('Metadata', {}))
        for header in _COPIED_OBJECT_HEADERS:
            value = source_object.get(header)
            if value and header not in put_args:
                put_args[header] = value
    dest_client.upload_fileobj(source_object['Body'], dest_bucket, dest_key,
                               ExtraArgs=put_args or None)


# ---------------------------------------------------------------------------------------------
# The final copy of an upload or workflow output, safe to repeat (#1848).
#
# Below SINGLE_REQUEST_COPY_MAX_BYTES a managed copy is ONE CopyObject (and the in-VPC cross-Region
# stream ONE PutObject). Under a burst of writes to one key Amazon S3 can store that request's new
# version and still answer 503 SlowDown, and an SDK retry then stores a second, identical version.
# copy_s3_object_repeat_safe() therefore sends each attempt on the one-attempt client and, after a
# retryable failure, waits the backoff botocore would have waited and checks whether the failed
# attempt landed, by the per-call token the caller stamped in the object's metadata
# (VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY; equality, never presence or size). A landed attempt is
# success; otherwise it copies again, within the attempts the asset client's retries allow. At and
# above the threshold s3transfer copies by multipart, and a multipart upload completes to at most one
# object, so that path is copy_s3_object() unchanged.
# ---------------------------------------------------------------------------------------------

# s3transfer's multipart threshold (boto3 TransferConfig().multipart_threshold): a copy or upload
# smaller than this is one request.
SINGLE_REQUEST_COPY_MAX_BYTES = 8 * 1024 * 1024
# The attempts S3_ASSET_CLIENT_CONFIG's retries allow (max_attempts 5 resolves to 6 in total), and
# botocore's backoff between them (botocore 1.43.126 retries/standard.py ExponentialBackoff,
# delay_amount lines 425-429: rand(0, 1) * min(2 ** i, 20), i the 0-based attempt).
SINGLE_REQUEST_COPY_MAX_ATTEMPTS = 6
SINGLE_REQUEST_COPY_BACKOFF_BASE = 2
SINGLE_REQUEST_COPY_MAX_BACKOFF_SECONDS = 20
# The landed check lists the versions stamped within this margin of the failed attempt's own interval
# (for the clock difference between Lambda and Amazon S3), and reads at most this many of them.
LANDED_CHECK_CLOCK_MARGIN_SECONDS = 2
LANDED_CHECK_MAX_VERSION_HEADS = 50

# What botocore's standard retry checker retries (botocore 1.43.126 retries/standard.py:
# TransientRetryableChecker lines 485-526, ThrottledRetryableChecker lines 529-557), so the loop retries
# exactly what the asset client's SDK retries did. S3 models no retryable error shapes, and a 200 whose
# body is an error arrives as status 500 (botocore handlers.py _handle_200_error, lines 1293-1307).
_RETRYABLE_TRANSIENT_ERROR_CODES = frozenset({
    'RequestTimeout', 'RequestTimeoutException', 'PriorRequestNotComplete',
})
_RETRYABLE_TRANSIENT_STATUS_CODES = frozenset({500, 502, 503, 504})
_RETRYABLE_THROTTLING_ERROR_CODES = frozenset({
    'Throttling', 'ThrottlingException', 'ThrottledException', 'RequestThrottledException',
    'TooManyRequestsException', 'ProvisionedThroughputExceededException',
    'TransactionInProgressException', 'RequestLimitExceeded', 'BandwidthLimitExceeded',
    'LimitExceededException', 'RequestThrottled', 'SlowDown', 'PriorRequestNotComplete',
    'EC2ThrottledException',
})
_NOT_FOUND_CODES = frozenset({'404', 'NoSuchKey', 'NotFound', 'NoSuchVersion'})
# A HEAD by version id of a delete marker answers 405.
_DELETE_MARKER_CODES = frozenset({'405', 'MethodNotAllowed'})


def _error_code(error) -> str:
    if isinstance(error, ClientError):
        return (error.response.get('Error') or {}).get('Code') or ''
    return ''


def _error_label(error) -> str:
    return _error_code(error) or type(error).__name__


def is_retryable_write_error(error) -> bool:
    """Whether botocore's standard retry checker would have retried ``error``: a throttling code, a
    transient code or status (500/502/503/504), or a connection error or read timeout. A connection
    error or read timeout may have landed, so the caller checks before it copies again."""
    if isinstance(error, (BotocoreConnectionError, HTTPClientError)):
        return True
    if not isinstance(error, ClientError):
        return False
    code = _error_code(error)
    status = (error.response.get('ResponseMetadata') or {}).get('HTTPStatusCode')
    return (code in _RETRYABLE_THROTTLING_ERROR_CODES or code in _RETRYABLE_TRANSIENT_ERROR_CODES
            or status in _RETRYABLE_TRANSIENT_STATUS_CODES)


def single_request_copy_backoff(attempt: int, rng) -> float:
    """botocore's delay after the 0-based ``attempt`` fails: rand(0, 1) * min(2 ** attempt, 20)."""
    return rng() * min(SINGLE_REQUEST_COPY_BACKOFF_BASE ** attempt, SINGLE_REQUEST_COPY_MAX_BACKOFF_SECONDS)


def _version_named_by_error(error):
    """The version id an error response names (``x-amz-version-id``), or None. botocore keeps every
    response's headers, error responses included, lowercased in ResponseMetadata.HTTPHeaders."""
    if not isinstance(error, ClientError):
        return None
    headers = (error.response.get('ResponseMetadata') or {}).get('HTTPHeaders') or {}
    version_id = {str(name).lower(): value for name, value in headers.items()}.get('x-amz-version-id')
    return version_id if version_id and version_id != 'null' else None


class _EarlierChecks:
    """What the earlier landed checks of one copy call saw. Every one ran after its own attempt failed
    and before the next attempt started, so none of these versions can be a later attempt's write.
    ``read`` were HEADed and do not carry the token (a version's metadata never changes), so they are
    never read again; ``listed`` were only listed, and are read last."""

    def __init__(self):
        self.read = set()
        self.listed = set()


def _seconds_outside(last_modified, attempt_started: datetime, attempt_ended: datetime) -> float:
    """How far, in seconds, a version's LastModified lies outside the attempt's own interval, 0 when it
    falls inside. Amazon S3 reports LastModified to the second, so a version stamped ``t`` was written
    in [t, t + 1 s)."""
    stamped_from = last_modified
    stamped_to = last_modified + timedelta(seconds=1)
    if stamped_to <= attempt_started:
        return (attempt_started - stamped_to).total_seconds()
    if stamped_from > attempt_ended:
        return (stamped_from - attempt_ended).total_seconds()
    return 0.0


def _landed_version(client, bucket: str, key: str, write_token: str, error, attempt_started: datetime,
                    attempt_ended: datetime, earlier: _EarlierChecks = None):
    """(landed, version_id) after a failed attempt of a copy that stamped ``write_token``.

    1. The version the error names: carries the token -> landed; does not exist -> that attempt did
       not land (an attempt writes at most the version its response names); refused or other -> 2.
    2. The current object carries the token -> landed (an unversioned or suspended bucket has no
       other object, and this is the quiet case).
    3. Versioned bucket only: one listing page of the key's versions, narrowed to the ones that can be
       this attempt's write: LastModified within LANDED_CHECK_CLOCK_MARGIN_SECONDS of the attempt's own
       interval (``attempt_started`` to ``attempt_ended``, this attempt's, not the call's), and not
       already read by this check or an earlier one. They are read in the order most likely to hold
       the attempt's write first, up to LANDED_CHECK_MAX_VERSION_HEADS of them: versions no earlier
       check saw before versions an earlier check listed (those predate this attempt); then versions
       stamped inside the attempt's interval before those in the margin, nearest first; then newest
       first (a key's versions are listed newest first, in write order). Newest first because the
       writes stamped inside the interval that follow this attempt's are the ones that land before
       its response arrives, while those before it include every write since the start of the second
       the attempt began in. The first with the token -> landed.
    Anything else, a refused or failed read included, is not confirmed: the caller copies again, so a
    token that cannot be found ends with the attempt budget rather than a loop. ``earlier`` collects
    what this check saw, for the next attempt's check.
    """
    earlier = earlier if earlier is not None else _EarlierChecks()
    checked = set(earlier.read)
    named = _version_named_by_error(error)
    if named:
        checked.add(named)
        try:
            head = client.head_object(Bucket=bucket, Key=key, VersionId=named)
            if is_own_upload_object(head, write_token):
                return True, head.get('VersionId') or named
            earlier.read.add(named)
        except ClientError as e:
            if _error_code(e) in _NOT_FOUND_CODES:
                return False, None
            logger.warning(f"Could not read version {named} of {key} after a failed copy "
                           f"({_error_label(e)}), so the current object is checked")
        except Exception as e:
            logger.warning(f"Could not read version {named} of {key} after a failed copy "
                           f"({_error_label(e)}), so the current object is checked")

    try:
        current = client.head_object(Bucket=bucket, Key=key)
    except Exception as e:
        if _error_code(e) not in _NOT_FOUND_CODES:
            logger.warning(f"Could not read {key} after a failed copy ({_error_label(e)})")
        return False, None
    if is_own_upload_object(current, write_token):
        return True, current.get('VersionId')
    current_version = current.get('VersionId')
    if not current_version or current_version == 'null':
        return False, None
    checked.add(current_version)
    earlier.read.add(current_version)

    try:
        page = client.list_object_versions(Bucket=bucket, Prefix=key, MaxKeys=S3_VERSIONS_PAGE_SIZE)
    except Exception as e:
        logger.warning(f"Could not list the versions of {key} after a failed copy ({_error_label(e)})")
        return False, None
    margin = LANDED_CHECK_CLOCK_MARGIN_SECONDS
    ranked = []
    # The prefix also lists longer keys; only this key's entries count. The page lists them newest
    # first, so ``position`` is the write order, newest first.
    own_entries = [entry for entry in (page.get('Versions') or []) if entry.get('Key') == key]
    for position, entry in enumerate(own_entries):
        version_id = entry.get('VersionId')
        if not version_id or version_id in checked:
            continue
        last_modified = entry.get('LastModified')
        if isinstance(last_modified, datetime):
            outside = _seconds_outside(last_modified, attempt_started, attempt_ended)
            if outside > margin:
                continue
        else:
            outside = margin
        ranked.append(((version_id in earlier.listed, outside, position), version_id))
    earlier.listed.update(entry.get('VersionId') for entry in own_entries if entry.get('VersionId'))
    ranked.sort(key=lambda item: item[0])
    for _, version_id in ranked[:LANDED_CHECK_MAX_VERSION_HEADS]:
        try:
            head = client.head_object(Bucket=bucket, Key=key, VersionId=version_id)
        except ClientError as e:
            if _error_code(e) in _NOT_FOUND_CODES or _error_code(e) in _DELETE_MARKER_CODES:
                continue
            logger.warning(f"Could not read a version of {key} after a failed copy ({_error_label(e)})")
            return False, None
        except Exception as e:
            logger.warning(f"Could not read a version of {key} after a failed copy ({_error_label(e)})")
            return False, None
        if is_own_upload_object(head, write_token):
            return True, version_id
        earlier.read.add(version_id)
    if len(ranked) > LANDED_CHECK_MAX_VERSION_HEADS:
        logger.warning(f"Checked {LANDED_CHECK_MAX_VERSION_HEADS} of the {len(ranked)} versions of {key} "
                       f"written around the failed attempt, none of them its write")
    return False, None


def copy_s3_object_repeat_safe(source_bucket: str, source_key: str, dest_bucket: str, dest_key: str,
                               extra_args: dict, write_token: str, source_size, source_version_id: str = None,
                               sleep=time.sleep, rng=random.random):
    """Copy an upload's or a workflow output's staged object to its final key without ever writing it
    twice, and return the version id of the object that landed (None on an unversioned bucket and on
    the delegated multipart path).

    ``extra_args`` takes the managed-copy ExtraArgs shape and must replace the metadata
    (``MetadataDirective: REPLACE``) with a ``Metadata`` that carries ``write_token`` under
    VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY: a fresh value per call, so the landed check can tell this
    call's object from every other one at the key. ``source_size`` is the staged object's
    ContentLength. Below SINGLE_REQUEST_COPY_MAX_BYTES the copy is one request on the destination
    Region's one-attempt client: a server-side CopyObject, or, when the Lambdas run in the VPC and the
    Regions differ, one GET of the source and a PutObject with the same arguments (copy_s3_object()'s
    streaming branch). A retryable failure (is_retryable_write_error) is followed by botocore's backoff
    and the landed check; a landed attempt is success, logged once, and is not copied again. A copy
    that did not land is sent again, up to SINGLE_REQUEST_COPY_MAX_ATTEMPTS attempts, and the last
    error is raised. Any other error is raised at once. A size at or over the threshold, or an unknown
    size, is copy_s3_object() unchanged. ``sleep`` and ``rng`` are injectable for tests.

    Raises:
        ValueError: ``extra_args`` does not replace the metadata with one carrying ``write_token``.
    """
    extra_args = dict(extra_args or {})
    metadata = extra_args.get('Metadata')
    if (not write_token or extra_args.get('MetadataDirective') != 'REPLACE' or not isinstance(metadata, dict)
            or metadata.get(VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY) != write_token):
        raise ValueError("A repeat-safe copy must replace the metadata with one that carries its write token")
    if not isinstance(source_size, int) or source_size >= SINGLE_REQUEST_COPY_MAX_BYTES:
        copy_s3_object(source_bucket, source_key, dest_bucket, dest_key, extra_args=extra_args,
                       source_version_id=source_version_id)
        return None

    source_region = bucket_region_for_name(source_bucket)
    dest_region = bucket_region_for_name(dest_bucket)
    read_client = s3_client_for_region(dest_region)
    write_client = s3_single_attempt_client_for_region(dest_region)
    copy_source = {'Bucket': source_bucket, 'Key': source_key}
    if source_version_id:
        copy_source['VersionId'] = source_version_id

    if source_region == dest_region or not lambdas_in_vpc():
        def write():
            return write_client.copy_object(CopySource=copy_source, Bucket=dest_bucket, Key=dest_key,
                                            **extra_args)
    else:
        logger.info(f"Streaming cross-Region copy {source_region} -> {dest_region} for {dest_key}")
        # Read once: below the threshold the body fits in memory, and every attempt sends the same bytes.
        body = s3_client_for_region(source_region).get_object(**copy_source)['Body'].read()
        put_args = {key: value for key, value in extra_args.items() if key not in _COPY_ARGS_NOT_FOR_PUT}

        def write():
            return write_client.put_object(Bucket=dest_bucket, Key=dest_key, Body=body, **put_args)

    earlier = _EarlierChecks()
    for attempt in range(SINGLE_REQUEST_COPY_MAX_ATTEMPTS):
        attempt_started = datetime.now(timezone.utc)
        try:
            response = write()
        except Exception as error:
            attempt_ended = datetime.now(timezone.utc)
            if not is_retryable_write_error(error):
                raise
            last_attempt = attempt == SINGLE_REQUEST_COPY_MAX_ATTEMPTS - 1
            if not last_attempt:
                # botocore waits before a retry and not after the last attempt; the wait also gives a
                # stored version time to show before it is looked for.
                sleep(single_request_copy_backoff(attempt, rng))
            landed, version_id = _landed_version(read_client, dest_bucket, dest_key, write_token, error,
                                                 attempt_started, attempt_ended, earlier)
            if landed:
                logger.info(f"Copy to {dest_key} answered {_error_label(error)} on attempt {attempt + 1}, "
                            f"but it landed as version {version_id}, so it is not copied again")
                return version_id
            if last_attempt:
                logger.warning(f"Copy to {dest_key} answered {_error_label(error)} on attempt "
                               f"{attempt + 1} of {SINGLE_REQUEST_COPY_MAX_ATTEMPTS} and did not land")
                raise
            logger.warning(f"Copy to {dest_key} answered {_error_label(error)} on attempt {attempt + 1} "
                           f"and was not confirmed as landed, so it is copied again")
            continue
        return response.get('VersionId')


class _RegionRoutingPaginator:
    """A paginator whose paginate() runs on the client for the Region of its Bucket."""

    def __init__(self, router, operation_name):
        self._router = router
        self._operation_name = operation_name

    def paginate(self, **kwargs):
        return self._router._client_for(kwargs.get('Bucket')).get_paginator(
            self._operation_name).paginate(**kwargs)


def _accelerated_presign(ordinary_client, bucket: str, args, kwargs):
    """The accelerated presigned URL for ``bucket``, or None when it cannot be served (the caller
    then signs with the ordinary client). Only the commercial partition (``aws``) supports Transfer
    Acceleration, so the partition is read from the ordinary client's Region and a flag set on a row
    in any other partition is ignored. Any failure to build the accelerated client or to sign with it
    (a name botocore cannot virtual-host, a FIPS endpoint setting, an endpoint resolution error) is
    logged once per bucket per cache window with the error class."""
    partition = ordinary_client.meta.partition
    if partition != 'aws':
        _log_once('partition', bucket,
                  f"Bucket {bucket} is flagged for S3 Transfer Acceleration but runs in partition "
                  f"{partition}, which does not support it; signing without acceleration.",
                  level='warning')
        return None
    try:
        client = accelerated_presign_client_for_bucket(bucket, ordinary_client.meta.region_name)
        return client.generate_presigned_url(*args, **kwargs)
    except Exception as e:
        _log_once('accelerate-presign', bucket,
                  f"Could not sign an accelerated URL for bucket {bucket} "
                  f"({type(e).__name__}: {e}); signing without acceleration.")
        return None


class RegionRoutingS3Client:
    """An S3 client whose every operation runs on the client for the Region of the bucket it
    names, so a handler that works across asset buckets in several Regions keeps one module-level
    client. The bucket is read from ``Bucket`` (or ``Params['Bucket']`` for a presigned URL); a
    call that names no bucket runs in the deployment Region. ``copy`` and ``copy_object`` go
    through copy_s3_object so an in-VPC cross-Region copy streams instead of failing.
    ``exceptions`` and ``meta`` are the deployment-Region client's: botocore shares one set of
    exception classes across the clients of a service."""

    def __init__(self):
        # The deployment-Region client is bound at construction rather than on first use, so a
        # handler module that builds its router at import binds it there, the way a plain
        # module-level client would.
        self._default_client = boto3.client('s3', region_name=deployment_region(),
                                            config=S3_ASSET_CLIENT_CONFIG)

    def _client_for(self, bucket):
        if not bucket:
            return self._default_client
        region = bucket_region_for_name(bucket)
        if region == deployment_region():
            return self._default_client
        return s3_client_for_region(region)


    @staticmethod
    def _bucket_from_call(args, kwargs):
        if 'Bucket' in kwargs:
            return kwargs['Bucket']
        params = kwargs.get('Params')
        if isinstance(params, dict) and 'Bucket' in params:
            return params['Bucket']
        return None

    def __getattr__(self, name):
        if name in ('exceptions', 'meta', '_endpoint', '_client_config'):
            return getattr(self._default_client, name)
        if name == 'get_paginator':
            return lambda operation_name: _RegionRoutingPaginator(self, operation_name)
        if name == 'copy':
            return self._copy
        if name == 'copy_object':
            return self._copy_object
        if name == 'generate_presigned_url':
            return self._presign

        def call(*args, **kwargs):
            return getattr(self._client_for(self._bucket_from_call(args, kwargs)), name)(*args, **kwargs)
        return call

    def _presign(self, *args, **kwargs):
        """Presign on the client for the bucket's Region; for a bucket flagged ``isAccelerated`` in
        the commercial partition, the URL a browser or the CLI uses is signed for the accelerate
        endpoint instead. An accelerate problem never fails the request: it is logged and the
        ordinary client's URL is returned."""
        bucket = self._bucket_from_call(args, kwargs)
        method = kwargs.get('ClientMethod', args[0] if args else None)
        ordinary = self._client_for(bucket)
        if bucket and method in ACCELERATED_PRESIGN_METHODS and bucket_is_accelerated(bucket):
            url = _accelerated_presign(ordinary, bucket, args, kwargs)
            if url is not None:
                return url
        return ordinary.generate_presigned_url(*args, **kwargs)

    @staticmethod
    def _copy(CopySource=None, Bucket=None, Key=None, ExtraArgs=None, **_ignored):
        copy_s3_object(CopySource['Bucket'], CopySource['Key'], Bucket, Key,
                       extra_args=ExtraArgs, source_version_id=CopySource.get('VersionId'))

    @staticmethod
    def _copy_object(CopySource=None, Bucket=None, Key=None, **kwargs):
        if isinstance(CopySource, str):
            source_bucket, _, source_key = CopySource.partition('/')
            source_version_id = None
            if '?versionId=' in source_key:
                source_key, _, source_version_id = source_key.partition('?versionId=')
        else:
            source_bucket, source_key = CopySource['Bucket'], CopySource['Key']
            source_version_id = CopySource.get('VersionId')
        copy_s3_object(source_bucket, source_key, Bucket, Key, extra_args=kwargs,
                       source_version_id=source_version_id)


class _RegionRoutingObject:
    """An S3 resource Object whose copy() goes through copy_s3_object; every other attribute is the
    real Object's, built on the resource for the bucket's Region."""

    def __init__(self, s3_object):
        self._object = s3_object

    def copy(self, CopySource, ExtraArgs=None, **_ignored):
        copy_s3_object(CopySource['Bucket'], CopySource['Key'], self._object.bucket_name,
                       self._object.key, extra_args=ExtraArgs,
                       source_version_id=CopySource.get('VersionId'))

    def __getattr__(self, name):
        return getattr(self._object, name)


class _RegionRoutingResourceMeta:
    def __init__(self, client):
        self.client = client


class RegionRoutingS3Resource:
    """The resource counterpart of RegionRoutingS3Client: Object()/Bucket() are built on the
    resource for the bucket's Region, and meta.client is the routing client."""

    def __init__(self):
        self._default_resource = boto3.resource('s3', region_name=deployment_region(),
                                                config=S3_ASSET_CLIENT_CONFIG)
        self.meta = _RegionRoutingResourceMeta(RegionRoutingS3Client())

    def _resource_for(self, bucket_name):
        region = bucket_region_for_name(bucket_name)
        if region == deployment_region():
            return self._default_resource
        return s3_resource_for_region(region)

    def Object(self, bucket_name, key):
        return _RegionRoutingObject(self._resource_for(bucket_name).Object(bucket_name, key))

    def Bucket(self, name):
        return self._resource_for(name).Bucket(name)

    def __getattr__(self, name):
        return getattr(self._default_resource, name)


def region_routing_s3_client() -> RegionRoutingS3Client:
    """A new Region-routing S3 client for a handler module's module-level client."""
    return RegionRoutingS3Client()


def region_routing_s3_resource() -> RegionRoutingS3Resource:
    """A new Region-routing S3 resource for a handler module's module-level resource."""
    return RegionRoutingS3Resource()


# The instances the shared helpers in this module fall back to when a caller passes no client.
s3_asset_client = region_routing_s3_client()
s3_asset_resource = region_routing_s3_resource()


def validateUnallowedFileExtensionAndContentType(keyPath: str, contentType: str):
    #Both comparisons are case-insensitive: file systems preserve ".EXE" and an uploader picks the
    #case freely, and a media type is case-insensitive by definition (RFC 2045). The blocklists in
    #common/constants.py are written lower-case, so the inputs are lower-cased to match them.

    #Check if the content type is in the list of unallowed MIME types. Only the media type counts:
    #S3 can report "application/x-msdownload; charset=binary", and a parameter must not let the
    #type through.
    mediaType = (contentType or "").split(";", 1)[0].strip().lower()
    if mediaType in UNALLOWED_MIME_LIST:
        logger.error(f"Unallowed file content type detected in asset: {keyPath}")
        return False
    
    #Check if the file extension of the keyPath is in the list of unallowed file extensions. The
    #extension is everything from the last dot of the key's final non-empty segment, read after
    #trailing dots and whitespace are removed: "payload.exe.", "payload.exe/", "..exe" -> ".exe".
    #The segment is split on ':' and EVERY piece is checked, since a Windows client materializes
    #whichever piece survives: on NTFS a ':' opens a stream name, so "payload.exe::$DATA" writes
    #"payload.exe", while a browser that cannot write ':' substitutes it and keeps the whole name,
    #so "a:b.exe" lands as "a_b.exe". A ':' in an ordinary name ("notes:v2.txt") is still accepted
    #here for an object already in a bucket; only a created key refuses it.
    for name in os.path.basename(keyPath.rstrip('/')).split(':'):
        while name and (name[-1] == '.' or name[-1].isspace()):
            name = name[:-1]
        extension = name[name.rfind('.'):].lower() if '.' in name else ''
        if extension and extension in UNALLOWED_FILE_EXTENSION_LIST:
            logger.error(f"Unallowed file extension detected in asset: {keyPath}")
            return False
    return True

def validateS3AssetExtensionsAndContentType(bucket: str, prefixKey: str):
    #Get list of all objects in a particular S3 key/prefix, paging to exhaustion so every object
    #under the prefix is inspected (a single page would leave objects past the first 1,000
    #unvalidated while callers still ingest them).
    objects = list_all_objects(bucket, prefixKey)

    #Check for each returned object if it is a valid asset based on ContentType
    #Check for all malicious executable MIME types. A prefix can hold many thousands of objects, so
    #the per-object head response is not logged; validateUnallowedFileExtensionAndContentType logs
    #the offending key when a check fails.
    for obj in objects:
        respHeader = s3_asset_client.head_object(Bucket=bucket, Key=obj['Key'])
        if not validateUnallowedFileExtensionAndContentType(obj['Key'], respHeader['ContentType']):
            return False
    return True


# Response overrides signed into every presigned GetObject URL that serves an asset or preview file
# to a client. An SVG upload is usually stored as application/octet-stream (browsers and the CLI
# send no SVG type), and a browser renders an SVG in <img> only when it arrives as image/svg+xml. The
# URL therefore asks S3 to answer with that type. It also asks for an attachment disposition, so
# opening the link directly downloads the file and no script inside the SVG runs at the bucket's
# origin; an <img> ignores the disposition and renders the image. response-* overrides are signed
# query parameters, so they work the same for KMS-encrypted and cross-account buckets, and the
# stored object and its metadata are never changed.
SVG_RESPONSE_CONTENT_TYPE = 'image/svg+xml'
SVG_RESPONSE_CONTENT_DISPOSITION = 'attachment'

# A download URL (attachment=True) asks S3 to answer with an attachment disposition naming the file
# by its key, whatever Content-Type or Content-Disposition the object was stored with, so opening the
# link saves the file under its own name. The bound is on the value as it is signed into the URL's
# query string (percent-encoded the way SigV4 encodes a query value), so it caps what the disposition
# adds to each URL: at most about 670 bytes, about 0.9 MB over a full bulk response of 1,500 URLs. It
# does not bound the response itself; the bulk route has no response-size guard. Every name of up
# to 255 ASCII letters, digits and "-._~" fits; a name that does not fit (long non-ASCII names
# first: each three-byte character takes 16 characters) is signed as a bare attachment, and the
# browser then names the file from the URL path, which ends in the same key.
ATTACHMENT_DISPOSITION_MAX_LENGTH = 640
# The characters SigV4 leaves unencoded in a query value (botocore.utils.SAFE_CHARS).
SIGNED_QUERY_SAFE_CHARS = '-._~'


def attachment_content_disposition(key: str) -> str:
    """The ``Content-Disposition`` that saves an object as a file named by the key's final segment.

    RFC 6266 form, plain ASCII whatever the key holds:
    ``attachment; filename="<fallback>"; filename*=UTF-8''<name, percent-encoded>``. The fallback
    replaces every character outside printable ASCII with ``_`` and escapes ``"`` and ``\\``; the
    extended value (RFC 8187) percent-encodes every UTF-8 byte outside the unreserved set, and
    browsers use it in preference to the fallback. A key ending in ``/`` (a folder marker) names no
    file and gets ``attachment`` alone, as does a name whose value, percent-encoded as it is signed
    into the URL, would pass ``ATTACHMENT_DISPOSITION_MAX_LENGTH``.
    """
    name = str(key or '').rsplit('/', 1)[-1]
    if not name:
        return 'attachment'
    fallback = ''.join(ch if ' ' <= ch <= '~' else '_' for ch in name)
    fallback = fallback.replace('\\', '\\\\').replace('"', '\\"')
    value = "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (fallback, quote(name, safe=''))
    if len(quote(value, safe=SIGNED_QUERY_SAFE_CHARS)) > ATTACHMENT_DISPOSITION_MAX_LENGTH:
        return 'attachment'
    return value


def presigned_get_object_params(params: dict, attachment: bool = False) -> dict:
    """Return a copy of GetObject ``params`` with the response overrides the URL needs.

    A key whose final name ends in ``.svg`` (any case) gets ``ResponseContentType`` image/svg+xml and
    ``ResponseContentDisposition`` attachment. With ``attachment`` (a download URL, never a stream or
    preview URL) every key gets ``ResponseContentDisposition`` from
    ``attachment_content_disposition``, which names the file. Without it, every other key is
    returned unchanged.
    """
    signed = dict(params)
    key = str(signed.get('Key') or '')
    if key.lower().endswith('.svg'):
        signed['ResponseContentType'] = SVG_RESPONSE_CONTENT_TYPE
        signed['ResponseContentDisposition'] = SVG_RESPONSE_CONTENT_DISPOSITION
    if attachment:
        signed['ResponseContentDisposition'] = attachment_content_disposition(key)
    return signed
