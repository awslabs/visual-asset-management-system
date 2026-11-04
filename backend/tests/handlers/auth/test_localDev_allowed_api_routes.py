# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The local development server's GET /auth/routes/api/allowed mock.

With ALLOW_ALL_ROUTES on (the default, as for the POST /auth/routes mock) the mock reports every
public API route as allowed, so capability-gated UI renders locally; with it off it reports none.

CI installs no Flask, so a stub flask module whose Blueprint decorators return the decorated
function unchanged stands in for it, and the module is loaded by path.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

_SERVER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "localDev_api_server.py"
)


def _passthrough_decorator(*args, **kwargs):
    def decorate(fn):
        return fn
    return decorate


class _Blueprint:
    def __init__(self, *args, **kwargs):
        pass

    route = staticmethod(_passthrough_decorator)

    @staticmethod
    def after_request(fn):
        return fn


def _stub_modules():
    flask = types.ModuleType("flask")
    flask.Flask = object
    flask.Blueprint = _Blueprint
    flask.request = None
    flask.redirect = flask.url_for = flask.make_response = lambda *a, **k: None
    jwt = types.ModuleType("jwt")
    mock_data = types.ModuleType("localMockData")
    mock_data.__path__ = []
    mock_roles = types.ModuleType("localMockData.mockRoles")
    mock_roles.mockRoles = {}
    # The POST /auth/routes mock delegates to the real handler; this test does not reach it.
    auth = types.ModuleType("handlers.auth")
    auth.routes = MagicMock()
    return {"flask": flask, "jwt": jwt, "localMockData": mock_data,
            "localMockData.mockRoles": mock_roles, "handlers.auth": auth}


@pytest.fixture
def server():
    stubs = _stub_modules()
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            "localDev_api_server_under_test", os.path.abspath(_SERVER_PATH))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
    return module


@pytest.mark.unit
class TestLocalAllowedApiRoutesMock:
    def test_default_reports_every_public_route(self, server, monkeypatch):
        monkeypatch.delenv("ALLOW_ALL_ROUTES", raising=False)
        body = server.allowedApiRoutes()
        assert body["routes"], "the default must report allowed routes"
        assert all(route["path"] and route["methods"] for route in body["routes"])
        assert {route["path"] for route in body["routes"]} >= {"/database", "/auth/routes/api/allowed"}
        assert body["userId"] == "vams-test-user"

    def test_switch_off_reports_none(self, server, monkeypatch):
        monkeypatch.setenv("ALLOW_ALL_ROUTES", "false")
        assert server.allowedApiRoutes() == {"routes": []}
