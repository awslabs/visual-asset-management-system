# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`log_authorization_denials` writes the denials of one caller's checks in one batch.

The compliance services check each archived asset a listing meets with the asset GET the asset
routes make (`common/compliance/archivedAssets`). `CasbinEnforcer.enforce` writes one CloudWatch
record per denial, a PutLogEvents round trip each; for a listing that refuses many archived assets
that is one round trip per asset. The checks run on the engine instead and their denials go out
here: the same records `log_authorization` writes, through `_write_batch_to_cloudwatch`, which
chunks them within the PutLogEvents limits.
"""

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
