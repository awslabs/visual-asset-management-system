# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The vector search entrypoints under the REAL `audited_handler` (D102).

The decorator resolves the caller's claims before the handler body runs, and the claims lookup
normalizes the event in place (`common.auth.apiEvent.normalize_event`): an absent `pathParameters`
and `queryStringParameters` become `{}` and `vamsPathParametersDecoded` is set. The suites load the
handlers under the repo-wide mock decorator, which returns the handler unchanged, so they never saw
those keys; the reindexer's payload check refused every direct call and every continuation with
"unknown payload keys". Here each handler body is wrapped in the production decorator, whose claims
lookup is the production one, and its audit delivery is captured: the reindexer accepts a direct
payload, a continuation and a CloudFormation request, still refuses a key of the caller's own, and
the indexer and the system-workflow launcher read their SQS batches.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.customLogging import auditLogging as real_audit
from tests.handlers.osVectorSearch.vectorsearch_support import load_handler

try:  # The production claims lookup, under the package path the real decorator module uses.
    from backend.backend.handlers.auth import request_to_claims as real_request_to_claims
except Exception:  # pragma: no cover - the directory's import stubs differ by run
    real_request_to_claims = None
from backend.backend.common.auth.apiEvent import normalize_event


def _claims_lookup(event):
    """The production lookup when it imports here; otherwise its first step (the in-place
    normalization) and its answer for an event without an authorizer, which is what it does for
    these payloads."""
    if real_request_to_claims is not None:
        return real_request_to_claims(event)
    normalize_event(event)
    return {"tokens": [], "roles": [], "externalAttributes": [], "mfaEnabled": False}


@pytest.fixture
def real_decorator():
    with patch.object(real_audit, "request_to_claims", _claims_lookup), \
            patch.object(real_audit, "cloudwatch_logs", MagicMock(name="cloudwatch_logs")), \
            patch.object(real_audit, "audit_flush_client", MagicMock(name="audit_flush_client")):
        yield real_audit.audited_handler


def _context():
    context = MagicMock()
    context.get_remaining_time_in_millis.return_value = 600_000
    context.aws_request_id = "req-1"
    return context


@pytest.fixture
def reindexer(real_decorator):
    m = load_handler("vectorReindexer")
    m.vector_search_enabled = True
    m._log_reindex = MagicMock(name="_log_reindex")
    runs = []

    def run(params, context, raise_on_failure=False, trigger=None):
        runs.append((params, raise_on_failure, trigger))
        return {"statusCode": 200, "body": json.dumps({
            "reindexRunId": "run-1", "phase": "enqueue", "deleted": 0, "enqueued": 0,
            "continued": False})}
    m._run_reindex = run
    handler = real_decorator(m.lambda_handler)
    return m, handler, runs


@pytest.mark.unit
class TestTheReindexerUnderTheRealDecorator:

    def test_the_decorator_normalizes_the_event_in_place(self, real_decorator):
        """[control] Without this the cases below would prove nothing."""
        seen = {}

        @real_decorator
        def body(event, context):
            seen.update(event)
            return {"statusCode": 200, "body": "{}"}
        body({"operation": "enqueue"}, _context())
        assert {"pathParameters", "queryStringParameters", "vamsPathParametersDecoded"} <= set(seen)

    def test_a_direct_payload_is_accepted(self, reindexer):
        m, handler, runs = reindexer
        response = handler({"operation": "enqueue", "dryRun": True, "databaseId": "db1"}, _context())
        assert response["statusCode"] == 200, response
        (params, raise_on_failure, trigger), = runs
        assert (params["operation"], params["dryRun"], params["databaseId"]) == ("enqueue", True, "db1")
        assert (raise_on_failure, trigger) == (False, m.TRIGGER_DIRECT)

    def test_a_continuation_payload_is_accepted(self, reindexer):
        m, handler, runs = reindexer
        payload = m.continuation_payload(
            {"operation": "enqueue", "dryRun": False, "limit": None, "databaseId": None,
             "startAfter": None, "continuation": None},
            {"runId": "run-1", "phase": "enqueue", "chunk": 3, "enqueued": 200, "deleted": 0,
             "invocations": 2, "startAfter": "cursor"})
        response = handler(json.loads(json.dumps(payload)), _context())
        assert response["statusCode"] == 200, response
        (params, raise_on_failure, trigger), = runs
        assert params["continuation"]["runId"] == "run-1"
        assert (raise_on_failure, trigger) == (True, m.TRIGGER_CONTINUATION)

    def test_a_cloudformation_request_is_accepted(self, reindexer):
        m, handler, runs = reindexer
        event = {"RequestType": "Create", "ResponseURL": "https://cfn.example/response",
                 "StackId": "stack", "RequestId": "r1", "LogicalResourceId": "VectorReindex",
                 "ResourceType": "Custom::VectorReindex",
                 "ResourceProperties": {"ServiceToken": "arn:fn", "Operation": "enqueue",
                                        "DryRun": "true", "Timestamp": "1"}}
        response = handler(event, _context())
        assert response["Data"]["ReindexRunId"] == "run-1"
        (params, _raise, trigger), = runs
        assert (params["operation"], params["dryRun"], trigger) == ("enqueue", True,
                                                                    m.TRIGGER_CLOUDFORMATION)

    @pytest.mark.parametrize("extra", [{"pathParameters": {"databaseId": "db1"}}, {"surprise": 1}],
                             ids=["own-path-parameters", "unknown-key"])
    def test_a_key_of_the_callers_own_is_still_refused(self, reindexer, extra):
        _m, handler, runs = reindexer
        response = handler({"operation": "enqueue", **extra}, _context())
        assert response["statusCode"] == 400
        assert "unknown payload keys" in json.loads(response["body"])["error"]
        assert runs == []

    def test_the_dropped_keys_are_exactly_what_normalize_event_writes(self, reindexer):
        m, _handler, _runs = reindexer
        given = {"operation": "enqueue", "dryRun": False}
        normalized = dict(given)
        normalize_event(normalized)
        assert m.payload_as_given(normalized) == given
        assert set(normalized) - set(given) == set(m.NORMALIZE_EVENT_DEFAULTS)


@pytest.mark.unit
def test_the_indexer_reads_its_batch_under_the_real_decorator(real_decorator):
    m = load_handler("vectorIndexer")
    body = {"Type": "Notification", "Message": json.dumps({"unexpected": True})}
    event = {"Records": [{"messageId": "m1", "eventSource": "aws:sqs", "body": json.dumps(body)}]}
    response = real_decorator(m.lambda_handler)(event, _context())
    assert response["statusCode"] == 200
    assert response["batchItemFailures"] == []
    assert len(json.loads(response["body"])["results"]) == 1


@pytest.mark.unit
def test_the_launcher_reads_its_batch_under_the_real_decorator(real_decorator):
    m = load_handler("systemWorkflowLauncher")
    response = real_decorator(m.lambda_handler)({"Records": []}, _context())
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["message"] == "System workflow launches processed"
