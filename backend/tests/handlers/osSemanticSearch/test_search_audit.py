# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A search records the request it asked and the number of hits returned, never the hits."""

import importlib.util
import json
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

for _name, _value in (("ASSET_STORAGE_TABLE_NAME", "test-asset-table"), ("DATABASE_STORAGE_TABLE_NAME", "test-db"),
                      ("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index"),
                      ("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index"),
                      ("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint"), ("OPENSEARCH_TYPE", "provisioned")):
    os.environ.setdefault(_name, _value)

_SEARCH_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "osSemanticSearch", "search.py")


def _load_search():
    """The real search module, loaded by path with boto3, its auth imports and the database pre-filter
    (common.databaseAccess) stubbed."""
    stubs = {"handlers.authz": types.ModuleType("handlers.authz"), "handlers.auth": types.ModuleType("handlers.auth"),
             "common.dynamodb": types.ModuleType("common.dynamodb"),
             "common.databaseAccess": types.ModuleType("common.databaseAccess")}
    stubs["handlers.authz"].CasbinEnforcer = MagicMock()
    stubs["handlers.auth"].request_to_claims = MagicMock(return_value={"tokens": ["alice"]})
    stubs["common.dynamodb"].validate_pagination_info = MagicMock()
    stubs["common.dynamodb"].batch_get_items = MagicMock(return_value=([], []))
    stubs["common.databaseAccess"].DatabaseAccessManager = MagicMock()
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}
    try:
        with patch("boto3.client", side_effect=lambda name, *a, **k: ssm if name == "ssm" else MagicMock()), \
                patch("boto3.resource", return_value=MagicMock()):
            spec = importlib.util.spec_from_file_location("search_under_test_audit", os.path.abspath(_SEARCH_PATH))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, module_before in saved.items():
            if module_before is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module_before
    return module


svc = _load_search()


@pytest.mark.unit
class TestSearchRecord:
    def test_the_request_and_the_hit_count_are_recorded(self, monkeypatch):
        recorder = MagicMock()
        monkeypatch.setattr(svc, "log_action", recorder)
        request = svc.SimpleSearchRequestModel(**{"query": "bracket", "entityTypes": ["asset"], "from": 0,
                                                  "size": 10})
        hits = [{"_source": {"str_assetname": "secret-hit-name"}}, {"_source": {}}]
        event = {"body": "{}"}
        svc._log_search(event, request, SimpleNamespace(hits=SimpleNamespace(hits=hits)))
        recorder.assert_called_once()
        assert recorder.call_args.args == (event, "search")
        kwargs = recorder.call_args.kwargs
        assert kwargs["item_count"] == 2
        assert kwargs["request_parameters"]["query"] == "bracket"
        assert kwargs["request_parameters"]["from"] == 0, "the request is recorded under its API field names"
        assert "secret-hit-name" not in json.dumps(kwargs, default=str)

    def test_a_response_without_hits_records_no_count(self, monkeypatch):
        recorder = MagicMock()
        monkeypatch.setattr(svc, "log_action", recorder)
        request = svc.SimpleSearchRequestModel(query="bracket")
        svc._log_search({}, request, SimpleNamespace())
        assert recorder.call_args.kwargs["item_count"] is None
