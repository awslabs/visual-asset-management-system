# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The tag listing's paginator budget is bounded, including on the un-modelled fallback path.

`get_tags` feeds `maxItems`/`pageSize` into a boto3 paginator's `PaginationConfig` and calls
`build_full_result()`, which accumulates pages until the budget is spent — so an unbounded
caller-supplied value pages the tag table to exhaustion inside one invocation.

`GetTagsRequestModel`'s `le=` bound is not sufficient on its own here, and that is the point of
driving `get_tags` directly: `get_tags_handler` catches the model's `ValidationError` and falls back
to `validate_pagination_info(query_parameters)` on the RAW parameters, which applies no ceiling of its
own (deliberately — see `tests/handlers/databases/test_pagination_ceiling_answers_400.py`). An
over-ceiling request therefore arrives at exactly the shape asserted below.

Both directions are asserted. Clamping only would also be satisfied by a handler that pinned every
request to the ceiling, which would silently ignore a caller asking for a small page.

The model-side `le=` bounds and the structural pass over the budget sites live in
`tests/models/test_pagination_ceilings_remaining_models.py`.
"""

import pytest

from backend.backend.handlers.tags.tagService import get_tags
from backend.backend.common.dynamodb import (
    MAX_PAGINATION_MAX_ITEMS,
    MAX_PAGINATION_PAGE_SIZE,
)

ABSURD = 10 ** 9


class _FakePaginator:
    """Captures the paginate() kwargs and returns an empty result."""

    def __init__(self, captured):
        self.captured = captured

    def paginate(self, **kwargs):
        self.captured.append(kwargs)
        result = type("_Result", (), {})()
        result.build_full_result = lambda: {"Items": []}
        return result


@pytest.fixture
def captured_config(monkeypatch):
    from backend.backend.handlers.tags import tagService

    captured = []
    monkeypatch.setattr(tagService, "paginator", _FakePaginator(captured))
    monkeypatch.setattr(tagService, "get_tag_types", lambda: [])
    monkeypatch.setattr(tagService, "claims_and_roles", {"tokens": ["unit-test-user"]})
    return captured


@pytest.mark.unit
class TestTagListingBudgetIsBounded:
    def test_an_uncapped_budget_is_reduced_to_the_ceiling(self, captured_config):
        get_tags({"maxItems": ABSURD, "pageSize": ABSURD, "startingToken": None})

        # The read really happened, so the bound below is not vacuous.
        assert len(captured_config) == 1, "the paginator was never reached"
        config = captured_config[0]["PaginationConfig"]
        # A bound, not an equality: a read that asks for LESS than the ceiling is cheaper and
        # safer, and pinning equality would fail it. The pass-through control below is what
        # keeps this from being satisfied by a budget clamped to nothing.
        assert config["MaxItems"] <= MAX_PAGINATION_MAX_ITEMS
        assert config["PageSize"] <= MAX_PAGINATION_PAGE_SIZE

    def test_a_within_bounds_budget_is_passed_through_unchanged(self, captured_config):
        """Control: the clamp must not flatten every request onto the ceiling."""
        get_tags({"maxItems": 250, "pageSize": 100, "startingToken": None})

        config = captured_config[0]["PaginationConfig"]
        assert config["MaxItems"] == 250
        assert config["PageSize"] == 100

    def test_the_ceiling_itself_is_passed_through(self, captured_config):
        get_tags({
            "maxItems": MAX_PAGINATION_MAX_ITEMS,
            "pageSize": MAX_PAGINATION_PAGE_SIZE,
            "startingToken": None,
        })

        config = captured_config[0]["PaginationConfig"]
        # A bound, not an equality: a read that asks for LESS than the ceiling is cheaper and
        # safer, and pinning equality would fail it. The pass-through control below is what
        # keeps this from being satisfied by a budget clamped to nothing.
        assert config["MaxItems"] <= MAX_PAGINATION_MAX_ITEMS
        assert config["PageSize"] <= MAX_PAGINATION_PAGE_SIZE
