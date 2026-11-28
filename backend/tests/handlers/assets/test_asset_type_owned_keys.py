# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An asset's type comes from the keys it owns, never from a nested asset's files (#1207).

The files under another asset's stored location, nested in this asset's, are that asset's. The four places
that classify an asset from a sample of its location (bucket sync, the upload completion, the large-file
completion and the large-copy engine) leave those keys out, so an asset with one own `.obj` file and a nested
asset of two `.glb` files is an `.obj` asset, not a `folder`. The lookup is a read
(`fileAttribution.nested_locations_for_asset_type`): it never walks the registrations, and a lookup that fails
classifies every key the location holds, as before, so the upload or copy step that sets the type does not
fail because of it.
"""

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

os.environ.setdefault("LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE_NAME", "t-copy-jobs")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "t-uploads")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-executions")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "sendEmailFunction")
os.environ.setdefault("PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME", "processOutput")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402
from backend.backend.handlers.assets import sqsUploadFileLarge  # noqa: F401,E402
from tests.common.fileAttributionFakes import (  # noqa: E402
    AttributionAssetTable, AttributionBucketsTable, asset_row, registration,
)
from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load as _load_bucket_sync  # noqa: E402

al = sys.modules['common.assetLocations']
fa = sys.modules['common.fileAttribution']

BUCKET = "vams-assets"
LOCATION = "p/outer/"
OUTER = asset_row("db-a", "outer", "reg-1", LOCATION, BUCKET)
# Nested assets in another database and in the same one: ownership decides, not the database
INNER = asset_row("db-b", "inner", "reg-1", "p/outer/inner/", BUCKET)
SAME_DB = asset_row("db-a", "kit", "reg-1", "p/outer/kit.glb", BUCKET)
OWN = ["p/outer/a.obj"]
NESTED_KEYS = ["p/outer/inner/b.glb", "p/outer/inner/c.glb", "p/outer/kit.glb"]
BACKEND = Path(__file__).resolve().parents[3] / "backend"


class _Table(AttributionAssetTable):
    """The attribution fake with a recorded update_item."""

    def __init__(self, rows):
        super().__init__(rows)
        self.updates = []

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        return {}


@pytest.fixture(autouse=True)
def _ready(monkeypatch):
    # Load the shared bucket-sync module before patching: loaded first while `index_ready` is patched, it
    # would bind the patch for good, and every later test on the worker would see an index that is ready
    _load_bucket_sync()
    al.clear_nested_locations_cache()
    monkeypatch.setattr(al, "index_ready", lambda name: True)
    yield
    al.clear_nested_locations_cache()


def _nested(rows=(OUTER, INNER, SAME_DB)):
    return fa.nested_locations_for_asset_type(AttributionAssetTable(list(rows)), OUTER, BUCKET, LOCATION)


def _listing(keys):
    def _list_all_objects(bucket, prefix, client=None, max_objects=None):
        listed = [{"Key": k} for k in sorted(keys) if k.startswith(prefix)]
        return listed[:max_objects] if max_objects is not None else listed
    return _list_all_objects


@pytest.mark.unit
class TestTheLookup:
    def test_it_names_the_nested_locations_whatever_their_database(self):
        assert _nested().locations == {"p/outer/inner/", "p/outer/kit.glb"}

    def test_it_is_a_read_and_never_walks_the_registrations(self, monkeypatch):
        monkeypatch.setattr(al, "index_ready", lambda name: False)
        buckets = AttributionBucketsTable([registration("reg-1", BUCKET, "p/")])
        table = AttributionAssetTable([OUTER, INNER])
        with patch.object(fa, "registrations_of_bucket", side_effect=AssertionError("walked")):
            nested = fa.nested_locations_for_asset_type(table, OUTER, BUCKET, LOCATION)
        assert not nested and table.reads == [] and buckets.reads == []

    def test_a_lookup_that_fails_classifies_every_key_as_before(self):
        table = AttributionAssetTable([OUTER])
        table.meta.client.query.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow down"}}, "Query")
        assert fa.nested_locations_for_asset_type(table, OUTER, BUCKET, LOCATION) is None


@pytest.mark.unit
class TestUploadAndLargeFileCompletion:
    @pytest.mark.parametrize("module_name", ["uploadFile", "sqsUploadFileLarge"])
    def test_the_type_leaves_the_nested_keys_out(self, module_name):
        module = importlib.import_module(f"backend.backend.handlers.assets.{module_name}")
        with patch.object(module, "list_all_objects", side_effect=_listing(OWN + NESTED_KEYS)), \
                patch.object(module, "is_file_archived", return_value=False):
            assert module.determine_asset_type("outer", BUCKET, LOCATION, nested=_nested()) == ".obj"
            # Control: without the nested locations the sample reads as a folder
            assert module.determine_asset_type("outer", BUCKET, LOCATION) == "folder"

    def test_the_large_file_completion_sets_the_type_from_the_owned_keys(self):
        module = sqsUploadFileLarge
        saved = []
        with patch.object(module, "asset_table", AttributionAssetTable([OUTER, INNER, SAME_DB])), \
                patch.object(module, "get_asset_details", return_value=dict(OUTER)), \
                patch.object(module, "list_all_objects", side_effect=_listing(OWN + NESTED_KEYS)), \
                patch.object(module, "is_file_archived", return_value=False), \
                patch.object(module, "update_asset_attributes",
                             side_effect=lambda db, asset, updates: saved.append(updates)), \
                patch.object(module, "send_subscription_email"):
            module.update_asset_after_file_processing("outer", "db-a", BUCKET, "p/outer/a.obj")
        assert [u["assetType"] for u in saved] == [".obj"]

    def test_both_upload_completions_classify_from_the_owned_keys(self):
        source = (BACKEND / "handlers" / "assets" / "uploadFile.py").read_text(encoding="utf-8")
        assert source.count("nested = nested_locations_for_asset_type(asset_table, asset, bucket_name, "
                            "asset_base_key,") == 2
        assert source.count("determine_asset_type(assetId, bucket_name, asset_base_key, nested=nested)") == 2


@pytest.fixture
def engine(monkeypatch):
    from backend.backend.handlers.assets import largeObjectCopy as module

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in OWN + NESTED_KEYS:
            client.put_object(Bucket=BUCKET, Key=key, Body=b"x")
        monkeypatch.setattr(module, "s3", client)
        monkeypatch.setattr(module, "lambda_client", MagicMock())
        yield module


@pytest.mark.unit
class TestTheLargeCopyEngine:
    def test_the_type_leaves_the_nested_keys_out(self, engine):
        assert engine._determine_asset_type(BUCKET, LOCATION, _nested()) == ".obj"
        assert engine._determine_asset_type(BUCKET, LOCATION) == "folder"

    def test_a_landed_copy_sets_the_type_from_the_owned_keys(self, engine, monkeypatch):
        table = _Table([OUTER, INNER, SAME_DB])
        monkeypatch.setattr(engine, "asset_table", table)
        engine._after_landing({"jobId": "j1", "databaseId": "db-a", "assetId": "outer",
                               "dest": {"bucket": BUCKET, "key": "p/outer/a.obj"}})
        assert [u["ExpressionAttributeValues"][":t"] for u in table.updates] == [".obj"]


@pytest.mark.unit
class TestBucketSync:
    def _module(self, keys):
        m = _load_bucket_sync()
        client = MagicMock()
        client.list_objects_v2.side_effect = lambda Bucket, Prefix, MaxKeys: {
            "Contents": [{"Key": k} for k in sorted(keys) if k.startswith(Prefix)][:MaxKeys]}
        return m, client

    def test_the_type_leaves_the_nested_keys_out(self):
        m, client = self._module(OWN + NESTED_KEYS)
        with patch.object(m, "s3_client", client), patch.object(m, "is_file_archived", return_value=False):
            assert m.determine_asset_type("outer", BUCKET, LOCATION, nested=_nested()) == ".obj"
            assert m.determine_asset_type("outer", BUCKET, LOCATION) == "folder"

    def test_a_synced_write_sets_the_type_from_the_owned_keys(self):
        m, client = self._module(OWN + NESTED_KEYS)
        # The loaded handler's lookup is the rule module the fixture made ready
        assert m.nested_locations_for_asset_type.__globals__["nested_locations_of"] is al.nested_locations_of
        written = []
        dynamodb = MagicMock()
        dynamodb.Table.return_value = AttributionAssetTable([OUTER, INNER, SAME_DB])
        with patch.object(m, "s3_client", client), patch.object(m, "is_file_archived", return_value=False), \
                patch.object(m, "dynamodb", dynamodb), \
                patch.object(m, "_update_asset_type_attribute",
                             side_effect=lambda b, a, d, t: written.append(t) or True):
            assert m.update_asset_type("reg-1", "outer", BUCKET, LOCATION, asset_data=dict(OUTER)) is True
        assert written == [".obj"]
