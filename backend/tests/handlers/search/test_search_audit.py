# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A search records the request it asked and the number of hits returned, never the hits, and never the
text its caller typed: that text sits under the one redacted ``searchText`` key."""

import importlib.util
import json
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.customLogging.logger import REDACTED, mask_sensitive_data

for _name, _value in (("ASSET_STORAGE_TABLE_NAME", "test-asset-table"), ("DATABASE_STORAGE_TABLE_NAME", "test-db"),
                      ("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index"),
                      ("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index"),
                      ("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint"), ("OPENSEARCH_TYPE", "provisioned")):
    os.environ.setdefault(_name, _value)

_SEARCH_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "search", "search.py")


def _load_search():
    """The real search module, loaded by path with boto3 and its auth imports stubbed."""
    stubs = {"handlers.authz": types.ModuleType("handlers.authz"), "handlers.auth": types.ModuleType("handlers.auth"),
             "common.dynamodb": types.ModuleType("common.dynamodb")}
    stubs["handlers.authz"].CasbinEnforcer = MagicMock()
    stubs["handlers.auth"].request_to_claims = MagicMock(return_value={"tokens": ["alice"]})
    stubs["common.dynamodb"].validate_pagination_info = MagicMock()
    stubs["common.dynamodb"].batch_get_items = MagicMock(return_value=([], []))
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
        parameters = kwargs["request_parameters"]
        assert "query" not in parameters, "the typed text is not a top-level request parameter"
        assert parameters["entityTypes"] == ["asset"], "identifier fields stay"
        assert parameters["from"] == 0, "the request is recorded under its API field names"
        assert "secret-hit-name" not in json.dumps(kwargs, default=str)

    def test_a_response_without_hits_records_no_count(self, monkeypatch):
        recorder = MagicMock()
        monkeypatch.setattr(svc, "log_action", recorder)
        request = svc.SimpleSearchRequestModel(query="bracket")
        svc._log_search({}, request, SimpleNamespace())
        assert recorder.call_args.kwargs["item_count"] is None


_TYPED = ("bracket-typed-marker", "meta-typed-marker", "token-typed-marker", "filter-typed-marker",
          "field-typed-marker", "name-typed-marker", "key-typed-marker", "metakey-typed-marker",
          "metaval-typed-marker")


def _recorded(request):
    """What a search record carries for the request once the log redaction has run."""
    return mask_sensitive_data(svc._search_request_parameters(request))


@pytest.mark.unit
class TestSearchTextIsRedacted:
    def test_a_search_request_records_no_typed_text(self):
        request = svc.SearchRequestModel(**{
            "query": "bracket-typed-marker", "metadataQuery": "meta-typed-marker",
            "tokens": [{"propertyKey": "str_assetname", "operator": ":", "value": "token-typed-marker"}],
            "filters": [{"query_string": {"query": "filter-typed-marker", "default_field": "field-typed-marker"}}],
            "entityTypes": ["asset"], "databaseId": "db1", "from": 20, "size": 10})
        recorded = _recorded(request)
        assert recorded["searchText"] == REDACTED
        assert not [marker for marker in _TYPED if marker in json.dumps(recorded)]
        assert recorded["searchTextFields"] == ["query", "metadataQuery", "tokens", "filters"]
        assert recorded["searchTextLength"] == sum(len(m) for m in _TYPED[:5])
        assert recorded["tokens"] == [{"operation": "AND", "operator": ":", "propertyKey": "str_assetname"}]
        assert recorded["filterCount"] == 1
        assert recorded["entityTypes"] == ["asset"] and recorded["from"] == 20 and recorded["size"] == 10
        assert "query" not in recorded and "filters" not in recorded and "metadataQuery" not in recorded

    def test_a_simple_search_request_records_no_typed_text(self):
        request = svc.SimpleSearchRequestModel(**{
            "query": "bracket-typed-marker", "assetName": "name-typed-marker", "fileKey": "key-typed-marker",
            "metadataKey": "metakey-typed-marker", "metadataValue": "metaval-typed-marker",
            "assetId": "a1", "databaseId": "db1", "tags": ["tag-typed-marker"], "fileExtension": "glb", "size": 5})
        recorded = _recorded(request)
        assert recorded["searchText"] == REDACTED
        assert not [marker for marker in _TYPED if marker in json.dumps(recorded)]
        assert "tag-typed-marker" not in json.dumps(recorded), "a typed tag is search text"
        assert recorded["searchTextFields"] == ["query", "metadataKey", "metadataValue", "assetName", "fileKey", "tags"]
        assert recorded["searchTextLength"] == sum(len(m) for m in _TYPED[5:]) + len(_TYPED[0]) + len("tag-typed-marker")
        assert "tags" not in recorded
        assert recorded["assetId"] == "a1" and recorded["fileExtension"] == "glb"
        assert recorded["databaseId"] == "db1" and recorded["size"] == 5

    def test_a_search_without_text_adds_no_text_keys(self):
        recorded = _recorded(svc.SearchRequestModel(**{"entityTypes": ["file"], "size": 10}))
        assert not {"searchText", "searchTextFields", "searchTextLength"} & set(recorded)

    def test_the_text_survives_in_the_unredacted_parameters_only_under_the_one_key(self):
        # Control: before redaction the text is present, under searchText, so the redaction (not a drop)
        # is what removes it. A key outside CONTENT_KEYS would write it in clear.
        raw = svc._search_request_parameters(svc.SimpleSearchRequestModel(query="bracket-typed-marker"))
        assert raw["searchText"] == {"query": "bracket-typed-marker"}
        assert "bracket-typed-marker" not in json.dumps({k: v for k, v in raw.items() if k != "searchText"})
