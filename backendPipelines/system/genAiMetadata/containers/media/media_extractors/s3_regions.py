# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Amazon S3 clients for asset buckets outside the deployment Region.

The pipeline state carries a bucket name -> Region map (``bucketRegions``, built by constructPipeline from
the workflow manifest's bucket Regions). A handler registers it once per invocation, and every request for
a registered bucket is signed with a client for that Region; a bucket the map leaves out (the auxiliary
bucket, the run bucket, a state from an older Lambda) uses the handler's default client. Per-Region clients
keep the adaptive retry mode and address the regional ``us-east-1`` endpoint, which an interface endpoint's
private DNS covers where the global ``s3.amazonaws.com`` name is not.

Every S3 client of the media image is built from ``S3_CLIENT_CONFIG``: Signature Version 4 on the bucket
Region's regional endpoint with path-style addressing, the configuration the VAMS backend's asset clients use
(``common.s3.S3_ASSET_CLIENT_CONFIG``). Without an explicit ``signature_version`` botocore presigns with
Signature Version 2 in the Regions that still accept it (us-east-1, us-west-2 and others), and Amazon S3 refuses
a Signature Version 2 URL for an object encrypted with an AWS KMS key. The handlers read a large input over a
presigned URL, so the client that presigns it must sign with Signature Version 4, for the bucket's own Region.
"""

from typing import Dict, Optional

import boto3
from botocore.config import Config

# The signing and addressing every media-image S3 client uses (see the module notes).
S3_SIGNATURE_VERSION = "s3v4"
S3_ADDRESSING = {"addressing_style": "path", "us_east_1_regional_endpoint": "regional"}
S3_CLIENT_CONFIG = Config(retries={'max_attempts': 5, 'mode': 'adaptive'}, signature_version=S3_SIGNATURE_VERSION,
                          s3=dict(S3_ADDRESSING))

_bucket_regions: Dict[str, str] = {}
_clients_by_region: Dict[str, object] = {}
_regional_retry_config = S3_CLIENT_CONFIG


def register_bucket_regions(bucket_regions: Optional[Dict[str, str]]) -> None:
    """Record the Region of each named bucket from a state's ``bucketRegions`` map."""
    for bucket_name, region in (bucket_regions or {}).items():
        if bucket_name and region:
            _bucket_regions[str(bucket_name)] = str(region)


def client_for_bucket(bucket_name: str, default_client):
    """The S3 client for the Region ``bucket_name`` was registered in; ``default_client`` otherwise."""
    region = _bucket_regions.get(bucket_name)
    if not region or region == getattr(getattr(default_client, "meta", None), "region_name", None):
        return default_client
    regional = _clients_by_region.get(region)
    if regional is None:
        regional = boto3.client("s3", region_name=region, config=_regional_retry_config)
        _clients_by_region[region] = regional
    return regional


def registered_region(bucket_name: str) -> str:
    """The Region ``bucket_name`` was registered in, or an empty string."""
    return _bucket_regions.get(bucket_name, "")
