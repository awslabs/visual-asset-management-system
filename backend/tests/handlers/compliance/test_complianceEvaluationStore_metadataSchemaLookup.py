# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceEvaluationStore.get_metadata_schema: how a metadata rule's `metadataSchemaRef` resolves.

The exact-type lookup (DatabaseIdMetadataEntityTypeIndex, the rule's database then GLOBAL) runs
first. On a miss the name is looked up under every entity type (DatabaseIdIndex, database then
GLOBAL), so a schema that exists under another type is reported as an entity-type mismatch instead of
as not found. Each test feeds real metadata-schema rows through the shared `Pager` / `RoutedPager`
stubs, one scripted page sequence per index partition: a scripted partition must be read (its pager's
read floor), an unscripted one must not be (it raises), and order is asserted as precedence between
first reads rather than as an exact call sequence."""

from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import ASSET, DB, SCHEMA, USER
from backend.tests.handlers.compliance.test_complianceEvaluationStore import (
    REQUIRE_UNITS, STORE, Tables, _file_metadata_rows, _scoped_body, metadata_schema_row,
)
from backend.tests.pagingStub import Pager, PagingLoopDidNotTerminate, RoutedPager
from handlers.compliance import complianceEvaluationStore as store

EXACT = "DatabaseIdMetadataEntityTypeIndex"
BY_DATABASE = "DatabaseIdIndex"
SCHEMA_NAME = "Model Schema"


class _ByPartition:
    """One GSI of the metadata schema table, each key-condition partition paged through its own
    scripted `Pager`, so a database read and its GLOBAL fallback page independently of call order.
    A partition left unscripted is a read the test does not expect and raises."""

    def __init__(self, index_name, pagers):
        self.index_name = index_name
        self.pagers = pagers

    def __call__(self, **kwargs):
        partition = kwargs["KeyConditionExpression"]._values[1]
        pager = self.pagers.get(partition)
        if pager is None:
            raise PagingLoopDidNotTerminate(
                f"unscripted read of {self.index_name} partition {partition!r}")
        return pager(**kwargs)

    def assert_paged_to_exhaustion(self):
        for pager in self.pagers.values():
            pager.assert_paged_to_exhaustion()


class _SchemaLookup:
    """The metadata schema table's `query`, as a `RoutedPager` on `IndexName` over the two lookup
    GSIs: `exact` scripts the DatabaseIdMetadataEntityTypeIndex partitions (`<db>:<entity type>`),
    `by_database` the DatabaseIdIndex partitions (`<db>`), each a `Pager` of post-filter pages.
    `first_read` maps `(index, partition)` to the position of its first read; `name_filters` holds
    the `(attribute, value)` of every read's FilterExpression."""

    def __init__(self, exact=None, by_database=None):
        self.routed = RoutedPager(on="IndexName", **{
            EXACT: _ByPartition(EXACT, exact or {}),
            BY_DATABASE: _ByPartition(BY_DATABASE, by_database or {}),
        })
        self.first_read = {}
        self.name_filters = set()

    def __call__(self, **kwargs):
        partition = (kwargs.get("IndexName"), kwargs["KeyConditionExpression"]._values[1])
        self.first_read.setdefault(partition, len(self.first_read))
        attribute, value = kwargs["FilterExpression"]._values
        self.name_filters.add((attribute.name, value))
        return self.routed(**kwargs)

    def assert_paged_to_exhaustion(self):
        self.routed.assert_paged_to_exhaustion()


def _no_rows():
    return Pager({"Items": []}, name="metadata schema partition")


def _exact_misses(entity_type="fileMetadata"):
    """Both exact-type partitions, each holding no schema of the name."""
    return {f"{DB}:{entity_type}": _no_rows(), f"GLOBAL:{entity_type}": _no_rows()}


def _files_rule_tables(lookup, file_rows=()):
    """A `Tables` whose schema holds one files-scope metadata rule `per_file` naming `Model Schema`
    in DB, the asset holding `/models/part.glb` and `/models/plain.glb`, and the metadata schema
    table answering through `lookup`."""
    tables = Tables(schema_body=_scoped_body(per_file=("files", REQUIRE_UNITS, {})),
                    asset_files=("/models/part.glb", "/models/plain.glb"))
    asset_rows = [{"metadataKey": "owner", "metadataValue": "v", "metadataValueType": "STRING",
                   "databaseId:assetId:filePath": f"{DB}:{ASSET}:/"}]
    tables.metadata.query.side_effect = lambda **kw: {"Items": list(
        file_rows) + asset_rows if kw.get("IndexName") == "DatabaseIdAssetIdIndex" else asset_rows}
    tables.metadata_schema.query.side_effect = lookup
    return tables


def _per_file_result(tables):
    result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
    return result, next(r for r in result["ruleResults"] if r["ruleName"] == "per_file")


@pytest.mark.unit
class TestMetadataSchemaLookup:

    def test_a_schema_of_the_name_under_another_entity_type_reports_the_mismatch(self):
        """A files-scope rule naming an assetMetadata schema of its database. Neither exact-type
        partition holds the name; the database's DatabaseIdIndex partition returns the schema row,
        as the metadata schema service writes it, on the page after one the filter emptied."""
        lookup = _SchemaLookup(exact=_exact_misses(), by_database={DB: Pager(
            {"Items": [], "LastEvaluatedKey": {"metadataSchemaId": "id-a", "databaseId": DB}},
            {"Items": [metadata_schema_row(SCHEMA_NAME, "assetMetadata",
                                           fields=[("units", "STRING", True)])]},
            name="DatabaseIdIndex DB")})
        result, rule = _per_file_result(_files_rule_tables(lookup))
        assert rule["status"] == "error"
        assert rule["message"] == store.engine.METADATA_SCHEMA_SCOPE_MISMATCH_MESSAGE
        assert "per_file" in result["errorRules"]
        lookup.assert_paged_to_exhaustion()
        order = lookup.first_read
        assert order[(EXACT, f"{DB}:fileMetadata")] < order[(EXACT, "GLOBAL:fileMetadata")]
        assert order[(EXACT, "GLOBAL:fileMetadata")] < order[(BY_DATABASE, DB)]
        # GLOBAL's DatabaseIdIndex partition is unscripted, so reading it would have raised: the
        # database's row ends the lookup.
        assert (BY_DATABASE, "GLOBAL") not in order
        assert ("schemaName", SCHEMA_NAME) in lookup.name_filters

    def test_a_global_schema_of_the_name_under_another_entity_type_reports_the_mismatch(self):
        lookup = _SchemaLookup(exact=_exact_misses(), by_database={
            DB: _no_rows(),
            "GLOBAL": Pager({"Items": [metadata_schema_row(
                SCHEMA_NAME, "assetLinkMetadata", database_id="GLOBAL")]})})
        _, rule = _per_file_result(_files_rule_tables(lookup))
        assert rule["status"] == "error"
        assert rule["message"] == store.engine.METADATA_SCHEMA_SCOPE_MISMATCH_MESSAGE
        lookup.assert_paged_to_exhaustion()
        order = lookup.first_read
        assert order[(EXACT, "GLOBAL:fileMetadata")] < order[(BY_DATABASE, DB)]
        assert order[(BY_DATABASE, DB)] < order[(BY_DATABASE, "GLOBAL")]

    def test_a_name_no_schema_of_any_entity_type_carries_is_not_found(self):
        """Every scripted partition is read (each pager's read floor) and none holds the name."""
        lookup = _SchemaLookup(exact=_exact_misses(),
                               by_database={DB: _no_rows(), "GLOBAL": _no_rows()})
        _, rule = _per_file_result(_files_rule_tables(lookup))
        assert rule["status"] == "error"
        assert rule["message"] == store.engine.METADATA_SCHEMA_NOT_FOUND_MESSAGE
        lookup.assert_paged_to_exhaustion()
        order = lookup.first_read
        assert order[(EXACT, "GLOBAL:fileMetadata")] < order[(BY_DATABASE, DB)]

    def test_a_global_schema_of_the_scope_s_type_wins_over_a_database_schema_of_another(self):
        """The exact-type lookup, database then GLOBAL, runs before any other type is considered:
        a GLOBAL fileMetadata schema resolves a files rule although the database also holds an
        assetMetadata schema of the name. No DatabaseIdIndex partition is scripted, so a read of
        that index would have raised."""
        lookup = _SchemaLookup(exact={
            f"{DB}:fileMetadata": _no_rows(),
            "GLOBAL:fileMetadata": Pager({"Items": [metadata_schema_row(
                SCHEMA_NAME, "fileMetadata", fields=[("units", "STRING", True)],
                database_id="GLOBAL")]})})
        _, rule = _per_file_result(_files_rule_tables(
            lookup, file_rows=_file_metadata_rows("/models/part.glb", units="mm")))
        assert rule["status"] == "evaluated"
        assert rule["passed"] is False
        assert {"/models/plain.glb"} == {unit["path"] for unit in rule["measured"]["failing"]}
        lookup.assert_paged_to_exhaustion()
        assert {index for index, _ in lookup.first_read} == {EXACT}

    @pytest.mark.parametrize("reverse", [False, True], ids=["index-order", "reversed"])
    def test_several_schemas_of_other_types_report_one_stable_mismatch(self, reverse):
        """Every same-named schema of another type is a mismatch; which one is reported does not
        depend on the order the index returns them, and a row of the requested type that only the
        DatabaseIdIndex read returns is not taken as a match."""
        rows = [metadata_schema_row(SCHEMA_NAME, "assetMetadata"),
                metadata_schema_row(SCHEMA_NAME, "fileMetadata", fields=[("units", "STRING", True)]),
                metadata_schema_row(SCHEMA_NAME, "assetLinkMetadata")]
        lookup = _SchemaLookup(exact=_exact_misses(), by_database={
            DB: Pager({"Items": list(reversed(rows)) if reverse else rows})})
        table = MagicMock(name="metadata_schema_table")
        table.query.side_effect = lookup
        with patch(f"{STORE}.metadata_schema_table", table):
            schema = store.get_metadata_schema(DB, SCHEMA_NAME, "fileMetadata")
        assert schema["entityType"] == "assetLinkMetadata"
        lookup.assert_paged_to_exhaustion()
