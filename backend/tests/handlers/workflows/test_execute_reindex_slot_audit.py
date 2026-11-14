# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""What a reindex launch writes to the audit log, through executeWorkflow's request outcome.

The reindex launcher looks at the slot rows and calls executeWorkflow only when one looks free; when another
launch claims it first, executeWorkflow answers 429 and the launcher sends the message again with a delay,
up to 2,000 times per file. That 429 is a wait, not an outcome: it writes no failed workflowExecute, so a
backfill that waits does not fill the audit log with failures of launches that later run. The launch that
claims a slot writes its workflowExecute record, and a launch refused for good (the per-file-version lock,
a 400) writes its failure once.

executeWorkflow is driven through the perInputFileVersion suite's harness, with the production audit
decorator, context and writer in place of the test tree's stand-in (as test_vectorSearch_audit does), and
with the production `log_errors` behind `models.common.general_error`, which writes the errors group's
record of a refused request with the request's action and outcome (D103: the slot wait wrote one there
per wait while the actions group stayed empty)."""

import pytest

from tests.common.test_audit_action_records import (  # noqa: F401 -- claims and clients are fixtures
    LAMBDA_CONTEXT,
    auditLogging as real_audit,
    claims,
    clients,
    group_name,
    parse,
)
from tests.handlers.workflows.test_execute_per_input_file_version_lock import KEY_F, _LockTable, _run, ewv2
from tests.handlers.workflows.test_execute_reindex_slots import SLOT, _cross_call


@pytest.fixture
def audited(monkeypatch):
    """executeWorkflow's entrypoint under the production decorator, with its record functions real."""
    decorated = real_audit.audited_handler(ewv2.lambda_handler)
    monkeypatch.setattr(ewv2, "lambda_handler", lambda event, context: decorated(event, LAMBDA_CONTEXT))
    monkeypatch.setattr(ewv2, "skip_route_outcome", real_audit.skip_route_outcome)
    monkeypatch.setattr(ewv2, "log_actions", real_audit.log_actions)
    monkeypatch.setitem(ewv2.general_error.__globals__, "log_errors", real_audit.log_errors)


def _records(clients, group_key="auditActions"):  # noqa: F811 -- the fixture of the same name
    group = group_name(group_key)
    return [parse(message)[1] for message in clients.flush.messages(group) + clients.module.messages(group)]


def _failed_anywhere(clients):  # noqa: F811
    """Every record with outcome failed, in any audit group."""
    return [parse(message)[1] for message in clients.flush.messages() + clients.module.messages()
            if parse(message)[1].get("outcome") == "failed"]


@pytest.mark.unit
class TestASlotWaitIsNoOutcome:
    def test_every_slot_held_answers_429_and_writes_no_record(self, audited, clients):  # noqa: F811
        table = _LockTable(held={SLOT[0]: "E-a", SLOT[1]: "E-b"})
        response, sfn, _log, _stop = _run(table, tokens=("SYSTEM_USER",),
                                          event=_cross_call(slots={"count": 3, "candidates": [0, 1]}))
        assert response["statusCode"] == 429
        sfn.start_execution.assert_not_called()
        assert _records(clients) == [], "a wait for a slot is not a failed launch"
        assert _records(clients, "auditErrors") == [], "nor an error"
        assert _failed_anywhere(clients) == []
        assert clients.flush.messages() + clients.module.messages() == []

    def test_the_launch_that_claims_a_slot_writes_one_workflow_execute(self, audited, clients):  # noqa: F811
        table = _LockTable()
        response, _sfn, _log, _stop = _run(table, tokens=("SYSTEM_USER",),
                                           event=_cross_call(slots={"count": 3, "candidates": [2]}))
        assert response["statusCode"] == 200
        records = _records(clients)
        assert [(r["action"], r["outcome"]) for r in records] == [("workflowExecute", "success")]
        assert records[0]["actor"]["authMethod"] == "system" and records[0]["actor"]["crossCall"] is True

    def test_a_launch_refused_by_the_file_version_lock_writes_its_failure(self, audited, clients):  # noqa: F811
        table = _LockTable(held={KEY_F: "E-running"})
        response, sfn, _log, _stop = _run(table, tokens=("SYSTEM_USER",),
                                          event=_cross_call(slots={"count": 3, "candidates": [0]}))
        assert response["statusCode"] == 400
        sfn.start_execution.assert_not_called()
        records = _records(clients)
        assert [(r["action"], r["outcome"], r["reason"]) for r in records] == [
            ("workflowExecute", "failed", "validation")]
        errors = _records(clients, "auditErrors")
        assert [(r["statusCode"], r["action"], r["outcome"]) for r in errors] == [
            (400, "workflowExecute", "failed")]


@pytest.mark.unit
class TestSkipRouteOutcome:
    """The marker itself, on the production module."""

    def test_a_marked_answer_writes_no_failed_record(self, clients):  # noqa: F811
        @real_audit.audited_handler
        def handler(handled, context):
            real_audit.skip_route_outcome(handled)
            return {"statusCode": 429}

        handler(_cross_call(slots={"count": 3}), LAMBDA_CONTEXT)
        assert clients.flush.messages() + clients.module.messages() == []

    def test_it_marks_only_the_request_it_was_given(self, clients):  # noqa: F811
        event = _cross_call(slots={"count": 3})

        @real_audit.audited_handler
        def handler(handled, context):
            real_audit.skip_route_outcome(dict(handled))  # a copy is another request
            return {"statusCode": 429}

        handler(event, LAMBDA_CONTEXT)
        assert [(r["action"], r["reason"]) for r in _records(clients)] == [("workflowExecute", "throttled")]

    def test_outside_a_request_it_does_nothing(self):
        assert real_audit.skip_route_outcome({"requestContext": {}}) is None
        assert real_audit.skip_route_outcome(None) is None
