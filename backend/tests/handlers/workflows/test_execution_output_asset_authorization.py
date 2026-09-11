# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-009 (S2-BACKEND-065): the asset an execution WROTE to is a Tier-2 gate on every path.

`_execution_access_check` is the single Tier-2 rule behind nine call sites. It enforced `asset_action` on
the assets a run READ, but on the asset the run WROTE to only when the run had no inputs of either kind.
Any run with an input file or a metadata-source asset therefore disclosed its OUTPUT asset's identity,
the inventory of files written into it, the metadata produced against it and the pipelines' results text
— plus its raw logs, which carry write-back S3 keys and cannot be selectively redacted — to a caller
authorized only on the INPUT asset. The launch already required POST on that output asset
(executeWorkflow._resolve_and_authorize_assets), so the write was gated and the read of what was written
was not.

Three shapes reach the gap without anybody deliberately overriding an output target:
  - `inputFileArity: 'none'` + `locationType: 'asset'`, where the workflow model MAKES
    `allowOverride: true` mandatory, combined with metadata-source assets;
  - two or more input assets, where the execute handler REQUIRES an explicit output target regardless of
    `allowOverride`;
  - a single input asset with `allowOverride: true` and an explicit output.

The tests below are deliberately built so a NO-OP implementation fails: every denial case supplies a
configuration row naming a REDIRECTED output asset *and* inputs *and* an enforcer that denies only the
output asset. The permitted cases matter as much: over-tightening takes execution history away from
legitimate users, so the memoisation, the read counts, the results-only shape and the deleted/archived
output asset each have their own pin. Negative assertions assert on the ARGUMENTS handed to Casbin, not
only on the verdict, because the enforcer is a MagicMock whose default return is truthy.

executionService resolves its table names at import (mirrors test_executions_authz_bound.py)."""

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

from backend.backend.handlers.workflows import executionService as le  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"

EXEC_ID = "e1000000000000000000000000000001"
MAIN = {"workflowExecutionId": EXEC_ID, "workflowId": "wf", "workflowDatabaseId": "wf-db"}

# The finding's own shape: the run READ (db-public, A) and WROTE into (db-secret, B).
INPUT_PAIR = ("db-public", "A")
REDIRECTED = {"outputLocationType": "asset", "outputDatabaseId": "db-secret", "outputAssetId": "B"}


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
    enf = MagicMock()
    enf.enforce.return_value = True
    enf.enforceAPI.return_value = True
    return enf


def _denying_assets(*denied, actions=None):
    """Allows everything except the named ASSET ids, and only for `actions` when given.

    `actions=('POST',)` is what makes the abort-vs-read asymmetry testable: the same caller reads the
    output asset and cannot write it."""
    enf = MagicMock()

    def _enforce(obj, action, *a, **k):
        if obj.get("object__type") != "asset":
            return True
        if obj.get("assetId", "") not in denied:
            return True
        return actions is not None and action not in actions

    enf.enforce.side_effect = _enforce
    enf.enforceAPI.return_value = True
    return enf


def _denying_databases(*denied, actions=None):
    enf = MagicMock()

    def _enforce(obj, action, *a, **k):
        if obj.get("object__type") != "database":
            return True
        if obj.get("databaseId", "") not in denied:
            return True
        return actions is not None and action not in actions

    enf.enforce.side_effect = _enforce
    enf.enforceAPI.return_value = True
    return enf


def _row(database_id, asset_id):
    return {"databaseId": database_id, "assetId": asset_id, "assetName": f"name-{asset_id}"}


def _batch_stub(resolvable):
    """A BatchGetItem stub over the ACTIVE partition only (which is what the real batch addresses),
    returning a row for each requested key present in `resolvable`."""
    def _batch(RequestItems):
        return {"Responses": {
            name: [_row(k["databaseId"], k["assetId"]) for k in spec["Keys"]
                   if (k["databaseId"], k["assetId"]) in resolvable]
            for name, spec in RequestItems.items()}}
    return _batch


class _Harness:
    """Runs the real rule with only the leaf DynamoDB reads stubbed.

    `prewarm_asset_details`, `_get_asset_details_cached`, `_enforce_cached` and the entity budget all run
    for real, so the read-count and enforce-count assertions below measure the production code rather
    than a stub."""

    def __init__(self, config_row, input_assets, enforcer, resolvable=None):
        self.config_row = config_row
        self.input_assets = input_assets
        self.enforcer = enforcer
        self.resolvable = (set(resolvable) if resolvable is not None
                           else set(input_assets) | self._output_pair(config_row))
        self.batch = MagicMock(side_effect=_batch_stub(self.resolvable))
        self.per_item = MagicMock(
            side_effect=lambda d, a: _row(d, a) if (d, a) in self.resolvable else None)
        self.prewarm = None

    @staticmethod
    def _output_pair(config_row):
        pair = (config_row.get("outputDatabaseId", ""), config_row.get("outputAssetId", ""))
        return {pair} if all(pair) else set()

    def run(self, action="GET"):
        """authorize_execution_access under `action`. Returns (allowed, reason)."""
        le.claims_and_roles = {"tokens": ["u1"]}
        le._asset_details_cache.clear()
        le._authz_decision_cache.clear()
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=self.enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=self.input_assets), \
             patch(f"{MOD}.get_asset_details", self.per_item), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=self.config_row), \
             patch(f"{MOD}.prewarm_asset_details",
                   side_effect=le.prewarm_asset_details) as prewarm:
            ddb.batch_get_item = self.batch
            self.prewarm = prewarm
            return le.authorize_execution_access(EXEC_ID, MAIN, action)

    def visible(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        le._asset_details_cache.clear()
        le._authz_decision_cache.clear()
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=self.enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=self.input_assets), \
             patch(f"{MOD}.get_asset_details", self.per_item), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=self.config_row):
            ddb.batch_get_item = self.batch
            return le._execution_visible_to_caller(EXEC_ID, MAIN)

    def enforce_objects(self, object_type=None, action=None):
        return [c.args[0] for c in self.enforcer.enforce.call_args_list
                if (object_type is None or c.args[0].get("object__type") == object_type)
                and (action is None or c.args[1] == action)]


@pytest.mark.unit
class TestTheOutputAssetIsGatedWhenTheRunAlsoReadAssets:
    """The defect itself: a run with inputs disclosed its output asset with no check on it."""

    def test_a_redirected_output_asset_denies_the_read(self):
        h = _Harness(REDIRECTED, [INPUT_PAIR], _denying_assets("B"))
        allowed, reason = h.run("GET")
        assert allowed is False, "a run that wrote into an unreadable asset was still readable"
        # Positive control on the ARGUMENTS, not just the verdict: the input asset was allowed on its own
        # row and the OUTPUT asset is the object that produced the denial.
        assert "output asset" in reason and "db-secret/B" in reason, reason
        checked = [(o.get("databaseId"), o.get("assetId")) for o in h.enforce_objects("asset")]
        assert checked == [INPUT_PAIR, ("db-secret", "B")], (
            f"the output asset was not the object handed to Casbin: {checked}")

    def test_the_same_run_is_readable_when_the_output_asset_is_readable(self):
        # The control that makes the denial above attributable to the output asset and nothing else.
        h = _Harness(REDIRECTED, [INPUT_PAIR], _allow_all())
        allowed, reason = h.run("GET")
        assert allowed is True, reason
        checked = [(o.get("databaseId"), o.get("assetId")) for o in h.enforce_objects("asset")]
        assert checked == [INPUT_PAIR, ("db-secret", "B")]

    def test_a_metadata_source_only_run_with_a_redirected_output_is_gated(self):
        # The arity-'none' + metadata-sources + explicit-output shape, which the workflow model makes the
        # REQUIRED configuration for locationType 'asset' with inputFileArity 'none'. It has no input
        # FILES, so the pre-fix guard saw a non-empty metadata-source set and skipped the output check.
        config = dict(REDIRECTED,
                      metadataSourceAssets=[{"databaseId": "db-public", "assetId": "src"}])
        h = _Harness(config, [], _denying_assets("B"))
        allowed, reason = h.run("GET")
        assert allowed is False, "an arity-none run with metadata sources skipped its output check"
        assert "db-secret/B" in reason

    def test_a_multi_input_run_with_a_redirected_output_is_gated(self):
        # Two or more input assets: the execute handler REQUIRES an explicit output target here whatever
        # allowOverride says, so this shape needs no workflow reconfiguration to reach the gap.
        h = _Harness(REDIRECTED, [("db-public", "A"), ("db-public", "A2")], _denying_assets("B"))
        allowed, reason = h.run("GET")
        assert allowed is False
        assert "db-secret/B" in reason

    def test_ids_present_without_an_output_location_type_is_still_gated(self):
        # A legacy or migrated row can name both ids and carry no outputLocationType, and the details
        # view projects the ids regardless (defaulting the type to 'asset'). Gating on the TYPE instead of
        # on ids-present would leave exactly those rows disclosed.
        config = {"outputDatabaseId": "db-secret", "outputAssetId": "B"}
        h = _Harness(config, [INPUT_PAIR], _denying_assets("B"))
        allowed, reason = h.run("GET")
        assert allowed is False, "a row with output ids but no outputLocationType escaped the gate"
        assert "db-secret/B" in reason

    def test_the_output_asset_cannot_be_the_only_thing_checked(self):
        # The complement of the fix: gating the output asset must not replace the read-asset span.
        h = _Harness(REDIRECTED, [INPUT_PAIR], _denying_assets("A"))
        allowed, reason = h.run("GET")
        assert allowed is False
        assert "db-public/A" in reason and "output" not in reason


@pytest.mark.unit
class TestTheOrdinaryShapeIsNotOverTightened:
    """The over-tightening catchers. The default single-input run, and every migrated V1 run, has
    outputAssetId == its input asset id, so it must stay readable at the IDENTICAL cost."""

    def test_output_equals_input_costs_no_extra_enforce(self):
        config = {"outputLocationType": "asset", "outputDatabaseId": "db", "outputAssetId": "a1"}
        h = _Harness(config, [("db", "a1")], _allow_all())
        allowed, reason = h.run("GET")
        assert allowed is True, reason
        asset_calls = h.enforce_objects("asset")
        assert len(asset_calls) == 1, (
            f"the ordinary shape now costs {len(asset_calls)} asset evaluations instead of 1; the "
            f"output asset must resolve through the same memo and answer from the decision memo")
        assert h.enforcer.enforce.call_count == 2, (
            f"expected exactly workflow GET + one asset GET, got "
            f"{[c.args[0].get('object__type') for c in h.enforcer.enforce.call_args_list]}")

    def test_output_equals_input_costs_no_extra_dynamodb_read(self):
        config = {"outputLocationType": "asset", "outputDatabaseId": "db", "outputAssetId": "a1"}
        h = _Harness(config, [("db", "a1")], _allow_all())
        assert h.run("GET")[0] is True
        # `entity_pairs` names the pair twice (read asset + output asset), exactly as it did before the
        # fix; prewarm_asset_details de-duplicates, so it is ONE distinct pair on the single-row path and
        # nothing reads it again.
        assert h.prewarm.call_args.args[0] == [("db", "a1"), ("db", "a1")]
        assert h.per_item.call_count == 1
        assert h.batch.call_count == 0

    def test_a_redirected_output_costs_no_extra_dynamodb_read_either(self):
        # The output pair was ALREADY part of entity_pairs and ALREADY pre-warmed before the fix, so
        # enforcing it adds Casbin evaluations only. This is what lets the global-list walk size its work
        # budget against a higher FILTER rate rather than a higher per-row cost.
        h = _Harness(REDIRECTED, [INPUT_PAIR], _allow_all())
        assert h.run("GET")[0] is True
        assert h.prewarm.call_args.args[0] == [INPUT_PAIR, ("db-secret", "B")]
        # Both pairs resolved in ONE batch; no per-item read at all.
        assert h.batch.call_count == 1
        assert h.per_item.call_count == 0

    def test_the_output_pair_does_not_consume_extra_entity_budget(self):
        # The pair is counted by _authz_entities_within_budget exactly as before, so a page's breadth
        # bound is unchanged by the fix.
        le._arm_authz_entity_budget(limit=2)
        h = _Harness(REDIRECTED, [INPUT_PAIR], _allow_all())
        assert h.run("GET")[0] is True
        assert le._authz_entity_budget_exceeded() is False
        le._disarm_authz_entity_budget()

        le._arm_authz_entity_budget(limit=1)
        h = _Harness(REDIRECTED, [INPUT_PAIR], _allow_all())
        assert h.run("GET")[0] is False, "two distinct pairs must not fit a budget of one"
        assert le._authz_entity_budget_exceeded() is True
        le._disarm_authz_entity_budget()


@pytest.mark.unit
class TestResultsOnlyRunsStayReadable:
    """`outputLocationType='none'` stores BOTH output ids as empty strings and the execute handler
    REJECTS a supplied target for such a workflow, so the pair can never be populated. The gate keys on
    ids-present, so workflow GET remains the sole control — which is what makes every LLM-style
    results-only run readable at all."""

    def test_a_results_only_run_with_no_inputs_rests_on_workflow_get(self):
        config = {"outputLocationType": "none", "outputDatabaseId": "", "outputAssetId": ""}
        h = _Harness(config, [], _allow_all())
        allowed, reason = h.run("GET")
        assert allowed is True, reason
        # Exactly one evaluation, and it is the workflow.
        assert [o.get("object__type") for o in h.enforce_objects()] == ["workflow"]
        # A fresh harness, so the list path's calls are counted separately from the details path's.
        h = _Harness(config, [], _allow_all())
        assert h.visible() is True
        assert [o.get("object__type") for o in h.enforce_objects()] == ["workflow"]

    def test_a_results_only_run_with_input_files_is_gated_on_those_files_only(self):
        # The workflow model explicitly allows locationType 'none' with any inputFileArity, so a
        # results-only run CAN have input assets and still no output asset.
        h = _Harness({"outputLocationType": "none"}, [INPUT_PAIR], _allow_all())
        allowed, reason = h.run("GET")
        assert allowed is True, reason
        assert [(o.get("object__type"), o.get("assetId", ""))
                for o in h.enforce_objects()] == [("workflow", ""), ("asset", "A")]

    def test_an_absent_configuration_row_falls_back_to_the_input_assets(self):
        # A legacy run, or a permanent-delete race: {} yields empty ids, so no output check is made.
        h = _Harness({}, [INPUT_PAIR], _denying_assets("B"))
        assert h.run("GET")[0] is True
        assert [o.get("object__type") for o in h.enforce_objects("database")] == []


@pytest.mark.unit
class TestTheAbortVsReadAsymmetry:
    """Reads need GET on the output asset; abort and permanent delete are mutations and need POST —
    matching the launch, which required POST. A literal action in the new block breaks one or the other:
    hard-coding GET under-protects the aborts, hard-coding POST breaks every read for a caller with
    read-only access to the output asset."""

    def _harness(self):
        # Allows GET on the output asset, denies POST on it.
        return _Harness(REDIRECTED, [INPUT_PAIR], _denying_assets("B", actions=("POST",)))

    def test_read_access_is_granted_and_write_access_is_denied(self):
        assert self._harness().run("GET")[0] is True, (
            "a read-only caller lost the execution, so the block hard-coded POST")
        allowed, reason = self._harness().run("POST")
        assert allowed is False, "an abort was authorized without POST on the output asset"
        assert "output asset POST denied" in reason

    def test_the_action_handed_to_casbin_is_the_callers_action(self):
        # The positive control for both directions: the object AND the action reaching Casbin.
        h = self._harness()
        h.run("GET")
        assert [(o.get("assetId"), c.args[1]) for o, c in
                zip(h.enforce_objects("asset"), [c for c in h.enforcer.enforce.call_args_list
                                                 if c.args[0].get("object__type") == "asset"])
                ] == [("A", "GET"), ("B", "GET")]
        h = self._harness()
        h.run("POST")
        assert [c.args[1] for c in h.enforcer.enforce.call_args_list
                if c.args[0].get("object__type") == "asset"] == ["POST", "POST"]

    def test_authorize_abort_hard_codes_post_and_the_reads_do_not(self):
        h = _Harness(REDIRECTED, [INPUT_PAIR], _denying_assets("B", actions=("POST",)))
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=h.enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[INPUT_PAIR]), \
             patch(f"{MOD}.get_asset_details", h.per_item), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=REDIRECTED):
            ddb.batch_get_item = h.batch
            le._asset_details_cache.clear()
            le._authz_decision_cache.clear()
            assert le.authorize_abort(EXEC_ID, MAIN)[0] is False
            le._asset_details_cache.clear()
            le._authz_decision_cache.clear()
            assert le._execution_visible_to_caller(EXEC_ID, MAIN) is True


@pytest.mark.unit
class TestTheDeletedAndArchivedOutputAsset:
    """A deleted output asset defers to its DATABASE under the same action (fail-closed would make
    historical executions unreadable to everyone; fail-open would let a deletion drop the gate). An
    ARCHIVED one is still resolvable, so it must be authorized on its own row rather than downgraded."""

    def test_a_deleted_output_asset_defers_to_its_database(self):
        # Only the input asset resolves; the output pair exists in neither partition.
        h = _Harness(REDIRECTED, [INPUT_PAIR], _allow_all(), resolvable={INPUT_PAIR})
        allowed, reason = h.run("GET")
        assert allowed is True, reason
        databases = [(o.get("databaseId"), c.args[1]) for o, c in
                     zip(h.enforce_objects("database"),
                         [c for c in h.enforcer.enforce.call_args_list
                          if c.args[0].get("object__type") == "database"])]
        assert databases == [("db-secret", "GET")], (
            f"the deleted output asset did not defer to its database under the caller's action: "
            f"{databases}")

    def test_denying_the_deleted_output_assets_database_denies_the_execution(self):
        h = _Harness(REDIRECTED, [INPUT_PAIR], _denying_databases("db-secret"),
                     resolvable={INPUT_PAIR})
        allowed, reason = h.run("GET")
        assert allowed is False, "a deleted output asset dropped the gate entirely"
        assert "db-secret" in reason

    def test_the_deleted_output_assets_database_is_checked_under_post_for_an_abort(self):
        h = _Harness(REDIRECTED, [INPUT_PAIR], _denying_databases("db-secret", actions=("POST",)),
                     resolvable={INPUT_PAIR})
        assert h.run("GET")[0] is True
        h = _Harness(REDIRECTED, [INPUT_PAIR], _denying_databases("db-secret", actions=("POST",)),
                     resolvable={INPUT_PAIR})
        assert h.run("POST")[0] is False

    def test_a_deleted_input_and_output_in_one_database_make_one_database_check(self):
        config = {"outputLocationType": "asset", "outputDatabaseId": "gone-db", "outputAssetId": "B"}
        h = _Harness(config, [("gone-db", "A")], _allow_all(), resolvable=set())
        assert h.run("GET")[0] is True
        assert [o.get("databaseId") for o in h.enforce_objects("database")] == ["gone-db"]

    def test_an_archived_output_asset_is_authorized_on_its_own_row(self):
        """Archiving moves the row to the `databaseId + '#deleted'` partition and is REVERSIBLE, so the
        output asset must still be authorized on its own attributes. The batched pre-warm addresses the
        ACTIVE partition only, so this is a batch MISS that the per-item fall-back resolves from the
        archived partition — memoising that miss as None would silently downgrade every archived output
        asset to a (weaker) database check without failing any other test."""
        archived = {"databaseId": f"db-secret{le.ARCHIVED_DATABASE_SUFFIX}", "assetId": "B",
                    "assetName": "archived-secret"}

        def _query(KeyConditionExpression=None, **kwargs):
            requested = [v for cond in getattr(KeyConditionExpression, "_values", ())
                         for v in getattr(cond, "_values", ()) if isinstance(v, str)]
            if any(v.endswith(le.ARCHIVED_DATABASE_SUFFIX) for v in requested):
                return {"Items": [archived]}
            return {"Items": []}

        enf = MagicMock()
        # Deny by NAME, which only the asset row carries — so a fallback to the database would allow.
        enf.enforce.side_effect = lambda obj, action, *a, **k: (
            obj.get("assetName") != "archived-secret" if obj.get("object__type") == "asset" else True)
        le.claims_and_roles = {"tokens": ["u1"]}
        le._asset_details_cache.clear()
        le._authz_decision_cache.clear()
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.asset_table") as table, \
             patch(f"{MOD}.CasbinEnforcer", return_value=enf), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[INPUT_PAIR]), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=REDIRECTED):
            # The batch resolves the input asset only (active partition).
            ddb.batch_get_item = MagicMock(side_effect=_batch_stub({INPUT_PAIR}))
            table.query = MagicMock(side_effect=_query)
            allowed, reason = le.authorize_execution_access(EXEC_ID, MAIN, "GET")
        assert allowed is False, "an archived output asset was downgraded to a database check"
        assert "db-secret/B" in reason
        objects = [o for o in (c.args[0] for c in enf.enforce.call_args_list)
                   if o.get("assetId") == "B"]
        assert objects and objects[0].get("object__type") == "asset"
        assert objects[0].get("assetName") == "archived-secret", (
            "the enforce object was not the archived row's own attributes")
        assert [o for o in (c.args[0] for c in enf.enforce.call_args_list)
                if o.get("object__type") == "database"] == [], (
            "an archived (resolvable) output asset must not fall back to its database")


@pytest.mark.unit
class TestListDetailsAgreementMatrix:
    """`_execution_visible_to_caller` and `authorize_execution_access(..., 'GET')` must return the SAME
    verdict for the same execution, or a row lists and then 403s — or worse, is hidden from the list and
    readable directly. Agreement is structural (both delegate to `_execution_access_check`), but the
    matrix is what catches a future implementer putting a check in one wrapper."""

    SHAPES = {
        "inputs-only": ({"outputLocationType": "none"}, [INPUT_PAIR]),
        "metadata-sources-only": (
            {"outputLocationType": "none",
             "metadataSourceAssets": [{"databaseId": "db-public", "assetId": "src"}]}, []),
        "results-only": ({"outputLocationType": "none"}, []),
        "no-inputs-with-output": (REDIRECTED, []),
        "inputs-with-redirected-output": (REDIRECTED, [INPUT_PAIR]),
        "output-asset-deleted": (REDIRECTED, [INPUT_PAIR]),
    }

    @pytest.mark.parametrize("shape", sorted(SHAPES))
    @pytest.mark.parametrize("deny_output", [False, True])
    def test_the_two_paths_agree(self, shape, deny_output):
        config, inputs = self.SHAPES[shape]
        resolvable = None
        if shape == "output-asset-deleted":
            resolvable = set(inputs)
        enforcer = (_denying_assets("B") if deny_output else _allow_all())
        allowed = _Harness(config, inputs, enforcer, resolvable=resolvable).run("GET")[0]
        visible = _Harness(config, inputs, enforcer, resolvable=resolvable).visible()
        assert allowed == visible, (
            f"{shape} (deny_output={deny_output}): the list said {visible} and the details path said "
            f"{allowed}")

    def test_the_matrix_contains_at_least_one_denial(self):
        # A positive control on the matrix itself: without a shape that actually denies, the agreement
        # assertions above would all be True == True and prove nothing.
        verdicts = {
            shape: _Harness(config, inputs, _denying_assets("B"),
                            resolvable=set(inputs) if shape == "output-asset-deleted" else None
                            ).run("GET")[0]
            for shape, (config, inputs) in self.SHAPES.items()}
        assert verdicts["inputs-with-redirected-output"] is False
        assert verdicts["no-inputs-with-output"] is False
        assert verdicts["results-only"] is True
        assert verdicts["inputs-only"] is True


@pytest.mark.unit
class TestTheDenialNeverEchoesTheOutputAssetToTheCaller:
    """`denied_reason` names the output asset's ids and is for LOGGING only. Returning it would recreate
    the very disclosure the fix closes, in a smaller but still cross-tenant form (backend/CLAUDE.md
    Rule 11)."""

    def _forbidden(self, response):
        body = response["body"]
        return [token for token in ("db-secret", "B", "output asset") if token in body]

    def _run_entry_point(self, call):
        h = _Harness(REDIRECTED, [INPUT_PAIR], _denying_assets("B"))
        le.claims_and_roles = {"tokens": ["u1"]}
        le._asset_details_cache.clear()
        le._authz_decision_cache.clear()
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=h.enforcer), \
             patch(f"{MOD}.get_execution_main_row", return_value=MAIN), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[INPUT_PAIR]), \
             patch(f"{MOD}.get_asset_details", h.per_item), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=REDIRECTED), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[]), \
             patch(f"{MOD}.sfn"):
            ddb.batch_get_item = h.batch
            ddb.Table.return_value = MagicMock()
            return call()

    def test_details_403s_without_naming_the_output_target(self):
        resp = self._run_entry_point(lambda: le.get_execution_details({}, EXEC_ID))
        assert resp["statusCode"] == 403
        assert self._forbidden(resp) == [], resp["body"]

    def test_details_metadata_403s_without_naming_the_output_target(self):
        resp = self._run_entry_point(
            lambda: le.get_execution_details_metadata({}, EXEC_ID, {"collection": "output"}))
        assert resp["statusCode"] == 403
        assert self._forbidden(resp) == [], resp["body"]

    def test_logs_403s_without_naming_the_output_target(self):
        resp = self._run_entry_point(
            lambda: le.get_execution_logs({}, EXEC_ID, {"mode": "truncated"}))
        assert resp["statusCode"] == 403
        assert self._forbidden(resp) == [], resp["body"]

    def test_abort_403s_without_naming_the_output_target(self):
        resp = self._run_entry_point(lambda: le.abort_execution({}, EXEC_ID))
        assert resp["statusCode"] == 403
        assert self._forbidden(resp) == [], resp["body"]

    def test_permanent_delete_403s_without_naming_the_output_target(self):
        resp = self._run_entry_point(lambda: le.permanent_delete_execution({}, EXEC_ID))
        assert resp["statusCode"] == 403
        assert self._forbidden(resp) == [], resp["body"]

    def test_rerun_403s_without_naming_the_output_target(self):
        resp = self._run_entry_point(
            lambda: le.rerun_execution({}, EXEC_ID, type("M", (), {"executionGroupId": None})()))
        assert resp["statusCode"] == 403
        assert self._forbidden(resp) == [], resp["body"]

    def test_the_reads_succeed_for_a_caller_who_can_read_the_output_asset(self):
        # The positive control for the five 403s above: with the output asset readable, the same calls
        # reach their handlers rather than the authorization branch.
        h = _Harness(REDIRECTED, [INPUT_PAIR], _allow_all())
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=h.enforcer), \
             patch(f"{MOD}.get_execution_main_row", return_value=dict(MAIN)), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[INPUT_PAIR]), \
             patch(f"{MOD}.get_asset_details", h.per_item), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=REDIRECTED), \
             patch(f"{MOD}._reconcile_main_status"), \
             patch(f"{MOD}.assemble_execution_details", return_value={"ok": True}), \
             patch(f"{MOD}.page_detail_metadata", return_value={"Items": []}):
            ddb.batch_get_item = h.batch
            le._asset_details_cache.clear()
            le._authz_decision_cache.clear()
            assert le.get_execution_details({}, EXEC_ID)["statusCode"] == 200
            le._asset_details_cache.clear()
            le._authz_decision_cache.clear()
            assert le.get_execution_details_metadata(
                {}, EXEC_ID, {"collection": "output"})["statusCode"] == 200


@pytest.mark.unit
class TestGroupAbortStaysOpaque:
    """A group is enumerated regardless of the caller's access, so a member withheld by the new output
    check must be counted opaquely — never named. The fix raises that count for groups containing
    redirected-output runs, which is exactly when a leak would start to matter."""

    def test_a_member_denied_on_its_output_asset_is_counted_not_named(self):
        members = [{"workflowExecutionId": f"e{i}" + "0" * 29 + f"{i}",
                    "workflowId": "wf", "workflowDatabaseId": "wf-db",
                    "executionStatus": "SUCCEEDED", "executionStopDate": "2026-01-01T00:00:00Z"}
                   for i in (1, 2, 3)]
        denied_id = members[1]["workflowExecutionId"]
        configs = {m["workflowExecutionId"]: ({"outputLocationType": "none"}
                                              if m["workflowExecutionId"] != denied_id
                                              else REDIRECTED)
                   for m in members}
        h = _Harness(REDIRECTED, [INPUT_PAIR], _denying_assets("B"))
        le.claims_and_roles = {"tokens": ["u1"]}
        le._asset_details_cache.clear()
        le._authz_decision_cache.clear()
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=h.enforcer), \
             patch(f"{MOD}._executions_in_group", return_value=members), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[INPUT_PAIR]), \
             patch(f"{MOD}.get_asset_details", h.per_item), \
             patch(f"{MOD}.get_workflow_execution_configuration_row",
                   side_effect=lambda eid: configs[eid]):
            ddb.batch_get_item = h.batch
            resp = le.abort_group({}, "g1")
        assert resp["statusCode"] == 200
        body = resp["body"]
        message = json.loads(body)["message"]
        assert message["skippedInaccessibleCount"] == 1
        assert denied_id not in {r["executionId"] for r in message["results"]}
        assert denied_id not in body, "the withheld member's id leaked into the response"
        for token in ("db-secret", "output asset"):
            assert token not in body, f"{token} leaked into the group-abort response"


@pytest.mark.unit
class TestTheUnreadableConfigurationRowStillFailsClosed:
    """The pre-existing fail-closed property, re-pinned for a run WITH inputs — previously the raise only
    mattered on the no-inputs branch, because that was the only branch that read the output target."""

    def test_a_failed_configuration_read_raises_for_a_run_with_inputs(self):
        h = _Harness(REDIRECTED, [INPUT_PAIR], _denying_assets("B"))
        le.claims_and_roles = {"tokens": ["u1"]}
        # Healthy read: denied on the output asset.
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=h.enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[INPUT_PAIR]), \
             patch(f"{MOD}.get_asset_details", h.per_item), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=REDIRECTED):
            ddb.batch_get_item = h.batch
            le._asset_details_cache.clear()
            assert le.authorize_execution_access(EXEC_ID, MAIN, "GET")[0] is False
        # Unreadable row: the failure must propagate rather than degrade into an allow.
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=h.enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[INPUT_PAIR]), \
             patch(f"{MOD}.get_asset_details", h.per_item), \
             patch(f"{MOD}.get_workflow_execution_configuration_row",
                   side_effect=Exception("ThrottlingException")):
            ddb.batch_get_item = h.batch
            le._asset_details_cache.clear()
            with pytest.raises(Exception, match="ThrottlingException"):
                le.authorize_execution_access(EXEC_ID, MAIN, "GET")


@pytest.mark.unit
class TestEmptyTokensDenyEveryPath:
    def test_no_tokens_denies_before_anything_is_evaluated(self):
        le.claims_and_roles = {"tokens": []}
        enf = _allow_all()
        with patch(f"{MOD}.CasbinEnforcer", return_value=enf):
            assert le.authorize_execution_access(EXEC_ID, MAIN, "GET") == (False, "no tokens")
            assert le.authorize_abort(EXEC_ID, MAIN) == (False, "no tokens")
            assert le._execution_visible_to_caller(EXEC_ID, MAIN) is False
        assert enf.enforce.call_count == 0


@pytest.mark.unit
class TestThePerAssetListingHonoursTheOutputGate:
    """The asset detail page's Executions tab shares the same rule. A run that read the requested asset
    but wrote into a denied one leaves the tab; a run whose output asset IS the requested asset stays
    (the memo makes that check free)."""

    def _list(self, enforcer, input_assets, config_row):
        le.claims_and_roles = {"tokens": ["u1"]}
        le._asset_details_cache.clear()
        le._authz_decision_cache.clear()
        rows = [{"workflowExecutionId": EXEC_ID, "databaseId": "db-public", "assetId": "A",
                 "workflowId": "wf", "workflowDatabaseId": "wf-db",
                 "executionStartDate": "2026-01-01T00:00:00Z", "inputAssetFileKey": "/f.glb"}]
        main_row = {"workflowExecutionId": EXEC_ID, "workflowId": "wf", "workflowDatabaseId": "wf-db",
                    "executionStatus": "Succeeded", "executionStartDate": "2026-01-01T00:00:00Z",
                    "executionStopDate": "2026-01-01T00:01:00Z"}
        inputs_table, cfg_table, main_table = MagicMock(), MagicMock(), MagicMock()
        inputs_table.query.return_value = {"Items": rows}
        cfg_table.query.return_value = {"Items": []}
        main_table.query.return_value = {"Items": [main_row]}

        def _table(name):
            return {le.workflow_execution_inputs_table: inputs_table,
                    le.workflow_execution_database_v2: main_table,
                    le.workflow_execution_configuration_table: cfg_table}.get(name, MagicMock())

        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_asset_details", side_effect=lambda d, a: _row(d, a)), \
             patch(f"{MOD}.prewarm_asset_details"), \
             patch(f"{MOD}.get_execution_input_assets", return_value=input_assets), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=config_row), \
             patch(f"{MOD}.sfn"):
            ddb.Table.side_effect = _table
            resp = le.get_executions({}, "db-public", "A", "", "", {})
        return [i["workflowExecutionId"]
                for i in json.loads(resp["body"])["message"]["Items"]]

    def test_a_run_that_wrote_into_a_denied_asset_leaves_the_tab(self):
        assert self._list(_denying_assets("B"), [INPUT_PAIR], REDIRECTED) == [], (
            "the asset Executions tab still offered a run whose output asset the caller cannot read")

    def test_the_same_run_stays_listed_when_the_output_asset_is_readable(self):
        assert self._list(_allow_all(), [INPUT_PAIR], REDIRECTED) == [EXEC_ID]

    def test_a_run_whose_output_is_the_requested_asset_stays_listed(self):
        config = {"outputLocationType": "asset", "outputDatabaseId": "db-public", "outputAssetId": "A"}
        assert self._list(_denying_assets("B"), [INPUT_PAIR], config) == [EXEC_ID]
