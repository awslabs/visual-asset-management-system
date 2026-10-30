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
    DB, USER, body_of, claims_for, database_table_with, enforcer, live_only_asset_table,
    mirror_engine, rest_event,
)
from common.compliance import archivedAssets
from common.compliance import listing
from common.compliance.archivedAssets import ArchivedAssets
from handlers.compliance import complianceCascadeService as cascade_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc

ARCHIVED_DB = f"{DB}#deleted"


@pytest.mark.unit
def test_the_suffix_is_the_engines():
    """The partition suffix this module picks is the one the engine strips and re-checks."""
    assert archivedAssets.ARCHIVED_DATABASE_ID_SUFFIX == authz_module.ARCHIVED_DATABASE_ID_SUFFIX
    assert archivedAssets.archived_database_id(DB) == ARCHIVED_DB
    assert archivedAssets.stored_database_id(DB, True) == ARCHIVED_DB
    assert archivedAssets.stored_database_id(DB, False) == DB


def _asset_reader():
    """An enforcer stand-in whose asset GET passes on every archived row and depends on the
    database id alone, so the decisions below are those of the compliance object."""
    return enforcer()


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
        assert ArchivedAssets(table, _asset_reader()).decide(DB, "a", decide) is False
        assert asked == [DB]
        table.get_item.assert_not_called()
        table.query.assert_not_called()

    def test_a_caller_allowed_on_both_ids_reads_nothing(self):
        table = live_only_asset_table(archived={"a"})
        decide, asked = self._decide({DB, ARCHIVED_DB})
        assert ArchivedAssets(table, _asset_reader()).decide(DB, "a", decide) is True
        assert asked == [DB, ARCHIVED_DB]
        table.get_item.assert_not_called()
        table.query.assert_not_called()

    @pytest.mark.parametrize("asset_id,expected", [("a", False), ("live", True)])
    def test_otherwise_one_consistent_read_of_the_archived_key_decides(self, asset_id, expected):
        table = live_only_asset_table(archived={"a"})
        decide, _ = self._decide({DB})
        assert ArchivedAssets(table, _asset_reader()).decide(DB, asset_id, decide) is expected
        table.get_item.assert_called_once_with(
            Key={"databaseId": ARCHIVED_DB, "assetId": asset_id}, ConsistentRead=True,
            ProjectionExpression="assetId")
        table.query.assert_not_called()

    def test_a_listing_reads_the_archived_partition_once_per_database(self):
        table = live_only_asset_table(archived={"a"})
        decide, _ = self._decide({DB, "db2"})
        archived = ArchivedAssets(table, _asset_reader())
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
        assert ArchivedAssets(table, _asset_reader()).decide(database_id, asset_id, decide) is True
        assert asked == [database_id]
        table.get_item.assert_not_called()
        table.query.assert_not_called()

    def test_a_listing_asks_the_stored_id_again_only_for_its_archived_rows(self):
        """Without a `cache_key` (an object that names more than the database, such as a cascade),
        a listing that has read a database's archived ids does not ask the stored id of a row whose
        asset is not among them: the answer is the live one whatever the stored id says. Every
        refused check writes an authorization audit entry, so asking it per row cost a role refused
        on the stored id one log write per row."""
        table = live_only_asset_table(archived={"a", "b"})
        decide, asked = self._decide({DB})
        archived = ArchivedAssets(table, _asset_reader())
        rows = [f"x{i}" for i in range(10)] + ["a"] + [f"y{i}" for i in range(10)] + ["b"]
        verdicts = {asset_id: archived.decide(DB, asset_id, decide, listing=True)
                    for asset_id in rows}
        assert {asset_id for asset_id, allowed in verdicts.items() if not allowed} == {"a", "b"}
        # The first row's stored id, before the archive was read, then each archived row's.
        assert asked.count(ARCHIVED_DB) == 3
        assert asked.count(DB) == len(rows)
        assert table.query.call_count == 1

    def test_a_listing_allowed_on_both_ids_still_reads_no_archive(self):
        table = live_only_asset_table(archived={"a"})
        decide, asked = self._decide({DB, ARCHIVED_DB})
        archived = ArchivedAssets(table, _asset_reader())
        assert all(archived.decide(DB, asset_id, decide, listing=True)
                   for asset_id in ("x1", "a", "x2"))
        assert asked == [DB, ARCHIVED_DB] * 3
        table.query.assert_not_called()
        table.get_item.assert_not_called()

    @pytest.mark.parametrize("stored_allowed", [True, False])
    def test_archive_visible_asks_the_stored_id_once_per_database_and_reads_nothing(
            self, stored_allowed):
        table = live_only_asset_table(archived={"a"})
        decide, asked = self._decide({DB, ARCHIVED_DB} if stored_allowed else {DB})
        archived = ArchivedAssets(table, _asset_reader())
        assert [archived.archived_asset_visible(DB, asset_id, decide, cache_key="GET")
                for asset_id in ("a", "b", None)] == [stored_allowed] * 3
        assert asked == [ARCHIVED_DB]
        # A scope that names no asset partition has no archive to withhold and asks nothing.
        assert archived.archived_asset_visible("GLOBAL", "a", decide, cache_key="GET") is True
        assert asked == [ARCHIVED_DB]
        table.query.assert_not_called()
        table.get_item.assert_not_called()


def _cascade_listing(stored_id_allowed, archived_parents=("p03", "p11"), rows=20):
    """The pending cascade listing of DB, `rows` cascades whose parents are `p00`.., for a caller
    allowed on the live id and, per `stored_id_allowed`, on the archived id. Returns (response,
    refused Tier-2 checks, asset table)."""
    items = [{"cascadeId": f"c{i:02d}", "state": "pending_approval",
              "createdAt": f"2026-10-28T00:{i:02d}:00+00:00",
              "triggeredByDatabaseId": DB, "triggeredByAssetId": f"p{i:02d}"}
             for i in range(rows)]
    cascade_table = MagicMock(name="cascade_table")
    cascade_table.query.return_value = {"Items": [dict(item) for item in items]}
    refused = []

    def enforce(obj, act):
        allowed = (obj["databaseId"] == DB
                   or (stored_id_allowed and obj["databaseId"] == ARCHIVED_DB))
        if not allowed:
            refused.append((obj.get("object__type"), obj["databaseId"], act))
        return allowed
    instance = MagicMock(name="CasbinEnforcer")
    instance.enforceAPI.return_value = True
    instance.enforce.side_effect = enforce
    mirror_engine(instance)
    asset_table = live_only_asset_table(archived=set(archived_parents))
    with patch.object(cascade_svc, "request_to_claims", claims_for(USER)), \
            patch.object(cascade_svc, "CasbinEnforcer", return_value=instance), \
            patch.object(cascade_svc, "cascade_table", cascade_table), \
            patch.object(cascade_svc.store, "asset_table", asset_table), \
            patch.object(cascade_svc.store, "database_table", database_table_with(DB)):
        response = cascade_svc.lambda_handler(
            rest_event("GET", "/compliance/cascades", query_params={"databaseId": DB}),
            MagicMock())
    return response, refused, asset_table


@pytest.mark.unit
class TestTheCascadeListingRead:

    def test_a_role_refused_on_the_archived_id_is_refused_once_plus_once_per_archived_row(self):
        response, refused, asset_table = _cascade_listing(False)
        assert response["statusCode"] == 200
        cascades = body_of(response)["cascades"]
        assert [row["assetId"] for row in cascades] == [
            f"p{i:02d}" for i in range(20) if i not in (3, 11)]
        assert all(row["permissions"] == {"approve": True} for row in cascades)
        # One refused check for the first row, before the archive was read, and one for each
        # withheld row; none for the `approve` flags of the rows listed.
        assert len(refused) == 3
        assert asset_table.query.call_count == 1
        asset_table.get_item.assert_not_called()

    def test_a_role_allowed_on_both_ids_lists_everything_and_reads_no_archive(self):
        response, refused, asset_table = _cascade_listing(True)
        cascades = body_of(response)["cascades"]
        assert len(cascades) == 20
        assert all(row["permissions"] == {"approve": True} for row in cascades)
        assert refused == []
        asset_table.query.assert_not_called()
        asset_table.get_item.assert_not_called()


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
    mirror_engine(instance)
    instance.enforce.side_effect = lambda obj, act: (
        obj["databaseId"] == DB or (stored_id_allowed and obj["databaseId"] == ARCHIVED_DB))
    asset_table = live_only_asset_table(archived=set(archived))
    with patch.object(evaluate_svc, "request_to_claims", claims_for(USER)), \
            patch.object(evaluate_svc, "CasbinEnforcer", return_value=instance), \
            patch.object(evaluate_svc, "asset_state_table", state_table), \
            patch.object(evaluate_svc, "asset_table", asset_table), \
            patch.object(evaluate_svc.store, "database_table", database_table_with(DB)):
        response = evaluate_svc.lambda_handler(
            rest_event("GET", f"/compliance/state/{DB}", {"databaseId": DB},
                       {"maxItems": str(page_size)}), MagicMock())
    page_reads = [c.kwargs for c in state_table.query.call_args_list
                  if c.kwargs.get("Select") != "COUNT"]
    return response, page_reads, asset_table, state_table


@pytest.mark.unit
class TestTheOverviewRead:

    def test_a_caller_allowed_on_both_ids_gets_the_archived_rows_left_out_too(self):
        """An archived asset is not evaluated, so its record would stay counted in the state it
        was archived in: the overview leaves it out for every caller, as the asset listing does,
        with the read the withheld rows already cost (one archive query, a page plus their
        number, one BatchGetItem of their keys)."""
        response, reads, asset_table, state_table = _overview(True, archived=("a0x",))
        assert response["statusCode"] == 200
        assert [r["Limit"] for r in reads] == [3 + 1]
        assert asset_table.query.call_count == 1
        asset_table.get_item.assert_not_called()
        state_table.meta.client.batch_get_item.assert_called_once()
        assert "a0x" not in [row["assetId"] for row in body_of(response)["assets"]]
        assert body_of(response)["summary"]["quarantined"] == 0
        assert body_of(response)["totalAssets"] == 6

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

