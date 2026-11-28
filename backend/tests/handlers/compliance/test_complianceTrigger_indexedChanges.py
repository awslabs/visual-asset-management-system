# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Metadata and link changes are stamped by the compliance trigger from the indexer events, not on
the request that made them.

Every stream record of the asset-file-metadata, file-attribute, asset-link and asset-link-metadata
tables maps to the asset(s) it changes (both ends of a link and of a link's metadata) and is recorded
on the asset row as `metadataEdit` / `linkChange` with the change's own instant, conditionally, in
every database whether it is bound or not. A record that only stamps evaluates nothing; the stamp's
asset-table MODIFY does, judged across the skew margin. A pipeline rule's own metadata write-back is
stamped `workflowExecution` and never evaluates."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from backend.tests.handlers.compliance._harness import ASSET, DB, SCHEMA
from backend.tests.handlers.compliance.test_complianceTrigger import (
    AUTO_EVAL_DB, _run, _sns_event, _stream_message)
from common.assetProvenance import record_indexed_change
from handlers.compliance import complianceTrigger as trigger

OTHER_DB = "db-other"
OTHER_ASSET = "x0ther-asset"
LINK_ID = "11111111-2222-4333-8444-555555555555"
# 2026-03-01T12:00:00Z; the stream reports the second, so the stamp is the following second.
CHANGE_EPOCH = 1772366400
CHANGE_STAMP = "2026-03-01T12:00:01.000000+00:00"
THROTTLED = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "UpdateItem")
CONDITION_FAILED = ClientError({"Error": {"Code": "ConditionalCheckFailedException",
                                          "Message": "no"}}, "UpdateItem")


def _s(value):
    return {"S": value}


def _metadata_record(event_name="MODIFY", database_id=DB, asset_id=ASSET, file_path="/model.stl",
                     key_field="metadataKey", epoch=CHANGE_EPOCH, image_extra=None):
    composite = f"{database_id}:{asset_id}:{file_path}"
    keys = {key_field: _s("units"), "databaseId:assetId:filePath": _s(composite)}
    image = {**keys, "databaseId:assetId": _s(f"{database_id}:{asset_id}"),
             "metadataValue": _s("mm"), **(image_extra or {})}
    data = {"Keys": keys, "ApproximateCreationDateTime": epoch, "SequenceNumber": "1"}
    data["OldImage" if event_name == "REMOVE" else "NewImage"] = image
    return {"eventName": event_name, "eventSource": "aws:dynamodb", "dynamodb": data}


def _link_image(from_db=DB, from_asset=ASSET, to_db=OTHER_DB, to_asset=OTHER_ASSET):
    return {"assetLinkId": _s(LINK_ID), "fromAssetDatabaseId": _s(from_db),
            "fromAssetId": _s(from_asset), "toAssetDatabaseId": _s(to_db),
            "toAssetId": _s(to_asset), "relationshipType": _s("parentChild"),
            "fromAssetDatabaseId:fromAssetId": _s(f"{from_db}:{from_asset}"),
            "toAssetDatabaseId:toAssetId": _s(f"{to_db}:{to_asset}")}


def _link_record(event_name="INSERT", image=None, epoch=CHANGE_EPOCH):
    data = {"Keys": {"assetLinkId": _s(LINK_ID)}, "ApproximateCreationDateTime": epoch}
    if image is not False:
        data["OldImage" if event_name == "REMOVE" else "NewImage"] = image or _link_image()
    return {"eventName": event_name, "eventSource": "aws:dynamodb", "dynamodb": data}


def _link_metadata_record(event_name="INSERT", image_extra=None):
    keys = {"assetLinkId": _s(LINK_ID), "metadataKey": _s("placement")}
    data = {"Keys": keys, "ApproximateCreationDateTime": CHANGE_EPOCH,
            "OldImage" if event_name == "REMOVE" else "NewImage": {
                **keys, "metadataValue": _s("top"), **(image_extra or {})}}
    return {"eventName": event_name, "eventSource": "aws:dynamodb", "dynamodb": data}


def _stamps(mocks):
    """`(databaseId, assetId, source, lastChangeAt, workflowExecutionId)` of every stamp written."""
    out = []
    for call in mocks["asset"].update_item.call_args_list:
        values = call.kwargs["ExpressionAttributeValues"]
        out.append((call.kwargs["Key"]["databaseId"], call.kwargs["Key"]["assetId"],
                    values[":src"], values[":at"], values.get(":wf")))
    return out


def _run_links(event, link_item=None, **kwargs):
    """`_run` with the links table answering `link_item` for every GetItem."""
    from unittest.mock import patch
    links = MagicMock(name="asset_links_table")
    links.get_item.return_value = {"Item": link_item} if link_item else {}
    with patch("handlers.compliance.complianceTrigger.asset_links_table", links):
        mocks = _run(event, **kwargs)
    mocks["links"] = links
    return mocks


UNBOUND_DB = {"databaseId": DB}


@pytest.mark.unit
class TestEachRecordKindStampsItsAssets:

    @pytest.mark.parametrize("event_name", ["INSERT", "MODIFY", "REMOVE"])
    def test_a_file_metadata_record_stamps_its_asset(self, event_name):
        mocks = _run(_sns_event(_metadata_record(event_name)), database_item=UNBOUND_DB)
        assert _stamps(mocks) == [(DB, ASSET, "metadataEdit", CHANGE_STAMP, None)]

    def test_an_asset_metadata_record_stamps_its_asset(self):
        mocks = _run(_sns_event(_metadata_record(file_path="/")), database_item=UNBOUND_DB)
        assert _stamps(mocks) == [(DB, ASSET, "metadataEdit", CHANGE_STAMP, None)]

    @pytest.mark.parametrize("event_name", ["INSERT", "REMOVE"])
    def test_a_file_attribute_record_stamps_its_asset(self, event_name):
        mocks = _run(_sns_event(_metadata_record(event_name, key_field="attributeKey")),
                     database_item=UNBOUND_DB)
        assert _stamps(mocks) == [(DB, ASSET, "metadataEdit", CHANGE_STAMP, None)]

    def test_a_file_path_with_colons_still_names_the_asset(self):
        mocks = _run(_sns_event(_metadata_record(file_path="/a:b/c:d.stl")), database_item=UNBOUND_DB)
        assert _stamps(mocks) == [(DB, ASSET, "metadataEdit", CHANGE_STAMP, None)]

    @pytest.mark.parametrize("event_name", ["INSERT", "REMOVE"])
    def test_a_link_record_stamps_both_ends(self, event_name):
        mocks = _run(_sns_event(_link_record(event_name)), database_item=UNBOUND_DB)
        assert sorted(_stamps(mocks)) == sorted([
            (DB, ASSET, "linkChange", CHANGE_STAMP, None),
            (OTHER_DB, OTHER_ASSET, "linkChange", CHANGE_STAMP, None)])

    def test_a_link_remove_without_an_image_stamps_nothing(self):
        mocks = _run(_sns_event(_link_record("REMOVE", image=False)), database_item=UNBOUND_DB)
        assert _stamps(mocks) == []

    def test_a_link_metadata_record_stamps_both_ends_of_its_link(self):
        link = {"assetLinkId": LINK_ID, "fromAssetDatabaseId": DB, "fromAssetId": ASSET,
                "toAssetDatabaseId": OTHER_DB, "toAssetId": OTHER_ASSET}
        mocks = _run_links(_sns_event(_link_metadata_record(), _link_metadata_record("MODIFY")),
                           link_item=link, database_item=UNBOUND_DB)
        assert sorted(_stamps(mocks)) == sorted([
            (DB, ASSET, "metadataEdit", CHANGE_STAMP, None),
            (OTHER_DB, OTHER_ASSET, "metadataEdit", CHANGE_STAMP, None)])
        # The link is read once for the batch.
        mocks["links"].get_item.assert_called_once_with(Key={"assetLinkId": LINK_ID})

    def test_the_metadata_of_a_link_already_gone_stamps_nothing(self):
        mocks = _run_links(_sns_event(_link_metadata_record("REMOVE")), link_item=None,
                           database_item=UNBOUND_DB)
        assert _stamps(mocks) == []

    def test_an_archived_partition_is_not_stamped(self):
        mocks = _run(_sns_event(_metadata_record(database_id=f"{DB}#deleted")),
                     database_item=UNBOUND_DB)
        assert _stamps(mocks) == []


@pytest.mark.unit
class TestAStampOnlyRecordIsCheap:

    def test_an_unbound_database_costs_one_conditional_update_and_no_evaluation_read(self):
        mocks = _run(_sns_event(_metadata_record()), database_item=UNBOUND_DB)
        assert mocks["asset"].update_item.call_count == 1
        assert "ConditionExpression" in mocks["asset"].update_item.call_args.kwargs
        mocks["database"].get_item.assert_not_called()
        mocks["get_compliance_record"].assert_not_called()
        mocks["asset"].query.assert_not_called()
        mocks["s3"].head_object.assert_not_called()
        mocks["run_evaluation"].assert_not_called()

    def test_a_bound_auto_eval_database_still_evaluates_nothing_on_the_record_itself(self):
        mocks = _run(_sns_event(_metadata_record()), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA})
        assert len(_stamps(mocks)) == 1
        mocks["run_evaluation"].assert_not_called()
        mocks["database"].get_item.assert_not_called()

    def test_several_changes_of_one_asset_in_a_batch_write_one_stamp_at_the_latest_instant(self):
        event = _sns_event(_metadata_record(epoch=CHANGE_EPOCH),
                           _metadata_record(epoch=CHANGE_EPOCH + 4, key_field="attributeKey"),
                           _metadata_record("REMOVE", epoch=CHANGE_EPOCH + 2))
        mocks = _run(event, database_item=UNBOUND_DB)
        assert _stamps(mocks) == [(DB, ASSET, "metadataEdit", "2026-03-01T12:00:05.000000+00:00",
                                   None)]


@pytest.mark.unit
class TestTheStampsModifyIsTheOneEvaluation:

    def test_bound_and_auto_eval_evaluates_once_through_the_stamps_modify(self):
        record = {"schemaName": SCHEMA, "lastEvaluationId": "eval-0",
                  "lastEvaluatedAt": "2026-03-01T11:00:00+00:00"}
        first = _run(_sns_event(_metadata_record()), database_item=AUTO_EVAL_DB,
                     compliance_record=record)
        first["run_evaluation"].assert_not_called()
        (_, _, source, change_at, _), = _stamps(first)
        # The stamp's asset-table MODIFY, as the asset indexer topic delivers it.
        second = _run(_sns_event(_stream_message(change_source=source, change_at=change_at),
                                 _stream_message(change_source=source, change_at=change_at)),
                      database_item=AUTO_EVAL_DB, compliance_record=record)
        second["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    @pytest.mark.parametrize("last_evaluated_at,evaluates", [
        # Started a second after the stamp: within the skew margin, not counted as covering it.
        ("2026-03-01T12:00:02+00:00", True),
        ("2026-03-01T12:00:03+00:00", False),
    ])
    def test_a_stamp_from_an_indexer_event_is_judged_across_the_skew_margin(
            self, last_evaluated_at, evaluates):
        record = {"schemaName": SCHEMA, "lastEvaluationId": "eval-0",
                  "lastEvaluatedAt": last_evaluated_at}
        mocks = _run(_sns_event(_stream_message(change_source="metadataEdit",
                                                change_at=CHANGE_STAMP)),
                     database_item=AUTO_EVAL_DB, compliance_record=record)
        assert mocks["run_evaluation"].called is evaluates

    def test_a_late_event_is_covered_by_an_evaluation_that_already_read_the_change(self):
        """The metadata changed at 12:00:00; an evaluation for another change started at 12:00:30
        and read it; the metadata record arrives at 12:05. Its stamp is the change's own instant, so
        the stamp's MODIFY is covered rather than starting a second evaluation."""
        record = {"schemaName": SCHEMA, "lastEvaluationId": "eval-1",
                  "lastEvaluatedAt": "2026-03-01T12:00:30+00:00"}
        first = _run(_sns_event(_metadata_record()), database_item=AUTO_EVAL_DB,
                     compliance_record=record)
        (_, _, source, change_at, _), = _stamps(first)
        assert change_at == CHANGE_STAMP
        second = _run(_sns_event(_stream_message(change_source=source, change_at=change_at)),
                      database_item=AUTO_EVAL_DB, compliance_record=record)
        second["run_evaluation"].assert_not_called()


@pytest.mark.unit
class TestAPipelineRulesMetadataWriteBackNeverEvaluates:

    def test_a_row_the_write_back_wrote_is_stamped_as_the_execution(self):
        mocks = _run(_sns_event(_metadata_record(image_extra={
            "lastChangeWorkflowExecutionId": _s("exec-1")})), database_item=UNBOUND_DB)
        assert _stamps(mocks) == [(DB, ASSET, "workflowExecution", CHANGE_STAMP, "exec-1")]

    def test_a_row_the_write_back_deleted_is_stamped_as_the_execution(self):
        mocks = _run(_sns_event(_metadata_record("REMOVE", image_extra={
            "deletedByWorkflowExecutionId": _s("exec-2")})), database_item=UNBOUND_DB)
        assert _stamps(mocks) == [(DB, ASSET, "workflowExecution", CHANGE_STAMP, "exec-2")]

    def test_a_user_deleting_a_row_a_workflow_wrote_is_a_user_change(self):
        mocks = _run(_sns_event(_metadata_record("REMOVE", image_extra={
            "lastChangeWorkflowExecutionId": _s("exec-1")})), database_item=UNBOUND_DB)
        assert _stamps(mocks) == [(DB, ASSET, "metadataEdit", CHANGE_STAMP, None)]

    def test_the_workflow_stamps_modify_starts_no_evaluation(self):
        first = _run(_sns_event(_metadata_record(image_extra={
            "lastChangeWorkflowExecutionId": _s("exec-1")})), database_item=AUTO_EVAL_DB,
            compliance_record={"schemaName": SCHEMA})
        (_, _, source, change_at, execution_id), = _stamps(first)
        second = _run(_sns_event(_stream_message(change_source=source, change_at=change_at,
                                                 workflow_execution_id=execution_id)),
                      database_item=AUTO_EVAL_DB, compliance_record={"schemaName": SCHEMA})
        second["run_evaluation"].assert_not_called()

    def test_a_user_change_in_the_same_batch_wins_so_the_stamp_evaluates(self):
        event = _sns_event(
            _metadata_record(epoch=CHANGE_EPOCH + 5,
                             image_extra={"lastChangeWorkflowExecutionId": _s("exec-1")}),
            _metadata_record(epoch=CHANGE_EPOCH, key_field="attributeKey"))
        mocks = _run(event, database_item=UNBOUND_DB)
        assert _stamps(mocks) == [(DB, ASSET, "metadataEdit", "2026-03-01T12:00:06.000000+00:00",
                                   None)]


@pytest.mark.unit
class TestStampFailures:

    def test_an_infrastructure_error_fails_every_record_of_the_asset(self):
        event = _sns_event(_metadata_record(), _metadata_record(key_field="attributeKey"),
                           _link_record())
        def update_item(**kwargs):
            if kwargs["Key"]["assetId"] == ASSET:
                raise THROTTLED
            return {}
        from unittest.mock import patch
        asset_table = MagicMock(name="asset_table")
        asset_table.update_item.side_effect = update_item
        with patch("handlers.compliance.complianceTrigger.asset_table", asset_table):
            response = trigger.lambda_handler(event, MagicMock())
        assert sorted(e["itemIdentifier"] for e in response["batchItemFailures"]) == [
            "q-1", "q-2", "q-3"]
        # The other end of the link was still stamped.
        assert any(c.kwargs["Key"]["assetId"] == OTHER_ASSET
                   for c in asset_table.update_item.call_args_list)

    def test_a_condition_that_does_not_hold_is_not_a_failure(self):
        from unittest.mock import patch
        asset_table = MagicMock(name="asset_table")
        asset_table.update_item.side_effect = CONDITION_FAILED
        with patch("handlers.compliance.complianceTrigger.asset_table", asset_table):
            response = trigger.lambda_handler(_sns_event(_metadata_record()), MagicMock())
        assert response == {"batchItemFailures": []}


@pytest.mark.unit
class TestIndexedChangeTime:

    @pytest.mark.parametrize("value,expected", [
        (CHANGE_EPOCH, CHANGE_STAMP),
        (CHANGE_EPOCH + 0.75, CHANGE_STAMP),
        (str(CHANGE_EPOCH), CHANGE_STAMP),
    ])
    def test_the_stream_second_is_rounded_up(self, value, expected):
        instant = trigger.indexed_change_time({"ApproximateCreationDateTime": value})
        assert instant.isoformat(timespec="microseconds") == expected

    def test_a_record_without_the_field_is_stamped_now(self):
        before = datetime.now(timezone.utc)
        assert trigger.indexed_change_time({}) >= before


# --- record_indexed_change against a real conditional write (moto) ---------------------------

ASSET_TABLE = {
    "TableName": "assetStorageTable",
    "KeySchema": [{"AttributeName": "databaseId", "KeyType": "HASH"},
                  {"AttributeName": "assetId", "KeyType": "RANGE"}],
    "AttributeDefinitions": [{"AttributeName": "databaseId", "AttributeType": "S"},
                             {"AttributeName": "assetId", "AttributeType": "S"}],
    "BillingMode": "PAY_PER_REQUEST",
}
EARLY = "2026-03-01T11:00:00.000000+00:00"
LATE = "2026-03-01T13:00:00.000000+00:00"


@pytest.fixture
def asset_table():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        yield resource.create_table(**ASSET_TABLE)


def _row(table, database_id=DB, asset_id=ASSET):
    return table.get_item(Key={"databaseId": database_id, "assetId": asset_id}).get("Item")


@pytest.mark.unit
class TestRecordIndexedChange:

    def test_a_newer_change_is_recorded(self, asset_table):
        asset_table.put_item(Item={"databaseId": DB, "assetId": ASSET, "lastChangeSource": "upload",
                                   "lastChangeAt": EARLY, "lastUploadAt": EARLY})
        assert record_indexed_change(asset_table, DB, ASSET, "metadataEdit", CHANGE_STAMP) is True
        row = _row(asset_table)
        assert (row["lastChangeSource"], row["lastChangeAt"], row["lastUploadAt"]) == (
            "metadataEdit", CHANGE_STAMP, EARLY)
        assert "lastChangeRecordedAt" in row

    def test_an_older_event_never_moves_the_stamp_backwards(self, asset_table):
        asset_table.put_item(Item={"databaseId": DB, "assetId": ASSET, "lastChangeSource": "upload",
                                   "lastChangeAt": LATE})
        assert record_indexed_change(asset_table, DB, ASSET, "metadataEdit", CHANGE_STAMP) is False
        assert _row(asset_table)["lastChangeAt"] == LATE
        assert _row(asset_table)["lastChangeSource"] == "upload"

    def test_the_same_instant_is_recorded_again(self, asset_table):
        asset_table.put_item(Item={"databaseId": DB, "assetId": ASSET,
                                   "lastChangeSource": "metadataEdit", "lastChangeAt": CHANGE_STAMP,
                                   "lastChangeRecordedAt": EARLY})
        assert record_indexed_change(asset_table, DB, ASSET, "linkChange", CHANGE_STAMP) is True
        assert _row(asset_table)["lastChangeRecordedAt"] != EARLY

    def test_a_user_change_overwrites_a_later_workflow_stamp(self, asset_table):
        asset_table.put_item(Item={"databaseId": DB, "assetId": ASSET,
                                   "lastChangeSource": "workflowExecution", "lastChangeAt": LATE,
                                   "lastChangeWorkflowExecutionId": "exec-9"})
        assert record_indexed_change(asset_table, DB, ASSET, "metadataEdit", CHANGE_STAMP) is True
        row = _row(asset_table)
        assert (row["lastChangeSource"], row["lastChangeAt"]) == ("metadataEdit", CHANGE_STAMP)
        assert "lastChangeWorkflowExecutionId" not in row

    def test_a_workflow_change_does_not_overwrite_a_later_user_stamp(self, asset_table):
        asset_table.put_item(Item={"databaseId": DB, "assetId": ASSET,
                                   "lastChangeSource": "metadataEdit", "lastChangeAt": LATE})
        assert record_indexed_change(asset_table, DB, ASSET, None, CHANGE_STAMP,
                                     workflow_execution_id="exec-1") is False
        assert _row(asset_table)["lastChangeSource"] == "metadataEdit"

    def test_a_workflow_change_is_recorded_with_its_execution(self, asset_table):
        asset_table.put_item(Item={"databaseId": DB, "assetId": ASSET, "lastChangeAt": EARLY})
        assert record_indexed_change(asset_table, DB, ASSET, None, CHANGE_STAMP,
                                     workflow_execution_id="exec-1") is True
        row = _row(asset_table)
        assert (row["lastChangeSource"], row["lastChangeWorkflowExecutionId"]) == (
            "workflowExecution", "exec-1")

    def test_a_row_without_a_stamp_is_stamped(self, asset_table):
        asset_table.put_item(Item={"databaseId": DB, "assetId": ASSET})
        assert record_indexed_change(asset_table, DB, ASSET, "linkChange", CHANGE_STAMP) is True

    @pytest.mark.parametrize("database_id", [DB, f"{DB}#deleted"])
    def test_a_missing_or_archived_asset_is_not_stamped_and_no_row_is_created(
            self, asset_table, database_id):
        if database_id != DB:
            asset_table.put_item(Item={"databaseId": database_id, "assetId": ASSET})
        assert record_indexed_change(asset_table, DB, ASSET, "linkChange", CHANGE_STAMP) is False
        assert _row(asset_table) is None

    def test_a_link_remove_after_a_permanent_delete_stamps_only_the_survivor(self, asset_table):
        """The deleted asset's row is gone by the time the link's REMOVE is processed (the permanent
        delete removes the asset row before its links), so only the asset at the other end is
        stamped."""
        from unittest.mock import patch
        asset_table.put_item(Item={"databaseId": OTHER_DB, "assetId": OTHER_ASSET,
                                   "lastChangeAt": EARLY})
        with patch("handlers.compliance.complianceTrigger.asset_table", asset_table):
            response = trigger.lambda_handler(_sns_event(_link_record("REMOVE")), MagicMock())
        assert response == {"batchItemFailures": []}
        assert _row(asset_table, OTHER_DB, OTHER_ASSET)["lastChangeSource"] == "linkChange"
        assert _row(asset_table) is None

    def test_any_other_error_is_raised(self):
        table = MagicMock()
        table.update_item.side_effect = THROTTLED
        with pytest.raises(ClientError):
            record_indexed_change(table, DB, ASSET, "linkChange", CHANGE_STAMP)
