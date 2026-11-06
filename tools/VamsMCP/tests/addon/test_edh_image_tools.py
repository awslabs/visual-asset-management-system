# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The image a workstation runs, in the MCP tools (feature #536, D14): the workstation tools pass the
image fields (`imageName`, `recipeId`, `recipeDisplayName`, `recipeVersion`, `latestRecipeVersion`,
`imageLatest`) through unchanged, and their descriptions and the README name them. The gate each tool
sits behind is held by `test_edh_tools.py`.
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

OLDER_BUILD = {"bootstrapId": "b-1", "sessionId": "s-1", "state": "ACTIVE", "imageId": "31",
               "imageName": "isaacsim-6-2-v13", "recipeId": "isaacsim",
               "recipeDisplayName": "Isaac Sim 6.2", "recipeVersion": 13, "latestRecipeVersion": 14,
               "imageLatest": False}
BASELINE = {"bootstrapId": "b-2", "sessionId": "s-2", "state": "STOPPED", "imageId": "12",
            "imageName": "Ubuntu 22 DCV", "recipeId": None, "recipeDisplayName": None,
            "recipeVersion": None, "latestRecipeVersion": None, "imageLatest": None}

IMAGE_FIELDS = ("`imageName`", "`recipeDisplayName`", "`recipeVersion`", "`latestRecipeVersion`",
                "`imageLatest`")

# Every tool that returns a workstation record or an admin session item.
WORKSTATION_TOOLS = (
    "list_edh_workstations", "get_edh_workstation", "list_edh_admin_instances",
    "create_edh_workstation", "start_edh_workstation", "stop_edh_workstation",
    "renew_edh_workstation", "delete_edh_workstation",
)


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


def _sentences(text):
    return [sentence for sentence in text.split(". ") if sentence]


@pytest.mark.parametrize("tool,method,args", [
    ("list_edh_workstations", "list_edh_workstations", ()),
    ("get_edh_workstation", "get_edh_workstation", ("s-1",)),
    ("list_edh_admin_instances", "list_edh_admin_instances", ()),
    ("start_edh_workstation", "start_edh_workstation", ("s-1",)),
    ("stop_edh_workstation", "stop_edh_workstation", ("s-1",)),
    ("renew_edh_workstation", "renew_edh_workstation", ("s-1",)),
    ("delete_edh_workstation", "delete_edh_workstation", ("s-1",)),
])
def test_the_workstation_tools_pass_the_image_fields_through(client, tools, tool, method, args):
    body = {"Items": [OLDER_BUILD, BASELINE]} if tool.startswith("list") else dict(OLDER_BUILD)
    getattr(client.api, method).return_value = body
    assert tools[tool](*args) == body


def test_create_passes_the_launch_snapshot_through(client, tools):
    accepted = {**OLDER_BUILD, "state": "CREATING", "sessionId": None, "imageLatest": True,
                "recipeVersion": 14, "sessionBootstrap": "provisioned"}
    client.api.create_edh_workstation.return_value = dict(accepted)
    assert tools["create_edh_workstation"]("36", "g6e.4xlarge", "Sim box", 200, "linux") == accepted


@pytest.mark.parametrize("tool", WORKSTATION_TOOLS)
def test_the_workstation_tool_descriptions_name_the_image_fields_in_one_sentence(tools, tool):
    doc = _doc(tools, tool)
    assert any(all(field in sentence for field in IMAGE_FIELDS) for sentence in _sentences(doc)), tool


@pytest.mark.parametrize("fragment", [
    "`recipeId`", "null for a baseline image", "newest AVAILABLE build", "launch a new workstation",
])
def test_the_list_description_spells_out_the_image_fields(tools, fragment):
    assert fragment in _doc(tools, "list_edh_workstations")


def test_the_readme_names_the_image_fields():
    text = " ".join(README_TEXT.split())
    for fragment in IMAGE_FIELDS + ("`recipeId`",):
        assert fragment in text, fragment
    assert "launch a new workstation" in text
