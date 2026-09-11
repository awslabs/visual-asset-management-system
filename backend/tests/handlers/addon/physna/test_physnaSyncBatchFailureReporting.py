# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Partial-batch failure reporting for the two Physna sync consumers.

Guards FIX-015 (S2-BACKEND-024). Both handlers returned
``{"statusCode": 200, ...}`` unconditionally -- ``physnaAssetSync`` while
counting failures it never reported. A response carrying no
``batchItemFailures`` is a whole-batch SUCCESS to the event source mapping, so
SQS deleted the message: the file or asset was never re-synced to Physna and
nothing recorded the loss beyond one log line.

Three properties are asserted separately, because each fails on its own:

* only the records that failed are reported, so the batch still drains;
* the field is PRESENT and empty on a clean batch, not absent -- absence and
  "nothing failed" are the same value to the mapping, which is exactly why the
  original bug was invisible;
* an exception escaping the per-record loop reports the whole batch, since an
  error response without a failure report deletes every message in it.

The report only takes effect because the event source mappings set
``reportBatchItemFailures`` (asserted on the emitted CloudFormation in
``infra/test/t1PartitionIam.test.ts``). Without that property Lambda ignores
the field entirely and the fix would ship inert.
"""

import json

import pytest
from unittest.mock import MagicMock, patch

# Module-level import ensures the real `backend.backend.handlers` package is
# populated in sys.modules before the root conftest's autouse fixture runs,
# preventing it from stubbing the package with a MockModule.
from backend.backend.handlers.addon.physna import physnaAssetSync as _pas  # noqa: F401
from backend.backend.handlers.addon.physna import physnaFileSync as _pfs  # noqa: F401


def _s3_record(key, event_name="ObjectCreated:Put", bucket="vams-bucket"):
    return {
        "eventSource": "aws:s3",
        "eventName": event_name,
        "s3": {"bucket": {"name": bucket}, "object": {"key": key}},
    }


def _sqs_record(message_id, sns_message, body_type="Notification"):
    """One SQS record carrying an SNS notification, WITH a messageId.

    Real SQS records always carry one; the identifier is what the event source
    mapping redrives, so an event built without it cannot exercise reporting.
    """
    body = {"Type": body_type, "Message": json.dumps(sns_message)}
    return {
        "messageId": message_id,
        "eventSource": "aws:sqs",
        "body": json.dumps(body),
    }


def _file_event(*records):
    return {"Records": list(records)}


def _s3_sqs_record(message_id, *s3_records):
    return _sqs_record(
        message_id, {"Records": list(s3_records), "ASSET_BUCKET_NAME": "vams-bucket"}
    )


def _stream_sqs_record(message_id, composite, event_name="MODIFY"):
    """An SQS record wrapping a file-metadata DynamoDB stream record."""
    return _sqs_record(
        message_id,
        {
            "eventSource": "aws:dynamodb",
            "eventName": event_name,
            "dynamodb": {
                "Keys": {"databaseId:assetId:filePath": {"S": composite}},
                "NewImage": {"databaseId:assetId:filePath": {"S": composite}},
            },
        },
    )


def _asset_sqs_record(message_id, database_id, asset_id, event_name="MODIFY"):
    return _sqs_record(
        message_id,
        {
            "eventSource": "aws:dynamodb",
            "eventName": event_name,
            "dynamodb": {
                "Keys": {
                    "databaseId": {"S": database_id},
                    "assetId": {"S": asset_id},
                },
                "NewImage": {
                    "databaseId": {"S": database_id},
                    "assetId": {"S": asset_id},
                },
            },
        },
    )


def _identifiers(response):
    """The reported identifiers, with the entry shape checked.

    A malformed entry makes Lambda fail the ENTIRE batch, so the key name and a
    non-empty value are part of the contract, not cosmetics.
    """
    assert "batchItemFailures" in response, (
        "no batchItemFailures field: the event source mapping reads that as a "
        "whole-batch success and deletes every message"
    )
    failures = response["batchItemFailures"]
    assert isinstance(failures, list), failures
    for entry in failures:
        assert list(entry) == ["itemIdentifier"], f"unexpected entry shape: {entry}"
        assert entry["itemIdentifier"], "an empty identifier fails the whole batch"
    return [entry["itemIdentifier"] for entry in failures]


@pytest.mark.unit
class TestFileSyncBatchFailures:
    def test_only_the_failed_record_is_reported(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        event = _file_event(
            _s3_sqs_record("msg-good", _s3_record("xABC/good.step")),
            _s3_sqs_record("msg-bad", _s3_record("xABC/bad.step")),
        )

        def handle(record):
            if "bad" in record["s3"]["object"]["key"]:
                raise RuntimeError("Physna rejected the upload")
            return True

        with patch.object(physnaFileSync, "_handle_s3_record", side_effect=handle):
            response = physnaFileSync.lambda_handler(event, MagicMock())

        identifiers = _identifiers(response)
        assert "msg-bad" in identifiers
        # The record that synced cleanly must NOT be redriven, or the batch
        # never drains and every retry re-uploads it.
        assert "msg-good" not in identifiers
        assert response["statusCode"] == 200
        assert response["body"]["successful"] == 1

    def test_a_clean_batch_reports_an_empty_list_not_an_absent_field(self):
        """Positive control for every negative above.

        An absent field and "nothing failed" are indistinguishable to the
        mapping, so the empty-list case has to be asserted explicitly -- it is
        what proves the reporting path runs at all rather than the field simply
        never being written.
        """
        from backend.backend.handlers.addon.physna import physnaFileSync

        event = _file_event(_s3_sqs_record("msg-1", _s3_record("xABC/part.step")))
        with patch.object(physnaFileSync, "_handle_s3_record", return_value=True):
            response = physnaFileSync.lambda_handler(event, MagicMock())

        assert _identifiers(response) == []
        assert response["body"]["successful"] == 1

    def test_one_failed_inner_record_redrives_its_whole_sqs_message(self):
        """SQS redrives the MESSAGE, not the S3 record inside it.

        One SNS notification can carry several S3 records, so a message with any
        failed unit of work must be reported -- acking it on the strength of the
        siblings that succeeded loses the failed one. Re-processing the siblings
        costs round trips, never data: the upload path settles for a metadata
        refresh when Physna already holds the current S3 VersionId.
        """
        from backend.backend.handlers.addon.physna import physnaFileSync

        event = _file_event(
            _s3_sqs_record(
                "msg-mixed",
                _s3_record("xABC/good.step"),
                _s3_record("xABC/bad.step"),
            ),
            _s3_sqs_record("msg-clean", _s3_record("xABC/other.step")),
        )

        def handle(record):
            if "bad" in record["s3"]["object"]["key"]:
                raise RuntimeError("Physna rejected the upload")
            return True

        with patch.object(physnaFileSync, "_handle_s3_record", side_effect=handle):
            response = physnaFileSync.lambda_handler(event, MagicMock())

        identifiers = _identifiers(response)
        assert identifiers == ["msg-mixed"]
        # Both siblings were still attempted -- per-record isolation survives.
        assert response["body"]["successful"] == 2

    def test_a_falsy_handler_result_is_reported_not_acked(self):
        """``_handle_s3_record`` returns False for a record it could not use at
        all (no bucket or key). That is a failure to process, so the message
        goes to the DLQ after its receive count is exhausted rather than being
        deleted on the first delivery."""
        from backend.backend.handlers.addon.physna import physnaFileSync

        event = _file_event(_s3_sqs_record("msg-unusable", _s3_record("xABC/x.step")))
        with patch.object(physnaFileSync, "_handle_s3_record", return_value=False):
            response = physnaFileSync.lambda_handler(event, MagicMock())

        assert _identifiers(response) == ["msg-unusable"]

    def test_a_failed_metadata_stream_record_is_reported(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        event = _file_event(
            _stream_sqs_record("msg-stream-bad", "db-1:asset-1:/part.step"),
            _stream_sqs_record("msg-stream-ok", "db-1:asset-2:/other.step"),
        )

        def handle(record):
            composite = record["dynamodb"]["NewImage"][
                "databaseId:assetId:filePath"
            ]["S"]
            if "asset-1" in composite:
                raise RuntimeError("Physna metadata PATCH failed")
            return True

        with patch.object(
            physnaFileSync, "_handle_file_metadata_stream", side_effect=handle
        ):
            response = physnaFileSync.lambda_handler(event, MagicMock())

        identifiers = _identifiers(response)
        assert "msg-stream-bad" in identifiers
        assert "msg-stream-ok" not in identifiers

    def test_an_unparseable_envelope_is_reported_rather_than_deleted(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        event = {
            "Records": [
                {
                    "messageId": "msg-garbage",
                    "eventSource": "aws:sqs",
                    "body": "{not json",
                },
                _s3_sqs_record("msg-ok", _s3_record("xABC/part.step")),
            ]
        }
        with patch.object(physnaFileSync, "_handle_s3_record", return_value=True):
            response = physnaFileSync.lambda_handler(event, MagicMock())

        identifiers = _identifiers(response)
        assert identifiers == ["msg-garbage"]

    def test_a_non_notification_body_is_a_skip_not_a_failure(self):
        """An SNS control message (e.g. SubscriptionConfirmation) carries no
        VAMS work. Reporting it would redrive it until it reached the DLQ, so
        it stays a skip -- and the record that DID carry work still runs."""
        from backend.backend.handlers.addon.physna import physnaFileSync

        event = {
            "Records": [
                _sqs_record(
                    "msg-control", {"Records": []}, body_type="SubscriptionConfirmation"
                ),
                _s3_sqs_record("msg-work", _s3_record("xABC/part.step")),
            ]
        }
        with patch.object(physnaFileSync, "_handle_s3_record", return_value=True):
            response = physnaFileSync.lambda_handler(event, MagicMock())

        assert _identifiers(response) == []
        assert response["body"]["successful"] == 1

    def test_an_exception_escaping_the_record_loop_reports_the_whole_batch(self):
        """An error response with no failure report deletes every message in the
        batch. Reporting all of them redrives the batch instead; a Physna sync
        that runs twice re-checks the exact path and settles for a metadata
        refresh, so the redrive is the safe direction."""
        from backend.backend.handlers.addon.physna import physnaFileSync

        event = _file_event(
            _s3_sqs_record("msg-1", _s3_record("xABC/a.step")),
            _s3_sqs_record("msg-2", _s3_record("xABC/b.step")),
        )
        with patch.object(
            physnaFileSync, "_walk_records", side_effect=RuntimeError("boom")
        ):
            response = physnaFileSync.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 500
        assert set(_identifiers(response)) == {"msg-1", "msg-2"}

    def test_a_record_with_no_messageId_is_logged_rather_than_reported_blank(self):
        """An empty ``itemIdentifier`` makes Lambda fail the WHOLE batch, so a
        record that carries no messageId is skipped and logged as an error
        instead. Real SQS always supplies one; this is the guard against
        reporting a blank."""
        from backend.backend.handlers.addon.physna import physnaFileSync

        no_id = _s3_sqs_record("placeholder", _s3_record("xABC/a.step"))
        del no_id["messageId"]
        event = _file_event(no_id)

        with patch.object(
            physnaFileSync, "_handle_s3_record", side_effect=RuntimeError("boom")
        ), patch.object(physnaFileSync.logger, "error") as log_error:
            response = physnaFileSync.lambda_handler(event, MagicMock())

        assert _identifiers(response) == []
        assert log_error.call_count >= 1
        assert "messageId" in " ".join(str(c.args[0]) for c in log_error.call_args_list)


@pytest.mark.unit
class TestBatchItemIdentifierContract:
    def test_identifier_sources(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        assert physnaFileSync._batch_item_identifier({"messageId": "m-1"}) == "m-1"
        assert physnaFileSync._batch_item_identifier({"messageId": ""}) is None
        assert physnaFileSync._batch_item_identifier({"eventSource": "aws:sqs"}) is None
        assert physnaFileSync._batch_item_identifier("not a dict") is None

    def test_all_batch_item_failures_skips_unidentifiable_records(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        assert physnaFileSync._all_batch_item_failures(
            {"Records": [{"eventSource": "aws:sqs"}, {"messageId": "m-2"}]}
        ) == [{"itemIdentifier": "m-2"}]
        assert physnaFileSync._all_batch_item_failures(["not", "a", "dict"]) == []

    def test_a_record_is_reported_at_most_once(self):
        """A single SQS message with two failed inner records is one redrive,
        not two entries naming the same message."""
        from backend.backend.handlers.addon.physna import physnaFileSync

        failures = []
        record = {"messageId": "m-1"}
        physnaFileSync._add_batch_item_failure(failures, record)
        physnaFileSync._add_batch_item_failure(failures, record)
        assert failures == [{"itemIdentifier": "m-1"}]

    def test_a_direct_invocation_carries_no_failure_report(self):
        """The field is only meaningful for a batch, so a non-event-source
        response must not grow it."""
        from backend.backend.handlers.addon.physna import physnaFileSync

        response = physnaFileSync._with_batch_item_failures(
            {"statusCode": 200}, {"databaseId": "db-1"}, []
        )
        assert "batchItemFailures" not in response


@pytest.mark.unit
class TestAssetSyncBatchFailures:
    def test_only_the_failed_record_is_reported(self):
        from backend.backend.handlers.addon.physna import physnaAssetSync

        event = {
            "Records": [
                _asset_sqs_record("msg-bad", "db-1", "asset-bad"),
                _asset_sqs_record("msg-good", "db-1", "asset-good"),
            ]
        }

        def sync(database_id, asset_id, is_delete=False):
            if asset_id == "asset-bad":
                raise RuntimeError("Physna listing failed")

        with patch.object(
            physnaAssetSync, "_sync_asset_metadata_to_physna", side_effect=sync
        ), patch.object(physnaAssetSync, "_record_asset_sync"):
            response = physnaAssetSync.lambda_handler(event, MagicMock())

        identifiers = _identifiers(response)
        assert "msg-bad" in identifiers
        assert "msg-good" not in identifiers
        # The counted-but-unreported failure is exactly what FIX-015 was: the
        # count must still be there AND now be reported.
        assert response["body"] == {"successful": 1, "failed": 1}

    def test_a_clean_batch_reports_an_empty_list_not_an_absent_field(self):
        from backend.backend.handlers.addon.physna import physnaAssetSync

        event = {"Records": [_asset_sqs_record("msg-1", "db-1", "asset-1")]}
        with patch.object(
            physnaAssetSync, "_sync_asset_metadata_to_physna"
        ), patch.object(physnaAssetSync, "_record_asset_sync"):
            response = physnaAssetSync.lambda_handler(event, MagicMock())

        assert _identifiers(response) == []
        assert response["body"] == {"successful": 1, "failed": 0}

    def test_a_record_this_handler_does_not_act_on_is_a_skip(self):
        """File-level metadata rows are handled by physnaFileSync. Reporting
        them here would redrive every file change until it hit the DLQ."""
        from backend.backend.handlers.addon.physna import physnaAssetSync

        event = {
            "Records": [
                _sqs_record(
                    "msg-file-level",
                    {
                        "eventSource": "aws:dynamodb",
                        "eventName": "MODIFY",
                        "dynamodb": {
                            "Keys": {
                                "databaseId:assetId:filePath": {
                                    "S": "db-1:asset-1:/part.step"
                                }
                            },
                            "NewImage": {
                                "databaseId:assetId:filePath": {
                                    "S": "db-1:asset-1:/part.step"
                                }
                            },
                        },
                    },
                )
            ]
        }
        with patch.object(
            physnaAssetSync, "_sync_asset_metadata_to_physna"
        ) as sync, patch.object(physnaAssetSync, "_record_asset_sync"):
            response = physnaAssetSync.lambda_handler(event, MagicMock())

        assert _identifiers(response) == []
        assert sync.call_count == 0
        assert response["body"] == {"successful": 0, "failed": 0}

    def test_an_unparseable_envelope_is_reported_rather_than_deleted(self):
        from backend.backend.handlers.addon.physna import physnaAssetSync

        event = {
            "Records": [
                {
                    "messageId": "msg-garbage",
                    "eventSource": "aws:sqs",
                    "body": "{not json",
                },
                _asset_sqs_record("msg-ok", "db-1", "asset-1"),
            ]
        }
        with patch.object(
            physnaAssetSync, "_sync_asset_metadata_to_physna"
        ), patch.object(physnaAssetSync, "_record_asset_sync"):
            response = physnaAssetSync.lambda_handler(event, MagicMock())

        assert _identifiers(response) == ["msg-garbage"]
        assert response["body"] == {"successful": 1, "failed": 1}
