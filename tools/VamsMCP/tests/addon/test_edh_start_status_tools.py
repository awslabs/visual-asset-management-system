# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The EDH workstation start status in the MCP tools (feature #536, D10/D11, D16): the start tool hands
back the record the route accepts the start with (HTTP 202, `activity` `starting`) unchanged, the
workstation reads pass `activity` and `startFailure` through, a start EDH accepted but never finished
(`lastError` START_STALLED) among them, and the descriptions say so. The gate each tool sits behind is
held by `test_edh_tools.py`.
"""

import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vams_mcp import server
from vams_mcp.addon_tools import edh
from vams_mcp.client import VamsClient
from vams_mcp.config import Config

README_TEXT = (Path(server.__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

START_FAILURE = {"errorCode": "InsufficientInstanceCapacity", "availabilityZone": "us-west-2b",
                 "observedAt": "2026-10-23T15:19:30Z"}
ACCEPTED = {"bootstrapId": "b-1", "sessionId": "s-1", "state": "STOPPED", "edhState": "stopped",
            "activity": "starting", "lastError": None, "startFailure": None,
            "sessionBootstrap": "provisioned"}
REFUSED = {**ACCEPTED, "activity": None, "lastError": "CAPACITY_UNAVAILABLE",
           "startFailure": START_FAILURE}


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


def _doc(tools, name):
    return " ".join((tools[name].__doc__ or "").split())


def test_start_returns_the_accepted_record_unchanged(client, tools):
    client.api.start_edh_workstation.return_value = dict(ACCEPTED)
    assert tools["start_edh_workstation"]("s-1") == ACCEPTED
    client.api.start_edh_workstation.assert_called_once_with("s-1")
    # The tool starts the workstation and returns: it does not poll in the agent's turn.
    client.api.get_edh_workstation.assert_not_called()


@pytest.mark.parametrize("tool,method,args", [
    ("list_edh_workstations", "list_edh_workstations", ()),
    ("get_edh_workstation", "get_edh_workstation", ("s-1",)),
    ("list_edh_admin_instances", "list_edh_admin_instances", ()),
    ("stop_edh_workstation", "stop_edh_workstation", ("s-1",)),
])
def test_the_workstation_tools_pass_activity_and_start_failure_through(client, tools, tool, method, args):
    body = {"Items": [REFUSED, ACCEPTED]} if tool.startswith("list") else dict(REFUSED)
    getattr(client.api, method).return_value = body
    assert tools[tool](*args) == body


@pytest.mark.parametrize("fragment", [
    "HTTP 202",
    "`activity` `starting`",
    "list_edh_workstations",
    "CAPACITY_UNAVAILABLE",
    "RESERVATION_FULL",
    "START_FAILED",
    "`startFailure`",
    "never deleted",
    "compute",
    "autoApprove",
])
def test_the_start_description_states_the_asynchronous_start(tools, fragment):
    assert fragment in _doc(tools, "start_edh_workstation")


@pytest.mark.parametrize("tool", [
    "list_edh_workstations", "get_edh_workstation", "list_edh_admin_instances",
    "start_edh_workstation", "stop_edh_workstation", "renew_edh_workstation", "delete_edh_workstation",
])
def test_the_workstation_tool_descriptions_name_activity_and_start_failure(tools, tool):
    doc = _doc(tools, tool)
    assert "`activity`" in doc, tool
    assert "`startFailure`" in doc, tool


@pytest.mark.parametrize("fragment", [
    "`starting`", "`stopping`", "`errorCode`", "`availabilityZone`", "`observedAt`", "START_FAILED",
])
def test_the_list_description_spells_out_the_two_fields(tools, fragment):
    assert fragment in _doc(tools, "list_edh_workstations")


def test_the_readme_states_the_asynchronous_start():
    for fragment in ("`activity`", "`startFailure`", "HTTP 202"):
        assert fragment in README_TEXT, fragment


# --- D16: a start EDH accepts but never finishes --------------------------------------------------

STALLED = {**ACCEPTED, "state": "CREATING", "edhState": "pending", "activity": None,
           "lastError": "START_STALLED", "startFailure": {"observedAt": "2026-10-24T11:40:00Z"}}


@pytest.mark.parametrize("tool", [
    "list_edh_workstations", "get_edh_workstation", "list_edh_admin_instances",
    "start_edh_workstation", "stop_edh_workstation",
])
def test_the_workstation_tool_descriptions_name_the_stalled_start(tools, tool):
    assert "START_STALLED" in _doc(tools, tool), tool


@pytest.mark.parametrize("fragment", ["START_STALLED", "30 minutes", "CREATING", "stop_edh_workstation"])
def test_the_list_description_says_what_a_stalled_start_means(tools, fragment):
    assert fragment in _doc(tools, "list_edh_workstations")


def test_the_stop_description_accepts_a_stalled_start(tools):
    doc = _doc(tools, "stop_edh_workstation")
    assert "CREATING" in doc
    assert "START_STALLED" in doc


@pytest.mark.parametrize("tool,method,args", [
    ("list_edh_workstations", "list_edh_workstations", ()),
    ("get_edh_workstation", "get_edh_workstation", ("s-1",)),
    ("list_edh_admin_instances", "list_edh_admin_instances", ()),
])
def test_the_workstation_reads_pass_a_stalled_start_through(client, tools, tool, method, args):
    body = {"Items": [STALLED]} if tool.startswith("list") else dict(STALLED)
    getattr(client.api, method).return_value = body
    assert tools[tool](*args) == body


def test_the_readme_names_the_stalled_start():
    text = " ".join(README_TEXT.split())
    assert "`START_STALLED`" in text
    assert "30 minutes" in text
