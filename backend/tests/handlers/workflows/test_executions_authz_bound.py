# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The bounded authorization fan-out of the global executions list.

Two mechanisms, one invariant. The DECISION MEMO collapses an identical Casbin question to one
evaluation per request, and the ENTITY BUDGET bounds how many distinct assets a single page resolves.
The invariant both must preserve: a row the list SHOWS never 403s when it is opened — so the budget
withholds a row it could not fully evaluate rather than admitting it unchecked.

Also pins the detail-view byte-budget split this module's paged endpoint is the escalation path for.
That allocator is owned elsewhere and has no test of its own, so the property is pinned here by
calling it rather than by assuming a constant.

executionService resolves its table names at import (mirrors test_executionService_wb53.py)."""

import base64
import json
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

from backend.backend.common.workflows import executionRecords as er
from backend.backend.handlers.workflows import executionService as le  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"


@pytest.fixture(autouse=True)
def _clear_caches():
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()
    le._disarm_authz_entity_budget()
    yield
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()
    le._disarm_authz_entity_budget()


def _allow_all():
    e = MagicMock()
    e.enforce.return_value = True
    e.enforceAPI.return_value = True
    return e


def _main_rows(count, shared_asset=True):
    # Carries the by-date GSI's keys and the base table's, which is what a continuation key names.
    return [{"workflowExecutionId": f"E{i}", "workflowId": "wf", "workflowDatabaseId": "db",
             "workflowDatabaseId:workflowId": "db:wf",
             "allListPartition": er.ALL_EXECUTIONS_LIST_PARTITION,
             "executionStatus": "SUCCEEDED", "executionStartDate": f"2026-01-{(i % 28) + 1:02d}T00:00:00Z"}
            for i in range(count)]


def _run_global_list(main_rows, input_assets_for, enforcer, query=None, last_key=None):
    """Run the global list over `main_rows`, where input_assets_for(execution_id) gives that row's
    input assets. Returns (parsed message, enforcer)."""
    table = MagicMock()
    resp = {"Items": main_rows}
    if last_key:
        resp["LastEvaluatedKey"] = last_key
    table.query.return_value = resp
    le.claims_and_roles = {"tokens": ["u1"]}
    with patch(f"{MOD}.dynamodb") as ddb, \
         patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
         patch(f"{MOD}.get_execution_input_assets", side_effect=input_assets_for), \
         patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}), \
         patch(f"{MOD}.get_asset_details",
               side_effect=lambda d, a: {"databaseId": d, "assetId": a, "assetName": a}):
        ddb.Table.return_value = table
        ddb.batch_get_item.side_effect = lambda RequestItems: {
            "Responses": {name: [{"databaseId": k["databaseId"], "assetId": k["assetId"],
                                  "assetName": k["assetId"]}
                                 for k in spec["Keys"]]
                          for name, spec in RequestItems.items()}}
        response = le.get_global_executions({}, query or {"pageSize": "100"})
    return json.loads(response["body"])["message"]


@pytest.mark.unit
class TestDecisionMemoCollapsesRepeatedQuestions:
    """A page over rows that reference the SAME few assets asks the same Casbin questions repeatedly.
    The memo answers each distinct question once per request."""

    def test_repeated_assets_collapse_to_one_enforce_call_each(self):
        # 20 executions, all reading the same one asset. Without the memo this is one workflow decision
        # and one asset decision PER ROW; with it, one of each for the whole page.
        rows = _main_rows(20)
        enf = _allow_all()
        message = _run_global_list(rows, lambda eid: [("db", "shared-asset")], enf)
        assert len(message["Items"]) == 20, "every row is still listed"
        asset_calls = [c for c in enf.enforce.call_args_list
                       if c.args[0].get("object__type") == "asset"]
        workflow_calls = [c for c in enf.enforce.call_args_list
                          if c.args[0].get("object__type") == "workflow"]
        assert len(asset_calls) == 1, f"the shared asset was re-evaluated per row: {len(asset_calls)}"
        assert len(workflow_calls) == 1, f"the shared workflow was re-evaluated: {len(workflow_calls)}"

    def test_distinct_assets_are_each_evaluated(self):
        # The memo must not collapse DIFFERENT entities: each distinct asset is its own decision.
        rows = _main_rows(5)
        enf = _allow_all()
        message = _run_global_list(rows, lambda eid: [("db", f"asset-{eid}")], enf)
        assert len(message["Items"]) == 5
        asset_ids = sorted(c.args[0].get("assetId") for c in enf.enforce.call_args_list
                           if c.args[0].get("object__type") == "asset")
        assert asset_ids == [f"asset-E{i}" for i in range(5)]

    def test_the_memo_does_not_change_which_rows_are_listed(self):
        # A denied asset stays denied however many rows reference it — the memo caches the DENIAL too.
        rows = _main_rows(6)
        enf = MagicMock()
        enf.enforceAPI.return_value = True
        enf.enforce.side_effect = lambda obj, action, *a, **k: not (
            obj.get("object__type") == "asset" and obj.get("assetId") == "secret")
        message = _run_global_list(
            rows, lambda eid: [("db", "secret" if eid in ("E1", "E3") else "ok")], enf)
        assert [i["workflowExecutionId"] for i in message["Items"]] == ["E0", "E2", "E4", "E5"]

    def test_a_denied_decision_is_cached_rather_than_recomputed(self):
        rows = _main_rows(10)
        enf = MagicMock()
        enf.enforceAPI.return_value = True
        enf.enforce.side_effect = lambda obj, action, *a, **k: (
            obj.get("object__type") != "asset")
        message = _run_global_list(rows, lambda eid: [("db", "denied")], enf)
        assert message["Items"] == []
        asset_calls = [c for c in enf.enforce.call_args_list
                       if c.args[0].get("object__type") == "asset"]
        assert len(asset_calls) == 1


@pytest.mark.unit
class TestDecisionMemoCannotCrossIdentities:
    """The memo sits on an authorization path, so it must be structurally incapable of answering one
    caller with another's decisions — not merely cleared often enough."""

    OBJ = {"object__type": "asset", "databaseId": "db", "assetId": "a1"}

    def _enforcer(self, allow):
        e = MagicMock()
        e.enforce.side_effect = lambda obj, action, *a, **k: allow
        return e

    def test_a_cached_allow_is_not_served_to_a_different_enforcer(self):
        # The fail-open this memo must never have: an ALLOW computed for one enforcer handed to another
        # that would have DENIED.
        le.claims_and_roles = {"tokens": ["u1"]}
        allowing, denying = self._enforcer(True), self._enforcer(False)
        assert le._enforce_cached(allowing, dict(self.OBJ), "GET") is True
        assert le._enforce_cached(denying, dict(self.OBJ), "GET") is False

    def test_a_cached_deny_is_not_served_to_a_different_enforcer(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        denying, allowing = self._enforcer(False), self._enforcer(True)
        assert le._enforce_cached(denying, dict(self.OBJ), "GET") is False
        assert le._enforce_cached(allowing, dict(self.OBJ), "GET") is True

    def test_the_same_enforcer_reuses_its_own_decision(self):
        # The optimization still holds within one enforcer: a second identical question is not re-asked.
        le.claims_and_roles = {"tokens": ["u1"]}
        enf = MagicMock()
        enf.enforce.return_value = True
        le._enforce_cached(enf, dict(self.OBJ), "GET")
        le._enforce_cached(enf, dict(self.OBJ), "GET")
        assert enf.enforce.call_count == 1

    def test_a_different_caller_identity_does_not_share_an_entry(self):
        # Same enforcer object, different claims: the identity leads the key, so the entries are distinct.
        enf = MagicMock()
        enf.enforce.return_value = True
        le.claims_and_roles = {"tokens": ["user-a"]}
        le._enforce_cached(enf, dict(self.OBJ), "GET")
        le.claims_and_roles = {"tokens": ["user-b"]}
        le._enforce_cached(enf, dict(self.OBJ), "GET")
        assert enf.enforce.call_count == 2, "two identities shared one cached decision"

    def test_a_different_mfa_state_does_not_share_an_entry(self):
        # MFA-gated roles are only active for an MFA session, so the decision differs by MFA state.
        enf = MagicMock()
        enf.enforce.return_value = True
        le.claims_and_roles = {"tokens": ["u1"], "mfaEnabled": False}
        le._enforce_cached(enf, dict(self.OBJ), "GET")
        le.claims_and_roles = {"tokens": ["u1"], "mfaEnabled": True}
        le._enforce_cached(enf, dict(self.OBJ), "GET")
        assert enf.enforce.call_count == 2, "two MFA states shared one cached decision"

    def test_a_different_action_does_not_share_an_entry(self):
        # GET and POST are different questions; an abort must not inherit a read's allow.
        enf = MagicMock()
        enf.enforce.return_value = True
        le.claims_and_roles = {"tokens": ["u1"]}
        le._enforce_cached(enf, dict(self.OBJ), "GET")
        le._enforce_cached(enf, dict(self.OBJ), "POST")
        assert enf.enforce.call_count == 2

    def test_two_workflows_in_one_database_do_not_share_an_entry(self):
        # A workflow object carries no assetId, so workflowId must be part of the key.
        enf = MagicMock()
        enf.enforce.return_value = True
        le.claims_and_roles = {"tokens": ["u1"]}
        le._enforce_cached(enf, {"object__type": "workflow", "databaseId": "db",
                                 "workflowId": "wf1"}, "GET")
        le._enforce_cached(enf, {"object__type": "workflow", "databaseId": "db",
                                 "workflowId": "wf2"}, "GET")
        assert enf.enforce.call_count == 2


@pytest.mark.unit
class TestEntityResolutionBound:
    """A page resolves a bounded number of DISTINCT assets. Beyond it, rows are WITHHELD (never
    admitted unchecked) and the bound is stated in the response warnings."""

    def test_a_page_over_many_distinct_assets_reports_the_bound(self):
        # Each row reads its own 10 assets, so the distinct-asset count crosses the bound mid-page.
        per_row = 10
        row_count = (le.MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE // per_row) + 5
        rows = _main_rows(row_count)
        message = _run_global_list(
            rows,
            lambda eid: [(f"db", f"{eid}-asset-{i}") for i in range(per_row)],
            _allow_all())
        assert message.get("warnings"), "the bound must be stated, not silent"
        assert str(le.MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE) in message["warnings"][0]
        # Rows beyond the bound are withheld, not listed unchecked.
        assert len(message["Items"]) < row_count

    def test_rows_within_the_bound_are_still_listed(self):
        rows = _main_rows(3)
        message = _run_global_list(rows, lambda eid: [("db", f"{eid}-a")], _allow_all())
        assert len(message["Items"]) == 3
        assert "warnings" not in message

    def test_a_page_over_shared_assets_is_never_bounded(self):
        # The bound is on the BREADTH of distinct assets, not the row count: many rows over a few
        # shared assets must page normally however long the page is.
        rows = _main_rows(200)
        message = _run_global_list(rows, lambda eid: [("db", "shared")], _allow_all())
        assert len(message["Items"]) == 200
        assert "warnings" not in message

    def test_the_withheld_rows_stay_reachable_through_the_next_token(self):
        per_row = 10
        row_count = (le.MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE // per_row) + 5
        message = _run_global_list(
            _main_rows(row_count),
            lambda eid: [("db", f"{eid}-asset-{i}") for i in range(per_row)],
            _allow_all(), last_key={"workflowExecutionId": "E0"})
        assert "NextToken" in message, "a bounded page must remain continuable"
        assert message.get("warnings")

    def test_a_bounded_page_is_continuable_even_when_the_query_is_exhausted(self):
        """The bound can stop a page part-way through a query DynamoDB reports as exhausted.

        There is no LastEvaluatedKey in that case, so a token taken only from the response would be
        absent and the withheld executions would be unreachable — not deferred. The walk therefore
        carries its own resume point. Note the previous test supplies a last_key explicitly, so it
        cannot distinguish this case; this one supplies none."""
        per_row = 10
        row_count = (le.MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE // per_row) + 5
        message = _run_global_list(
            _main_rows(row_count),
            lambda eid: [("db", f"{eid}-asset-{i}") for i in range(per_row)],
            _allow_all())                      # <- no last_key: the query is exhausted
        assert message.get("warnings"), "the bound must be stated"
        assert "NextToken" in message, (
            "a bounded page with no LastEvaluatedKey must still be continuable, "
            "or the withheld executions are unreachable")
        key = json.loads(base64.b64decode(message["NextToken"]))
        assert set(key) == {"allListPartition", "executionStartDate",
                            "workflowExecutionId", "workflowDatabaseId:workflowId"}, (
            "a GSI continuation must name the index keys AND the base-table keys")

    def test_the_warning_only_promises_a_token_when_one_is_present(self):
        # A row missing its key attributes yields no synthesized token, so the advice must not name one.
        rows = [{"workflowExecutionId": f"E{i}", "workflowId": "wf", "workflowDatabaseId": "db",
                 "executionStatus": "SUCCEEDED", "executionStartDate": "2026-01-01T00:00:00Z"}
                for i in range((le.MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE // 10) + 5)]
        message = _run_global_list(
            rows, lambda eid: [("db", f"{eid}-a{i}") for i in range(10)], _allow_all())
        warning = " ".join(message.get("warnings") or [])
        assert warning, "the bound must still be stated"
        if "NextToken" not in message:
            assert "NextToken" not in warning, (
                "the warning named a continuation the response does not offer")

    def test_the_budget_is_disarmed_after_the_page(self):
        # A single-execution authorization later in the same invocation must not inherit the page bound.
        _run_global_list(_main_rows(2), lambda eid: [("db", "a")], _allow_all())
        assert le._authz_entity_budget_exceeded() is False
        assert le._authz_entities_within_budget(
            [("db", f"a{i}") for i in range(le.MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE * 2)]) is True

    def test_the_budget_bounds_breadth_not_repeats(self):
        le._arm_authz_entity_budget(limit=3)
        assert le._authz_entities_within_budget([("db", "a1"), ("db", "a1"), ("db", "a1")]) is True
        assert le._authz_entity_budget_exceeded() is False

    def test_one_asset_repeated_past_the_limit_within_a_single_call_is_admitted(self):
        # A run that names the SAME asset more times than the whole budget is one distinct asset, so it
        # must pass. Counting the repeats rather than the distinct set would bound a run on its input
        # COUNT — an execution over one asset's thousand files would be withheld from every list.
        le._arm_authz_entity_budget(limit=5)
        assert le._authz_entities_within_budget([("db", "shared")] * 50) is True
        assert le._authz_entity_budget_exceeded() is False

    def test_an_already_resolved_asset_does_not_recount_against_the_budget(self):
        # Rows later in a page reference assets earlier rows already resolved; those are memo hits and
        # must not consume budget a second time.
        le._arm_authz_entity_budget(limit=3)
        le._asset_details_cache[("db", "a1")] = {"assetId": "a1"}
        le._asset_details_cache[("db", "a2")] = {"assetId": "a2"}
        assert le._authz_entities_within_budget([("db", "a1"), ("db", "a2")] * 20) is True
        assert le._authz_entity_budget_exceeded() is False

    def test_exceeding_the_budget_flags_it(self):
        le._arm_authz_entity_budget(limit=2)
        assert le._authz_entities_within_budget([("db", "a1"), ("db", "a2"), ("db", "a3")]) is False
        assert le._authz_entity_budget_exceeded() is True

    def test_a_disarmed_budget_admits_any_breadth(self):
        le._disarm_authz_entity_budget()
        assert le._authz_entities_within_budget([("db", f"a{i}") for i in range(10000)]) is True


@pytest.mark.unit
class TestListedRowNeverForbiddenWhenOpened:
    """The invariant #68 must not weaken, stated directly: for every row the list returns, opening its
    details authorizes. The bound may only ever REMOVE rows from the list, never admit unchecked ones."""

    def test_every_listed_row_authorizes_on_the_details_path(self):
        rows = _main_rows(8)

        def assets_for(eid):
            return [("db", "secret")] if eid in ("E2", "E5") else [("db", f"ok-{eid}")]

        enf = MagicMock()
        enf.enforceAPI.return_value = True
        enf.enforce.side_effect = lambda obj, action, *a, **k: not (
            obj.get("object__type") == "asset" and obj.get("assetId") == "secret")
        message = _run_global_list(rows, assets_for, enf)
        listed = [i["workflowExecutionId"] for i in message["Items"]]
        assert "E2" not in listed and "E5" not in listed

        # Each listed row must now pass the details-path rule under the same caller.
        for execution_id in listed:
            le._asset_details_cache.clear()
            le._authz_decision_cache.clear()
            main = next(r for r in rows if r["workflowExecutionId"] == execution_id)
            le.claims_and_roles = {"tokens": ["u1"]}
            with patch(f"{MOD}.CasbinEnforcer", return_value=enf), \
                 patch(f"{MOD}.get_execution_input_assets",
                       side_effect=lambda eid, _e=execution_id: assets_for(_e)), \
                 patch(f"{MOD}._get_asset_details_cached",
                       side_effect=lambda d, a: {"databaseId": d, "assetId": a, "assetName": a}), \
                 patch(f"{MOD}.prewarm_asset_details"), \
                 patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}):
                allowed, reason = le.authorize_execution_access(execution_id, main, "GET")
            assert allowed is True, f"listed row {execution_id} would 403 when opened: {reason}"

    def test_a_bounded_page_withholds_rather_than_admits(self):
        # When the bound stops resolution, the affected rows must be ABSENT from Items — the failure
        # mode to exclude is a row listed on assets that were never checked.
        per_row = 10
        row_count = (le.MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE // per_row) + 5
        rows = _main_rows(row_count)
        message = _run_global_list(
            rows, lambda eid: [("db", f"{eid}-asset-{i}") for i in range(per_row)], _allow_all())
        listed = {i["workflowExecutionId"] for i in message["Items"]}
        # The bound was reached, so at least one row was withheld and nothing was admitted unchecked.
        assert len(listed) < row_count
        assert message.get("warnings")


@pytest.mark.unit
class TestDetailByteBudgetSplit:
    """The detail view's file/metadata byte split, pinned by CALLING the allocator rather than by
    assuming its constants. Owned elsewhere; this is the property the paged metadata endpoint is the
    escalation path for, so a change to it should surface here."""

    def test_the_two_budgets_never_exceed_the_ceiling(self):
        ceiling = le.DETAIL_RESPONSE_BYTE_CEILING
        for file_bytes in (0, 1024, ceiling // 2, ceiling, ceiling * 3):
            file_budget, metadata_budget = le._allocate_detail_byte_budgets(file_bytes)
            assert file_budget >= 0 and metadata_budget >= 0
            assert min(file_bytes, file_budget) + metadata_budget <= ceiling, (
                f"file_bytes={file_bytes} overflows the ceiling")

    def test_metadata_always_keeps_at_least_its_floor(self):
        # A file-heavy execution still shows some metadata rather than three empty tables.
        _file_budget, metadata_budget = le._allocate_detail_byte_budgets(
            le.DETAIL_RESPONSE_BYTE_CEILING * 4)
        assert metadata_budget >= le.MIN_DETAIL_METADATA_BYTES_RETURNED

    def test_files_are_served_before_metadata(self):
        # Light files leave the remainder to metadata; heavy files take their whole allowance.
        _fb_light, md_light = le._allocate_detail_byte_budgets(1024)
        _fb_heavy, md_heavy = le._allocate_detail_byte_budgets(
            le.DETAIL_RESPONSE_BYTE_CEILING)
        assert md_light > md_heavy

    def test_the_file_allowance_reserves_the_metadata_floor(self):
        # The reservation comes OUT of the file allowance, which is what keeps the sum bounded.
        file_budget, _md = le._allocate_detail_byte_budgets(0)
        assert file_budget == (le.DETAIL_RESPONSE_BYTE_CEILING
                               - le.MIN_DETAIL_METADATA_BYTES_RETURNED)
