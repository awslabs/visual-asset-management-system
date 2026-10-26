# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The EDH capacity reservation tools: what reaches APIClient, what comes back, and what the
docstrings and README say about them. The gate each tool sits behind is held by `test_edh_tools.py`
(EDH_READ_TOOLS, EDH_WRITE_TOOLS, EDH_DESTRUCTIVE_TOOLS).
"""

import json
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from vamscli.addon_constants import EDH_WORKSTATIONS_DISABLED_MESSAGE
from vamscli.utils.addon.edh_exceptions import EdhReservationFullError, EdhReservationNotPermittedError

from vams_mcp import server
from vams_mcp.addon_tools import edh
from vams_mcp.client import VamsClient
from vams_mcp.config import Config

README_TEXT = (Path(server.__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
# The capacity-display fixture the web and CLI tests read (feature #536, D17/D18/D19).
CAPACITY_CASES = json.loads(
    (Path(server.__file__).resolve().parents[3] / "web/src/addons/edh/capacityDisplayCases.json").read_text(
        encoding="utf-8"))

CR_ID = "cr-0123456789abcdef0"
VIEW = {"capacityReservationId": CR_ID, "label": "Render pool", "fallbackToOnDemand": True,
        "enabled": True, "instanceType": "g5.16xlarge", "availabilityZone": "us-east-1a",
        "instancePlatform": "Linux/UNIX", "state": "active", "totalInstanceCount": 4,
        "availableInstanceCount": 3, "instanceMatchCriteria": "targeted", "endDate": None,
        "reservationType": "default"}


class RecordingMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def record(fn):
            self.tools[fn.__name__] = fn
            return fn

        return record


@pytest.fixture
def client():
    mock = MagicMock()
    mock.has_feature.return_value = True
    mock.require_feature = types.MethodType(VamsClient.require_feature, mock)
    return mock


@pytest.fixture
def tools(client):
    mcp = RecordingMcp()
    edh.register(mcp, client, Config(enable_writes=True, enable_destructive=True), server.tool_result)
    return mcp.tools


def test_the_catalog_tool_forwards_its_filters_and_returns_the_body(client, tools):
    client.api.list_edh_catalog_capacity_reservations.return_value = {"Items": [VIEW]}
    assert tools["list_edh_catalog_capacity_reservations"]() == {"Items": [VIEW]}
    client.api.list_edh_catalog_capacity_reservations.assert_called_with(instance_type=None, os_family=None)

    tools["list_edh_catalog_capacity_reservations"](instance_type="g5.16xlarge", os_family="linux")
    client.api.list_edh_catalog_capacity_reservations.assert_called_with(
        instance_type="g5.16xlarge", os_family="linux")


def test_the_admin_list_tool_returns_the_body(client, tools):
    client.api.list_edh_admin_capacity_reservations.return_value = {"Items": [VIEW]}
    assert tools["list_edh_admin_capacity_reservations"]() == {"Items": [VIEW]}


def test_register_sends_the_id_the_label_and_only_the_switches_given(client, tools):
    client.api.register_edh_capacity_reservation.return_value = VIEW
    assert tools["register_edh_capacity_reservation"](CR_ID, "Render pool") == VIEW
    client.api.register_edh_capacity_reservation.assert_called_with(
        {"capacityReservationId": CR_ID, "label": "Render pool"})

    tools["register_edh_capacity_reservation"](CR_ID, "Render pool", fallback_to_on_demand=False,
                                               enabled=False)
    client.api.register_edh_capacity_reservation.assert_called_with(
        {"capacityReservationId": CR_ID, "label": "Render pool", "fallbackToOnDemand": False,
         "enabled": False})


def test_update_sends_a_partial_patch_and_refuses_an_empty_one(client, tools):
    client.api.update_edh_capacity_reservation.return_value = VIEW
    tools["update_edh_capacity_reservation"](CR_ID, enabled=False)
    client.api.update_edh_capacity_reservation.assert_called_with(CR_ID, {"enabled": False})

    tools["update_edh_capacity_reservation"](CR_ID, label="Render farm", fallback_to_on_demand=True)
    client.api.update_edh_capacity_reservation.assert_called_with(
        CR_ID, {"label": "Render farm", "fallbackToOnDemand": True})

    client.api.update_edh_capacity_reservation.reset_mock()
    answer = tools["update_edh_capacity_reservation"](CR_ID)
    assert "error" in answer and "Pass at least one of" in answer["error"]
    assert not client.api.update_edh_capacity_reservation.called


def test_unregister_forwards_the_id_and_returns_the_body(client, tools):
    client.api.unregister_edh_capacity_reservation.return_value = {"capacityReservationId": CR_ID}
    assert tools["unregister_edh_capacity_reservation"](CR_ID) == {"capacityReservationId": CR_ID}
    client.api.unregister_edh_capacity_reservation.assert_called_with(CR_ID)


def test_create_forwards_the_reservation_only_when_given(client, tools):
    client.api.create_edh_workstation.return_value = {"state": "CREATING"}
    tools["create_edh_workstation"]("12", "g5.16xlarge", "Render desk", 200, "linux",
                                    bootstrap_id="b-1", capacity_reservation_id=CR_ID)
    assert client.api.create_edh_workstation.call_args.args[0]["capacityReservationId"] == CR_ID

    tools["create_edh_workstation"]("12", "g5.16xlarge", "Render desk", 200, "linux", bootstrap_id="b-1")
    assert "capacityReservationId" not in client.api.create_edh_workstation.call_args.args[0]


def test_a_full_reservation_is_returned_as_its_error_type_with_the_code(client, tools):
    client.api.create_edh_workstation.side_effect = EdhReservationFullError(
        "Workstation launch refused (409): The capacity reservation has no free capacity")
    answer = tools["create_edh_workstation"]("12", "g5.16xlarge", "Render desk", 200, "linux",
                                             capacity_reservation_id=CR_ID)
    assert answer["error_type"] == "EdhReservationFullError"
    assert answer["code"] == "RESERVATION_FULL"


def test_the_catalog_tool_passes_launch_allowed_and_the_edh_subnet_field_unchanged(client, tools):
    items = [case["view"] for case in CAPACITY_CASES["catalogOptions"]]
    client.api.list_edh_catalog_capacity_reservations.return_value = {"Items": items}
    answer = tools["list_edh_catalog_capacity_reservations"]()
    assert answer == {"Items": items}
    assert {item["launchAllowed"] for item in answer["Items"]} == {True, False}
    assert {item["edhSubnetAvailable"] for item in answer["Items"]} == {True, False, None}


def test_the_workstation_tools_pass_the_fallback_reason_unchanged(client, tools):
    records = [case["record"] for case in CAPACITY_CASES["records"]]
    client.api.list_edh_workstations.return_value = {"Items": records}
    assert tools["list_edh_workstations"]() == {"Items": records}
    assert "ended" in {r.get("capacityReservationFallbackReason") for r in records}


def test_a_view_only_reservation_is_returned_as_its_error_type_with_the_code(client, tools):
    client.api.create_edh_workstation.side_effect = EdhReservationNotPermittedError(
        "Access forbidden. Your role may view this capacity reservation but not launch into it",
        code="RESERVATION_NOT_PERMITTED")
    answer = tools["create_edh_workstation"]("12", "g5.16xlarge", "Render desk", 200, "linux",
                                             capacity_reservation_id=CR_ID)
    assert answer["error_type"] == "EdhReservationNotPermittedError"
    assert answer["code"] == "RESERVATION_NOT_PERMITTED"
    assert "may view this capacity reservation but not launch into it" in answer["error"]


@pytest.mark.parametrize("tool", [
    "list_edh_catalog_capacity_reservations", "list_edh_admin_capacity_reservations",
    "register_edh_capacity_reservation", "update_edh_capacity_reservation",
    "unregister_edh_capacity_reservation",
])
def test_each_tool_answers_the_disabled_sentence_before_any_request(client, tools, tool):
    client.has_feature.return_value = False
    fn = tools[tool]
    args = {"register_edh_capacity_reservation": (CR_ID, "x"),
            "update_edh_capacity_reservation": (CR_ID,),
            "unregister_edh_capacity_reservation": (CR_ID,)}.get(tool, ())
    answer = fn(*args)
    assert EDH_WORKSTATIONS_DISABLED_MESSAGE in answer["error"]
    assert not client.api.method_calls


@pytest.mark.parametrize("tool,fragment", [
    ("list_edh_catalog_capacity_reservations", "capacity_reservation_id"),
    ("list_edh_catalog_capacity_reservations", "on-demand"),
    ("create_edh_workstation", "RESERVATION_FULL"),
    ("create_edh_workstation", "capacity_reservation_id"),
    ("create_edh_workstation", "RESERVATION_NOT_PERMITTED"),
    ("create_edh_workstation", "EdhReservationNotPermittedError"),
    ("list_edh_catalog_capacity_reservations", "launchAllowed"),
    ("list_edh_catalog_capacity_reservations", "edhSubnetAvailable"),
    ("list_edh_admin_capacity_reservations", "edhSubnetAvailable"),
    ("list_edh_workstations", "RESERVATION_ENDED"),
    ("list_edh_workstations", "capacityReservationFallbackReason"),
    ("start_edh_workstation", "RESERVATION_ENDED"),
    ("register_edh_capacity_reservation", "never changes the reservation in EC2"),
    ("unregister_edh_capacity_reservation", "EC2 reservation itself is not changed"),
])
def test_the_docstrings_state_the_contract(tools, tool, fragment):
    assert fragment in " ".join((tools[tool].__doc__ or "").split())


def test_the_readme_states_the_capacity_reservation_tools():
    for fragment in ("`list_edh_catalog_capacity_reservations`", "`capacity_reservation_id`",
                     "`RESERVATION_FULL`", "`unregister_edh_capacity_reservation`",
                     "`RESERVATION_NOT_PERMITTED`", "`EdhReservationNotPermittedError`", "`launchAllowed`",
                     "`edhSubnetAvailable`", "`RESERVATION_ENDED`", "`capacityReservationFallbackReason`"):
        assert fragment in README_TEXT
