# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pagination ceilings for the database and bucket listing request models.

`GET /database` and `GET /buckets` take maxItems/pageSize straight from the query string
(databaseService.py:146 and :529) and feed pageSize to a DynamoDB scan Limit. Without an upper
bound the caller chooses the size of the work the Lambda takes on, so the ceilings are asserted
here alongside the values the handlers themselves produce, which must keep parsing.
"""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError


# The effective pageSize a request with no pagination parameters reaches the model with:
# get_databases_handler calls validate_pagination_info (common/dynamodb.py:168), which
# substitutes defaultMaxItemsOverride for an absent pageSize and then copies it to maxItems.
HANDLER_INJECTED_PAGE_SIZE = 10000
# get_buckets_handler seeds pageSize from BUCKETS_DEFAULT_PAGE_SIZE (databaseService.py:42).
BUCKETS_HANDLER_INJECTED_PAGE_SIZE = 3000


@pytest.mark.unit
class TestDatabaseListingPaginationCeilings:
    LIST_MODELS = ["GetDatabasesRequestModel", "GetBucketsRequestModel"]

    def _model(self, model_name):
        import models.databases as d
        return getattr(d, model_name)

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_rejects_page_size_above_ceiling(self, model_name):
        import models.databases as d
        with pytest.raises((ValidationError, ValueError)):
            self._model(model_name)(pageSize=d.MAX_LIST_PAGE_SIZE + 1)

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_rejects_max_items_above_ceiling(self, model_name):
        import models.databases as d
        with pytest.raises((ValidationError, ValueError)):
            self._model(model_name)(maxItems=d.MAX_LIST_MAX_ITEMS + 1)

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_rejects_the_unbounded_value_from_the_finding(self, model_name):
        with pytest.raises((ValidationError, ValueError)):
            self._model(model_name)(maxItems=10 ** 9, pageSize=10 ** 9)

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_bounds_are_declared_and_live(self, model_name):
        # Pydantic v1 collects an unrecognized Field kwarg into FieldInfo.extra instead of
        # raising, so a misspelled bound reads as a constraint and enforces nothing.
        model_cls = self._model(model_name)
        for field in ("maxItems", "pageSize"):
            info = model_cls.__fields__[field].field_info
            assert info.ge == 1
            assert info.le is not None
            assert not info.extra

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_ceilings_admit_the_declared_defaults(self, model_name):
        # A ceiling below a field's own default would 400 every request that omits it.
        import models.databases as d
        model = self._model(model_name)()
        assert model.maxItems <= d.MAX_LIST_MAX_ITEMS
        assert model.pageSize <= d.MAX_LIST_PAGE_SIZE

    def test_page_size_ceiling_admits_the_handler_injected_default(self):
        # GET /database with no query parameters arrives at the model carrying this pageSize;
        # a lower ceiling would reject the frontend's own database listing.
        import models.databases as d
        assert d.MAX_LIST_PAGE_SIZE >= HANDLER_INJECTED_PAGE_SIZE
        assert d.MAX_LIST_MAX_ITEMS >= HANDLER_INJECTED_PAGE_SIZE


@pytest.mark.unit
class TestDatabaseListingPaginationAcceptedValues:
    """Positive controls: every value a client legitimately sends today still parses."""

    LIST_MODELS = ["GetDatabasesRequestModel", "GetBucketsRequestModel"]

    def _model(self, model_name):
        import models.databases as d
        return getattr(d, model_name)

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_accepts_absent_pagination_parameters(self, model_name):
        model = self._model(model_name)()
        assert model.maxItems == 30000
        assert model.pageSize == 3000

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_accepts_the_handler_injected_page_size(self, model_name):
        model = self._model(model_name)(
            maxItems=HANDLER_INJECTED_PAGE_SIZE, pageSize=HANDLER_INJECTED_PAGE_SIZE
        )
        assert model.pageSize == HANDLER_INJECTED_PAGE_SIZE

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_accepts_the_buckets_handler_injected_page_size(self, model_name):
        model = self._model(model_name)(pageSize=BUCKETS_HANDLER_INJECTED_PAGE_SIZE)
        assert model.pageSize == BUCKETS_HANDLER_INJECTED_PAGE_SIZE

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    @pytest.mark.parametrize("page_size", [1, 200, 500, 3000, 10000])
    def test_accepts_the_cli_page_size_range(self, model_name, page_size):
        # `vamscli database list --page-size N` / `database list-buckets --page-size N`.
        assert self._model(model_name)(pageSize=page_size).pageSize == page_size

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_accepts_max_items_at_the_ceiling(self, model_name):
        assert self._model(model_name)(maxItems=30000).maxItems == 30000

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_still_rejects_the_zero_and_negative_floor(self, model_name):
        for value in (0, -1):
            with pytest.raises((ValidationError, ValueError)):
                self._model(model_name)(pageSize=value)
            with pytest.raises((ValidationError, ValueError)):
                self._model(model_name)(maxItems=value)
