# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceTrigger (SNS-invoked): the three message shapes it accepts, the complianceAutoEval gate,
schema resolution (asset override, else the database binding with auto-registration), the evaluation
it runs, the cascade it opens for an asset with children, and the two guards against multiplying
evaluations: the provenance skip for objects a workflow execution wrote, and the coalescing of a change
into an evaluation still awaiting its pipeline rules."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.tests.handlers.compliance._harness import ASSET, DB, SCHEMA, put_items
from handlers.compliance import complianceTrigger as trigger

MOD = "handlers.compliance.complianceTrigger"
STORE = "handlers.compliance.complianceEvaluationStore"

EVALUATION = {"evaluationId": "eval-1", "verdict": "compliant", "complianceState": "compliant",
              "ruleResults": [], "pipelineRulesPending": 0}


def _sns_event(*messages):
    return {"Records": [{"Sns": {"Message": json.dumps(m)}} for m in messages]}


def _stream_message(event_name="MODIFY", database_id=DB, asset_id=ASSET, created_at=None):
    dynamodb_data = {
        "Keys": {"databaseId": {"S": database_id}, "assetId": {"S": asset_id}},
        "NewImage": {"databaseId": {"S": database_id}, "assetId": {"S": asset_id}}}
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
         evaluation=EVALUATION, evaluation_rows=None, head=None):
    database_table = MagicMock(name="database_table")
    database_table.get_item.return_value = {"Item": database_item} if database_item else {}
    cascade_table = MagicMock(name="cascade_table")
    state_table = MagicMock(name="asset_state_table")
    asset_table = MagicMock(name="asset_table")
    asset_table.query.return_value = {"Items": list(asset_rows or [])}
    s3_client = MagicMock(name="s3_client")
    if isinstance(head, (Exception, list)):
        s3_client.head_object.side_effect = head
    else:
        s3_client.head_object.return_value = head if head is not None else _s3_head("upload")
    rows = dict(evaluation_rows or {})

    def _get_evaluation(evaluation_id, consistent_read=False):
        return rows.get(evaluation_id)

    with patch(f"{MOD}.database_table", database_table), \
            patch(f"{MOD}.cascade_table", cascade_table), \
            patch(f"{MOD}.asset_state_table", state_table), \
            patch(f"{MOD}.asset_table", asset_table), \
            patch(f"{MOD}.s3_client", s3_client), \
            patch(f"{STORE}.get_compliance_record", return_value=compliance_record), \
            patch(f"{STORE}.get_evaluation", side_effect=_get_evaluation) as get_evaluation, \
            patch(f"{STORE}.get_child_links", return_value=list(children)), \
            patch(f"{STORE}.run_evaluation", return_value=evaluation) as run_evaluation, \
            patch(f"{STORE}.write_audit") as write_audit:
        trigger.lambda_handler(event, MagicMock())
    return {"database": database_table, "cascade": cascade_table, "state": state_table,
            "asset": asset_table, "s3": s3_client, "get_evaluation": get_evaluation,
            "run_evaluation": run_evaluation, "audit": write_audit}


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


def _file_message(key=f"{ASSET}/model/part.glb", event_time="2026-03-01T12:00:00.000Z",
                  version_id="v1", bucket="asset-bucket", wrapped=False):
    record = {"eventTime": event_time,
              "s3": {"bucket": {"name": bucket}, "object": {"key": key, "versionId": version_id}}}
    if wrapped:
        return {"Records": [record], "ASSET_BUCKET_NAME": bucket, "ASSET_BUCKET_PREFIX": ""}
    return dict(record, ASSET_BUCKET_NAME=bucket, ASSET_BUCKET_PREFIX="")


ASSET_ROWS = [{"databaseId": DB, "assetId": ASSET}]


@pytest.mark.unit
class TestWorkflowWrittenObjectsAreSkipped:
    """A pipeline rule's workflow writes its outputs into the asset; without this guard each such write
    re-enters the trigger and relaunches the evaluation that produced it."""

    def test_an_object_a_workflow_execution_wrote_is_not_evaluated(self):
        mocks = _run(_sns_event(_file_message()), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows=ASSET_ROWS,
                     head=_s3_head("workflowExecution"))
        mocks["run_evaluation"].assert_not_called()
        mocks["database"].get_item.assert_not_called()

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

    @pytest.mark.parametrize("change_source", ["upload", "direct", "fileCopy", None])
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
class TestChangesCoalesceIntoAPendingEvaluation:
    """An N-file upload emits N file events; the first starts an evaluation whose pipeline rules are in
    flight, and the rest land while the asset's state row points at it. Those are coalesced into it
    rather than each launching another execution."""

    def test_a_second_file_event_while_the_evaluation_is_pending_launches_nothing(self):
        mocks = _run(_sns_event(_file_message(event_time="2026-03-01T12:00:00.000Z")),
                     database_item=AUTO_EVAL_DB, compliance_record=PENDING_RECORD,
                     asset_rows=ASSET_ROWS, evaluation_rows=_pending_rows("2026-03-01T12:00:05+00:00"))
        mocks["run_evaluation"].assert_not_called()
        mocks["cascade"].put_item.assert_not_called()
        read = mocks["get_evaluation"].call_args
        assert read.args[0] == "eval-pending"
        assert read.kwargs["consistent_read"] is True

    def test_a_change_after_the_pending_evaluation_began_is_evaluated(self):
        """The in-flight evaluation cannot have seen a file that landed after it started."""
        mocks = _run(_sns_event(_file_message(event_time="2026-03-01T12:00:10.000Z")),
                     database_item=AUTO_EVAL_DB, compliance_record=PENDING_RECORD,
                     asset_rows=ASSET_ROWS, evaluation_rows=_pending_rows("2026-03-01T12:00:05+00:00"))
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_a_stream_record_is_coalesced_by_its_creation_time(self):
        covered = _run(_sns_event(_stream_message(created_at=1772366400.0)),  # 2026-03-01T12:00:00Z
                       database_item=AUTO_EVAL_DB, compliance_record=PENDING_RECORD,
                       evaluation_rows=_pending_rows("2026-03-01T12:00:05+00:00"))
        covered["run_evaluation"].assert_not_called()
        later = _run(_sns_event(_stream_message(created_at="1772366410")),  # 12:00:10Z, as str
                     database_item=AUTO_EVAL_DB, compliance_record=PENDING_RECORD,
                     evaluation_rows=_pending_rows("2026-03-01T12:00:05+00:00"))
        later["run_evaluation"].assert_called_once()

    def test_an_event_without_a_time_is_covered_by_any_pending_evaluation(self):
        mocks = _run(_sns_event({"databaseId": DB, "assetId": ASSET}), database_item=AUTO_EVAL_DB,
                     compliance_record=PENDING_RECORD, evaluation_rows=_pending_rows())
        mocks["run_evaluation"].assert_not_called()

    @pytest.mark.parametrize("status", ["completed", "error", "failed"])
    def test_a_state_row_left_pending_by_a_finished_evaluation_does_not_suppress(self, status):
        mocks = _run(_sns_event(_file_message()), database_item=AUTO_EVAL_DB,
                     compliance_record=PENDING_RECORD, asset_rows=ASSET_ROWS,
                     evaluation_rows=_pending_rows(status=status))
        mocks["run_evaluation"].assert_called_once()

    def test_a_pending_state_row_whose_evaluation_is_gone_does_not_suppress(self):
        mocks = _run(_sns_event(_file_message()), database_item=AUTO_EVAL_DB,
                     compliance_record=PENDING_RECORD, asset_rows=ASSET_ROWS, evaluation_rows={})
        mocks["run_evaluation"].assert_called_once()

    @pytest.mark.parametrize("state", ["compliant", "non_compliant", "quarantined", "unknown"])
    def test_a_settled_state_row_reads_no_evaluation(self, state):
        mocks = _run(_sns_event(_file_message()), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA, "complianceState": state,
                                        "lastEvaluationId": "eval-old"},
                     asset_rows=ASSET_ROWS, evaluation_rows=_pending_rows())
        mocks["get_evaluation"].assert_not_called()
        mocks["run_evaluation"].assert_called_once()

    def test_a_pending_state_row_without_an_evaluation_id_does_not_suppress(self):
        mocks = _run(_sns_event(_file_message()), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA, "complianceState": "pending_evaluation"},
                     asset_rows=ASSET_ROWS)
        mocks["get_evaluation"].assert_not_called()
        mocks["run_evaluation"].assert_called_once()

    @pytest.mark.parametrize("value,expected", [
        ("2026-03-01T12:00:00.000Z", datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)),
        ("2026-03-01T12:00:05.123456+00:00", datetime(2026, 3, 1, 12, 0, 5, 123456, tzinfo=timezone.utc)),
        (1772366400, datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)),
        ("1772366400.5", datetime(2026, 3, 1, 12, 0, 0, 500000, tzinfo=timezone.utc)),
        ("", None), (None, None), ("not a time", None), ("inf", None),
    ])
    def test_event_time_parsing(self, value, expected):
        assert trigger.parse_event_time(value) == expected
