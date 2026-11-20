# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""A workstation that has run which EDH reports in error, in the MCP tools (#1731): the workstation tools return
its state and `connectionUnavailableReason` DESKTOP_ERROR unchanged, a refused connect or start comes back as
`EdhDesktopErrorError` with `code` DESKTOP_ERROR and the sentence, and the docstrings and README say so.
"""

import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from vamscli.utils.addon.edh_exceptions import EdhDesktopErrorError

from vams_mcp import server
from vams_mcp.addon_tools import edh
from vams_mcp.client import VamsClient
from vams_mcp.config import Config

README_TEXT = (Path(server.__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
SENTENCE = ("The workstation's remote display is not responding; its disk and data are kept. Wait a few minutes, "
            "or stop it, then start it again, to reconnect.")
IN_ERROR = {"bootstrapId": "b-1", "sessionId": "s-1", "state": "ACTIVE", "edhState": "error", "activity": None,
            "connectionAvailable": False, "connectionUnavailableReason": "DESKTOP_ERROR"}


class RecordingMcp:
    def __init__(self):
        self.tools = {}

    def tool(self, **_kwargs):
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


def test_the_workstation_tools_return_the_state_and_reason_unchanged(client, tools):
    client.api.list_edh_workstations.return_value = {"Items": [IN_ERROR]}
    client.api.get_edh_workstation.return_value = IN_ERROR
    assert tools["list_edh_workstations"]() == {"Items": [IN_ERROR]}
    assert tools["get_edh_workstation"]("s-1") == IN_ERROR


@pytest.mark.parametrize("tool, call", [("get_edh_workstation_connect", "get_edh_workstation_connection"),
                                        ("start_edh_workstation", "start_edh_workstation")])
def test_a_refusal_is_returned_as_its_error_type_with_the_code(client, tools, tool, call):
    message = f"Workstation refused (409): {SENTENCE}"
    getattr(client.api, call).side_effect = EdhDesktopErrorError(message)
    answer = tools[tool]("s-1")
    assert answer == {"error": message, "error_type": "EdhDesktopErrorError", "code": "DESKTOP_ERROR"}


@pytest.mark.parametrize("tool,fragment", [
    ("list_edh_workstations", "DESKTOP_ERROR"),
    ("get_edh_workstation", "DESKTOP_ERROR"),
    ("get_edh_workstation_connect", "DESKTOP_ERROR"),
    ("get_edh_workstation_connect", "EdhDesktopErrorError"),
])
def test_the_docstrings_state_the_contract(tools, tool, fragment):
    assert fragment in " ".join((tools[tool].__doc__ or "").split())


def test_the_readme_states_the_reason():
    for fragment in ("`DESKTOP_ERROR`", "`EdhDesktopErrorError`"):
        assert fragment in README_TEXT
