# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""databaseService.hand_off_compliance_move: a database delete queues the move of its compliance
data on the compliance job queue, and a move it cannot queue goes to that queue's dead-letter
queue, where redriving it starts the move."""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.databases import databaseService

# The real hand-off is under test, so the package conftest leaves it in place.
USES_RETIREMENT_RECORD = True

QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/123456789012/ComplianceJobQueue"
DLQ_URL = "https://sqs.us-east-1.amazonaws.com/123456789012/ComplianceJobDLQ"
DATABASE_ID = "db1"


def _sqs_failing_on(*failing_urls):
    sqs = MagicMock(name="sqs")

    def send_message(QueueUrl=None, MessageBody=None):
        if QueueUrl in failing_urls:
            raise ClientError({"Error": {"Code": "InternalError", "Message": "unavailable"}},
                              "SendMessage")
        return {"MessageId": "m-1"}

    sqs.send_message.side_effect = send_message
    return sqs


def _hand_off(sqs):
    with patch.object(databaseService, "boto3") as boto3, \
            patch.object(databaseService, "logger") as logger:
        boto3.client.return_value = sqs
        handed_off = databaseService.hand_off_compliance_move(DATABASE_ID)
    sent = {(c.kwargs["QueueUrl"], json.dumps(json.loads(c.kwargs["MessageBody"]), sort_keys=True))
            for c in sqs.send_message.call_args_list}
    return handed_off, sent, logger


@pytest.fixture(autouse=True)
def _queue_urls(monkeypatch):
    monkeypatch.setenv("COMPLIANCE_JOB_QUEUE_URL", QUEUE_URL)
    monkeypatch.setenv("COMPLIANCE_JOB_DLQ_URL", DLQ_URL)


# The body the compliance job Lambda runs a deleted database's move from.
MOVE_MESSAGE = json.dumps({"vamsInternalAction": "moveDeletedDatabaseCompliance",
                           "databaseId": DATABASE_ID}, sort_keys=True)


@pytest.mark.unit
class TestTheComplianceMoveHandOff:

    def test_the_move_is_queued_on_the_job_queue(self):
        handed_off, sent, _ = _hand_off(_sqs_failing_on())
        assert handed_off is True
        assert (QUEUE_URL, MOVE_MESSAGE) in sent
        assert DLQ_URL not in {url for url, _ in sent}

    def test_a_move_the_job_queue_refuses_is_put_in_the_dead_letter_queue(self):
        handed_off, sent, logger = _hand_off(_sqs_failing_on(QUEUE_URL))
        assert handed_off is False
        assert (DLQ_URL, MOVE_MESSAGE) in sent
        assert any(DATABASE_ID in str(c.args[0]) and "dead-letter queue" in str(c.args[0])
                   for c in logger.error.call_args_list)

    def test_a_move_neither_queue_takes_is_logged_as_an_error(self):
        handed_off, _, logger = _hand_off(_sqs_failing_on(QUEUE_URL, DLQ_URL))
        assert handed_off is False
        assert any(DATABASE_ID in str(c.args[0]) and "was not queued" in str(c.args[0])
                   for c in logger.error.call_args_list)
