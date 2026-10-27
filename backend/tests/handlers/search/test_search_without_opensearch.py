# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The search handler on a deployment without OpenSearch.

buildSearchFunction sets OPENSEARCH_DISABLED to 'true' when neither provisioned nor serverless
OpenSearch is enabled, and such a deployment has no index or endpoint SSM parameters. The module
must still load, and every search route must answer the documented 404.
"""

import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-db-table")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")

_SEARCH_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "search", "search.py"
)

NOT_AVAILABLE = "Search is not available when OpenSearch feature is not enabled"


def _missing_parameter(**kwargs):
    raise ClientError(
        {"Error": {"Code": "ParameterNotFound", "Message": "not found"}}, "GetParameter"
    )


@pytest.fixture
def ssm_client():
    client = MagicMock()
    client.get_parameter.side_effect = _missing_parameter
    return client


def _load(monkeypatch, ssm_client, disabled):
    monkeypatch.setenv("OPENSEARCH_DISABLED", disabled)
    names = ("handlers.auth", "handlers.authz", "common.dynamodb")
    saved = {name: sys.modules.get(name) for name in names}

    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["user1"]})
    sys.modules["handlers.auth"] = auth_stub
    dynamodb_stub = types.ModuleType("common.dynamodb")
    dynamodb_stub.validate_pagination_info = MagicMock()
    dynamodb_stub.batch_get_items = MagicMock(return_value=([], []))
    sys.modules["common.dynamodb"] = dynamodb_stub

    def _boto_client(name, *args, **kwargs):
        return ssm_client if name == "ssm" else MagicMock()

    try:
        with patch("boto3.client", side_effect=_boto_client), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "search_under_test_without_opensearch", os.path.abspath(_SEARCH_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


def _event(method, path, body=None):
    event = {
        "requestContext": {"http": {"method": method, "path": path}},
        "headers": {"authorization": "Bearer test-token"},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


@pytest.mark.unit
class TestSearchWithoutOpenSearch:
    def test_module_loads_without_reading_ssm(self, monkeypatch, ssm_client):
        module = _load(monkeypatch, ssm_client, "true")
        assert ssm_client.get_parameter.call_count == 0
        assert module.DualIndexSearchManager().is_available() is False

    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("POST", "/search", {"query": "x"}),
            ("POST", "/search/simple", {"query": "x"}),
            ("GET", "/search", None),
        ],
    )
    def test_search_routes_answer_404(self, monkeypatch, ssm_client, method, path, body):
        module = _load(monkeypatch, ssm_client, "true")
        response = module.lambda_handler(_event(method, path, body), None)
        assert response["statusCode"] == 404
        assert json.loads(response["body"])["message"] == NOT_AVAILABLE

    def test_enabled_deployment_still_requires_its_parameters(self, monkeypatch, ssm_client):
        with pytest.raises(Exception, match="configuration parameter"):
            _load(monkeypatch, ssm_client, "false")
