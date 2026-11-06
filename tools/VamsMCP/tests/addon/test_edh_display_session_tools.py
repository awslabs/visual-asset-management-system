# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""A running workstation whose remote display session has ended, in the MCP tools (feature #536, D20):
the workstation tools return `connectionUnavailableReason` unchanged, a refused connect or Windows
sign-in comes back as `EdhDisplaySessionEndedError` with `code` DISPLAY_SESSION_ENDED and the sentence,
and the docstrings and README say so.
"""

import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from vamscli.utils.addon.edh_exceptions import EdhDisplaySessionEndedError

from vams_mcp import server
from vams_mcp.addon_tools import edh
from vams_mcp.client import VamsClient
from vams_mcp.config import Config

README_TEXT = (Path(server.__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
SENTENCE = ("The workstation is running but its remote display session has ended. "
            "Stop it, then start it again, to reconnect.")
ENDED = {"bootstrapId": "b-1", "sessionId": "s-1", "state": "ACTIVE", "edhState": "running", "activity": None,
         "connectionAvailable": False, "connectionUnavailableReason": "DISPLAY_SESSION_ENDED"}


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


def test_the_workstation_tools_return_the_reason_unchanged(client, tools):
    client.api.list_edh_workstations.return_value = {"Items": [ENDED]}
    client.api.get_edh_workstation.return_value = ENDED
    assert tools["list_edh_workstations"]() == {"Items": [ENDED]}
    assert tools["get_edh_workstation"]("s-1") == ENDED


def test_a_refused_connect_is_returned_as_its_error_type_with_the_code(client, tools):
    message = f"Workstation connection refused (409): {SENTENCE}"
    client.api.get_edh_workstation_connection.side_effect = EdhDisplaySessionEndedError(message)
    answer = tools["get_edh_workstation_connect"]("s-1")
    assert answer == {"error": message, "error_type": "EdhDisplaySessionEndedError", "code": "DISPLAY_SESSION_ENDED"}


@pytest.mark.parametrize("tool,fragment", [
    ("list_edh_workstations", "connectionUnavailableReason"),
    ("list_edh_workstations", "DISPLAY_SESSION_ENDED"),
    ("get_edh_workstation", "connectionUnavailableReason"),
    ("get_edh_workstation_connect", "DISPLAY_SESSION_ENDED"),
    ("get_edh_workstation_connect", "EdhDisplaySessionEndedError"),
    ("get_edh_workstation_connect", "stop it, then start it again"),
])
def test_the_docstrings_state_the_contract(tools, tool, fragment):
    assert fragment in " ".join((tools[tool].__doc__ or "").split())


def test_the_readme_states_the_reason():
    for fragment in ("`connectionUnavailableReason`", "`DISPLAY_SESSION_ENDED`", "`EdhDisplaySessionEndedError`"):
        assert fragment in README_TEXT
