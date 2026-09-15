# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceTrigger (SNS-invoked): the three message shapes it accepts, the complianceAutoEval gate,
schema resolution (asset override, else the database binding with auto-registration), the evaluation
it runs, and the cascade it opens for an asset with children."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import ASSET, DB, SCHEMA, put_items
from handlers.compliance import complianceTrigger as trigger

MOD = "handlers.compliance.complianceTrigger"
STORE = "handlers.compliance.complianceEvaluationStore"

EVALUATION = {"evaluationId": "eval-1", "verdict": "compliant", "complianceState": "compliant",
              "ruleResults": [], "pipelineRulesPending": 0}


def _sns_event(*messages):
    return {"Records": [{"Sns": {"Message": json.dumps(m)}} for m in messages]}


def _stream_message(event_name="MODIFY", database_id=DB, asset_id=ASSET):
    return {"eventName": event_name, "dynamodb": {
        "Keys": {"databaseId": {"S": database_id}, "assetId": {"S": asset_id}},
        "NewImage": {"databaseId": {"S": database_id}, "assetId": {"S": asset_id}}}}


def _run(event, database_item=None, compliance_record=None, children=(), asset_rows=None,
         evaluation=EVALUATION):
    database_table = MagicMock(name="database_table")
    database_table.get_item.return_value = {"Item": database_item} if database_item else {}
    cascade_table = MagicMock(name="cascade_table")
    state_table = MagicMock(name="asset_state_table")
    asset_table = MagicMock(name="asset_table")
    asset_table.query.return_value = {"Items": list(asset_rows or [])}
    with patch(f"{MOD}.database_table", database_table), \
            patch(f"{MOD}.cascade_table", cascade_table), \
            patch(f"{MOD}.asset_state_table", state_table), \
            patch(f"{MOD}.asset_table", asset_table), \
            patch(f"{STORE}.get_compliance_record", return_value=compliance_record), \
            patch(f"{STORE}.get_child_links", return_value=list(children)), \
            patch(f"{STORE}.run_evaluation", return_value=evaluation) as run_evaluation, \
            patch(f"{STORE}.write_audit") as write_audit:
        trigger.lambda_handler(event, MagicMock())
    return {"database": database_table, "cascade": cascade_table, "state": state_table,
            "asset": asset_table, "run_evaluation": run_evaluation, "audit": write_audit}


AUTO_EVAL_DB = {"databaseId": DB, "complianceAutoEval": True, "complianceSchemaName": SCHEMA}


@pytest.mark.unit
class TestMessageShapes:

    def test_a_stream_modify_record_evaluates_the_asset(self):
        mocks = _run(_sns_event(_stream_message()), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA, "schemaSource": "database"})
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_a_stream_insert_record_evaluates_the_asset(self):
        mocks = _run(_sns_event(_stream_message("INSERT")), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA})
        mocks["run_evaluation"].assert_called_once()

    def test_a_stream_remove_record_is_ignored(self):
        mocks = _run(_sns_event(_stream_message("REMOVE")), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA})
        mocks["run_evaluation"].assert_not_called()
        mocks["database"].get_item.assert_not_called()

    def test_an_archived_partition_is_ignored(self):
        mocks = _run(_sns_event(_stream_message(database_id=f"{DB}#deleted")),
                     database_item=AUTO_EVAL_DB, compliance_record={"schemaName": SCHEMA})
        mocks["run_evaluation"].assert_not_called()

    def test_a_stream_record_without_keys_is_skipped(self):
        mocks = _run(_sns_event({"eventName": "MODIFY", "dynamodb": {}}), database_item=AUTO_EVAL_DB)
        mocks["run_evaluation"].assert_not_called()

    def test_a_file_event_resolves_the_asset_through_the_asset_id_index(self):
        message = {"s3": {"object": {"key": f"prefix/{ASSET}/model/part.glb"}},
                   "ASSET_BUCKET_PREFIX": "prefix"}
        mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA},
                     asset_rows=[{"databaseId": DB, "assetId": ASSET}])
        query = mocks["asset"].query.call_args.kwargs
        assert query["IndexName"] == "assetIdGSI"
        assert query["KeyConditionExpression"]._values[1] == ASSET
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_a_file_event_wrapped_in_records_is_unwrapped(self):
        message = {"Records": [{"s3": {"object": {"key": f"{ASSET}/file.glb"}}}]}
        mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA},
                     asset_rows=[{"databaseId": DB, "assetId": ASSET}])
        mocks["run_evaluation"].assert_called_once()

    def test_a_file_event_for_an_unknown_asset_is_skipped(self):
        message = {"s3": {"object": {"key": f"{ASSET}/file.glb"}}}
        mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB)
        mocks["run_evaluation"].assert_not_called()
        mocks["database"].get_item.assert_not_called()

    def test_a_direct_compliance_message_evaluates_the_asset(self):
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA})
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_an_unrecognized_message_is_skipped(self):
        mocks = _run(_sns_event({"hello": "world"}), database_item=AUTO_EVAL_DB)
        mocks["run_evaluation"].assert_not_called()

    def test_a_bad_record_does_not_stop_the_batch(self):
        event = {"Records": [{"Sns": {"Message": "not json"}},
                             {"Sns": {"Message": json.dumps({"databaseId": DB, "assetId": ASSET})}}]}
        mocks = _run(event, database_item=AUTO_EVAL_DB, compliance_record={"schemaName": SCHEMA})
        mocks["run_evaluation"].assert_called_once()

    @pytest.mark.parametrize("key,prefix,expected", [
        ("a1/file.glb", "", "a1"), ("p/a1/file.glb", "p", "a1"), ("p/a1/file.glb", "p/", "a1"),
        ("a1/file.glb", "/", "a1"), ("", "", None), ("other/a1/x", "p", "other"),
    ])
    def test_asset_id_extraction_from_an_object_key(self, key, prefix, expected):
        assert trigger._extract_asset_id_from_key(key, prefix) == expected


@pytest.mark.unit
class TestEvaluationGate:

    @pytest.mark.parametrize("database_item", [None, {"databaseId": DB},
                                               {"databaseId": DB, "complianceAutoEval": False},
                                               {"databaseId": DB, "complianceAutoEval": "true"}])
    def test_a_database_without_auto_eval_is_skipped(self, database_item):
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=database_item,
                     compliance_record={"schemaName": SCHEMA})
        mocks["run_evaluation"].assert_not_called()

    def test_an_untracked_asset_is_registered_under_the_database_schema(self):
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB)
        registered = put_items(mocks["state"])[0]
        assert registered["databaseId"] == DB and registered["assetId"] == ASSET
        assert registered["schemaName"] == SCHEMA
        assert registered["schemaSource"] == "database"
        assert registered["complianceState"] == "unknown"
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_an_untracked_asset_in_a_database_without_a_schema_is_skipped(self):
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}),
                     database_item={"databaseId": DB, "complianceAutoEval": True})
        mocks["state"].put_item.assert_not_called()
        mocks["run_evaluation"].assert_not_called()

    def test_an_asset_override_is_evaluated_against_its_own_schema(self):
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": "override-schema", "schemaSource": "asset"})
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, "override-schema", "SYSTEM_USER")
        mocks["state"].put_item.assert_not_called()

    def test_a_tracked_asset_without_a_schema_is_skipped(self):
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                     compliance_record={"complianceState": "unknown"})
        mocks["run_evaluation"].assert_not_called()


@pytest.mark.unit
class TestCascadeCheck:
    CHILDREN = [{"toAssetDatabaseId": DB, "toAssetId": "child-1"},
                {"toAssetDatabaseId": DB, "toAssetId": "child-2"}]

    def test_an_asset_with_children_opens_a_pending_cascade(self, notifications_aws):
        notifications_aws.dynamodb_client.query.return_value = {"Items": [
            {"assetName": {"S": "Root"}, "snsTopic": {"S": "arn:aws:sns:us-east-1:1:t"}}]}
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, children=self.CHILDREN)
        cascade = put_items(mocks["cascade"])[0]
        assert cascade["state"] == "pending_approval"
        assert cascade["triggeredByDatabaseId"] == DB
        assert cascade["triggeredByAssetId"] == ASSET
        assert cascade["actor"] == "SYSTEM_USER"
        assert cascade["requireApproval"] is True
        assert "approvalTimeoutAt" in cascade
        audit = mocks["audit"].call_args.kwargs
        assert audit["event_type"] == "cascade_auto_triggered"
        assert audit["cascade_id"] == cascade["cascadeId"]
        assert audit["details"] == {"reason": "parent_update", "childCount": 2}
        published = notifications_aws.sns_client.publish.call_args.kwargs
        assert "CASCADE PENDING" in published["Subject"]
        assert cascade["cascadeId"] in published["Message"]

    def test_an_asset_without_children_opens_no_cascade(self):
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA})
        mocks["cascade"].put_item.assert_not_called()
        mocks["audit"].assert_not_called()

    def test_an_evaluation_error_opens_no_cascade(self):
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, children=self.CHILDREN,
                     evaluation=dict(EVALUATION, verdict="error", error="Schema not found"))
        mocks["cascade"].put_item.assert_not_called()

    def test_a_failed_notification_does_not_fail_the_cascade(self, notifications_aws):
        notifications_aws.dynamodb_client.query.side_effect = RuntimeError("down")
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, children=self.CHILDREN)
        assert put_items(mocks["cascade"])[0]["state"] == "pending_approval"
        notifications_aws.sns_client.publish.assert_not_called()
