# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The automatic trigger decides whether an asset is archived when its evaluation starts, not
when the message that queued it arrived.

An unarchive writes the asset's live row back, and its stream INSERT names the live database id.
When the asset is archived again before the queued evaluation runs, the event still says "live"
but the asset table no longer holds the live row. The trigger used to evaluate it anyway: the
evaluation could not read the asset and recorded an `error` evaluation ("Asset could not be read")
and `lastEvaluationStatus: error` on the record. Now the trigger reads the asset table, strongly
consistent, right before the evaluation starts, through the same decision Evaluate All and the
schema sweeps use (`automatic_evaluation_target`): an archived asset is skipped with a log line,
and nothing is written. An asset that is gone everywhere has its record removed, as its
permanent delete removes it.

Run against DynamoDB doubles built from the CDK table definitions (`_cdkTables.table_doubles`),
with the real evaluation.
"""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import REAL_TO_UPDATE_EXPR, schema_row
from backend.tests.handlers.compliance.test_complianceTrigger import _sns_event, _stream_message
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceTrigger as trigger

DB = "race-db"
ASSET = "race-asset"
SCHEMA = "race-schema"
# An asset-attributes rule reads the asset row, so an evaluation of an archived asset errors.
ATTRIBUTE_RULE_BODY = {
    "schemaFormat": "vams-rules-v1",
    "rules": {"naming": {"ruleType": "assetAttributes", "enforcement": "quarantine",
                         "checks": [{"name": "name", "field": "assetName", "matches": "^[A-Z]"}]}},
}
RECORD = {
    "databaseId": DB, "assetId": ASSET, "schemaName": SCHEMA, "schemaSource": "asset",
    "complianceState": "quarantined", "lastEvaluationId": "eval-0",
    "lastEvaluatedAt": "2026-10-01T00:00:00+00:00", "lastEvaluationStatus": "completed",
    "assetName": "Race Bracket", "assetNameSort": "race bracket",
    "assetSearchText": "race bracket race-asset",
}


@pytest.fixture
def tables():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, (store, trigger))
        # The root harness mocks `common.dynamodb`; the store unpacks the real helper's result.
        patches.append(patch.object(store, "to_update_expr", REAL_TO_UPDATE_EXPR))
        for active in patches:
            active.start()
        try:
            doubles["complianceSchemaStorage"].put_item(
                Item=schema_row(SCHEMA, body=ATTRIBUTE_RULE_BODY, database_id=DB))
            doubles["databaseStorage"].put_item(Item={
                "databaseId": DB, "complianceAutoEval": True, "complianceSchemaName": SCHEMA})
            doubles["complianceAssetStateStorage"].put_item(Item=dict(RECORD))
            yield doubles
        finally:
            for active in reversed(patches):
                active.stop()


def _archived(tables):
    """The asset as the archive leaves it: its row only under `<databaseId>#deleted`."""
    tables["assetStorage"].put_item(Item={"databaseId": f"{DB}#deleted", "assetId": ASSET,
                                          "assetName": "Race Bracket"})


def _live(tables):
    tables["assetStorage"].put_item(Item={"databaseId": DB, "assetId": ASSET,
                                          "assetName": "Race Bracket"})


# The messages a live asset raises before it is archived again: the unarchive's INSERT (live id,
# no change stamp), an edit's MODIFY stamped after the record's last evaluation, and a direct
# compliance message. Each names the live database id.
def _edit_modify():
    message = _stream_message("MODIFY", database_id=DB, asset_id=ASSET, change_source="assetEdit",
                              change_at="2026-10-28T06:22:32.000000+00:00")
    # A real row image carries the asset's name, which the trigger keeps on the record.
    message["dynamodb"]["NewImage"]["assetName"] = {"S": "Race Bracket"}
    return message


MESSAGES = {
    "unarchive insert": lambda: _stream_message("INSERT", database_id=DB, asset_id=ASSET),
    "edit modify": _edit_modify,
    "direct message": lambda: {"databaseId": DB, "assetId": ASSET},
}


def _handle(tables, message):
    """Run the trigger on one queue record; returns (response, info lines, asset-table GetItem
    calls)."""
    asset_table = tables["assetStorage"]
    reads = []
    real_get_item = asset_table.get_item

    def get_item(**kwargs):
        reads.append(kwargs)
        return real_get_item(**kwargs)

    logger = MagicMock(name="logger")
    with patch.object(asset_table, "get_item", side_effect=get_item), \
            patch.object(trigger, "logger", logger):
        response = trigger.lambda_handler(_sns_event(message), MagicMock())
    infos = [str(call.args[0]) for call in logger.info.call_args_list]
    return response, infos, reads


def _evaluations(tables):
    return [row for row in tables["complianceEvaluationStorage"].scan(ConsistentRead=True)["Items"]
            if row.get("assetId") == ASSET]


def _audit(tables):
    return [row for row in tables["complianceAuditStorage"].scan(ConsistentRead=True)["Items"]
            if row.get("assetId") == ASSET]


def _record(tables):
    return tables["complianceAssetStateStorage"].get_item(
        Key={"databaseId": DB, "assetId": ASSET}, ConsistentRead=True).get("Item")


@pytest.mark.unit
class TestAnAssetArchivedBeforeItsEvaluationStartsIsSkipped:

    @pytest.mark.parametrize("message", list(MESSAGES.values()), ids=list(MESSAGES))
    def test_nothing_is_evaluated_or_written(self, tables, message):
        _archived(tables)
        response, infos, reads = _handle(tables, message())
        assert response == {"batchItemFailures": []}
        assert _evaluations(tables) == []
        assert _record(tables) == RECORD
        assert _audit(tables) == []
        # The decision is the asset table's, read strongly consistent; the event said "live".
        assert {read.get("ConsistentRead") for read in reads} == {True}
        assert any(f"asset={ASSET}" in line and "outcome=archived" in line for line in infos), infos

    def test_a_live_asset_is_still_evaluated(self, tables):
        """The control: the same world with the asset live evaluates it, so the skip above is the
        archive's doing."""
        _live(tables)
        response, infos, _ = _handle(tables, MESSAGES["unarchive insert"]())
        assert response == {"batchItemFailures": []}
        evaluations = _evaluations(tables)
        assert [row.get("status") for row in evaluations] == ["completed"]
        assert _record(tables)["lastEvaluationId"] == evaluations[0]["evaluationId"]
        assert any("outcome=evaluating" in line for line in infos), infos


@pytest.mark.unit
def test_an_asset_gone_everywhere_loses_its_record_instead_of_an_error_evaluation(tables):
    """No row live or archived (a permanent delete whose REMOVE has not been processed yet): the
    record is removed and audited once as `asset_deleted`, as the REMOVE would, and nothing is
    evaluated."""
    response, infos, _ = _handle(tables, MESSAGES["direct message"]())
    assert response == {"batchItemFailures": []}
    assert _evaluations(tables) == []
    assert _record(tables) is None
    entries = _audit(tables)
    assert [entry["eventType"] for entry in entries] == ["asset_deleted"]
    details = entries[0].get("details")
    details = json.loads(details) if isinstance(details, str) else details
    assert details["reason"] == "asset_permanently_deleted"
    assert "detectedBy" not in details
    assert any("outcome=removed" in line for line in infos), infos


@pytest.mark.unit
def test_the_sweeps_and_the_trigger_share_one_decision():
    """Evaluate All and the schema sweeps decide with the trigger's function, so "archived means
    skip" is decided in one place for every automatic evaluation."""
    assert evaluate_svc.automatic_evaluation_target is trigger.automatic_evaluation_target
