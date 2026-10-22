# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The admin session views carry the launch-failure reason VAMS captured from EDH (in-dev D8).

Each instance item, in the list and in the stale view, carries ``edhFailure`` from the joined VAMS row,
or null when the row holds none or no row joins; the row's read stamp is never returned.
"""

import pytest

from backend.tests.handlers.addon.edh.edhAdminTestSupport import (
    INSTANCES_PATH, STALE_PATH, FakeEdhClient, edh_desktop, install_admin_fakes, make_event, response_body,
    session_row,
)
import backend.backend.handlers.addon.edh.edhAdminService as service

FAILURE = {"event": "placement_failed", "errorCode": "InsufficientInstanceCapacity",
           "operation": "CreateCapacityReservation", "observedAt": "2026-10-22T15:23:00+00:00"}
READ_AT = "2026-10-22T15:23:00+00:00"


def _items(response):
    return {item["sessionId"]: item for item in response_body(response)["Items"]}


@pytest.mark.unit
class TestTheAdminItemCarriesTheReason:
    def test_the_list_carries_the_reason_of_the_joined_row_and_null_otherwise(self, monkeypatch):
        rows = [
            session_row(bootstrapId="b-1", sessionId="s-1", edhUid="vowner1", state="ORPHANED", edhState="error",
                        lastError="CAPACITY_UNAVAILABLE", edhFailure=dict(FAILURE), edhFailureReadAt=READ_AT),
            session_row(bootstrapId="b-2", sessionId="s-2", edhUid="vowner2"),
        ]
        edh = FakeEdhClient(list_all_desktops=[edh_desktop("s-1", "vowner1", "error"),
                                               edh_desktop("s-2", "vowner2", "running"),
                                               edh_desktop("s-3", "vowner3", "running")])
        fakes = install_admin_fakes(monkeypatch, edh=edh, rows=rows)

        response = fakes.module.lambda_handler(make_event("GET", INSTANCES_PATH), {})

        assert response["statusCode"] == 200
        items = _items(response)
        assert items["s-1"]["edhFailure"] == FAILURE
        assert items["s-1"]["lastError"] == "CAPACITY_UNAVAILABLE"
        assert items["s-2"]["edhFailure"] is None
        assert items["s-3"]["edhFailure"] is None
        assert all("edhFailureReadAt" not in item for item in items.values())
        assert len(items) == 3

    def test_the_stale_view_carries_the_reason_of_a_row_edh_no_longer_lists(self, monkeypatch):
        rows = [session_row(bootstrapId="b-1", sessionId="s-1", edhUid="vowner1", state="ORPHANED",
                            edhState="error", lastError="CAPACITY_UNAVAILABLE", edhFailure=dict(FAILURE),
                            edhFailureReadAt=READ_AT)]
        fakes = install_admin_fakes(monkeypatch, edh=FakeEdhClient(list_all_desktops=[]), rows=rows)

        response = fakes.module.lambda_handler(make_event("GET", STALE_PATH), {})

        assert response["statusCode"] == 200
        item = _items(response)["s-1"]
        assert item["staleReason"] == "missingInEdh"
        assert item["edhFailure"] == FAILURE
        assert "edhFailureReadAt" not in item

    @pytest.mark.parametrize("stored", ["placement_failed", ["placement_failed"], 7],
                             ids=["a string", "a list", "a number"])
    def test_a_stored_value_that_is_not_a_map_is_null(self, stored):
        item = service._instance_item(None, session_row(edhFailure=stored))
        assert item["edhFailure"] is None

    def test_only_the_contract_keys_of_a_stored_map_are_returned(self):
        item = service._instance_item(None, session_row(edhFailure={**FAILURE, "subStatus": "EDH text"}))
        assert item["edhFailure"] == FAILURE
