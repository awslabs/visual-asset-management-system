# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The trigger listing serves one bounded page and a usable NextToken.

A workflow holds several triggers of one base type (sort keys ``fileUpload#<triggerId>``), each
carrying its own inputFileFilters allow/exclude lists and defaultTemplateIds map, so the row set for
one workflow can outgrow both a single 1 MB query page (Rule 14) and a synchronous Lambda response
(Rule 15). The listing therefore pages EXTERNALLY: the caller receives a page plus an opaque token
and walks it, rather than the handler accumulating the partition.

The assertion that matters is the ROUND TRIP -- take the token from page one, feed it back as
``startingToken``, and assert page two begins where page one stopped. Asserting only that a
``NextToken`` is present passes on a token no client can use, which is the failure mode
``backend/CLAUDE.md`` calls out: page one looks perfect and every later page is unreachable with no
error anywhere.

``_same_type_triggers`` in the same module reads the same partition with the same key condition and
must stay EXHAUSTIVE (Rule 14 case A): it is the duplicate-sibling check inside ``set_trigger``, so a
bounded read there makes duplicate rejection stop firing silently. A live seed of three small rows
cannot prove that -- the whole partition fits in one DynamoDB page either way -- so it is proven here
by forcing a ``LastEvaluatedKey``.
"""

import importlib.util
import json
import os
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.workflows import workflowTriggerService as wts
from backend.backend.handlers.workflows.workflowTriggerService import lambda_handler
from backend.tests.pagingStub import BareMockReader, Pager

MOD = "backend.backend.handlers.workflows.workflowTriggerService"

# The root conftest replaces `common.dynamodb` with a MagicMock, whose
# `validate_pagination_info` fills nothing -- so every PaginationConfig assertion below would read
# whatever the test happened to pass rather than what the handler really serves. Load the REAL helper
# by path: the unit tests normalize their own params through it, and the two handler tests patch the
# module's bound name with it.
# parents: [0] workflows, [1] handlers, [2] the backend package root that also holds common/.
_REAL_DDB_PATH = (pathlib.Path(wts.__file__).parents[2] / "common" / "dynamodb.py")
_real_ddb_spec = importlib.util.spec_from_file_location(
    "_real_common_dynamodb_for_trigger_paging", os.fspath(_REAL_DDB_PATH))
_real_ddb = importlib.util.module_from_spec(_real_ddb_spec)
_real_ddb_spec.loader.exec_module(_real_ddb)
REAL_VALIDATE_PAGINATION_INFO = _real_ddb.validate_pagination_info


def _real_pagination(query_params=None):
    """The query-parameter dict the handler would hand `list_triggers`, normalized for real."""
    params = dict(query_params or {})
    REAL_VALIDATE_PAGINATION_INFO(params, wts.DEFAULT_LIST_PAGE_ITEMS)
    return params


@pytest.mark.unit
def test_the_real_pagination_validator_is_the_one_under_test():
    """Non-vacuity control: the loaded helper actually fills the dict, so the assertions in this
    file are about the handler's bound rather than about a no-op mock."""
    assert _REAL_DDB_PATH.is_file(), _REAL_DDB_PATH
    params = _real_pagination()
    assert params["maxItems"] == wts.DEFAULT_LIST_PAGE_ITEMS
    assert params["pageSize"] == wts.DEFAULT_LIST_PAGE_ITEMS
    assert params["startingToken"] is None


BASE = "/database/db1/workflows/wflow1/triggers"
PARAMS = {"databaseId": "db1", "workflowId": "wflow1"}
WF_ITEM = {"databaseId": "db1", "workflowId": "wflow1", "workflowName": "W"}


def _event(query=None):
    return {
        "requestContext": {"http": {"method": "GET", "path": BASE}},
        "pathParameters": dict(PARAMS),
        "queryStringParameters": query,
        "headers": {"authorization": "Bearer test-token"},
        "body": None,
    }


def _enforcer():
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = True
    return inst


def _row(trigger_key):
    """A stored trigger row, keyed as the base type or as ``type#triggerId``."""
    return {
        "workflowDatabaseId:workflowId": "db1:wflow1",
        "workflowDatabaseId": "db1",
        "workflowId": "wflow1",
        "triggerType": trigger_key,
        "triggerConfig": {"inputFileFilters": {"allow": ["*.glb"], "exclude": []},
                          "defaultTemplateIds": {}},
        "enabled": True,
        "dateCreated": "2026-01-01T00:00:00Z",
        "dateModified": "2026-01-01T00:00:00Z",
    }


def _cursor(trigger_key):
    """The continuation key a triggers-table page carries: both halves of the composite primary key."""
    return {"workflowDatabaseId:workflowId": "db1:wflow1", "triggerType": trigger_key}


def _table(side_effect):
    table = MagicMock()
    table.query.side_effect = side_effect
    return table


class FakePaginator:
    """A paginator that honours ``StartingToken`` and ``MaxItems`` over a fixed row list.

    Modelled on botocore's contract rather than on a fixed page script, so a token this stub emits
    can be fed back through the handler and the resulting page ASSERTED to begin where the previous
    one stopped. A canned ``{"Items": [...], "NextToken": "..."}`` cannot show that: it answers the
    same way whatever token the caller sends, so the round trip would pass on an unusable token.
    """

    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def get_paginator(self, operation):
        assert operation == "query", operation
        return self

    def paginate(self, **kwargs):
        self.calls.append(kwargs)
        config = kwargs.get("PaginationConfig") or {}
        start = int(config.get("StartingToken") or 0)
        max_items = int(config.get("MaxItems") or len(self.rows))
        stop = start + max_items
        result = {"Items": self.rows[start:stop]}
        if stop < len(self.rows):
            # Opaque to the handler and to the caller, exactly as botocore's own token is.
            result["NextToken"] = str(stop)
        outer = MagicMock()
        outer.build_full_result.return_value = result
        return outer


def _with_paginator(paginator):
    """Patch the module's dynamodb resource so its low-level client hands back ``paginator``."""
    mock_dynamodb = MagicMock()
    mock_dynamodb.meta.client.get_paginator.side_effect = paginator.get_paginator
    return patch(f"{MOD}.dynamodb", mock_dynamodb)


@pytest.mark.unit
class TestListTriggersExternalPaging:
    def test_a_bounded_page_reports_a_token_that_reaches_the_next_page(self):
        """The round trip: page two begins where page one stopped, and the union is the whole set."""
        paginator = FakePaginator([_row("fileUpload"), _row("fileUpload#second"),
                                   _row("fileUpload#third")])

        with _with_paginator(paginator):
            first = wts.list_triggers("db1", "wflow1", _real_pagination({"pageSize": "2"}))

        assert [item.triggerType for item in first.Items] == ["fileUpload", "fileUpload#second"]
        assert isinstance(first.NextToken, str) and first.NextToken, (
            f"a bounded page reported no usable token: {first.NextToken!r}")

        with _with_paginator(paginator):
            second = wts.list_triggers(
                "db1", "wflow1",
                _real_pagination({"pageSize": "2", "startingToken": first.NextToken}))

        assert [item.triggerType for item in second.Items] == ["fileUpload#third"]
        assert second.NextToken is None
        # Stated as a partition of the seeded set, so a page that repeated a row fails here rather
        # than passing on a plausible-looking count.
        first_keys = [item.triggerType for item in first.Items]
        second_keys = [item.triggerType for item in second.Items]
        assert not set(first_keys) & set(second_keys), (first_keys, second_keys)
        assert set(first_keys) | set(second_keys) == {
            "fileUpload", "fileUpload#second", "fileUpload#third"}

    def test_a_complete_listing_reports_no_token(self):
        """Negative control: the ordinary workflow fits one page and offers no continuation."""
        paginator = FakePaginator([_row("fileUpload")])

        with _with_paginator(paginator):
            result = wts.list_triggers("db1", "wflow1", _real_pagination())

        assert [item.triggerType for item in result.Items] == ["fileUpload"]
        assert result.Items[0].triggerBaseType == "fileUpload"
        assert result.Items[0].triggerId == ""
        assert result.NextToken is None

    def test_an_empty_partition_lists_nothing(self):
        paginator = FakePaginator([])

        with _with_paginator(paginator):
            result = wts.list_triggers("db1", "wflow1", _real_pagination())

        assert result.Items == []
        assert result.NextToken is None
        assert paginator.calls, "the listing never read the table"

    def test_the_read_is_bounded_and_keeps_the_partition_key_condition(self):
        paginator = FakePaginator([_row("fileUpload")])

        with _with_paginator(paginator):
            wts.list_triggers("db1", "wflow1", _real_pagination())

        assert paginator.calls, "nothing was recorded, so the bound proves nothing"
        assert len(paginator.calls) <= 1, paginator.calls
        call = paginator.calls[0]
        assert call["TableName"] == wts.triggers_table_name
        # A read that dropped the key condition would list another workflow's triggers.
        assert "KeyConditionExpression" in call
        # The base table, not the by-type GSI the dispatcher uses for its cross-workflow lookup.
        assert "IndexName" not in call, call
        # Rule 15: the accumulation is bounded, so the response cannot grow past the Lambda limit.
        config = call["PaginationConfig"]
        assert config["MaxItems"] == wts.DEFAULT_LIST_PAGE_ITEMS
        assert config["PageSize"] == wts.DEFAULT_LIST_PAGE_ITEMS
        assert config["StartingToken"] is None

    def test_a_caller_cannot_ask_for_an_unbounded_page(self):
        paginator = FakePaginator([_row("fileUpload")])

        with _with_paginator(paginator):
            wts.list_triggers("db1", "wflow1",
                              _real_pagination({"maxItems": "100000", "pageSize": "100000"}))

        assert paginator.calls, "the paginator was never called, so no bound was requested"
        config = paginator.calls[0]["PaginationConfig"]
        # The claim is that an oversized caller value is CLAMPED, not that the request equals the
        # cap exactly -- a read that asks for less is cheaper and must not fail this.
        assert config["MaxItems"] <= wts.MAX_LIST_PAGE_ITEMS, config
        assert config["PageSize"] <= wts.MAX_LIST_PAGE_ITEMS, config

    def test_the_caller_query_params_are_not_the_only_source_of_the_bound(self):
        """Positive control on the clamp: a small explicit page is honoured, so the assertion above
        is a clamp rather than a constant."""
        paginator = FakePaginator([_row("fileUpload"), _row("fileUpload#second")])

        with _with_paginator(paginator):
            wts.list_triggers("db1", "wflow1",
                              _real_pagination({"maxItems": "1", "pageSize": "1"}))

        config = paginator.calls[0]["PaginationConfig"]
        assert config["MaxItems"] == 1
        assert config["PageSize"] == 1


@pytest.mark.unit
class TestListTriggersEndpointPaging:
    """The same contract through GET /database/{id}/workflows/{id}/triggers."""

    @patch(f"{MOD}.validate_pagination_info", REAL_VALIDATE_PAGINATION_INFO)
    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_the_endpoint_walks_its_own_token(self, mock_enforcer, mock_claims, mock_parent):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, WF_ITEM)
        paginator = FakePaginator([_row("fileUpload"), _row("fileUpload#second")])

        with _with_paginator(paginator):
            first = lambda_handler(_event({"pageSize": "1"}), MagicMock())

        assert first["statusCode"] == 200
        page_one = json.loads(first["body"])["message"]
        assert [i["triggerType"] for i in page_one["Items"]] == ["fileUpload"]
        token = page_one["NextToken"]
        assert isinstance(token, str) and token, page_one

        with _with_paginator(paginator):
            second = lambda_handler(
                _event({"pageSize": "1", "startingToken": token}), MagicMock())

        assert second["statusCode"] == 200
        page_two = json.loads(second["body"])["message"]
        assert [i["triggerType"] for i in page_two["Items"]] == ["fileUpload#second"]
        assert page_two["NextToken"] is None

    @patch(f"{MOD}.validate_pagination_info", REAL_VALIDATE_PAGINATION_INFO)
    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_an_unparameterized_request_serves_the_default_page(
            self, mock_enforcer, mock_claims, mock_parent):
        """A REST event sends explicit JSON ``null`` for queryStringParameters, which must not make
        the read unbounded."""
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, WF_ITEM)
        paginator = FakePaginator([_row("fileUpload")])

        with _with_paginator(paginator):
            resp = lambda_handler(_event(None), MagicMock())

        assert resp["statusCode"] == 200
        config = paginator.calls[0]["PaginationConfig"]
        assert config["MaxItems"] == wts.DEFAULT_LIST_PAGE_ITEMS


@pytest.mark.unit
class TestSameTypeTriggersStaysExhaustive:
    """The duplicate-sibling read inside ``set_trigger`` must see the WHOLE partition.

    It shares the key condition with the listing, so factoring the two into one bounded helper makes
    a duplicate sitting past page one stop being rejected -- with no error and no failing listing
    assertion. Rule 14 case A applies here and not to the listing.
    """

    def test_a_sibling_on_a_later_page_is_seen(self):
        pager = Pager(
            {"Items": [_row("fileUpload")], "LastEvaluatedKey": _cursor("fileUpload")},
            {"Items": [_row("fileUpload#second")]},
            name="_same_type_triggers",
        )

        with patch(f"{MOD}._triggers_table", return_value=_table(pager)):
            rows = wts._same_type_triggers("db1", "wflow1", "fileUpload")

        assert [row["triggerType"] for row in rows] == ["fileUpload", "fileUpload#second"]
        pager.assert_paged_to_exhaustion()

    def test_the_continuation_keeps_the_partition_key_condition(self):
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": _cursor("fileUpload")},
            {"Items": []},
            name="_same_type_triggers",
        )

        with patch(f"{MOD}._triggers_table", return_value=_table(pager)):
            wts._same_type_triggers("db1", "wflow1", "fileUpload")

        # `all()` holds vacuously over no calls, so assert the continuation happened first.
        assert len(pager.calls) >= 2, (
            f"the pager was not driven across a continuation: {pager.calls}")
        assert all("KeyConditionExpression" in call for call in pager.calls), pager.calls
        assert all("IndexName" not in call for call in pager.calls), (
            f"the sibling check must read the base table, not the by-type GSI: {pager.calls}")

    def test_terminates_against_an_under_stubbed_reader(self):
        """The loop ends on key PRESENCE, so a bare-mock page ends it after one read.

        The value form would spin here rather than fail, and a timeout raises no assertion -- the
        capped reader turns that into a diagnosable failure instead."""
        reader = BareMockReader(name="_same_type_triggers")

        with patch(f"{MOD}._triggers_table", return_value=_table(reader)):
            rows = wts._same_type_triggers("db1", "wflow1", "fileUpload")

        assert rows == []
        assert reader.calls, "the sibling check never read the table"
        assert len(reader.calls) <= 1, reader.calls
