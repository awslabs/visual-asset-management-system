# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import threading
from botocore.exceptions import ClientError
from vams_utils.logging import log
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = log.get_logger()

client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"), config=retry_config)
s3 = boto3.resource('s3', region_name=os.getenv("AWS_REGION", "us-east-1"), config=retry_config)


# An asset bucket may be in another Region than this container. The pipeline definition carries a
# bucket name -> Region map (constructPipeline builds it from the manifest's bucket Regions), the
# entry point registers it, and every request for a registered bucket is signed with a client for
# that Region; the client above serves the buckets the map leaves out (the auxiliary bucket, a
# definition from an older Lambda). Per-Region clients keep the adaptive retry mode and address the
# regional us-east-1 endpoint, which an interface endpoint's private DNS covers where the global
# s3.amazonaws.com name is not.
_bucket_regions = {}
_clients_by_region = {}
_regional_retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'},
                                s3={'us_east_1_regional_endpoint': 'regional'})


def register_bucket_regions(bucket_regions):
    """Record the Region of each named bucket from a definition's bucketRegions map."""
    for bucket_name, region in (bucket_regions or {}).items():
        if bucket_name and region:
            _bucket_regions[bucket_name] = region


# The S3 version the run reads its input at, by (bucket, key). The upstream `main.py` downloads
# S3_INPUT with a bare `download_file`, so the routing client below adds the version to every read of
# a registered object; any other object is read as it is now.
_input_versions = {}

# Error codes Amazon S3 answers a read of a version that no longer exists with: NoSuchVersion or
# NoSuchKey (GET), 404 (HEAD of a deleted version), 405 / MethodNotAllowed (a delete marker).
_MISSING_VERSION_CODES = ("NoSuchVersion", "NoSuchKey", "404", "405", "MethodNotAllowed")


def register_input_version(bucket_name, object_key, version_id):
    """Record the version the run reads ``bucket_name``/``object_key`` at; an empty version records
    nothing, so the object is read as it is now."""
    if bucket_name and object_key and version_id:
        _input_versions[(bucket_name, object_key)] = version_id


def input_version_for(bucket_name, object_key):
    """The registered version of an object, or "" to read it as it is now."""
    return _input_versions.get((bucket_name, object_key), "")


def input_version_missing_cause(bucket_name, object_key, version_id):
    """The failure cause when ``version_id`` of the object no longer exists, else "". Another error is
    left for the download itself to report."""
    if not version_id:
        return ""
    try:
        client_for_bucket(bucket_name).head_object(Bucket=bucket_name, Key=object_key, VersionId=version_id)
    except ClientError as e:
        if str(e.response.get("Error", {}).get("Code", "")) in _MISSING_VERSION_CODES:
            return (f"The input file {object_key} at version {version_id} no longer exists; "
                    f"it was deleted after the run started.")
    return ""


def client_for_bucket(bucket_name):
    """The S3 client for the Region a bucket was registered in; the default client otherwise."""
    region = _bucket_regions.get(bucket_name)
    if not region or region == client.meta.region_name:
        return client
    regional = _clients_by_region.get(region)
    if regional is None:
        regional = boto3.client("s3", region_name=region, config=_regional_retry_config)
        _clients_by_region[region] = regional
    return regional


# Position of the Bucket argument in the boto3 transfer methods -- the only S3 client methods that
# take positional arguments (botocore API operations are keyword-only).
_TRANSFER_BUCKET_POSITION = {'download_file': 0, 'download_fileobj': 0,
                             'upload_file': 1, 'upload_fileobj': 1, 'copy': 1}

# Reads that take a VersionId: the transfer downloads through ExtraArgs, the API operations directly.
_TRANSFER_DOWNLOADS = ('download_file', 'download_fileobj')
_VERSIONED_READS = ('get_object', 'head_object')


def _with_input_version(name, args, kwargs):
    """``kwargs`` with the registered input version added to a read of a registered object. A call
    that already names a version, and every other call, are passed through unchanged."""
    if name in _TRANSFER_DOWNLOADS:
        bucket = kwargs.get('Bucket', args[0] if len(args) > 0 else None)
        key = kwargs.get('Key', args[1] if len(args) > 1 else None)
        version_id = input_version_for(bucket, key)
        extra_args = dict(kwargs.get('ExtraArgs') or {})
        if version_id and 'VersionId' not in extra_args:
            extra_args['VersionId'] = version_id
            return dict(kwargs, ExtraArgs=extra_args)
    elif name in _VERSIONED_READS and 'VersionId' not in kwargs:
        version_id = input_version_for(kwargs.get('Bucket'), kwargs.get('Key'))
        if version_id:
            return dict(kwargs, VersionId=version_id)
    return kwargs


class _RegionRoutingPaginator:
    """A paginator whose paginate() runs on the client for the Region of its Bucket."""

    def __init__(self, operation_name):
        self._operation_name = operation_name

    def paginate(self, **kwargs):
        return client_for_bucket(kwargs.get('Bucket')).get_paginator(
            self._operation_name).paginate(**kwargs)


class RegionRoutingClient:
    """An S3 client whose every call runs on the client for the Region of the bucket it names
    (client_for_bucket), so code that was written against one `boto3.client('s3')` -- the upstream
    `main.py`, which downloads S3_INPUT and uploads to S3_OUTPUT with one -- reaches a bucket in
    another Region without relying on botocore's redirect-on-error. A read of an object registered
    with `register_input_version` reads that version. A call that names no bucket, and attribute
    reads such as `meta` and `exceptions`, go to the default client."""

    @staticmethod
    def _bucket_from_call(name, args, kwargs):
        if 'Bucket' in kwargs:
            return kwargs['Bucket']
        position = _TRANSFER_BUCKET_POSITION.get(name)
        if position is not None and len(args) > position:
            return args[position]
        params = kwargs.get('Params')
        if isinstance(params, dict):
            return params.get('Bucket')
        return None

    def __getattr__(self, name):
        if name in ('meta', 'exceptions', '_endpoint', '_client_config'):
            return getattr(client, name)
        if name == 'get_paginator':
            return lambda operation_name: _RegionRoutingPaginator(operation_name)

        def call(*args, **kwargs):
            target = client_for_bucket(self._bucket_from_call(name, args, kwargs))
            return getattr(target, name)(*args, **_with_input_version(name, args, kwargs))
        return call


def region_routing_client():
    """A RegionRoutingClient over the buckets registered so far (and any registered later)."""
    return RegionRoutingClient()


def download(bucket_name, object_key, file_path):
    logger.info(
        "Downloading Object from S3 Bucket. Bucket: {}, Object: {}, File Path: {}".format(
            bucket_name, object_key, file_path
        )
    )
    try:
        with open(file_path, "wb") as data:
            client_for_bucket(bucket_name).download_fileobj(bucket_name, object_key, data)
    except ClientError as e:
        logger.exception(e)
        return None
    return file_path


def uploadV2(bucket_name, object_key, file_path):
    logger.info(
        f"Uploading Object to S3 Bucket w/ auto chunking for multi-part.\nBucket:{bucket_name}.\n:Object: {object_key}"
    )

   # Multipart upload
    try:
        GB = 1024 ** 3
        MB = 1024 ** 2
        config = TransferConfig(multipart_threshold=1*GB, max_concurrency=10,
                                multipart_chunksize=100*MB, use_threads=True
                                )
        client_for_bucket(bucket_name).upload_file(file_path, bucket_name, object_key,
                                   ExtraArgs={},
                                   Config=config,
                                   Callback=ProgressPercentage(file_path)
                                   )
    except ClientError as e:
        logger.exception(e)
        return None
    return object_key


def exists(bucket_name, object_key):
    logger.info(
        f"Checking if object exists in S3 Bucket\nBucket:{bucket_name}.\n:Object: {object_key}"
    )
    try:
        client_for_bucket(bucket_name).head_object(Bucket=bucket_name, Key=object_key)
    except ClientError:
        return False
    return True


def delete(bucket_name, object_key):
    logger.info(
        f"Deleting Object from S3 Bucket\nBucket:{bucket_name}.\n:Object: {object_key}"
    )
    try:
        client_for_bucket(bucket_name).delete_object(Bucket=bucket_name, Key=object_key)
    except ClientError as e:
        logger.exception(e)
        return None
    return object_key


def delete_all_path_contents(bucket_name: str, pathKey: str):
    """
    Delete all objects in a bucket path
    """

    try:
        # # check object versioning status of bucket
        # response = client.get_bucket_versioning(Bucket=bucket_name)
        # if 'Status' in response and response['Status'] == 'Enabled':
        #     print(f"Versioning Enabled on Bucket: {bucket_name}")

        #     # get all objects in bucket, paginate, and delete (including versions and markers)
        #     object_response_paginator = client.get_paginator('list_object_versions')
        #     delete_marker_list = []
        #     version_list = []

        #     for object_response_itr in object_response_paginator.paginate(Bucket=bucket_name, Prefix=pathKey):
        #         if 'DeleteMarkers' in object_response_itr:
        #             for delete_marker in object_response_itr['DeleteMarkers']:
        #                 delete_marker_list.append(
        #                     {'Key': delete_marker['Key'], 'VersionId': delete_marker['VersionId']})

        #         if 'Versions' in object_response_itr:
        #             for version in object_response_itr['Versions']:
        #                 version_list.append(
        #                     {'Key': version['Key'], 'VersionId': version['VersionId']})

        #     for i in range(0, len(delete_marker_list), 1000):
        #         print(f"Deleting {len(delete_marker_list)} Marker Objects")
        #         delResponse = client.delete_objects(
        #             Bucket=bucket_name,
        #             Delete={
        #                 'Objects': delete_marker_list[i:i+1000],
        #                 'Quiet': True
        #             }
        #         )

        #     for i in range(0, len(version_list), 1000):
        #         print(f"Deleting {len(version_list)} Version Objects")
        #         delResponse = client.delete_objects(
        #             Bucket=bucket_name,
        #             Delete={
        #                 'Objects': version_list[i:i+1000],
        #                 'Quiet': True
        #             }
        #         )

        # get all other objects in bucket (non-verionsed?) - Final object sweep.
        # Paginate the listing: a single page caps at 1,000 keys, and an octree or
        # splat output routinely exceeds that, which would leave stale files behind
        # when a preview is regenerated.
        object_keys = []
        paginator = client_for_bucket(bucket_name).get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket_name, Prefix=pathKey):
            object_keys.extend(map_object_keys(page.get("Contents", [])))

        if object_keys:
            print(f"Deleting {len(object_keys)} Other Objects")

            # delete objects from bucket path, in batches of the 1,000-key
            # delete_objects maximum
            for i in range(0, len(object_keys), 1000):
                client_for_bucket(bucket_name).delete_objects(
                    Bucket=bucket_name,
                    Delete={
                        'Objects': object_keys[i:i + 1000],
                    })
    except Exception as e:
        logger.exception(e)
        
# map object ket from object list for multi object delete request
def map_object_keys(objects) -> list:
    keys = []
    for o in objects:
        keys.append({
            'Key': o["Key"]
        })

    return keys

def get_all_files_in_path(bucket, path):
    result = {
        "Items": []
    }

    # Paginate: a single page caps at 1,000 keys, so an asset holding more files than
    # that would be only partially processed.
    paginator = client_for_bucket(bucket).get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket, Prefix=path):
        # map object from object list
        for o in page.get("Contents", []):
            result["Items"].append({
                'key': o['Key'],
                'relativePath': o['Key'].removeprefix(path)
            })

    # Log the length of files with a description
    logger.info("Files in the path: ")
    logger.info(len(result["Items"]))
    return result["Items"]

# Class for multipart upload
class ProgressPercentage(object):
    def __init__(self, filename):
        self._filename = filename
        self._size = float(os.path.getsize(filename))
        self._seen_so_far = 0
        self._lock = threading.Lock()

    def __call__(self, bytes_amount):
        # To simplify we'll assume this is hooked up
        # to a single filename.
        with self._lock:
            self._seen_so_far += bytes_amount
            percentage = (self._seen_so_far / self._size) * 100
            # sys.stdout.write(
            #     "\r%s  %s / %s  (%.2f%%)" % (
            #         self._filename, self._seen_so_far, self._size,
            #         percentage))
            # sys.stdout.flush()
