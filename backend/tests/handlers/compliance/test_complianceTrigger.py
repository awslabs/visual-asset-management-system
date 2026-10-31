# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceTrigger (SNS-invoked): the three message shapes it accepts (a stream record, a file
indexer message in every envelope the indexer topic carries, a direct compliance message), the
complianceAutoEval gate, schema resolution (asset override, else the database binding with
auto-registration), the evaluation it runs, the cascade it opens for an asset with children (once
per trigger asset while one is pending), and the guards against multiplying evaluations: the
provenance skip for objects a workflow execution wrote, the provenance skip for an asset row a
workflow execution's outputs last changed, and the coverage rule under which a change the asset's
last evaluation already read — an evaluation still awaiting its pipeline rules included — is not
evaluated again while a later change is."""

import itertools
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from backend.tests.common.test_indexerEvents import (
    indexer_message, nested_indexer_message, s3_event_record, sns_envelope, sqs_record)
from backend.tests.handlers.compliance._harness import (
    ASSET, DB, SCHEMA, FakeAssetStateTable, put_items)
from backend.tests.handlers.compliance.test_complianceEvaluationStore import Tables
from handlers.compliance import complianceTrigger as trigger

MOD = "handlers.compliance.complianceTrigger"
STORE = "handlers.compliance.complianceEvaluationStore"

EVALUATION = {"evaluationId": "eval-1", "verdict": "compliant", "complianceState": "compliant",
              "ruleResults": [], "pipelineRulesPending": 0}

# The cascade table as the CDK defines it (storageBuilder-nestedStack.ts): PK cascadeId, GSI
# StateIndex (state, createdAt) projecting ALL.
CASCADE_TABLE_DEFINITION = {
    "TableName": "complianceCascadeStorageTable",
    "KeySchema": [{"AttributeName": "cascadeId", "KeyType": "HASH"}],
    "AttributeDefinitions": [
        {"AttributeName": "cascadeId", "AttributeType": "S"},
        {"AttributeName": "state", "AttributeType": "S"},
        {"AttributeName": "createdAt", "AttributeType": "S"},
    ],
    "GlobalSecondaryIndexes": [{
        "IndexName": "StateIndex",
        "KeySchema": [{"AttributeName": "state", "KeyType": "HASH"},
                      {"AttributeName": "createdAt", "KeyType": "RANGE"}],
        "Projection": {"ProjectionType": "ALL"},
    }],
    "BillingMode": "PAY_PER_REQUEST",
}


def opened_cascades(table):
    """The cascades the trigger opened through a mock table: the cascade item (the one with a
    `state`; the other is the parent's pending marker) of each TransactWriteItems call."""
    return [entry["Put"]["Item"]
            for call in table.meta.client.transact_write_items.call_args_list
            for entry in call.kwargs["TransactItems"] if "state" in entry["Put"]["Item"]]


def _sns_event(*messages):
    """The SQS event the trigger's queue delivers: one record per indexer-topic message, each body
    the SNS `Notification` the topic subscription wrapped it in."""
    return {"Records": [sqs_record(sns_envelope(m), message_id=f"q-{index}")
                        for index, m in enumerate(messages, start=1)]}


def _stream_message(event_name="MODIFY", database_id=DB, asset_id=ASSET, created_at=None,
                    change_source=None, workflow_execution_id=None, change_at=None):
    new_image = {"databaseId": {"S": database_id}, "assetId": {"S": asset_id}}
    if change_source is not None:
        new_image["lastChangeSource"] = {"S": change_source}
        new_image["lastChangeAt"] = {"S": change_at or "2026-03-01T12:00:00+00:00"}
    elif change_at is not None:
        new_image["lastChangeAt"] = {"S": change_at}
    if workflow_execution_id is not None:
        new_image["lastChangeWorkflowExecutionId"] = {"S": workflow_execution_id}
    dynamodb_data = {
        "Keys": {"databaseId": {"S": database_id}, "assetId": {"S": asset_id}},
        "NewImage": new_image}
    if created_at is not None:
        dynamodb_data["ApproximateCreationDateTime"] = created_at
    return {"eventName": event_name, "dynamodb": dynamodb_data}


def _s3_head(change_source=None):
    """A HeadObject response carrying the given `vams-changesource` (none when None)."""
    metadata = {"assetid": ASSET, "databaseid": DB}
    if change_source is not None:
        metadata["vams-changesource"] = change_source
    return {"Metadata": metadata, "ContentLength": 3}


def _run(event, database_item=None, compliance_record=None, children=(), asset_rows=None,
         evaluation=EVALUATION, evaluation_rows=None, head=None, cascade_table=None,
         compliance_records=None, run_evaluation_effect=None, database_effect=None,
         asset_query_effect=None, asset_item=None):
    """Run the handler against stand-ins. `asset_rows` answers every assetIdGSI query; a dict maps an
    assetId to its own rows. `compliance_records` maps `databaseId:assetId` to a state row and
    takes precedence over `compliance_record`. `cascade_table` replaces the MagicMock cascade table
    (a moto-backed table for the dedup tests). `run_evaluation_effect` / `database_effect` become
    the side effect of `run_evaluation` / the database row read (an exception to raise, or a
    callable), `asset_query_effect` that of the assetIdGSI query. `asset_item`, when given, is the
    asset row a strongly consistent read by key returns. The handler's response is returned under
    `response`."""
    database_table = MagicMock(name="database_table")
    database_table.get_item.return_value = {"Item": database_item} if database_item else {}
    if database_effect is not None:
        database_table.get_item.side_effect = database_effect
    cascade_table = cascade_table if cascade_table is not None else MagicMock(name="cascade_table")
    if isinstance(cascade_table, MagicMock):
        cascade_table.query.return_value = {"Items": []}
    state_table = MagicMock(name="asset_state_table")
    asset_table = MagicMock(name="asset_table")
    if isinstance(asset_rows, dict):
        asset_table.query.side_effect = lambda **kwargs: {
            "Items": list(asset_rows.get(kwargs["KeyConditionExpression"]._values[1], []))}
    else:
        asset_table.query.return_value = {"Items": list(asset_rows or [])}
    if asset_query_effect is not None:
        asset_table.query.side_effect = asset_query_effect
    if asset_item is not None:
        asset_table.get_item.return_value = {"Item": asset_item}

    def asset_get_item(Key=None, **_kwargs):
        # An archived key (`<databaseId>#deleted`) holds no row unless a test says so; any other key
        # answers the configured row, as before.
        if str((Key or {}).get("databaseId", "")).endswith("#deleted"):
            return {}
        return asset_table.get_item.return_value
    asset_table.get_item.side_effect = asset_get_item
    s3_client = MagicMock(name="s3_client")
    if isinstance(head, (Exception, list)):
        s3_client.head_object.side_effect = head
    else:
        # By default the object is a direct bucket write (no `vams-changesource`), judged by its own
        # `eventTime`; a test of an upload's object passes `head=_s3_head("upload")`.
        s3_client.head_object.return_value = head if head is not None else _s3_head()
    rows = dict(evaluation_rows or {})
    logger = MagicMock(name="logger")

    def _get_evaluation(evaluation_id, consistent_read=False):
        return rows.get(evaluation_id)

    def _get_compliance_record(database_id, asset_id, consistent_read=False):
        if compliance_records is not None:
            return compliance_records.get(f"{database_id}:{asset_id}")
        return compliance_record

    with patch(f"{MOD}.database_table", database_table), \
            patch(f"{MOD}.cascade_table", cascade_table), \
            patch(f"{MOD}.asset_state_table", state_table), \
            patch(f"{STORE}.asset_state_table", state_table), \
            patch(f"{MOD}.asset_table", asset_table), \
            patch(f"{MOD}.s3_client", s3_client), \
            patch(f"{MOD}.logger", logger), \
            patch(f"{STORE}.get_compliance_record",
                  side_effect=_get_compliance_record) as get_compliance_record, \
            patch(f"{STORE}.get_evaluation", side_effect=_get_evaluation) as get_evaluation, \
            patch(f"{STORE}.get_child_links", return_value=list(children)), \
            patch(f"{STORE}.run_evaluation", return_value=evaluation,
                  side_effect=run_evaluation_effect) as run_evaluation, \
            patch(f"{STORE}.write_audit") as write_audit:
        response = trigger.lambda_handler(event, MagicMock())
    return {"database": database_table, "cascade": cascade_table, "state": state_table,
            "asset": asset_table, "s3": s3_client, "get_evaluation": get_evaluation,
            "get_compliance_record": get_compliance_record,
            "run_evaluation": run_evaluation, "audit": write_audit, "logger": logger,
            "response": response}


def _info_messages(mocks):
    return [str(call.args[0]) for call in mocks["logger"].info.call_args_list]


def _coverage_lines(mocks, outcome=None):
    """The coverage-decision lines the run logged (`complianceTrigger.coverage_log_line`), only those
    with `outcome` (`covered` / `evaluating`) when it is given."""
    lines = [m for m in _info_messages(mocks) if m.startswith("Compliance coverage ")]
    return [m for m in lines if outcome is None or m.endswith(f" outcome={outcome}")]


AUTO_EVAL_DB = {"databaseId": DB, "complianceAutoEval": True, "complianceSchemaName": SCHEMA}
ASSET_ROWS = [{"databaseId": DB, "assetId": ASSET}]


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

    def test_a_file_event_for_an_archived_asset_is_skipped(self):
        message = {"s3": {"object": {"key": f"{ASSET}/file.glb"}}}
        mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA},
                     asset_rows=[{"databaseId": f"{DB}#deleted", "assetId": ASSET}])
        mocks["run_evaluation"].assert_not_called()
        mocks["database"].get_item.assert_not_called()

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
        event = {"Records": [{"messageId": "q-bad", "body": "not json"},
                             sqs_record(sns_envelope({"databaseId": DB, "assetId": ASSET}),
                                        message_id="q-good")]}
        mocks = _run(event, database_item=AUTO_EVAL_DB, compliance_record={"schemaName": SCHEMA})
        mocks["run_evaluation"].assert_called_once()

    @pytest.mark.parametrize("key,prefix,expected", [
        ("a1/file.glb", "", "a1"), ("p/a1/file.glb", "p", "a1"), ("p/a1/file.glb", "p/", "a1"),
        ("a1/file.glb", "/", "a1"), ("", "", None), ("other/a1/x", "p", "other"),
    ])
    def test_asset_id_extraction_from_an_object_key(self, key, prefix, expected):
        assert trigger._extract_asset_id_from_key(key, prefix) == expected


@pytest.mark.unit
class TestIndexerMessagesAreUnwrapped:
    """The file indexer topic carries the message sqsBucketSync publishes: SQS records wrapping an SNS
    Notification wrapping the S3 event. The trigger reads every S3 record out of it, the way the
    file indexer does, and queues one evaluation per asset per message."""

    def test_the_deployed_sqs_wrapped_shape_queues_one_evaluation_per_upload(self):
        message = indexer_message(s3_event_record(f"{ASSET}/model.stl"))
        mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")
        head = mocks["s3"].head_object.call_args.kwargs
        assert head == {"Bucket": "asset-bucket", "Key": f"{ASSET}/model.stl", "VersionId": "v1"}

    def test_the_bucket_prefix_on_the_message_scopes_the_asset_id(self):
        message = indexer_message(s3_event_record(f"prefix-a/{ASSET}/model.stl"), prefix="prefix-a/")
        mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS)
        assert mocks["asset"].query.call_args.kwargs["KeyConditionExpression"]._values[1] == ASSET
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_the_nested_inner_notification_variant_queues_one_evaluation(self):
        message = nested_indexer_message(s3_event_record(f"{ASSET}/model.stl"))
        mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_a_multi_record_message_for_one_asset_queues_one_evaluation(self):
        """An N-file upload arrives as N notifications in one message; the asset's row and target
        are resolved once, the first object's provenance is read and the asset is evaluated once —
        the later records of the same asset are neither read nor evaluated."""
        message = indexer_message(s3_event_record(f"{ASSET}/one.stl"),
                                  s3_event_record(f"{ASSET}/two.stl"),
                                  s3_event_record(f"{ASSET}/three.stl"))
        mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")
        assert mocks["s3"].head_object.call_count == 1
        assert mocks["asset"].query.call_count == 1
        assert mocks["database"].get_item.call_count == 1

    def test_a_multi_record_message_for_two_assets_queues_one_evaluation_each(self):
        message = indexer_message(s3_event_record(f"{ASSET}/one.stl"),
                                  s3_event_record("other-asset/one.stl"),
                                  s3_event_record(f"{ASSET}/two.stl"))
        mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA},
                     asset_rows={ASSET: ASSET_ROWS,
                                 "other-asset": [{"databaseId": DB, "assetId": "other-asset"}]})
        evaluated = [call.args[:2] for call in mocks["run_evaluation"].call_args_list]
        assert evaluated == [(DB, ASSET), (DB, "other-asset")]

    def test_a_workflow_written_record_in_a_batch_does_not_suppress_the_others(self):
        message = indexer_message(s3_event_record(f"{ASSET}/output.glb"),
                                  s3_event_record(f"{ASSET}/source.stl"))
        mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS,
                     head=[_s3_head("workflowExecution"), _s3_head()])
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_a_batch_of_only_workflow_written_records_queues_nothing(self):
        message = indexer_message(s3_event_record(f"{ASSET}/one.glb"), s3_event_record(f"{ASSET}/two.glb"))
        mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS,
                     head=_s3_head("workflowExecution"))
        mocks["run_evaluation"].assert_not_called()
        # Every record is read (none evaluated, so none is skipped as a duplicate); the asset's
        # row and target were resolved once for both.
        assert mocks["s3"].head_object.call_count == 2
        assert mocks["asset"].query.call_count == 1

    def test_a_message_without_s3_data_queues_nothing_and_says_so(self):
        message = {"Records": [sqs_record(sns_envelope({"eventName": "MODIFY", "dynamodb": {}}))],
                   "ASSET_BUCKET_NAME": "asset-bucket", "ASSET_BUCKET_PREFIX": ""}
        mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_not_called()
        mocks["s3"].head_object.assert_not_called()
        mocks["asset"].query.assert_not_called()
        assert any("no s3 data" in text for text in _info_messages(mocks))

    def test_an_empty_records_list_queues_nothing(self):
        mocks = _run(_sns_event({"Records": []}), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_not_called()


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
    CHILDREN = [{"toAssetDatabaseId": DB, "toAssetId": "child-1", "relationshipType": "parentChild"},
                {"toAssetDatabaseId": DB, "toAssetId": "child-2", "relationshipType": "parentChild"}]

    def test_an_asset_with_children_opens_a_pending_cascade(self, notifications_aws):
        notifications_aws.dynamodb_client.query.return_value = {"Items": [
            {"assetName": {"S": "Root"}, "snsTopic": {"S": "arn:aws:sns:us-east-1:1:t"}}]}
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, children=self.CHILDREN)
        cascade = opened_cascades(mocks["cascade"])[0]
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

    def test_an_asset_whose_only_links_are_related_opens_no_cascade(self):
        """A related link is not a parent/child dependency (D25): no cascade, no audit entry."""
        related = [dict(child, relationshipType="related") for child in self.CHILDREN]
        mocks = _run(_sns_event(_stream_message()), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, children=related)
        mocks["run_evaluation"].assert_called_once()
        assert opened_cascades(mocks["cascade"]) == []
        mocks["audit"].assert_not_called()

    def test_a_cascade_counts_the_parent_child_children_only(self, notifications_aws):
        mixed = self.CHILDREN + [{"toAssetDatabaseId": DB, "toAssetId": "rel-1",
                                  "relationshipType": "related"}]
        mocks = _run(_sns_event(_stream_message()), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, children=mixed)
        assert len(opened_cascades(mocks["cascade"])) == 1
        assert mocks["audit"].call_args.kwargs["details"]["childCount"] == len(self.CHILDREN)

    def test_an_asset_without_children_opens_no_cascade(self):
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA})
        assert opened_cascades(mocks["cascade"]) == []
        mocks["audit"].assert_not_called()

    def test_an_evaluation_error_opens_no_cascade(self):
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, children=self.CHILDREN,
                     evaluation=dict(EVALUATION, verdict="error", error="Schema not found"))
        assert opened_cascades(mocks["cascade"]) == []

    def test_a_failed_notification_does_not_fail_the_cascade(self, notifications_aws):
        notifications_aws.dynamodb_client.query.side_effect = RuntimeError("down")
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, children=self.CHILDREN)
        assert opened_cascades(mocks["cascade"])[0]["state"] == "pending_approval"
        notifications_aws.sns_client.publish.assert_not_called()

    def test_the_pending_cascade_lookup_reads_the_state_index_for_the_trigger_asset(self):
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, children=self.CHILDREN)
        query = mocks["cascade"].query.call_args.kwargs
        assert query["IndexName"] == "StateIndex"
        key_attribute, key_value = query["KeyConditionExpression"]._values
        assert (key_attribute.name, key_value) == ("state", "pending_approval")
        filters = {(cond._values[0].name, cond._values[1]) for cond in query["FilterExpression"]._values}
        assert filters == {("triggeredByDatabaseId", DB), ("triggeredByAssetId", ASSET)}


def _cascade_row(cascade_id, database_id=DB, asset_id=ASSET, state="pending_approval", created_at=None):
    return {"cascadeId": cascade_id, "state": state, "triggeredByDatabaseId": database_id,
            "triggeredByAssetId": asset_id, "createdAt": created_at or f"2026-03-01T12:00:0{cascade_id[-1]}+00:00",
            "actor": "SYSTEM_USER", "requireApproval": True}


@pytest.mark.unit
class TestOnePendingCascadePerTriggerAsset:
    """Every evaluation of a parent with children used to open another cascade awaiting approval, so an
    asset evaluated eleven times had eleven pending cascades for one approver. Against a DynamoDB
    double built from the CDK table definition: the StateIndex query finds a pending cascade already
    triggered by the same asset and no second one is opened; a pending cascade for another asset, or
    a cascade for this asset that is no longer pending, does not stand in the way."""

    CHILDREN = [{"toAssetDatabaseId": DB, "toAssetId": "child-1", "relationshipType": "parentChild"}]

    def _run_with_cascades(self, *rows):
        with mock_aws():
            table = boto3.resource("dynamodb", region_name="us-east-1").create_table(**CASCADE_TABLE_DEFINITION)
            for row in rows:
                table.put_item(Item=row)
            mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                         compliance_record={"schemaName": SCHEMA}, children=self.CHILDREN,
                         cascade_table=table)
            items = table.scan()["Items"]
            # The cascade rows; the parent's pending-cascade marker rows carry no state.
            mocks["cascades"] = [item for item in items if "state" in item]
            mocks["markers"] = [item for item in items if "state" not in item]
        return mocks

    def test_a_pending_cascade_for_the_same_asset_keeps_a_second_from_opening(self):
        mocks = self._run_with_cascades(_cascade_row("c-1"))
        assert [c["cascadeId"] for c in mocks["cascades"]] == ["c-1"]
        mocks["audit"].assert_not_called()
        assert any("already has a cascade awaiting approval" in text for text in _info_messages(mocks))

    def test_no_pending_cascade_opens_one(self):
        mocks = self._run_with_cascades()
        assert len(mocks["cascades"]) == 1
        opened = mocks["cascades"][0]
        assert opened["state"] == "pending_approval"
        assert (opened["triggeredByDatabaseId"], opened["triggeredByAssetId"]) == (DB, ASSET)
        assert mocks["audit"].call_args.kwargs["event_type"] == "cascade_auto_triggered"

    def test_a_pending_cascade_for_another_asset_does_not_stand_in_the_way(self):
        mocks = self._run_with_cascades(_cascade_row("c-1", asset_id="other-asset"),
                                        _cascade_row("c-2", database_id="db2"))
        assert len(mocks["cascades"]) == 3
        assert sum(1 for c in mocks["cascades"] if c["triggeredByAssetId"] == ASSET
                   and c["triggeredByDatabaseId"] == DB) == 1

    @pytest.mark.parametrize("state", ["executing", "completed", "aborted", "rejected", "expired"])
    def test_a_cascade_for_this_asset_that_is_no_longer_pending_does_not_stand_in_the_way(self, state):
        mocks = self._run_with_cascades(_cascade_row("c-1", state=state))
        pending = [c for c in mocks["cascades"] if c["state"] == "pending_approval"]
        assert len(pending) == 1 and pending[0]["cascadeId"] != "c-1"

    def test_a_pending_cascade_past_its_approval_timeout_is_aborted_and_a_new_one_opens(self):
        """A cascade nobody approved in time no longer blocks its parent for ever: the pending check
        aborts it ("approval timed out") and the evaluation opens a fresh one."""
        expired = dict(_cascade_row("c-1"), approvalTimeoutAt=(
            datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
        mocks = self._run_with_cascades(expired)
        by_id = {c["cascadeId"]: c for c in mocks["cascades"]}
        assert by_id["c-1"]["state"] == "aborted"
        assert by_id["c-1"]["abortReason"] == "approval timed out"
        assert "completedAt" in by_id["c-1"]
        pending = [c for c in mocks["cascades"] if c["state"] == "pending_approval"]
        assert len(pending) == 1 and pending[0]["cascadeId"] != "c-1"
        events = [call.kwargs["event_type"] for call in mocks["audit"].call_args_list]
        assert events == ["cascade_timed_out", "cascade_auto_triggered"]
        assert mocks["audit"].call_args_list[0].kwargs["cascade_id"] == "c-1"

    def test_a_pending_cascade_within_its_approval_timeout_still_keeps_a_second_from_opening(self):
        current = dict(_cascade_row("c-1"), approvalTimeoutAt=(
            datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        mocks = self._run_with_cascades(current)
        assert [(c["cascadeId"], c["state"]) for c in mocks["cascades"]] == [
            ("c-1", "pending_approval")]
        mocks["audit"].assert_not_called()


def _file_message(key=f"{ASSET}/model/part.glb", event_time="2026-03-01T12:00:00.000Z",
                  version_id="v1", bucket="asset-bucket", wrapped=False):
    record = {"eventTime": event_time,
              "s3": {"bucket": {"name": bucket}, "object": {"key": key, "versionId": version_id}}}
    if wrapped:
        return {"Records": [record], "ASSET_BUCKET_NAME": bucket, "ASSET_BUCKET_PREFIX": ""}
    return dict(record, ASSET_BUCKET_NAME=bucket, ASSET_BUCKET_PREFIX="")


@pytest.mark.unit
class TestWorkflowWrittenObjectsAreSkipped:
    """A pipeline rule's workflow writes its outputs into the asset; without this guard each such write
    re-enters the trigger and relaunches the evaluation that produced it."""

    def test_an_object_a_workflow_execution_wrote_is_not_evaluated(self):
        mocks = _run(_sns_event(_file_message()), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS,
                     head=_s3_head("workflowExecution"))
        mocks["run_evaluation"].assert_not_called()
        mocks["s3"].head_object.assert_called_once()

    def test_an_object_of_a_database_without_auto_evaluation_is_not_read(self):
        """The auto-evaluation and binding gate runs before the object HEAD, so a file event in a
        deployment that does not evaluate the asset costs no object read."""
        mocks = _run(_sns_event(_file_message()),
                     database_item={"databaseId": DB, "complianceAutoEval": False},
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_not_called()
        mocks["s3"].head_object.assert_not_called()
        assert mocks["asset"].query.call_count == 1

    def test_an_object_of_an_asset_without_a_binding_is_not_read(self):
        mocks = _run(_sns_event(_file_message()),
                     database_item={"databaseId": DB, "complianceAutoEval": True},
                     compliance_record=None, asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_not_called()
        mocks["s3"].head_object.assert_not_called()

    def test_the_provenance_is_read_from_the_events_object_version(self):
        mocks = _run(_sns_event(_file_message(key="a%20b/x.glb", version_id="v7")),
                     database_item=AUTO_EVAL_DB, compliance_record={"schemaName": SCHEMA},
                     asset_rows=ASSET_ROWS)
        head = mocks["s3"].head_object.call_args.kwargs
        assert head == {"Bucket": "asset-bucket", "Key": "a b/x.glb", "VersionId": "v7"}

    def test_an_unversioned_event_heads_the_current_object(self):
        mocks = _run(_sns_event(_file_message(version_id="null")), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS)
        assert "VersionId" not in mocks["s3"].head_object.call_args.kwargs

    def test_a_key_the_decoded_form_does_not_name_falls_back_to_the_raw_key(self):
        """A literal '+' in a key decodes to a space that names no object; the raw key is tried next."""
        not_found = ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        mocks = _run(_sns_event(_file_message(key="A+B/x.glb")), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS,
                     head=[not_found, _s3_head("workflowExecution")])
        keys = [c.kwargs["Key"] for c in mocks["s3"].head_object.call_args_list]
        assert keys == ["A B/x.glb", "A+B/x.glb"]
        mocks["run_evaluation"].assert_not_called()

    # An upload's object is judged by its completion stamp instead (TestAnUploadIsEvaluatedOnceAt
    # ItsCompletion); every other provenance, and none, is judged by the object's own event time.
    @pytest.mark.parametrize("change_source", ["direct", "fileCopy", "fileMove", None])
    def test_other_provenance_is_evaluated(self, change_source):
        mocks = _run(_sns_event(_file_message()), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS,
                     head=_s3_head(change_source))
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_an_unreadable_object_is_evaluated_rather_than_assumed_a_workflow_write(self):
        denied = ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")
        mocks = _run(_sns_event(_file_message()), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS, head=denied)
        mocks["run_evaluation"].assert_called_once()

    def test_a_wrapped_record_is_checked_the_same_way(self):
        mocks = _run(_sns_event(_file_message(wrapped=True)), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS,
                     head=_s3_head("workflowExecution"))
        mocks["run_evaluation"].assert_not_called()
        assert mocks["s3"].head_object.call_args.kwargs["Bucket"] == "asset-bucket"

    def test_stream_and_direct_messages_read_no_object(self):
        for message in (_stream_message(), {"databaseId": DB, "assetId": ASSET}):
            mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                         compliance_record={"schemaName": SCHEMA})
            mocks["s3"].head_object.assert_not_called()
            mocks["run_evaluation"].assert_called_once()


PENDING_RECORD = {"schemaName": SCHEMA, "complianceState": "pending_evaluation",
                  "lastEvaluationId": "eval-pending"}


def _pending_rows(evaluated_at="2026-03-01T12:00:05+00:00", status="pending_pipeline"):
    return {"eval-pending": {"evaluationId": "eval-pending", "status": status,
                             "evaluatedAt": evaluated_at}}


@pytest.mark.unit
class TestWorkflowSourcedRowChangesAreSkipped:
    """A pipeline rule's workflow completes its outputs into the asset through `uploadFile`, which
    writes `lastChangeSource: workflowExecution` onto the asset row. The stream MODIFY that write
    emits must not re-evaluate the asset, or the rule relaunches its own workflow."""

    def test_a_modify_last_changed_by_a_workflow_execution_is_not_evaluated(self):
        mocks = _run(_sns_event(_stream_message(change_source="workflowExecution",
                                                workflow_execution_id="exec-1")),
                     database_item=AUTO_EVAL_DB, compliance_record={"schemaName": SCHEMA})
        mocks["run_evaluation"].assert_not_called()
        mocks["database"].get_item.assert_not_called()
        assert any("workflow execution" in text for text in _info_messages(mocks))

    def test_a_modify_last_changed_by_an_upload_is_evaluated(self):
        mocks = _run(_sns_event(_stream_message(change_source="upload")),
                     database_item=AUTO_EVAL_DB, compliance_record={"schemaName": SCHEMA})
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_a_modify_without_provenance_is_evaluated(self):
        """An asset row written before provenance was recorded, or changed by a writer that records
        none, carries no `lastChangeSource`; that is not a workflow write."""
        mocks = _run(_sns_event(_stream_message()), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA})
        mocks["run_evaluation"].assert_called_once()

    @pytest.mark.parametrize("change_source", ["direct", "fileCopy", "assetUnarchive", ""])
    def test_other_provenance_is_evaluated(self, change_source):
        mocks = _run(_sns_event(_stream_message(change_source=change_source)),
                     database_item=AUTO_EVAL_DB, compliance_record={"schemaName": SCHEMA})
        mocks["run_evaluation"].assert_called_once()

    def test_the_guard_reads_the_typed_stream_image(self):
        """A stream image carries `{"S": ...}` values; a bare string in the image is not the shape
        DynamoDB emits and is not read as a workflow write."""
        message = _stream_message()
        message["dynamodb"]["NewImage"]["lastChangeSource"] = "workflowExecution"
        mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA})
        mocks["run_evaluation"].assert_called_once()


@pytest.mark.unit
class TestAnAssetVersionReEvaluatesTheAsset:
    """Creating or reverting an asset version stamps `lastChangeSource: assetVersion` with a fresh
    `lastChangeAt` onto the asset row. The stream MODIFY that write emits is a user change: it is
    evaluated, judged against its own stamp, so a version-coverage verdict that failed after an
    upload clears once the files are versioned."""

    LAST_EVALUATED = "2026-03-01T12:00:05+00:00"

    def _record(self):
        return {"schemaName": SCHEMA, "complianceState": "non_compliant",
                "lastEvaluationId": "eval-1", "lastEvaluatedAt": self.LAST_EVALUATED,
                "lastEvaluationStatus": "completed"}

    def test_a_version_created_after_the_last_evaluation_is_evaluated(self):
        mocks = _run(_sns_event(_stream_message(change_source="assetVersion",
                                                change_at="2026-03-01T12:05:00+00:00")),
                     database_item=AUTO_EVAL_DB, compliance_record=self._record())
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")
        assert not any("workflow execution" in text for text in _info_messages(mocks))

    def test_a_version_the_last_evaluation_already_read_is_covered(self):
        mocks = _run(_sns_event(_stream_message(change_source="assetVersion",
                                                change_at="2026-03-01T12:00:01+00:00")),
                     database_item=AUTO_EVAL_DB, compliance_record=self._record())
        mocks["run_evaluation"].assert_not_called()

    def test_the_stamp_is_not_a_workflow_execution_write(self):
        from common.assetProvenance import ASSET_CHANGE_SOURCE_ASSET_VERSION
        from common.s3MetadataKeys import VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION
        assert ASSET_CHANGE_SOURCE_ASSET_VERSION == "assetVersion"
        assert ASSET_CHANGE_SOURCE_ASSET_VERSION != VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION


@pytest.mark.unit
class TestAnAssetEditReEvaluatesTheAsset:
    """An edit of the asset's name, description or tags stamps `lastChangeSource: assetEdit` with a
    fresh `lastChangeAt` onto the asset row. The stream MODIFY that write emits is a user change: it
    is evaluated, judged against its own stamp, so an asset-attribute verdict that failed on a
    missing tag clears once the user adds it."""

    LAST_EVALUATED = "2026-03-01T12:00:05+00:00"

    def _record(self):
        return {"schemaName": SCHEMA, "complianceState": "quarantined",
                "lastEvaluationId": "eval-1", "lastEvaluatedAt": self.LAST_EVALUATED,
                "lastEvaluationStatus": "completed"}

    def test_an_edit_after_the_last_evaluation_is_evaluated(self):
        mocks = _run(_sns_event(_stream_message(change_source="assetEdit",
                                                change_at="2026-03-01T12:05:00+00:00")),
                     database_item=AUTO_EVAL_DB, compliance_record=self._record())
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")
        assert not any("workflow execution" in text for text in _info_messages(mocks))

    def test_an_edit_the_last_evaluation_already_read_is_covered(self):
        mocks = _run(_sns_event(_stream_message(change_source="assetEdit",
                                                change_at="2026-03-01T12:00:01+00:00")),
                     database_item=AUTO_EVAL_DB, compliance_record=self._record())
        mocks["run_evaluation"].assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("change_source", ["metadataEdit", "linkChange"])
class TestAMetadataOrLinkChangeReEvaluatesTheAsset:
    """A write of the asset's metadata, a file's metadata or attributes or a link's metadata stamps
    `lastChangeSource: metadataEdit`; creating or deleting a link stamps `linkChange` on both linked
    assets. The stream MODIFY either write emits is a user change, admitted exactly as an
    `assetEdit` is: judged against `lastEvaluatedAt` by its own `lastChangeAt`, the source itself not
    consulted, so a metadata rule that quarantined an asset for a missing field clears once the user
    adds it."""

    LAST_EVALUATED = "2026-03-01T12:00:05+00:00"

    def _record(self):
        return {"schemaName": SCHEMA, "complianceState": "quarantined",
                "lastEvaluationId": "eval-1", "lastEvaluatedAt": self.LAST_EVALUATED,
                "lastEvaluationStatus": "completed"}

    def test_a_change_after_the_last_evaluation_is_evaluated(self, change_source):
        mocks = _run(_sns_event(_stream_message(change_source=change_source,
                                                change_at="2026-03-01T12:05:00+00:00")),
                     database_item=AUTO_EVAL_DB, compliance_record=self._record())
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")
        assert not any("workflow execution" in text for text in _info_messages(mocks))

    def test_a_change_the_last_evaluation_already_read_is_covered(self, change_source):
        mocks = _run(_sns_event(_stream_message(change_source=change_source,
                                                change_at="2026-03-01T12:00:01+00:00")),
                     database_item=AUTO_EVAL_DB, compliance_record=self._record())
        mocks["run_evaluation"].assert_not_called()

    def test_the_source_is_not_a_workflow_execution_write(self, change_source):
        from common.assetProvenance import (
            ASSET_CHANGE_SOURCE_LINK_CHANGE, ASSET_CHANGE_SOURCE_METADATA_EDIT)
        from common.s3MetadataKeys import VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION
        assert change_source in (ASSET_CHANGE_SOURCE_METADATA_EDIT, ASSET_CHANGE_SOURCE_LINK_CHANGE)
        assert change_source != VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION


@pytest.mark.unit
class TestAPipelineRulesMetadataOutputDoesNotReEvaluate:
    """A workflow execution's metadata write-back stamps `workflowExecution` with the execution's id
    (the metadata service, on a cross-call carrying `workflowExecutionId`); its MODIFY is skipped
    like a file output's."""

    def test_the_stamp_starts_no_evaluation(self):
        mocks = _run(_sns_event(_stream_message(change_source="workflowExecution",
                                                change_at="2026-03-01T12:05:00+00:00")),
                     database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA, "complianceState": "compliant",
                                        "lastEvaluationId": "eval-1",
                                        "lastEvaluatedAt": "2026-03-01T12:00:05+00:00"})
        mocks["run_evaluation"].assert_not_called()


@pytest.mark.unit
class TestAPendingEvaluationCoversOnlyTheChangesItRead:
    """An evaluation still awaiting its pipeline rules covers the changes made at or before its start
    (the N-file upload that raised it) like any other evaluation, and no more: a change after its
    start is evaluated now, since the callback that finalizes the pending evaluation re-evaluates
    nothing and would otherwise record a verdict for a file set the change has already left behind.
    The state row's `lastEvaluatedAt` / `lastEvaluationStatus` decide it; the evaluation row is not
    read."""

    PENDING_START = "2026-03-01T12:00:05+00:00"

    @staticmethod
    def _pending_record(started_at="2026-03-01T12:00:05+00:00"):
        return {"schemaName": SCHEMA, "complianceState": "pending_evaluation",
                "lastEvaluationId": "eval-pending", "lastEvaluatedAt": started_at,
                "lastEvaluationStatus": "pending_pipeline"}

    def test_a_file_that_landed_before_the_pending_evaluation_started_is_covered(self):
        mocks = _run(_sns_event(_file_message(event_time="2026-03-01T12:00:00.000Z")),
                     database_item=AUTO_EVAL_DB, compliance_record=self._pending_record(),
                     asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_not_called()
        assert opened_cascades(mocks["cascade"]) == []
        mocks["get_evaluation"].assert_not_called()
        assert len(_coverage_lines(mocks, "covered")) == 1

    def test_a_stamped_upload_completed_before_the_pending_evaluation_started_is_covered(self):
        mocks = _run(_sns_event(_stream_message(change_source="upload",
                                                change_at="2026-03-01T12:00:04+00:00")),
                     database_item=AUTO_EVAL_DB, compliance_record=self._pending_record())
        mocks["run_evaluation"].assert_not_called()

    def test_a_change_after_the_pending_evaluation_started_is_evaluated_now(self):
        """The evaluation began at 12:00:05 and the upload completion is stamped at 12:00:10: the
        pending evaluation read a file set the change has left behind, so a new evaluation starts."""
        mocks = _run(_sns_event(_stream_message(change_source="upload",
                                                change_at="2026-03-01T12:00:10+00:00")),
                     database_item=AUTO_EVAL_DB, compliance_record=self._pending_record())
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")
        mocks["get_evaluation"].assert_not_called()

    def test_a_file_written_after_the_pending_evaluation_started_is_evaluated_now(self):
        mocks = _run(_sns_event(_file_message(event_time="2026-03-01T12:00:10.000Z")),
                     database_item=AUTO_EVAL_DB, compliance_record=self._pending_record(),
                     asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_a_change_without_an_instant_is_evaluated(self):
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                     compliance_record=self._pending_record())
        mocks["run_evaluation"].assert_called_once()

    def test_a_pending_state_row_without_a_start_does_not_cover(self):
        mocks = _run(_sns_event(_file_message()), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA, "complianceState": "pending_evaluation",
                                        "lastEvaluationId": "eval-pending"},
                     asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_called_once()

    def test_a_multi_record_message_for_a_covered_asset_evaluates_nothing(self):
        message = indexer_message(s3_event_record(f"{ASSET}/one.stl", event_time="2026-03-01T12:00:00.000Z"),
                                  s3_event_record(f"{ASSET}/two.stl", event_time="2026-03-01T12:00:00.000Z"))
        mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                     compliance_record=self._pending_record(), asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_not_called()
        assert mocks["get_evaluation"].call_count == 0


@pytest.mark.unit
class TestAPipelineRulesOutputWriteIsNotTheUploadsCompletion:
    """An asset bound to a pipeline rule: the upload completes and is evaluated, the rule's workflow
    writes its outputs into the asset — re-stamping the row's `lastChangeAt` as a workflow change —
    and only then does the indexer's message for the upload arrive. The uploaded object is judged
    against the upload's own completion (`lastUploadAt`), which the output write did not move, so
    the evaluation that covered the upload still covers it."""

    UPLOAD_EVENT = "2026-03-01T07:43:12.117Z"
    UPLOAD_STAMP = "2026-03-01T07:43:12.212000+00:00"
    EVALUATION_START = "2026-03-01T07:43:12.760000+00:00"
    WORKFLOW_STAMP = "2026-03-01T07:43:14.500000+00:00"

    def _rows_after_the_output_write(self, with_upload_stamp=True):
        row = {"databaseId": DB, "assetId": ASSET, "lastChangeAt": self.WORKFLOW_STAMP,
               "lastChangeSource": "workflowExecution", "lastChangeWorkflowExecutionId": "exec-1"}
        if with_upload_stamp:
            row["lastUploadAt"] = self.UPLOAD_STAMP
        return [row]

    def test_the_uploads_file_message_is_covered_although_a_later_output_write_re_stamped_the_row(self):
        mocks = _run(_sns_event(indexer_message(s3_event_record(f"{ASSET}/model.stl",
                                                                event_time=self.UPLOAD_EVENT))),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(self.EVALUATION_START,
                                                            status="pending_pipeline"),
                     asset_rows=self._rows_after_the_output_write())
        mocks["run_evaluation"].assert_not_called()
        assert len(_coverage_lines(mocks, "covered")) == 1

    def test_a_row_without_an_upload_stamp_falls_back_to_the_object_time_and_the_margin(self):
        mocks = _run(_sns_event(indexer_message(s3_event_record(f"{ASSET}/model.stl",
                                                                event_time=self.UPLOAD_EVENT))),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(self.EVALUATION_START,
                                                            status="pending_pipeline"),
                     asset_rows=self._rows_after_the_output_write(with_upload_stamp=False))
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_a_new_upload_after_the_evaluation_is_evaluated(self):
        mocks = _run(_sns_event(indexer_message(s3_event_record(f"{ASSET}/second.stl",
                                                                event_time="2026-03-01T07:43:19.900Z"))),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(self.EVALUATION_START),
                     asset_rows=_stamped_rows("2026-03-01T07:43:20.000000+00:00"))
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_the_file_path_reads_the_upload_stamp_not_the_latest_change(self):
        """The row's latest change is the workflow's; the upload's file message is nevertheless
        judged by the upload stamp, which the evaluation started after."""
        rows = self._rows_after_the_output_write()
        rows[0]["lastUploadAt"] = "2026-03-01T07:43:12.700000+00:00"
        mocks = _run(_sns_event(indexer_message(s3_event_record(f"{ASSET}/model.stl",
                                                                event_time=self.UPLOAD_EVENT))),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(self.EVALUATION_START),
                     asset_rows=rows)
        mocks["run_evaluation"].assert_not_called()


@pytest.mark.unit
class TestAnInsertCarriesNoCompletion:
    """A stream INSERT's image carries whatever `lastChangeAt` the row was written with — an
    unarchived asset keeps its old stamp — so the stamp is not the completion that raised the event
    and does not mark the change as covered."""

    def test_a_stamped_insert_is_evaluated_although_the_stamp_predates_the_last_evaluation(self):
        mocks = _run(_sns_event(_stream_message(event_name="INSERT", change_source="upload",
                                                change_at="2026-03-01T12:00:00+00:00")),
                     database_item=AUTO_EVAL_DB, compliance_record=_evaluated_record(5))
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_a_stamped_modify_with_the_same_stamp_is_covered(self):
        mocks = _run(_sns_event(_stream_message(event_name="MODIFY", change_source="upload",
                                                change_at="2026-03-01T12:00:00+00:00")),
                     database_item=AUTO_EVAL_DB, compliance_record=_evaluated_record(5))
        mocks["run_evaluation"].assert_not_called()


# The instant an upload landed (S3 `eventTime` on the file path, the asset row's `lastChangeAt` on the
# stream path) and the state row of an asset whose last evaluation started at a chosen offset from it.
CHANGE_TIME_S3 = "2026-03-01T12:00:00.000Z"
CHANGE_TIME_ROW = "2026-03-01T12:00:00+00:00"


def _evaluated_record(seconds_after_change, status="completed"):
    return {"schemaName": SCHEMA, "complianceState": "compliant", "lastEvaluationId": "eval-prev",
            "lastEvaluatedAt": f"2026-03-01T12:00:{seconds_after_change:02d}+00:00",
            "lastEvaluationStatus": status}


@pytest.mark.unit
class TestOneUploadIsEvaluatedOnce:
    """An upload reaches the trigger twice — the asset row's stream image (its provenance stamp) and
    the file indexer's S3 record, one message per object — so both paths skip a change the asset's
    last evaluation already covers: an evaluation that started at least the skew margin after the
    change read the changed inputs."""

    def test_the_file_record_of_an_upload_the_stream_path_already_evaluated_is_skipped(self):
        first = _run(_sns_event(_stream_message(change_source="upload", change_at=CHANGE_TIME_ROW)),
                     database_item=AUTO_EVAL_DB, compliance_record={"schemaName": SCHEMA},
                     asset_rows=ASSET_ROWS)
        first["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")
        second = _run(_sns_event(indexer_message(s3_event_record(f"{ASSET}/model.obj",
                                                                 event_time=CHANGE_TIME_S3))),
                      database_item=AUTO_EVAL_DB, compliance_record=_evaluated_record(4),
                      asset_rows=ASSET_ROWS)
        second["run_evaluation"].assert_not_called()
        assert any("lastEvaluationId=eval-prev" in m for m in _coverage_lines(second, "covered"))

    def test_one_message_per_object_of_a_three_file_upload_evaluates_nothing_after_the_covering_run(self):
        messages = [indexer_message(s3_event_record(f"{ASSET}/{name}", event_time=CHANGE_TIME_S3))
                    for name in ("one.stl", "two.stl", "three.stl")]
        mocks = _run(_sns_event(*messages), database_item=AUTO_EVAL_DB,
                     compliance_record=_evaluated_record(3), asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_not_called()

    def test_a_file_that_landed_after_the_last_evaluation_started_is_evaluated(self):
        mocks = _run(_sns_event(indexer_message(s3_event_record(f"{ASSET}/late.stl",
                                                                event_time="2026-03-01T12:00:09.000Z"))),
                     database_item=AUTO_EVAL_DB, compliance_record=_evaluated_record(4),
                     asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_an_evaluation_that_started_within_the_skew_margin_does_not_cover_the_change(self):
        """An object written after the last recorded upload completion is judged against its own
        eventTime with the skew margin, so a start one second later does not cover it."""
        mocks = _run(_sns_event(indexer_message(s3_event_record(f"{ASSET}/model.stl",
                                                                event_time=CHANGE_TIME_S3))),
                     database_item=AUTO_EVAL_DB, compliance_record=_evaluated_record(1),
                     asset_rows=_stamped_rows("2026-03-01T11:59:30+00:00"))
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    @pytest.mark.parametrize("status", ["completed", "pending_pipeline", "error"])
    def test_the_stream_path_skips_a_covered_change_whatever_the_last_evaluation_status(self, status):
        mocks = _run(_sns_event(_stream_message(change_source="upload", change_at=CHANGE_TIME_ROW)),
                     database_item=AUTO_EVAL_DB, compliance_record=_evaluated_record(5, status),
                     asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_not_called()
        mocks["get_evaluation"].assert_not_called()

    def test_an_insert_without_a_change_time_is_evaluated(self):
        mocks = _run(_sns_event(_stream_message(event_name="INSERT")), database_item=AUTO_EVAL_DB,
                     compliance_record=_evaluated_record(30), asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_an_unparseable_event_time_is_evaluated(self):
        mocks = _run(_sns_event(indexer_message(s3_event_record(f"{ASSET}/model.stl",
                                                                event_time="not-a-time"))),
                     database_item=AUTO_EVAL_DB, compliance_record=_evaluated_record(30),
                     asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_an_asset_never_evaluated_is_evaluated(self):
        mocks = _run(_sns_event(indexer_message(s3_event_record(f"{ASSET}/model.stl",
                                                                event_time=CHANGE_TIME_S3))),
                     database_item=AUTO_EVAL_DB, compliance_record={"schemaName": SCHEMA},
                     asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    @pytest.mark.parametrize("value,expected_hour", [
        ("2026-03-01T12:00:00.000Z", 12), ("2026-03-01T12:00:00+00:00", 12),
        ("2026-03-01T14:00:00+02:00", 12)])
    def test_the_event_time_parser_reads_both_spellings_as_utc(self, value, expected_hour):
        parsed = trigger.parse_event_time(value)
        assert parsed.tzinfo is not None
        assert parsed.astimezone(timezone.utc).hour == expected_hour

    @pytest.mark.parametrize("value", [None, "", "not-a-time", 12])
    def test_the_event_time_parser_returns_none_for_anything_it_cannot_read(self, value):
        assert trigger.parse_event_time(value) is None


def _stamped_rows(last_upload_at, last_change_at=None, change_source="upload"):
    """The asset's row as the assetIdGSI returns it, carrying the last upload completion's stamp
    (`lastUploadAt`) and the latest change of either kind (`lastChangeAt`, the same instant unless
    a workflow execution's output write landed since)."""
    return [{"databaseId": DB, "assetId": ASSET, "lastUploadAt": last_upload_at,
             "lastChangeAt": last_change_at or last_upload_at, "lastChangeSource": change_source}]


def _record_evaluated_at(last_evaluated_at, status="completed"):
    return {"schemaName": SCHEMA, "complianceState": "compliant", "lastEvaluationId": "eval-prev",
            "lastEvaluatedAt": last_evaluated_at, "lastEvaluationStatus": status}


# A three-file upload as the deployment records it: the objects land, the upload completion stamps the
# asset row a tenth of a second later, the stream path evaluates within the second, and the indexer's
# per-object messages arrive ten seconds after that.
UPLOAD_EVENT_TIME = "2026-03-01T04:51:40.515Z"
UPLOAD_STAMP = "2026-03-01T04:51:40.628000+00:00"
STREAM_EVALUATION_START = "2026-03-01T04:51:41.378000+00:00"


@pytest.mark.unit
class TestARecordedUploadCompletionCoversItsFiles:
    """The asset row's `lastChangeAt` is written after every object of an upload was copied, so an
    evaluation that started at or after it has seen those files; the comparison is causal and takes
    no skew margin. Only an object written outside a recorded completion falls back to its own
    eventTime with the margin."""

    def test_the_file_messages_of_an_upload_the_stream_evaluation_covered_are_skipped(self):
        messages = [indexer_message(s3_event_record(f"{ASSET}/{name}", event_time=UPLOAD_EVENT_TIME))
                    for name in ("one.stl", "two.stl", "three.stl")]
        mocks = _run(_sns_event(*messages), database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(STREAM_EVALUATION_START),
                     asset_rows=_stamped_rows(UPLOAD_STAMP))
        mocks["run_evaluation"].assert_not_called()
        assert len(_coverage_lines(mocks, "covered")) == 3

    def test_the_stream_path_is_covered_by_an_evaluation_started_at_the_stamp_itself(self):
        mocks = _run(_sns_event(_stream_message(change_source="upload", change_at=UPLOAD_STAMP)),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(UPLOAD_STAMP), asset_rows=ASSET_ROWS)
        mocks["run_evaluation"].assert_not_called()

    def test_a_manual_evaluation_right_after_the_stamp_covers_the_file_messages(self):
        mocks = _run(_sns_event(indexer_message(s3_event_record(f"{ASSET}/one.stl",
                                                                event_time=UPLOAD_EVENT_TIME))),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at("2026-03-01T04:51:41.028000+00:00"),
                     asset_rows=_stamped_rows(UPLOAD_STAMP))
        mocks["run_evaluation"].assert_not_called()

    def test_an_evaluation_started_before_the_stamp_does_not_cover_the_upload(self):
        mocks = _run(_sns_event(indexer_message(s3_event_record(f"{ASSET}/one.stl",
                                                                event_time=UPLOAD_EVENT_TIME))),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at("2026-03-01T04:51:40.600000+00:00"),
                     asset_rows=_stamped_rows(UPLOAD_STAMP))
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_an_object_written_after_the_last_stamp_falls_back_to_the_margin_and_evaluates(self):
        mocks = _run(_sns_event(indexer_message(s3_event_record(f"{ASSET}/late.stl",
                                                                event_time="2026-03-01T04:52:10.000Z"))),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at("2026-03-01T04:52:11+00:00"),
                     asset_rows=_stamped_rows(UPLOAD_STAMP))
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_an_object_written_after_the_last_stamp_is_covered_past_the_margin(self):
        mocks = _run(_sns_event(indexer_message(s3_event_record(f"{ASSET}/late.stl",
                                                                event_time="2026-03-01T04:52:10.000Z"))),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at("2026-03-01T04:52:13+00:00"),
                     asset_rows=_stamped_rows(UPLOAD_STAMP))
        mocks["run_evaluation"].assert_not_called()

    def test_an_asset_row_without_a_stamp_is_judged_by_the_object_time_and_the_margin(self):
        covered = _run(_sns_event(indexer_message(s3_event_record(f"{ASSET}/one.stl",
                                                                  event_time=UPLOAD_EVENT_TIME))),
                       database_item=AUTO_EVAL_DB,
                       compliance_record=_record_evaluated_at("2026-03-01T04:51:43+00:00"),
                       asset_rows=ASSET_ROWS)
        covered["run_evaluation"].assert_not_called()
        not_covered = _run(_sns_event(indexer_message(s3_event_record(f"{ASSET}/one.stl",
                                                                      event_time=UPLOAD_EVENT_TIME))),
                           database_item=AUTO_EVAL_DB,
                           compliance_record=_record_evaluated_at(STREAM_EVALUATION_START),
                           asset_rows=ASSET_ROWS)
        not_covered["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_the_asset_row_is_read_once_per_message_for_database_and_stamp(self):
        message = indexer_message(s3_event_record(f"{ASSET}/one.stl", event_time=UPLOAD_EVENT_TIME),
                                  s3_event_record(f"{ASSET}/two.stl", event_time=UPLOAD_EVENT_TIME))
        mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(STREAM_EVALUATION_START),
                     asset_rows=_stamped_rows(UPLOAD_STAMP))
        assert mocks["asset"].query.call_count == 1
        mocks["run_evaluation"].assert_not_called()


# An asset-version revert as the round-11 re-smoke recorded it (D11): an earlier upload was stamped and
# evaluated; the revert copies the target version's objects into the asset (new S3 versions whose
# eventTime is the revert's), then stamps the row `assetVersion` with `lastAssetVersionAt`; the stamp's
# stream MODIFY starts an evaluation within the second, and the indexer's messages for the copies
# arrive about 20 s later.
EARLIER_UPLOAD_STAMP = "2026-03-01T18:05:00.200000+00:00"
EARLIER_UPLOAD_EVALUATION = "2026-03-01T18:05:00.900000+00:00"
REVERT_COPY_EVENT_TIME = "2026-03-01T18:09:09.310Z"
REVERT_STAMP = "2026-03-01T18:09:09.520000+00:00"
REVERT_EVALUATION_START = "2026-03-01T18:09:09.700000+00:00"


def _reverted_rows(change_source="assetVersion", change_at=REVERT_STAMP, **extra):
    """The asset's row after the revert's stamp: `lastAssetVersionAt` and `lastChangeAt` at the stamp,
    `lastUploadAt` still the earlier upload's. `change_source` / `change_at` replace the latest change
    to show a later writer re-stamping it (a workflow execution's output write, an asset edit), which
    leaves both completion stamps as they are; `extra` adds attributes."""
    return [{"databaseId": DB, "assetId": ASSET, "lastUploadAt": EARLIER_UPLOAD_STAMP,
             "lastAssetVersionAt": REVERT_STAMP, "lastChangeAt": change_at,
             "lastChangeSource": change_source, **extra}]


def _revert_copy_messages(event_time=REVERT_COPY_EVENT_TIME, names=("r-1.txt", "r-2.txt", "r-3.txt")):
    """One indexer message per object the revert copied, as the file indexer emits them. The state
    row stands still between messages here, so a test that expects an evaluation sends one."""
    return [indexer_message(s3_event_record(f"{ASSET}/{name}", event_time=event_time))
            for name in names]


@pytest.mark.unit
class TestAnAssetVersionRevertIsEvaluatedOnce:
    """Reverting to an asset version copies the target version's objects into the asset and only then
    stamps the row `lastChangeSource: assetVersion` with a fresh `lastChangeAt` and the same instant
    as `lastAssetVersionAt`, leaving `lastUploadAt` at the last user upload. The stamp's stream
    MODIFY evaluates the asset once. The indexer's messages for the copies arrive later and are
    judged by the latest completion stamp on the row, here `lastAssetVersionAt`, which was written
    after every copy completed (causal, no margin, like an upload completion's), so the evaluation
    that started at or after it covers them. Judged by the older `lastUploadAt` they fell back to
    their eventTime plus the skew margin and started a second evaluation, and for a pipeline rule a
    second workflow execution, per revert (D11).

    Creating an asset version stamps the row the same way but writes no S3 object
    (`tests/handlers/assets/test_assetVersions_change_provenance.py` pins that), so no file message
    follows it and its stream MODIFY is its only evaluation."""

    def test_a_revert_evaluates_once_on_the_stream_and_not_again_on_its_copies(self):
        stream = _run(_sns_event(_stream_message(change_source="assetVersion",
                                                 change_at=REVERT_STAMP)),
                      database_item=AUTO_EVAL_DB,
                      compliance_record=_record_evaluated_at(EARLIER_UPLOAD_EVALUATION),
                      asset_rows=_reverted_rows())
        stream["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")
        copies = _run(_sns_event(*_revert_copy_messages()), database_item=AUTO_EVAL_DB,
                      compliance_record=_record_evaluated_at(REVERT_EVALUATION_START),
                      asset_rows=_reverted_rows())
        copies["run_evaluation"].assert_not_called()
        lines = _coverage_lines(copies, "covered")
        assert len(lines) == 3
        assert all("stamp=lastAssetVersionAt" in line for line in lines)

    def test_the_copies_are_covered_by_an_evaluation_started_at_the_stamp_itself(self):
        mocks = _run(_sns_event(*_revert_copy_messages()), database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(REVERT_STAMP),
                     asset_rows=_reverted_rows())
        mocks["run_evaluation"].assert_not_called()

    def test_a_copy_written_at_the_stamp_instant_is_covered(self):
        mocks = _run(_sns_event(*_revert_copy_messages("2026-03-01T18:09:09.520Z")),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(REVERT_EVALUATION_START),
                     asset_rows=_reverted_rows())
        mocks["run_evaluation"].assert_not_called()

    def test_an_evaluation_started_before_the_stamp_does_not_cover_the_copies(self):
        """An evaluation that began while the revert was still copying may have read part of the
        reverted file set, so the copies' messages start a new one."""
        mocks = _run(_sns_event(*_revert_copy_messages(names=("r-1.txt",))),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at("2026-03-01T18:09:09.400000+00:00"),
                     asset_rows=_reverted_rows())
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_an_object_written_after_the_stamp_is_a_new_change_and_is_evaluated(self):
        """A user upload landing after the revert: its object is newer than the version stamp and its
        own completion has not stamped the row yet, so it is judged by its eventTime with the margin
        and evaluated."""
        mocks = _run(_sns_event(indexer_message(s3_event_record(
                         f"{ASSET}/new.txt", event_time="2026-03-01T18:09:30.000Z"))),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(REVERT_EVALUATION_START),
                     asset_rows=_reverted_rows())
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    # An upload completed after the revert: its stamp is the latest on the row.
    LATER_UPLOAD_EVENT_TIME = "2026-03-01T18:10:00.100Z"
    LATER_UPLOAD_STAMP = "2026-03-01T18:10:00.200000+00:00"

    def _rows_after_a_later_upload(self):
        return _reverted_rows(change_source="upload", change_at=self.LATER_UPLOAD_STAMP,
                              lastUploadAt=self.LATER_UPLOAD_STAMP)

    def test_an_upload_after_the_revert_is_judged_by_its_own_completion(self):
        """`lastUploadAt` is now later than `lastAssetVersionAt`, so the upload's object is judged by
        the upload's completion; the revert's evaluation started before it and does not cover it."""
        mocks = _run(_sns_event(indexer_message(s3_event_record(
                         f"{ASSET}/new.txt", event_time=self.LATER_UPLOAD_EVENT_TIME))),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(REVERT_EVALUATION_START),
                     asset_rows=self._rows_after_a_later_upload())
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")
        (line,) = _coverage_lines(mocks, "evaluating")
        assert f"stamp=lastUploadAt stampAt={self.LATER_UPLOAD_STAMP}" in line

    def test_an_upload_after_the_revert_is_covered_by_the_evaluation_its_completion_started(self):
        mocks = _run(_sns_event(indexer_message(s3_event_record(
                         f"{ASSET}/new.txt", event_time=self.LATER_UPLOAD_EVENT_TIME))),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at("2026-03-01T18:10:00.900000+00:00"),
                     asset_rows=self._rows_after_a_later_upload())
        mocks["run_evaluation"].assert_not_called()


# The round-12 re-smoke sequence on a pipeline-rule asset (D11, asset xed6b8739 on sbxf at 19:55Z), with
# T1 the last user upload and T2 the revert's stamp: the revert's copies land at T2 - 0.2 s, the stamp's
# stream MODIFY starts an evaluation at T2 + 0.8 s, that evaluation's pipeline rule launches a
# workflow whose output completion re-stamps `lastChangeSource: workflowExecution` / `lastChangeAt` at
# T2 + 1.5 s (leaving `lastUploadAt` and `lastAssetVersionAt`), and the indexer's messages for the
# copies arrive about 10 s later.
PIPELINE_UPLOAD_STAMP = "2026-03-01T19:50:10.120000+00:00"                 # T1
PIPELINE_REVERT_STAMP = "2026-03-01T19:55:34.050000+00:00"                 # T2
PIPELINE_REVERT_COPY_EVENT_TIME = "2026-03-01T19:55:33.850Z"                # T2 - 0.2 s
PIPELINE_REVERT_EVALUATION_START = "2026-03-01T19:55:34.850000+00:00"      # T2 + 0.8 s
PIPELINE_OUTPUT_STAMP = "2026-03-01T19:55:35.550000+00:00"                 # T2 + 1.5 s


def _pipeline_rows(change_source, change_at, **extra):
    """The pipeline-rule asset's row after the revert stamped it and a later writer re-stamped its
    latest change; both completion stamps are as the revert left them."""
    return [{"databaseId": DB, "assetId": ASSET, "lastUploadAt": PIPELINE_UPLOAD_STAMP,
             "lastAssetVersionAt": PIPELINE_REVERT_STAMP, "lastChangeSource": change_source,
             "lastChangeAt": change_at, **extra}]


def _rows_after_the_output_write():
    return _pipeline_rows("workflowExecution", PIPELINE_OUTPUT_STAMP,
                          lastChangeWorkflowExecutionId="exec-pipeline-output")


def _pipeline_copy_messages(names=("r12-p2.stl",)):
    return [indexer_message(s3_event_record(f"{ASSET}/{name}",
                                            event_time=PIPELINE_REVERT_COPY_EVENT_TIME))
            for name in names]


@pytest.mark.unit
class TestARevertOnAPipelineRuleAssetIsEvaluatedOnce:
    """D11 on an asset whose pipeline rule launches a workflow: the workflow's output completion
    re-stamps the row's latest change as `workflowExecution` before the indexer's messages for the
    revert's copies arrive. Keyed on `lastChangeSource`, the copies then fell back to the older
    `lastUploadAt` and the margin and started a second evaluation and a second execution. Judged by
    the latest completion stamp, `lastAssetVersionAt`, which no output write or edit moves, they are
    covered by the revert's one evaluation."""

    def test_the_revert_evaluates_once_and_its_copies_after_the_output_write_evaluate_nothing(self):
        revert = _run(_sns_event(_stream_message(change_source="assetVersion",
                                                 change_at=PIPELINE_REVERT_STAMP)),
                      database_item=AUTO_EVAL_DB,
                      compliance_record=_record_evaluated_at("2026-03-01T19:50:10.700000+00:00"),
                      asset_rows=_pipeline_rows("assetVersion", PIPELINE_REVERT_STAMP))
        revert["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

        pending = _record_evaluated_at(PIPELINE_REVERT_EVALUATION_START, status="pending_pipeline")
        output = _run(_sns_event(_stream_message(change_source="workflowExecution",
                                                 change_at=PIPELINE_OUTPUT_STAMP,
                                                 workflow_execution_id="exec-pipeline-output")),
                      database_item=AUTO_EVAL_DB, compliance_record=pending,
                      asset_rows=_rows_after_the_output_write())
        output["run_evaluation"].assert_not_called()

        copies = _run(_sns_event(*_pipeline_copy_messages(("r12-p2.stl", "r12-p2.glb"))),
                      database_item=AUTO_EVAL_DB, compliance_record=pending,
                      asset_rows=_rows_after_the_output_write(),
                      head=[_s3_head("upload"), _s3_head("workflowExecution")])
        copies["run_evaluation"].assert_not_called()
        assert _coverage_lines(copies) == [
            f"Compliance coverage asset={ASSET} path=file stamp=lastAssetVersionAt "
            f"stampAt={PIPELINE_REVERT_STAMP} " "eventTime=2026-03-01T19:55:33.850000+00:00 "
            f"lastEvaluationId=eval-prev lastEvaluatedAt={PIPELINE_REVERT_EVALUATION_START} "
            "outcome=covered"]

    def test_the_copies_are_covered_although_the_output_write_re_stamped_the_latest_change(self):
        mocks = _run(_sns_event(*_pipeline_copy_messages()), database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(PIPELINE_REVERT_EVALUATION_START,
                                                            status="pending_pipeline"),
                     asset_rows=_rows_after_the_output_write())
        mocks["run_evaluation"].assert_not_called()

    def test_the_copies_are_covered_although_an_asset_edit_re_stamped_the_latest_change(self):
        mocks = _run(_sns_event(*_pipeline_copy_messages()), database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(PIPELINE_REVERT_EVALUATION_START),
                     asset_rows=_pipeline_rows("assetEdit", PIPELINE_OUTPUT_STAMP))
        mocks["run_evaluation"].assert_not_called()

    def test_an_object_written_after_the_revert_stamp_is_still_evaluated(self):
        """A user upload landing after the output write: its object postdates every completion stamp
        and its own completion has not stamped the row yet, so it is judged by its eventTime with
        the margin and evaluated."""
        mocks = _run(_sns_event(indexer_message(s3_event_record(
                         f"{ASSET}/second.stl", event_time="2026-03-01T19:55:40.000Z"))),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(PIPELINE_REVERT_EVALUATION_START),
                     asset_rows=_rows_after_the_output_write())
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")


@pytest.mark.unit
class TestTheFileEventCompletionTimeIsTheLatestCompletionStamp:
    """`file_event_completion_time` reads `lastUploadAt` and `lastAssetVersionAt`, takes the later,
    and never consults `lastChangeSource` / `lastChangeAt`."""

    UPLOAD = "2026-03-01T10:00:00+00:00"
    VERSION = "2026-03-01T11:00:00+00:00"
    LATER_CHANGE = "2026-03-01T12:00:00+00:00"

    @pytest.mark.parametrize("row,expected", [
        ({}, None),
        ({"lastUploadAt": UPLOAD}, UPLOAD),
        ({"lastAssetVersionAt": VERSION}, VERSION),
        ({"lastUploadAt": UPLOAD, "lastAssetVersionAt": VERSION}, VERSION),
        ({"lastUploadAt": VERSION, "lastAssetVersionAt": UPLOAD}, VERSION),
        ({"lastUploadAt": "not-a-time", "lastAssetVersionAt": VERSION}, VERSION),
    ], ids=["neither", "upload-only", "version-only", "version-later", "upload-later",
            "unparseable-upload"])
    def test_the_later_of_the_two_stamps(self, row, expected):
        result = trigger.file_event_completion_time(row)
        assert result == (trigger.parse_event_time(expected) if expected else None)

    @pytest.mark.parametrize("change_source", ["upload", "workflowExecution", "assetVersion",
                                               "assetEdit"])
    def test_the_latest_change_source_and_instant_are_not_consulted(self, change_source):
        row = {"lastUploadAt": self.UPLOAD, "lastAssetVersionAt": self.VERSION,
               "lastChangeSource": change_source, "lastChangeAt": self.LATER_CHANGE}
        assert trigger.file_event_completion_time(row) == trigger.parse_event_time(self.VERSION)

    def test_a_row_stamped_only_with_a_latest_change_has_no_completion(self):
        row = {"lastChangeSource": "assetVersion", "lastChangeAt": self.LATER_CHANGE}
        assert trigger.file_event_completion_time(row) is None

    def test_the_stamp_names_the_attribute_it_read(self):
        assert trigger.file_event_completion_stamp(
            {"lastUploadAt": self.UPLOAD, "lastAssetVersionAt": self.VERSION}) == (
            "lastAssetVersionAt", trigger.parse_event_time(self.VERSION))
        assert trigger.file_event_completion_stamp({}) == (None, None)


@pytest.mark.unit
class TestEachCoverageDecisionLogsOneLine:
    """One INFO line per coverage decision: the asset by id, the path, the stamp attribute and its
    instant, the S3 eventTime, the last evaluation compared with, and the outcome; never an object
    key."""

    def test_a_covered_file_event(self):
        mocks = _run(_sns_event(*_revert_copy_messages(names=("r-1.txt",))),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(REVERT_EVALUATION_START),
                     asset_rows=_reverted_rows())
        assert _coverage_lines(mocks) == [
            f"Compliance coverage asset={ASSET} path=file stamp=lastAssetVersionAt "
            f"stampAt={REVERT_STAMP} " "eventTime=2026-03-01T18:09:09.310000+00:00 "
            f"lastEvaluationId=eval-prev lastEvaluatedAt={REVERT_EVALUATION_START} outcome=covered"]
        assert not any("r-1.txt" in message for message in _info_messages(mocks))

    def test_an_evaluating_stream_modify(self):
        mocks = _run(_sns_event(_stream_message(change_source="assetVersion",
                                                change_at=REVERT_STAMP)),
                     database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(EARLIER_UPLOAD_EVALUATION))
        assert _coverage_lines(mocks) == [
            f"Compliance coverage asset={ASSET} path=stream stamp=lastChangeAt "
            f"stampAt={REVERT_STAMP} eventTime=none lastEvaluationId=eval-prev "
            f"lastEvaluatedAt={EARLIER_UPLOAD_EVALUATION} outcome=evaluating"]

    def test_a_stream_insert_records_no_stamp(self):
        mocks = _run(_sns_event(_stream_message(event_name="INSERT", change_source="upload")),
                     database_item=AUTO_EVAL_DB, compliance_record={"schemaName": SCHEMA})
        assert _coverage_lines(mocks) == [
            f"Compliance coverage asset={ASSET} path=stream stamp=none stampAt=none eventTime=none "
            "lastEvaluationId=none lastEvaluatedAt=none outcome=evaluating"]

    def test_a_direct_message(self):
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA})
        assert _coverage_lines(mocks) == [
            f"Compliance coverage asset={ASSET} path=direct stamp=none stampAt=none eventTime=none "
            "lastEvaluationId=none lastEvaluatedAt=none outcome=evaluating"]

    def test_one_line_per_message_and_none_for_a_workflow_written_object(self):
        messages = _revert_copy_messages() + [
            indexer_message(s3_event_record(f"{ASSET}/out.glb", event_time=REVERT_COPY_EVENT_TIME))]
        mocks = _run(_sns_event(*messages), database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(REVERT_EVALUATION_START),
                     asset_rows=_reverted_rows(),
                     head=[_s3_head("upload")] * 3 + [_s3_head("workflowExecution")])
        assert len(_coverage_lines(mocks)) == 3

    def test_an_asset_that_is_not_evaluated_logs_no_decision(self):
        mocks = _run(_sns_event(_stream_message()), database_item={"databaseId": DB},
                     compliance_record={"schemaName": SCHEMA})
        assert _coverage_lines(mocks) == []


# D13 as the sbxf trigger log recorded it, on a pipeline-rule asset whose workflow-launch Lambdas
# were cold: the upload's objects land, the upload completion stamps the row, the stream MODIFY
# starts an evaluation 0.4 s later, and the object's file-indexer message arrives while that
# evaluation's first workflow launch is still in flight.
COLD_PRIOR_EVALUATION_START = "2026-03-01T21:17:54.000000+00:00"
COLD_UPLOAD_EVENT_TIME = "2026-03-01T21:18:10.900Z"
COLD_UPLOAD_STAMP = "2026-03-01T21:18:11.050000+00:00"
COLD_STREAM_EVALUATION_START = "2026-03-01T21:18:11.400000+00:00"


def _clock(start):
    """A `now_iso` stand-in: `start`, then 100 ms later on every call, so each evaluation that
    starts later in the run starts at a later instant."""
    first = datetime.fromisoformat(start)
    instants = (first + timedelta(milliseconds=100 * n) for n in itertools.count())
    return lambda: next(instants).isoformat()


@pytest.mark.unit
class TestAnInvocationOverlappingARunningEvaluation:
    """Two trigger invocations for one asset, the second running while the first's evaluation is
    still launching its workflow. The first's evaluation claimed the state row before it launched
    and the trigger reads the row strongly consistent, so the second sees the claim: a message for
    the change being evaluated is covered and starts nothing, while a change made after that
    evaluation started is still evaluated. Runs the real trigger and the real evaluation store
    against a state table that keeps what is written to it."""

    def _run_overlapping(self, file_event_time, head=None):
        tables = Tables()
        state = FakeAssetStateTable({
            "databaseId": DB, "assetId": ASSET, "schemaName": SCHEMA, "schemaSource": "database",
            "complianceState": "compliant", "lastEvaluationId": "eval-prev",
            "lastEvaluatedAt": COLD_PRIOR_EVALUATION_START, "lastEvaluationStatus": "completed"})
        tables.state = state.mock
        database_table = MagicMock(name="database_table")
        database_table.get_item.return_value = {"Item": AUTO_EVAL_DB}
        asset_table = MagicMock(name="asset_table")
        asset_table.query.return_value = {"Items": _stamped_rows(COLD_UPLOAD_STAMP)}
        s3_client = MagicMock(name="s3_client")
        s3_client.head_object.return_value = head or _s3_head("upload")
        logger = MagicMock(name="logger")
        launch = tables.lambda_client.invoke.return_value
        row_during_launch = []

        def cold_launch(**_):
            # The file indexer's message is processed by a second invocation while this launch is
            # still in flight; only the first launch carries it.
            if not row_during_launch:
                row_during_launch.append(state.row(DB, ASSET))
                trigger.lambda_handler(_sns_event(indexer_message(s3_event_record(
                    f"{ASSET}/model.stl", event_time=file_event_time))), MagicMock())
            return launch

        tables.lambda_client.invoke.side_effect = cold_launch
        stream = _sns_event(_stream_message(change_source="upload", change_at=COLD_UPLOAD_STAMP))
        with patch(f"{MOD}.database_table", database_table), \
                patch(f"{MOD}.asset_table", asset_table), \
                patch(f"{MOD}.cascade_table", MagicMock(name="cascade_table")), \
                patch(f"{MOD}.s3_client", s3_client), \
                patch(f"{MOD}.logger", logger), \
                patch(f"{STORE}.now_iso", side_effect=_clock(COLD_STREAM_EVALUATION_START)):
            tables.run(trigger.lambda_handler, stream, MagicMock())
        logger.exception.assert_not_called()
        return tables, state, row_during_launch[0], {"logger": logger}

    def test_the_file_message_of_the_change_being_evaluated_is_covered_and_runs_nothing(self):
        tables, state, row_during_launch, mocks = self._run_overlapping(COLD_UPLOAD_EVENT_TIME)

        parents, _ = tables.evaluation_records()
        assert len(parents) == 1
        evaluation_id = parents[0]["evaluationId"]
        assert parents[0]["evaluatedAt"] == COLD_STREAM_EVALUATION_START
        assert tables.lambda_client.invoke.call_count == 1
        # The row the second invocation read: the first evaluation's claim, before its launch
        # returned.
        assert row_during_launch["lastEvaluationId"] == evaluation_id
        assert row_during_launch["lastEvaluatedAt"] == COLD_STREAM_EVALUATION_START
        assert row_during_launch["lastEvaluationStatus"] == "pending_pipeline"
        assert _coverage_lines(mocks) == [
            f"Compliance coverage asset={ASSET} path=stream stamp=lastChangeAt "
            f"stampAt={COLD_UPLOAD_STAMP} eventTime=none lastEvaluationId=eval-prev "
            f"lastEvaluatedAt={COLD_PRIOR_EVALUATION_START} outcome=evaluating",
            f"Compliance coverage asset={ASSET} path=file stamp=lastUploadAt "
            f"stampAt={COLD_UPLOAD_STAMP} eventTime=2026-03-01T21:18:10.900000+00:00 "
            f"lastEvaluationId={evaluation_id} lastEvaluatedAt={COLD_STREAM_EVALUATION_START} "
            "outcome=covered"]
        assert state.row(DB, ASSET)["lastEvaluationId"] == evaluation_id
        assert state.row(DB, ASSET)["complianceState"] == "pending_evaluation"
        # Every read of the row on this path is strongly consistent.
        assert {read for op, read in state.calls if op == "get_item"} == {True}

    def test_an_upload_object_written_after_the_running_evaluation_started_waits_for_its_completion(
            self):
        """An object written a second after the first evaluation started belongs to an upload whose
        own completion has not stamped the row yet. It starts no evaluation over the partial file
        set: that upload's completion stamp raises its own MODIFY, which evaluates the asset once
        with every file in place."""
        tables, state, _, mocks = self._run_overlapping("2026-03-01T21:18:12.400Z")

        parents, _ = tables.evaluation_records()
        assert len(parents) == 1
        assert tables.lambda_client.invoke.call_count == 1
        assert [line.rsplit(" ", 1)[1] for line in _coverage_lines(mocks)] == [
            "outcome=evaluating", "outcome=deferred"]

    def test_a_direct_write_after_the_running_evaluation_started_is_still_evaluated(self):
        """A direct bucket write (no `vams-changesource`) has no completion to wait for: the change
        that evaluation did not read is evaluated by the second invocation, and its newer evaluation
        keeps the row."""
        tables, state, _, mocks = self._run_overlapping("2026-03-01T21:18:12.400Z", head=_s3_head())

        parents, _ = tables.evaluation_records()
        assert len(parents) == 2
        first, second = sorted(parents, key=lambda row: row["evaluatedAt"])
        assert tables.lambda_client.invoke.call_count == 2
        assert [line.rsplit(" ", 1)[1] for line in _coverage_lines(mocks)] == [
            "outcome=evaluating", "outcome=evaluating"]
        assert state.row(DB, ASSET)["lastEvaluationId"] == second["evaluationId"]
        assert first["evaluatedAt"] < second["evaluatedAt"]


@pytest.mark.unit
class TestTheCoverageReadIsStronglyConsistent:

    @pytest.mark.parametrize("event", [
        _sns_event(_stream_message(change_source="upload")),
        _sns_event(indexer_message(s3_event_record(f"{ASSET}/one.stl",
                                                   event_time=UPLOAD_EVENT_TIME))),
        _sns_event({"databaseId": DB, "assetId": ASSET}),
    ], ids=["stream", "file", "direct"])
    def test_every_path_reads_the_state_row_consistently(self, event):
        mocks = _run(event, database_item=AUTO_EVAL_DB,
                     compliance_record=_record_evaluated_at(COLD_PRIOR_EVALUATION_START),
                     asset_rows=_stamped_rows(UPLOAD_STAMP))
        reads = mocks["get_compliance_record"].call_args_list
        assert reads
        assert all(call.kwargs == {"consistent_read": True} for call in reads)
