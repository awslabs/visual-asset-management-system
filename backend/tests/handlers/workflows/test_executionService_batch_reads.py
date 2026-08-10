# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the batched entity reads executionService uses to resolve the assets an execution
read (and each step's pipeline definition) before authorizing them one at a time.
"""

import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

from backend.backend.handlers.workflows import executionService as le

MOD = "backend.backend.handlers.workflows.executionService"

# The table names the module resolved at import (the root conftest seeds them as env overrides), so
# the batch_get_item stubs key off the same names the helpers request.
ASSETS_TABLE = le.asset_storage_table_name
PIPELINES_TABLE = le.pipeline_database


@pytest.fixture(autouse=True)
def _clear_asset_cache():
    # Both module-level memos: the asset rows and the authorization decisions keyed off them.
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()
    yield
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()


def _asset_row(database_id, asset_id):
    return {"databaseId": database_id, "assetId": asset_id, "assetName": f"n-{asset_id}"}


def _responder(table_name, existing):
    """A batch_get_item stub returning the rows of `existing` (a set of (db, id) pairs) that the
    request asked for. Records each call's key list on the returned mock's `chunks`."""
    calls = []

    def _call(RequestItems):
        keys = RequestItems[table_name]["Keys"]
        calls.append(list(keys))
        rows = [_asset_row(k["databaseId"], k["assetId"]) for k in keys
                if (k["databaseId"], k["assetId"]) in existing]
        return {"Responses": {table_name: rows}}

    stub = MagicMock(side_effect=_call)
    stub.chunks = calls
    return stub


@pytest.mark.unit
class TestPrewarmAssetDetailsChunking:
    """BatchGetItem accepts at most 100 keys per request, so a larger key set is split."""

    def test_exactly_one_hundred_keys_is_a_single_request(self):
        pairs = [("db", f"a{i}") for i in range(100)]
        stub = _responder(ASSETS_TABLE, set(pairs))
        with patch(f"{MOD}.dynamodb") as ddb, patch(f"{MOD}.get_asset_details") as per_item:
            ddb.batch_get_item = stub
            result = le.prewarm_asset_details(pairs)
        assert len(stub.chunks) == 1
        assert len(stub.chunks[0]) == 100
        assert len(result) == 100
        # Everything resolved in the batch, so no per-item fallback read happened.
        per_item.assert_not_called()

    def test_one_hundred_and_one_keys_splits_into_two_requests(self):
        pairs = [("db", f"a{i}") for i in range(101)]
        stub = _responder(ASSETS_TABLE, set(pairs))
        with patch(f"{MOD}.dynamodb") as ddb, patch(f"{MOD}.get_asset_details") as per_item:
            ddb.batch_get_item = stub
            result = le.prewarm_asset_details(pairs)
        assert [len(c) for c in stub.chunks] == [100, 1]
        assert len(result) == 101
        per_item.assert_not_called()

    def test_two_hundred_and_fifty_keys_chunk_at_the_limit(self):
        pairs = [("db", f"a{i}") for i in range(250)]
        stub = _responder(ASSETS_TABLE, set(pairs))
        with patch(f"{MOD}.dynamodb") as ddb, patch(f"{MOD}.get_asset_details"):
            ddb.batch_get_item = stub
            le.prewarm_asset_details(pairs)
        assert [len(c) for c in stub.chunks] == [100, 100, 50]

    def test_a_lone_uncached_asset_skips_the_batch(self):
        # One asset is already a single round-trip (the measured common case), so it reads directly
        # rather than through a batch that would cost the same.
        stub = MagicMock()
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.get_asset_details", side_effect=_asset_row) as per_item:
            ddb.batch_get_item = stub
            result = le.prewarm_asset_details([("db", "a1")])
        stub.assert_not_called()
        per_item.assert_called_once_with("db", "a1")
        assert result[("db", "a1")]["assetId"] == "a1"

    def test_two_uncached_assets_use_the_batch(self):
        pairs = [("db", "a1"), ("db", "a2")]
        stub = _responder(ASSETS_TABLE, set(pairs))
        with patch(f"{MOD}.dynamodb") as ddb, patch(f"{MOD}.get_asset_details") as per_item:
            ddb.batch_get_item = stub
            le.prewarm_asset_details(pairs)
        assert len(stub.chunks) == 1
        per_item.assert_not_called()

    def test_duplicate_and_empty_pairs_are_dropped_before_the_request(self):
        pairs = [("db", "a1"), ("db", "a1"), ("", "a2"), ("db", ""), ("db", "a3")]
        stub = _responder(ASSETS_TABLE, {("db", "a1"), ("db", "a3")})
        with patch(f"{MOD}.dynamodb") as ddb, patch(f"{MOD}.get_asset_details"):
            ddb.batch_get_item = stub
            result = le.prewarm_asset_details(pairs)
        assert len(stub.chunks) == 1
        assert stub.chunks[0] == [{"databaseId": "db", "assetId": "a1"},
                                  {"databaseId": "db", "assetId": "a3"}]
        assert set(result) == {("db", "a1"), ("db", "a3")}


@pytest.mark.unit
class TestPrewarmAssetDetailsUnprocessedKeys:
    """DynamoDB may return UnprocessedKeys (throttling, or a 16 MB response); they are re-requested,
    and anything still unresolved falls back to the per-item read."""

    def test_unprocessed_keys_are_retried(self):
        responses = [
            {"Responses": {ASSETS_TABLE: [_asset_row("db", "a1")]},
             "UnprocessedKeys": {ASSETS_TABLE: {"Keys": [{"databaseId": "db", "assetId": "a2"}]}}},
            {"Responses": {ASSETS_TABLE: [_asset_row("db", "a2")]}},
        ]
        stub = MagicMock(side_effect=responses)
        with patch(f"{MOD}.dynamodb") as ddb, patch(f"{MOD}.get_asset_details") as per_item, \
             patch(f"{MOD}.time.sleep") as sleep:
            ddb.batch_get_item = stub
            result = le.prewarm_asset_details([("db", "a1"), ("db", "a2")])
        assert stub.call_count == 2
        assert sleep.call_count == 1
        assert result[("db", "a2")]["assetId"] == "a2"
        per_item.assert_not_called()

    def test_retries_are_bounded_and_the_remainder_falls_back_to_per_item_reads(self):
        # Always unprocessed: the retry budget is spent, then the leftover keys are read individually
        # so the memo is still complete.
        unprocessed = {
            "Responses": {ASSETS_TABLE: []},
            "UnprocessedKeys": {ASSETS_TABLE: {"Keys": [{"databaseId": "db", "assetId": "a1"},
                                                        {"databaseId": "db", "assetId": "a2"}]}},
        }
        stub = MagicMock(return_value=unprocessed)
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.get_asset_details", side_effect=_asset_row) as per_item, \
             patch(f"{MOD}.time.sleep"):
            ddb.batch_get_item = stub
            result = le.prewarm_asset_details([("db", "a1"), ("db", "a2")])
        assert stub.call_count == le.BATCH_GET_MAX_RETRIES + 1
        assert per_item.call_count == 2
        assert result[("db", "a1")]["assetId"] == "a1"
        assert result[("db", "a2")]["assetId"] == "a2"

    def test_a_failed_batch_call_falls_back_to_per_item_reads(self):
        stub = MagicMock(side_effect=Exception("throttled"))
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.get_asset_details", side_effect=_asset_row) as per_item:
            ddb.batch_get_item = stub
            result = le.prewarm_asset_details([("db", "a1"), ("db", "a2")])
        assert per_item.call_count == 2
        assert set(result) == {("db", "a1"), ("db", "a2")}


@pytest.mark.unit
class TestPrewarmAssetDetailsMissingAsset:
    """A missing asset stays distinguishable — it memoizes as None exactly as the per-item read returned
    — and both read paths then authorize it on the database it lived in rather than denying, so an
    execution outlives the asset it ran against."""

    def test_an_absent_asset_resolves_to_none(self):
        # a2 is not in the table: BatchGetItem simply omits it, and the per-item fallback confirms
        # absence with None.
        stub = _responder(ASSETS_TABLE, {("db", "a1")})
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.get_asset_details", return_value=None) as per_item:
            ddb.batch_get_item = stub
            result = le.prewarm_asset_details([("db", "a1"), ("db", "a2")])
        assert result[("db", "a1")]["assetId"] == "a1"
        assert result[("db", "a2")] is None
        assert le._asset_details_cache[("db", "a2")] is None
        per_item.assert_called_once_with("db", "a2")

    def test_an_archived_asset_is_authorized_on_its_own_row(self):
        """Archiving is REVERSIBLE and moves the row to the '#deleted' partition, so an archived asset
        must still be authorized on its own attributes. Resolving only the active partition would make
        archiving downgrade the check to the asset's DATABASE — a weaker rule — so a role whose
        asset-GET scope is narrower than its database-GET scope would gain the archived asset's
        execution metadata simply because somebody archived it."""
        archived = {"databaseId": "db#deleted", "assetId": "a1", "assetName": "secret"}

        def _query(archived_exists):
            def run(KeyConditionExpression=None, **kwargs):
                # The composed condition carries the partition value; find it without string-matching
                # the boto3 condition object (whose repr does not contain it).
                requested = [v for cond in getattr(KeyConditionExpression, "_values", ())
                             for v in getattr(cond, "_values", ())
                             if isinstance(v, str)]
                is_archived = any(v.endswith("#deleted") for v in requested)
                return {"Items": [archived]} if (is_archived and archived_exists) else {"Items": []}
            return run

        # Deny the asset by name; allow its database. Only the asset row carries assetName.
        enforcer = MagicMock()
        enforcer.enforce.side_effect = lambda obj, action, *a, **k: (
            obj.get("assetName") != "secret" if obj.get("object__type") == "asset" else True)
        le.claims_and_roles = {"tokens": ["u1"]}
        main_item = {"workflowExecutionId": "E1", "workflowId": "wf1", "workflowDatabaseId": "wf-db"}

        for archived_exists, expected in ((True, False), (False, True)):
            le._asset_details_cache.clear()
            with patch(f"{MOD}.asset_table") as table, \
                 patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
                 patch(f"{MOD}.get_execution_input_assets", return_value=[("db", "a1")]), \
                 patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}):
                table.query = MagicMock(side_effect=_query(archived_exists))
                allowed, reason = le.authorize_execution_access("E1", main_item, "GET")
            # Archived -> denied on the asset row. Truly deleted -> the database fallback allows it.
            assert allowed is expected, f"archived_exists={archived_exists}: {reason}"

    def test_an_unreadable_configuration_row_denies_rather_than_allowing(self):
        """A FAILED read of the configuration row must not read as "this run had no inputs".

        The row carries the metadata-source databases and assets the read gate checks and the
        output-target ids that gate a run with no inputs, so answering a failed read with {} strips
        every data-level check and leaves workflow GET alone. A caller who is denied because they
        cannot read the run's output asset would then be ALLOWED the moment DynamoDB throttled that
        read — a throttle silently turning a denial into an approval."""
        # Workflow GET allowed, but the caller cannot read the run's output asset.
        enforcer = MagicMock()
        enforcer.enforce.side_effect = lambda obj, action, *a, **k: (
            obj.get("object__type") != "asset")
        le.claims_and_roles = {"tokens": ["u1"]}
        main_item = {"workflowExecutionId": "E1", "workflowId": "wf1", "workflowDatabaseId": "wf-db"}
        config = {"outputLocationType": "asset", "outputDatabaseId": "db", "outputAssetId": "out"}

        # Healthy read: the output asset gates it and the caller is denied.
        with patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
             patch(f"{MOD}.get_asset_details",
                   return_value={"databaseId": "db", "assetId": "out", "assetName": "n"}), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=config):
            le._asset_details_cache.clear()
            assert le.authorize_execution_access("E1", main_item, "GET")[0] is False

        # The row cannot be read: the failure must propagate, not degrade to an allow.
        with patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
             patch(f"{MOD}.get_asset_details",
                   return_value={"databaseId": "db", "assetId": "out", "assetName": "n"}), \
             patch(f"{MOD}.get_workflow_execution_configuration_row",
                   side_effect=Exception("ThrottlingException")):
            le._asset_details_cache.clear()
            with pytest.raises(Exception, match="ThrottlingException"):
                le.authorize_execution_access("E1", main_item, "GET")

    def test_the_configuration_row_read_does_not_swallow_failures(self):
        # An absent row is still {} — only a FAILED read raises, so "no row" stays distinguishable
        # from "unreadable".
        with patch(f"{MOD}.dynamodb") as ddb:
            ddb.Table.return_value.get_item.return_value = {}
            assert le.get_workflow_execution_configuration_row("E1") == {}
        with patch(f"{MOD}.dynamodb") as ddb:
            ddb.Table.return_value.get_item.side_effect = Exception("boom")
            with pytest.raises(Exception, match="boom"):
                le.get_workflow_execution_configuration_row("E1")

    def test_get_asset_details_reads_the_archived_partition(self):
        seen = []

        def run(KeyConditionExpression=None, **kwargs):
            seen.append([v for cond in getattr(KeyConditionExpression, "_values", ())
                         for v in getattr(cond, "_values", ()) if isinstance(v, str)])
            return {"Items": []}

        with patch(f"{MOD}.asset_table") as table:
            table.query = MagicMock(side_effect=run)
            assert le.get_asset_details("db", "a1") is None
        # Active partition first, then the archived one — a live asset costs exactly one query.
        assert any("db" in keys for keys in seen)
        assert any(f"db{le.ARCHIVED_DATABASE_SUFFIX}" in keys for keys in seen)

    def _deleted_asset_run(self, enforcer, config=None, present=frozenset()):
        """Authorize a run whose only input asset no longer resolves."""
        le.claims_and_roles = {"tokens": ["u1"]}
        main_item = {"workflowExecutionId": "E1", "workflowId": "wf1", "workflowDatabaseId": "wf-db"}
        stub = _responder(ASSETS_TABLE, set(present))
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[("asset-db", "gone")]), \
             patch(f"{MOD}.get_asset_details", return_value=None), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=config or {}):
            ddb.batch_get_item = stub
            return le.authorize_execution_access("E1", main_item, "GET")

    def test_a_deleted_asset_defers_to_its_own_database(self):
        # Deleting an asset does not delete the executions that ran against it, and those runs are the
        # record of what happened to it — so an unresolvable asset is authorized on the database it
        # lived in rather than denied outright. A database is never removed (deletion rewrites it under
        # a '#deleted' id), so that permission stays answerable.
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        allowed, reason = self._deleted_asset_run(enforcer)
        assert allowed is True, reason
        databases = [c.args[0]["databaseId"] for c in enforcer.enforce.call_args_list
                     if c.args[0].get("object__type") == "database"]
        # The ASSET's database, not the workflow's.
        assert databases == ["asset-db"]

    def test_denying_the_deleted_asset_s_database_denies_the_execution(self):
        enforcer = MagicMock()
        enforcer.enforce.side_effect = lambda obj, action, *a, **k: not (
            obj.get("object__type") == "database" and obj.get("databaseId") == "asset-db")
        allowed, reason = self._deleted_asset_run(enforcer)
        assert allowed is False
        assert "asset-db" in reason

    def test_both_read_paths_agree_on_a_deleted_asset(self):
        # The list and the details path evaluate one rule, so a row the list offers never 403s.
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        le.claims_and_roles = {"tokens": ["u1"]}
        main_item = {"workflowExecutionId": "E1", "workflowId": "wf1", "workflowDatabaseId": "wf-db"}
        config = {"outputLocationType": "asset", "outputDatabaseId": "asset-db", "outputAssetId": "out"}
        stub = _responder(ASSETS_TABLE, {("asset-db", "out")})
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[("asset-db", "gone")]), \
             patch(f"{MOD}.get_asset_details", return_value=None), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=config):
            ddb.batch_get_item = stub
            assert le._execution_visible_to_caller("E1", main_item) is True
            assert le.authorize_execution_access("E1", main_item, "GET")[0] is True

    def test_a_run_with_no_assets_at_all_rests_on_workflow_get(self):
        # No input and no output asset: there is no asset database to fall back to, so workflow GET is
        # the whole gate and no database check is made.
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        le.claims_and_roles = {"tokens": ["u1"]}
        main_item = {"workflowExecutionId": "E1", "workflowId": "wf1", "workflowDatabaseId": "wf-db"}
        with patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}):
            allowed, reason = le.authorize_execution_access("E1", main_item, "GET")
        assert allowed is True, reason
        assert [c.args[0]["object__type"] for c in enforcer.enforce.call_args_list] == ["workflow"]


@pytest.mark.unit
class TestPrewarmAssetDetailsCacheReuse:
    """The pre-warm populates the same memo `_get_asset_details_cached` reads, so a warmed pair costs
    no further read."""

    def test_a_warmed_pair_is_not_read_again(self):
        pairs = [("db", "a1"), ("db", "a2")]
        stub = _responder(ASSETS_TABLE, set(pairs))
        with patch(f"{MOD}.dynamodb") as ddb, patch(f"{MOD}.get_asset_details") as per_item:
            ddb.batch_get_item = stub
            le.prewarm_asset_details(pairs)
            assert stub.call_count == 1
            for database_id, asset_id in pairs:
                assert le._get_asset_details_cached(database_id, asset_id)["assetId"] == asset_id
            # No batch and no per-item read followed the pre-warm.
            assert stub.call_count == 1
            per_item.assert_not_called()

    def test_an_already_cached_pair_is_left_out_of_the_batch(self):
        le._asset_details_cache[("db", "a1")] = _asset_row("db", "a1")
        pairs = [("db", "a1"), ("db", "a2"), ("db", "a3")]
        stub = _responder(ASSETS_TABLE, {("db", "a2"), ("db", "a3")})
        with patch(f"{MOD}.dynamodb") as ddb, patch(f"{MOD}.get_asset_details") as per_item:
            ddb.batch_get_item = stub
            result = le.prewarm_asset_details(pairs)
        # Only the uncached pairs are requested; the cached one is never re-read.
        assert stub.chunks == [[{"databaseId": "db", "assetId": "a2"},
                                {"databaseId": "db", "assetId": "a3"}]]
        # The return still covers every requested pair, cached ones included.
        assert set(result) == set(pairs)
        per_item.assert_not_called()

    def test_all_pairs_cached_issues_no_request_at_all(self):
        le._asset_details_cache[("db", "a1")] = _asset_row("db", "a1")
        stub = MagicMock()
        with patch(f"{MOD}.dynamodb") as ddb, patch(f"{MOD}.get_asset_details") as per_item:
            ddb.batch_get_item = stub
            result = le.prewarm_asset_details([("db", "a1")])
        stub.assert_not_called()
        per_item.assert_not_called()
        assert result[("db", "a1")]["assetId"] == "a1"

    def test_authorization_reads_each_asset_once_across_both_paths(self):
        # The two authorization paths share the module memo, so the second one issues no read.
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        le.claims_and_roles = {"tokens": ["u1"]}
        main_item = {"workflowExecutionId": "E1", "workflowId": "wf1", "workflowDatabaseId": "db1"}
        input_assets = [("db", "a1"), ("db", "a2")]
        stub = _responder(ASSETS_TABLE, set(input_assets))
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=input_assets), \
             patch(f"{MOD}.get_asset_details") as per_item, \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}):
            ddb.batch_get_item = stub
            assert le.authorize_execution_access("E1", main_item, "GET")[0] is True
            assert le._execution_visible_to_caller("E1", main_item) is True
        # One batched request for the pair of assets; the visibility pass reuses the memo.
        assert stub.call_count == 1
        per_item.assert_not_called()


@pytest.mark.unit
class TestPipelineDefinitionBatchRead:
    """A multi-step execution's step definitions resolve in batched reads rather than one get_item
    per step."""

    def _pipeline_responder(self, existing):
        calls = []

        def _call(RequestItems):
            keys = RequestItems[PIPELINES_TABLE]["Keys"]
            calls.append(list(keys))
            rows = [{"databaseId": k["databaseId"], "pipelineId": k["pipelineId"],
                     "pipelineName": f"n-{k['pipelineId']}"}
                    for k in keys if (k["databaseId"], k["pipelineId"]) in existing]
            return {"Responses": {PIPELINES_TABLE: rows}}

        stub = MagicMock(side_effect=_call)
        stub.chunks = calls
        return stub

    def test_definitions_resolve_in_one_request(self):
        pairs = [("db", "p1"), ("db", "p2"), ("db", "p3")]
        stub = self._pipeline_responder(set(pairs))
        with patch(f"{MOD}.dynamodb") as ddb, patch(f"{MOD}.get_pipeline_definition") as per_item:
            ddb.batch_get_item = stub
            result = le.get_pipeline_definitions(pairs)
        assert stub.call_count == 1
        assert result[("db", "p2")]["pipelineName"] == "n-p2"
        per_item.assert_not_called()

    def test_a_repeated_pipeline_is_requested_once(self):
        # p1 appears twice (the same pipeline used as two steps); it collapses to one key.
        stub = self._pipeline_responder({("db", "p1"), ("db", "p2")})
        with patch(f"{MOD}.dynamodb") as ddb, patch(f"{MOD}.get_pipeline_definition"):
            ddb.batch_get_item = stub
            le.get_pipeline_definitions([("db", "p1"), ("db", "p2"), ("db", "p1")])
        assert stub.chunks == [[{"databaseId": "db", "pipelineId": "p1"},
                                {"databaseId": "db", "pipelineId": "p2"}]]

    def test_a_deleted_pipeline_degrades_to_an_empty_definition(self):
        stub = self._pipeline_responder(set())
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.get_pipeline_definition", return_value={}) as per_item:
            ddb.batch_get_item = stub
            result = le.get_pipeline_definitions([("db", "gone")])
        assert result[("db", "gone")] == {}
        per_item.assert_called_once_with("db", "gone")

    def test_chunking_at_the_key_limit(self):
        pairs = [("db", f"p{i}") for i in range(101)]
        stub = self._pipeline_responder(set(pairs))
        with patch(f"{MOD}.dynamodb") as ddb, patch(f"{MOD}.get_pipeline_definition"):
            ddb.batch_get_item = stub
            le.get_pipeline_definitions(pairs)
        assert [len(c) for c in stub.chunks] == [100, 1]
