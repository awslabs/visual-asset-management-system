# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The admin instance views read the whole session table: the scan is paged on the cursor DynamoDB
hands back and stops when the cursor is gone, so a row on a later page is joined like one on the first."""

import pytest

from backend.tests.handlers.addon.edh.edhAdminTestSupport import (
    INSTANCES_PATH, FakeEdhClient, edh_desktop, install_admin_fakes, make_event, response_body, session_row,
)
from backend.tests.pagingStub import Pager


@pytest.mark.unit
class TestSessionScanPaging:
    def test_the_session_scan_is_paged_to_exhaustion(self, monkeypatch):
        desktops = [edh_desktop("s-1", "vowner1", "running"), edh_desktop("s-6", "vowner6", "stopped")]
        fakes = install_admin_fakes(monkeypatch, edh=FakeEdhClient(list_all_desktops=desktops))
        first = session_row(bootstrapId="b-1", sessionId="s-1", userId="alice", edhUid="vowner1")
        later = session_row(bootstrapId="b-6", sessionId="s-6", userId="dave", edhUid="vowner6",
                            state="STOPPED", edhState="stopped", connectionAvailable=False)
        pager = Pager({"Items": [first], "LastEvaluatedKey": {"bootstrapId": "b-1"}},
                      {"Items": [later]}, name="sessions")
        fakes.table.scan.side_effect = pager

        response = fakes.module.lambda_handler(make_event("GET", INSTANCES_PATH), {})

        pager.assert_paged_to_exhaustion()
        items = {item["sessionId"]: item for item in response_body(response)["Items"]}
        assert items["s-1"]["userId"] == "alice" and items["s-6"]["userId"] == "dave"
