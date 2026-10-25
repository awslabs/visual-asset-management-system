# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The admin instance item carries ``activity``, derived as the workstation record derives it with EDH's live
state and connectability laid over the joined row, and the row's ``startFailure`` (in-dev D10, D11); an admin stop
stamps the row as the owner's stop does."""

from datetime import datetime, timedelta, timezone

import pytest

import backend.backend.handlers.addon.edh.edhAdminService as service
from backend.backend.handlers.addon.edh.common.edhSessionState import start_stamp
from backend.backend.models.addon.edh import WorkstationRecordModel, start_failure_projection
from backend.tests.handlers.addon.edh.edhAdminTestSupport import edh_desktop, session_row

FAILURE = {"errorCode": "InsufficientInstanceCapacity", "availabilityZone": "us-west-2b",
           "observedAt": "2026-10-23T15:19:30+00:00"}


@pytest.mark.unit
class TestTheAdminItemCarriesActivity:
    def test_a_start_in_progress_reads_starting(self):
        fresh = start_stamp(datetime.now(timezone.utc) - timedelta(seconds=20))
        row = session_row(bootstrapId="b-1", sessionId="s-1", edhUid="vowner1", state="STOPPED", edhState="stopped",
                          activity="starting", startRequestedAt=fresh)
        item = service._instance_item(edh_desktop("s-1", "vowner1", "stopped"), row)
        assert item["state"] == "STOPPED" and item["activity"] == "starting"

    def test_a_desktop_edh_lists_stopping_reads_stopping(self):
        row = session_row(bootstrapId="b-1", sessionId="s-1", edhUid="vowner1", state="ACTIVE", edhState="running")
        item = service._instance_item(edh_desktop("s-1", "vowner1", "stopping"), row)
        assert item["activity"] == "stopping"

    def test_a_running_desktop_without_a_row_does_nothing(self):
        item = service._instance_item(edh_desktop("s-3", "vowner3", "running"), None)
        assert item["activity"] is None and item["startFailure"] is None

    def test_a_stopped_rows_start_failure_is_returned(self):
        row = session_row(bootstrapId="b-1", sessionId="s-1", edhUid="vowner1", state="STOPPED", edhState="stopped",
                          lastError="CAPACITY_UNAVAILABLE", startFailure={**FAILURE, "edhText": "not returned"})
        item = service._instance_item(edh_desktop("s-1", "vowner1", "stopped"), row)
        assert item["lastError"] == "CAPACITY_UNAVAILABLE"
        assert item["startFailure"] == FAILURE


@pytest.mark.unit
class TestTheRecordModel:
    @pytest.mark.parametrize("stored", ["InsufficientInstanceCapacity", [FAILURE], {"errorCode": "X"}, 7])
    def test_a_start_failure_that_is_not_a_stamped_map_is_null(self, stored):
        assert start_failure_projection(stored) is None

    def test_only_the_contract_keys_are_returned(self):
        assert start_failure_projection({**FAILURE, "operation": "StartInstances"}) == FAILURE

    def test_an_activity_outside_the_vocabulary_is_refused(self):
        with pytest.raises(ValueError):
            WorkstationRecordModel(bootstrapId="b", userId="u", edhUid="e", state="STOPPED", name="n", imageId="1",
                                   osFamily="linux", instanceType="g5.xlarge", createdAt="c", updatedAt="u",
                                   activity="rebooting")
