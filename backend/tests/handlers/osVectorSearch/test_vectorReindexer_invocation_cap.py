# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""vectorReindexer: the invocation cap that bounds a run's self-invoke chain.

AWS Lambda's recursive-loop detection is turned off for the reindexer, because a long run continues itself
past the detection's limit of about 16 invocations. The run bounds the chain itself: the invocation that
reaches MAX_RUN_INVOCATIONS with work left does not continue; it raises ReindexContinuationFailed, so the
asynchronous invocation reaches the on-failure dead-letter queue with a resume payload. That payload keeps
the run's cursor and counts and starts the invocation count again, so resuming it continues the run.
"""

import json
from unittest.mock import MagicMock

import pytest

from tests.handlers.osVectorSearch.vectorsearch_support import FakeVectorStore, load_handler

K1 = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/a.glb#v1"}}
K2 = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/a.glb#v2"}}
K3 = {"databaseId:assetId": {"S": "db1:a2"}, "fileVersionKey": {"S": "/b.glb#v1"}}
CURSOR = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/a.glb#v2"}}
# The cap, written out so a test of the bound does not move with it.
CAP = 200


def _context(remaining_ms):
    context = MagicMock()
    context.get_remaining_time_in_millis.return_value = remaining_ms
    return context


def _continuation(invocations, clear_start_key=None, deleted=0):
    return {"operation": "clear", "continuation": {
        "runId": "abc123def456", "phase": "clear", "clearStartKey": clear_start_key, "startAfter": None,
        "chunk": 0, "enqueued": 0, "deleted": deleted, "invocations": invocations}}


@pytest.fixture
def reindexer():
    m = load_handler("vectorReindexer")
    # Two pages: the first leaves a cursor, so a short-on-time invocation has work left after it.
    m.vector_store = FakeVectorStore(scan_pages=[(None, ([K1, K2], CURSOR)), (CURSOR, ([K3], None))])
    m.lambda_client = MagicMock()
    m.sqs_client = MagicMock()
    return m


@pytest.mark.unit
class TestInvocationCap:
    def test_the_cap_is_200_invocations(self, reindexer):
        assert reindexer.MAX_RUN_INVOCATIONS == CAP

    def test_the_invocation_that_reaches_the_cap_with_work_left_stops_with_a_resume_payload(self, reindexer):
        # This continuation is the run's 200th invocation and runs out of time after the first page.
        with pytest.raises(reindexer.ReindexContinuationFailed) as excinfo:
            reindexer.lambda_handler(_continuation(CAP - 1), _context(60000))
        reindexer.lambda_client.invoke.assert_not_called()
        failure = json.loads(str(excinfo.value))
        assert failure["reindexRunId"] == "abc123def456" and failure["phase"] == "clear"
        assert failure["error"].startswith("ReindexHopLimitReached: the run reached 200 invocations")
        resumed = failure["resumePayload"]["continuation"]
        # The work this invocation did is kept, and the resumed run gets a fresh invocation budget.
        assert resumed["clearStartKey"] == CURSOR
        assert resumed["deleted"] == 2
        assert resumed["invocations"] == 0
        assert failure["resumePayload"]["operation"] == "clear"

    def test_below_the_cap_the_run_continues_itself(self, reindexer):
        # Control: the same short-on-time invocation one hop earlier self-invokes as before.
        body = json.loads(reindexer.lambda_handler(_continuation(CAP - 2), _context(60000))["body"])
        assert body["continued"] is True and body["invocations"] == CAP - 1
        payload = json.loads(reindexer.lambda_client.invoke.call_args.kwargs["Payload"])
        assert payload["continuation"]["invocations"] == CAP - 1

    def test_an_invocation_at_the_cap_that_finishes_its_work_completes(self, reindexer):
        body = json.loads(
            reindexer.lambda_handler(_continuation(CAP - 1, CURSOR, deleted=2), _context(600000))["body"])
        assert (body["phase"], body["continued"], body["deleted"]) == ("done", False, 3)
        reindexer.lambda_client.invoke.assert_not_called()

    def test_the_resume_payload_from_the_cap_continues_the_run(self, reindexer):
        with pytest.raises(reindexer.ReindexContinuationFailed) as excinfo:
            reindexer.lambda_handler(_continuation(CAP - 1), _context(60000))
        resume = json.loads(str(excinfo.value))["resumePayload"]
        body = json.loads(reindexer.lambda_handler(resume, _context(600000))["body"])
        assert body["reindexRunId"] == "abc123def456"
        assert body["phase"] == "done"
        assert body["deleted"] == 3
        assert body["invocations"] == 1
