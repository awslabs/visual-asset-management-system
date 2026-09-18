# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The /search handler imports without OpenSearch and answers 404 on every route when it is disabled;
with OpenSearch enabled the three SSM parameters are read on first use, once, not at import.

The module is loaded by file path with ``boto3.client`` replaced by a recorder and the auth surface
stubbed, the way the other by-path search suites do it.
"""

import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_SEARCH_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "osSemanticSearch", "search.py")
_ACCESS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "common", "databaseAccess.py")
SSM_ENV = {
    "OPENSEARCH_ASSET_INDEX_SSM_PARAM": "/vams/aos/assetIndexName",
    "OPENSEARCH_FILE_INDEX_SSM_PARAM": "/vams/aos/fileIndexName",
    "OPENSEARCH_ENDPOINT_SSM_PARAM": "/vams/aos/endPoint",
}


class _Recorder:
    """Stands in for ``boto3.client``: records every service asked for and serves one ssm stub."""

    def __init__(self):
        self.services = []
        self.ssm = MagicMock()
        self.ssm.get_parameter.side_effect = lambda Name: {"Parameter": {"Value": f"value-of-{Name}"}}

    def __call__(self, name, *args, **kwargs):
        self.services.append(name)
        return self.ssm if name == "ssm" else MagicMock()


def _load_search(monkeypatch, env, unset=()):
    monkeypatch.setenv("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
    monkeypatch.setenv("DATABASE_STORAGE_TABLE_NAME", "test-db-table")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for key in unset:
        monkeypatch.delenv(key, raising=False)
    recorder = _Recorder()
    saved = {name: sys.modules.get(name) for name in ("handlers.auth", "handlers.authz", "common.dynamodb", "common.databaseAccess")}
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["t"]})
    authz_stub = types.ModuleType("handlers.authz")
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    authz_stub.CasbinEnforcer = MagicMock(return_value=enforcer)
    dynamodb_stub = types.ModuleType("common.dynamodb")
    dynamodb_stub.validate_pagination_info = MagicMock()
    sys.modules["handlers.auth"], sys.modules["handlers.authz"], sys.modules["common.dynamodb"] = auth_stub, authz_stub, dynamodb_stub
    try:
        with patch("boto3.client", side_effect=recorder), patch("boto3.resource", return_value=MagicMock()):
            access_spec = importlib.util.spec_from_file_location("common.databaseAccess", os.path.abspath(_ACCESS_PATH))
            access = importlib.util.module_from_spec(access_spec)
            access_spec.loader.exec_module(access)
            sys.modules["common.databaseAccess"] = access
            spec = importlib.util.spec_from_file_location("search_under_test_a4", os.path.abspath(_SEARCH_PATH))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, saved_module in saved.items():
            if saved_module is not None:
                sys.modules[name] = saved_module
            else:
                sys.modules.pop(name, None)
    return module, recorder


def _event(method, path):
    return {"requestContext": {"http": {"path": path, "method": method}}, "body": json.dumps({"query": "x"})}


class TestOpenSearchDisabled:
    @pytest.fixture
    def loaded(self, monkeypatch):
        return _load_search(monkeypatch, {"OPENSEARCH_DISABLED": "true"}, unset=tuple(SSM_ENV))

    def test_import_makes_no_ssm_call(self, loaded):
        module, recorder = loaded
        assert "ssm" not in recorder.services
        assert module.opensearch_disabled is True

    @pytest.mark.parametrize("method,path", [("POST", "/search"), ("GET", "/search"), ("POST", "/search/simple")])
    def test_every_route_answers_404(self, loaded, method, path):
        module, recorder = loaded
        response = module.lambda_handler(_event(method, path), None)
        assert response["statusCode"] == 404
        assert json.loads(response["body"])["message"] == "Search is not available when OpenSearch is not enabled"
        assert "ssm" not in recorder.services

    def test_manager_is_unavailable_without_a_client(self, loaded):
        module, _ = loaded
        manager = module.DualIndexSearchManager()
        assert manager.is_available() is False and manager.client is None


class TestOpenSearchEnabled:
    def test_ssm_is_read_once_on_first_use(self, monkeypatch):
        module, recorder = _load_search(monkeypatch, {"OPENSEARCH_DISABLED": "false", **SSM_ENV})
        assert "ssm" not in recorder.services
        assert recorder.ssm.get_parameter.call_count == 0

        with patch("boto3.client", side_effect=recorder), patch.dict(sys.modules, {"opensearchpy": MagicMock()}):
            first = module.DualIndexSearchManager()
            assert recorder.ssm.get_parameter.call_count == 3
            second = module.DualIndexSearchManager()
            assert recorder.ssm.get_parameter.call_count == 3
        names = {call.kwargs["Name"] for call in recorder.ssm.get_parameter.call_args_list}
        assert names == set(SSM_ENV.values())
        assert first.asset_index == "value-of-/vams/aos/assetIndexName"
        assert second.file_index == "value-of-/vams/aos/fileIndexName"

    def test_client_failure_leaves_the_manager_unavailable(self, monkeypatch):
        module, recorder = _load_search(monkeypatch, {"OPENSEARCH_DISABLED": "false", **SSM_ENV})
        broken = MagicMock()
        broken.OpenSearch.side_effect = RuntimeError("no network")
        with patch("boto3.client", side_effect=recorder), patch.dict(sys.modules, {"opensearchpy": broken}):
            manager = module.DualIndexSearchManager()
        assert manager.is_available() is False
        with pytest.raises(module.VAMSGeneralErrorResponse):
            manager.search_dual_index({}, {}, ["file"])
