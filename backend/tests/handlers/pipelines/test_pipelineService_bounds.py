# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Response bounds on the pipeline list and detail paths.

Three independent ceilings, all there to keep a response under the 6 MB Lambda synchronous-response
limit (backend Rule 15):

  - LIST ROWS: `maxItems`/`pageSize` are clamped to MAX_LIST_PAGE_ITEMS. MaxItems is what the boto3
    paginator accumulates into one response, so it is the value that has to be capped — PageSize is
    only DynamoDB's per-request size and bounds nothing on its own. Each returned pipeline also costs
    one COUNT query for its templateCount, so the clamp bounds the query fan-out too.
  - LIST BYTES: the row cap bounds the COUNT of items, not their SIZE. Each row carries its
    executionConfig verbatim and one executionConfig is accepted up to MAX_EXECUTION_CONFIG_BYTES (a
    DeadlineCloud block may hold a 256 KB inline OpenJD template), so ~20-30 such rows exceed 6 MB
    while sitting far inside the row cap. `_filtered_page` stops accumulating at MAX_LIST_PAGE_BYTES
    and hands back a cursor resuming after the last row it kept.
  - DETAIL: the inline `templates` list is capped at MAX_DETAIL_TEMPLATES, with the full set reachable
    through the paginated template endpoint.

The byte budget has to do both halves or it is not a bound: breaching 6 MB fails the request with a
502 carrying no body and no NextToken, so a page trimmed without a cursor makes the withheld rows
unreachable rather than deferred — the same outcome by a quieter route.

The detail cap has a trap worth its own coverage: `templateCount` must come from the COUNT query, not
from `len(templates)`. Capping the list while counting it would make a pipeline with more templates
than the cap silently report the cap as its total, leaving a caller no way to know more exist.
"""

import json
import math
import pathlib
import re
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.paginate import TokenDecoder

from backend.backend.handlers.pipelines import pipelineService as ps
from backend.backend.handlers.pipelines.pipelineService import lambda_handler

MOD = "backend.backend.handlers.pipelines.pipelineService"

LAMBDA_RESPONSE_LIMIT_BYTES = 6 * 1024 * 1024

_CLAIMS = {"tokens": ["user1"], "roles": []}


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


def _pipeline_row(pipeline_id, template_chars=0):
    """One stored pipeline row as a list query returns it. `template_chars` sizes the inline OpenJD
    job template a DeadlineCloud executionConfig may legitimately carry (up to 256 KB), which is what
    makes a page of rows large while the row COUNT stays small."""
    return {
        "databaseId": "db1",
        "pipelineId": pipeline_id,
        "pipelineName": pipeline_id,
        "category": "conversion",
        "enabled": True,
        "archived": False,
        "allListPartition": ps.pr.ALL_PIPELINES_LIST_PARTITION,
        "dateModified": "2026-01-01T00:00:00Z",
        "schemaVersion": 1,
        "executionConfig": {
            "executionType": "DeadlineCloud",
            "waitForCallback": "Enabled",
            "deadlineCloud": {"farmId": "farm-abc", "queueId": "queue-abc",
                              "template": "x" * template_chars},
        },
        "systemConfig": {},
    }


def _templates_table_stub(count=2):
    """Templates table whose COUNT query answers `count`, so one call is made per row KEPT."""
    table = MagicMock()
    table.query.return_value = {"Count": count}
    return table


def _list_page(which, page, query=None, templates_table=None):
    """Run one of the two list reads against a stubbed paginator serving `page`, and return the
    response model plus the templates-table stub the per-row COUNT query went through."""
    query = query or {"maxItems": "500", "pageSize": "500", "startingToken": None}
    table = templates_table if templates_table is not None else _templates_table_stub()
    paginator = MagicMock()
    paginator.paginate.return_value.build_full_result.return_value = page
    with patch(f"{MOD}.dynamodb") as mock_dynamodb, \
            patch(f"{MOD}.CasbinEnforcer") as mock_enforcer, \
            patch(f"{MOD}._templates_table", return_value=table):
        mock_dynamodb.meta.client.get_paginator.return_value = paginator
        mock_enforcer.return_value = _enforcer()
        if which == "get_all_pipelines":
            result = ps.get_all_pipelines(query, False, _CLAIMS)
        else:
            result = ps.get_database_pipelines("db1", query, False, _CLAIMS)
    return result, table


def _list_request(page, query=None):
    """GET /pipelines end-to-end through lambda_handler against a stubbed paginator serving `page`."""
    paginator = MagicMock()
    paginator.paginate.return_value.build_full_result.return_value = page
    with patch(f"{MOD}.dynamodb") as mock_dynamodb, \
            patch(f"{MOD}.request_to_claims") as mock_claims, \
            patch(f"{MOD}.CasbinEnforcer") as mock_enforcer, \
            patch(f"{MOD}._templates_table", return_value=_templates_table_stub()):
        mock_dynamodb.meta.client.get_paginator.return_value = paginator
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        event = _event("GET", "/pipelines")
        event["queryStringParameters"] = query
        return lambda_handler(event, MagicMock())


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


@pytest.mark.unit
class TestListPageByteBudget:
    """The row cap bounds how MANY pipelines a page returns, never how many BYTES.

    A row's executionConfig is returned verbatim and is accepted up to MAX_EXECUTION_CONFIG_BYTES, so
    a render-farm deployment carrying inline OpenJD templates breaches the 6 MB Lambda limit at
    ~20-30 rows — an order of magnitude inside MAX_LIST_PAGE_ITEMS. The config stays in the listing
    (the web pipelines page, `vamscli pipeline list` and the MCP tools all read it); what bounds the
    response is MAX_LIST_PAGE_BYTES plus a cursor for the remainder.
    """

    def test_thirty_fat_pipelines_stay_inside_the_lambda_response_limit(self):
        # 30 DeadlineCloud pipelines each carrying a 256 KB inline template, requested with no query
        # parameters, serializes to ~7.9 MB.
        page = {"Items": [_pipeline_row(f"pipe{i}", template_chars=256 * 1024) for i in range(30)]}

        resp = _list_request(page)

        assert resp["statusCode"] == 200
        assert len(resp["body"]) < LAMBDA_RESPONSE_LIMIT_BYTES, (
            f"the list body is {len(resp['body'])} bytes, over the {LAMBDA_RESPONSE_LIMIT_BYTES}-byte "
            "Lambda synchronous-response limit — the invocation fails with a 502 and no body")
        data = json.loads(resp["body"])["message"]
        assert 0 < len(data["Items"]) < 30, data.keys()
        # The withheld rows are deferred, not lost: the caller reaches them through the cursor.
        assert data["NextToken"], "a trimmed page returned no cursor, so the remaining rows are lost"
        # The config the owner ruling keeps in the listing is still there on the rows returned.
        assert data["Items"][0]["executionConfig"]["executionType"] == "DeadlineCloud"

    def test_an_ordinary_page_returns_every_row_with_no_cursor(self):
        """Positive control: the budget must not trim a normal listing. The shipped executionConfigs
        are ~600 bytes, so 30 of them are nowhere near the budget."""
        page = {"Items": [_pipeline_row(f"pipe{i}", template_chars=400) for i in range(30)]}

        resp = _list_request(page)

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])["message"]
        assert len(data["Items"]) == 30
        # Nothing was withheld, so nothing needs a cursor — an unset NextToken is what stops a caller.
        assert data.get("NextToken") in (None, "")

    def test_a_trimmed_page_resumes_after_the_last_row_kept(self):
        # A budget of one byte overflows on the second row whatever a pipeline row weighs, so this
        # does not depend on the size of the fixture.
        page = {"Items": [_pipeline_row("pipe1"), _pipeline_row("pipe2"), _pipeline_row("pipe3")]}

        with patch.object(ps, "MAX_LIST_PAGE_BYTES", 1):
            result, _ = _list_page("get_all_pipelines", page)

        assert [i.pipelineId for i in result.Items] == ["pipe1"]
        assert result.NextToken, "pipe2/pipe3 are unreachable without a cursor"
        # Decoded through botocore, which is the contract the caller relies on when passing the token
        # back as startingToken: it resumes after the last row KEPT, not at the query's own end.
        resumed = TokenDecoder().decode(result.NextToken)["ExclusiveStartKey"]
        assert resumed["pipelineId"] == "pipe1", resumed
        # A by-date-GSI cursor names the index keys as well as the table's, or DynamoDB rejects it.
        assert {"databaseId", "pipelineId", "allListPartition", "dateModified"} <= set(resumed)

    def test_the_trimmed_cursor_is_accepted_back_as_a_starting_token(self):
        """The link the decode above only assumes: the cursor is handed back as `startingToken` and a
        REAL boto3 paginator turns it into the DynamoDB `ExclusiveStartKey` for the row after the last
        one kept. Decoding the token proves it is well formed, not that the paginator it is fed to
        accepts it — a token the paginator rejected, or silently ignored and served page one for, would
        leave the withheld rows unreachable with nothing failing.
        """
        page = {"Items": [_pipeline_row("pipe1"), _pipeline_row("pipe2")]}
        with patch.object(ps, "MAX_LIST_PAGE_BYTES", 1):
            trimmed, _ = _list_page("get_all_pipelines", page)
        assert trimmed.NextToken

        sent = []

        def _record(operation, kwargs):
            sent.append(kwargs)
            return {"Items": [], "Count": 0, "ScannedCount": 0}

        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.meta.client._make_api_call = _record
        query = {"maxItems": "500", "pageSize": "500", "startingToken": trimmed.NextToken}
        with patch(f"{MOD}.dynamodb", resource), \
                patch(f"{MOD}.CasbinEnforcer") as mock_enforcer, \
                patch(f"{MOD}._templates_table", return_value=_templates_table_stub()):
            mock_enforcer.return_value = _enforcer()
            ps.get_all_pipelines(query, False, _CLAIMS)

        assert sent, "the resumed listing issued no query at all"
        assert sent[0].get("ExclusiveStartKey") == {
            "databaseId": "db1", "pipelineId": "pipe1",
            "allListPartition": ps.pr.ALL_PIPELINES_LIST_PARTITION,
            "dateModified": "2026-01-01T00:00:00Z"}, sent[0]

    def test_a_database_scoped_cursor_names_only_the_table_keys(self):
        # The database listing queries the base table, whose key is databaseId + pipelineId. Adding
        # index keys to that cursor would have DynamoDB reject the continuation.
        page = {"Items": [_pipeline_row("pipe1"), _pipeline_row("pipe2")]}

        with patch.object(ps, "MAX_LIST_PAGE_BYTES", 1):
            result, _ = _list_page("get_database_pipelines", page)

        resumed = TokenDecoder().decode(result.NextToken)["ExclusiveStartKey"]
        assert resumed == {"databaseId": "db1", "pipelineId": "pipe1"}, resumed

    def test_the_first_row_is_always_kept(self):
        """A single row over the budget still comes back: an empty page reads as "no pipelines", and
        the caller cannot page past it either."""
        page = {"Items": [_pipeline_row("pipe1", template_chars=256 * 1024)]}

        with patch.object(ps, "MAX_LIST_PAGE_BYTES", 1):
            result, _ = _list_page("get_all_pipelines", page)

        assert [i.pipelineId for i in result.Items] == ["pipe1"]
        # Nothing was withheld, so the page needs no cursor of its own.
        assert result.NextToken in (None, "")

    def test_a_row_missing_its_keys_yields_no_cursor_rather_than_a_wrong_one(self):
        page = {"Items": [{"pipelineName": "keyless"}, _pipeline_row("pipe2")]}

        with patch.object(ps, "MAX_LIST_PAGE_BYTES", 1):
            result, _ = _list_page("get_all_pipelines", page)

        assert len(result.Items) == 1
        assert result.NextToken in (None, "")

    def test_a_trimmed_row_costs_no_template_count_query(self):
        """The trim happens before the per-row COUNT query, so a row withheld from the page is not
        paid for — the fan-out shrinks with the page rather than tracking what was read."""
        page = {"Items": [_pipeline_row(f"pipe{i}") for i in range(5)]}
        table = _templates_table_stub()

        with patch.object(ps, "MAX_LIST_PAGE_BYTES", 1):
            result, table = _list_page("get_all_pipelines", page, templates_table=table)

        assert len(result.Items) == 1
        # One COUNT query for the row kept, one for the row that overflowed the budget (it has to be
        # built to be measured) — not five.
        assert table.query.call_count <= 2, table.query.call_args_list

    def test_the_paginators_own_token_is_still_passed_through(self):
        """An untrimmed page that the paginator itself stopped short of keeps the paginator's cursor —
        the byte budget must not shadow it."""
        page = {"Items": [_pipeline_row("pipe1")], "NextToken": "paginator-token"}

        result, _ = _list_page("get_all_pipelines", page)

        assert [i.pipelineId for i in result.Items] == ["pipe1"]
        assert result.NextToken == "paginator-token"

    def test_archived_and_unauthorized_rows_are_still_filtered_out(self):
        """The filters that precede the budget still apply: archived rows are dropped unless asked
        for, and a Tier-2 denial keeps a row out of the page."""
        rows = [_pipeline_row("visible"), _pipeline_row("archived"), _pipeline_row("denied")]
        rows[1]["archived"] = True
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        enforcer.enforce.side_effect = lambda obj, action: obj.get("pipelineId") != "denied"
        paginator = MagicMock()
        paginator.paginate.return_value.build_full_result.return_value = {"Items": rows}

        with patch(f"{MOD}.dynamodb") as mock_dynamodb, \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
                patch(f"{MOD}._templates_table", return_value=_templates_table_stub()):
            mock_dynamodb.meta.client.get_paginator.return_value = paginator
            result = ps.get_all_pipelines(
                {"maxItems": "500", "pageSize": "500", "startingToken": None}, False, _CLAIMS)

        assert [i.pipelineId for i in result.Items] == ["visible"]
        # Every row read was filtered rather than withheld, so no byte-budget cursor is minted.
        assert result.NextToken in (None, "")

    def test_the_byte_budget_matches_the_workflow_service_ceiling(self):
        # The pipeline and workflow list endpoints are paged by the same clients, so a divergent
        # ceiling would make identical requests behave differently per entity type. Read from the
        # source text rather than importing workflowService, which pulls in handler-only imports that
        # do not resolve under this test's sys.path.
        source = (pathlib.Path(__file__).parents[3]
                  / "backend" / "handlers" / "workflows" / "workflowService.py").read_text()
        # Matched as a product of integers rather than evaluated, so the declaration is read without
        # executing anything from the source file.
        match = re.search(r"^MAX_LIST_PAGE_BYTES\s*=\s*([0-9]+(?:\s*\*\s*[0-9]+)*)\s*$",
                          source, re.MULTILINE)
        assert match, "workflowService.MAX_LIST_PAGE_BYTES not found"
        assert ps.MAX_LIST_PAGE_BYTES == math.prod(
            int(factor) for factor in match.group(1).split("*"))
        # And it leaves headroom under the limit it exists to respect.
        assert ps.MAX_LIST_PAGE_BYTES < LAMBDA_RESPONSE_LIMIT_BYTES
