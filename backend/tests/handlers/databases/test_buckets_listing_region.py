#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Asset buckets in other Regions: what the API and the handlers expose about a bucket's Region.

The buckets table row carries `bucketRegion` (and `bucketAccountId` for a cross-account bucket),
written by the deploy-time populate custom resource. Three things hang off it:

- `GET /buckets` projects both so the web picker, the CLI and the MCP server can show them.
- Every bucket-details helper a handler resolves a bucket through returns them, so the handler's
  S3 calls and presigned URLs are signed for the bucket's Region.
- No asset-bucket handler builds a module-level S3 client of its own any more: each holds a
  `RegionRoutingS3Client`/`RegionRoutingS3Resource` from `common.s3`, which dispatches every call
  to the client for the Region of the bucket it names. The ratchet here holds that at zero for
  the listed modules; a `boto3.client('s3', ...)` added back to one of them would sign every
  request for the deployment Region and fail on the first cross-Region bucket.
"""

import json
import os
import re
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.databases.databaseService import get_buckets
from backend.backend.models.databases import BucketModel


_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

# Every handler module that reads or writes asset buckets, or mints presigned URLs for them.
ASSET_BUCKET_HANDLERS = [
    "handlers/assets/uploadFile.py", "handlers/assets/sqsUploadFileLarge.py",
    "handlers/assets/downloadAsset.py", "handlers/assets/streamAsset.py",
    "handlers/assets/streamAuxiliaryPreviewAsset.py", "handlers/assets/assetExportService.py",
    "handlers/assets/assetFiles.py", "handlers/assets/assetVersions.py",
    "handlers/assets/createAsset.py", "handlers/assets/ingestAsset.py", "handlers/assets/assetService.py",
    "handlers/metadata/metadataService.py",
    "handlers/workflows/executeWorkflow.py", "handlers/workflows/workflowTriggerService.py",
    "handlers/workflows/sfn/processWorkflowExecutionOutput.py",
    "handlers/workflows/sfn/interimPipelineTracking.py", "handlers/workflows/sfn/workflowTriggerDispatch.py",
    "handlers/indexing/sqsBucketSync.py", "handlers/indexing/fileIndexer.py", "handlers/indexing/crReindexer.py",
    "handlers/addon/garnetFramework/garnetDataIndexFile.py",
    "handlers/addon/physna/physnaCommon.py", "handlers/addon/physna/physnaFileSync.py",
    "handlers/pipelines/pipelineTemplateService.py",
]

# Every bucket-details helper, as (module path, helper name, row variable it reads).
BUCKET_DETAILS_HELPERS = [
    ("handlers/assets/assetService.py", "get_default_bucket_details"),
    ("handlers/assets/createAsset.py", "get_default_bucket_details"),
    ("handlers/assets/assetExportService.py", "get_default_bucket_details"),
    ("handlers/assets/assetFiles.py", "get_default_bucket_details"),
    ("handlers/assets/assetVersions.py", "get_default_bucket_details"),
    ("handlers/assets/downloadAsset.py", "get_default_bucket_details"),
    ("handlers/assets/streamAsset.py", "get_default_bucket_details"),
    ("handlers/assets/uploadFile.py", "get_default_bucket_details"),
    ("handlers/workflows/sfn/processWorkflowExecutionOutput.py", "get_default_bucket_details"),
    ("handlers/workflows/executeWorkflow.py", "_asset_bucket_details"),
    ("handlers/metadata/metadataService.py", "get_bucket_details"),
    ("handlers/indexing/fileIndexer.py", "get_bucket_details"),
    ("handlers/indexing/assetIndexer.py", "get_bucket_details"),
    ("handlers/addon/garnetFramework/garnetDataIndexFile.py", "get_bucket_details"),
    ("handlers/addon/physna/physnaCommon.py", "get_bucket_details"),
    ("handlers/addon/physna/physnaCommon.py", "get_bucket_details_by_name"),
]


def _read(relative):
    with open(os.path.join(_BACKEND, *relative.split("/")), encoding="utf-8") as f:
        return f.read()


def _scan_response(rows):
    def typed(value):
        if isinstance(value, bool):
            return {"BOOL": value}
        return {"S": str(value)}
    return {"Items": [{k: typed(v) for k, v in row.items()} for row in rows]}


@pytest.mark.unit
class TestBucketsListingRegion:
    def test_region_and_account_are_projected(self):
        rows = [
            {"bucketId": "b9a3aba3-c092-475f-978a-d39e5d5a2657", "bucketName": "local-bucket",
             "baseAssetsPrefix": "/", "isDefault": True, "bucketRegion": "us-west-2"},
            {"bucketId": "aa11bb22-c092-475f-978a-d39e5d5a2657", "bucketName": "remote-bucket",
             "baseAssetsPrefix": "/team-a/", "isDefault": False, "bucketRegion": "us-east-1",
             "bucketAccountId": "222222222222"},
        ]
        with patch("backend.backend.handlers.databases.databaseService.dbClient") as db:
            db.scan.return_value = _scan_response(rows)
            result = get_buckets({}, {})
        by_name = {i.bucketName: i for i in result.Items}
        assert by_name["local-bucket"].bucketRegion == "us-west-2"
        assert by_name["local-bucket"].bucketAccountId is None
        assert by_name["remote-bucket"].bucketRegion == "us-east-1"
        assert by_name["remote-bucket"].bucketAccountId == "222222222222"

    def test_a_row_without_a_region_reads_as_none_not_as_an_empty_string(self):
        rows = [{"bucketId": "cc33dd44-c092-475f-978a-d39e5d5a2657", "bucketName": "legacy-bucket",
                 "baseAssetsPrefix": "/"}]
        with patch("backend.backend.handlers.databases.databaseService.dbClient") as db:
            db.scan.return_value = _scan_response(rows)
            result = get_buckets({}, {})
        payload = json.loads(json.dumps(result.dict()))
        assert payload["Items"][0]["bucketRegion"] is None
        assert payload["Items"][0]["bucketAccountId"] is None

    def test_the_model_declares_both_fields_optional(self):
        for name in ("bucketRegion", "bucketAccountId"):
            field = BucketModel.__fields__[name]
            assert field.type_ is str
            assert field.required is False
            assert field.default is None
            assert not field.field_info.extra


@pytest.mark.unit
class TestBucketDetailsCarryTheRegion:
    @pytest.mark.parametrize("relative,helper", BUCKET_DETAILS_HELPERS,
                             ids=[f"{m}:{h}" for m, h in BUCKET_DETAILS_HELPERS])
    def test_every_bucket_details_helper_spreads_bucket_region_fields(self, relative, helper):
        source = _read(relative)
        start = source.index(f"def {helper}(")
        end = source.find("\ndef ", start + 1)
        body = source[start:end if end > 0 else None]
        assert "bucket_region_fields(" in body, (
            f"{relative}:{helper} returns bucket details without bucketRegion/bucketAccountId; "
            f"spread common.s3.bucket_region_fields(<row>) into the returned dict")
        assert re.search(r"^from common\.s3 import .*\bbucket_region_fields\b|^\s+bucket_region_fields,$",
                         source, re.M), f"{relative} uses bucket_region_fields without importing it"


@pytest.mark.unit
class TestNoDeploymentRegionS3ClientInAssetHandlers:
    S3_CLIENT = re.compile(r"boto3\.(client|resource)\(\s*['\"]s3['\"]")

    @pytest.mark.parametrize("relative", ASSET_BUCKET_HANDLERS)
    def test_the_module_holds_a_region_routing_client_not_a_plain_one(self, relative):
        source = _read(relative)
        plain = [line.strip() for line in source.splitlines() if self.S3_CLIENT.search(line)]
        assert plain == [], (
            f"{relative} builds a plain S3 client ({plain}); use common.s3.region_routing_s3_client() / "
            f"region_routing_s3_resource() so calls are signed for each bucket's Region")
        assert re.search(r"= region_routing_s3_(client|resource)\(\)", source), (
            f"{relative} holds no Region-routing S3 client")

    def test_no_handler_sets_the_regional_endpoint_variable_itself(self):
        # common/s3.py sets AWS_S3_US_EAST_1_REGIONAL_ENDPOINT once, before any client is built.
        offenders = [relative for relative in ASSET_BUCKET_HANDLERS
                     if "AWS_S3_US_EAST_1_REGIONAL_ENDPOINT" in _read(relative)]
        assert offenders == []
        assert 'os.environ.setdefault("AWS_S3_US_EAST_1_REGIONAL_ENDPOINT", "regional")' in _read("common/s3.py")

    def test_the_regex_sees_a_plain_client(self):
        # Positive control for the scan above.
        assert self.S3_CLIENT.search("s3 = boto3.client('s3', region_name=region, config=s3_config)")
        assert self.S3_CLIENT.search('s3r = boto3.resource("s3", config=cfg)')
