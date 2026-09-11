# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""All three comment reads bound their paginator budget.

`get_all_comments`, `get_comments` and `get_comments_version` each feed `maxItems`/`pageSize` into a
boto3 paginator's `PaginationConfig` and call `build_full_result()`, which accumulates pages until the
budget is spent. `commentService` has no request model at all — the handler calls
`validate_pagination_info(queryParameters)` and passes the raw dict on, and that helper deliberately
applies no ceiling of its own (it runs ahead of a request model, so clamping there would turn a 400
into a 200 carrying a quietly reduced page; see
`tests/handlers/databases/test_pagination_ceiling_answers_400.py`). The bound at the budget site is
therefore the only thing standing between a caller-chosen `maxItems` and a walk of the comment table.

All three are asserted rather than one, because they are three separate literal dicts: a fix applied to
one leaves the others as they were, and nothing else in the suite reads the budget.

The pass-through case is the necessary other half — clamping alone would also be satisfied by pinning
every request to the ceiling.
"""

import pytest

from backend.backend.handlers.comments.commentService import (
    get_all_comments,
    get_comments,
    get_comments_version,
)
from backend.backend.common.dynamodb import (
    MAX_PAGINATION_MAX_ITEMS,
    MAX_PAGINATION_PAGE_SIZE,
)

ABSURD = 10 ** 9


class _FakePaginator:
    def __init__(self, captured):
        self.captured = captured

    def paginate(self, **kwargs):
        self.captured.append(kwargs)
        result = type("_Result", (), {})()
        result.build_full_result = lambda: {"Items": []}
        return result


@pytest.fixture
def captured_configs(monkeypatch):
    """Route both the low-level client paginator and the resource-API one to one capture list."""
    from backend.backend.handlers.comments import commentService

    captured = []
    paginator = _FakePaginator(captured)

    class _FakeLowLevelClient:
        def get_paginator(self, operation_name):
            return paginator

    class _FakeMeta:
        client = _FakeLowLevelClient()

    class _FakeResource:
        meta = _FakeMeta()

    monkeypatch.setattr(commentService, "dynamodb_client", _FakeLowLevelClient())
    monkeypatch.setattr(commentService, "dynamodb", _FakeResource())
    return captured


@pytest.mark.unit
class TestCommentBudgetsAreBounded:
    def test_every_comment_read_reduces_an_uncapped_budget(self, captured_configs):
        params = {"maxItems": ABSURD, "pageSize": ABSURD, "startingToken": None}
        get_all_comments(dict(params))
        get_comments("asset-1", dict(params))
        get_comments_version("asset-1", "1", dict(params))

        # Each read really reached the paginator, so the bounds below are not vacuous.
        assert len(captured_configs) == 3
        for kwargs in captured_configs:
            config = kwargs["PaginationConfig"]
            # A bound, not an equality: a read that asks for LESS than the ceiling is cheaper
            # and safer, and pinning equality would fail it.
            assert config["MaxItems"] <= MAX_PAGINATION_MAX_ITEMS
            assert config["PageSize"] <= MAX_PAGINATION_PAGE_SIZE

    def test_a_within_bounds_budget_is_passed_through_unchanged(self, captured_configs):
        """Control: the clamp must not flatten every request onto the ceiling."""
        params = {"maxItems": 500, "pageSize": 200, "startingToken": None}
        get_all_comments(dict(params))
        get_comments("asset-1", dict(params))
        get_comments_version("asset-1", "1", dict(params))

        assert len(captured_configs) == 3
        for kwargs in captured_configs:
            config = kwargs["PaginationConfig"]
            assert config["MaxItems"] == 500
            assert config["PageSize"] == 200

    def test_a_starting_token_is_still_threaded(self, captured_configs):
        """The clamp must not have displaced the cursor, which is a separate config key."""
        params = {"maxItems": 500, "pageSize": 200, "startingToken": "cursor-abc"}
        get_all_comments(dict(params))

        config = captured_configs[0]["PaginationConfig"]
        assert config["StartingToken"] == "cursor-abc"
