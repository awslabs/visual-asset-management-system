# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A cascade awaiting approval carries `approvalTimeoutAt` (24 hours after creation). Once that time
has passed it can no longer be approved: wherever the cascade service reads it — the queue listing,
a get, an approve or a reject — it is aborted with abortReason "approval timed out" and a
`cascade_timed_out` audit entry, and the listing no longer shows it. The abort is conditional on the
row still being pending with the same timeout, so concurrent readers abort it once.

The trigger's pending check is covered in test_complianceTrigger.py
(TestOnePendingCascadePerTriggerAsset). Run against a DynamoDB double built from the CDK table
definition, so the condition expressions are evaluated rather than asserted as strings."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance._harness import (
    ASSET, CASCADE_ID, DB, USER, body_of, claims_for, enforcer, rest_event,
)
from handlers.compliance import complianceCascadeService as svc
from handlers.compliance import complianceEvaluationStore as store

MOD = "handlers.compliance.complianceCascadeService"
STORE = "handlers.compliance.complianceEvaluationStore"

CASCADE_TABLE_DEFINITION = {
    "TableName": "complianceCascadeStorageTable",
    "KeySchema": [{"AttributeName": "cascadeId", "KeyType": "HASH"}],
    "AttributeDefinitions": [
        {"AttributeName": "cascadeId", "AttributeType": "S"},
        {"AttributeName": "state", "AttributeType": "S"},
        {"AttributeName": "createdAt", "AttributeType": "S"},
    ],
    "GlobalSecondaryIndexes": [{
        "IndexName": "StateIndex",
        "KeySchema": [{"AttributeName": "state", "KeyType": "HASH"},
                      {"AttributeName": "createdAt", "KeyType": "RANGE"}],
        "Projection": {"ProjectionType": "ALL"},
    }],
    "BillingMode": "PAY_PER_REQUEST",
}

OTHER_ID = "5b1e2c3d-0000-4000-8000-000000000002"
ID_PARAMS = {"cascadeId": CASCADE_ID}


def _iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


def _pending(cascade_id=CASCADE_ID, timeout=None, created="2026-10-20T10:00:00+00:00"):
    row = {"cascadeId": cascade_id, "state": "pending_approval", "triggeredByDatabaseId": DB,
           "triggeredByAssetId": ASSET, "createdAt": created, "requireApproval": True,
           "triggerReason": "Parent asset updated; downstream re-evaluation needed",
           "actor": "SYSTEM_USER", "nodes": "{}", "executionOrder": "[]"}
    if timeout is not None:
        row["approvalTimeoutAt"] = timeout
    return row


EXPIRED = _iso(timedelta(minutes=-1))
CURRENT = _iso(timedelta(hours=1))


def _run(event, *rows):
    """Call the handler as an allowed caller against a cascade table holding `rows`."""
    with mock_aws():
        # The handler's resource and its table are one, as in the Lambda: the conditional-check
        # exception it catches is that resource's client's class.
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        table = resource.create_table(**CASCADE_TABLE_DEFINITION)
        for row in rows:
            table.put_item(Item=row)
        lambda_client = MagicMock(name="lambda_client")
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer()), \
                patch(f"{MOD}.dynamodb", resource), \
                patch(f"{MOD}.cascade_table", table), \
                patch(f"{MOD}.lambda_client", lambda_client), \
                patch(f"{MOD}.cascade_executor_function_name", "vams-complianceCascadeExecutor"), \
                patch(f"{STORE}.all_database_ids", return_value=[DB]), \
                patch(f"{STORE}.write_audit") as write_audit:
            response = svc.lambda_handler(event, MagicMock())
        rows_after = {item["cascadeId"]: item for item in table.scan()["Items"]}
    return response, {"rows": rows_after, "audit": write_audit, "invoke": lambda_client.invoke}


def _audit_events(audit):
    return [(call.kwargs["event_type"], call.kwargs.get("cascade_id")) for call in audit.call_args_list]


def _timed_out(row):
    return (row["state"], row.get("abortReason")) == ("aborted", "approval timed out") \
        and "completedAt" in row


@pytest.mark.unit
class TestTheTimeoutIsRead:

    @pytest.mark.parametrize("timeout, expected", [
        (EXPIRED, True), (CURRENT, False), (None, False), ("not-a-date", False),
        ("2026-10-22T10:00:00", True),  # naive: read as UTC
    ])
    def test_a_pending_cascade_times_out_at_its_approval_timeout(self, timeout, expected):
        assert store.cascade_approval_timed_out(_pending(timeout=timeout)) is expected

    @pytest.mark.parametrize("state", ["executing", "completed", "aborted"])
    def test_a_cascade_that_left_pending_approval_never_times_out(self, state):
        assert store.cascade_approval_timed_out(dict(_pending(timeout=EXPIRED), state=state)) is False


@pytest.mark.unit
class TestTheQueue:

    def test_a_timed_out_cascade_is_aborted_and_left_out_of_the_listing(self):
        response, mocks = _run(rest_event("GET", "/compliance/cascades"),
                               _pending(timeout=EXPIRED),
                               _pending(OTHER_ID, timeout=CURRENT, created="2026-10-26T10:00:00+00:00"))
        assert response["statusCode"] == 200
        assert [c["cascadeId"] for c in body_of(response)["cascades"]] == [OTHER_ID]
        assert _timed_out(mocks["rows"][CASCADE_ID])
        assert mocks["rows"][OTHER_ID]["state"] == "pending_approval"
        assert _audit_events(mocks["audit"]) == [("cascade_timed_out", CASCADE_ID)]
        audit = mocks["audit"].call_args.kwargs
        assert (audit["actor"], audit["details"]["approvalTimeoutAt"]) == ("SYSTEM_USER", EXPIRED)

    def test_a_cascade_without_a_timeout_stays_listed(self):
        response, mocks = _run(rest_event("GET", "/compliance/cascades"), _pending())
        assert [c["cascadeId"] for c in body_of(response)["cascades"]] == [CASCADE_ID]
        mocks["audit"].assert_not_called()

    def test_getting_a_timed_out_cascade_returns_it_aborted(self):
        response, mocks = _run(rest_event("GET", f"/compliance/cascades/{CASCADE_ID}", ID_PARAMS),
                               _pending(timeout=EXPIRED))
        assert response["statusCode"] == 200
        assert _timed_out(body_of(response))
        assert _timed_out(mocks["rows"][CASCADE_ID])


@pytest.mark.unit
class TestApproveAndRejectAfterTheTimeout:

    @pytest.mark.parametrize("verb", ["approve", "reject"])
    def test_a_timed_out_cascade_is_refused_and_aborted(self, verb):
        response, mocks = _run(
            rest_event("POST", f"/compliance/cascades/{CASCADE_ID}/{verb}", ID_PARAMS, body={}),
            _pending(timeout=EXPIRED))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == svc.CASCADE_TIMED_OUT_MESSAGE
        row = mocks["rows"][CASCADE_ID]
        assert _timed_out(row)
        assert "approvedBy" not in row and "rejectedBy" not in row
        mocks["invoke"].assert_not_called()
        assert _audit_events(mocks["audit"]) == [("cascade_timed_out", CASCADE_ID)]

    def test_a_cascade_within_its_timeout_is_still_approved(self):
        response, mocks = _run(
            rest_event("POST", f"/compliance/cascades/{CASCADE_ID}/approve", ID_PARAMS, body={}),
            _pending(timeout=CURRENT))
        assert response["statusCode"] == 202, response
        assert mocks["rows"][CASCADE_ID]["state"] == "executing"
        mocks["invoke"].assert_called_once()

    def test_a_cascade_within_its_timeout_is_still_rejected_without_a_reason(self):
        response, mocks = _run(
            rest_event("POST", f"/compliance/cascades/{CASCADE_ID}/reject", ID_PARAMS, body={}),
            _pending(timeout=CURRENT))
        assert response["statusCode"] == 200, response
        row = mocks["rows"][CASCADE_ID]
        assert (row["state"], row["rejectionReason"]) == ("aborted", "rejected")

    @pytest.mark.parametrize("verb", ["approve", "reject"])
    def test_the_transition_itself_refuses_a_cascade_that_timed_out_after_it_was_read(self, verb):
        """The read-time check and the write are not one step: the update's own condition keeps a
        cascade whose timeout passed in between from being approved or rejected."""
        with patch(f"{STORE}.cascade_approval_timed_out", return_value=False):
            response, mocks = _run(
                rest_event("POST", f"/compliance/cascades/{CASCADE_ID}/{verb}", ID_PARAMS, body={}),
                _pending(timeout=EXPIRED))
        assert response["statusCode"] == 400
        assert mocks["rows"][CASCADE_ID]["state"] == "pending_approval"
        mocks["invoke"].assert_not_called()
        mocks["audit"].assert_not_called()


@pytest.mark.unit
class TestOneAbortPerTimedOutCascade:

    def test_concurrent_readers_abort_it_once_and_both_see_it_aborted(self):
        with mock_aws():
            table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
                **CASCADE_TABLE_DEFINITION)
            stale = _pending(timeout=EXPIRED)
            table.put_item(Item=stale)
            with patch(f"{STORE}.write_audit") as write_audit:
                first = store.expire_timed_out_cascade(table, dict(stale))
                # A second reader holding the same stale pending copy.
                second = store.expire_timed_out_cascade(table, dict(stale))
        assert _timed_out(first) and _timed_out(second)
        assert first["completedAt"] == second["completedAt"]
        assert write_audit.call_count == 1

    def test_a_cascade_approved_before_the_abort_is_left_executing(self):
        with mock_aws():
            table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
                **CASCADE_TABLE_DEFINITION)
            stale = _pending(timeout=EXPIRED)
            table.put_item(Item=dict(stale, state="executing"))
            with patch(f"{STORE}.write_audit") as write_audit:
                current = store.expire_timed_out_cascade(table, dict(stale))
            assert table.get_item(Key={"cascadeId": CASCADE_ID})["Item"]["state"] == "executing"
        assert current["state"] == "executing"
        write_audit.assert_not_called()
