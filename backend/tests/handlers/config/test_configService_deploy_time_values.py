# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""/secure-config reads the web URL and the map key ARN with the resource names (#1820).

With both in the resource-name chunk parameters, the web app's start reads no Parameter Store
parameter of its own: two requests make the one chunk read and no GetParameter. While a deployment
is still adding the keys, each is read from the parameter that held it before, cached for five
minutes. With Amazon Location Service off nothing is looked up for the map, and a lookup Parameter
Store refuses still answers 200 with empty URLs.

configService is loaded by path with the auth surface stubbed and every other boto3 client a
MagicMock; the SSM client of this test's resourceNames and ssmParameters copies is a
ChunkedParameterStore.
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

_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "config", "configService.py"))
_loads = itertools.count()

LOCATION_PARAM = "/vams/location/apiKeyArn"
WEB_PARAM = "/vams/web/deployedUrl"
KEY_ARN = "arn:aws:geo:us-east-1:123456789012:api-key/vams-key"
TABLES = {"dynamoTables/appFeatureEnabledStorage": "t-features"}
DEPLOY_TIME = {"web/deployedUrl": "https://chunk.example.com", "location/apiKeyArn": KEY_ARN}
LEGACY = {WEB_PARAM: "https://legacy.example.com", LOCATION_PARAM: KEY_ARN}


def _event():
    return {"requestContext": {"http": {"method": "GET", "path": "/secure-config"}},
            "queryStringParameters": {}, "headers": {"authorization": "Bearer t"}}


def _paginator():
    paginator = MagicMock()
    paginator.paginate.return_value.build_full_result.return_value = {"Items": []}
    return paginator


def _load(monkeypatch, store, location=True, env=None):
    serve(monkeypatch, store)
    authz = types.ModuleType("handlers.authz")
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    authz.CasbinEnforcer = MagicMock(return_value=enforcer)
    auth = types.ModuleType("handlers.auth")
    auth.request_to_claims = MagicMock(return_value={"tokens": ["test-user"], "roles": []})
    monkeypatch.setitem(sys.modules, "handlers.authz", authz)
    monkeypatch.setitem(sys.modules, "handlers.auth", auth)
    monkeypatch.setenv("LOCATION_SERVICE_API_KEY_ARN_SSM_PARAM", LOCATION_PARAM)
    monkeypatch.setenv("LOCATION_SERVICE_URL_FORMAT", "https://maps.test/<apiKey>" if location else "")
    monkeypatch.setenv("WEB_DEPLOYED_URL_SSM_PARAM", WEB_PARAM)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    with patch("boto3.client", return_value=MagicMock()):
        spec = importlib.util.spec_from_file_location(f"configService_deploy_time_values_{next(_loads)}", _PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    module.dynamodb_client = MagicMock(**{"get_paginator.return_value": _paginator()})
    module.geo_client = MagicMock(**{"describe_key.return_value": {"Key": "key-value"}})
    return module


def _get(module):
    response = module.lambda_handler(_event(), MagicMock())
    assert response["statusCode"] == 200
    return json.loads(response["body"])


@pytest.mark.unit
class TestSecureConfigWithTheResourceNames:
    def test_two_requests_make_the_one_chunk_read_and_no_parameter_read(self, monkeypatch):
        store = ChunkedParameterStore({**TABLES, **DEPLOY_TIME}, LEGACY)
        module = _load(monkeypatch, store)
        for _ in range(2):
            body = _get(module)
            assert body["webDeployedUrl"] == "https://chunk.example.com"
            assert body["locationServiceApiUrl"] == "https://maps.test/key-value"
        assert (store.count("GetParameters"), store.parameter_reads()) == (1, [])
        module.geo_client.describe_key.assert_called_with(KeyName="vams-key")

    def test_a_deployment_still_adding_the_keys_reads_the_legacy_parameters_for_five_minutes(self, monkeypatch):
        store = ChunkedParameterStore(TABLES, LEGACY)
        clock = install_clock(sys.modules["common.ssmParameters"], FakeClock(), monkeypatch)
        module = _load(monkeypatch, store)
        assert _get(module)["webDeployedUrl"] == "https://legacy.example.com"
        assert sorted(store.parameter_reads()) == sorted([
            f"{PREFIX}/location/apiKeyArn", LOCATION_PARAM, f"{PREFIX}/web/deployedUrl", WEB_PARAM])
        reads = len(store.parameter_reads())
        clock.advance(299)
        _get(module)
        assert len(store.parameter_reads()) == reads
        store.parameters[WEB_PARAM] = "https://legacy-new.example.com"
        clock.advance(2)
        assert _get(module)["webDeployedUrl"] == "https://legacy-new.example.com"

    def test_with_location_off_nothing_is_looked_up_for_the_map(self, monkeypatch):
        store = ChunkedParameterStore({**TABLES, "web/deployedUrl": "https://chunk.example.com"}, {})
        module = _load(monkeypatch, store, location=False)
        body = _get(module)
        assert body["locationServiceApiUrl"] == ""
        assert body["webDeployedUrl"] == "https://chunk.example.com"
        assert store.parameter_reads() == []
        module.geo_client.describe_key.assert_not_called()

    def test_a_refused_lookup_answers_200_with_empty_urls_and_reads_no_legacy_parameter(self, monkeypatch):
        def fault(operation, _names):
            if operation == "GetParameters":
                raise throttling()
        store = ChunkedParameterStore({**TABLES, **DEPLOY_TIME}, LEGACY, fault=fault)
        install_clock(sys.modules["common.resourceNames"], FakeClock(), monkeypatch)
        module = _load(monkeypatch, store, env={"APPFEATUREENABLED_STORAGE_TABLE_NAME": "t-features"})
        body = _get(module)
        assert (body["locationServiceApiUrl"], body["webDeployedUrl"]) == ("", "")
        assert store.parameter_reads() == []
