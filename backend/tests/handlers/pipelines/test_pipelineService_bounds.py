# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Response bounds on the pipeline list and detail paths.

Two independent ceilings, both there to keep a response under the 6 MB Lambda synchronous-response
limit (backend Rule 15):

  - LIST: `maxItems`/`pageSize` are clamped to MAX_LIST_PAGE_ITEMS. MaxItems is what the boto3
    paginator accumulates into one response, so it is the value that has to be capped — PageSize is
    only DynamoDB's per-request size and bounds nothing on its own. Each returned pipeline also costs
    one COUNT query for its templateCount, so the clamp bounds the query fan-out too.
  - DETAIL: the inline `templates` list is capped at MAX_DETAIL_TEMPLATES, with the full set reachable
    through the paginated template endpoint.

The detail cap has a trap worth its own coverage: `templateCount` must come from the COUNT query, not
from `len(templates)`. Capping the list while counting it would make a pipeline with more templates
than the cap silently report the cap as its total, leaving a caller no way to know more exist.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.pipelines import pipelineService as ps
from backend.backend.handlers.pipelines.pipelineService import lambda_handler

MOD = "backend.backend.handlers.pipelines.pipelineService"


def _event(method, path, path_params=None, query=None):
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": path_params,
        "queryStringParameters": query,
        "headers": {"authorization": "Bearer test-token"},
        "body": None,
    }


def _enforcer():
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = True
    return inst


def _template_items(count, start=0):
    """`count` stored template rows, as the templates table would return them."""
    return [{"templateId": f"t{i}", "templateName": f"Template {i}",
             "configFormat": "json", "allowCustomEdit": True}
            for i in range(start, start + count)]


@pytest.mark.unit
class TestListPaginationClamp:
    """F3 — maxItems/pageSize clamped to MAX_LIST_PAGE_ITEMS."""

    def test_oversized_maxitems_is_clamped(self):
        cfg = ps._pagination_config(
            {"maxItems": "100000", "pageSize": "100000", "startingToken": None})
        assert cfg["MaxItems"] == ps.MAX_LIST_PAGE_ITEMS
        assert cfg["PageSize"] == ps.MAX_LIST_PAGE_ITEMS

    def test_under_the_cap_is_passed_through(self):
        cfg = ps._pagination_config({"maxItems": "25", "pageSize": "10", "startingToken": None})
        assert cfg["MaxItems"] == 25
        assert cfg["PageSize"] == 10

    def test_pagesize_never_exceeds_maxitems(self):
        # A PageSize above MaxItems would have DynamoDB read more rows than the response can return.
        cfg = ps._pagination_config({"maxItems": "5", "pageSize": "500", "startingToken": None})
        assert cfg["MaxItems"] == 5
        assert cfg["PageSize"] == 5

    def test_starting_token_is_preserved(self):
        cfg = ps._pagination_config(
            {"maxItems": "10", "pageSize": "10", "startingToken": "tok"})
        assert cfg["StartingToken"] == "tok"

    def test_cap_matches_the_workflow_service_ceiling(self):
        # The pipeline and workflow list endpoints are paged by the same clients, so a divergent
        # ceiling would make identical requests behave differently per entity type. Read from the
        # source text rather than importing workflowService, which pulls in handler-only imports that
        # do not resolve under this test's sys.path.
        import pathlib
        import re
        source = (pathlib.Path(__file__).parents[3]
                  / "backend" / "handlers" / "workflows" / "workflowService.py").read_text()
        match = re.search(r"^MAX_LIST_PAGE_ITEMS\s*=\s*(\d+)", source, re.MULTILINE)
        assert match, "workflowService.MAX_LIST_PAGE_ITEMS not found"
        assert ps.MAX_LIST_PAGE_ITEMS == int(match.group(1))

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}.dynamodb")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_clamp_reaches_the_paginator_on_a_real_list_request(
            self, mock_enforcer, mock_claims, mock_dynamodb, mock_templates_table):
        # Asserting on the paginator kwargs, not just the helper: the clamp is only worth anything if
        # the request path actually routes through it.
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        paginator = MagicMock()
        paginator.paginate.return_value.build_full_result.return_value = {"Items": []}
        mock_dynamodb.meta.client.get_paginator.return_value = paginator
        mock_templates_table.return_value = MagicMock()

        event = _event("GET", "/pipelines")
        event["queryStringParameters"] = {
            "maxItems": "999999", "pageSize": "999999", "startingToken": None}
        resp = lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        cfg = paginator.paginate.call_args.kwargs["PaginationConfig"]
        assert cfg["MaxItems"] == ps.MAX_LIST_PAGE_ITEMS
        assert cfg["PageSize"] == ps.MAX_LIST_PAGE_ITEMS


@pytest.mark.unit
class TestDetailTemplatesBounded:
    """F12 — get_pipeline_templates is capped instead of reading every page."""

    @patch(f"{MOD}._templates_table")
    def test_returns_at_most_the_cap(self, mock_templates_table):
        table = MagicMock()
        table.query.return_value = {"Items": _template_items(50)}
        mock_templates_table.return_value = table
        out = ps.get_pipeline_templates("db1", "pipe1")
        assert len(out) == ps.MAX_DETAIL_TEMPLATES

    @patch(f"{MOD}._templates_table")
    def test_query_carries_a_dynamodb_limit(self, mock_templates_table):
        # Without Limit the query would read (and pay for) every row before the slice.
        table = MagicMock()
        table.query.return_value = {"Items": _template_items(3)}
        mock_templates_table.return_value = table
        ps.get_pipeline_templates("db1", "pipe1")
        assert table.query.call_args.kwargs["Limit"] == ps.MAX_DETAIL_TEMPLATES

    @patch(f"{MOD}._templates_table")
    def test_stops_paging_once_the_cap_is_reached(self, mock_templates_table):
        # A page that already fills the cap must not trigger another request even though the table
        # reports more rows behind a LastEvaluatedKey.
        #
        # The side_effect list is FINITE on purpose. An always-LastEvaluatedKey mock would make a
        # regression here loop forever — the test would hang instead of failing, which is useless as a
        # guard. With a finite list an unbounded read raises StopIteration on the second call, so the
        # regression surfaces as a fast, legible failure.
        table = MagicMock()
        table.query.side_effect = [
            {"Items": _template_items(ps.MAX_DETAIL_TEMPLATES), "LastEvaluatedKey": {"k": "v"}},
        ]
        mock_templates_table.return_value = table
        out = ps.get_pipeline_templates("db1", "pipe1")
        assert len(out) == ps.MAX_DETAIL_TEMPLATES
        assert table.query.call_count == 1

    @patch(f"{MOD}._templates_table")
    def test_fewer_than_the_cap_returns_everything(self, mock_templates_table):
        table = MagicMock()
        table.query.return_value = {"Items": _template_items(3)}
        mock_templates_table.return_value = table
        out = ps.get_pipeline_templates("db1", "pipe1")
        assert [t["templateId"] for t in out] == ["t0", "t1", "t2"]

    @patch(f"{MOD}._templates_table")
    def test_short_pages_are_followed_until_the_cap(self, mock_templates_table):
        # DynamoDB may return fewer items than Limit and still carry a LastEvaluatedKey.
        table = MagicMock()
        table.query.side_effect = [
            {"Items": _template_items(4), "LastEvaluatedKey": {"k": 1}},
            {"Items": _template_items(4, start=4), "LastEvaluatedKey": {"k": 2}},
            {"Items": _template_items(4, start=8)},
        ]
        mock_templates_table.return_value = table
        out = ps.get_pipeline_templates("db1", "pipe1")
        assert len(out) == ps.MAX_DETAIL_TEMPLATES
        assert [t["templateId"] for t in out][:3] == ["t0", "t1", "t2"]

    @patch(f"{MOD}._templates_table")
    def test_descriptors_carry_no_config_body(self, mock_templates_table):
        # Bodies are what make this response large; the paginated template endpoint returns them.
        table = MagicMock()
        table.query.return_value = {
            "Items": [{"templateId": "t0", "templateName": "T", "configFormat": "json",
                       "allowCustomEdit": True, "configBody": "x" * 5000}]}
        mock_templates_table.return_value = table
        out = ps.get_pipeline_templates("db1", "pipe1")
        assert "configBody" not in out[0]


@pytest.mark.unit
class TestTemplateCountIsNotTheCappedLength:
    """THE TRAP — templateCount must survive the inline cap.

    A pipeline with more templates than MAX_DETAIL_TEMPLATES must still report its TRUE total. If the
    count is taken from the capped list, the total silently pins to the cap and a caller cannot tell
    that more templates exist.
    """

    @staticmethod
    def _run_detail(count_total, stored_templates):
        """GET one pipeline's details with `stored_templates` rows and a COUNT of `count_total`."""
        with patch(f"{MOD}.CasbinEnforcer") as mock_enforcer, \
                patch(f"{MOD}.request_to_claims") as mock_claims, \
                patch(f"{MOD}._pipeline_table") as mock_pipeline_table, \
                patch(f"{MOD}._templates_table") as mock_templates_table:
            mock_claims.return_value = {"tokens": ["user1"]}
            mock_enforcer.return_value = _enforcer()
            pipeline_table = MagicMock()
            pipeline_table.get_item.return_value = {"Item": {
                "databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "P",
                "enabled": True, "archived": False}}
            mock_pipeline_table.return_value = pipeline_table

            templates_table = MagicMock()

            def _query(**kwargs):
                # The COUNT query and the descriptor query hit the same table; Select tells them apart.
                if kwargs.get("Select") == "COUNT":
                    return {"Count": count_total}
                return {"Items": stored_templates}

            templates_table.query.side_effect = _query
            mock_templates_table.return_value = templates_table

            resp = lambda_handler(
                _event("GET", "/database/db1/pipelines/pipe1",
                       {"databaseId": "db1", "pipelineId": "pipe1"}), MagicMock())
            assert resp["statusCode"] == 200
            return json.loads(resp["body"])["message"]

    def test_true_total_reported_when_templates_exceed_the_cap(self):
        data = self._run_detail(count_total=137, stored_templates=_template_items(50))
        # The inline list is capped...
        assert len(data["templates"]) == ps.MAX_DETAIL_TEMPLATES
        # ...but the count is the real total, NOT the cap and NOT the inline length.
        assert data["templateCount"] == 137
        assert data["templateCount"] != ps.MAX_DETAIL_TEMPLATES
        assert data["templateCount"] != len(data["templates"])

    def test_count_matches_length_when_under_the_cap(self):
        data = self._run_detail(count_total=3, stored_templates=_template_items(3))
        assert len(data["templates"]) == 3
        assert data["templateCount"] == 3

    def test_zero_templates(self):
        data = self._run_detail(count_total=0, stored_templates=[])
        assert data["templates"] == []
        assert data["templateCount"] == 0

    def test_exactly_at_the_cap_is_not_reported_as_truncated(self):
        n = ps.MAX_DETAIL_TEMPLATES
        data = self._run_detail(count_total=n, stored_templates=_template_items(n))
        assert data["templateCount"] == n
        assert len(data["templates"]) == n
