# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The vector search handlers' audit records.

A natural-language search (POST /search/nlp) records the action `search` as keyword search does: the request
as asked and the number of hits returned, never the hits, a vector or a score. The vector reindexer records
each invocation as the system's `searchReindex`, with what it was asked, what it did and its run id. The
vector indexer and the system-workflow launcher record no action but open the audit context, and their
partial-batch failure reports come back through it unchanged.

The handlers import the test tree's no-op audit stand-in, so the records are read two ways: through a
recorder in place of `log_action`, and through the production audit module (its decorator, context and
writer, the record read back from the CloudWatch Logs client the way test_audit_action_records does).
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.common.test_audit_action_records import (  # noqa: F401 -- claims and clients are fixtures
    LAMBDA_CONTEXT,
    auditLogging as real_audit,
    claims,
    clients,
    group_name,
    parse,
)
from tests.handlers.osVectorSearch.test_vectorSearchService import FakeStore, item, rows, svc  # noqa: F401
from tests.handlers.osVectorSearch.vectorsearch_support import FakeVectorStore, load_handler

USER = "alice"


def _nlp_event(body):
    """A POST /search/nlp request as API Gateway delivers it, with the caller's verified claims."""
    return {
        "requestContext": {
            "requestId": "req-nlp-1",
            "http": {"method": "POST", "path": "/search/nlp", "sourceIp": "198.51.100.7"},
            "authorizer": {"lambda": {"vams:tokens": json.dumps([USER]), "vams:roles": '["searcher"]',
                                      "vams:mfaEnabled": "true",
                                      "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_pool"}},
        },
        "body": json.dumps(body),
    }


def _action_records(clients):  # noqa: F811 -- the fixture of the same name
    group = group_name("auditActions")
    return [parse(message)[1] for message in clients.flush.messages(group) + clients.module.messages(group)]


def _every_record(clients):  # noqa: F811
    return clients.flush.messages() + clients.module.messages()


# ---------------------------------------------------------------------------------------------------
# POST /search/nlp
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestNaturalLanguageSearchRecord:
    def test_the_request_and_the_number_of_hits_returned_are_recorded(self, svc, monkeypatch):  # noqa: F811
        recorder = MagicMock()
        monkeypatch.setattr(svc, "log_action", recorder)
        rows(svc, ("db1", "a1"), ("db1", "a2"))
        svc._store = MagicMock(return_value=FakeStore([item(asset="a1", path="secret-hit.glb"),
                                                       item(asset="a2", path="other.glb", distance=0.4)]))
        event = _nlp_event({"query": "pump housing", "size": 1, "fileExtensions": ["glb"]})
        response = svc.lambda_handler(event, None)
        assert response["statusCode"] == 200
        recorder.assert_called_once()
        assert recorder.call_args.args == (event, "search")
        kwargs = recorder.call_args.kwargs
        assert kwargs["item_count"] == 1, "the hits returned on the page, not the matches counted"
        assert kwargs["request_parameters"]["query"] == "pump housing"
        assert kwargs["request_parameters"]["size"] == 1
        assert kwargs["request_parameters"]["fileExtensions"] == ["glb"]
        assert "secret-hit" not in json.dumps(kwargs, default=str), "the hits are never recorded"

    def test_an_answer_with_no_accessible_database_records_no_hits(self, svc, monkeypatch):  # noqa: F811
        recorder = MagicMock()
        monkeypatch.setattr(svc, "log_action", recorder)
        svc.access.get_accessible_databases_with_count.return_value = ([], 0)
        response = svc.lambda_handler(_nlp_event({"query": "pump housing"}), None)
        assert response["statusCode"] == 200
        recorder.assert_called_once()
        assert recorder.call_args.kwargs["item_count"] == 0

    @pytest.mark.parametrize("body", [{"query": ""}, {"query": "pump", "size": 0}])
    def test_a_refused_request_records_no_success(self, svc, monkeypatch, body):  # noqa: F811
        recorder = MagicMock()
        monkeypatch.setattr(svc, "log_action", recorder)
        assert svc.lambda_handler(_nlp_event(body), None)["statusCode"] == 400
        recorder.assert_not_called()


@pytest.mark.unit
class TestNaturalLanguageSearchRecordWritten:
    """Through the production decorator and writer: one record per request, in the record contract."""

    def _handler(self, svc, monkeypatch):  # noqa: F811
        monkeypatch.setattr(svc, "log_action", real_audit.log_action)
        return real_audit.audited_handler(svc.lambda_handler)

    def test_a_search_writes_one_search_record_with_no_hits_or_vector(self, svc, clients, monkeypatch):  # noqa: F811
        handler = self._handler(svc, monkeypatch)
        rows(svc, ("db1", "a1"))
        svc._store = MagicMock(return_value=FakeStore([item(asset="a1", path="secret-hit.glb")]))
        assert handler(_nlp_event({"query": "pump housing"}), LAMBDA_CONTEXT)["statusCode"] == 200
        records = _action_records(clients)
        assert len(records) == 1, "one record per request"
        record = records[0]
        assert record["action"] == "search" and record["outcome"] == "success"
        assert record["itemCount"] == 1
        assert record["requestParameters"]["query"] == "pump housing"
        assert record["request"]["route"] == "/search/nlp" and record["request"]["method"] == "POST"
        assert record["actor"]["userId"] == USER
        assert "changes" not in record and "previous" not in record
        written = "".join(_every_record(clients))
        assert "secret-hit" not in written and "distance" not in written

    @pytest.mark.parametrize("body, reason", [
        ({"query": ""}, "validation"),
        ({"query": "pump", "size": 0}, "validation"),
    ])
    def test_a_refused_request_writes_a_failed_search(self, svc, clients, monkeypatch, body, reason):  # noqa: F811
        handler = self._handler(svc, monkeypatch)
        assert handler(_nlp_event(body), LAMBDA_CONTEXT)["statusCode"] == 400
        records = _action_records(clients)
        assert [(r["action"], r["outcome"], r["reason"]) for r in records] == [("search", "failed", reason)]

    def test_a_search_while_the_index_builds_writes_a_failed_search(self, svc, clients, monkeypatch):  # noqa: F811
        handler = self._handler(svc, monkeypatch)
        store = MagicMock()
        store.search.side_effect = svc.VectorIndexNotReady("building")
        svc._store = MagicMock(return_value=store)
        assert handler(_nlp_event({"query": "pump"}), LAMBDA_CONTEXT)["statusCode"] == 503
        records = _action_records(clients)
        assert [(r["action"], r["outcome"], r["reason"]) for r in records] == [("search", "failed", "internal")]

    def test_a_route_denial_writes_no_search_record(self, svc, clients, monkeypatch):  # noqa: F811
        handler = self._handler(svc, monkeypatch)
        svc.enforcer.enforceAPI.return_value = False
        assert handler(_nlp_event({"query": "pump"}), LAMBDA_CONTEXT)["statusCode"] == 403
        assert _action_records(clients) == []


# ---------------------------------------------------------------------------------------------------
# The vector reindexer: searchReindex, as the system
# ---------------------------------------------------------------------------------------------------

K1 = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/a.glb#v1"}}
K2 = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/a.glb#v2"}}
CURSOR = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/a.glb#v2"}}


def _context(remaining_ms=600000):
    context = MagicMock()
    context.get_remaining_time_in_millis.return_value = remaining_ms
    return context


def _cfn_event(request_type="Create", properties=None):
    return {
        "RequestType": request_type,
        "ResponseURL": "https://cloudformation-custom-resource-response.example/presigned-secret",
        "StackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/vams/guid",
        "RequestId": "req-1",
        "LogicalResourceId": "VectorReindexTrigger",
        "ResourceType": "AWS::CloudFormation::CustomResource",
        "ResourceProperties": {"ServiceToken": "arn:aws:lambda:us-east-1:123456789012:function:provider",
                               "Operation": "clear", "Timestamp": "1700000000000", **(properties or {})},
    }


@pytest.fixture
def reindexer():
    m = load_handler("vectorReindexer")
    m.vector_store = FakeVectorStore(scan_pages=[(None, ([K1, K2], None))])
    m.lambda_client = MagicMock()
    m.sqs_client = MagicMock()
    m.sqs_client.send_message_batch.return_value = {"Successful": [], "Failed": []}
    m.workflow_storage_table_v2 = MagicMock()
    m.workflow_storage_table_v2.get_item.return_value = {"Item": {
        "databaseId": "GLOBAL", "workflowId": "system-genai-metadata",
        "specifiedPipelines": [], "systemConfig": {"inputFileFilters": {"allow": ["*.glb"], "exclude": []}}}}
    m.pipeline_storage_table_v2 = MagicMock()
    m.enumerate_latest_live_files = MagicMock(return_value=([], None))
    return m


def _recording(reindexer, monkeypatch, event):
    """A recorder in place of log_action, and an invocation context opened for ``event``."""
    recorder = MagicMock()
    monkeypatch.setattr(reindexer, "log_action", recorder)
    monkeypatch.setattr(reindexer, "current_audit_context", lambda: SimpleNamespace(event=event))
    return recorder


@pytest.mark.unit
class TestReindexRecord:
    def test_a_direct_run_records_what_it_was_asked_and_what_it_did(self, reindexer, monkeypatch):
        event = {"operation": "clear", "databaseId": "db1"}
        recorder = _recording(reindexer, monkeypatch, event)
        body = json.loads(reindexer.lambda_handler(event, _context())["body"])
        recorder.assert_called_once()
        assert recorder.call_args.args == (event, "searchReindex")
        kwargs = recorder.call_args.kwargs
        assert kwargs["outcome"] == "success" and kwargs["reason"] is None
        assert kwargs["item_count"] == 2 and kwargs["database_id"] == "db1"
        assert kwargs["details"] == {
            "trigger": "direct", "operation": "clear", "dryRun": False, "scope": "database",
            "reindexRunId": body["reindexRunId"], "invocation": 1, "phase": "done", "continued": False,
            "deleted": 2, "enqueued": 0}

    def test_an_invocation_that_continues_and_its_continuation_share_the_run_id(self, reindexer, monkeypatch):
        reindexer.vector_store = FakeVectorStore(scan_pages=[(None, ([K1], CURSOR)), (CURSOR, ([K2], None))])
        first = {"operation": "clear", "dryRun": True, "limit": 5}
        recorder = _recording(reindexer, monkeypatch, first)
        reindexer.lambda_handler(first, _context(remaining_ms=30000))
        continuation = json.loads(reindexer.lambda_client.invoke.call_args.kwargs["Payload"])
        monkeypatch.setattr(reindexer, "current_audit_context", lambda: SimpleNamespace(event=continuation))
        reindexer.lambda_handler(continuation, _context())
        (first_call, second_call) = recorder.call_args_list
        first_details, second_details = first_call.kwargs["details"], second_call.kwargs["details"]
        assert first_call.args[0] is first and second_call.args[0] is continuation
        assert first_details["reindexRunId"] == second_details["reindexRunId"]
        assert (first_details["trigger"], first_details["invocation"], first_details["continued"]) == (
            "direct", 1, True)
        assert (second_details["trigger"], second_details["invocation"], second_details["continued"]) == (
            "continuation", 2, False)
        assert first_details["dryRun"] is True and first_details["limit"] == 5
        assert first_details["scope"] == "all" and first_call.kwargs["database_id"] is None
        # item_count is what each invocation did; deleted is the run's total so far (a dry run counts).
        assert [c.kwargs["item_count"] for c in recorder.call_args_list] == [1, 1]
        assert [c.kwargs["details"]["deleted"] for c in recorder.call_args_list] == [1, 2]

    def test_the_cloudformation_resource_records_its_run(self, reindexer, monkeypatch):
        event = _cfn_event("Create")
        recorder = _recording(reindexer, monkeypatch, event)
        response = reindexer.lambda_handler(event, _context())
        recorder.assert_called_once()
        details = recorder.call_args.kwargs["details"]
        assert details["trigger"] == "cloudFormation" and details["operation"] == "clear"
        assert details["reindexRunId"] == response["Data"]["ReindexRunId"]
        assert "presigned-secret" not in json.dumps(recorder.call_args.kwargs, default=str)

    def test_a_cloudformation_delete_does_nothing_and_records_nothing(self, reindexer, monkeypatch):
        event = _cfn_event("Delete")
        recorder = _recording(reindexer, monkeypatch, event)
        reindexer.lambda_handler(event, _context())
        recorder.assert_not_called()

    def test_a_failed_first_invocation_records_a_failure_without_the_error_text(self, reindexer, monkeypatch):
        event = {"operation": "enqueue"}
        recorder = _recording(reindexer, monkeypatch, event)
        reindexer.enumerate_latest_live_files = MagicMock(side_effect=RuntimeError("bucket-secret-name gone"))
        assert reindexer.lambda_handler(event, _context())["statusCode"] == 500
        kwargs = recorder.call_args.kwargs
        assert kwargs["outcome"] == "failed" and kwargs["reason"] == "internal"
        assert kwargs["details"]["phase"] == "enqueue" and kwargs["details"]["continued"] is False
        assert "bucket-secret-name" not in json.dumps(kwargs, default=str)

    def test_a_failed_continuation_records_its_failure_and_still_raises(self, reindexer, monkeypatch):
        event = {"operation": "enqueue", "continuation": {"runId": "run-7", "phase": "enqueue", "invocations": 3}}
        recorder = _recording(reindexer, monkeypatch, event)
        reindexer.enumerate_latest_live_files = MagicMock(side_effect=RuntimeError("boom"))
        with pytest.raises(reindexer.ReindexContinuationFailed):
            reindexer.lambda_handler(event, _context())
        details = recorder.call_args.kwargs["details"]
        assert recorder.call_args.kwargs["outcome"] == "failed"
        assert (details["trigger"], details["reindexRunId"], details["invocation"]) == ("continuation", "run-7", 4)

    @pytest.mark.parametrize("event, trigger", [
        ({"operation": "purge"}, "direct"),
        ({"operation": "clear", "continuation": {"bogus": 1}}, "continuation"),
    ])
    def test_a_refused_payload_records_a_validation_failure(self, reindexer, monkeypatch, event, trigger):
        recorder = _recording(reindexer, monkeypatch, event)
        assert reindexer.lambda_handler(event, _context())["statusCode"] == 400
        kwargs = recorder.call_args.kwargs
        assert kwargs["outcome"] == "failed" and kwargs["reason"] == "validation"
        assert kwargs["details"] == {"trigger": trigger} and kwargs["item_count"] is None

    def test_a_refused_cloudformation_request_records_a_validation_failure(self, reindexer, monkeypatch):
        event = _cfn_event("Create", {"Operation": "rebuild"})
        recorder = _recording(reindexer, monkeypatch, event)
        with pytest.raises(ValueError):
            reindexer.lambda_handler(event, _context())
        assert recorder.call_args.kwargs["reason"] == "validation"
        assert recorder.call_args.kwargs["details"] == {"trigger": "cloudFormation"}

    def test_no_record_is_written_outside_an_invocation(self, reindexer, monkeypatch):
        recorder = MagicMock()
        monkeypatch.setattr(reindexer, "log_action", recorder)
        monkeypatch.setattr(reindexer, "current_audit_context", lambda: None)
        reindexer.lambda_handler({"operation": "clear"}, _context())
        recorder.assert_not_called()


@pytest.mark.unit
class TestReindexRecordWritten:
    """Through the production decorator, context and writer."""

    def _handler(self, reindexer, monkeypatch):
        monkeypatch.setattr(reindexer, "log_action", real_audit.log_action)
        monkeypatch.setattr(reindexer, "current_audit_context", real_audit.current_audit_context)
        return real_audit.audited_handler(reindexer.lambda_handler)

    @pytest.mark.parametrize("event", [{"operation": "clear"}, _cfn_event("Create")])
    def test_each_invocation_writes_one_system_record(self, reindexer, clients, monkeypatch, event):  # noqa: F811
        self._handler(reindexer, monkeypatch)(event, LAMBDA_CONTEXT)
        records = _action_records(clients)
        assert len(records) == 1
        record = records[0]
        assert record["action"] == "searchReindex" and record["outcome"] == "success"
        assert record["actor"] == {"userId": "SYSTEM_USER", "authMethod": "system"}
        assert record["itemCount"] == 2 and record["details"]["deleted"] == 2
        assert "presigned-secret" not in "".join(_every_record(clients))

    def test_a_failed_continuation_writes_one_failed_record(self, reindexer, clients, monkeypatch):  # noqa: F811
        reindexer.enumerate_latest_live_files = MagicMock(side_effect=RuntimeError("boom"))
        event = {"operation": "enqueue", "continuation": {"runId": "run-7", "phase": "enqueue"}}
        with pytest.raises(reindexer.ReindexContinuationFailed):
            self._handler(reindexer, monkeypatch)(event, _context())
        records = _action_records(clients)
        assert [(r["action"], r["outcome"], r["reason"]) for r in records] == [
            ("searchReindex", "failed", "internal")]
        assert "boom" not in "".join(_every_record(clients))


# ---------------------------------------------------------------------------------------------------
# The vector indexer and the system-workflow launcher: the context only
# ---------------------------------------------------------------------------------------------------

def _sqs_record(message_id, body):
    return {"messageId": message_id, "eventSource": "aws:sqs", "body": json.dumps(body)}


@pytest.mark.unit
class TestQueueHandlersThroughTheContext:
    def test_the_launchers_batch_failures_come_back_unchanged(self, clients, monkeypatch):  # noqa: F811
        launcher = load_handler("systemWorkflowLauncher")
        message = {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/part.glb", "versionId": "",
                   "reindexRunId": "abc123def456", "chunk": 0}
        outcomes = {"m-ok": "launched", "m-bad": RuntimeError("executeWorkflow returned status 500")}

        def launch(parsed):
            outcome = outcomes[parsed["marker"]]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(launcher, "launch", launch)
        event = {"Records": [_sqs_record("m-ok", dict(message, marker="m-ok")),
                             _sqs_record("m-bad", dict(message, marker="m-bad"))]}
        bare = launcher.lambda_handler(event, LAMBDA_CONTEXT)
        through = real_audit.audited_handler(launcher.lambda_handler)(event, LAMBDA_CONTEXT)
        assert through == bare
        assert through["batchItemFailures"] == [{"itemIdentifier": "m-bad"}]
        assert _every_record(clients) == [], "the launcher records no action of its own"

    def test_the_launchers_error_answer_reports_every_record(self, clients, monkeypatch):  # noqa: F811
        launcher = load_handler("systemWorkflowLauncher")
        event = {"Records": [_sqs_record("m-1", {}), _sqs_record("m-2", {})]}
        monkeypatch.setattr(launcher, "batch_item_identifier", MagicMock(side_effect=RuntimeError("bad batch")))
        through = real_audit.audited_handler(launcher.lambda_handler)(event, LAMBDA_CONTEXT)
        assert through["statusCode"] == 500
        assert through["batchItemFailures"] == [{"itemIdentifier": "m-1"}, {"itemIdentifier": "m-2"}]
        assert _every_record(clients) == []

    def test_the_indexers_batch_failures_come_back_unchanged(self, clients, monkeypatch):  # noqa: F811
        indexer = load_handler("vectorIndexer")

        def dispatch(record, time_left):
            ok = record["messageId"] != "m-bad"
            return [indexer.Outcome(ok, "ignore" if ok else "error", "")]

        monkeypatch.setattr(indexer, "_dispatch_sqs_record", dispatch)
        event = {"Records": [_sqs_record("m-ok", {}), _sqs_record("m-bad", {})]}
        bare = indexer.lambda_handler(event, LAMBDA_CONTEXT)
        through = real_audit.audited_handler(indexer.lambda_handler)(event, LAMBDA_CONTEXT)
        assert through == bare
        assert through["batchItemFailures"] == [{"itemIdentifier": "m-bad"}]
        assert _every_record(clients) == []

    @pytest.mark.parametrize("module_name", ["vectorIndexer", "systemWorkflowLauncher", "vectorReindexer",
                                             "vectorSearchService"])
    def test_each_entrypoint_is_decorated(self, module_name):
        import ast
        import os
        from tests.handlers.osVectorSearch.vectorsearch_support import _HANDLERS
        with open(os.path.join(_HANDLERS, f"{module_name}.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        entry = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "lambda_handler")
        assert [getattr(d, "id", None) for d in entry.decorator_list] == ["audited_handler"]
