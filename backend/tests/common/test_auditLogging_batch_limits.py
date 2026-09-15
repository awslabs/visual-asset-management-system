# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-018 -- the audit batch writer must respect the PutLogEvents SIZE limits, not only the count.

CloudWatch rejects a batch whose events exceed 1,048,576 bytes in total (26 bytes of overhead per
event on top of the UTF-8 message bytes) and rejects any single event over 256 KiB. Every entry in a
batch carries the whole masked request event appended as ``event_suffix``, so a bulk download of
~1500 files is 1500 copies of a multi-KB event -- several megabytes in one call. Chunking by event
count alone therefore produces an over-limit batch, and chunking on message length alone under-counts
it. ``_write_batch_to_cloudwatch`` must chunk on the real byte budget, overhead included.

Chunking on that budget makes the payload land rather than be rejected, which is only half the
contract. 1500 copies of an event that itself carries the 1500-key request body is ~373 MiB in
~375 sequential PutLogEvents calls, inside the download request the caller is waiting on. The echo
is identical on every entry, so it is written once and referenced from the rest, and each entry's
byte cost is measured once as the entry is built -- ``TestBulkDownloadEchoIsNotReplicatedPerEntry``
holds the assertions for both.

An overflow is INVISIBLE: the whole function is wrapped in a bare ``except Exception`` that logs
locally and returns, so the API call raises, the audit trail for that request is dropped in full, and
the download still succeeds. That is why these tests assert on the calls made to a stubbed client and
on the NUMBER of events that were accepted -- never on a return value and never on the absence of an
exception, both of which look identical whether the audit landed or vanished.

``_write_to_cloudwatch`` (every one of the eight non-bulk audit event types) delegates into the same
function, so the single-entry shape is pinned here as the over-tightening catcher.
"""

import json
import os

import pytest
from unittest.mock import patch

# get_log_group_name resolves an env-var override before any SSM lookup, so seeding the legacy audit
# log-group names keeps the writer offline. Set before the module import below, which binds the
# resolver at import time.
os.environ.setdefault("AUDIT_LOG_FILEDOWNLOAD", "test-auditFileDownload")
os.environ.setdefault("AUDIT_LOG_AUTHORIZATION", "test-auditAuthorization")
os.environ.setdefault("AUDIT_LOG_AUTHENTICATION", "test-auditAuthentication")
os.environ.setdefault("AUDIT_LOG_ACTIONS", "test-auditActions")

from backend.backend.customLogging import auditLogging  # noqa: E402
from backend.backend.customLogging.logger import mask_sensitive_data, REDACTED  # noqa: E402

# The real PutLogEvents contract.
MAX_BATCH_BYTES = 1_048_576
MAX_BATCH_EVENTS = 10_000
MAX_EVENT_BYTES = 262_144
EVENT_OVERHEAD_BYTES = 26

BULK_FILE_COUNT = 1500
_BEARER = "Bearer topsecret.jwt.value"


class _Rejected(Exception):
    """Stands in for the InvalidParameterException the real API raises on an oversized batch."""


class _ResourceAlreadyExists(Exception):
    pass


class _CloudWatchOracle:
    """A stubbed logs client that enforces the real PutLogEvents limits.

    Batches that would be refused by CloudWatch raise instead of being recorded, so the accepted
    event count is exactly what would have reached the log group.
    """

    def __init__(self):
        self.accepted_batches = []
        self.attempts = 0
        self.rejections = []

        class _Exceptions:
            ResourceAlreadyExistsException = _ResourceAlreadyExists

        self.exceptions = _Exceptions()

    def create_log_stream(self, **kwargs):
        return {}

    def put_log_events(self, **kwargs):
        self.attempts += 1
        events = kwargs["logEvents"]
        if not events:
            self.rejections.append("empty batch")
            raise _Rejected("logEvents must not be empty")
        if len(events) > MAX_BATCH_EVENTS:
            self.rejections.append(f"{len(events)} events")
            raise _Rejected("too many events in one batch")
        total = sum(len(e["message"].encode("utf-8")) + EVENT_OVERHEAD_BYTES for e in events)
        if total > MAX_BATCH_BYTES:
            self.rejections.append(f"{total} bytes")
            raise _Rejected("batch exceeds the 1 MiB PutLogEvents limit")
        for event in events:
            size = len(event["message"].encode("utf-8")) + EVENT_OVERHEAD_BYTES
            if size > MAX_EVENT_BYTES:
                self.rejections.append(f"single event {size} bytes")
                raise _Rejected("single event exceeds 256 KiB")
        self.accepted_batches.append(events)
        return {}

    @property
    def accepted_events(self):
        return [event for batch in self.accepted_batches for event in batch]


def _realistic_event(body_bytes=3000):
    """An API Gateway event of the size the download routes actually carry."""
    return {
        "requestContext": {
            "http": {"method": "POST", "path": "/database/db1/assets/asset1/download"},
            "requestId": "b7d1c0f0-0000-4000-8000-000000000001",
        },
        "headers": {"authorization": _BEARER, "user-agent": "x" * 200},
        "body": json.dumps({"downloadType": "assetFile", "filler": "f" * body_bytes}),
    }


def _file_entries(count):
    return [{"filePath": f"/folder{i // 50}/model_{i}.glb", "versionId": f"v{i}"}
            for i in range(count)]


def _run_bulk(oracle, event, entries, custom_data_base=None):
    with patch.object(auditLogging, "cloudwatch_logs", oracle), \
            patch.object(auditLogging, "mask_sensitive_data", mask_sensitive_data):
        auditLogging.log_file_download_bulk(
            event, "db1", "asset1", entries,
            custom_data_base=custom_data_base or {"downloadType": "assetFile"})


@pytest.mark.unit
class TestBulkDownloadBatchByteBudget:
    """A bulk download must produce one audit entry per file, whatever the byte total."""

    def test_every_file_is_audited_when_the_batch_exceeds_the_byte_limit(self):
        oracle = _CloudWatchOracle()
        _run_bulk(oracle, _realistic_event(), _file_entries(BULK_FILE_COUNT))

        # The writer was reached at all (guards against a mis-wired patch making the count
        # assertion below vacuous).
        assert oracle.attempts >= 1, "put_log_events was never called -- the stub is not wired in"
        # THE CONTRACT: one entry per file reaches CloudWatch.
        assert len(oracle.accepted_events) == BULK_FILE_COUNT, (
            f"{len(oracle.accepted_events)} of {BULK_FILE_COUNT} audit entries reached CloudWatch; "
            f"rejections: {oracle.rejections}")
        # Chunking must not emit an empty batch, and every event in a request shares one timestamp
        # (PutLogEvents requires chronological order within a batch).
        assert all(batch for batch in oracle.accepted_batches)
        timestamps = {event["timestamp"] for event in oracle.accepted_events}
        assert len(timestamps) == 1, f"events span {len(timestamps)} timestamps"
        # Masking still runs before anything is written, in whatever form the event echo takes.
        for event in oracle.accepted_events:
            assert _BEARER not in event["message"]
        assert any(REDACTED in event["message"] for event in oracle.accepted_events)
        # The file identity is what makes the trail useful.
        joined = "\n".join(event["message"] for event in oracle.accepted_events)
        assert "/folder0/model_0.glb" in joined
        assert f"/folder{(BULK_FILE_COUNT - 1) // 50}/model_{BULK_FILE_COUNT - 1}.glb" in joined

    def test_a_small_bulk_download_is_accepted_by_the_oracle(self):
        """CONTROL for the test above. Proves the oracle, the env-var log-group override and the
        patched client are wired correctly, so a zero accepted-event count there is the byte
        overflow rather than a broken fixture."""
        oracle = _CloudWatchOracle()
        _run_bulk(oracle, _realistic_event(), _file_entries(5))
        assert oracle.rejections == []
        assert len(oracle.accepted_events) == 5
        # A download well inside both budgets keeps the single round trip batching exists for --
        # and a chunker that always splits fails here while satisfying every other assertion.
        assert len(oracle.accepted_batches) == 1, (
            f"5 entries were split across {len(oracle.accepted_batches)} put_log_events calls")

    def test_an_oversized_single_entry_is_truncated_rather_than_dropped(self):
        oracle = _CloudWatchOracle()
        _run_bulk(oracle, _realistic_event(body_bytes=100),
                  [{"filePath": "/huge.glb"}],
                  custom_data_base={"downloadType": "assetFile", "note": "n" * 300_000})

        assert oracle.attempts >= 1, "put_log_events was never called -- the stub is not wired in"
        assert len(oracle.accepted_events) == 1, (
            f"the oversized entry never reached CloudWatch; rejections: {oracle.rejections}")
        message = oracle.accepted_events[0]["message"]
        assert len(message.encode("utf-8")) + EVENT_OVERHEAD_BYTES < MAX_EVENT_BYTES
        # The cut is taken from the tail, so the bracketed prefix and the identifying fields the
        # formatter emits first are what survive -- a slice taken anywhere else loses them.
        assert message.startswith("[FILEDOWNLOAD]")
        assert "/huge.glb" in message
        # And the record says it was cut, so a consumer cannot read a truncated entry as complete.
        assert message.endswith(auditLogging.AUDIT_EVENT_TRUNCATION_MARKER)


@pytest.mark.unit
class TestBatchBudgetAccounting:
    """The chunker's arithmetic, driven straight through the writer.

    These call ``_write_batch_to_cloudwatch`` with pre-sized messages and an empty event, so the
    entry bytes are exactly the message bytes and the budget boundary can be landed on. The real
    formatters cannot reach the count limit -- the shortest audit message a bulk download can
    produce is over 100 bytes, so 10,000 of them are 1 MB and the byte budget always binds first.
    """

    def _write(self, messages):
        oracle = _CloudWatchOracle()
        with patch.object(auditLogging, "cloudwatch_logs", oracle):
            auditLogging._write_batch_to_cloudwatch("test-auditFileDownload", messages, {})
        return oracle

    def test_the_per_event_overhead_counts_against_the_batch_budget(self):
        """A batch that fits on message bytes alone but NOT with the 26-byte overhead must split.

        1024 x 1000 message bytes is 1,024,000 -- inside the 1,048,576-byte limit. Adding 26
        bytes per event makes it 1,050,624, which CloudWatch refuses in full. A chunker that
        sums message lengths only sends one batch here and loses all 1024 entries.
        """
        count = 1024
        message = "m" * 1000
        assert count * len(message) <= MAX_BATCH_BYTES, "the case must fit on message bytes alone"
        assert count * (len(message) + EVENT_OVERHEAD_BYTES) > MAX_BATCH_BYTES, (
            "the case must breach the budget once the overhead is counted")

        oracle = self._write([message] * count)

        assert oracle.rejections == [], f"an over-budget batch was sent: {oracle.rejections}"
        assert len(oracle.accepted_batches) > 1, "the over-budget batch was not split"
        assert len(oracle.accepted_events) == count, (
            f"{len(oracle.accepted_events)} of {count} entries survived chunking")

    def test_a_batch_inside_the_budget_with_overhead_is_written_in_one_call(self):
        """CONTROL for the test above: 1022 of the same entries total 1,048,572 bytes WITH the
        overhead, so they still belong in a single call. A chunker that over-reserves -- or one
        that splits unconditionally -- fails here."""
        count = 1022
        message = "m" * 1000
        assert count * (len(message) + EVENT_OVERHEAD_BYTES) <= MAX_BATCH_BYTES

        oracle = self._write([message] * count)

        assert oracle.rejections == []
        assert len(oracle.accepted_batches) == 1, (
            f"{len(oracle.accepted_batches)} calls for a batch that fits in one")
        assert len(oracle.accepted_events) == count

    def test_the_event_count_limit_still_applies(self):
        """Entries small enough that the byte budget never binds are still capped at 10,000 per
        call, so the count limit the writer already honoured is not lost to the byte chunking."""
        count = MAX_BATCH_EVENTS + 5
        oracle = self._write(["x"] * count)

        assert oracle.rejections == [], f"a batch over the event cap was sent: {oracle.rejections}"
        assert len(oracle.accepted_events) == count
        assert len(oracle.accepted_batches) == 2, (
            f"{len(oracle.accepted_batches)} calls for {count} tiny entries")
        assert len(oracle.accepted_batches[0]) == MAX_BATCH_EVENTS
        # The byte budget really is slack here, or this would be testing byte chunking instead.
        total = sum(len(e["message"].encode("utf-8")) + EVENT_OVERHEAD_BYTES
                    for e in oracle.accepted_events)
        assert total < MAX_BATCH_BYTES, f"{total} bytes -- the byte budget bound instead"


@pytest.mark.unit
class TestSingleEntryAuditTypesUnchanged:
    """OVER-TIGHTENING CATCHER. `_write_to_cloudwatch` delegates into the batch writer, so the eight
    non-bulk audit types ride on this change. Each must still emit exactly one event carrying the
    masked event echo."""

    def _run(self, call):
        oracle = _CloudWatchOracle()
        with patch.object(auditLogging, "cloudwatch_logs", oracle), \
                patch.object(auditLogging, "mask_sensitive_data", mask_sensitive_data):
            call()
        return oracle

    def test_log_authentication_emits_one_event_with_the_event_echo(self):
        oracle = self._run(lambda: auditLogging.log_authentication(
            _realistic_event(body_bytes=10), True, {"authProvider": "cognito"}))
        assert len(oracle.accepted_batches) == 1
        assert len(oracle.accepted_events) == 1
        message = oracle.accepted_events[0]["message"]
        assert message.startswith("[AUTHENTICATION][authenticated: True]")
        assert "--- [event:" in message
        assert _BEARER not in message

    def test_log_authorization_emits_one_event(self):
        oracle = self._run(lambda: auditLogging.log_authorization(
            {"tokens": ["u1"], "roles": ["readonly"], "mfaEnabled": False}, False,
            {"action": "GET", "obj": {"object__type": "api", "route__path": "/database"}}))
        assert len(oracle.accepted_batches) == 1
        assert len(oracle.accepted_events) == 1
        assert oracle.accepted_events[0]["message"].startswith("[AUTHORIZATION][authorized: False]")

    def test_log_actions_emits_one_event(self):
        oracle = self._run(lambda: auditLogging.log_actions(
            _realistic_event(body_bytes=10), "assetCreate", {"assetId": "a1"}))
        assert len(oracle.accepted_batches) == 1
        assert len(oracle.accepted_events) == 1
        assert "--- [event:" in oracle.accepted_events[0]["message"]


# Key length used for the bulk fixtures below: well inside MAX_S3_KEY_LENGTH (1024), so the
# payloads these produce are reachable by an ordinary request rather than a contrived one.
BULK_KEY_BYTES = 200


def _bulk_entries(count, key_bytes=BULK_KEY_BYTES):
    return [{"filePath": f"/folder{i // 50}/{'k' * key_bytes}_{i}.glb", "versionId": f"v{i}"}
            for i in range(count)]


def _bulk_download_event(entries):
    """The event ``downloadAsset`` hands the writer for a bulk download.

    The request body IS the key list, so the event -- and therefore the echo appended to each
    entry -- grows with the entry count. That is the property that turns a per-entry echo into a
    payload quadratic in the entry count, and an event whose size is fixed independently of the
    entry count (``_realistic_event`` above) cannot exhibit it.
    """
    return {
        "requestContext": {
            "http": {"method": "POST", "path": "/database/db1/assets/asset1/download"},
            "requestId": "b7d1c0f0-0000-4000-8000-000000000001",
        },
        "headers": {"authorization": _BEARER, "user-agent": "x" * 200},
        "body": json.dumps({
            "downloadType": "assetFile",
            "keys": [{"key": entry["filePath"], "versionId": entry["versionId"]}
                     for entry in entries],
        }),
    }


def _transmitted_bytes(oracle):
    """Bytes the writer put on the wire, counted the way PutLogEvents charges them."""
    return sum(len(event["message"].encode("utf-8")) + EVENT_OVERHEAD_BYTES
               for event in oracle.accepted_events)


@pytest.mark.unit
class TestBulkDownloadEchoIsNotReplicatedPerEntry:
    """The audit write for a bulk download must cost bytes proportional to the file count.

    The echo is the same text on every entry of a batch, and for a bulk download it carries the
    request's own key list -- so appending it to each entry costs entries x event bytes. At the
    endpoint's documented limit (1500 keys) that is hundreds of megabytes, chunked into hundreds
    of sequential ``put_log_events`` calls INSIDE the download request path, which the caller
    waits on before its presigned URLs are returned. Chunking alone makes that payload land
    instead of being rejected; it does not make it smaller.
    """

    def test_the_documented_worst_case_stays_inside_a_bounded_payload(self):
        entries = _bulk_entries(BULK_FILE_COUNT)
        oracle = _CloudWatchOracle()
        _run_bulk(oracle, _bulk_download_event(entries), entries)

        # AUDIT COMPLETENESS FIRST: a smaller payload is worthless if it lost entries.
        assert oracle.rejections == []
        assert len(oracle.accepted_events) == BULK_FILE_COUNT, (
            f"{len(oracle.accepted_events)} of {BULK_FILE_COUNT} audit entries reached "
            f"CloudWatch; rejections: {oracle.rejections}")
        # Replicating the echo across 1500 entries is ~373 MiB in ~375 calls; one shared copy is
        # under a megabyte in one call. The bounds are loose enough to survive fixture drift and
        # tight enough that a return to per-entry replication cannot pass them.
        transmitted = _transmitted_bytes(oracle)
        assert transmitted <= 4 * 1024 * 1024, (
            f"{transmitted} bytes written for {BULK_FILE_COUNT} audit entries")
        assert len(oracle.accepted_batches) <= 8, (
            f"{len(oracle.accepted_batches)} put_log_events calls in the download request path")

    def test_the_transmitted_bytes_grow_linearly_with_the_entry_count(self):
        """PERFORMANCE ASSERTION, on the quantity the defect is about: bytes on the wire.

        Quadrupling the entry count must roughly quadruple the payload. Replicating an echo that
        itself grows with the entry count multiplies it by ~16 instead, which is the whole
        defect; measured against pre-fix code this ratio is 15.8.
        """
        def payload_bytes(count):
            entries = _bulk_entries(count)
            oracle = _CloudWatchOracle()
            _run_bulk(oracle, _bulk_download_event(entries), entries)
            assert len(oracle.accepted_events) == count, (
                f"{len(oracle.accepted_events)} of {count} entries reached CloudWatch")
            return _transmitted_bytes(oracle)

        small = payload_bytes(100)
        large = payload_bytes(400)
        ratio = large / small

        assert ratio <= 6, (
            f"payload grew {ratio:.1f}x for a 4x entry count -- the echo is still replicated")
        # POSITIVE CONTROL: the measurement tracks the entry count at all. A fixture that
        # produced a constant payload would satisfy the bound above while proving nothing.
        assert ratio >= 3, f"payload grew only {ratio:.1f}x -- the fixture is not scaling"

    def test_each_entry_is_measured_exactly_once(self):
        """PERFORMANCE ASSERTION, on the work done per entry.

        Every byte measurement in the writer goes through ``_message_byte_length``, so counting
        calls to it counts how many times an entry is measured. One per entry (plus one for the
        shared echo) is the single-pass contract: the cost is measured as the entry is built and
        carried forward as a running total, so the chunker never re-measures.

        WHAT THIS PROVES: no per-entry re-scan or re-measure of the accumulated batch -- the
        shape that turns chunking into O(n^2) work. A batch re-measured per entry would call it
        ~1.1 million times for 1500 entries.

        WHAT IT DOES NOT PROVE: wall-clock performance, and it cannot see a measurement that
        bypasses the helper with an inline ``len(x.encode())``. The byte-volume assertions in
        this class bound the payload from the other side, where such a bypass would still show.
        """
        entries = _bulk_entries(BULK_FILE_COUNT)
        oracle = _CloudWatchOracle()
        measured = []
        real_length = auditLogging._message_byte_length

        def counting(message):
            measured.append(len(message))
            return real_length(message)

        with patch.object(auditLogging, "_message_byte_length", counting):
            _run_bulk(oracle, _bulk_download_event(entries), entries)

        assert len(oracle.accepted_events) == BULK_FILE_COUNT
        # POSITIVE CONTROL first: the seam is on the path at all, once per entry.
        assert len(measured) >= BULK_FILE_COUNT, (
            f"only {len(measured)} measurements for {BULK_FILE_COUNT} entries -- the helper is "
            f"not the writer's measuring point any more, so this test is vacuous")
        # The exact count is one per entry, one for the shared echo, and one more for each entry
        # cut to the per-event budget (the first entry here, which carries the echo). The margin
        # keeps that accounting from making the test brittle while staying three orders of
        # magnitude below a re-scan.
        assert len(measured) <= BULK_FILE_COUNT + 8, (
            f"{len(measured)} measurements for {BULK_FILE_COUNT} entries")
        # And the bytes fed through it, which a re-scan inflates even when the call count is
        # linear (measuring the whole accumulated batch once per entry).
        assert sum(measured) <= 4 * 1024 * 1024, (
            f"{sum(measured)} bytes measured for {BULK_FILE_COUNT} entries")

    def test_the_echo_is_written_once_and_referenced_by_the_remaining_entries(self):
        """The context is not lost, it is written once -- the entries after the first point at it.

        Nothing is dropped: the echo is identical on every entry, so one copy carries the same
        information, and each entry still identifies its own file.
        """
        entries = _bulk_entries(BULK_FILE_COUNT)
        oracle = _CloudWatchOracle()
        _run_bulk(oracle, _bulk_download_event(entries), entries)

        messages = [event["message"] for event in oracle.accepted_events]
        assert len(messages) == BULK_FILE_COUNT
        # The first entry carries the echo, masked as always.
        assert "--- [event: {" in messages[0]
        assert REDACTED in messages[0]
        assert all(_BEARER not in message for message in messages)
        # The rest carry the reference, and none of them a second copy of the echo.
        assert all(auditLogging.AUDIT_EVENT_ECHO_REFERENCE in message
                   for message in messages[1:])
        assert sum("--- [event: {" in message for message in messages) == 1
        # PER-ENTRY IDENTITY: order preserved, one entry per file, each naming its own file.
        for index, entry in enumerate(entries):
            assert entry["filePath"] in messages[index]

    def test_an_echo_small_enough_to_replicate_is_still_carried_by_every_entry(self):
        """CONTROL, and the contract the denial-audit sink depends on.

        ``handlers/auth/routes.py`` batches authorization-denial records against a synthesized
        ~100-byte audit event; replicating that across a whole batch costs a fraction of one
        call, so those entries keep their own echo. The shared copy applies only where the
        replication is itself the cost -- a writer that always elides would fail here while
        passing every assertion above.
        """
        oracle = _CloudWatchOracle()
        messages = [f"[AUTHORIZATION][authorized: False] [user: u1] [denial {index}]"
                    for index in range(2000)]
        synthesized_event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "u1"}}}}
        }
        with patch.object(auditLogging, "cloudwatch_logs", oracle), \
                patch.object(auditLogging, "mask_sensitive_data", mask_sensitive_data):
            auditLogging._write_batch_to_cloudwatch(
                "test-auditAuthorization", messages, synthesized_event)

        assert oracle.rejections == []
        assert len(oracle.accepted_events) == len(messages)
        assert all("--- [event: {" in event["message"] for event in oracle.accepted_events), (
            "a small echo was elided; the denial records lost their per-entry event context")
