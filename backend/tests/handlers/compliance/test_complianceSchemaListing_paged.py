# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`GET /compliance/schemas` reads the latest version of each schema only, a page at a time.

The listing used to scan every version of every schema, with its body, and answer one unpaged
list. Now only a schema's latest version row carries the latest-version marker (a save moves it to
the new version in one transaction), the listing reads LatestVersionIndex a page at a time
(`maxItems`, `startingToken`, `NextToken`), and rows saved before the marker existed are marked
once by the first listing, conditionally and only where the marker is missing.

Run against moto tables built from the CDK definitions (the index included), through the schema
service's handler.
"""

import base64
import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    RULES_SCHEMA_BODY, USER, body_of, claims_for, enforcer, rest_event, schema_row,
)
from handlers.compliance import complianceSchemaService as svc

DB_A = "listing-a"


@pytest.fixture
def tables():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, (svc,))
        for active in patches:
            active.start()
        try:
            doubles["databaseStorage"].put_item(Item={"databaseId": DB_A})
            yield doubles
        finally:
            for active in reversed(patches):
                active.stop()


def _call(method, path, params=None, body=None, query=None):
    with patch.object(svc, "request_to_claims", claims_for(USER)), \
            patch.object(svc, "CasbinEnforcer", return_value=enforcer()), \
            patch.object(svc, "may_run_workflow", return_value=True):
        response = svc.lambda_handler(
            rest_event(method, path, params or {}, body=body, query_params=query), MagicMock())
    return response["statusCode"], body_of(response)


def _save(name, database_id="GLOBAL"):
    status, body = _call("POST", "/compliance/schemas", body={
        "schemaName": name, "databaseId": database_id, "schemaBody": RULES_SCHEMA_BODY})
    assert status == 200, body
    return body["internalVersion"]


def _list(**query):
    return _call("GET", "/compliance/schemas", query={k: str(v) for k, v in query.items()})


def _walk(**query):
    """Every page of the listing, following NextToken."""
    pages, token = [], None
    for _ in range(20):
        status, body = _list(**query, **({"startingToken": token} if token else {}))
        assert status == 200, body
        pages.append(body)
        token = body.get("NextToken")
        if not token:
            return pages
    raise AssertionError("the listing did not end")


def _marked(tables):
    return sorted((row["schemaName"], int(row["internalVersion"])) for row in
                  tables["complianceSchemaStorage"].scan()["Items"]
                  if svc.LATEST_VERSION_ATTRIBUTE in row)


def _token(key):
    return base64.b64encode(json.dumps(key).encode()).decode()


@pytest.mark.unit
class TestTheListingReadsLatestVersionsAPageAtATime:

    def test_each_page_holds_latest_versions_only_and_the_walk_ends(self, tables):
        _save("s-a"), _save("s-a")
        _save("s-b")
        _save("s-c"), _save("s-c"), _save("s-c")
        pages = _walk(maxItems=1)
        listed = [(s["schemaName"], s["version"]) for page in pages for s in page["schemas"]]
        assert listed == [("s-a", 2), ("s-b", 1), ("s-c", 3)]
        assert all(len(page["schemas"]) == 1 for page in pages)
        assert "NextToken" not in pages[-1]

    def test_a_save_moves_the_marker_so_one_row_per_schema_is_marked(self, tables):
        _save("s-a"), _save("s-a"), _save("s-a")
        assert _marked(tables) == [("s-a", 3)]

    def test_the_listing_reads_no_earlier_version(self, tables):
        _save("s-a"), _save("s-a")
        _list()  # the one-time backfill check reads the table once, on the first listing
        real_query = tables["complianceSchemaStorage"].query
        read = []

        def query(**kwargs):
            response = real_query(**kwargs)
            read.extend(int(item["internalVersion"]) for item in response["Items"])
            return response
        with patch.object(svc.schema_table, "query", side_effect=query), \
                patch.object(svc.schema_table, "scan", side_effect=AssertionError("scanned")):
            status, body = _list()
        assert status == 200
        assert read == [2]

    def test_a_database_filter_lists_global_and_that_databases_schemas(self, tables):
        _save("global-one")
        _save("own-one", DB_A)
        status, body = _list(databaseId=DB_A)
        assert status == 200, body
        assert sorted(s["schemaName"] for s in body["schemas"]) == ["global-one", "own-one"]
        assert "createScopes" not in body
        status, body = _list(databaseId="GLOBAL")
        assert [s["schemaName"] for s in body["schemas"]] == ["global-one"]

    def test_the_unfiltered_listing_carries_the_create_scopes(self, tables):
        _save("s-a")
        status, body = _list()
        assert status == 200 and body["createScopes"] == ["GLOBAL", DB_A]


@pytest.mark.unit
class TestTheListingTokenAndPageSize:

    @pytest.mark.parametrize("token", [
        "not base64!",
        _token(["latest", "s-a", 1]),
        _token({"latestVersion": "latest", "schemaName": "s-a"}),
        _token({"latestVersion": "other", "schemaName": "s-a", "internalVersion": 1}),
        _token({"latestVersion": "latest", "schemaName": "s-a", "internalVersion": "1"}),
        _token({"latestVersion": "latest", "schemaName": "s-a", "internalVersion": True}),
        _token({"databaseId": "GLOBAL", "schemaName": "s-a", "internalVersion": 1}),
    ], ids=["not-base64", "not-an-object", "missing-version", "another-partition",
            "version-as-text", "version-as-bool", "another-indexs-key"])
    def test_a_token_that_is_not_this_listings_is_refused(self, tables, token):
        status, body = _list(startingToken=token)
        assert (status, body["message"]) == (400, "Invalid pagination token")

    def test_a_page_size_that_is_not_a_number_is_refused(self, tables):
        status, body = _list(maxItems="many")
        assert (status, body["message"]) == (400, "maxItems must be an integer")


@pytest.mark.unit
class TestRowsSavedBeforeTheMarkerAreMarkedOnce:

    def _legacy(self, tables, name, versions):
        for version in versions:
            tables["complianceSchemaStorage"].put_item(Item=schema_row(name, version=version))

    def test_the_first_listing_marks_the_latest_version_and_records_it(self, tables):
        self._legacy(tables, "old-a", (1, 2))
        self._legacy(tables, "old-b", (1,))
        status, body = _list()
        assert status == 200, body
        assert [(s["schemaName"], s["version"]) for s in body["schemas"]] == [
            ("old-a", 2), ("old-b", 1)]
        assert _marked(tables) == [("old-a", 2), ("old-b", 1)]
        assert tables["complianceSchemaStorage"].get_item(
            Key=svc.LATEST_MARKER_BACKFILL_KEY).get("Item")

    def test_the_check_after_marking_reads_consistently(self, tables):
        # A save that committed just before the check must be seen, or its predecessor keeps the
        # marker for good.
        self._legacy(tables, "old-a", (1,))
        real_query = tables["complianceSchemaStorage"].query
        reads = []

        def query(**kwargs):
            if "IndexName" not in kwargs:
                reads.append(kwargs.get("ConsistentRead"))
            return real_query(**kwargs)
        with patch.object(svc.schema_table, "query", side_effect=query):
            status, _ = _list()
        assert status == 200
        assert reads == [True]

    def test_a_later_listing_reads_nothing_for_it(self, tables):
        self._legacy(tables, "old-a", (1,))
        _list()
        with patch.object(svc.schema_table, "scan", side_effect=AssertionError("scanned")):
            status, _ = _list()
        assert status == 200

    def test_a_marker_on_an_earlier_version_is_moved_to_the_latest(self, tables):
        self._legacy(tables, "old-a", (1, 2))
        tables["complianceSchemaStorage"].update_item(
            Key={"schemaName": "old-a", "internalVersion": 1},
            UpdateExpression="SET latestVersion = :m", ExpressionAttributeValues={":m": "latest"})
        _list()
        assert _marked(tables) == [("old-a", 2)]

    def test_a_backfill_out_of_time_marks_what_it_reached_and_the_next_listing_goes_on(self, tables):
        self._legacy(tables, "old-a", (1,))
        self._legacy(tables, "old-b", (1,))
        readings = iter([0, 0, svc.LATEST_MARKER_BACKFILL_SECONDS + 1])
        with patch.object(svc, "time") as clock:
            clock.monotonic.side_effect = lambda: next(readings, svc.LATEST_MARKER_BACKFILL_SECONDS + 1)
            status, _ = _list()
        assert status == 200
        assert len(_marked(tables)) == 1
        assert not tables["complianceSchemaStorage"].get_item(
            Key=svc.LATEST_MARKER_BACKFILL_KEY).get("Item")
        status, body = _list()
        assert sorted(s["schemaName"] for s in body["schemas"]) == ["old-a", "old-b"]
        assert _marked(tables) == [("old-a", 1), ("old-b", 1)]

    def test_the_backfill_row_is_never_listed_or_found_as_a_schema(self, tables):
        self._legacy(tables, "old-a", (1,))
        _list()
        _, body = _list()
        assert [s["schemaName"] for s in body["schemas"]] == ["old-a"]


@pytest.mark.unit
class TestTwoSavesOfOneVersion:

    def test_the_save_that_loses_the_version_is_refused_and_one_marker_stays(self, tables):
        _save("s-x")
        current = svc._latest_schema_item("s-x")
        with patch.object(svc, "_latest_schema_item", return_value=current), \
                patch.object(svc, "claims_and_roles", {"tokens": [USER], "roles": []}):
            first = svc._write_schema_version(MagicMock(), "s-x", "GLOBAL", "", RULES_SCHEMA_BODY,
                                              False)
            second = svc._write_schema_version(MagicMock(), "s-x", "GLOBAL", "", RULES_SCHEMA_BODY,
                                               False)
        assert first["statusCode"] == 200
        assert second["statusCode"] == 409
        assert _marked(tables) == [("s-x", 2)]


@pytest.mark.unit
class TestTheListingWhileTheIndexIsBuilt:
    """An update that adds LatestVersionIndex completes while DynamoDB still builds it; the
    listing then reads the latest versions without it, whole, as it did before the index."""

    def _building(self, tables, message="Cannot read from backfilling global secondary index"):
        real_query = tables["complianceSchemaStorage"].query

        def query(**kwargs):
            if kwargs.get("IndexName") == svc.LATEST_VERSION_INDEX:
                raise ClientError({"Error": {"Code": "ValidationException", "Message": message}},
                                  "Query")
            return real_query(**kwargs)
        return patch.object(svc.schema_table, "query", side_effect=query)

    def test_the_first_page_lists_every_latest_version_with_no_token(self, tables):
        _save("s-a"), _save("s-a")
        _save("s-b", DB_A)
        with self._building(tables):
            status, body = _list(maxItems=1)
        assert status == 200, body
        assert [(s["schemaName"], s["version"]) for s in body["schemas"]] == [("s-a", 2), ("s-b", 1)]
        assert "NextToken" not in body and body["createScopes"] == ["GLOBAL", DB_A]

    def test_a_database_filter_reads_global_and_that_database(self, tables):
        _save("global-one")
        _save("own-one", DB_A)
        with self._building(tables):
            status, body = _list(databaseId="GLOBAL")
        assert status == 200, body
        assert [s["schemaName"] for s in body["schemas"]] == ["global-one"]

    def test_another_read_error_is_not_hidden(self, tables):
        _save("s-a")
        with self._building(tables, message="Some other problem"):
            status, _ = _list()
        assert status == 500


@pytest.mark.unit
class TestAFreshDeployment:
    """A first deploy: the schema table is empty when the default-schema seed runs, so no row
    predates the marker. The seeded row (as `dynamodb-complianceschema-defaults-construct.ts`
    passes it, with `latestVersion`) is in LatestVersionIndex at once, and the first listing finds
    it there; the one-time backfill finds nothing to mark and records that it ran."""

    SEED = {"schemaName": {"S": "default-compliance-schema"}, "internalVersion": {"N": "1"},
            "databaseId": {"S": "GLOBAL"}, "registeredBy": {"S": "SYSTEM_USER"},
            "isSystem": {"BOOL": True}, "latestVersion": {"S": "latest"},
            "schemaBody": {"S": json.dumps(RULES_SCHEMA_BODY)}}

    def test_the_seeded_schema_is_listed_by_the_first_listing(self, tables):
        from handlers.compliance import complianceDefaultSchemaSeed as seed_module
        table = tables["complianceSchemaStorage"]
        assert table.scan()["Items"] == []
        client = boto3.client("dynamodb", region_name="us-east-1")
        with patch.object(seed_module, "dynamodb_client", client):
            assert seed_module.seed({"TableName": table.name, "Item": json.dumps(self.SEED)})
        index = table.query(IndexName=svc.LATEST_VERSION_INDEX,
                            KeyConditionExpression=svc.Key("latestVersion").eq("latest"))["Items"]
        assert [row["schemaName"] for row in index] == ["default-compliance-schema"]

        status, body = _list()
        assert status == 200, body
        assert [(s["schemaName"], s["version"]) for s in body["schemas"]] == [
            ("default-compliance-schema", 1)]
        assert _marked(tables) == [("default-compliance-schema", 1)]
        assert table.get_item(Key=svc.LATEST_MARKER_BACKFILL_KEY).get("Item")
