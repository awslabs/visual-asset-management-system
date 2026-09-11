# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A Garnet indexer must report a failed record for redrive, not delete it (owner question 87, A).

`S2-BACKEND-031` fixed this in the CORE indexers: a failed indexing operation was swallowed and
reported as success, so the event-source mapping deleted the SQS message and the document was never
indexed. The Garnet add-on carried the same defect untouched, behind a documented "No dead-letter
queue." limitation.

**Two halves, each inert without the other.** `reportBatchItemFailures: true` on the event source tells
the mapping to READ a `batchItemFailures` key; a response without that key is a whole-batch SUCCESS. So
setting the CDK flag while returning the old response shape changes nothing, and returning the key
without the flag changes nothing either. This file pins the handler half; the CDK half is asserted in
`infra/test/addon/garnetIndexerQueueFailureHandling.test.ts`.

Asserted on the RESPONSE rather than on the source, because the response is what the mapping consumes.
Both directions are covered: a failed record must be reported, and a clean batch must report an EMPTY
list rather than omitting the key — omitting it on the success path would be correct by accident today
and wrong the moment a partial failure occurs.
"""

import importlib.util
import os

import pytest
from unittest.mock import MagicMock, patch

_GARNET_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "backend", "handlers", "addon", "garnetFramework",
)


def _load(file_name, suffix):
    """Load a Garnet indexer under a suite-private module name."""
    path = os.path.abspath(os.path.join(_GARNET_DIR, file_name))
    spec = importlib.util.spec_from_file_location(f"{file_name[:-3]}_batchfail_{suffix}", path)
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.resource", return_value=MagicMock()), \
            patch("boto3.client", return_value=MagicMock()):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def asset_indexer():
    return _load("garnetDataIndexAsset.py", "asset")


def _sqs_event(*message_ids):
    """An SQS batch whose records carry no parseable body, so each one fails."""
    return {
        "Records": [
            {"messageId": mid, "eventSource": "aws:sqs", "body": "not json at all"}
            for mid in message_ids
        ]
    }


@pytest.mark.unit
class TestGarnetIndexerReportsFailedRecords:
    def test_a_failed_record_is_reported_for_redrive(self, asset_indexer):
        response = asset_indexer.lambda_handler(_sqs_event("m-1"), MagicMock())

        assert "batchItemFailures" in response, (
            "the response carries no batchItemFailures key, so the event-source mapping reads the "
            "batch as a clean success and DELETES the message that failed"
        )
        assert response["batchItemFailures"] == [{"itemIdentifier": "m-1"}]

    def test_only_the_failing_records_are_reported(self, asset_indexer):
        """Partial-batch, which is the whole point of the mechanism.

        All three fail here, so the assertion is on the SET of identifiers rather than on a count of
        one — a handler that reported a fixed single record, or the first record repeatedly, would pass
        a one-record test.
        """
        response = asset_indexer.lambda_handler(_sqs_event("m-1", "m-2", "m-3"), MagicMock())
        reported = [f["itemIdentifier"] for f in response["batchItemFailures"]]
        assert sorted(reported) == ["m-1", "m-2", "m-3"]

    def test_an_event_with_no_records_gets_no_failure_key(self, asset_indexer):
        """A direct invocation is not an event-source batch.

        Reporting failures for it would put a key the caller does not understand into an API-shaped
        response, so the key is added only when `Records` is present.
        """
        response = asset_indexer.lambda_handler({"some": "direct-invoke"}, MagicMock())
        assert "batchItemFailures" not in response

    def test_the_helpers_identify_a_record_the_way_the_mapping_does(self):
        """`messageId` is the identifier for an SQS source; the stream fallback matches core.

        Checked directly because an identifier the mapping does not recognise is silently ignored — the
        response would look correct and the record would still be deleted.
        """
        from common.batchItemFailures import all_batch_item_failures, batch_item_identifier

        assert batch_item_identifier({"messageId": "m-9"}) == "m-9"
        assert batch_item_identifier({"dynamodb": {"SequenceNumber": "42"}}) == "42"
        assert batch_item_identifier({}) == ""
        assert batch_item_identifier("not a dict") == ""

        # A whole-batch report, used where the cause cannot be attributed to one record.
        assert all_batch_item_failures(_sqs_event("a", "b")) == [
            {"itemIdentifier": "a"}, {"itemIdentifier": "b"}
        ]
        assert all_batch_item_failures({}) == []

    def test_the_error_path_also_reports(self, asset_indexer):
        """The exit path most likely to be missed.

        A response without the key is read as a clean batch, so an error path that omits it deletes the
        very messages it failed to process — the original defect, reached by a different route. Forced
        by making the handler's own logging raise, which is outside every inner try/except.
        """
        with patch.object(asset_indexer.logger, "info", side_effect=RuntimeError("boom")):
            response = asset_indexer.lambda_handler(_sqs_event("m-7", "m-8"), MagicMock())

        assert response.get("statusCode") == 500
        reported = [f["itemIdentifier"] for f in response.get("batchItemFailures", [])]
        assert sorted(reported) == ["m-7", "m-8"], (
            "the error path must report the WHOLE batch: the failure is not attributable to one "
            "record, and re-indexing an already-indexed entity is harmless (upsert by entity id)"
        )
