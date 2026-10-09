#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import time
import boto3
import json
from botocore.config import Config
from botocore.exceptions import ClientError
from common.validators import validate
from customLogging.logger import safeLogger
from common.constants import UNALLOWED_MIME_LIST, UNALLOWED_FILE_EXTENSION_LIST

logger = safeLogger(service_name="S3Common")

# Presigned URLs for us-east-1 buckets carry the regional endpoint rather than the global one, so a
# URL signed for one Region is never redirected to another. Set before any S3 client is built.
os.environ.setdefault("AWS_S3_US_EAST_1_REGIONAL_ENDPOINT", "regional")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

# Concurrency the asset handlers use for parallel per-file S3 work; the connection pool of every
# Regional client is sized to match so parallel calls do not queue on botocore's default pool of 10.
MAX_PARALLEL_S3_WORKERS = 16

# The one client configuration for asset-bucket traffic. SigV4 and path-style addressing make the
# signing Region part of every request, so a client is built per Region and a request for a bucket
# in another Region is signed for that Region rather than redirected or rejected.
S3_ASSET_CLIENT_CONFIG = Config(
    signature_version='s3v4',
    s3={'addressing_style': 'path', 'us_east_1_regional_endpoint': 'regional'},
    retries={'max_attempts': 5, 'mode': 'adaptive'},
    max_pool_connections=MAX_PARALLEL_S3_WORKERS
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


def bucket_region(bucket_details) -> str:
    """The Region of an asset bucket from its details or table row: the stored ``bucketRegion``, or
    the deployment Region for a row written without one."""
    region = (bucket_details or {}).get('bucketRegion')
    return region if region else deployment_region()


def bucket_region_fields(bucket_row) -> dict:
    """The Region and owning account of a bucket row, in the shape every bucket-details helper
    returns: ``bucketRegion`` always set (deployment Region when the row carries none),
    ``bucketAccountId`` when the row carries one. Also records the bucket name's Region so a later
    call that holds only the name routes to the same Region."""
    fields = {'bucketRegion': bucket_region(bucket_row)}
    account_id = (bucket_row or {}).get('bucketAccountId')
    if account_id:
        fields['bucketAccountId'] = account_id
    register_bucket_region((bucket_row or {}).get('bucketName'), fields['bucketRegion'])
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
    global _buckets_table
    now = time.monotonic()
    missed_at = _bucket_regions_misses.get(bucket_name)
    if missed_at is not None and now - missed_at < BUCKET_REGION_CACHE_TTL_SECONDS:
        return deployment_region()
    try:
        if _buckets_table is None:
            from common.resourceNames import get_table_name, ResourceKeys
            _buckets_table = boto3.resource('dynamodb', config=retry_config).Table(
                get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE))
        from boto3.dynamodb.conditions import Key
        response = _buckets_table.query(
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


class _RegionRoutingPaginator:
    """A paginator whose paginate() runs on the client for the Region of its Bucket."""

    def __init__(self, router, operation_name):
        self._router = router
        self._operation_name = operation_name

    def paginate(self, **kwargs):
        return self._router._client_for(kwargs.get('Bucket')).get_paginator(
            self._operation_name).paginate(**kwargs)


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

        def call(*args, **kwargs):
            return getattr(self._client_for(self._bucket_from_call(args, kwargs)), name)(*args, **kwargs)
        return call

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
    #Check if the content type is in the list of unallowed MIME types
    if contentType in UNALLOWED_MIME_LIST:
        logger.error(f"Unallowed file content type detected in asset: {keyPath}")
        return False
    
    #check if the file extension of the keyPath is in the list of unallowed file extensions
    if os.path.splitext(keyPath)[1] and os.path.splitext(keyPath)[1] in UNALLOWED_FILE_EXTENSION_LIST:
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