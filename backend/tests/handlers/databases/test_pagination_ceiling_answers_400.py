# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An over-ceiling page size on GET /database and GET /buckets is answered 400, before the read.

The ceilings live on the request models, and the models are constructed inside `get_databases` /
`get_buckets` rather than in the route handler. Which status code the caller sees therefore depends
on which `except` clause the model's ValidationError lands in: the enclosing catch-all re-raises it
as a `VAMSGeneralErrorResponse`, which the route handler maps to a 400 — but the route handler's own
catch-all maps a bare exception to a 500. A bound wired one frame further out answers 500 for a
caller mistake, which no model-level assertion can see.

The other half of the same guarantee is that the rejection happens before the table is read; the
point of the ceiling is the work not done, and a listing that scanned first and validated afterwards
would satisfy a status-code assertion on its own.

`validate_pagination_info` runs ahead of the model and is patched here to the deployed helper, since
`tests/conftest.py` replaces `common.dynamodb` with a bare MagicMock. That matters in both
directions: the helper clamps pageSize down to maxItems, so it is the helper — not the model — that
decides which value reaches the ceiling, and it applies no ceiling of its own.
"""

import importlib.util
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.databases.databaseService import lambda_handler

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]


def _load_deployed_pagination_helper():
    """`common.dynamodb.validate_pagination_info` as deployed, loaded from its source file."""
    source = REPO_ROOT / "backend" / "backend" / "common" / "dynamodb.py"
    spec = importlib.util.spec_from_file_location("_deployed_common_dynamodb_ceilings", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_pagination_info


DEPLOYED_VALIDATE_PAGINATION_INFO = _load_deployed_pagination_helper()

# One row per listing, in the low-level typed shape each listing deserializes itself. No
# defaultBucketId on the database row: that would send the listing into a second table.
DATABASE_ROW = {"databaseId": {"S": "building-scans"}, "description": {"S": "A test database"}}
BUCKET_ROW = {
    "bucketId": {"S": "b9a3aba3-c092-475f-978a-d39e5d5a2657"},
    "bucketName": {"S": "vams-created-asset-bucket"},
    "baseAssetsPrefix": {"S": "/"},
}

LISTINGS = [("/database", DATABASE_ROW), ("/buckets", BUCKET_ROW)]
LISTING_PATHS = [path for path, _ in LISTINGS]


def _rest_event(path, query=None):
    """A REST API (v1) proxy event, as API Gateway delivers it."""
    return {
        "httpMethod": "GET",
        "path": path,
        "requestContext": {
            "requestId": "test-request-id",
            "identity": {"sourceIp": "10.0.0.1"},
            "authorizer": {
                "principalId": "test-user",
                "vams:tokens": json.dumps(["test-user"]),
            },
        },
        "queryStringParameters": query,
        "pathParameters": None,
        "headers": {"Authorization": "Bearer test-token"},
    }


def _allowing_enforcer(mock_casbin):
    """Casbin allowing both tiers, so the pagination bound is the only thing that can answer."""
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    enforcer.enforce.return_value = True
    mock_casbin.return_value = enforcer
    return enforcer


def _ceilings():
    import models.databases as d
    return d.MAX_LIST_PAGE_SIZE, d.MAX_LIST_MAX_ITEMS


@pytest.mark.unit
@patch(
    "backend.backend.handlers.databases.databaseService.validate_pagination_info",
    DEPLOYED_VALIDATE_PAGINATION_INFO,
)
class TestPaginationCeilingIsAnsweredAsCallerError:
    @pytest.mark.parametrize("path", LISTING_PATHS)
    @patch("backend.backend.handlers.databases.databaseService.dbClient")
    @patch("backend.backend.handlers.databases.databaseService.CasbinEnforcer")
    def test_page_size_over_the_ceiling_answers_400_and_never_reads(self, mock_casbin, mock_db, path):
        _allowing_enforcer(mock_casbin)
        page_size_ceiling, _ = _ceilings()

        response = lambda_handler(
            _rest_event(path, {"pageSize": str(page_size_ceiling + 1)}), MagicMock()
        )

        assert response["statusCode"] == 400
        mock_db.scan.assert_not_called()

    @pytest.mark.parametrize("path", LISTING_PATHS)
    @patch("backend.backend.handlers.databases.databaseService.dbClient")
    @patch("backend.backend.handlers.databases.databaseService.CasbinEnforcer")
    def test_max_items_over_the_ceiling_answers_400_and_never_reads(self, mock_casbin, mock_db, path):
        _allowing_enforcer(mock_casbin)
        _, max_items_ceiling = _ceilings()

        response = lambda_handler(
            _rest_event(path, {"maxItems": str(max_items_ceiling + 1)}), MagicMock()
        )

        assert response["statusCode"] == 400
        mock_db.scan.assert_not_called()

    @pytest.mark.parametrize("path", LISTING_PATHS)
    @patch("backend.backend.handlers.databases.databaseService.dbClient")
    @patch("backend.backend.handlers.databases.databaseService.CasbinEnforcer")
    def test_the_reproduction_value_from_the_report_answers_400(self, mock_casbin, mock_db, path):
        # The value the finding used, as a query string rather than as a model keyword.
        _allowing_enforcer(mock_casbin)

        response = lambda_handler(
            _rest_event(path, {"pageSize": "1000000000", "maxItems": "1000000000"}), MagicMock()
        )

        assert response["statusCode"] == 400
        mock_db.scan.assert_not_called()


@pytest.mark.unit
@patch(
    "backend.backend.handlers.databases.databaseService.validate_pagination_info",
    DEPLOYED_VALIDATE_PAGINATION_INFO,
)
class TestPaginationInsideTheCeilingStillServes:
    """Positive controls: the 400s above are the ceiling answering, not the listing failing."""

    @pytest.mark.parametrize("path,row", LISTINGS)
    @patch("backend.backend.handlers.databases.databaseService.dbClient")
    @patch("backend.backend.handlers.databases.databaseService.CasbinEnforcer")
    def test_a_page_size_inside_the_ceiling_reads_the_table_and_serves_200(
        self, mock_casbin, mock_db, path, row
    ):
        _allowing_enforcer(mock_casbin)
        mock_db.scan.return_value = {"Items": [row]}

        response = lambda_handler(_rest_event(path, {"pageSize": "250"}), MagicMock())

        assert response["statusCode"] == 200
        assert len(json.loads(response["body"])["Items"]) == 1
        assert mock_db.scan.call_args.kwargs["Limit"] == 250

    @pytest.mark.parametrize("path,row", LISTINGS)
    @patch("backend.backend.handlers.databases.databaseService.dbClient")
    @patch("backend.backend.handlers.databases.databaseService.CasbinEnforcer")
    def test_a_page_size_exactly_at_the_ceiling_is_accepted(self, mock_casbin, mock_db, path, row):
        # The database listing is handed this value by the shared helper whenever the caller asks
        # for no page size, so the boundary has to be inclusive.
        _allowing_enforcer(mock_casbin)
        mock_db.scan.return_value = {"Items": [row]}
        page_size_ceiling, _ = _ceilings()

        response = lambda_handler(
            _rest_event(path, {"pageSize": str(page_size_ceiling)}), MagicMock()
        )

        assert response["statusCode"] == 200
        assert mock_db.scan.call_args.kwargs["Limit"] == page_size_ceiling

    @pytest.mark.parametrize("path,row", LISTINGS)
    @patch("backend.backend.handlers.databases.databaseService.dbClient")
    @patch("backend.backend.handlers.databases.databaseService.CasbinEnforcer")
    def test_a_request_carrying_no_pagination_at_all_is_accepted(
        self, mock_casbin, mock_db, path, row
    ):
        # The web application's own listing call: no query string at all, so whatever the shared
        # helper seeds has to sit inside the ceilings.
        _allowing_enforcer(mock_casbin)
        mock_db.scan.return_value = {"Items": [row]}

        response = lambda_handler(_rest_event(path), MagicMock())

        assert response["statusCode"] == 200
        assert mock_db.scan.called, 'the listing never read the table'
        assert mock_db.scan.call_count <= 1, 'the bounded listing read more than one page'


@pytest.mark.unit
class TestTheSharedHelperAppliesNoCeilingOfItsOwn:
    """Why the model bound is load-bearing: nothing upstream of it clamps an oversized value."""

    def test_the_helper_passes_an_oversized_page_size_through_unchanged(self):
        page_size_ceiling, max_items_ceiling = _ceilings()
        params = {"pageSize": str(page_size_ceiling + 1)}

        DEPLOYED_VALIDATE_PAGINATION_INFO(params)

        assert int(params["pageSize"]) == page_size_ceiling + 1
        # It also copies the oversized value onto an absent maxItems, so the two parameters cannot
        # be assumed independent when reasoning about which ceiling fires.
        assert int(params["maxItems"]) == page_size_ceiling + 1

    def test_the_helper_clamps_page_size_down_to_a_smaller_max_items(self):
        # The paired control for the test above: the helper does modify these values, so its
        # pass-through of an oversized page size is a missing ceiling and not a no-op stub.
        params = {"pageSize": "5000", "maxItems": "10"}

        DEPLOYED_VALIDATE_PAGINATION_INFO(params)

        assert int(params["pageSize"]) == 10
