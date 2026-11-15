# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An audit entry that cannot be delivered is kept in the function log and counted.

The audit writer never raises into the handler, so before this contract a failed write left only
an error line ("Failed to write 1 audit entries ...") and the entry itself was gone. Every path
that loses an entry -- put_log_events raising, the client missing, the log stream not created,
the log group name not resolved, the record not formatted -- now writes the entry, as it would
have been written, to the function's own log as one JSON line marked auditFallback, and one
CloudWatch embedded-metric-format line counting the lost entries (namespace VAMS/Audit, metric
AuditWriteFailures, dimension AuditLogType). A successful write prints nothing.
"""

import json
import os

import pytest
from unittest.mock import MagicMock

os.environ.setdefault("AUDIT_LOG_ACTIONS", "test-auditActions")
os.environ.setdefault("AUDIT_LOG_FILEDOWNLOAD", "test-auditFileDownload")
os.environ.setdefault("AUDIT_LOG_AUTHORIZATION", "test-auditAuthorization")

from backend.backend.customLogging import auditLogging  # noqa: E402
from backend.backend.customLogging.logger import mask_sensitive_data  # noqa: E402

KEYS = auditLogging.ResourceKeys
MARKER = "audit-body-marker-0123"
BEARER = "Bearer topsecret.jwt.value"
LONG_DIR = "d" * 900


class _AlreadyExists(Exception):
    pass


def _client(put_error=None, create_error=None):
    client = MagicMock()
    client.exceptions.ResourceAlreadyExistsException = _AlreadyExists
    if put_error is not None:
        client.put_log_events.side_effect = put_error
    if create_error is not None:
        client.create_log_stream.side_effect = create_error
    return client


def _event():
    return {"headers": {"Authorization": BEARER},
            "requestContext": {"http": {"method": "POST", "path": "/database/db1/assets"}},
            "_claims": {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": False}}


def _lines(capsys):
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _fallbacks(lines):
    return [line for line in lines if line.get("auditFallback") is True]


def _metrics(lines):
    return [line for line in lines if "_aws" in line]


@pytest.fixture
def audit(monkeypatch):
    """The real masker, claims from the event, and fresh stream bookkeeping."""
    monkeypatch.setattr(auditLogging, "mask_sensitive_data", mask_sensitive_data)
    monkeypatch.setattr(auditLogging, "request_to_claims", lambda event: dict(event["_claims"]))
    monkeypatch.setattr(auditLogging, "_created_log_streams", {})
    # Resolve the groups these tests write to, so each name is known by its log type.
    for key in (KEYS.AUDIT_LOG_ACTIONS, KEYS.AUDIT_LOG_FILEDOWNLOAD, KEYS.AUDIT_LOG_AUTHORIZATION):
        assert auditLogging._audit_log_group(key), "the env-var log-group override is not in effect"
    return monkeypatch


def _assert_metric(metric, log_type, value):
    directive = metric["_aws"]["CloudWatchMetrics"][0]
    assert directive["Namespace"] == "VAMS/Audit"
    assert directive["Dimensions"] == [["AuditLogType"]]
    assert directive["Metrics"] == [{"Name": "AuditWriteFailures", "Unit": "Count"}]
    assert isinstance(metric["_aws"]["Timestamp"], int)
    assert metric["AuditLogType"] == log_type
    assert metric["AuditWriteFailures"] == value


@pytest.mark.unit
class TestUndeliveredEntryIsKept:
    def test_a_rejected_put_writes_the_entry_to_the_function_log(self, audit, capsys):
        client = _client(put_error=RuntimeError("throttled"))
        audit.setattr(auditLogging, "cloudwatch_logs", client)
        auditLogging.log_actions(_event(), "assetCreate", {"note": MARKER})
        assert client.put_log_events.called, "the write was never attempted"

        lines = _lines(capsys)
        fallbacks = _fallbacks(lines)
        assert len(fallbacks) == 1
        fallback = fallbacks[0]
        assert fallback["logGroupKey"] == "auditActions"
        assert fallback["reason"] == "putLogEventsFailed"
        assert fallback["record"].startswith("[ACTIONS][type: assetCreate] [user: alice]")
        assert MARKER in fallback["record"]
        # The record is the masked one, echo included.
        assert " --- [event: " in fallback["record"]
        assert BEARER not in fallback["record"] and "topsecret" not in json.dumps(lines)
        metrics = _metrics(lines)
        assert len(metrics) == 1
        _assert_metric(metrics[0], "auditActions", 1)

    def test_a_missing_client_keeps_the_entry(self, audit, capsys):
        audit.setattr(auditLogging, "cloudwatch_logs", None)
        auditLogging.log_actions(_event(), "assetCreate", {"note": MARKER})
        lines = _lines(capsys)
        fallbacks = _fallbacks(lines)
        assert [f["reason"] for f in fallbacks] == ["clientUnavailable"]
        assert MARKER in fallbacks[0]["record"]
        _assert_metric(_metrics(lines)[0], "auditActions", 1)

    def test_a_failed_stream_creation_keeps_the_entry(self, audit, capsys):
        client = _client(create_error=RuntimeError("access denied"))
        audit.setattr(auditLogging, "cloudwatch_logs", client)
        auditLogging.log_actions(_event(), "assetCreate", {"note": MARKER})
        assert not client.put_log_events.called
        fallbacks = _fallbacks(_lines(capsys))
        assert [f["reason"] for f in fallbacks] == ["createLogStreamFailed"]
        assert MARKER in fallbacks[0]["record"]

    def test_an_unresolved_log_group_keeps_the_entry(self, audit, capsys):
        client = _client()
        audit.setattr(auditLogging, "cloudwatch_logs", client)
        audit.setattr(auditLogging, "_audit_log_group", lambda key: None)
        auditLogging.log_actions(_event(), "assetCreate", {"note": MARKER})
        assert not client.put_log_events.called
        lines = _lines(capsys)
        fallbacks = _fallbacks(lines)
        assert [f["reason"] for f in fallbacks] == ["logGroupUnresolved"]
        assert fallbacks[0]["logGroupKey"] == "auditActions"
        assert MARKER in fallbacks[0]["record"] and " --- [event: " in fallbacks[0]["record"]
        assert "topsecret" not in json.dumps(lines)
        _assert_metric(_metrics(lines)[0], "auditActions", 1)

    def test_an_unresolved_log_group_keeps_a_deferred_authorization_entry(self, audit, capsys):
        audit.setattr(auditLogging, "cloudwatch_logs", _client())
        audit.setattr(auditLogging, "_audit_log_group", lambda key: None)
        with auditLogging.deferred_authorization_audit():
            auditLogging.log_authorization({"tokens": ["alice"], "roles": []}, False, {"action": MARKER})
        fallbacks = _fallbacks(_lines(capsys))
        assert [(f["logGroupKey"], f["reason"]) for f in fallbacks] == [
            ("auditAuthorization", "logGroupUnresolved")]
        assert MARKER in fallbacks[0]["record"]

    def test_a_record_that_cannot_be_formatted_is_counted(self, audit, capsys):
        audit.setattr(auditLogging, "cloudwatch_logs", _client())
        circular = {}
        circular["self"] = circular
        auditLogging.log_actions(_event(), "assetCreate", circular)
        lines = _lines(capsys)
        assert [f["reason"] for f in _fallbacks(lines)] == ["formatFailed"]
        _assert_metric(_metrics(lines)[0], "auditActions", 1)

    def test_a_successful_write_prints_nothing(self, audit, capsys):
        client = _client()
        audit.setattr(auditLogging, "cloudwatch_logs", client)
        auditLogging.log_actions(_event(), "assetCreate", {"note": MARKER})
        assert client.put_log_events.called
        assert capsys.readouterr().out == ""

    def test_reporting_never_raises_into_the_handler(self, audit, capsys):
        audit.setattr(auditLogging, "cloudwatch_logs", _client(put_error=RuntimeError("throttled")))
        audit.setattr(auditLogging, "_write_function_log_line",
                      MagicMock(side_effect=OSError("stdout closed")))
        auditLogging.log_actions(_event(), "assetCreate", {"note": MARKER})


@pytest.mark.unit
class TestBatchFallback:
    def test_every_entry_of_a_failed_bulk_write_is_kept_with_one_metric_per_batch(self, audit, capsys):
        client = _client(put_error=RuntimeError("throttled"))
        audit.setattr(auditLogging, "cloudwatch_logs", client)
        # Long paths, so the entries need more than one PutLogEvents batch.
        entries = [{"filePath": f"/folder/{LONG_DIR}/model_{i}.glb", "versionId": f"v{i}"} for i in range(1500)]
        event = _event()
        event["body"] = json.dumps({"filler": "f" * 3000})
        auditLogging.log_file_download_bulk(event, "db1", "asset1", entries, {"downloadType": "assetFile"})

        lines = _lines(capsys)
        fallbacks = _fallbacks(lines)
        metrics = _metrics(lines)
        assert client.put_log_events.call_count >= 2, "one batch held every entry; the per-batch count is untested"
        assert len(fallbacks) == 1500
        assert {f["logGroupKey"] for f in fallbacks} == {"auditFileDownload"}
        assert len(metrics) == client.put_log_events.call_count
        assert sum(m["AuditWriteFailures"] for m in metrics) == 1500
        joined = "\n".join(f["record"] for f in fallbacks)
        assert "/model_0.glb" in joined and "/model_1499.glb" in joined

    def test_only_the_failed_batch_is_reported(self, audit, capsys):
        client = _client(put_error=[RuntimeError("throttled"), {}, {}, {}, {}, {}, {}, {}, {}, {}])
        audit.setattr(auditLogging, "cloudwatch_logs", client)
        entries = [{"filePath": f"/{LONG_DIR}/m_{i}.glb"} for i in range(1500)]
        event = _event()
        event["body"] = json.dumps({"filler": "f" * 3000})
        auditLogging.log_file_download_bulk(event, "db1", "asset1", entries)
        first_batch = client.put_log_events.call_args_list[0].kwargs["logEvents"]
        lines = _lines(capsys)
        assert len(_fallbacks(lines)) == len(first_batch) < 1500
        assert [m["AuditWriteFailures"] for m in _metrics(lines)] == [len(first_batch)]

    def test_an_oversized_entry_fits_one_function_log_line(self, audit, capsys):
        audit.setattr(auditLogging, "cloudwatch_logs", _client(put_error=RuntimeError("throttled")))
        auditLogging.log_actions(_event(), "assetCreate", {"note": '"\\' * 200_000})
        out = capsys.readouterr().out
        fallback_line = [line for line in out.splitlines() if '"auditFallback"' in line][0]
        assert len(fallback_line.encode("utf-8")) < auditLogging.AUDIT_EVENT_MAX_BYTES
        record = json.loads(fallback_line)["record"]
        assert record.startswith("[ACTIONS][type: assetCreate]")
        assert record.endswith(auditLogging.AUDIT_EVENT_TRUNCATION_MARKER)
