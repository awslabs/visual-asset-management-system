# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-098 (MEDIUM): maxItems must bound a metadata page, and neither pagination
parameter may exceed the ceiling.

`paginate_metadata_records` resolved its page size as::

    page_size = int(query_params.get('pageSize') or query_params.get('maxItems')
                    or DEFAULT_METADATA_PAGE_SIZE)

Every metadata GET builds `query_params` from a request model that supplies a `pageSize`
default, so the `or ... maxItems` arm was unreachable on a real request: `maxItems`, documented
as "Maximum items to return", limited nothing. `pageSize` also carried no upper bound, so a
caller could ask for any size at all.

The page is now `min(pageSize, maxItems)` and a value above `MAX_METADATA_PAGE_SIZE` is
refused with `VAMSGeneralErrorResponse` (a 400 through every metadata handler) rather than
quietly reduced. Each of the four `Get*RequestModel`s carries the same value as an `le=`
bound, so an oversized parameter is refused at request validation as well.

The default page size sits BELOW `MAX_METADATA_RECORDS_PER_ENTITY`, so an entity written up to
the record cap really pages and the `NextToken` walk every client implements is exercised by an
ordinary read rather than only by a hand-built request. Hence
`test_the_default_page_size_engages_paging_within_the_record_cap`, paired with a control that
following the token still yields the entity's whole set in order.

Positive controls, since this narrows accepted input: a request whose record count fits one page
returns it whole with no token, an explicit `pageSize` still pages and its token still
round-trips, and the ceiling value itself is accepted.
"""

import base64
import contextlib
import sys

import pytest
from unittest.mock import MagicMock, patch
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.handlers.metadata import metadataService
from backend.backend.handlers.metadata.metadataService import (
    DEFAULT_METADATA_MAX_ITEMS,
    DEFAULT_METADATA_PAGE_SIZE,
    MAX_METADATA_PAGE_SIZE,
    MAX_METADATA_RECORDS_PER_ENTITY,
    METADATA_PAGE_SIZE_OUT_OF_RANGE_MESSAGE,
    VAMSGeneralErrorResponse,
    paginate_metadata_records,
    resolve_metadata_page_parameter,
)

CLAIMS = {"tokens": ["user1"]}

# What every metadata GET actually hands the pager: the request model supplies both defaults,
# so a caller who submits nothing still arrives with both parameters present.
MODEL_DEFAULTS = {"pageSize": DEFAULT_METADATA_PAGE_SIZE, "maxItems": DEFAULT_METADATA_MAX_ITEMS}


def _offset(token):
    return int(base64.b64decode(token).decode("utf-8"))


@pytest.fixture(autouse=True)
def _clear_schema_cache():
    """The aggregate cache is a module global, and this directory's conftest loads
    `common.metadataSchemaValidation` as a separate module object from
    `backend.backend.common.metadataSchemaValidation`, so both are cleared."""
    modules = [
        sys.modules.get("common.metadataSchemaValidation"),
        sys.modules.get("backend.backend.common.metadataSchemaValidation"),
    ]
    for module in modules:
        if module is not None:
            module._schema_cache.clear()
    yield
    for module in modules:
        if module is not None:
            module._schema_cache.clear()


@pytest.mark.unit
class TestMaxItemsBoundsThePage:
    """The decisive case: maxItems alongside the pageSize default every request carries."""

    def test_maxitems_bounds_the_page_when_pagesize_is_the_model_default(self):
        records = list(range(25))
        page, next_token = paginate_metadata_records(
            records, dict(MODEL_DEFAULTS, maxItems=10))

        assert page == list(range(10)), (
            "maxItems did not bound the page; the whole record set was returned")
        assert next_token is not None, "a bounded page must carry a token for the remainder"
        assert _offset(next_token) == 10

    def test_the_maxitems_token_round_trips_to_the_remainder(self):
        """A ceiling that pages without a usable token would make the tail unreachable."""
        records = list(range(25))
        params = dict(MODEL_DEFAULTS, maxItems=10)
        _, token = paginate_metadata_records(records, params)
        page2, token2 = paginate_metadata_records(records, dict(params, startingToken=token))

        assert page2 == list(range(10, 20))
        assert _offset(token2) == 20

    def test_the_smaller_of_the_two_wins_when_pagesize_is_the_smaller(self):
        """maxItems is a ceiling, not an override: an explicit smaller pageSize still governs."""
        records = list(range(25))
        page, _ = paginate_metadata_records(records, {"pageSize": 5, "maxItems": 10})

        assert page == list(range(5))


@pytest.mark.unit
class TestThePaginationParametersAreBounded:
    def test_an_oversized_pagesize_is_refused(self):
        with pytest.raises(VAMSGeneralErrorResponse) as raised:
            paginate_metadata_records(list(range(25)), {"pageSize": MAX_METADATA_PAGE_SIZE + 1})

        # Containment, not equality: VAMSGeneralErrorResponse.__init__ prepends
        # "VAMS General Error: " to every message, so an equality assertion fails on the prefix
        # while saying nothing about the bound. What matters is that the specific bound message
        # survives to the caller rather than being replaced by a generic one.
        assert METADATA_PAGE_SIZE_OUT_OF_RANGE_MESSAGE in str(raised.value)

    def test_an_oversized_maxitems_is_refused(self):
        with pytest.raises(VAMSGeneralErrorResponse):
            paginate_metadata_records(
                list(range(25)),
                dict(MODEL_DEFAULTS, maxItems=MAX_METADATA_PAGE_SIZE + 1))

    def test_the_refusal_names_no_submitted_value(self):
        """Rule 11: the client message states the bound, never what the caller sent."""
        with pytest.raises(VAMSGeneralErrorResponse) as raised:
            paginate_metadata_records(list(range(25)), {"pageSize": 987654321})

        assert "987654321" not in str(raised.value)

    def test_the_ceiling_value_itself_is_accepted(self):
        """Boundary control: the bound is inclusive, so the largest legal request still serves."""
        records = list(range(25))
        page, token = paginate_metadata_records(records, {"pageSize": MAX_METADATA_PAGE_SIZE})

        assert page == records
        assert token is None


@pytest.mark.unit
class TestTheLegitimateRequestsStillWork:
    """Positive controls. Without these a refusal-only file cannot distinguish a bound from
    an outage."""

    def test_the_model_defaults_return_the_whole_record_set(self):
        records = list(range(25))
        page, token = paginate_metadata_records(records, dict(MODEL_DEFAULTS))

        assert page == records
        assert token is None

    def test_an_explicit_page_size_still_pages(self):
        records = list(range(25))
        page, token = paginate_metadata_records(records, {"pageSize": 10})

        assert page == list(range(10))
        assert _offset(token) == 10

    def test_a_string_page_size_from_a_query_string_is_honoured(self):
        page, _ = paginate_metadata_records(list(range(25)), {"pageSize": "10"})

        assert page == list(range(10))

    def test_a_non_numeric_page_size_serves_the_default(self):
        """Unusable input keeps the previous forgiving behaviour rather than 400-ing a read."""
        records = list(range(25))
        page, token = paginate_metadata_records(records, {"pageSize": "abc"})

        assert page == records
        assert token is None

    def test_a_page_size_below_one_serves_the_default(self):
        records = list(range(25))
        page, _ = paginate_metadata_records(records, {"pageSize": 0})

        assert page == records

    def test_the_default_page_size_engages_paging_within_the_record_cap(self):
        """The default is below the per-entity record cap, so paging happens on a real read.

        A default at or above the cap means no legally-written entity ever pages, which leaves the
        NextToken walk in every client (web, CLI, MCP, both connectors) unexercised in production.
        """
        assert DEFAULT_METADATA_PAGE_SIZE < MAX_METADATA_RECORDS_PER_ENTITY

        records = list(range(MAX_METADATA_RECORDS_PER_ENTITY))
        page, token = paginate_metadata_records(records, dict(MODEL_DEFAULTS))
        assert page == records[:DEFAULT_METADATA_PAGE_SIZE]
        assert token is not None

    def test_following_the_token_returns_the_whole_set_at_the_default(self):
        """The paired arm: a smaller default must not make the tail unreachable.

        Asserting only that the first page is short would pass on a pager that never emits a usable
        token, so the walk is run to exhaustion and the result compared to the full ordered set.
        """
        records = list(range(MAX_METADATA_RECORDS_PER_ENTITY))
        collected = []
        params = dict(MODEL_DEFAULTS)
        pages = 0
        while True:
            page, token = paginate_metadata_records(records, params)
            collected.extend(page)
            pages += 1
            assert pages <= MAX_METADATA_RECORDS_PER_ENTITY, "the walk did not terminate"
            if token is None:
                break
            params = dict(MODEL_DEFAULTS, startingToken=token)

        assert pages > 1, "the record cap fits one page, so this proves nothing"
        assert collected == records


@pytest.mark.unit
class TestTheRequestModelsCarryTheBound:
    """The models are read off `metadataService`, which is where the request path gets them.

    `le=` is asserted through `field_info` rather than by reading the declaration: pydantic v1
    collects an unrecognised keyword into `field_info.extra` instead of raising, so a bound
    spelled the v2 way would annotate the field and validate nothing.
    """

    # The models the handler itself imported, and the two parameters each of them accepts.
    MODELS = (
        "GetAssetLinkMetadataRequestModel",
        "GetAssetMetadataRequestModel",
        "GetFileMetadataRequestModel",
        "GetDatabaseMetadataRequestModel",
    )
    PARAMETERS = ("maxItems", "pageSize")

    @pytest.mark.parametrize("model_name", MODELS)
    @pytest.mark.parametrize("parameter", PARAMETERS)
    def test_the_bound_is_live_and_is_the_handler_ceiling(self, model_name, parameter):
        field_info = getattr(metadataService, model_name).__fields__[parameter].field_info

        assert field_info.le == MAX_METADATA_PAGE_SIZE, (
            f"{model_name}.{parameter} does not carry the handler's ceiling as an le= bound")
        assert not field_info.extra, (
            f"{model_name}.{parameter} has a keyword pydantic v1 swallowed: {field_info.extra}")

    @pytest.mark.parametrize("model_name", MODELS)
    @pytest.mark.parametrize("parameter", PARAMETERS)
    def test_an_oversized_parameter_is_refused_at_request_validation(self, model_name, parameter):
        model_cls = getattr(metadataService, model_name)

        with pytest.raises(ValidationError) as raised:
            model_cls(**dict(self._required_fields(model_name),
                             **{parameter: MAX_METADATA_PAGE_SIZE + 1}))

        # The bound is what refused it, not a required field or another rule on the model.
        assert (parameter,) in {error['loc'] for error in raised.value.errors()}
        # Set containment over the meaningful values. An exact sequence also pins how many
        # errors pydantic reported and in what order, so a version that adds a second,
        # more specific error for the same field would fail a test about the bound.
        assert 'value_error.number.not_le' in {error['type'] for error in raised.value.errors()}

    @pytest.mark.parametrize("model_name", MODELS)
    def test_the_bound_value_and_the_defaults_are_accepted(self, model_name):
        """Positive control: the refusal above is the bound, not the required fields."""
        model_cls = getattr(metadataService, model_name)
        required = self._required_fields(model_name)

        at_bound = model_cls(**dict(required, pageSize=MAX_METADATA_PAGE_SIZE,
                                    maxItems=MAX_METADATA_PAGE_SIZE))
        assert at_bound.pageSize == MAX_METADATA_PAGE_SIZE

        defaulted = model_cls(**required)
        assert defaulted.pageSize == DEFAULT_METADATA_PAGE_SIZE
        assert defaulted.maxItems == DEFAULT_METADATA_MAX_ITEMS, (
            "the model default and the handler default have drifted apart")

    @staticmethod
    def _required_fields(model_name):
        if model_name == "GetFileMetadataRequestModel":
            return {"filePath": "/f.txt", "type": "metadata"}
        return {}


@pytest.mark.unit
class TestResolveMetadataPageParameter:
    def test_an_absent_value_takes_the_default(self):
        assert resolve_metadata_page_parameter(None, 42, "pageSize") == 42

    def test_an_empty_string_takes_the_default(self):
        assert resolve_metadata_page_parameter("", 42, "pageSize") == 42

    def test_a_legal_value_is_returned_unchanged(self):
        assert resolve_metadata_page_parameter(17, 42, "pageSize") == 17

    def test_an_oversized_value_raises(self):
        with pytest.raises(VAMSGeneralErrorResponse):
            resolve_metadata_page_parameter(MAX_METADATA_PAGE_SIZE + 1, 42, "pageSize")


class _AssetMetadataGetHarness:
    """Module globals get_asset_metadata touches, seeded with `record_count` stored rows.

    The schema query answers with no schemas, so enrichment is a pass-through and the page
    the pager returns is the page the caller sees.
    """

    def __init__(self, record_count=25):
        self.client = MagicMock()
        page_iterator = MagicMock()
        page_iterator.build_full_result.return_value = {
            "Items": [
                {
                    "metadataKey": {"S": f"key{i:03d}"},
                    "metadataValue": {"S": f"value{i}"},
                    "metadataValueType": {"S": "string"},
                    "databaseId:assetId:filePath": {"S": "db1:asset1:/"},
                }
                for i in range(record_count)
            ]
        }
        paginator = MagicMock()
        paginator.paginate.return_value = page_iterator
        self.client.get_paginator.return_value = paginator
        self.client.query.return_value = {"Items": []}

        self.asset_table = MagicMock()
        self.asset_table.get_item.return_value = {
            "Item": {"databaseId": "db1", "assetId": "asset1", "assetName": "A", "tags": []}
        }
        self.database_table = MagicMock()
        self.database_table.get_item.return_value = {"Item": {"databaseId": "db1"}}

        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        self.enforcer_cls = MagicMock(return_value=enforcer)

        self._stack = contextlib.ExitStack()

    def __enter__(self):
        for target, replacement in (
            ("dynamodb_client", self.client),
            ("asset_storage_table", self.asset_table),
            ("database_storage_table", self.database_table),
            ("asset_file_metadata_table", MagicMock()),
            ("CasbinEnforcer", self.enforcer_cls),
        ):
            self._stack.enter_context(patch.object(metadataService, target, replacement))
        return self

    def __exit__(self, *exc):
        self._stack.close()
        return False


@pytest.mark.unit
class TestTheBoundReachesTheCaller:
    """Through the real GET, because the pager's exception is only useful if the handler
    surfaces it instead of folding it into "Error retrieving metadata"."""

    def test_a_get_with_maxitems_returns_that_many_records_and_a_token(self):
        with _AssetMetadataGetHarness(record_count=25):
            response = metadataService.get_asset_metadata(
                "db1", "asset1", dict(MODEL_DEFAULTS, maxItems=10), CLAIMS)

        assert len(response.metadata) == 10, "maxItems did not bound the GET response"
        assert response.NextToken is not None

    def test_a_get_with_an_oversized_pagesize_is_refused_with_the_bound_message(self):
        with _AssetMetadataGetHarness(record_count=25):
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                metadataService.get_asset_metadata(
                    "db1", "asset1",
                    dict(MODEL_DEFAULTS, pageSize=MAX_METADATA_PAGE_SIZE + 1), CLAIMS)

        assert METADATA_PAGE_SIZE_OUT_OF_RANGE_MESSAGE in str(raised.value), (
            "the bound was swallowed into the generic retrieval error")

    def test_the_same_get_returns_every_record_on_the_defaults(self):
        """Positive control: the harness reaches a complete answer, so the two assertions
        above are about the parameters and not about the harness."""
        with _AssetMetadataGetHarness(record_count=25):
            response = metadataService.get_asset_metadata(
                "db1", "asset1", dict(MODEL_DEFAULTS), CLAIMS)

        assert len(response.metadata) == 25
        assert response.NextToken is None
