# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The launch worker stamps the start of a stopped desktop EDH accepted (in-dev D16): ``startAcceptedAt``, in the
start-stamp form, on the CREATING row it records -- on EDH's answer and on a read of the desktop after a lost
answer alike -- so the session sweep can tell a start that never finished. A desktop the read already finds
running carries no stamp, a refused start writes none, and a new launch never writes it."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.backend.handlers.addon.edh.common.edhClient import EdhCapacityError, EdhOutcomeUnknownError
from backend.tests.handlers.addon.edh.edhServiceTestSupport import SESSION_OWN, FakeEdhClient, Sequence
from backend.tests.handlers.addon.edh.edhWorkerTestSupport import (
    creating_row, desktop, install_worker_fakes, updates, worker_event,
)
from backend.tests.handlers.addon.edh.test_edhLaunchWorker_start import (
    ICE_TEXT, INSTANCE_ID, _run, _stamp, _start_row, _stored,
)

# How far the stamp may sit from the test's own clock: the worker writes it while the test runs.
STAMP_SLACK = timedelta(minutes=1)


def _fresh_stamp(value):
    """Whether ``value`` is a start stamp written just now: UTC to the microsecond, with an offset."""
    assert isinstance(value, str) and value, f"no stamp: {value!r}"
    instant = datetime.fromisoformat(value)
    assert instant.tzinfo is not None
    assert value == instant.isoformat(timespec="microseconds")
    return abs(datetime.now(timezone.utc) - instant) < STAMP_SLACK


@pytest.mark.unit
class TestAnAcceptedStartIsStamped:
    def test_a_start_edh_answers_is_stamped_on_the_creating_row(self, monkeypatch):
        result, fakes = _run(monkeypatch, _start_row(_stamp()))
        assert result["outcome"] == "started"
        stored = _stored(fakes)
        assert stored["state"] == "CREATING"
        assert _fresh_stamp(stored["startAcceptedAt"])

    def test_a_start_whose_answer_was_lost_and_edh_then_lists_pending_is_stamped(self, monkeypatch):
        edh = FakeEdhClient(start_desktop=EdhOutcomeUnknownError("lost"), get_desktop_states=Sequence(
            [{"session_uuid": SESSION_OWN, "session_state": "pending", "instance_id": INSTANCE_ID}]))
        result, fakes = _run(monkeypatch, _start_row(_stamp()), edh=edh)
        assert result["outcome"] == "started"
        stored = _stored(fakes)
        assert stored["state"] == "CREATING"
        assert _fresh_stamp(stored["startAcceptedAt"])

    def test_a_desktop_the_read_already_finds_running_carries_no_stamp(self, monkeypatch):
        edh = FakeEdhClient(start_desktop=EdhOutcomeUnknownError("lost"), get_desktop_states=Sequence(
            [{"session_uuid": SESSION_OWN, "session_state": "running", "instance_id": INSTANCE_ID}]))
        _, fakes = _run(monkeypatch, _start_row(_stamp()), edh=edh)
        stored = _stored(fakes)
        assert stored["state"] == "ACTIVE"
        assert stored.get("startAcceptedAt") is None

    def test_an_accepted_start_clears_an_earlier_stall(self, monkeypatch):
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="microseconds")
        _, fakes = _run(monkeypatch, _start_row(_stamp(), lastError="START_STALLED", startAcceptedAt=old,
                                                startFailure={"observedAt": old}))
        stored = _stored(fakes)
        assert stored["lastError"] is None and stored["startFailure"] is None
        assert _fresh_stamp(stored["startAcceptedAt"]) and stored["startAcceptedAt"] != old


@pytest.mark.unit
class TestNoOtherWriteStampsIt:
    def test_a_refused_start_writes_no_stamp(self, monkeypatch):
        edh = FakeEdhClient(start_desktop=EdhCapacityError("no capacity", status=500, body_text=ICE_TEXT))
        _, fakes = _run(monkeypatch, _start_row(_stamp()), edh=edh)
        stored = _stored(fakes)
        assert stored["state"] == "STOPPED"
        assert stored.get("startAcceptedAt") is None

    def test_a_new_launch_never_writes_it(self, monkeypatch):
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(
            find_desktop_by_name=Sequence(None, desktop(state="pending", connection_string=""))))
        fakes.module.lambda_handler(worker_event(row), {})
        written = updates(fakes)
        assert written, "the launch wrote nothing"
        assert not any("startAcceptedAt" in attributes for attributes in written)
