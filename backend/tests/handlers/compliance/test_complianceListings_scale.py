# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The compliance listings at scale: a database of 5,000 tracked assets and an audit trail of
20,000 entries, in a DynamoDB double built from the CDK table definitions.

Each listing reads with a key condition on an index and fills a page across a bounded number of
reads (`listing.query_page`, `listing.DEFAULT_READ_BUDGET`): the first page and a selective
filter read at most the budget, never the whole database or trail, and the overview's counts are
`Select=COUNT` queries that return no rows. The read counts per listing are printed (`READS ...`)
for the record. Also: the page-fill and token rules of `query_page` on a scripted table."""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance._harness import (
    COMPLIANCE_ASSET_STATE_TABLE_DEFINITION, USER, body_of, claims_for, database_table_with,
    enforcer, rest_event,
)
from common.compliance import listing
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceQuarantineService as quarantine_svc

DB = "scale-db"
OTHER_DB = "other-db"
STATE_ROWS = 5_000
AUDIT_ROWS = 20_000
STATES = ["compliant", "compliant", "compliant", "non_compliant", "quarantined",
          "pending_evaluation", "exception", "unknown"]
EVENT_TYPES = ["compliance_check", "compliance_check", "compliance_check", "exception_granted",
               "quarantine_released", "schema_bound_to_database", "cascade_triggered",
               "evaluation_error"]

AUDIT_TABLE_DEFINITION = {
    "TableName": "t-compliance-audit",
    "KeySchema": [{"AttributeName": "entryId", "KeyType": "HASH"}],
    "AttributeDefinitions": [
        {"AttributeName": "entryId", "AttributeType": "S"},
        {"AttributeName": "databaseId:assetId", "AttributeType": "S"},
        {"AttributeName": "timestamp", "AttributeType": "S"},
        {"AttributeName": "eventType", "AttributeType": "S"},
        {"AttributeName": "allListPartition", "AttributeType": "S"},
    ],
    "GlobalSecondaryIndexes": [
        {"IndexName": name, "KeySchema": [{"AttributeName": pk, "KeyType": "HASH"},
                                          {"AttributeName": "timestamp", "KeyType": "RANGE"}],
         "Projection": {"ProjectionType": "ALL"}}
        for name, pk in (("AssetIndex", "databaseId:assetId"), ("EventTypeIndex", "eventType"),
                         ("AuditByDateGSI", "allListPartition"))
    ],
    "BillingMode": "PAY_PER_REQUEST",
}


class CountingTable:
    """A table whose `query` calls are counted (and otherwise delegated)."""

    def __init__(self, table):
        self._table = table
        self.reads = 0

    def query(self, **kwargs):
        self.reads += 1
        return self._table.query(**kwargs)

    def __getattr__(self, name):
        return getattr(self._table, name)


def _state_row(database_id, index):
    asset_id = f"x{index:05d}"
    name = f"Bracket {index:05d}" if index % 50 else f"Turbine Blade {index:05d}"
    return {"databaseId": database_id, "assetId": asset_id, "schemaName": "std",
            "schemaSource": "asset" if index % 97 == 0 else "database",
            "complianceState": STATES[index % len(STATES)],
            "lastEvaluationStatus": "error" if index % 41 == 0 else "completed",
            **listing.asset_name_attributes(asset_id, name)}


def _audit_row(index):
    database_id = DB if index % 4 else OTHER_DB
    asset_id = f"x{index % STATE_ROWS:05d}"
    from common.compliance.auditRecord import build_audit_item
    item = build_audit_item(database_id, asset_id, EVENT_TYPES[index % len(EVENT_TYPES)],
                            "reviewer" if index % 1000 else "auditor-7", {"i": index},
                            timestamp=f"2026-10-{1 + index % 28:02d}T{index % 24:02d}:00:{index % 60:02d}+00:00",
                            asset_name=f"Bracket {index % STATE_ROWS:05d}")
    item["entryId"] = f"e{index:06d}"
    return item


@pytest.fixture(scope="module")
def tables():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        state = resource.create_table(**COMPLIANCE_ASSET_STATE_TABLE_DEFINITION)
        audit = resource.create_table(**AUDIT_TABLE_DEFINITION)
        with state.batch_writer() as batch:
            for index in range(STATE_ROWS):
                batch.put_item(Item=_state_row(DB, index))
            for index in range(500):
                batch.put_item(Item=_state_row(OTHER_DB, index))
        with audit.batch_writer() as batch:
            for index in range(AUDIT_ROWS):
                batch.put_item(Item=_audit_row(index))
        yield {"state": state, "audit": audit}


def _call(module, table_attribute, table, event):
    counting = CountingTable(table)
    databases = database_table_with(DB, OTHER_DB)
    with patch(f"{module.__name__}.request_to_claims", claims_for(USER)), \
            patch(f"{module.__name__}.CasbinEnforcer", return_value=enforcer()), \
            patch(f"{module.__name__}.{table_attribute}", counting), \
            patch.object(evaluate_svc.store, "database_table", databases), \
            patch.object(audit_svc, "database_table", databases):
        response = module.lambda_handler(event, MagicMock())
    assert response["statusCode"] == 200, response
    return body_of(response), counting.reads


def _overview(tables, **params):
    return _call(evaluate_svc, "asset_state_table", tables["state"],
                 rest_event("GET", f"/compliance/state/{DB}", {"databaseId": DB},
                            query_params=params or None))


# The overview's counts: one COUNT per state plus one for the error overlay; each COUNT pages at
# 1 MB of evaluated index data, so a few reads at 5,000 rows.
COUNT_READS_BOUND = (len(evaluate_svc.OVERVIEW_STATES) + 1) * 3


@pytest.mark.unit
class TestTheDatabaseOverviewAt5000Assets:

    def test_the_first_page_reads_one_page_and_counts_without_rows(self, tables):
        body, reads = _overview(tables, maxItems="100")
        assert len(body["assets"]) == 100 and "NextToken" in body
        assert body["totalAssets"] == STATE_ROWS
        assert sum(v for k, v in body["summary"].items() if k != "error") == STATE_ROWS
        assert body["summary"]["error"] == len([i for i in range(STATE_ROWS) if i % 41 == 0])
        assert body["assets"][0]["assetName"].startswith("Turbine Blade")
        assert "assetSearchText" not in body["assets"][0]
        assert reads <= 1 + COUNT_READS_BOUND
        print(f"READS overview first page (100 of 5,000): {reads} queries "
              f"(1 page + {reads - 1} COUNT)")

    def test_a_state_filter_reads_the_state_partition_of_the_database(self, tables):
        body, reads = _overview(tables, state="quarantined", maxItems="50")
        assert len(body["assets"]) == 50
        assert {a["complianceState"] for a in body["assets"]} == {"quarantined"}
        assert {a["databaseId"] for a in body["assets"]} == {DB}
        assert reads - COUNT_READS_BOUND <= 1
        print(f"READS overview state=quarantined: {reads} queries incl. counts")

    def test_a_selective_text_filter_stays_within_the_read_budget(self, tables):
        body, reads = _overview(tables, text="TURBINE", maxItems="100")
        names = [a["assetName"] for a in body["assets"]]
        assert names and all(name.startswith("Turbine Blade") for name in names)
        page_reads = reads - (len(evaluate_svc.OVERVIEW_STATES) + 1)
        assert page_reads <= listing.DEFAULT_READ_BUDGET + COUNT_READS_BOUND
        # 100 matches exist (every 50th asset); a page that stopped at the budget says so.
        total = len(names)
        token = body.get("NextToken")
        while token:
            body, _ = _overview(tables, text="turbine", maxItems="100", startingToken=token)
            total += len(body["assets"])
            token = body.get("NextToken")
        assert total == STATE_ROWS // 50
        print(f"READS overview text=turbine (100 of 5,000 match): {reads} queries incl. counts")

    def test_the_name_sort_lists_by_name(self, tables):
        body, _ = _overview(tables, sort="name", maxItems="20")
        names = [a["assetName"].lower() for a in body["assets"]]
        assert names == sorted(names)
        follow, _ = _overview(tables, sort="name", maxItems="20", startingToken=body["NextToken"])
        assert follow["assets"][0]["assetName"].lower() >= names[-1]

    def test_the_other_filters_combine(self, tables):
        body, _ = _overview(tables, schemaSource="asset", evaluationError="false", maxItems="200")
        assert body["assets"] and all(a["schemaSource"] == "asset" for a in body["assets"])
        assert all(a["lastEvaluationStatus"] != "error" for a in body["assets"])

    @pytest.mark.parametrize("params", [{"state": "nope"}, {"schemaSource": "x"},
                                        {"sort": "size"}, {"evaluationError": "maybe"},
                                        {"startingToken": "bm90LWpzb24="}])
    def test_a_bad_filter_or_token_is_refused(self, tables, params):
        with patch(f"{evaluate_svc.__name__}.request_to_claims", claims_for(USER)), \
                patch(f"{evaluate_svc.__name__}.CasbinEnforcer", return_value=enforcer()), \
                patch(f"{evaluate_svc.__name__}.asset_state_table", tables["state"]):
            response = evaluate_svc.lambda_handler(
                rest_event("GET", f"/compliance/state/{DB}", {"databaseId": DB},
                           query_params=params), MagicMock())
        assert response["statusCode"] == 400


@pytest.mark.unit
class TestTheQuarantineListAtScale:

    def test_one_database_reads_its_state_partition(self, tables):
        body, reads = _call(quarantine_svc, "asset_state_table", tables["state"],
                            rest_event("GET", "/compliance/quarantine",
                                       query_params={"databaseId": DB, "maxItems": "100"}))
        rows = body["quarantinedAssets"]
        assert len(rows) == 100 and {r["databaseId"] for r in rows} == {DB}
        assert all(r["assetName"] for r in rows)
        assert reads == 1
        print(f"READS quarantine databaseId (100 of 625): {reads} queries")

    def test_a_text_filter_stays_within_the_budget(self, tables):
        body, reads = _call(quarantine_svc, "asset_state_table", tables["state"],
                            rest_event("GET", "/compliance/quarantine",
                                       query_params={"text": "turbine", "maxItems": "100"}))
        assert all("turbine" in r["assetName"].lower() for r in body["quarantinedAssets"])
        assert reads <= listing.DEFAULT_READ_BUDGET
        print(f"READS quarantine text=turbine: {reads} queries")


def _audit(tables, **params):
    return _call(audit_svc, "audit_table", tables["audit"],
                 rest_event("GET", "/compliance/audit", query_params=params or None))


@pytest.mark.unit
class TestTheAuditTrailAt20000Entries:

    def test_the_first_page_is_one_read_of_the_date_index(self, tables):
        body, reads = _audit(tables, maxItems="100")
        stamps = [e["timestamp"] for e in body["entries"]]
        assert len(stamps) == 100 and stamps == sorted(stamps, reverse=True)
        assert body["entries"][0]["assetName"].startswith("Bracket")
        assert "searchText" not in body["entries"][0]
        assert reads == 1
        print(f"READS audit first page (100 of 20,000): {reads} queries")

    def test_an_event_type_reads_its_partition(self, tables):
        body, reads = _audit(tables, eventType="exception_granted", maxItems="100")
        assert {e["eventType"] for e in body["entries"]} == {"exception_granted"}
        assert reads == 1
        print(f"READS audit eventType: {reads} queries")

    def test_a_database_filter_fills_the_page_within_the_budget(self, tables):
        body, reads = _audit(tables, databaseId=OTHER_DB, maxItems="100")
        assert len(body["entries"]) == 100
        assert {e["databaseId"] for e in body["entries"]} == {OTHER_DB}
        assert reads <= listing.DEFAULT_READ_BUDGET
        print(f"READS audit databaseId (a quarter of the trail): {reads} queries")

    def test_a_selective_text_filter_stays_within_the_budget(self, tables):
        body, reads = _audit(tables, text="auditor-7", maxItems="100")
        assert all(e["actor"] == "auditor-7" for e in body["entries"])
        assert reads <= listing.DEFAULT_READ_BUDGET
        print(f"READS audit text=auditor-7 (20 of 20,000 match): {reads} queries, "
              f"{len(body['entries'])} on the first page, token={'NextToken' in body}")

    def test_one_asset_reads_its_asset_index_partition(self, tables):
        body, reads = _audit(tables, databaseId=DB, assetId="x00001", maxItems="100")
        assert body["entries"] and {e["assetId"] for e in body["entries"]} == {"x00001"}
        assert reads == 1

    def test_an_asset_without_a_database_is_refused(self, tables):
        with patch(f"{audit_svc.__name__}.request_to_claims", claims_for(USER)), \
                patch(f"{audit_svc.__name__}.CasbinEnforcer", return_value=enforcer()), \
                patch(f"{audit_svc.__name__}.audit_table", tables["audit"]):
            response = audit_svc.lambda_handler(
                rest_event("GET", "/compliance/audit", query_params={"assetId": "x00001"}),
                MagicMock())
        assert response["statusCode"] == 400


class ScriptedTable:
    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages[len(self.calls) - 1]


def _item(i):
    return {"databaseId": "db", "assetId": f"a{i}", "match": i % 2 == 0}


@pytest.mark.unit
class TestQueryPage:

    def test_an_unfiltered_page_is_one_read_of_exactly_the_page(self):
        table = ScriptedTable({"Items": [_item(i) for i in range(3)],
                               "LastEvaluatedKey": {"databaseId": "db", "assetId": "a2"}})
        items, key, reads = listing.query_page(table, {}, 3, ("databaseId", "assetId"))
        assert len(items) == 3 and reads == 1 and table.calls[0]["Limit"] == 3
        assert key == {"databaseId": "db", "assetId": "a2"}

    def test_a_filtered_page_fills_across_reads_and_resumes_after_the_last_item(self):
        table = ScriptedTable(
            {"Items": [_item(0), _item(1)], "LastEvaluatedKey": {"databaseId": "db", "assetId": "a1"}},
            {"Items": [_item(2), _item(3), _item(4)],
             "LastEvaluatedKey": {"databaseId": "db", "assetId": "a4"}})
        items, key, reads = listing.query_page(table, {}, 2, ("databaseId", "assetId"),
                                               accept=lambda item: item["match"])
        assert [i["assetId"] for i in items] == ["a0", "a2"]
        # Resumes after a2, not after a4: a3 and a4 are read again on the next page.
        assert key == {"databaseId": "db", "assetId": "a2"} and reads == 2
        assert table.calls[1]["ExclusiveStartKey"] == {"databaseId": "db", "assetId": "a1"}
        assert 2 < table.calls[0]["Limit"] <= listing.FILTERED_READ_LIMIT

    def test_the_budget_stops_a_selective_filter_with_a_token(self):
        pages = [{"Items": [_item(1)], "LastEvaluatedKey": {"databaseId": "db", "assetId": f"k{n}"}}
                 for n in range(10)]
        table = ScriptedTable(*pages)
        items, key, reads = listing.query_page(table, {}, 5, ("databaseId", "assetId"),
                                               accept=lambda item: item["match"], read_budget=3)
        assert items == [] and reads == 3 and key == {"databaseId": "db", "assetId": "k2"}

    def test_a_page_full_at_the_last_item_of_the_query_has_no_token(self):
        table = ScriptedTable({"Items": [_item(0), _item(1)]})
        items, key, _ = listing.query_page(table, {}, 2, ("databaseId", "assetId"))
        assert len(items) == 2 and key is None

    def test_tokens_round_trip_and_reject_another_listings_key(self):
        token = listing.encode_token({"databaseId": "db", "assetId": "a"})
        assert listing.decode_token(token, ("databaseId", "assetId")) == {"databaseId": "db",
                                                                          "assetId": "a"}
        assert listing.decode_token(token, ("entryId",)) is None
        assert listing.decode_token("%%%", ("entryId",)) is None

    def test_the_name_attributes(self):
        names = listing.asset_name_attributes("x1", "  Turbine Blade ")
        assert names == {"assetName": "Turbine Blade", "assetNameSort": "turbine blade#x1",
                         "assetSearchText": "turbine blade x1"}
        assert listing.asset_name_attributes("X1", None)["assetNameSort"] == "x1#X1"


@pytest.mark.unit
class TestTheStateRowNameIsKeptCurrent:

    def test_a_rename_rewrites_the_name_attributes_and_an_unchanged_name_writes_nothing(self):
        from handlers.compliance import complianceEvaluationStore as store
        with mock_aws():
            table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
                **dict(COMPLIANCE_ASSET_STATE_TABLE_DEFINITION, TableName="t-name-refresh"))
            table.put_item(Item={"databaseId": "db", "assetId": "a1", "complianceState": "compliant",
                                 "schemaName": "std"})
            assert store.refresh_asset_state_name("db", "a1", "Old Name", table=table) is True
            assert store.refresh_asset_state_name("db", "a1", "Old Name", table=table) is False
            assert store.refresh_asset_state_name("db", "a1", "New Name", table=table) is True
            row = table.get_item(Key={"databaseId": "db", "assetId": "a1"})["Item"]
            assert (row["assetName"], row["assetNameSort"], row["complianceState"]) == (
                "New Name", "new name#a1", "compliant")
            # An asset without a compliance record gets none.
            assert store.refresh_asset_state_name("db", "untracked", "X", table=table) is False
            assert "Item" not in table.get_item(Key={"databaseId": "db", "assetId": "untracked"})
