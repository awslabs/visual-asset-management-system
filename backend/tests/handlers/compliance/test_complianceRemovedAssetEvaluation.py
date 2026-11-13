# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Archiving or permanently deleting an asset closes its pending evaluations and waiting pipeline
rules with no verdict (D126).

Archiving an asset while one of its pipeline rules waited ended the run the rule waited for; the
callback then relaunched the waiting rule, the launch was refused (the asset is archived), and the
rule was recorded as a tooling error, so the evaluation finished `compliant` from its other rules
and the archived asset's history read compliant though its last measured run failed. Now:

- the compliance trigger, on the asset-table REMOVE of an archive or a permanent delete, closes every
  `pending_pipeline` evaluation of the asset: its outstanding pipeline rules end with "Not completed:
  the asset was archived" (or "deleted"), the evaluation ends with no verdict (`closedReason`
  `assetArchived` / `assetDeleted`, the same message), an `evaluation_error` audit entry says why,
  and the asset-state row records the evaluation as `error` (its state is not changed);
- the workflow callback, which may see the run end before the trigger sees the archive, does the
  same instead of recording the run's result or launching a waiting rule for an archived or deleted
  asset;
- an unarchive changes nothing.

Run against the D113 world (moto doubles from the CDK table definitions, the real execute handler
over the lock table, the real callback) with the real trigger.
"""

import json
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import ASSET, DB, REAL_TO_UPDATE_EXPR
from backend.tests.handlers.compliance.test_compliancePipelineRuleWaits import (
    FAILING, RULE, World,
)
from common.compliance import evaluationEngine as engine
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceTrigger as trigger
from handlers.compliance import complianceWorkflowCallback as callback

ARCHIVED_TEXT = "Not completed: the asset was archived"
DELETED_TEXT = "Not completed: the asset was deleted"


@pytest.fixture
def world():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, (store, callback, trigger))
        patches.append(patch.object(store, "to_update_expr", REAL_TO_UPDATE_EXPR))
        if "workflowExecutionInputsStorage" not in doubles:
            inputs = _cdkTables.storage_tables()["dynamoTables/workflowExecutionInputsStorage"]
            resource.create_table(**inputs.create_table_kwargs("t-workflow-execution-inputs"))
            doubles["workflowExecutionInputsStorage"] = resource.Table("t-workflow-execution-inputs")
        for active in patches:
            active.start()
        built = []

        def build(restriction="perAsset", **options):
            built.append(World(doubles, restriction, **options))
            return built[-1]
        try:
            yield build
        finally:
            for one in built:
                one.stop()
            for active in reversed(patches):
                active.stop()


def _archive(w):
    """What an archive does to the asset row: it moves under `<databaseId>#deleted`."""
    table = w.doubles["assetStorage"]
    row = table.get_item(Key={"databaseId": DB, "assetId": ASSET})["Item"]
    table.put_item(Item={**row, "databaseId": f"{DB}#deleted"})
    table.delete_item(Key={"databaseId": DB, "assetId": ASSET})


def _delete_permanently(w):
    table = w.doubles["assetStorage"]
    for database_id in (DB, f"{DB}#deleted"):
        table.delete_item(Key={"databaseId": database_id, "assetId": ASSET})


def _remove_stream(database_id=DB):
    """The asset-table REMOVE of the given partition, as the asset indexer relays it."""
    trigger._process_asset_removal(database_id, ASSET)


def _two_evaluations(w):
    """e1 runs (r1 holds the lock); e2 waits for r1."""
    e1 = w.evaluate()
    (r1,) = w.launched()
    e2 = w.evaluate()
    assert w.tracking(e2["evaluationId"])["status"] == store.PIPELINE_EXECUTION_WAITING
    return e1, e2, r1


def _assert_closed(w, evaluation_id, reason, text):
    evaluation = w.evaluation(evaluation_id)
    assert (evaluation["status"], evaluation["verdict"]) == ("error", "error")
    assert (evaluation["closedReason"], evaluation["errorMessage"]) == (reason, text)
    assert evaluation["violations"] == []
    assert {entry["status"] for entry in evaluation["pipelineExecutions"]} == {"completed"}
    row = w.tracking(evaluation_id)
    assert row["status"] == store.PIPELINE_EXECUTION_COMPLETED
    assert "waitingWorkflowKey" not in row
    assert json.loads(row["ruleResults"])[0]["message"] == text
    audits = [a for a in w.audits("evaluation_error") if a.get("evaluationId") == evaluation_id]
    assert len(audits) == 1
    details = audits[0]["details"]
    details = json.loads(details) if isinstance(details, str) else details
    assert (details["closedReason"], details["reason"]) == (reason, text)


@pytest.mark.unit
class TestArchivingClosesTheAssetsOpenEvaluations:

    def test_the_trigger_closes_them_and_the_run_ending_after_launches_nothing(self, world):
        w = world()
        e1, e2, r1 = _two_evaluations(w)
        _archive(w)
        _remove_stream()
        _assert_closed(w, e1["evaluationId"], "assetArchived", ARCHIVED_TEXT)
        _assert_closed(w, e2["evaluationId"], "assetArchived", ARCHIVED_TEXT)
        assert w.waiting_index() == []
        state = w.state()
        # The record stays (an archived asset keeps it); the closed evaluation decided nothing.
        assert (state["lastEvaluationId"], state["lastEvaluationStatus"]) == (
            e2["evaluationId"], "error")
        assert state["complianceState"] == engine.STATE_PENDING_EVALUATION

        w.end(r1, output=FAILING)
        assert w.launched() == [r1]
        assert w.evaluation(e2["evaluationId"])["closedReason"] == "assetArchived"

    def test_the_callback_closes_them_when_the_run_ends_before_the_trigger_sees_the_archive(
            self, world):
        w = world()
        e1, e2, r1 = _two_evaluations(w)
        _archive(w)
        w.end(r1, output=FAILING)
        # No relaunch for an archived asset, and no verdict for either evaluation.
        assert w.launched() == [r1]
        _assert_closed(w, e1["evaluationId"], "assetArchived", ARCHIVED_TEXT)
        _assert_closed(w, e2["evaluationId"], "assetArchived", ARCHIVED_TEXT)
        # The trigger's REMOVE, arriving after, finds nothing left to close.
        _remove_stream()
        assert len([a for a in w.audits("evaluation_error")
                    if a.get("evaluationId") == e2["evaluationId"]]) == 1

    def test_an_unarchive_closes_nothing(self, world):
        w = world()
        _e1, e2, _r1 = _two_evaluations(w)
        # The unarchive's REMOVE is of the archive partition; the live row is back.
        _remove_stream(f"{DB}#deleted")
        assert w.evaluation(e2["evaluationId"])["status"] == engine.EVALUATION_STATUS_PENDING_PIPELINE
        assert w.tracking(e2["evaluationId"])["status"] == store.PIPELINE_EXECUTION_WAITING


@pytest.mark.unit
class TestAPermanentDeleteClosesTheAssetsOpenEvaluations:

    def test_the_trigger_closes_them_and_removes_the_record(self, world):
        w = world()
        e1, e2, r1 = _two_evaluations(w)
        _delete_permanently(w)
        _remove_stream()
        _assert_closed(w, e1["evaluationId"], "assetDeleted", DELETED_TEXT)
        _assert_closed(w, e2["evaluationId"], "assetDeleted", DELETED_TEXT)
        assert "Item" not in w.doubles["complianceAssetStateStorage"].get_item(
            Key={"databaseId": DB, "assetId": ASSET})
        assert len(w.audits("asset_deleted")) == 1
        w.end(r1, output=FAILING)
        assert w.launched() == [r1]
