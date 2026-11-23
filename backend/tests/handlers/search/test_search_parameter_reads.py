# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Search reads its three OpenSearch settings on first use, through common.ssmParameters (#1820).

With OpenSearch enabled, the handler read the aos/* index names and the endpoint at import, each with
a new SSM client carrying Rule 6's five adaptive attempts, so under Parameter Store throttling init ran
toward Lambda's 10-second limit and a refused read failed the import. Import now reads nothing. The
first request reads the three values through the bounded, cached lookup; a value that cannot be read
answers the handler's internal error at once, naming no parameter, and the container then backs off.

The module is loaded by file path with ``boto3.client`` replaced by a recorder and the auth surface
stubbed, as ``test_search_without_opensearch.py`` does.
"""

import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import botocore.session
import pytest

from backend.tests.ssmParametersStub import (
    FakeClock,
    ParameterStoreStub,
    always,
    install_clock,
    serve_parameters_from,
    throttled,
)

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-db-table")

_SEARCH_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "search", "search.py"
)

SSM_ENV = {
    "OPENSEARCH_ASSET_INDEX_SSM_PARAM": "/vams-test/aos/assetIndexName",
    "OPENSEARCH_FILE_INDEX_SSM_PARAM": "/vams-test/aos/fileIndexName",
    "OPENSEARCH_ENDPOINT_SSM_PARAM": "/vams-test/aos/endPoint",
}
VALUES = {name: f"value-of-{name}" for name in SSM_ENV.values()}

ROUTES = [("POST", "/search"), ("GET", "/search"), ("POST", "/search/simple")]
INTERNAL_ERROR_BODY = {"message": "Internal Server Error"}


class _Recorder:
    """Stands in for ``boto3.client``: records every service asked for, and the config of every SSM
    client, and serves one SSM stub that answers every setting."""

    def __init__(self):
        self.services = []
        self.ssm_configs = []
        self.ssm = MagicMock()
        self.ssm.get_parameter.side_effect = lambda Name, **kwargs: {"Parameter": {"Value": VALUES[Name]}}

    def __call__(self, name, *args, **kwargs):
        self.services.append(name)
        if name == "ssm":
            self.ssm_configs.append(kwargs.get("config"))
        return self.ssm if name == "ssm" else MagicMock()


def _load(monkeypatch):
    monkeypatch.setenv("OPENSEARCH_DISABLED", "false")
    for key, value in SSM_ENV.items():
        monkeypatch.setenv(key, value)
    recorder = _Recorder()
    names = ("handlers.auth", "handlers.authz", "common.dynamodb")
    saved = {name: sys.modules.get(name) for name in names}

    authz_stub = types.ModuleType("handlers.authz")
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    authz_stub.CasbinEnforcer = MagicMock(return_value=enforcer)
    sys.modules["handlers.authz"] = authz_stub
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["user1"]})
    sys.modules["handlers.auth"] = auth_stub
    dynamodb_stub = types.ModuleType("common.dynamodb")
    dynamodb_stub.validate_pagination_info = MagicMock()
    sys.modules["common.dynamodb"] = dynamodb_stub

    try:
        with patch("boto3.client", side_effect=recorder), patch("boto3.resource", return_value=MagicMock()):
            spec = importlib.util.spec_from_file_location(
                "search_under_test_parameter_reads", os.path.abspath(_SEARCH_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
    return module, recorder


def _event(method, path):
    event = {
        "requestContext": {"http": {"method": method, "path": path}},
        "headers": {"authorization": "Bearer test-token"},
    }
    if method == "POST":
        event["body"] = json.dumps({"query": "x"})
    return event


@pytest.mark.unit
class TestImportReadsNothing:
    def test_import_makes_no_parameter_store_call(self, monkeypatch):
        """On the previous code, import read all three settings (and built an SSM client per read)."""
        _, recorder = _load(monkeypatch)
        assert "ssm" not in recorder.services, "search built an SSM client while importing"
        assert recorder.ssm.get_parameter.call_count == 0, "search read Parameter Store while importing"


@pytest.mark.unit
class TestSettingsAreReadOnFirstUse:
    def test_each_setting_is_read_once_per_container(self, monkeypatch):
        module, recorder = _load(monkeypatch)
        with patch("boto3.client", side_effect=recorder), patch.dict(sys.modules, {"opensearchpy": MagicMock()}):
            first = module.DualIndexSearchManager()
            second = module.DualIndexSearchManager()
        names = {call.kwargs["Name"] for call in recorder.ssm.get_parameter.call_args_list}
        assert names == set(SSM_ENV.values())
        assert recorder.ssm.get_parameter.call_count == 3, "a setting was read again inside its cache TTL"
        assert first.asset_index == VALUES[SSM_ENV["OPENSEARCH_ASSET_INDEX_SSM_PARAM"]]
        assert second.file_index == VALUES[SSM_ENV["OPENSEARCH_FILE_INDEX_SSM_PARAM"]]
        assert first.is_available() and second.is_available()

    def test_search_reads_through_the_bounded_client(self, monkeypatch):
        """Search built a new SSM client per read with Rule 6's config: 60 s timeouts and six attempts.
        The client its reads use now has 1 s / 2 s timeouts and one attempt, and is built once."""
        module, recorder = _load(monkeypatch)
        with patch("boto3.client", side_effect=recorder), patch.dict(sys.modules, {"opensearchpy": MagicMock()}):
            module.DualIndexSearchManager()
        assert recorder.ssm_configs, "search read no setting"
        for config in recorder.ssm_configs:
            resolved = botocore.session.get_session().create_client(
                "ssm", region_name="us-east-1", config=config).meta.config
            assert (resolved.connect_timeout, resolved.read_timeout) == (1, 2)
            assert resolved.retries.get("total_max_attempts") == 1
        assert len(recorder.ssm_configs) == 1, "a client was built per read"


@pytest.mark.unit
class TestAnUnreadableSettingAnswersAtOnce:
    """A setting that cannot be read answers the handler's internal error (500, the generic body it
    returns for any server-side failure) within the read budget, and the parameter name stays in the
    log. The previous code failed the import instead, after five adaptive retries per value."""

    @pytest.fixture
    def throttled_search(self, monkeypatch):
        module, _ = _load(monkeypatch)
        stub = ParameterStoreStub(values=VALUES, fault=always(throttled))
        helper = serve_parameters_from(module, stub)
        clock = install_clock(helper, FakeClock())
        return module, stub, helper, clock

    @pytest.mark.parametrize("method,path", ROUTES)
    def test_a_throttled_first_use_is_the_internal_error_inside_the_read_budget(self, throttled_search, method, path):
        module, stub, helper, clock = throttled_search
        started = clock.now
        with patch.dict(sys.modules, {"opensearchpy": MagicMock()}):
            response = module.lambda_handler(_event(method, path), None)
        assert response["statusCode"] == 500
        assert json.loads(response["body"]) == INTERNAL_ERROR_BODY
        assert "/vams-test/" not in response["body"]
        assert len(stub.calls) > 1, "the throttled read was never retried"
        assert clock.now - started <= helper.PARAMETER_READ_BUDGET_SECONDS

    def test_the_next_request_inside_the_back_off_window_reads_nothing(self, throttled_search):
        module, stub, _, _ = throttled_search
        with patch.dict(sys.modules, {"opensearchpy": MagicMock()}):
            first = module.lambda_handler(_event("POST", "/search"), None)
            calls_after_first = len(stub.calls)
            second = module.lambda_handler(_event("POST", "/search/simple"), None)
        assert first["statusCode"] == second["statusCode"] == 500
        assert len(stub.calls) == calls_after_first, "a request inside the back-off window read Parameter Store"

    def test_search_answers_again_once_the_window_has_passed(self, throttled_search):
        module, stub, helper, clock = throttled_search
        with patch.dict(sys.modules, {"opensearchpy": MagicMock()}):
            assert module.lambda_handler(_event("POST", "/search"), None)["statusCode"] == 500
            stub.fault = None
            clock.advance(helper.BACKOFF_MAX_SECONDS)
            manager = module.DualIndexSearchManager()
        assert manager.is_available()
        assert manager.asset_index == VALUES[SSM_ENV["OPENSEARCH_ASSET_INDEX_SSM_PARAM"]]

    @pytest.mark.parametrize("method,path", ROUTES)
    def test_a_missing_setting_is_the_internal_error_without_the_parameter_name(self, monkeypatch, method, path):
        module, _ = _load(monkeypatch)
        serve_parameters_from(module, ParameterStoreStub(values={}))
        with patch.dict(sys.modules, {"opensearchpy": MagicMock()}):
            response = module.lambda_handler(_event(method, path), None)
        assert response["statusCode"] == 500
        assert json.loads(response["body"]) == INTERNAL_ERROR_BODY
        assert "/vams-test/" not in response["body"] and "parameter" not in response["body"].lower()
