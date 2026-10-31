"""System (deployment-owned) pipelines and workflows, as the MCP tools present them.

Two things an agent relies on: every write and destructive tool that a system record refuses says so
in its docstring, and a refusal reaches the agent as the API's own message. The second is driven
through the real `APIClient` methods against a mocked HTTP 400, so the CLI's error mapping is part of
what is checked, not a mock that returns the expected string.
"""

import importlib
import os
from unittest.mock import MagicMock

import pytest
import requests

from vams_mcp import server as server_module
from vamscli.utils.api_client import APIClient

from .test_server_tools import _docstring_of

PIPELINE_TOOLS = (
    "update_pipeline",
    "create_pipeline_template",
    "update_pipeline_template",
    "set_pipeline_template_tag_schema",
    "archive_pipeline",
    "unarchive_pipeline",
    "delete_pipeline_template",
)
WORKFLOW_TOOLS = (
    "update_workflow",
    "set_workflow_trigger",
    "archive_workflow",
    "unarchive_workflow",
    "delete_workflow_trigger",
)

GATE_VARS = ("VAMS_ENABLE_WRITES", "VAMS_ENABLE_DESTRUCTIVE")


@pytest.mark.parametrize("tool", PIPELINE_TOOLS)
def test_pipeline_tool_docstrings_state_the_system_rule(tool):
    doc = _docstring_of(tool)
    assert "System pipelines (isSystem) are read-only" in doc
    assert "400" in doc


@pytest.mark.parametrize("tool", PIPELINE_TOOLS)
def test_pipeline_tool_docstrings_name_every_editable_template_field(tool):
    doc = _docstring_of(tool)
    for field in ("configBody", "tagSchema", "webFormJson"):
        assert field in doc


@pytest.mark.parametrize("tool", WORKFLOW_TOOLS)
def test_workflow_tool_docstrings_state_the_system_rule(tool):
    doc = _docstring_of(tool)
    assert "system workflow" in doc.lower() and "(isSystem)" in doc
    assert "400" in doc


@pytest.mark.parametrize("tool", [t for t in WORKFLOW_TOOLS if t != "set_workflow_trigger"])
def test_workflow_tool_docstrings_name_both_toggles(tool):
    doc = _docstring_of(tool)
    assert 'update_workflow(..., {"enabled": false})' in doc
    assert 'set_workflow_trigger(..., {"enabled": false})' in doc


# --- A refusal reaches the agent verbatim -----------------------------------------------------------


@pytest.fixture
def enabled_server():
    """Reimport `vams_mcp.server` with both gates on, then restore the default-gate module (the same
    pattern as test_gated_tools.py, whose docstring explains why it manages os.environ directly)."""
    for name in GATE_VARS:
        os.environ[name] = "true"
    importlib.reload(server_module)
    try:
        assert server_module.CONFIG.enable_writes is True
        assert server_module.CONFIG.enable_destructive is True
        yield server_module
    finally:
        for name in GATE_VARS:
            os.environ.pop(name, None)
        importlib.reload(server_module)


def _http_400(message):
    response = MagicMock()
    response.status_code = 400
    response.content = b"{}"
    response.json.return_value = {"message": message}
    return requests.exceptions.HTTPError(response=response)


def _client_answering(message):
    profile_manager = MagicMock()
    profile_manager.is_override_token.return_value = False
    profile_manager.load_auth_profile.return_value = {}
    api = APIClient("https://example.com/api", profile_manager=profile_manager)
    api._pwe_request = MagicMock(side_effect=_http_400(message))
    return api


@pytest.fixture
def refusing(enabled_server, monkeypatch):
    """The reloaded server whose CLIENT.api is a real APIClient that answers every call with a 400."""

    def install(message):
        client = MagicMock()
        client.unwrap_message = enabled_server.VamsClient.unwrap_message
        client.config = enabled_server.CONFIG
        client.api = _client_answering(message)
        monkeypatch.setattr(enabled_server, "CLIENT", client)
        return enabled_server

    return install


PIPELINE_REFUSAL = 'System pipelines are read-only except "enabled"; "description" cannot be changed.'
PIPELINE_ARCHIVE = ("System pipelines cannot be archived or restored through the API; the deployment "
                    "owns them.")
TEMPLATE_REFUSAL = ('Templates of system pipelines are read-only except "configBody", "tagSchema" and '
                    '"webFormJson"; "templateName" differs from the stored value.')
TEMPLATE_LOCKED = "Templates of system pipelines cannot be added or deleted."
WORKFLOW_REFUSAL = 'System workflows are read-only except "enabled"; "workflowName" cannot be changed.'
WORKFLOW_ARCHIVE = ("System workflows cannot be archived or restored through the API; the deployment "
                    "owns them.")
TRIGGER_REFUSAL = ('Triggers of system workflows are read-only except "enabled"; "inputFileFilters" '
                   'differs from the stored trigger.')
TRIGGER_LOCKED = "Triggers of system workflows cannot be added or deleted."


@pytest.mark.parametrize("message,call", [
    (PIPELINE_REFUSAL, lambda s: s.update_pipeline("GLOBAL", "p", {"description": "x"})),
    (PIPELINE_ARCHIVE, lambda s: s.unarchive_pipeline("GLOBAL", "p")),
    (TEMPLATE_REFUSAL, lambda s: s.update_pipeline_template("GLOBAL", "p", "t", {"templateName": "x"})),
    (WORKFLOW_REFUSAL, lambda s: s.update_workflow("GLOBAL", "w", {"workflowName": "x"})),
    (WORKFLOW_ARCHIVE, lambda s: s.unarchive_workflow("GLOBAL", "w")),
    (TRIGGER_REFUSAL, lambda s: s.set_workflow_trigger("GLOBAL", "w", "fileUpload",
                                                        {"inputFileFilters": {"allow": ["*"]}})),
], ids=["update_pipeline", "unarchive_pipeline", "update_pipeline_template", "update_workflow",
        "unarchive_workflow", "set_workflow_trigger"])
def test_a_400_refusal_is_returned_verbatim(refusing, message, call):
    result = call(refusing(message))
    assert result["error"] == message


@pytest.mark.parametrize("message,call", [
    (PIPELINE_ARCHIVE, lambda s: s.archive_pipeline("GLOBAL", "p")),
    (TEMPLATE_LOCKED, lambda s: s.create_pipeline_template("GLOBAL", "p", {"templateName": "x"})),
    (TEMPLATE_LOCKED, lambda s: s.delete_pipeline_template("GLOBAL", "p", "t")),
    (WORKFLOW_ARCHIVE, lambda s: s.archive_workflow("GLOBAL", "w")),
    (TRIGGER_LOCKED, lambda s: s.delete_workflow_trigger("GLOBAL", "w", "fileUpload")),
], ids=["archive_pipeline", "create_pipeline_template", "delete_pipeline_template",
        "archive_workflow", "delete_workflow_trigger"])
def test_a_400_refusal_on_a_path_without_a_400_branch_still_carries_the_message(refusing, message, call):
    # These client methods map a 400 to a generic error whose text is "Failed to ...: <message>", so
    # the API's message reaches the agent whole, after a short prefix.
    result = call(refusing(message))
    assert message in result["error"]
