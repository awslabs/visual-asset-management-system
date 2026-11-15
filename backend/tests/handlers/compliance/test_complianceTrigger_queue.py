# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceTrigger consumes its own SQS queue: every record is an SNS `Notification` around one
indexer-topic message; a batch is processed as a whole, an asset is evaluated at most once per batch
and its database row, asset row and evaluation target are read once; and only an infrastructure
error (an AWS call that failed) fails a record for redelivery, while a malformed message, a schema
or rule error and a failed cascade do not."""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from backend.tests.common.test_indexerEvents import (
    indexer_message, s3_event_record, sns_envelope, sqs_record)
from backend.tests.handlers.compliance._harness import ASSET, DB, SCHEMA
from backend.tests.handlers.compliance.test_complianceTrigger import (
    AUTO_EVAL_DB, _run, _sns_event, _stream_message)
from handlers.compliance import complianceTrigger as trigger

OTHER_ASSET = "x0ther-asset"
THROTTLED = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException",
                                   "Message": "slow down"}}, "GetItem")
CONDITION_FAILED = ClientError({"Error": {"Code": "ConditionalCheckFailedException",
                                          "Message": "no"}}, "UpdateItem")


def _record_never_evaluated(**extra):
    return {"schemaName": SCHEMA, "schemaSource": "database", **extra}


def _failed_ids(mocks):
    return [entry["itemIdentifier"] for entry in mocks["response"]["batchItemFailures"]]


@pytest.mark.unit
class TestQueueRecordsAreUnwrapped:

    def test_the_sns_notification_in_the_body_is_unwrapped(self):
        record = sqs_record(sns_envelope({"databaseId": DB, "assetId": ASSET}), message_id="q-1")
        assert trigger.message_from_queue_record(record) == {"databaseId": DB, "assetId": ASSET}

    def test_a_raw_body_is_read_as_the_message_itself(self):
        record = sqs_record({"databaseId": DB, "assetId": ASSET}, message_id="q-1")
        assert trigger.message_from_queue_record(record) == {"databaseId": DB, "assetId": ASSET}

    @pytest.mark.parametrize("body", ["not json", json.dumps([1, 2]),
                                      json.dumps({"Type": "Notification", "Message": "not json"})])
    def test_a_body_that_is_no_json_object_yields_none(self, body):
        assert trigger.message_from_queue_record({"messageId": "q-1", "body": body}) is None

    def test_a_clean_batch_reports_no_failures(self):
        mocks = _run(_sns_event(_stream_message()), database_item=AUTO_EVAL_DB,
                     compliance_record=_record_never_evaluated())
        assert mocks["response"] == {"batchItemFailures": []}

    def test_a_malformed_record_is_dropped_not_retried(self):
        event = {"Records": [{"messageId": "q-bad", "body": "not json"}]}
        mocks = _run(event, database_item=AUTO_EVAL_DB)
        assert _failed_ids(mocks) == []
        mocks["run_evaluation"].assert_not_called()


@pytest.mark.unit
class TestOneEvaluationPerAssetPerBatch:

    def test_several_messages_for_one_asset_evaluate_it_once(self):
        event = _sns_event(_stream_message(change_source="upload",
                                           change_at="2026-03-01T12:00:00+00:00"),
                           _stream_message(change_source="assetEdit",
                                           change_at="2026-03-01T12:00:01+00:00"),
                           {"databaseId": DB, "assetId": ASSET})
        mocks = _run(event, database_item=AUTO_EVAL_DB,
                     compliance_record=_record_never_evaluated())
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")
        # The database row and the strongly consistent state row are read once for the batch.
        assert mocks["database"].get_item.call_count == 1
        assert mocks["get_compliance_record"].call_count == 1

    def test_a_stream_message_and_the_file_messages_of_one_asset_evaluate_it_once(self):
        event = _sns_event(
            _stream_message(change_source="upload", change_at="2026-03-01T12:00:05+00:00"),
            indexer_message(s3_event_record(f"{ASSET}/one.stl",
                                            event_time="2026-03-01T12:00:09.000Z")),
            indexer_message(s3_event_record(f"{ASSET}/two.stl",
                                            event_time="2026-03-01T12:00:09.500Z")))
        mocks = _run(event, database_item=AUTO_EVAL_DB,
                     compliance_record=_record_never_evaluated(),
                     asset_rows=[{"databaseId": DB, "assetId": ASSET}])
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")
        # The asset is evaluated before its file messages are judged, so no object is read.
        mocks["s3"].head_object.assert_not_called()

    def test_two_assets_in_one_batch_are_evaluated_once_each(self):
        event = _sns_event(_stream_message(asset_id=ASSET), _stream_message(asset_id=OTHER_ASSET),
                           _stream_message(asset_id=ASSET), _stream_message(asset_id=OTHER_ASSET))
        mocks = _run(event, database_item=AUTO_EVAL_DB,
                     compliance_record=_record_never_evaluated())
        evaluated = sorted(call.args[1] for call in mocks["run_evaluation"].call_args_list)
        assert evaluated == sorted([ASSET, OTHER_ASSET])
        # One database, read once for the whole batch.
        assert mocks["database"].get_item.call_count == 1

    def test_an_unbound_database_is_read_once_for_a_batch_of_its_assets(self):
        event = _sns_event(*[_stream_message(asset_id=f"x{index:08d}") for index in range(10)])
        mocks = _run(event, database_item={"databaseId": DB, "complianceAutoEval": False})
        mocks["run_evaluation"].assert_not_called()
        mocks["get_compliance_record"].assert_not_called()
        assert mocks["database"].get_item.call_count == 1

    def test_a_covered_message_does_not_keep_a_later_change_of_the_asset_from_evaluating(self):
        record = _record_never_evaluated(lastEvaluationId="eval-1",
                                         lastEvaluatedAt="2026-03-01T12:00:05+00:00")
        event = _sns_event(_stream_message(change_source="upload",
                                           change_at="2026-03-01T12:00:01+00:00"),
                           _stream_message(change_source="assetEdit",
                                           change_at="2026-03-01T12:00:09+00:00"))
        mocks = _run(event, database_item=AUTO_EVAL_DB, compliance_record=record)
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")


@pytest.mark.unit
class TestOnlyInfrastructureErrorsAreRetried:

    def test_a_failed_database_read_fails_only_its_record(self):
        def database_read(Key):
            if Key["databaseId"] == "db-broken":
                raise THROTTLED
            return {"Item": AUTO_EVAL_DB}

        event = _sns_event(_stream_message(database_id="db-broken"), _stream_message())
        mocks = _run(event, compliance_record=_record_never_evaluated(),
                     database_effect=database_read)
        assert _failed_ids(mocks) == ["q-1"]
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    @pytest.mark.parametrize("error", [THROTTLED, EndpointConnectionError(endpoint_url="https://x")],
                             ids=["service-error", "transport-error"])
    def test_an_evaluation_that_failed_on_an_aws_call_is_retried(self, error):
        mocks = _run(_sns_event(_stream_message()), database_item=AUTO_EVAL_DB,
                     compliance_record=_record_never_evaluated(), run_evaluation_effect=error)
        assert _failed_ids(mocks) == ["q-1"]

    @pytest.mark.parametrize("error", [ValueError("bad schema"), KeyError("rules"), CONDITION_FAILED],
                             ids=["value-error", "key-error", "condition-not-met"])
    def test_an_error_no_retry_can_fix_is_logged_and_not_retried(self, error):
        mocks = _run(_sns_event(_stream_message()), database_item=AUTO_EVAL_DB,
                     compliance_record=_record_never_evaluated(), run_evaluation_effect=error)
        assert _failed_ids(mocks) == []
        mocks["logger"].exception.assert_called()

    def test_a_rule_error_is_a_recorded_evaluation_not_a_retry(self):
        mocks = _run(_sns_event(_stream_message()), database_item=AUTO_EVAL_DB,
                     compliance_record=_record_never_evaluated(),
                     evaluation={"evaluationId": "eval-err", "verdict": "error",
                                 "error": "schema could not be read"})
        mocks["run_evaluation"].assert_called_once()
        assert _failed_ids(mocks) == []

    def test_a_failed_cascade_does_not_fail_the_record(self):
        """The evaluation has claimed the state row, so a redelivered record would find its change
        covered and open no cascade either: the failure is logged, not retried."""
        cascade_table = MagicMock(name="cascade_table")
        cascade_table.query.side_effect = THROTTLED
        mocks = _run(_sns_event(_stream_message()), database_item=AUTO_EVAL_DB,
                     compliance_record=_record_never_evaluated(),
                     children=[{"toAssetId": "child", "relationshipType": "parentChild"}],
                     cascade_table=cascade_table)
        mocks["run_evaluation"].assert_called_once()
        assert _failed_ids(mocks) == []
        mocks["logger"].exception.assert_called()

    def test_a_failed_asset_row_lookup_on_the_file_path_is_retried(self):
        event = _sns_event(indexer_message(s3_event_record(f"{ASSET}/one.stl")))
        mocks = _run(event, database_item=AUTO_EVAL_DB, compliance_record=_record_never_evaluated(),
                     asset_query_effect=THROTTLED)
        assert _failed_ids(mocks) == ["q-1"]
        mocks["run_evaluation"].assert_not_called()

    @pytest.mark.parametrize("error,expected", [
        (THROTTLED, True), (EndpointConnectionError(endpoint_url="https://x"), True),
        (CONDITION_FAILED, False), (ValueError("x"), False), (KeyError("x"), False)])
    def test_the_classification(self, error, expected):
        assert trigger.is_infrastructure_error(error) is expected

