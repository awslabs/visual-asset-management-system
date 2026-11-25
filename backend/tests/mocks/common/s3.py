# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Mock of common.s3 for tests.

The real module validates file extensions and MIME types against blocklists.
Tests default these to "valid" unless explicitly mocked.
"""

# Mirror the real module's page-size constants for import parity.
S3_VERSIONS_PAGE_SIZE = 1000
S3_OBJECTS_PAGE_SIZE = 1000

import os
import time  # noqa: E402
import boto3
from botocore.config import Config
from customLogging.logger import safeLogger  # noqa: E402

logger = safeLogger()

# ---------------------------------------------------------------------------
# Per-Region client factory and Region-routing client/resource.
#
# Same contract as the real module: a client per bucket Region, a Region-routing client whose
# every call runs on the client for the Region of its Bucket, and bucket details that carry
# ``bucketRegion``/``bucketAccountId``. The Region of a bucket name comes from the details a
# handler resolved (register_bucket_region) or defaults to AWS_REGION — the real module's
# buckets-table lookup is not performed here, so a test never reaches DynamoDB through it.
# Clients are created lazily so a moto mock active at call time intercepts them.
# ---------------------------------------------------------------------------

MAX_PARALLEL_S3_WORKERS = 16
S3_CLIENT_MAX_POOL_CONNECTIONS = 50
S3_ASSET_CLIENT_CONFIG = Config(
    signature_version='s3v4',
    s3={'addressing_style': 'path', 'us_east_1_regional_endpoint': 'regional'},
    retries={'max_attempts': 5, 'mode': 'adaptive'},
    max_pool_connections=S3_CLIENT_MAX_POOL_CONNECTIONS,
)

S3_ASSET_ACCELERATED_CLIENT_CONFIG = Config(
    signature_version='s3v4',
    s3={'addressing_style': 'path', 'us_east_1_regional_endpoint': 'regional',
        'use_accelerate_endpoint': True},
    retries={'max_attempts': 5, 'mode': 'adaptive'},
    max_pool_connections=S3_CLIENT_MAX_POOL_CONNECTIONS,
)

# The one-attempt client of the repeat-safe final copy (#1848); same values as the shipped module.
S3_ASSET_SINGLE_ATTEMPT_CLIENT_CONFIG = Config(
    signature_version='s3v4',
    s3={'addressing_style': 'path', 'us_east_1_regional_endpoint': 'regional'},
    retries={'total_max_attempts': 1, 'mode': 'adaptive'},
    max_pool_connections=S3_CLIENT_MAX_POOL_CONNECTIONS,
)

_s3_clients_by_region = {}
_s3_resources_by_region = {}
_s3_single_attempt_clients_by_region = {}
_bucket_regions_by_name = {}


def deployment_region():
    return os.environ['AWS_REGION']


def lambdas_in_vpc():
    return os.environ.get('VAMS_LAMBDAS_IN_VPC', '').lower() == 'true'


def s3_client_for_region(region=None):
    region = region or deployment_region()
    client = _s3_clients_by_region.get(region)
    if client is None:
        client = boto3.client('s3', region_name=region, config=S3_ASSET_CLIENT_CONFIG)
        _s3_clients_by_region[region] = client
    return client


def s3_resource_for_region(region=None):
    region = region or deployment_region()
    resource = _s3_resources_by_region.get(region)
    if resource is None:
        resource = boto3.resource('s3', region_name=region, config=S3_ASSET_CLIENT_CONFIG)
        _s3_resources_by_region[region] = resource
    return resource


def s3_single_attempt_client_for_region(region=None):
    region = region or deployment_region()
    client = _s3_single_attempt_clients_by_region.get(region)
    if client is None:
        client = boto3.client('s3', region_name=region, config=S3_ASSET_SINGLE_ATTEMPT_CLIENT_CONFIG)
        _s3_single_attempt_clients_by_region[region] = client
    return client


# S3 Transfer Acceleration: same contract as the shipped module. The flag comes from bucket details
# (register_bucket_accelerated / bucket_region_fields); the mock performs no table lookup, so a name
# nobody registered is not accelerated and a test never reaches DynamoDB through it.
BUCKET_ACCELERATE_CACHE_TTL_SECONDS = 600
BUCKET_ACCELERATE_FAILURE_TTL_SECONDS = 30
ACCELERATED_PRESIGN_METHODS = frozenset({'get_object', 'upload_part', 'put_object'})
_s3_accelerated_clients_by_region = {}
_bucket_accelerated_by_name = {}


class AccelerateNotSupportedError(ValueError):
    pass


def s3_accelerated_client_for_region(region=None):
    region = region or deployment_region()
    client = _s3_accelerated_clients_by_region.get(region)
    if client is None:
        client = boto3.client('s3', region_name=region, config=S3_ASSET_ACCELERATED_CLIENT_CONFIG)
        _s3_accelerated_clients_by_region[region] = client
    return client


def accelerated_presign_client_for_bucket(bucket_name, region=None):
    if '.' in (bucket_name or ''):
        raise AccelerateNotSupportedError(
            f"Bucket {bucket_name} has a period in its name, which S3 Transfer Acceleration does "
            f"not support")
    return s3_accelerated_client_for_region(region or bucket_region_for_name(bucket_name))


def register_bucket_accelerated(bucket_name, accelerated):
    if bucket_name:
        _bucket_accelerated_by_name[bucket_name] = (bool(accelerated), time.monotonic())


def bucket_is_accelerated(bucket_name):
    cached = _bucket_accelerated_by_name.get(bucket_name)
    if cached is not None and time.monotonic() - cached[1] < BUCKET_ACCELERATE_CACHE_TTL_SECONDS:
        return cached[0]
    return False


def bucket_region(bucket_details):
    region = (bucket_details or {}).get('bucketRegion')
    return region if region else deployment_region()


def register_bucket_region(bucket_name, region):
    if bucket_name and region:
        _bucket_regions_by_name[bucket_name] = region


def bucket_region_fields(bucket_row):
    fields = {'bucketRegion': bucket_region(bucket_row)}
    account_id = (bucket_row or {}).get('bucketAccountId')
    if account_id:
        fields['bucketAccountId'] = account_id
    bucket_name = (bucket_row or {}).get('bucketName')
    register_bucket_region(bucket_name, fields['bucketRegion'])
    if (bucket_row or {}).get('isAccelerated') is True:
        fields['isAccelerated'] = True
    if bucket_row is not None and bucket_name and 'bucketName' in bucket_row:
        register_bucket_accelerated(bucket_name, bucket_row.get('isAccelerated') is True)
    return fields


def s3_client_for_bucket(bucket_details):
    region = bucket_region(bucket_details)
    register_bucket_region((bucket_details or {}).get('bucketName'), region)
    return s3_client_for_region(region)


def s3_resource_for_bucket(bucket_details):
    region = bucket_region(bucket_details)
    register_bucket_region((bucket_details or {}).get('bucketName'), region)
    return s3_resource_for_region(region)


def bucket_region_for_name(bucket_name):
    return _bucket_regions_by_name.get(bucket_name) or deployment_region()


def s3_client_for_bucket_name(bucket_name):
    return s3_client_for_region(bucket_region_for_name(bucket_name))


def s3_resource_for_bucket_name(bucket_name):
    return s3_resource_for_region(bucket_region_for_name(bucket_name))


_COPY_ARGS_NOT_FOR_PUT = frozenset({'MetadataDirective', 'TaggingDirective', 'CopySourceIfMatch',
                                    'CopySourceIfModifiedSince', 'CopySourceIfNoneMatch',
                                    'CopySourceIfUnmodifiedSince', 'CopySourceSSECustomerAlgorithm',
                                    'CopySourceSSECustomerKey', 'CopySourceSSECustomerKeyMD5'})
_COPIED_OBJECT_HEADERS = ('CacheControl', 'ContentDisposition', 'ContentEncoding',
                          'ContentLanguage', 'ContentType', 'Expires')


def copy_s3_object(source_bucket, source_key, dest_bucket, dest_key, extra_args=None,
                   source_version_id=None):
    """Same branches as the real helper: destination-Region managed copy, or a streamed GET -> PUT
    when the Lambdas run in the VPC and the Regions differ."""
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
    put_args = {key: value for key, value in extra_args.items() if key not in _COPY_ARGS_NOT_FOR_PUT}
    source_object = source_client.get_object(**copy_source)
    if extra_args.get('MetadataDirective') != 'REPLACE':
        put_args.setdefault('Metadata', source_object.get('Metadata', {}))
        for header in _COPIED_OBJECT_HEADERS:
            value = source_object.get(header)
            if value and header not in put_args:
                put_args[header] = value
    dest_client.upload_fileobj(source_object['Body'], dest_bucket, dest_key,
                               ExtraArgs=put_args or None)


# The repeat-safe final copy of an upload or workflow output (#1848). Same signature, refusal,
# threshold and branch selection as the shipped module; each copy is sent once, because the shipped
# module's retry-and-landed-check loop is tested on the shipped module itself
# (tests/common/test_s3_repeat_safe_copy.py), not through this mock.
SINGLE_REQUEST_COPY_MAX_BYTES = 8 * 1024 * 1024
SINGLE_REQUEST_COPY_MAX_ATTEMPTS = 6
SINGLE_REQUEST_COPY_BACKOFF_BASE = 2
SINGLE_REQUEST_COPY_MAX_BACKOFF_SECONDS = 20
LANDED_CHECK_CLOCK_MARGIN_SECONDS = 2
LANDED_CHECK_MAX_VERSION_HEADS = 50
_COPY_UPLOAD_TOKEN_METADATA_KEY = "vams-copyuploadtoken"


def is_retryable_write_error(error):
    from botocore.exceptions import ClientError, ConnectionError as BotocoreConnectionError, HTTPClientError
    if isinstance(error, (BotocoreConnectionError, HTTPClientError)):
        return True
    if not isinstance(error, ClientError):
        return False
    code = (error.response.get('Error') or {}).get('Code') or ''
    status = (error.response.get('ResponseMetadata') or {}).get('HTTPStatusCode')
    return (code in {'Throttling', 'ThrottlingException', 'ThrottledException', 'RequestThrottledException',
                     'TooManyRequestsException', 'ProvisionedThroughputExceededException',
                     'TransactionInProgressException', 'RequestLimitExceeded', 'BandwidthLimitExceeded',
                     'LimitExceededException', 'RequestThrottled', 'SlowDown', 'PriorRequestNotComplete',
                     'EC2ThrottledException', 'RequestTimeout', 'RequestTimeoutException'}
            or status in {500, 502, 503, 504})


def single_request_copy_backoff(attempt, rng):
    return rng() * min(SINGLE_REQUEST_COPY_BACKOFF_BASE ** attempt, SINGLE_REQUEST_COPY_MAX_BACKOFF_SECONDS)


def copy_s3_object_repeat_safe(source_bucket, source_key, dest_bucket, dest_key, extra_args, write_token,
                               source_size, source_version_id=None, sleep=time.sleep, rng=None):
    extra_args = dict(extra_args or {})
    metadata = extra_args.get('Metadata')
    if (not write_token or extra_args.get('MetadataDirective') != 'REPLACE' or not isinstance(metadata, dict)
            or metadata.get(_COPY_UPLOAD_TOKEN_METADATA_KEY) != write_token):
        raise ValueError("A repeat-safe copy must replace the metadata with one that carries its write token")
    if not isinstance(source_size, int) or source_size >= SINGLE_REQUEST_COPY_MAX_BYTES:
        copy_s3_object(source_bucket, source_key, dest_bucket, dest_key, extra_args=extra_args,
                       source_version_id=source_version_id)
        return None
    source_region = bucket_region_for_name(source_bucket)
    dest_region = bucket_region_for_name(dest_bucket)
    write_client = s3_single_attempt_client_for_region(dest_region)
    copy_source = {'Bucket': source_bucket, 'Key': source_key}
    if source_version_id:
        copy_source['VersionId'] = source_version_id
    if source_region == dest_region or not lambdas_in_vpc():
        response = write_client.copy_object(CopySource=copy_source, Bucket=dest_bucket, Key=dest_key,
                                            **extra_args)
    else:
        body = s3_client_for_region(source_region).get_object(**copy_source)['Body'].read()
        put_args = {key: value for key, value in extra_args.items() if key not in _COPY_ARGS_NOT_FOR_PUT}
        response = write_client.put_object(Bucket=dest_bucket, Key=dest_key, Body=body, **put_args)
    return response.get('VersionId')


class _RegionRoutingPaginator:
    def __init__(self, router, operation_name):
        self._router = router
        self._operation_name = operation_name

    def paginate(self, **kwargs):
        return self._router._client_for(kwargs.get('Bucket')).get_paginator(
            self._operation_name).paginate(**kwargs)


def _accelerated_presign(ordinary_client, bucket, args, kwargs):
    # Same contract as the shipped module: commercial partition only, and an accelerate problem is
    # logged and signed without acceleration instead of failing the request.
    partition = ordinary_client.meta.partition
    if partition != 'aws':
        logger.warning(f"Bucket {bucket} is flagged for Transfer Acceleration but runs in "
                       f"partition {partition}, signing without acceleration.")
        return None
    try:
        client = accelerated_presign_client_for_bucket(bucket, ordinary_client.meta.region_name)
        return client.generate_presigned_url(*args, **kwargs)
    except Exception as e:
        logger.error(f"Could not sign an accelerated URL for bucket {bucket} "
                     f"({type(e).__name__}: {e}), signing without acceleration.")
        return None


class RegionRoutingS3Client:
    def __init__(self, config=None):
        self._config_override = config
        self._clients_by_region = {}
        # Bound at construction, so a module that builds its router at import binds its
        # deployment-Region client there (which is where tests stub boto3.client).
        self._default_client = boto3.client('s3', region_name=deployment_region(),
                                            config=self._client_config())

    def _client_config(self):
        if self._config_override is None:
            return S3_ASSET_CLIENT_CONFIG
        return S3_ASSET_CLIENT_CONFIG.merge(self._config_override)

    def _client_for(self, bucket):
        if not bucket:
            return self._default_client
        region = bucket_region_for_name(bucket)
        if region == deployment_region():
            return self._default_client
        if self._config_override is None:
            return s3_client_for_region(region)
        client = self._clients_by_region.get(region)
        if client is None:
            client = boto3.client('s3', region_name=region, config=self._client_config())
            self._clients_by_region[region] = client
        return client

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


def region_routing_s3_client(config=None) -> RegionRoutingS3Client:
    """A new Region-routing S3 client for a handler module's module-level client."""
    return RegionRoutingS3Client(config)


def region_routing_s3_resource() -> RegionRoutingS3Resource:
    """A new Region-routing S3 resource for a handler module's module-level resource."""
    return RegionRoutingS3Resource()


# The instances the shared helpers in this module fall back to when a caller passes no client.
s3_asset_client = region_routing_s3_client()
s3_asset_resource = region_routing_s3_resource()


def validateUnallowedFileExtensionAndContentType(keyPath, contentType):
    """Mock: always returns True (valid)."""
    return True


def is_object_version_archived(bucket, key, version_id=None, client=None):
    """Mock: head-based archive check using the provided client.

    Mirrors the real helper's head_object-based contract so handlers under test
    behave the same. Returns False when no client is supplied.
    """
    if client is None:
        return False
    try:
        if version_id:
            try:
                client.head_object(Bucket=bucket, Key=key, VersionId=version_id)
                return False
            except Exception as e:
                code = getattr(e, "response", {}).get("Error", {}).get("Code") if hasattr(e, "response") else None
                if code in ("405", "MethodNotAllowed"):
                    return True
                if code in ("NoSuchKey", "404", "NotFound"):
                    return False
                raise
        else:
            try:
                response = client.head_object(Bucket=bucket, Key=key)
                return response.get("DeleteMarker", False)
            except Exception as e:
                code = getattr(e, "response", {}).get("Error", {}).get("Code") if hasattr(e, "response") else None
                if code in ("NoSuchKey", "404", "NotFound"):
                    # Single-entry, exact-key, IsLatest match, per common.s3's docstring: the
                    # key leads its own prefix page and its versions come newest first.
                    versions_response = client.list_object_versions(
                        Bucket=bucket, Prefix=key, MaxKeys=1)
                    return any(marker.get("Key") == key and marker.get("IsLatest")
                               for marker in versions_response.get("DeleteMarkers", []))
                raise
    except Exception:
        return False


def validateS3AssetExtensionsAndContentType(bucket, prefixKey):
    """Mock: always returns True (valid)."""
    return True


def list_all_object_versions(bucket, prefix, client=None, max_keys=None,
                             key_marker=None, version_id_marker=None):
    """Mock: page through the provided client's list_object_versions, or empty.

    Mirrors the real helper's contract (aggregated Versions + DeleteMarkers) so
    handlers under test behave the same. When a client is supplied it makes a
    single call (sufficient for test fakes that return all data at once). The
    max_keys / marker params are accepted for signature parity and ignored.
    """
    if client is None:
        return {"Versions": [], "DeleteMarkers": []}
    response = client.list_object_versions(Bucket=bucket, Prefix=prefix)
    return {
        "Versions": response.get("Versions", []),
        "DeleteMarkers": response.get("DeleteMarkers", []),
    }


def list_key_versions_to_newest_version(bucket, key, client=None):
    """Mock with the real helper's logic: page the provided client's list_object_versions with
    Prefix=key until a page holds a version of the key, an entry of another key, or IsTruncated is
    not True; return only the key's own entries. Without a client, empty."""
    if client is None:
        return {"Versions": [], "DeleteMarkers": []}
    versions, delete_markers = [], []
    list_kwargs = {"Bucket": bucket, "Prefix": key, "MaxKeys": S3_VERSIONS_PAGE_SIZE}
    while True:
        response = client.list_object_versions(**list_kwargs)
        page_versions = response.get("Versions", []) or []
        page_markers = response.get("DeleteMarkers", []) or []
        own_versions = [v for v in page_versions if v.get("Key") == key]
        versions.extend(own_versions)
        delete_markers.extend(m for m in page_markers if m.get("Key") == key)
        other_key_seen = any(e.get("Key") != key for e in list(page_versions) + list(page_markers))
        if own_versions or other_key_seen or response.get("IsTruncated") is not True:
            return {"Versions": versions, "DeleteMarkers": delete_markers}
        list_kwargs["KeyMarker"] = response.get("NextKeyMarker")
        list_kwargs["VersionIdMarker"] = response.get("NextVersionIdMarker")


def list_all_objects(bucket, prefix, client=None, max_objects=None):
    """Mock: return objects under a prefix via the provided client, or empty.

    Honors max_objects so callers that cap the listing (e.g. asset-type
    detection) behave the same as the real helper.
    """
    if client is None:
        return []
    objects = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objects.extend(page.get("Contents", []))
        if max_objects is not None and len(objects) >= max_objects:
            return objects[:max_objects]
    return objects


# Same rule as the real module: presigned asset/preview downloads of an SVG key carry the SVG
# response type and an attachment disposition; every other key is unchanged.
SVG_RESPONSE_CONTENT_TYPE = 'image/svg+xml'
SVG_RESPONSE_CONTENT_DISPOSITION = 'attachment'


def presigned_get_object_params(params):
    """Mirror of the real helper (pure function, no AWS calls)."""
    signed = dict(params)
    if str(signed.get('Key') or '').lower().endswith('.svg'):
        signed['ResponseContentType'] = SVG_RESPONSE_CONTENT_TYPE
        signed['ResponseContentDisposition'] = SVG_RESPONSE_CONTENT_DISPOSITION
    return signed
