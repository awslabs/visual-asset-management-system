# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`log_authorization_denials` writes the denials of one caller's checks in one batch.

The compliance services check each archived asset a listing meets with the asset GET the asset
routes make (`common/compliance/assetAccess`). `CasbinEnforcer.enforce` writes one CloudWatch
record per denial, a PutLogEvents round trip each; for a listing that refuses many archived assets
that is one round trip per asset. The checks run on the engine instead and their denials go out
here: the same records `log_authorization` writes, through `_write_batch_to_cloudwatch`, which
chunks them within the PutLogEvents limits.
"""

import json
import os

from unittest.mock import patch

os.environ.setdefault("AUDIT_LOG_AUTHORIZATION", "test-auditAuthorization")

from backend.backend.customLogging import auditLogging  # noqa: E402


def test_the_denials_go_out_in_one_batch_write():
    with patch.object(auditLogging, "_audit_log_group", return_value="authz-group"), \
            patch.object(auditLogging, "_write_batch_to_cloudwatch") as write:
        auditLogging.log_authorization_denials(
            {"tokens": ["u1"], "roles": ["r"], "mfaEnabled": True},
            [{"action": "GET", "obj": {"assetId": f"a{index}"}} for index in range(3)])
    write.assert_called_once()
    log_group, messages, event = write.call_args.args
    assert log_group == "authz-group"
    assert len(messages) == 3
    assert all(message.startswith("[AUTHORIZATION][authorized: False]") for message in messages)
    assert all(f'"a{index}"' in messages[index] for index in range(3))
    assert event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"] == "u1"


def test_each_record_is_the_one_log_authorization_writes():
    claims = {"tokens": ["u1"], "roles": ["r"], "mfaEnabled": False}
    denial = {"action": "GET", "obj": {"assetId": "a1", "object__type": "asset"}}
    with patch.object(auditLogging, "_audit_log_group", return_value="authz-group"), \
            patch.object(auditLogging, "_write_batch_to_cloudwatch") as batch, \
            patch.object(auditLogging, "_write_to_cloudwatch") as single:
        auditLogging.log_authorization_denials(claims, [denial])
        auditLogging.log_authorization(claims, False, denial)
    assert batch.call_args.args[1] == [single.call_args.args[1]]


def test_nothing_is_written_for_no_denial_and_a_failure_is_silent():
    with patch.object(auditLogging, "_audit_log_group", return_value="authz-group"), \
            patch.object(auditLogging, "_write_batch_to_cloudwatch") as write:
        auditLogging.log_authorization_denials({"tokens": ["u1"]}, [])
        write.assert_not_called()
        write.side_effect = RuntimeError("down")
        auditLogging.log_authorization_denials({"tokens": ["u1"]}, [{"action": "GET"}])
    with patch.object(auditLogging, "_audit_log_group", return_value=None), \
            patch.object(auditLogging, "_write_batch_to_cloudwatch") as write:
        auditLogging.log_authorization_denials({"tokens": ["u1"]}, [{"action": "GET"}])
        write.assert_not_called()


def _fallback_lines(capsys):
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    return ([line for line in lines if line.get("auditFallback") is True],
            [line for line in lines if "_aws" in line])


def test_an_unresolved_log_group_keeps_every_denial(capsys):
    """Like every audit record (#1420), a denial whose log group does not resolve is written to the
    function's own log as undelivered and counted, never dropped."""
    denials = [{"action": "GET", "obj": {"assetId": f"kept-{index}"}} for index in range(3)]
    with patch.object(auditLogging, "_audit_log_group", return_value=None), \
            patch.object(auditLogging, "_write_batch_to_cloudwatch") as write:
        auditLogging.log_authorization_denials({"tokens": ["u1"], "roles": ["r"]}, denials)
    write.assert_not_called()
    fallbacks, metrics = _fallback_lines(capsys)
    assert [(f["logGroupKey"], f["reason"]) for f in fallbacks] == [
        ("auditAuthorization", "logGroupUnresolved")] * 3
    for index, fallback in enumerate(fallbacks):
        assert fallback["record"].startswith("[AUTHORIZATION][authorized: False] [user: u1]")
        assert f'"kept-{index}"' in fallback["record"]
    assert [(m["AuditLogType"], m["AuditWriteFailures"]) for m in metrics] == [
        ("auditAuthorization", 3)]


def test_a_denial_that_cannot_be_formatted_is_counted_under_its_log_type(capsys):
    circular = {}
    circular["self"] = circular
    with patch.object(auditLogging, "_audit_log_group", return_value="authz-group"), \
            patch.object(auditLogging, "_write_batch_to_cloudwatch") as write:
        auditLogging.log_authorization_denials({"tokens": ["u1"]}, [{"action": "GET", "obj": circular}])
    fallbacks, _metrics = _fallback_lines(capsys)
    assert [(f["logGroupKey"], f["reason"]) for f in fallbacks] == [
        ("auditAuthorization", "formatFailed")]
    # The marker record is still written, as log_authorization writes it.
    assert write.call_args.args[1] == ["[AUTHORIZATION][authorized: False] "
                                       "[ERROR: Failed to format message]"]


def test_an_unexpected_failure_keeps_the_formatted_denials(capsys):
    with patch.object(auditLogging, "_audit_log_group", side_effect=RuntimeError("ssm down")), \
            patch.object(auditLogging, "_write_batch_to_cloudwatch") as write:
        auditLogging.log_authorization_denials(
            {"tokens": ["u1"]}, [{"action": "GET", "obj": {"assetId": "kept"}}])
    write.assert_not_called()
    fallbacks, _metrics = _fallback_lines(capsys)
    assert [(f["logGroupKey"], f["reason"]) for f in fallbacks] == [
        ("auditAuthorization", "unexpectedError")]
    assert '"kept"' in fallbacks[0]["record"]


def test_a_denied_compliance_call_is_an_authorization_record(capsys):
    """A compliance route answered 403 records its action as denied (common/compliance/routeAudit), and
    the real writer puts that record in the authorization log group, naming the compliance action."""
    from backend.backend.common.compliance import routeAudit

    os.environ.setdefault("AUDIT_LOG_ACTIONS", "test-auditActions")
    written = []
    with patch.object(auditLogging, "cloudwatch_logs") as client, \
            patch.object(auditLogging, "_created_log_streams", {}), \
            patch.object(auditLogging, "request_to_claims",
                         lambda event: {"tokens": ["alice"], "roles": [], "mfaEnabled": True}):
        client.put_log_events.side_effect = lambda **kwargs: written.append(kwargs)
        event = {"httpMethod": "POST", "path": "/compliance/quarantine/db1/a1/exception",
                 "requestContext": {"requestId": "r1"}}
        auditLogging.log_action(event, "complianceExceptionGrant", database_id="db1", asset_id="a1",
                                **routeAudit.audit_outcome({"statusCode": 403}))
    assert [call["logGroupName"] for call in written] == [os.environ["AUDIT_LOG_AUTHORIZATION"]]
    message = written[0]["logEvents"][0]["message"]
    assert message.startswith("[AUTHORIZATION][authorized: False] [user: alice]")
    assert '"action": "complianceExceptionGrant"' in message and '"outcome": "denied"' in message
    assert '"route": "/compliance/quarantine/{databaseId}/{assetId}/exception"' in message
