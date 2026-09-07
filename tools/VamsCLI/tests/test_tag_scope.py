# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock

from vamscli.utils.api_client import APIClient


def _client():
    # Bypass __init__/network setup; stub the internal request method.
    c = APIClient.__new__(APIClient)
    mock_response = MagicMock()
    mock_response.json.return_value = {"message": {"Items": []}}
    c.get = MagicMock(return_value=mock_response)
    return c


def _params(mock_get):
    _, kwargs = mock_get.call_args
    return kwargs.get("params", {}) or {}


def test_get_tags_passes_database_id():
    c = _client()
    c.get_tags(database_id="factory-db")
    assert _params(c.get).get("databaseId") == "factory-db"


def test_get_tags_passes_scope():
    c = _client()
    c.get_tags(scope="global")
    assert _params(c.get).get("scope") == "global"


def test_get_tags_no_scope_omits_params():
    c = _client()
    c.get_tags()
    params = _params(c.get)
    assert "databaseId" not in params and "scope" not in params


def _delete_client():
    c = APIClient.__new__(APIClient)
    mock_response = MagicMock()
    mock_response.json.return_value = {"message": "deleted"}
    c.delete = MagicMock(return_value=mock_response)
    return c


def _delete_params(mock_delete):
    _, kwargs = mock_delete.call_args
    return kwargs.get("params", {}) or {}


def test_delete_tag_passes_database_id():
    c = _delete_client()
    c.delete_tag("EquipID", database_id="factory-db")
    assert _delete_params(c.delete).get("databaseId") == "factory-db"


def test_delete_tag_no_database_omits_param():
    c = _delete_client()
    c.delete_tag("Status")
    assert "databaseId" not in _delete_params(c.delete)
