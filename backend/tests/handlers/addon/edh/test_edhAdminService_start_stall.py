# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The admin Sessions view and a start EDH accepted but never finished (in-dev D16): the instance item of a row the
sweep recorded ``START_STALLED`` carries the code and its ``startFailure`` and reads no ``activity``, as the
workstation record does, and an administrator's stop clears the code, the failure and the stamp as the owner's
stop does."""

from datetime import datetime, timedelta, timezone

import pytest

import backend.backend.handlers.addon.edh.edhAdminService as service
from backend.backend.handlers.addon.edh.common.edhSessionState import start_stamp
from backend.tests.handlers.addon.edh.edhAdminTestSupport import (
    FakeEdhClient, edh_desktop, install_admin_fakes, instance_path, make_event, session_row,
)
from backend.tests.handlers.addon.edh.edhRowWriteTestSupport import written_values

FAILURE = {"observedAt": "2026-10-24T15:30:00+00:00"}


def _stalled_row(**fields):
    accepted = start_stamp(datetime.now(timezone.utc) - timedelta(hours=1))
    return session_row(bootstrapId="b-1", sessionId="s-1", edhUid="vowner1", state="CREATING", edhState="pending",
                       connectionAvailable=False, lastError="START_STALLED", startFailure=dict(FAILURE),
                       startAcceptedAt=accepted, **fields)


@pytest.mark.unit
class TestTheAdminItemOfAStalledStart:
    @pytest.mark.parametrize("edh_state", ["pending", "placing"])
    def test_it_carries_the_code_and_the_failure_and_reads_no_activity(self, edh_state):
        item = service._instance_item(edh_desktop("s-1", "vowner1", edh_state), _stalled_row())
        assert item["state"] == "CREATING"
        assert item["lastError"] == "START_STALLED"
        assert item["startFailure"] == FAILURE
        assert item["activity"] is None
        assert "startAcceptedAt" not in item

    def test_a_start_that_has_not_stalled_still_reads_starting(self):
        row = session_row(bootstrapId="b-1", sessionId="s-1", edhUid="vowner1", state="CREATING",
                          edhState="pending", connectionAvailable=False)
        item = service._instance_item(edh_desktop("s-1", "vowner1", "pending"), row)
        assert item["activity"] == "starting" and item["lastError"] is None


@pytest.mark.unit
class TestTheAdminStop:
    def test_an_admin_stop_clears_the_stall_its_failure_and_the_stamp(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, rows=[_stalled_row()], edh=FakeEdhClient(
            list_all_desktops=[edh_desktop("s-1", "vowner1", "pending")]))
        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", "stop"), path_params={"sessionId": "s-1"}), {})
        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("stop_desktop") == [("vowner1", "s-1")]
        assert "STOPPED" in written_values(fakes, "state")
        for attribute in ("lastError", "startFailure", "startAcceptedAt"):
            values = written_values(fakes, attribute)
            assert values, f"{attribute} was not cleared"
            assert all(value is None for value in values)

    def test_an_admin_deactivate_drops_the_stamp(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, rows=[_stalled_row()], edh=FakeEdhClient(
            list_all_desktops=[edh_desktop("s-1", "vowner1", "pending")]))
        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", "deactivate"), path_params={"sessionId": "s-1"}), {})
        assert response["statusCode"] == 200
        assert "DELETED" in written_values(fakes, "state")
        values = written_values(fakes, "startAcceptedAt")
        assert values and all(value is None for value in values)
