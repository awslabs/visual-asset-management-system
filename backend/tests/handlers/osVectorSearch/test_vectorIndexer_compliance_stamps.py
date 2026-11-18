# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A compliance change stamp on the asset row changes no vector state and launches nothing.

The compliance trigger records metadata and link changes on the asset row (`metadataEdit`,
`linkChange`, or `workflowExecution` for a pipeline's own write-back), and the asset edit and
asset-version paths stamp `assetEdit` / `assetVersion` with the write they make. Each such write is
an asset-table MODIFY of the live row that the asset indexer topic relays to the vector indexer.
None of them may archive, unarchive or delete a vector item, read the asset row, or send a launch
of the system workflow: only an S3 record restoring a never-embedded file sends one
(`run_unarchive_file` from `handle_s3_record`).
"""

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.handlers.osVectorSearch.vectorsearch_support import FakeVectorStore, load_handler

VECTOR_INDEXER = (Path(__file__).resolve().parents[3] / "backend" / "handlers" / "osVectorSearch"
                  / "vectorIndexer.py")

STAMPS = {
    "metadataEdit": {}, "linkChange": {},
    "workflowExecution": {"lastChangeWorkflowExecutionId": {"S": "e1"}},
    "assetEdit": {"assetName": {"S": "Renamed pump"}},
    "assetVersion": {"lastAssetVersionAt": {"S": "2026-03-01T12:00:00+00:00"}},
}


@pytest.fixture
def indexer():
    m = load_handler("vectorIndexer")
    m.vector_store = FakeVectorStore()
    m.asset_storage_table = MagicMock()
    m.sqs_client = MagicMock()
    return m


def _stamp_record(module, source, database_key="db1"):
    """The relayed asset-table MODIFY a stamp raises (the asset table streams the new image)."""
    keys = {"databaseId": {"S": database_key}, "assetId": {"S": "a1"}}
    image = {**keys, "assetName": {"S": "Pump"}, "isDistributable": {"BOOL": True},
             "lastChangeSource": {"S": source}, "lastChangeAt": {"S": "2026-03-01T12:00:01+00:00"},
             "lastChangeRecordedAt": {"S": "2026-03-01T12:00:02.000001+00:00"}, **STAMPS[source]}
    return {"eventSource": "aws:dynamodb", "eventName": "MODIFY",
            "eventSourceARN": "arn:aws:dynamodb:us-east-1:1:table/" + module.asset_storage_table_name
                              + "/stream/x",
            "dynamodb": {"Keys": keys, "NewImage": image, "SequenceNumber": "7"}}


def _assert_untouched(module):
    assert module.vector_store.calls == []
    module.asset_storage_table.get_item.assert_not_called()
    module.sqs_client.send_message.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("source", sorted(STAMPS))
class TestAStampOnlyModifyChangesNothing:

    def test_on_the_live_row(self, indexer, source):
        outcome = indexer.handle_stream_record(_stamp_record(indexer, source))
        assert outcome.ok and outcome.action == "ignore"
        _assert_untouched(indexer)

    def test_on_an_archived_row(self, indexer, source):
        outcome = indexer.handle_stream_record(_stamp_record(indexer, source, "db1#deleted"))
        assert outcome.ok and outcome.action == "ignore"
        _assert_untouched(indexer)

    def test_delivered_through_the_asset_indexer_topic(self, indexer, source):
        body = {"Type": "Notification", "Message": json.dumps(_stamp_record(indexer, source))}
        event = {"Records": [{"messageId": "m1", "eventSource": "aws:sqs", "body": json.dumps(body)}]}
        context = MagicMock()
        context.get_remaining_time_in_millis.return_value = 60_000
        response = indexer.lambda_handler(event, context)
        assert response["batchItemFailures"] == []
        assert [r["action"] for r in json.loads(response["body"])["results"]] == ["ignore"]
        _assert_untouched(indexer)


def _callers(tree, name):
    """Names of the functions in `tree` whose body calls `name`."""
    found = set()
    for function in ast.walk(tree):
        if isinstance(function, ast.FunctionDef):
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and \
                        node.func.id == name:
                    found.add(function.name)
    return found


@pytest.mark.unit
def test_only_an_s3_restore_sends_a_system_workflow_launch():
    """The launch queue is written in one place, reached only from the S3 record path, so no
    stream record of any table can start a system workflow launch."""
    tree = ast.parse(VECTOR_INDEXER.read_text())
    senders = set()
    for function in ast.walk(tree):
        if isinstance(function, ast.FunctionDef):
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                        node.func.attr == "send_message" and any(
                            isinstance(k.value, ast.Name) and k.value.id == "workflow_launch_queue_url"
                            for k in node.keywords):
                    senders.add(function.name)
    assert senders == {"_launch_embedding"}
    assert _callers(tree, "_launch_embedding") == {"run_unarchive_file"}
    assert _callers(tree, "run_unarchive_file") == {"handle_s3_record"}
    assert "handle_s3_record" not in _callers(tree, "handle_stream_record")
