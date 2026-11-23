# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Search reads the OpenSearch endpoint and index names with the resource names (#1820).

The deployment publishes the three values in the resource-name chunk parameters, so a cold start of
search makes one Parameter Store call, the chunk read the table lookup already makes, instead of that
call plus three GetParameter reads of aos/*. While a deployment is still adding the keys, search
reads all three from aos/* (one missed lookup first, not three). A lookup Parameter Store refuses
answers the existing 503 and reads nothing more; with OpenSearch off, search answers 404 with no
read at all.

search.py is loaded by path with the auth surface stubbed and every boto3 client a MagicMock; the
SSM client of this test's resourceNames and ssmParameters copies is a ChunkedParameterStore.
"""

import importlib.util
import itertools
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.resourceNameChunksStub import PREFIX, ChunkedParameterStore, serve, throttling
from backend.tests.ssmParametersStub import FakeClock, install_clock

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")
_SEARCH_PATH = os.path.abspath(os.path.join(_BACKEND, "handlers", "osSemanticSearch", "search.py"))
_ACCESS_PATH = os.path.abspath(os.path.join(_BACKEND, "common", "databaseAccess.py"))
_loads = itertools.count()

LEGACY = {
    "/vams/aos/endPoint": "https://legacy.aoss.example",
    "/vams/aos/assetIndexName": "assets-legacy",
    "/vams/aos/fileIndexName": "files-legacy",
}
ENV = {
    "OPENSEARCH_ENDPOINT_SSM_PARAM": "/vams/aos/endPoint",
    "OPENSEARCH_ASSET_INDEX_SSM_PARAM": "/vams/aos/assetIndexName",
    "OPENSEARCH_FILE_INDEX_SSM_PARAM": "/vams/aos/fileIndexName",
}
TABLES = {"dynamoTables/assetStorage": "assets-table", "dynamoTables/databaseStorage": "databases-table"}
DEPLOY_TIME = {
    "openSearch/endpoint": "https://chunk.aoss.example",
    "openSearch/assetIndexName": "assets-v3",
    "openSearch/fileIndexName": "files-v3",
}


def _load(monkeypatch, store, env):
    serve(monkeypatch, store)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    auth = types.ModuleType("handlers.auth")
    auth.request_to_claims = MagicMock(return_value={"tokens": ["t"]})
    authz = types.ModuleType("handlers.authz")
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    authz.CasbinEnforcer = MagicMock(return_value=enforcer)
    dynamodb_stub = types.ModuleType("common.dynamodb")
    dynamodb_stub.validate_pagination_info = MagicMock()
    dynamodb_stub.batch_get_items = MagicMock(return_value=([], []))
    monkeypatch.setitem(sys.modules, "handlers.auth", auth)
    monkeypatch.setitem(sys.modules, "handlers.authz", authz)
    monkeypatch.setitem(sys.modules, "common.dynamodb", dynamodb_stub)
    with patch("boto3.client", return_value=MagicMock()), patch("boto3.resource", return_value=MagicMock()):
        access_spec = importlib.util.spec_from_file_location("common.databaseAccess", _ACCESS_PATH)
        access = importlib.util.module_from_spec(access_spec)
        access_spec.loader.exec_module(access)
        monkeypatch.setitem(sys.modules, "common.databaseAccess", access)
        spec = importlib.util.spec_from_file_location(f"search_deploy_time_values_{next(_loads)}", _SEARCH_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def _event(method="POST", path="/search"):
    return {"requestContext": {"http": {"method": method, "path": path}, "authorizer": {"jwt": {"claims": {}}}},
            "body": json.dumps({}), "queryStringParameters": {}}


@pytest.mark.unit
class TestSearchSettingsWithTheResourceNames:
    def test_a_cold_start_makes_the_one_chunk_read_and_no_parameter_read(self, monkeypatch):
        store = ChunkedParameterStore({**TABLES, **DEPLOY_TIME}, LEGACY)
        module = _load(monkeypatch, store, {**ENV, "OPENSEARCH_DISABLED": "false"})
        settings = module.opensearch_settings()
        assert settings == {"endpoint": "https://chunk.aoss.example", "asset_index": "assets-v3",
                            "file_index": "files-v3"}
        module.opensearch_settings()
        assert (store.count("GetParameters"), store.parameter_reads()) == (1, [])

    def test_a_deployment_still_adding_the_keys_reads_all_three_from_aos(self, monkeypatch):
        store = ChunkedParameterStore(TABLES, LEGACY)
        module = _load(monkeypatch, store, {**ENV, "OPENSEARCH_DISABLED": "false"})
        settings = module.opensearch_settings()
        assert settings == {"endpoint": "https://legacy.aoss.example", "asset_index": "assets-legacy",
                            "file_index": "files-legacy"}
        # One lookup of the endpoint's own parameter misses; the index keys are not looked up.
        assert store.parameter_reads() == [
            f"{PREFIX}/openSearch/endpoint",
            "/vams/aos/endPoint", "/vams/aos/assetIndexName", "/vams/aos/fileIndexName",
        ]

    def test_a_refused_lookup_is_the_503_and_reads_no_aos_parameter(self, monkeypatch):
        # Table names from the environment, so the module imports; the chunk read is throttled.
        def fault(operation, _names):
            if operation == "GetParameters":
                raise throttling()
        store = ChunkedParameterStore({**TABLES, **DEPLOY_TIME}, LEGACY, fault=fault)
        clock = install_clock(sys.modules["common.resourceNames"], FakeClock(), monkeypatch)
        module = _load(monkeypatch, store, {**ENV, "OPENSEARCH_DISABLED": "false",
                                            "ASSET_STORAGE_TABLE_NAME": "assets-table",
                                            "DATABASE_STORAGE_TABLE_NAME": "databases-table"})
        started = clock.now
        with patch.dict(sys.modules, {"opensearchpy": MagicMock()}):
            response = module.lambda_handler(_event(), None)
        assert response["statusCode"] == 503
        assert json.loads(response["body"]) == {"message": module.SEARCH_UNAVAILABLE_MESSAGE}
        assert store.parameter_reads() == []
        assert clock.now - started <= sys.modules["common.resourceNames"].SWEEP_BUDGET_SECONDS

    def test_with_opensearch_off_search_answers_404_with_no_lookup(self, monkeypatch):
        store = ChunkedParameterStore({**TABLES}, {})
        module = _load(monkeypatch, store, {"OPENSEARCH_DISABLED": "true",
                                            "ASSET_STORAGE_TABLE_NAME": "assets-table",
                                            "DATABASE_STORAGE_TABLE_NAME": "databases-table"})
        response = module.lambda_handler(_event(), None)
        assert response["statusCode"] == 404
        assert store.calls == []
