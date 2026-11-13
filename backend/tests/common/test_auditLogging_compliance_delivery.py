# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A compliance request's audit records are delivered once each under the request-flush delivery.

Inside the request audit context an allowed authorization record is buffered for the end-of-request
flush and a denial is written at once. A compliance request writes four kinds of record: the route
check (allowed), a denial for each database it decides and may not act on (`CasbinEnforcer.enforce`,
written as core's listings write them), the asset checks' denials batched at the end of the request
(`AssetAccess.flush_denials` through `log_authorization_denials`, D193), and its route's action
record (D196). Every record reaches its log group exactly once: none is lost to the buffer and none
is written by both the immediate path and the flush.

Every assertion is made on what the writer handed the CloudWatch Logs clients.
"""

import json

import pytest

from backend.backend.customLogging import auditLogging
from backend.tests.common.test_auditLogging_authorization_delivery import (  # noqa: F401 -- clients: a fixture
    CLAIMS, LAMBDA_CONTEXT, api_check, api_event, clients,
)


@pytest.fixture
def recording(clients):  # noqa: F811 -- the recording log clients of the delivery tests
    return clients


OTHER_DATABASES = ("db-other-1", "db-other-2", "db-other-3")
ASSET_DENIALS = ({"action": "GET", "obj": {"object__type": "asset", "databaseId": "db1", "assetId": "a-tag-1"}},
                 {"action": "GET", "obj": {"object__type": "asset", "databaseId": "db1", "assetId": "a-tag-2"}})


def _evaluation_denial(database_id):
    return {"action": "POST", "obj": {"object__type": "complianceEvaluation", "databaseId": database_id,
                                      "complianceState": ""}}


def _all_messages(recording, group):
    return recording.module.messages(group) + recording.flush.messages(group)


def _payload(message):
    """The JSON payload of one record: between the bracketed prefix and the event echo."""
    return json.loads(message[message.index("{"):message.index(" --- [event:")])


def _count(messages, needle):
    return sum(1 for message in messages if needle in message)


def _approve(recording, outcome="success"):
    event = api_event(path="/compliance/cascades/c-1/approve", method="POST")
    with auditLogging.audit_request(event, LAMBDA_CONTEXT):
        auditLogging.log_authorization_api(event, True, api_check(event))
        for database_id in OTHER_DATABASES:
            auditLogging.log_authorization(CLAIMS, False, _evaluation_denial(database_id))
        auditLogging.log_authorization_denials(CLAIMS, list(ASSET_DENIALS))
        if outcome == "success":
            auditLogging.log_action(event, "complianceCascadeApprove", cascadeId="c-1")
        else:
            auditLogging.log_action(event, "complianceCascadeApprove", outcome="denied", reason="forbidden",
                                    cascadeId="c-1")
        written_before_flush = len(recording.module.messages(recording.auth))
    return written_before_flush


@pytest.mark.unit
class TestAComplianceRequestsRecordsAreDeliveredOnceEach:

    def test_every_authorization_record_reaches_the_authorization_log_once(self, recording):
        _approve(recording)
        messages = _all_messages(recording, recording.auth)
        # The route check, one denial per other database, the two batched asset denials.
        assert len(messages) == 1 + len(OTHER_DATABASES) + len(ASSET_DENIALS)
        for database_id in OTHER_DATABASES:
            assert _count(messages, f'"databaseId": "{database_id}"') == 1, database_id
        for denial in ASSET_DENIALS:
            assert _count(messages, f'"assetId": "{denial["obj"]["assetId"]}"') == 1
        assert _count(messages, '"route__path": "/compliance/cascades/c-1/approve"') == 1

    def test_the_denials_are_written_at_once_and_the_allowed_record_in_the_flush(self, recording):
        written_before_flush = _approve(recording)
        # Written while the handler ran: the three per-database denials (the batch writer of the asset
        # denials may use either client; it is counted with the others below).
        assert written_before_flush >= len(OTHER_DATABASES)
        flushed = recording.flush.messages(recording.auth)
        assert _count(flushed, '"route__path": "/compliance/cascades/c-1/approve"') == 1
        assert not any(f'"databaseId": "{database_id}"' in message
                       for message in flushed for database_id in OTHER_DATABASES)

    def test_the_route_action_record_is_written_once(self, recording):
        _approve(recording)
        actions = _all_messages(recording, recording.actions)
        assert len(actions) == 1
        record = _payload(actions[0])
        assert (record["action"], record["outcome"], record["cascadeId"]) == (
            "complianceCascadeApprove", "success", "c-1")

    def test_a_denied_route_action_goes_to_the_authorization_log_once_and_not_to_actions(self, recording):
        _approve(recording, outcome="denied")
        assert _all_messages(recording, recording.actions) == []
        payloads = [_payload(message) for message in _all_messages(recording, recording.auth)]
        denied = [payload for payload in payloads if payload.get("action") == "complianceCascadeApprove"]
        assert len(payloads) == 1 + len(OTHER_DATABASES) + len(ASSET_DENIALS) + 1
        assert [payload["outcome"] for payload in denied] == ["denied"]
