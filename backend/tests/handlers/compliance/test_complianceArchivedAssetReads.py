# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""When the compliance services read the archive, and how much.

`common.compliance.archivedAssets` reads an asset's archived status only when it can change a
decision: never for a caller refused on the live id or allowed on both ids, one strongly-consistent
GetItem for a single record, and one keys-only Query per database for a listing, whatever its size.
The overview reads a page plus the archived rows it withholds, in one read.
"""

from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module
from backend.tests.handlers.compliance._harness import (
    DB, USER, body_of, claims_for, live_only_asset_table, rest_event,
)
from common.compliance import archivedAssets
from common.compliance import listing
from common.compliance.archivedAssets import ArchivedAssets
from handlers.compliance import complianceEvaluateService as evaluate_svc

ARCHIVED_DB = f"{DB}#deleted"


@pytest.mark.unit
def test_the_suffix_is_the_engines():
    """The partition suffix this module picks is the one the engine strips and re-checks."""
    assert archivedAssets.ARCHIVED_DATABASE_ID_SUFFIX == authz_module.ARCHIVED_DATABASE_ID_SUFFIX
    assert archivedAssets.archived_database_id(DB) == ARCHIVED_DB
    assert archivedAssets.stored_database_id(DB, True) == ARCHIVED_DB
    assert archivedAssets.stored_database_id(DB, False) == DB


@pytest.mark.unit
class TestDecide:

    def _decide(self, allowed_ids):
        asked = []

        def decide(database_id):
            asked.append(database_id)
            return database_id in allowed_ids
        return decide, asked

    def test_a_caller_refused_on_the_live_id_reads_nothing(self):
        table = live_only_asset_table(archived={"a"})
        decide, asked = self._decide(set())
        assert ArchivedAssets(table).decide(DB, "a", decide) is False
        assert asked == [DB]
        table.get_item.assert_not_called()
        table.query.assert_not_called()

    def test_a_caller_allowed_on_both_ids_reads_nothing(self):
        table = live_only_asset_table(archived={"a"})
        decide, asked = self._decide({DB, ARCHIVED_DB})
        assert ArchivedAssets(table).decide(DB, "a", decide) is True
        assert asked == [DB, ARCHIVED_DB]
        table.get_item.assert_not_called()
        table.query.assert_not_called()

    @pytest.mark.parametrize("asset_id,expected", [("a", False), ("live", True)])
    def test_otherwise_one_consistent_read_of_the_archived_key_decides(self, asset_id, expected):
        table = live_only_asset_table(archived={"a"})
        decide, _ = self._decide({DB})
        assert ArchivedAssets(table).decide(DB, asset_id, decide) is expected
        table.get_item.assert_called_once_with(
            Key={"databaseId": ARCHIVED_DB, "assetId": asset_id}, ConsistentRead=True,
            ProjectionExpression="assetId")
        table.query.assert_not_called()

    def test_a_listing_reads_the_archived_partition_once_per_database(self):
        table = live_only_asset_table(archived={"a"})
        decide, _ = self._decide({DB, "db2"})
        archived = ArchivedAssets(table)
        rows = [(DB, f"x{i}") for i in range(50)] + [(DB, "a"), ("db2", "y"), ("db2", "z")]
        verdicts = {(d, a): archived.decide(d, a, decide, cache_key="GET", listing=True)
                    for d, a in rows}
        assert verdicts[(DB, "a")] is False
        assert sum(verdicts.values()) == len(rows) - 1
        queries = [c.kwargs for c in table.query.call_args_list]
        assert [q["KeyConditionExpression"].get_expression()["values"][1]
                for q in queries] == [ARCHIVED_DB, "db2#deleted"]
        # Consistent, and keys only: nothing beyond the table's key attributes is read.
        assert [q["ConsistentRead"] for q in queries] == [True, True]
        assert [{name.strip() for name in q["ProjectionExpression"].split(",")}
                <= {"databaseId", "assetId"} for q in queries] == [True, True]
        table.get_item.assert_not_called()

    @pytest.mark.parametrize("database_id,asset_id", [
        ("GLOBAL", "a"), ("*", "*"), ("", "a"), (DB, ""), (DB, "*"), (ARCHIVED_DB, "a")])
    def test_rows_that_name_no_asset_partition_read_nothing(self, database_id, asset_id):
        table = live_only_asset_table(archived={"a"})
        decide, asked = self._decide({database_id})
        assert ArchivedAssets(table).decide(database_id, asset_id, decide) is True
        assert asked == [database_id]
        table.get_item.assert_not_called()
        table.query.assert_not_called()


def _overview(stored_id_allowed, archived=(), page_size=3):
    """Run the overview of DB for a caller allowed on the live id and, per `stored_id_allowed`, on
    the archived id; the state table holds the archived ids plus live rows."""
    rows = [{"databaseId": DB, "assetId": f"a{i}", "complianceState": "compliant"}
            for i in range(6)]
    rows += [{"databaseId": DB, "assetId": asset_id, "complianceState": "quarantined"}
             for asset_id in archived]
    rows.sort(key=lambda row: row["assetId"])
    state_table = MagicMock(name="asset_state_table")

    def query(**kwargs):
        if kwargs.get("Select") == "COUNT":
            if "IndexName" not in kwargs:
                return {"Count": 0}
            # (complianceState = s AND databaseId = DB) on the ComplianceStateIndex.
            state = kwargs["KeyConditionExpression"]._values[0]._values[1]
            return {"Count": sum(1 for row in rows if row["complianceState"] == state)}
        limit = kwargs["Limit"]
        return {"Items": [dict(row) for row in rows[:limit]],
                **({"LastEvaluatedKey": {"databaseId": DB, "assetId": rows[limit - 1]["assetId"]}}
                   if limit < len(rows) else {})}
    state_table.query.side_effect = query
    state_table.name = "state"
    state_table.meta.client.batch_get_item.return_value = {"Responses": {"state": [
        {"assetId": a, "complianceState": "quarantined"} for a in archived]}}
    instance = MagicMock(name="CasbinEnforcer")
    instance.enforceAPI.return_value = True
    instance.enforce.side_effect = lambda obj, act: (
        obj["databaseId"] == DB or (stored_id_allowed and obj["databaseId"] == ARCHIVED_DB))
    asset_table = live_only_asset_table(archived=set(archived))
    with patch.object(evaluate_svc, "request_to_claims", claims_for(USER)), \
            patch.object(evaluate_svc, "CasbinEnforcer", return_value=instance), \
            patch.object(evaluate_svc, "asset_state_table", state_table), \
            patch.object(evaluate_svc, "asset_table", asset_table):
        response = evaluate_svc.lambda_handler(
            rest_event("GET", f"/compliance/state/{DB}", {"databaseId": DB},
                       {"maxItems": str(page_size)}), MagicMock())
    page_reads = [c.kwargs for c in state_table.query.call_args_list
                  if c.kwargs.get("Select") != "COUNT"]
    return response, page_reads, asset_table, state_table


@pytest.mark.unit
class TestTheOverviewRead:

    def test_a_caller_allowed_on_both_ids_reads_as_before(self):
        response, reads, asset_table, state_table = _overview(True, archived=("a0x",))
        assert response["statusCode"] == 200
        assert [r["Limit"] for r in reads] == [3]
        asset_table.query.assert_not_called()
        asset_table.get_item.assert_not_called()
        state_table.meta.client.batch_get_item.assert_not_called()
        assert "a0x" in [row["assetId"] for row in body_of(response)["assets"]]
        assert body_of(response)["summary"]["quarantined"] == 1

    def test_withheld_rows_cost_one_archive_query_and_a_page_plus_their_number(self):
        response, reads, asset_table, state_table = _overview(False, archived=("a0x", "a1x"))
        assert response["statusCode"] == 200
        listed = [row["assetId"] for row in body_of(response)["assets"]]
        assert listed == ["a0", "a1", "a2"]
        # One read of a page plus the two withheld rows fills the page.
        assert [r["Limit"] for r in reads] == [3 + 2]
        assert asset_table.query.call_count <= 1
        asset_table.get_item.assert_not_called()
        # The counts leave the withheld records out: one BatchGetItem of their keys.
        state_table.meta.client.batch_get_item.assert_called_once()
        summary = body_of(response)["summary"]
        assert summary["quarantined"] == 0
        assert summary["compliant"] == 6
        assert body_of(response)["totalAssets"] == 6

    def test_a_caller_refused_on_the_archived_id_with_no_archived_asset_reads_as_before(self):
        response, reads, asset_table, state_table = _overview(False, archived=())
        assert [r["Limit"] for r in reads] == [3]
        assert asset_table.query.call_count <= 1
        state_table.meta.client.batch_get_item.assert_not_called()

    def test_the_read_limit_never_exceeds_a_filtered_read(self):
        response, reads, _, _ = _overview(False, archived=tuple(f"z{i}" for i in range(600)),
                                          page_size=200)
        assert reads
        assert all(r["Limit"] <= listing.FILTERED_READ_LIMIT for r in reads)

