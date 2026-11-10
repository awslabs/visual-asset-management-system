# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""vectorIndexer: the hop cap that bounds an asset- or file-wide rule's continuation chain.

AWS Lambda's recursive-loop detection is turned off for the indexer, because a rule over a large item
collection re-enqueues itself onto the queue the indexer consumes more often than the detection's limit of
about 16 hops. The chain is bounded here: every `vector.indexer.continue` message carries its hop count, a
hand-off from a message at MAX_CONTINUATION_HOPS goes to the indexer's dead-letter queue instead of its own
queue, with the rule, its identity and the start key unchanged and the hop count reset to 0, so redriving
the dead-letter queue resumes the rule where it stopped with a full budget.
"""

import json
from unittest.mock import MagicMock

import pytest

from tests.handlers.osVectorSearch.vectorsearch_support import FakeVectorStore, load_handler

KEY_PATH = "/models/part.glb"
START_KEY = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": f"{KEY_PATH}#v0"}}
NEXT_KEY = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": f"{KEY_PATH}#v1#t0000083456"}}
CONTINUE = "vector.indexer.continue"
# The cap, written out so a test of the bound does not move with it.
CAP = 200


def _budget():
    return 42_000


def _continuation(rule="deleteAsset", **fields):
    message = {"detailType": CONTINUE, "rule": rule, "pk": "db1:a1", "startKey": START_KEY}
    message.update(fields)
    return message


class ResumedHandOffStore(FakeVectorStore):
    """A store whose asset- and file-wide methods hand back NEXT_KEY on a resumed call too (the shared fake
    hands off only on a rule's first page), so a continuation can hand off again."""

    def _paged(self, method, count, start_key, time_remaining_fn):
        self.paging.append((method, start_key, time_remaining_fn))
        return count, NEXT_KEY


def _sent(indexer):
    """(queue url, parsed body) of the last message the indexer sent, after checking it sent one."""
    calls = indexer.sqs_client.send_message.call_args_list
    assert calls, "the indexer sent no message"
    kwargs = calls[-1].kwargs
    return kwargs["QueueUrl"], json.loads(kwargs["MessageBody"])


def _queues(indexer):
    """Every queue URL the indexer sent to."""
    return {call.kwargs["QueueUrl"] for call in indexer.sqs_client.send_message.call_args_list}


@pytest.fixture
def indexer():
    m = load_handler("vectorIndexer")
    m.vector_store = ResumedHandOffStore()
    m.sqs_client = MagicMock()
    m.s3_client = MagicMock()
    m.asset_storage_table = MagicMock()
    return m


@pytest.mark.unit
class TestHopCount:
    def test_the_cap_is_200_continuations(self, indexer):
        assert indexer.MAX_CONTINUATION_HOPS == CAP

    def test_a_continuation_hands_off_with_one_more_hop_to_its_own_queue(self, indexer):
        outcome = indexer.handle_continuation(_continuation(hops=5), _budget)
        assert outcome.ok and "continued" in outcome.detail
        queue, body = _sent(indexer)
        assert queue == indexer.vector_indexer_queue_url
        assert body["hops"] == 6
        assert body["startKey"] == NEXT_KEY

    def test_a_message_without_a_hop_count_resumes_as_the_first_hop(self, indexer):
        # A continuation written before the cap existed is still resumed, and its hand-off is hop 1.
        outcome = indexer.handle_continuation(_continuation(), _budget)
        assert outcome.ok and "continued" in outcome.detail
        queue, body = _sent(indexer)
        assert queue == indexer.vector_indexer_queue_url
        assert body["hops"] == 1

    def test_the_last_hop_below_the_cap_still_continues_on_its_own_queue(self, indexer):
        indexer.handle_continuation(_continuation(hops=CAP - 1), _budget)
        queue, body = _sent(indexer)
        assert queue == indexer.vector_indexer_queue_url
        assert body["hops"] == CAP
        assert "stoppedAtHopCap" not in body

    @pytest.mark.parametrize("hops", ["3", -1, True, 1.5])
    def test_an_invalid_hop_count_is_dropped(self, indexer, hops):
        outcome = indexer.handle_continuation(_continuation(hops=hops), _budget)
        assert outcome.ok and outcome.action == "drop"
        assert indexer.vector_store.calls == []
        indexer.sqs_client.send_message.assert_not_called()


CAPPED = [
    ("setArchivedForFile", {"keyPath": KEY_PATH, "archived": True}),
    ("deleteFile", {"keyPath": KEY_PATH}),
    ("setArchivedForAsset", {"archived": False}),
    ("deleteAsset", {}),
]


@pytest.mark.unit
class TestTheCap:
    @pytest.mark.parametrize("rule, fields", CAPPED, ids=[rule for rule, _ in CAPPED])
    def test_a_hand_off_past_the_cap_is_parked_in_the_dead_letter_queue(self, indexer, rule, fields):
        outcome = indexer.handle_continuation(_continuation(rule, hops=CAP, **fields), _budget)
        # The page this invocation did is done and the record itself succeeds; the rest is parked.
        assert outcome.ok
        assert "parked at the continuation cap" in outcome.detail
        queue, body = _sent(indexer)
        assert queue == indexer.vector_indexer_dlq_url
        # Nothing re-enters the indexer's own queue.
        assert _queues(indexer) == {indexer.vector_indexer_dlq_url}
        # Everything the rule needs to resume, from the store's next key, with a fresh hop budget.
        expected = {"detailType": CONTINUE, "rule": rule, "pk": "db1:a1", **fields, "startKey": NEXT_KEY,
                    "hops": 0, "stoppedAtHopCap": CAP}
        assert body == expected

    def test_a_parked_message_redriven_from_the_dead_letter_queue_resumes_the_rule(self, indexer):
        indexer.handle_continuation(_continuation("deleteFile", hops=CAP, keyPath=KEY_PATH), _budget)
        _, parked = _sent(indexer)
        indexer.sqs_client.send_message.reset_mock()
        before = len(indexer.vector_store.paging)
        assert before == 1
        outcome = indexer.handle_continuation(parked, _budget)
        assert outcome.ok and "continued" in outcome.detail
        # The store resumes from the parked start key, and the chain starts again at hop 1 on its own queue.
        assert indexer.vector_store.paging[before][0] == "delete_file"
        assert indexer.vector_store.paging[before][1] == NEXT_KEY
        queue, body = _sent(indexer)
        assert queue == indexer.vector_indexer_queue_url
        assert body["hops"] == 1

    def test_a_rule_that_completes_at_the_cap_sends_nothing(self, indexer):
        # Control: the cap applies to a hand-off only; a rule that finishes its last page parks nothing.
        indexer.vector_store = FakeVectorStore()
        outcome = indexer.handle_continuation(_continuation(hops=CAP), _budget)
        assert outcome.ok and "parked" not in outcome.detail
        indexer.sqs_client.send_message.assert_not_called()
