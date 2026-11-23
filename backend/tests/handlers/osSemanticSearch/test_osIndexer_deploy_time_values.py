# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The OpenSearch indexers read their index name and endpoint with the resource names (#1820).

With the deploy-time values in the resource-name chunk parameters, an indexer's first batch makes no
Parameter Store call of its own: the chunk read its import-time table lookups make carries both. While
a deployment is still adding the keys, both are read from aos/* (one missed lookup first). A lookup
Parameter Store refuses fails the whole batch before any record is handled, so every record is
redelivered, and nothing is read from aos/*.

Each indexer is loaded by path with every boto3 client a MagicMock; the SSM client of this test's
resourceNames and ssmParameters copies is a ChunkedParameterStore.
"""

import importlib.util
import itertools
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.resourceNameChunksStub import PREFIX, ChunkedParameterStore, serve, throttling
from backend.tests.ssmParametersStub import FakeClock, install_clock

_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "osSemanticSearch")
_loads = itertools.count()
INDEXERS = ["osAssetIndexer", "osFileIndexer"]

ENV = {
    "OPENSEARCH_ENDPOINT_SSM_PARAM": "/vams/aos/endPoint",
    "OPENSEARCH_ASSET_INDEX_SSM_PARAM": "/vams/aos/assetIndexName",
    "OPENSEARCH_FILE_INDEX_SSM_PARAM": "/vams/aos/fileIndexName",
    "OPENSEARCH_TYPE": "serverless",
}
LEGACY = {
    "/vams/aos/endPoint": "https://legacy.aoss.example",
    "/vams/aos/assetIndexName": "assets-legacy",
    "/vams/aos/fileIndexName": "files-legacy",
}
TABLES = {
    "dynamoTables/assetStorage": "t-assets",
    "dynamoTables/assetFileMetadataStorage": "t-file-metadata",
    "dynamoTables/fileAttributeStorage": "t-file-attributes",
    "dynamoTables/s3AssetBucketsStorage": "t-buckets",
    "dynamoTables/assetFileVersionHistoryStorage": "t-file-history",
    "dynamoTables/assetLinksStorageV2": "t-links",
    "dynamoTables/assetVersionsStorage": "t-versions",
}
TABLE_ENV = {
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "ASSET_FILE_METADATA_STORAGE_TABLE_NAME": "t-file-metadata",
    "FILE_ATTRIBUTE_STORAGE_TABLE_NAME": "t-file-attributes",
    "S3_ASSET_BUCKETS_STORAGE_TABLE_NAME": "t-buckets",
    "ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE_NAME": "t-file-history",
    "ASSET_LINKS_STORAGE_TABLE_V2_NAME": "t-links",
    "ASSET_VERSIONS_STORAGE_TABLE_NAME": "t-versions",
}
DEPLOY_TIME = {
    "openSearch/endpoint": "https://chunk.aoss.example",
    "openSearch/assetIndexName": "assets-v3",
    "openSearch/fileIndexName": "files-v3",
}


def _index_of(module_name, module):
    return module.opensearch_asset_index if module_name == "osAssetIndexer" else module.opensearch_file_index


def _load(module_name, monkeypatch, store, env):
    serve(monkeypatch, store)
    for key, value in {**ENV, **env}.items():
        monkeypatch.setenv(key, value)
    authz = types.ModuleType("handlers.authz")
    authz.CasbinEnforcer = MagicMock()
    auth = types.ModuleType("handlers.auth")
    auth.request_to_claims = MagicMock(return_value={"tokens": ["mock_token"]})
    monkeypatch.setitem(sys.modules, "handlers.authz", authz)
    monkeypatch.setitem(sys.modules, "handlers.auth", auth)
    with patch("boto3.client", return_value=MagicMock()), patch("boto3.resource", return_value=MagicMock()):
        spec = importlib.util.spec_from_file_location(
            f"{module_name}_deploy_time_values_{next(_loads)}",
            os.path.abspath(os.path.join(_DIR, f"{module_name}.py")))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def _batch(*message_ids):
    """SQS records whose body is no SNS notification: handled without touching OpenSearch."""
    return {"Records": [{"messageId": message_id, "eventSource": "aws:sqs", "body": "{}"}
                        for message_id in message_ids]}


def _failed_ids(response):
    return sorted(item["itemIdentifier"] for item in response.get("batchItemFailures", []))


@pytest.mark.unit
@pytest.mark.parametrize("module_name", INDEXERS)
class TestIndexerSettingsWithTheResourceNames:
    def test_the_first_batch_reads_no_parameter_of_its_own(self, module_name, monkeypatch):
        store = ChunkedParameterStore({**TABLES, **DEPLOY_TIME}, LEGACY)
        module = _load(module_name, monkeypatch, store, {})
        first = module.lambda_handler(_batch("m1"), None)
        second = module.lambda_handler(_batch("m2"), None)
        assert _failed_ids(first) == _failed_ids(second) == []
        assert module.opensearch_endpoint == "https://chunk.aoss.example"
        assert _index_of(module_name, module) == ("assets-v3" if module_name == "osAssetIndexer" else "files-v3")
        assert (store.count("GetParameters"), store.parameter_reads()) == (1, [])

    def test_a_deployment_still_adding_the_keys_reads_both_from_aos(self, module_name, monkeypatch):
        store = ChunkedParameterStore(TABLES, LEGACY)
        module = _load(module_name, monkeypatch, store, {})
        assert _failed_ids(module.lambda_handler(_batch("m1"), None)) == []
        legacy_index = "/vams/aos/assetIndexName" if module_name == "osAssetIndexer" else "/vams/aos/fileIndexName"
        assert store.parameter_reads() == [f"{PREFIX}/openSearch/endpoint", "/vams/aos/endPoint", legacy_index]
        assert module.opensearch_endpoint == "https://legacy.aoss.example"

    def test_a_refused_lookup_fails_the_whole_batch_and_reads_no_aos_parameter(self, module_name, monkeypatch):
        def fault(operation, _names):
            if operation == "GetParameters":
                raise throttling()
        store = ChunkedParameterStore({**TABLES, **DEPLOY_TIME}, LEGACY, fault=fault)
        install_clock(sys.modules["common.resourceNames"], FakeClock(), monkeypatch)
        module = _load(module_name, monkeypatch, store, TABLE_ENV)
        handled = MagicMock(side_effect=AssertionError("a record was handled without the index settings"))
        for name in ("handle_asset_stream", "handle_metadata_stream", "handle_s3_notification"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, handled)
        response = module.lambda_handler(_batch("m1", "m2"), None)
        assert _failed_ids(response) == ["m1", "m2"]
        handled.assert_not_called()
        assert store.parameter_reads() == []
        assert module.opensearch_endpoint is None
