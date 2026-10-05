# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""``DatabaseAccessManager.get_accessible_databases`` reads the database table to the end.

The search pre-filter scans the database table through the ``scan`` paginator with a
``ScanFilter`` (``databaseId NOT_CONTAINS "#deleted"``) and ``PageSize`` 100. DynamoDB applies
``Limit`` to the items it evaluates and the filter afterwards, so a page whose evaluated rows are
all ``#deleted`` tombstones comes back as ``Items: []`` with a ``LastEvaluatedKey``, and the
paginator yields it and requests the next page. The paginator itself ends the scan when a page
carries no ``LastEvaluatedKey`` or ``MaxItems`` is reached. A live database after an empty page must
still be returned: a database missing from the list is invisible to both search endpoints, and an
empty list becomes a ``match_none`` restriction, which matches no documents.

``TestEmptyFilteredPages`` drives the loop with MagicMock pages, the stub shape used by
``test_database_prefilter_object_type.py``. ``TestRealScanPaginator`` drives it with a real botocore
``scan`` paginator under ``Stubber``, which pins the premise the MagicMock tests rest on: the real
paginator hands the loop an empty filtered page and then the page after it.

``get_accessible_databases`` returns ``[]`` on any exception, so an unexpected ``scan`` call under
``Stubber`` surfaces as an empty result rather than as an error; every Stubber test therefore asserts
a non-empty result.

The module is loaded from its file path because the root conftest registers mock
``handlers``/``common`` packages that shadow the real ones -- the same approach as
``test_database_prefilter_object_type.py``.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.stub import Stubber

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-db-table")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_SEARCH_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "search", "search.py"
)

_ssm_stub = MagicMock()
_ssm_stub.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}


def _boto_client(name, *args, **kwargs):
    if name == "ssm":
        return _ssm_stub
    return MagicMock()


@pytest.fixture
def search_module():
    """The real search module, loaded by file path with boto3 stubbed."""
    saved = {
        name: sys.modules.get(name)
        for name in ("handlers.auth", "handlers.authz", "common.dynamodb")
    }

    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub

    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["mock_token"]})
    sys.modules["handlers.auth"] = auth_stub

    dynamodb_stub = types.ModuleType("common.dynamodb")
    dynamodb_stub.validate_pagination_info = MagicMock()
    sys.modules["common.dynamodb"] = dynamodb_stub

    try:
        with patch("boto3.client", side_effect=_boto_client), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "search_under_test_db_scan_paging", os.path.abspath(_SEARCH_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


class _AllowAllEnforcer:
    """Stands in for CasbinEnforcer, grants GET on every database row and records each document, so
    the returned list is decided by the scan loop alone."""

    documents = []

    def __init__(self, claims_and_roles):
        self.claims_and_roles = claims_and_roles

    def enforce(self, document, action):
        _AllowAllEnforcer.documents.append(dict(document))
        return True


@pytest.fixture
def allow_all(search_module):
    _AllowAllEnforcer.documents = []
    search_module.CasbinEnforcer = _AllowAllEnforcer
    return _AllowAllEnforcer


_CLAIMS = {"tokens": ["search-user"], "roles": ["search-role"]}

_NOT_DELETED_FILTER = {
    "databaseId": {
        "AttributeValueList": [{"S": "#deleted"}],
        "ComparisonOperator": "NOT_CONTAINS",
    }
}


def _db_row(database_id):
    """A database row as DynamoDB returns it from a scan (attribute-value encoded)."""
    return {
        "databaseId": {"S": database_id},
        "description": {"S": "a database"},
        "dateCreated": {"S": "2026-01-01T00:00:00Z"},
    }


def _page(*database_ids):
    """One page as the scan paginator yields it: the rows that survived the filter out of the 100
    the page evaluated. No ids is a page whose evaluated rows were all ``#deleted`` tombstones."""
    return {
        "Items": [_db_row(database_id) for database_id in database_ids],
        "Count": len(database_ids),
        "ScannedCount": 100,
    }


def _stub_scan(search_module, pages):
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    search_module.dynamodb_client.get_paginator.return_value = paginator
    return paginator


@pytest.mark.unit
class TestEmptyFilteredPages:
    """An empty filtered page is one page of the scan, not the end of it."""

    def test_rows_on_a_single_page_are_returned(self, search_module, allow_all):
        """Control: the stubbed scan reaches the enforcement point and the rows come back."""
        _stub_scan(search_module, [_page("db-a", "db-b")])
        result = search_module.DatabaseAccessManager.get_accessible_databases(_CLAIMS)
        assert result == ["db-a", "db-b"]
        assert [d["databaseId"] for d in allow_all.documents] == ["db-a", "db-b"]

    def test_an_empty_first_page_does_not_end_the_scan(self, search_module, allow_all):
        _stub_scan(search_module, [_page(), _page("db-a")])
        assert search_module.DatabaseAccessManager.get_accessible_databases(_CLAIMS) == ["db-a"]

    def test_an_empty_middle_page_does_not_end_the_scan(self, search_module, allow_all):
        _stub_scan(search_module, [_page("db-a"), _page(), _page("db-b")])
        assert search_module.DatabaseAccessManager.get_accessible_databases(_CLAIMS) == [
            "db-a",
            "db-b",
        ]

    def test_consecutive_empty_pages_are_all_followed(self, search_module, allow_all):
        _stub_scan(search_module, [_page(), _page(), _page(), _page("db-a")])
        assert search_module.DatabaseAccessManager.get_accessible_databases(_CLAIMS) == ["db-a"]

    def test_a_scan_of_only_empty_pages_returns_no_databases(self, search_module, allow_all):
        """Control: with no live row anywhere the result is empty and Casbin is never consulted."""
        _stub_scan(search_module, [_page(), _page()])
        assert search_module.DatabaseAccessManager.get_accessible_databases(_CLAIMS) == []
        assert allow_all.documents == []

    def test_the_accessible_limit_still_ends_the_scan(self, search_module, allow_all):
        """Control: ``max_databases`` still bounds the loop, so the rows after the limit are not
        evaluated."""
        _stub_scan(search_module, [_page("db-a", "db-b"), _page(), _page("db-c")])
        result = search_module.DatabaseAccessManager.get_accessible_databases(
            _CLAIMS, max_databases=2
        )
        assert result == ["db-a", "db-b"]
        assert [d["databaseId"] for d in allow_all.documents] == ["db-a", "db-b"]

    def test_the_scan_request_is_the_filtered_paginated_scan(self, search_module, allow_all):
        """Control: the paginator, not the loop, owns termination -- ``PageSize`` bounds each request
        and ``MaxItems`` bounds the rows the scan returns."""
        paginator = _stub_scan(search_module, [_page("db-a")])
        search_module.DatabaseAccessManager.get_accessible_databases(_CLAIMS)
        search_module.dynamodb_client.get_paginator.assert_any_call("scan")
        paginator.paginate.assert_any_call(
            TableName=search_module.database_storage_table_name,
            ScanFilter=_NOT_DELETED_FILTER,
            PaginationConfig={"PageSize": 100, "MaxItems": 10000},
        )


@pytest.mark.unit
class TestRealScanPaginator:
    """The same loop driven by a real botocore ``scan`` paginator, answering from ``Stubber``."""

    @staticmethod
    def _client():
        return boto3.client(
            "dynamodb",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )

    @staticmethod
    def _request(search_module, **extra):
        request = {
            "TableName": search_module.database_storage_table_name,
            "ScanFilter": _NOT_DELETED_FILTER,
            "Limit": 100,
        }
        request.update(extra)
        return request

    def test_an_empty_filtered_page_is_followed_to_the_next_page(self, search_module, allow_all):
        client = self._client()
        tombstone_key = {"databaseId": {"S": "retired-db#deleted"}}
        with Stubber(client) as stubber:
            stubber.add_response(
                "scan",
                {"Items": [], "Count": 0, "ScannedCount": 100, "LastEvaluatedKey": tombstone_key},
                self._request(search_module),
            )
            stubber.add_response(
                "scan",
                {"Items": [_db_row("db-a")], "Count": 1, "ScannedCount": 1},
                self._request(search_module, ExclusiveStartKey=tombstone_key),
            )
            search_module.dynamodb_client = client
            result = search_module.DatabaseAccessManager.get_accessible_databases(_CLAIMS)
            assert result == ["db-a"]
            stubber.assert_no_pending_responses()

    def test_a_page_without_a_next_key_ends_the_scan(self, search_module, allow_all):
        """Control: the scan stops at the page that carries no ``LastEvaluatedKey``. A further
        ``scan`` call would find no stubbed response, raise inside the function and come back as
        ``[]``."""
        client = self._client()
        with Stubber(client) as stubber:
            stubber.add_response(
                "scan",
                {"Items": [_db_row("db-a")], "Count": 1, "ScannedCount": 1},
                self._request(search_module),
            )
            search_module.dynamodb_client = client
            result = search_module.DatabaseAccessManager.get_accessible_databases(_CLAIMS)
            assert result == ["db-a"]
            stubber.assert_no_pending_responses()
