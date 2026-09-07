# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The per-database schema query must page to exhaustion, and a partial read must not be cached.

`get_aggregated_schemas` raises `SchemaLookupError` rather than returning a partial aggregate, because
every control derived from the aggregate reads absence as permission: `restrictMetadataOutsideSchemas`
is applied only inside `if aggregated_schema:`, and a field missing from the aggregate is simply not
required. That raise closed the "a query failed" hole but not the "a query succeeded and returned only
its first page" one — a single DynamoDB `query` returns at most 1 MB, so a database with more metadata
schemas than fit one page produced exactly the partial field set the raise exists to prevent, with no
error and no warning. The partial aggregate was then written to the 60-second module cache, so one
oversized schema set degraded validation for every subsequent write on that Lambda instance.

Paging to exhaustion then needs a bound of its own. This lookup runs on the SYNCHRONOUS metadata
create/update path on every cache miss, so an unbounded walk lets one write issue an arbitrary number
of sequential queries -- and the bound cannot be a silent truncation, because a truncated aggregate is
the very partial field set described above. `MAX_SCHEMA_QUERY_PAGES` therefore raises
`SchemaLookupError` rather than returning what it has, which the write paths already translate into a
refusal and the read paths into un-enriched output.

Sibling coverage: `test_metadataSchemaValidation_fetch_fails_closed.py` carries the failed-query half.
This file carries the completeness half, and each negative is paired with a positive control so an
implementation that raised (or cached nothing) unconditionally cannot satisfy it.
"""

import contextlib

import pytest
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from backend.backend.common import metadataSchemaValidation as msv
from backend.backend.common.metadataSchemaValidation import (
    MAX_SCHEMA_QUERY_PAGES,
    SchemaLookupError,
    get_aggregated_schemas,
)
from backend.tests.pagingStub import Pager

THROTTLE = ClientError(
    {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "throttled"}},
    "Query",
)

_TABLE = "test-metadata-schema-table"
_ENTITY = "assetMetadata"

# Generous ceiling on the reads one lookup may issue, so a bounded implementation passes and a loop
# that never advances its cursor does not. Not the number of pages any scenario serves.
QUERY_CALL_CEILING = 40

# A schema set needing more pages than BOTH the loop's own cap and the ceiling above, so a walk that
# simply pages to the end fails the read-count bound rather than passing it by accident.
PAGES_BEYOND_THE_CAP = QUERY_CALL_CEILING + 20

# Where the shared Pager gives up rather than let a non-terminating loop hang the suite. Kept above
# the loop's own cap AND above the pages served, so the stub never trips before the bound under test
# (backend/tests/CLAUDE.md: a stub cap below the loop's cap makes the bound unfailable).
RUNAWAY_READS = PAGES_BEYOND_THE_CAP + 20


def _schema_item(field_name, database_id, schema_id):
    """One typed DynamoDB schema row carrying a single required field."""
    return {
        "metadataSchemaId": {"S": schema_id},
        "databaseId": {"S": database_id},
        "schemaName": {"S": f"{schema_id}-name"},
        "metadataEntityType": {"S": _ENTITY},
        "enabled": {"BOOL": True},
        "fields": {
            "L": [
                {
                    "M": {
                        "metadataFieldKeyName": {"S": field_name},
                        "metadataFieldName": {"S": field_name},
                        "metadataFieldValueType": {"S": "string"},
                        "required": {"BOOL": True},
                        "sequence": {"N": "1"},
                    }
                }
            ]
        },
    }


def _page(field_names, database_id="db1", last_key=None):
    """One DynamoDB query response. `last_key=None` means this is the final page.

    The key is OMITTED on the final page rather than set to a falsy value, which is what DynamoDB
    actually does — and the reason the loop must test key PRESENCE.
    """
    page = {
        "Items": [
            _schema_item(name, database_id, f"schema-{name}") for name in field_names
        ]
    }
    if last_key is not None:
        page["LastEvaluatedKey"] = last_key
    return page


@pytest.fixture(autouse=True)
def _clear_schema_cache():
    """The 60-second aggregate cache is a module global with no per-test reset."""
    msv._schema_cache.clear()
    yield
    msv._schema_cache.clear()


class _PagingClient:
    """A DynamoDB client that serves `pages` in order and records every query kwargs dict.

    Once the pages run out it raises, so a loop that keeps querying after the final page fails the
    test with a message instead of hanging the suite.
    """

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) > len(self.pages):
            raise AssertionError(
                f"the schema query was still paging after {len(self.pages)} pages had been served")
        page = self.pages[len(self.calls) - 1]
        if isinstance(page, Exception):
            raise page
        return page


@pytest.mark.unit
class TestThePerDatabaseSchemaQueryPagesToExhaustion:
    def test_a_field_declared_on_a_later_page_is_in_the_aggregate(self):
        """The defect: an un-paged call returns page one and nothing says the rest exists.

        `lateRequiredField` is only on page three. Without paging it is absent from the aggregate,
        so it is not required, it is off-schema under restrictMetadataOutsideSchemas, and its
        controlled list constrains nothing — with a 200 response.
        """
        client = _PagingClient([
            _page(["firstPageField"], last_key={"metadataSchemaId": "p1"}),
            _page(["secondPageField"], last_key={"metadataSchemaId": "p2"}),
            _page(["lateRequiredField"]),
        ])

        aggregated = get_aggregated_schemas(
            database_ids=["db1"],
            entity_type=_ENTITY,
            file_path=None,
            dynamodb_client=client,
            schema_table_name=_TABLE,
        )

        assert "lateRequiredField" in aggregated, (
            f"only the first page of schemas was read, so the aggregate is missing every schema "
            f"beyond page one: {sorted(aggregated)}")
        # Positive control: the earlier pages are still there, so the fix did not merely replace
        # page one with the last page.
        assert {"firstPageField", "secondPageField"} <= set(aggregated), sorted(aggregated)
        assert aggregated["lateRequiredField"]["required"] is True
        assert len(client.calls) <= QUERY_CALL_CEILING, (
            f"the paging loop issued {len(client.calls)} queries for three pages")

    def test_the_cursor_from_each_page_is_sent_back(self):
        """Asserted over the set of reads rather than at a pinned call index."""
        client = _PagingClient([
            _page(["a"], last_key={"metadataSchemaId": "p1"}),
            _page(["b"], last_key={"metadataSchemaId": "p2"}),
            _page(["c"]),
        ])

        get_aggregated_schemas(
            database_ids=["db1"],
            entity_type=_ENTITY,
            file_path=None,
            dynamodb_client=client,
            schema_table_name=_TABLE,
        )

        sent = [call.get("ExclusiveStartKey") for call in client.calls]
        for cursor in ({"metadataSchemaId": "p1"}, {"metadataSchemaId": "p2"}):
            assert cursor in sent, (
                f"a page's LastEvaluatedKey was never sent back as ExclusiveStartKey: {sent}")

    def test_a_single_complete_page_ends_the_loop(self):
        """Positive control for the loop's exit: the common case must not query twice forever.

        The final page carries no LastEvaluatedKey at all, so an implementation that paged on the
        VALUE of the key rather than its presence would still work here — but one that never
        terminated would trip the stub's own guard.
        """
        client = _PagingClient([_page(["onlyField"])])

        aggregated = get_aggregated_schemas(
            database_ids=["db1"],
            entity_type=_ENTITY,
            file_path=None,
            dynamodb_client=client,
            schema_table_name=_TABLE,
        )

        assert "onlyField" in aggregated
        assert len(client.calls) <= QUERY_CALL_CEILING

    def test_each_database_in_scope_is_paged_independently(self):
        """A GLOBAL schema on the second database's later page must reach the aggregate too."""
        client = _PagingClient([
            _page(["dbField"], database_id="db1", last_key={"metadataSchemaId": "p1"}),
            _page(["dbLateField"], database_id="db1"),
            _page(["globalField"], database_id="GLOBAL", last_key={"metadataSchemaId": "g1"}),
            _page(["globalLateField"], database_id="GLOBAL"),
        ])

        aggregated = get_aggregated_schemas(
            database_ids=["db1", "GLOBAL"],
            entity_type=_ENTITY,
            file_path=None,
            dynamodb_client=client,
            schema_table_name=_TABLE,
        )

        assert {"dbField", "dbLateField", "globalField", "globalLateField"} <= set(aggregated), (
            sorted(aggregated))


def _pages(count):
    """`count` pages of one distinct schema field each; only the last omits LastEvaluatedKey."""
    return [
        _page([f"field{index}"],
              last_key=None if index == count - 1 else {"metadataSchemaId": f"p{index}"})
        for index in range(count)
    ]


def _paging_client(pages):
    """A DynamoDB client whose `query` is a shared `Pager` over `pages`."""
    client = MagicMock()
    pager = Pager(*pages, name="metadata schema query", max_reads=RUNAWAY_READS)
    client.query.side_effect = pager
    return client, pager


@pytest.mark.unit
class TestThePagingWalkIsItselfBounded:
    """Paging to exhaustion needs a bound, and the bound has to report itself.

    This lookup is on the SYNCHRONOUS metadata create/update path: on a cache miss, one write pays for
    every page of every database in scope, sequentially. Unbounded, one oversized schema set makes a
    single write issue an arbitrary number of queries.

    The bound cannot be a silent truncation either. The pages read so far are a partial aggregate, and
    a partial aggregate is precisely what turns `restrictMetadataOutsideSchemas` and the required-field
    check off (`if aggregated_schema:` opens on a non-empty aggregate; a field that is missing is
    simply not required) -- the defect the paging was added to close. So the cap raises
    `SchemaLookupError`, which the callers already translate: write paths refuse, delete paths refuse,
    read paths degrade to un-enriched output.
    """

    def test_the_sequential_queries_one_write_pays_for_are_bounded(self):
        """The cost half, asserted without depending on the refusal.

        `PAGES_BEYOND_THE_CAP` pages are available and every one is readable, so a walk that simply
        pages to the end reads all of them -- more than the ceiling -- inside one synchronous write.
        The refusal is suppressed here so this assertion, and not the raise, is what fails.
        """
        client, pager = _paging_client(_pages(PAGES_BEYOND_THE_CAP))

        with contextlib.suppress(SchemaLookupError):
            get_aggregated_schemas(
                database_ids=["db1"],
                entity_type=_ENTITY,
                file_path=None,
                dynamodb_client=client,
                schema_table_name=_TABLE,
            )

        assert len(pager.calls) <= QUERY_CALL_CEILING, (
            f"one metadata write issued {len(pager.calls)} sequential schema queries; the walk is "
            f"not bounded")

    def test_a_walk_that_cannot_finish_within_the_cap_refuses_the_pages_it_did_read(self):
        """The visibility half: stopping early must not look like a complete answer.

        Returning the pages it managed to read hands back a NON-EMPTY aggregate, which opens the
        callers' `if aggregated_schema:` guard and applies restrictMetadataOutsideSchemas against a
        field set missing every page beyond the cap.
        """
        client, _ = _paging_client(_pages(PAGES_BEYOND_THE_CAP))

        with pytest.raises(SchemaLookupError):
            get_aggregated_schemas(
                database_ids=["db1"],
                entity_type=_ENTITY,
                file_path=None,
                dynamodb_client=client,
                schema_table_name=_TABLE,
            )

    def test_the_pages_a_capped_walk_read_are_not_cached(self):
        """The same reason the failed-query case is not cached: 60 seconds of degraded validation."""
        client, _ = _paging_client(_pages(PAGES_BEYOND_THE_CAP))

        with pytest.raises(SchemaLookupError):
            get_aggregated_schemas(
                database_ids=["db1"],
                entity_type=_ENTITY,
                file_path=None,
                dynamodb_client=client,
                schema_table_name=_TABLE,
            )

        assert msv._schema_cache == {}, (
            f"a truncated walk was cached as the answer: {msv._schema_cache}")

    def test_a_multi_page_walk_that_finishes_within_the_cap_is_still_complete(self):
        """Positive control, at the boundary: the cap must not fire one page early.

        Exactly `MAX_SCHEMA_QUERY_PAGES` pages, the last of them final. The walk must return the whole
        aggregate rather than refusing a schema set it was able to read -- refusing it would take
        metadata writes down on that database entirely.
        """
        client, pager = _paging_client(_pages(MAX_SCHEMA_QUERY_PAGES))

        aggregated = get_aggregated_schemas(
            database_ids=["db1"],
            entity_type=_ENTITY,
            file_path=None,
            dynamodb_client=client,
            schema_table_name=_TABLE,
        )

        assert {f"field{index}" for index in range(MAX_SCHEMA_QUERY_PAGES)} <= set(aggregated), (
            f"a walk that fits inside the cap lost fields: {sorted(aggregated)}")
        pager.assert_paged_to_exhaustion()


@pytest.mark.unit
class TestAPartialMultiPageReadIsNeverTheAnswer:
    def test_a_failure_on_a_later_page_raises_instead_of_returning_page_one(self):
        """The first page is genuinely readable; the second throttles.

        An implementation that returned what it had would hand back a NON-EMPTY aggregate, which
        opens the handlers' `if aggregated_schema:` guard and applies
        restrictMetadataOutsideSchemas against a field set missing every later page.
        """
        client = _PagingClient([
            _page(["firstPageField"], last_key={"metadataSchemaId": "p1"}),
            THROTTLE,
        ])

        with pytest.raises(SchemaLookupError):
            get_aggregated_schemas(
                database_ids=["db1"],
                entity_type=_ENTITY,
                file_path=None,
                dynamodb_client=client,
                schema_table_name=_TABLE,
            )

        assert len(client.calls) >= 2, (
            f"the second page was never requested, so this is the single-call case again rather "
            f"than a partial multi-page read: {len(client.calls)} queries")

    def test_a_partial_multi_page_read_is_not_cached(self):
        """Caching it would extend one incomplete read into 60 seconds of degraded validation."""
        client = _PagingClient([
            _page(["firstPageField"], last_key={"metadataSchemaId": "p1"}),
            THROTTLE,
        ])

        with pytest.raises(SchemaLookupError):
            get_aggregated_schemas(
                database_ids=["db1"],
                entity_type=_ENTITY,
                file_path=None,
                dynamodb_client=client,
                schema_table_name=_TABLE,
            )

        assert msv._schema_cache == {}, (
            f"a partial multi-page read was cached as the answer: {msv._schema_cache}")

        # And the retry really re-reads rather than being served the partial aggregate.
        recovered = _PagingClient([
            _page(["firstPageField"], last_key={"metadataSchemaId": "p1"}),
            _page(["lateRequiredField"]),
        ])
        aggregated = get_aggregated_schemas(
            database_ids=["db1"],
            entity_type=_ENTITY,
            file_path=None,
            dynamodb_client=recovered,
            schema_table_name=_TABLE,
        )
        assert recovered.calls, "the retry was served from the cache"
        assert "lateRequiredField" in aggregated, sorted(aggregated)

    def test_a_complete_multi_page_read_is_cached(self):
        """Positive control for the assertion above: caching itself still happens."""
        client = _PagingClient([
            _page(["firstPageField"], last_key={"metadataSchemaId": "p1"}),
            _page(["lateRequiredField"]),
        ])
        get_aggregated_schemas(
            database_ids=["db1"],
            entity_type=_ENTITY,
            file_path=None,
            dynamodb_client=client,
            schema_table_name=_TABLE,
        )

        assert msv._schema_cache, "a complete multi-page lookup was not cached at all"

        second = MagicMock()
        second.query.side_effect = THROTTLE
        aggregated = get_aggregated_schemas(
            database_ids=["db1"],
            entity_type=_ENTITY,
            file_path=None,
            dynamodb_client=second,
            schema_table_name=_TABLE,
        )
        assert {"firstPageField", "lateRequiredField"} <= set(aggregated), sorted(aggregated)
        assert not second.query.called, "the cached aggregate was not used"
