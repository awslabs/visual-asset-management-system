# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The file indexer records only a file's own preview as its preview key.

`find_preview_file_key` lists `{s3_key}.previewFile.` and stores the first match in the file's search
document (`str_previewfilekey`). The listed prefix also matches keys inside a folder named like the
preview, such as `p/model.glb.previewFile.png/model.glb`, which is not a preview of `p/model.glb`. Only
a key `{s3_key}.previewFile.{ext}` with no `/` after the stem counts, as in the file-operation lookups
(`common.assetLocations._is_preview_of`).

Run against a versioned moto bucket, so the listing answers as S3 does.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_FILE_INDEXER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "backend", "handlers", "osSemanticSearch", "osFileIndexer.py",
)

BUCKET = "asset-bucket"
FILE = "p/model.glb"
OWN_PREVIEW = "p/model.glb.previewFile.png"
FOLDER_KEY = "p/model.glb.previewFile.png/model.glb"


def _boto_client(name, *args, **kwargs):
    if name == "ssm":
        ssm = MagicMock()
        ssm.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}
        return ssm
    return MagicMock()


@pytest.fixture
def indexer():
    """The real osFileIndexer module, loaded by path with boto3 stubbed, its S3 client a versioned
    moto bucket."""
    saved = {name: sys.modules.get(name) for name in ("handlers.auth", "handlers.authz")}
    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["mock_token"]})
    sys.modules["handlers.auth"] = auth_stub
    try:
        with patch("boto3.client", side_effect=_boto_client), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "osFileIndexer_preview_key_under_test", os.path.abspath(_FILE_INDEXER_PATH))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        with patch.object(module, "s3_client", client):
            yield module, client


def _put(client, *keys):
    for key in keys:
        client.put_object(Bucket=BUCKET, Key=key, Body=b"x")


@pytest.mark.unit
class TestThePreviewKeyIsTheFilesOwnPreview:
    def test_a_key_inside_a_folder_named_like_the_preview_is_not_the_preview(self, indexer):
        module, client = indexer
        _put(client, FILE, FOLDER_KEY)

        assert module.find_preview_file_key(BUCKET, FILE) == ""

    def test_the_own_preview_is_returned(self, indexer):
        """Positive control: refusing every key would pass the test above on its own."""
        module, client = indexer
        _put(client, FILE, OWN_PREVIEW)

        assert module.find_preview_file_key(BUCKET, FILE) == OWN_PREVIEW

    def test_the_own_preview_is_returned_beside_such_a_folder(self, indexer):
        module, client = indexer
        _put(client, FILE, OWN_PREVIEW, FOLDER_KEY)

        assert module.find_preview_file_key(BUCKET, FILE) == OWN_PREVIEW

    def test_the_own_preview_is_found_past_a_folder_that_sorts_first_with_many_keys(self, indexer):
        """A folder sorting before the own preview lists as one entry, so its keys do not fill the
        listing's five-key window."""
        module, client = indexer
        _put(client, FILE, OWN_PREVIEW, *[f"p/model.glb.previewFile.aaa/k{i}.glb" for i in range(8)])

        assert module.find_preview_file_key(BUCKET, FILE) == OWN_PREVIEW

    def test_a_preview_of_another_file_with_the_same_prefix_is_not_the_preview(self, indexer):
        """`p/model.glb2.previewFile.png` does not start with `p/model.glb.previewFile.`."""
        module, client = indexer
        _put(client, FILE, "p/model.glb2", "p/model.glb2.previewFile.png")

        assert module.find_preview_file_key(BUCKET, FILE) == ""

    def test_a_file_with_no_preview_has_none(self, indexer):
        module, client = indexer
        _put(client, FILE)

        assert module.find_preview_file_key(BUCKET, FILE) == ""
