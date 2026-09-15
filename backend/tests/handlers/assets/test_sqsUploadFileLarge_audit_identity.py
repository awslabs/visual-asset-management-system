# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The actor on an asynchronous large-file upload audit entry.

The >1GB upload path finishes inside an SQS-triggered Lambda, so there is no API event to
resolve an actor from and the handler supplies one. The entry it writes has to name an
identity the rest of VAMS recognises: the initiating user the queuing handler recorded on
the message, or SYSTEM_USER when the message carries none. An identity that exists in
neither the user table nor the user-roles table makes the async entry uncorrelatable with
the synchronous POST /uploads/{id}/complete entry and with the change provenance the same
run stamps, and fails closed with no obvious cause if the event is ever reused on a path
that performs a SYSTEM_USER comparison or a Casbin check.

These tests resolve the supplied event through the real request_to_claims, which is what
the audit writer does (auditLogging._extract_user_context), rather than asserting on the
event's interior shape.
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")

# Module-level imports ensure the real backend.backend.handlers.{assets,auth} packages are
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import sqsUploadFileLarge  # noqa: F401,E402
from backend.backend.handlers.auth import request_to_claims  # noqa: E402


def _sqs_event(**file_info_overrides):
    """One valid SQS record for a large file, shaped as uploadFile queues it."""
    file_info = {
        "relativeKey": "/out/scan.laz",
        "uploadIdS3": "real-s3-upload-id",
        "parts": [{"PartNumber": 1, "ETag": "etag-1"}],
        "tempS3Key": "temp/up-1/scan.laz",
        "finalS3Key": "asset-1/out/scan.laz",
        "bucketName": "asset-bucket",
        "databaseId": "db-1",
        "assetId": "asset-1",
        "uploadId": "up-1",
        "uploadType": "assetFile",
    }
    file_info.update(file_info_overrides)
    return {"Records": [{"body": json.dumps({"fileInfo": file_info})}]}


def _captured_audit_call(event):
    """Run lambda_handler over a successful record; return the log_file_upload call args."""
    from backend.backend.handlers.assets import sqsUploadFileLarge as sq

    context = MagicMock()
    context.aws_request_id = "req-1"

    with patch.object(sq, 'process_large_file', return_value=True), \
            patch.object(sq, 'log_file_upload') as mock_audit:
        sq.lambda_handler(event, context)

    assert mock_audit.call_count == 1, "the successful record wrote no audit entry"
    return mock_audit.call_args


@pytest.mark.unit
class TestAsyncLargeUploadAuditActor:
    def test_audit_actor_is_the_initiating_user_from_the_message(self):
        """changeUserId on the queued message is the actor on the audit entry."""
        call_args = _captured_audit_call(_sqs_event(changeUserId="alice@corp"))
        claims = request_to_claims(call_args.args[0])
        assert claims["tokens"] == ["alice@corp"]

    def test_audit_actor_falls_back_to_system_user(self):
        """A message with no changeUserId is attributed to the reserved system identity."""
        call_args = _captured_audit_call(_sqs_event())
        claims = request_to_claims(call_args.args[0])
        assert claims["tokens"] == ["SYSTEM_USER"]

    def test_audit_actor_is_never_an_unknown_synthetic_identity(self):
        """Whatever the message carries, the actor resolves to a real identity -- not a
        one-off spelling that exists in no user or user-roles table."""
        for file_info_overrides in ({}, {"changeUserId": "alice@corp"},
                                    {"changeUserId": "SYSTEM_USER"}):
            call_args = _captured_audit_call(_sqs_event(**file_info_overrides))
            claims = request_to_claims(call_args.args[0])
            assert claims["tokens"], "no actor resolved from the audit event"
            actor = claims["tokens"][0]
            assert not (actor.startswith("SYSTEM_") and actor != "SYSTEM_USER"), (
                f"audit entry attributed to the non-existent identity {actor}")

    def test_workflow_output_upload_is_attributed_to_the_executing_identity(self):
        """A workflow-output large file carries the executing identity, matching the
        change provenance resolve_change_metadata stamps on the same run."""
        event = _sqs_event(changeUserId="SYSTEM_USER", workflowId="wf-1",
                           workflowExecutionId="exec-1")
        call_args = _captured_audit_call(event)
        claims = request_to_claims(call_args.args[0])
        assert claims["tokens"] == ["SYSTEM_USER"]


@pytest.mark.unit
class TestAsyncLargeUploadAuditMfaState:
    """The audit entry states the actor's MFA status beside their id, and the queued message
    carries no MFA state. An end-user actor must therefore not pick up the cross-call default
    (system cross-calls run as MFA-satisfied), which would assert of a named user that their
    session presented MFA on no evidence -- and would activate MFA-gated roles if this event
    were ever reused on a path that authorizes."""

    def test_an_end_user_actor_is_not_recorded_as_mfa_satisfied(self):
        call_args = _captured_audit_call(_sqs_event(changeUserId="alice@corp"))
        claims = request_to_claims(call_args.args[0])
        assert claims["tokens"] == ["alice@corp"]
        assert claims["mfaEnabled"] is False

    def test_the_system_fallback_keeps_the_cross_call_default(self):
        """The other direction: SYSTEM_USER is the sanctioned system identity and takes the
        documented default, so the end-user rule above cannot be satisfied by pinning False
        for every actor."""
        call_args = _captured_audit_call(_sqs_event())
        claims = request_to_claims(call_args.args[0])
        assert claims["tokens"] == ["SYSTEM_USER"]
        assert claims["mfaEnabled"] is True

    def test_a_message_naming_system_user_is_treated_as_the_system_actor(self):
        """changeUserId can legitimately BE SYSTEM_USER (a trigger-launched workflow
        write-back), so the branch keys off the resolved actor, not on the field's presence."""
        call_args = _captured_audit_call(_sqs_event(changeUserId="SYSTEM_USER"))
        claims = request_to_claims(call_args.args[0])
        assert claims["mfaEnabled"] is True


@pytest.mark.unit
class TestAsyncLargeUploadAuditEntryStillWritten:
    """Positive control: the audit entry itself is unchanged in every other respect, and
    the record is still counted as processed. A fix that silenced the audit write, or that
    made a legitimate record fail, would be indistinguishable from the fix without this."""

    def test_audit_entry_keeps_its_subject_and_custom_data(self):
        call_args = _captured_audit_call(_sqs_event(changeUserId="alice@corp"))
        _, database_id, asset_id, file_path, upload_denied, denied_reason, custom_data = \
            call_args.args
        assert database_id == "db-1"
        assert asset_id == "asset-1"
        assert file_path == "/out/scan.laz"
        assert upload_denied is False
        assert denied_reason is None
        assert custom_data["uploadId"] == "up-1"
        assert custom_data["uploadType"] == "assetFile"
        assert custom_data["status"] == "completed_async"
        assert custom_data["processingType"] == "large_file_async"

    def test_successful_record_is_not_turned_into_a_failure(self):
        """The audit write is best-effort; a legitimate record still processes normally."""
        from backend.backend.handlers.assets import sqsUploadFileLarge as sq

        context = MagicMock()
        context.aws_request_id = "req-1"

        with patch.object(sq, 'process_large_file', return_value=True) as mock_process, \
                patch.object(sq, 'log_file_upload'):
            sq.lambda_handler(_sqs_event(changeUserId="alice@corp"), context)

        assert mock_process.call_count == 1


@pytest.mark.unit
class TestAsyncLargeUploadAuditEventShape:
    def test_claims_resolution_leaves_the_audit_event_untouched(self):
        """request_to_claims short-circuits an internal cross-call event, so the event the
        audit writer echoes is the one the handler built. A synthetic API-event shape is
        instead walked by normalize_event, which rewrites it before the echo."""
        call_args = _captured_audit_call(_sqs_event(changeUserId="alice@corp"))
        audit_event = call_args.args[0]
        before = json.dumps(audit_event, sort_keys=True)
        request_to_claims(audit_event)
        assert json.dumps(audit_event, sort_keys=True) == before
        assert "http" not in audit_event.get("requestContext", {})
