# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-155 -- the audit writer's CloudWatch Logs client, and how often it creates a stream.

Two properties of the module-level client, both invisible to every other test in the suite because
the audit path fails silently:

1. **Adaptive retries.** `backend/CLAUDE.md` Rule 6 requires
   `Config(retries={'max_attempts': 5, 'mode': 'adaptive'})` on every module-level AWS client.
   botocore's default `legacy` mode does retry a `ThrottlingException`, so what the missing config
   costs is the client-side rate limiting that smooths a sustained burst -- which is exactly the
   condition the audit path meets, because a bulk download issues one `PutLogEvents` per batch
   inside the request the caller is waiting on.

2. **CreateLogStream once per group per day, not once per write.** `CreateLogStream` is capped at
   50 TPS account-wide, and the audit writer called it on EVERY audit write against nine log
   groups. A throttled create is not `ResourceAlreadyExistsException`, so the writer logged and
   `return`ed -- the audit entry was dropped before a single event was written.

Both assertions have to be made on the calls the writer makes, never on a return value or on the
absence of an exception: `_write_batch_to_cloudwatch` is wrapped in a bare `except Exception` that
logs locally and returns, so a dropped audit trail and a written one are indistinguishable from
the outside. The positive controls below matter for the same reason -- eliding the per-write create
must not elide the create itself, must not skip a second log group, and must not survive a stream
that goes away underneath it.
"""

import ast
import inspect
import os

import pytest
from unittest.mock import patch
from datetime import datetime

# get_log_group_name resolves an env-var override before any SSM lookup, so seeding the legacy audit
# log-group names keeps the writer offline. Set before the module import below, which binds the
# resolver at import time.
os.environ.setdefault("AUDIT_LOG_ACTIONS", "test-auditActions")
os.environ.setdefault("AUDIT_LOG_AUTHORIZATION", "test-auditAuthorization")

from backend.backend.customLogging import auditLogging  # noqa: E402

GROUP = "test-auditActions"
OTHER_GROUP = "test-auditAuthorization"


class _ResourceAlreadyExists(Exception):
    pass


class _RecordingLogsClient:
    """A logs client that records what the writer called it with.

    `created_streams` and `put_calls` are the two quantities the finding is about: how many
    CreateLogStream round trips a sequence of audit writes costs, and whether the entries still
    reach a log stream afterwards.
    """

    def __init__(self, create_error=None, put_error=None):
        self.created_streams = []
        self.put_calls = []
        self.create_error = create_error
        self.put_error = put_error

        class _Exceptions:
            ResourceAlreadyExistsException = _ResourceAlreadyExists

        self.exceptions = _Exceptions()

    def create_log_stream(self, **kwargs):
        self.created_streams.append((kwargs["logGroupName"], kwargs["logStreamName"]))
        if self.create_error is not None:
            raise self.create_error
        return {}

    def put_log_events(self, **kwargs):
        self.put_calls.append(kwargs)
        if self.put_error is not None:
            raise self.put_error
        return {}

    @property
    def written_events(self):
        return [event for call in self.put_calls for event in call["logEvents"]]


class _FrozenClock:
    """Stands in for the module's `datetime`, so the stream name (the current UTC date) is fixed."""

    def __init__(self, moment):
        self._moment = moment

    def utcnow(self):
        return self._moment


@pytest.fixture(autouse=True)
def _clear_created_stream_record():
    """The record of created streams is module-level container state.

    Cleared before and after every test here so the tests are order-independent, and so nothing in
    this file leaves a recorded stream behind for another test file's writer. Resolved with
    ``getattr`` so the assertions below are the only thing that depends on the record existing --
    a fixture that indexed it directly would turn every test in the file into the same error.
    """
    def _clear():
        record = getattr(auditLogging, "_created_log_streams", None)
        if record is not None:
            record.clear()

    _clear()
    yield
    _clear()


def _write(client, group=GROUP, messages=None, event=None):
    with patch.object(auditLogging, "cloudwatch_logs", client):
        auditLogging._write_batch_to_cloudwatch(group, messages or ["[ACTIONS][type: test]"],
                                                event if event is not None else {})


@pytest.mark.unit
class TestAuditLogsClientRetryConfig:
    """Rule 6 on the client the audit writer actually uses."""

    def test_the_module_level_logs_client_is_built_with_adaptive_retries(self):
        client = auditLogging.cloudwatch_logs
        # POSITIVE CONTROL FIRST: the module-level client exists. It is built inside a try/except
        # that falls back to None, and every assertion below would be vacuous against None.
        assert client is not None, (
            "cloudwatch_logs is None -- the client failed to construct at import, so this test "
            "says nothing about its retry configuration")

        retries = client.meta.config.retries or {}
        assert retries.get("mode") == "adaptive", (
            f"the audit logs client resolves retry mode {retries.get('mode')!r}; Rule 6 requires "
            f"'adaptive', which is what adds client-side rate limiting under a sustained burst")
        # botocore resolves max_attempts=5 to total_max_attempts=6 in standard/adaptive mode.
        assert retries.get("total_max_attempts") == 6, (
            f"the audit logs client allows {retries.get('total_max_attempts')} total attempts, "
            f"not the 6 that max_attempts=5 resolves to")

    def test_the_retry_config_is_declared_in_the_form_rule_6_requires(self):
        """The declaration, not just the resolved client -- so the intent is legible in the module
        and a future client added here has the object to reuse.

        Read from the module's source rather than from ``retry_config.retries``, because botocore
        normalises the retries dict it is HANDED, in place, while the client is built: it pops
        ``max_attempts`` and writes ``total_max_attempts`` back into the caller's own dict
        (``botocore/args.py::_compute_retry_max_attempts``, and the dict is passed by reference from
        ``compute_client_args``). After the module import there is no ``max_attempts`` key left on
        the object, so asserting one there fails against a module that declares it correctly. The
        resolved values are the assertion above; this one is about the declared form.
        """
        declared = None
        for node in ast.walk(ast.parse(inspect.getsource(auditLogging))):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "retry_config"
                       for target in node.targets):
                continue
            for keyword in node.value.keywords:
                if keyword.arg == "retries":
                    declared = ast.literal_eval(keyword.value)

        assert declared is not None, (
            "no module-level `retry_config = Config(retries={...})` declaration found in "
            "auditLogging -- Rule 6 has nothing to point at")
        assert declared == {"max_attempts": 5, "mode": "adaptive"}, (
            f"retry_config declares {declared}; Rule 6 requires "
            f"{{'max_attempts': 5, 'mode': 'adaptive'}}")


@pytest.mark.unit
class TestCreateLogStreamIsNotPaidPerWrite:
    """The CreateLogStream round trip is what a burst of audit writes throttles on."""

    def test_repeated_writes_to_one_group_create_the_stream_once(self):
        client = _RecordingLogsClient()
        for index in range(5):
            _write(client, messages=[f"[ACTIONS][type: test][n: {index}]"])

        # THE CONTRACT: one CreateLogStream for five audit writes to the same group on the same day.
        assert len(client.created_streams) == 1, (
            f"{len(client.created_streams)} CreateLogStream calls for 5 audit writes: "
            f"{client.created_streams}")
        assert client.created_streams[0][0] == GROUP
        # The stream written to is the one that was created (the date is not recomputed here, which
        # would make the test fail across a midnight boundary rather than on the contract).
        assert {call["logStreamName"] for call in client.put_calls} == {
            client.created_streams[0][1]}
        # And nothing was traded away for it -- every write still reached a log stream.
        assert len(client.put_calls) == 5, (
            f"{len(client.put_calls)} PutLogEvents calls for 5 audit writes -- entries were "
            f"dropped, not just the stream creation")
        assert len(client.written_events) == 5
        messages = [event["message"] for event in client.written_events]
        assert all(f"[n: {index}]" in messages[index] for index in range(5))

    def test_a_resource_already_exists_create_is_recorded_as_created(self):
        """The stream exists from a previous container. The first write learns that from the
        exception and the writes after it must not ask again."""
        client = _RecordingLogsClient(create_error=_ResourceAlreadyExists("already there"))
        _write(client)
        _write(client)

        assert len(client.created_streams) == 1, (
            f"{len(client.created_streams)} CreateLogStream calls; the ResourceAlreadyExists "
            f"answer was not treated as 'the stream is there'")
        assert len(client.written_events) == 2, "both entries must still be written"

    def test_the_first_write_to_a_group_does_create_the_stream(self):
        """POSITIVE CONTROL. Eliding the per-write create must not elide the create -- without it
        the very first PutLogEvents of a container hits a stream that does not exist."""
        client = _RecordingLogsClient()
        _write(client)

        assert len(client.created_streams) == 1
        assert client.created_streams[0][0] == GROUP
        assert len(client.written_events) == 1
        assert client.put_calls[0]["logStreamName"] == client.created_streams[0][1]

    def test_each_audit_log_group_gets_its_own_stream(self):
        """POSITIVE CONTROL. Nine audit log groups share this writer, so a record keyed on the
        stream NAME alone (the date, identical across groups) would skip creation for the other
        eight and drop their entries."""
        client = _RecordingLogsClient()
        _write(client, group=GROUP)
        _write(client, group=OTHER_GROUP)

        assert [group for group, _ in client.created_streams] == [GROUP, OTHER_GROUP], (
            f"streams created: {client.created_streams}")
        assert len(client.written_events) == 2

    def test_the_stream_is_created_again_when_the_date_rolls_over(self):
        """POSITIVE CONTROL for the record's key. The stream name IS the UTC date, so a record that
        does not age out at midnight would leave a long-lived container writing to a stream it never
        created -- every audit write for the rest of its life dropped."""
        client = _RecordingLogsClient()
        with patch.object(auditLogging, "datetime", _FrozenClock(datetime(2026, 3, 1, 23, 59))):
            _write(client)
            _write(client)
        with patch.object(auditLogging, "datetime", _FrozenClock(datetime(2026, 3, 2, 0, 1))):
            _write(client)

        assert (GROUP, "2026/03/01") in client.created_streams
        assert client.created_streams[-1] == (GROUP, "2026/03/02"), (
            f"the write after midnight did not create the new day's stream: "
            f"{client.created_streams}")
        assert client.put_calls[-1]["logStreamName"] == "2026/03/02"
        assert len(client.written_events) == 3

    def test_a_create_failure_still_aborts_the_write(self):
        """POSITIVE CONTROL for the pre-existing contract: a create that fails for any other reason
        (a throttle, a missing log group) stops the write rather than putting events at a stream
        that may not exist -- and is not remembered, so the write after it tries again."""
        client = _RecordingLogsClient(create_error=RuntimeError("ThrottlingException"))
        _write(client)

        assert len(client.created_streams) == 1
        assert client.put_calls == [], "events were written after the stream creation failed"

        healthy = _RecordingLogsClient()
        _write(healthy)
        assert len(healthy.created_streams) == 1, (
            "a failed creation was treated as created, so the write after it skipped creation")
        assert len(healthy.written_events) == 1

    def test_a_failed_write_makes_the_following_write_create_the_stream_again(self):
        """The record is an optimisation, not a source of truth. If a write fails -- the stream was
        deleted, the log group was replaced under a surviving container -- the record is dropped so
        the next write re-creates the stream instead of failing for the container's whole life."""
        failing = _RecordingLogsClient(put_error=RuntimeError("ResourceNotFoundException"))
        _write(failing)
        assert len(failing.created_streams) == 1
        assert len(failing.put_calls) == 1

        recovered = _RecordingLogsClient()
        _write(recovered)
        assert len(recovered.created_streams) == 1, (
            "the write after a failed write did not re-create the stream")
        assert len(recovered.written_events) == 1


@pytest.mark.unit
class TestPublicAuditFunctionsUnchanged:
    """OVER-TIGHTENING CATCHER. Every audit event type routes through the same writer, so a
    caching mistake there silences the whole audit trail. These go through the public functions,
    resolving the log group the way a handler does."""

    def test_log_actions_writes_one_entry_with_the_event_echo(self):
        client = _RecordingLogsClient()
        event = {"requestContext": {"http": {"method": "PUT", "path": "/assets"}}}
        with patch.object(auditLogging, "cloudwatch_logs", client):
            auditLogging.log_actions(event, "assetCreate", {"assetId": "a1"})

        assert len(client.created_streams) == 1
        assert len(client.written_events) == 1
        message = client.written_events[0]["message"]
        assert message.startswith("[ACTIONS][type: assetCreate]")
        assert "--- [event:" in message

    def test_a_second_audit_event_type_still_lands_after_the_first(self):
        """Two event types, two log groups, one container: the second must not be starved by the
        first having recorded a stream of the same name."""
        client = _RecordingLogsClient()
        event = {"requestContext": {"http": {"method": "PUT", "path": "/assets"}}}
        with patch.object(auditLogging, "cloudwatch_logs", client):
            auditLogging.log_actions(event, "assetCreate", {"assetId": "a1"})
            auditLogging.log_authorization(
                {"tokens": ["u1"], "roles": ["readonly"], "mfaEnabled": False}, False,
                {"action": "GET"})

        assert len(client.written_events) == 2
        groups = [call["logGroupName"] for call in client.put_calls]
        assert len(set(groups)) == 2, f"both audit entries went to the same log group: {groups}"
