# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The EDH Workstations add-on tools (`vams_mcp/addon_tools/edh.py`): what reaches APIClient, what
comes back, which gate registers each tool, and the README's EDH section.

The tools are defined inside `edh.register(mcp, client, config, tool_result)`, so each test registers
them on a recording fake `mcp` with a mocked client and the server's own `tool_result`, and calls the
recorded functions directly; a gate setting is a `Config(...)` argument rather than a module reload.
The add-on's list routes return every row in one `{"Items": [...]}` page and no `message` envelope,
so the read tools return the body directly rather than through paginate(); the assertions pin that a
tool neither pages nor unwraps. get_edh_workstation_connect sits with the writes because it issues a
desktop credential. The layout every add-on module shares is held by `tests/test_addon_tools.py`.
"""

import ast
import importlib
import inspect
import json
import os
import re
import types
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from vamscli.addon_constants import (
    EDH_AMI_RECIPES_DISABLED_MESSAGE,
    EDH_CONTAINERS_DISABLED_MESSAGE,
    EDH_WORKSTATIONS_DISABLED_MESSAGE,
    FEATURE_EDH_AMI_RECIPES,
    FEATURE_EDH_CONTAINERS,
    FEATURE_EDH_WORKSTATIONS,
)
from vamscli.utils.addon import edh_exceptions

from vams_mcp import server
from vams_mcp.addon_tools import edh
from vams_mcp.client import VamsClient
from vams_mcp.config import Config

SOURCE_PATH = Path(edh.__file__)
SOURCE_TEXT = SOURCE_PATH.read_text(encoding="utf-8")
# The module source parsed once for every helper that walks its definitions.
_TREE = ast.parse(SOURCE_TEXT)
SERVER_TEXT = Path(server.__file__).read_text(encoding="utf-8")
README_TEXT = (Path(server.__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

GATE_VARS = ("VAMS_ENABLE_WRITES", "VAMS_ENABLE_DESTRUCTIVE")

# The contract, stated here rather than derived from the source: a test that reads the module it is
# checking cannot notice a tool that moved between the two gate blocks.
EDH_READ_TOOLS = (
    "list_edh_workstations",
    "get_edh_workstation",
    "list_edh_catalog_images",
    "list_edh_catalog_instance_types",
    "get_edh_catalog_lease",
    "list_edh_admin_images",
    "list_edh_admin_instances",
    "list_edh_stale_instances",
    "get_edh_capacity",
    "get_edh_health",
    "get_edh_admin_settings",
    "list_edh_container_catalog",
    "list_edh_container_jobs",
    "list_edh_ami_recipes",
    "get_edh_ami_recipe",
    "list_edh_ami_recipe_builds",
)

EDH_WRITE_TOOLS = (
    "get_edh_workstation_connect",
    "create_edh_workstation",
    "renew_edh_workstation",
    "start_edh_workstation",
    "stop_edh_workstation",
    "register_edh_vdi_image",
    "register_edh_simulation_image",
    "stop_edh_admin_instance",
    "update_edh_admin_settings",
    "submit_edh_container_job",
    "create_edh_ami_recipe",
    "update_edh_ami_recipe",
    "duplicate_edh_ami_recipe",
    "start_edh_ami_recipe_build",
    "register_edh_ami_recipe_build",
)

EDH_DESTRUCTIVE_TOOLS = (
    "delete_edh_workstation",
    "delete_edh_vdi_image",
    "delete_edh_simulation_image",
    "deactivate_edh_admin_instance",
    "kill_edh_container_job",
    "delete_edh_ami_recipe",
    "retire_edh_ami_recipe_build",
)

EDH_GATED_TOOLS = EDH_WRITE_TOOLS + EDH_DESTRUCTIVE_TOOLS
EDH_TOOLS = EDH_READ_TOOLS + EDH_GATED_TOOLS

# Reads whose output names no other user and carries no credential.
SAFE_EDH_READS_IN_AUTOAPPROVE = (
    "list_edh_workstations",
    "get_edh_workstation",
    "list_edh_catalog_images",
    "list_edh_catalog_instance_types",
    "get_edh_catalog_lease",
    "get_edh_capacity",
    "get_edh_health",
)

# The tools behind the add-on's second switch: a deployment can enable workstations without them.
EDH_CONTAINER_TOOLS = (
    "list_edh_container_catalog",
    "list_edh_container_jobs",
    "submit_edh_container_job",
    "kill_edh_container_job",
)

# The tools behind the add-on's third switch: AMI Recipes.
EDH_AMI_RECIPES_TOOLS = (
    "list_edh_ami_recipes",
    "get_edh_ami_recipe",
    "list_edh_ami_recipe_builds",
    "create_edh_ami_recipe",
    "update_edh_ami_recipe",
    "duplicate_edh_ami_recipe",
    "start_edh_ami_recipe_build",
    "register_edh_ami_recipe_build",
    "delete_edh_ami_recipe",
    "retire_edh_ami_recipe_build",
)

# Each switch and the CLI sentence a tool behind it answers when the switch is absent.
SWITCH_MESSAGES = {
    FEATURE_EDH_WORKSTATIONS: EDH_WORKSTATIONS_DISABLED_MESSAGE,
    FEATURE_EDH_CONTAINERS: EDH_CONTAINERS_DISABLED_MESSAGE,
    FEATURE_EDH_AMI_RECIPES: EDH_AMI_RECIPES_DISABLED_MESSAGE,
}
SWITCH_NAMES = {
    FEATURE_EDH_WORKSTATIONS: ("FEATURE_EDH_WORKSTATIONS", "EDH_WORKSTATIONS_DISABLED_MESSAGE"),
    FEATURE_EDH_CONTAINERS: ("FEATURE_EDH_CONTAINERS", "EDH_CONTAINERS_DISABLED_MESSAGE"),
    FEATURE_EDH_AMI_RECIPES: ("FEATURE_EDH_AMI_RECIPES", "EDH_AMI_RECIPES_DISABLED_MESSAGE"),
}

READ_HEADING = "#### Read (always available)"
WRITE_HEADING = "#### Write (require `VAMS_ENABLE_WRITES=true`)"
DESTRUCTIVE_HEADING = "#### Destructive (require `VAMS_ENABLE_DESTRUCTIVE=true`)"
SECURITY_HEADING = "#### Security notes"


class RecordingMcp:
    """Stands in for the MCP server: `tool()` records each function it decorates, by name."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def record(fn):
            assert fn.__name__ not in self.tools, f"{fn.__name__} registered twice"
            self.tools[fn.__name__] = fn
            return fn

        return record


def _register(client, **gates):
    """The tools `edh.register` defines for `Config(**gates)`, by name."""
    mcp = RecordingMcp()
    edh.register(mcp, client, Config(**gates), server.tool_result)
    return mcp.tools


def _switch_of(name):
    if name in EDH_CONTAINER_TOOLS:
        return FEATURE_EDH_CONTAINERS
    if name in EDH_AMI_RECIPES_TOOLS:
        return FEATURE_EDH_AMI_RECIPES
    return FEATURE_EDH_WORKSTATIONS


@pytest.fixture
def client():
    """A mocked VamsClient whose pre-flight is the real `require_feature`, over a mocked
    `has_feature` that reports every switch present unless a test says otherwise."""
    mock = MagicMock()
    mock.has_feature.return_value = True
    mock.require_feature = types.MethodType(VamsClient.require_feature, mock)
    return mock


@pytest.fixture
def reads(client):
    """The tools registered with both gates off."""
    return _register(client)


@pytest.fixture
def gated(client):
    """Every EDH tool, registered with both gates on."""
    return _register(client, enable_writes=True, enable_destructive=True)


@pytest.fixture
def enabled_server():
    """Reimport `vams_mcp.server` with both gates on, then restore the default-gate module."""
    for name in GATE_VARS:
        os.environ[name] = "true"
    importlib.reload(server)
    try:
        assert server.CONFIG.enable_writes is True
        assert server.CONFIG.enable_destructive is True
        yield server
    finally:
        for name in GATE_VARS:
            os.environ.pop(name, None)
        importlib.reload(server)


class _EmptyAnswer:
    """The response a recorded verb returns: an empty JSON body."""

    def json(self):
        return {}


def _recording_client(monkeypatch):
    """A client holding vamscli's real APIClient, whose HTTP verbs record the endpoint and send nothing;
    returns (client, record). The id check under test, `_edh_session_id`, lives in that APIClient and
    runs before the verb; beneath the verbs, its `_make_request` also refuses a path with an empty, '.'
    or '..' segment."""
    from vamscli.utils.api_client import APIClient

    client = MagicMock()
    client.has_feature.return_value = True
    client.require_feature = types.MethodType(VamsClient.require_feature, client)
    client.api = APIClient("https://api.example.com", profile_manager=MagicMock())
    sent = []
    for verb in ("get", "post", "put", "delete"):
        monkeypatch.setattr(
            client.api, verb,
            lambda endpoint, *args, _verb=verb, **kwargs: sent.append((_verb, endpoint)) or _EmptyAnswer())
    return client, sent


def _docstring_of(name):
    for node in ast.walk(_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return " ".join((ast.get_docstring(node) or "").split())
    raise AssertionError(f"no function named {name!r} in {SOURCE_PATH.name}")


def _tool_definitions():
    """Every @tool_result-decorated def in the module, gated or not."""
    return [
        node for node in ast.walk(_TREE)
        if isinstance(node, ast.FunctionDef)
        and any(ast.unparse(d) == "tool_result" for d in node.decorator_list)
    ]


def _first_statement_call(node):
    """(callee, argument names) of the first statement after the def's docstring, or None."""
    body = node.body[1:] if ast.get_docstring(node) else node.body
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Call):
        return ast.unparse(first.value.func), [ast.unparse(a) for a in first.value.args]
    return None


def _placeholder_call(fn):
    """Call `fn` with a placeholder for each required parameter: the pre-flight runs before any
    argument is read."""
    params = inspect.signature(fn).parameters.values()
    return fn(*["x" for p in params if p.default is inspect.Parameter.empty])


def _section(text, heading, ends):
    start = text.index(heading)
    stops = [i for i in (text.find(end, start + len(heading)) for end in ends) if i != -1]
    return text[start:min(stops) if stops else len(text)]


def _edh_section():
    addon = _section(README_TEXT, "## Add-on tools", ("\n## ",))
    return _section(addon, "### EDH Workstations", ("\n### ",))


def _edh_tier(heading):
    return _section(_edh_section(), heading, ("\n#### ",))


def _core_auto_approve_sample():
    block = re.search(r"```json\n(\{\n\s*\"mcpServers\".*?\n\})\n```", README_TEXT, re.S)
    assert block, "README autoApprove sample not found"
    return json.loads(block.group(1))["mcpServers"]["vams"]["autoApprove"]


def _edh_auto_approve_addition():
    block = re.search(r"```json\n(\[.*?\])\n```", _edh_tier(READ_HEADING), re.S)
    assert block, "the EDH section's autoApprove addition was not found"
    return json.loads(block.group(1))


def _referenced_names(tree):
    """Every attribute, name and string constant a source tree refers to."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


# --- Registration: which gate registers each tool ---------------------------------------------


@pytest.mark.parametrize(
    "gates,expected",
    [
        ({}, EDH_READ_TOOLS),
        ({"enable_writes": True}, EDH_READ_TOOLS + EDH_WRITE_TOOLS),
        ({"enable_writes": True, "enable_destructive": True}, EDH_TOOLS),
    ],
    ids=["gates-off", "writes", "writes-and-destructive"],
)
def test_each_gate_setting_registers_exactly_its_tools(client, gates, expected):
    """Both directions at once: a write tool filed under the destructive gate is missing with writes
    alone, and a read placed in a gate block is missing with the gates off."""
    assert sorted(_register(client, **gates)) == sorted(expected)


def test_the_connect_tool_is_withheld_until_writes_are_enabled(client):
    """It changes nothing, but it issues a desktop credential, so it sits with the writes rather than
    in the always-available read tier: absent at the default gates, present with writes alone on."""
    assert "get_edh_workstation_connect" not in _register(client)
    assert "get_edh_workstation_connect" in _register(client, enable_writes=True)


def test_the_duplicate_tool_is_a_write_not_a_destructive_tool(client):
    """It creates a recipe and destroys nothing: absent at the default gates, present with writes
    alone on."""
    assert "duplicate_edh_ami_recipe" not in _register(client)
    assert "duplicate_edh_ami_recipe" in _register(client, enable_writes=True)


@pytest.mark.asyncio
async def test_the_server_registers_the_reads_and_withholds_the_gated_tools_by_default():
    names = {tool.name for tool in await server.mcp.list_tools()}
    missing = sorted(set(EDH_READ_TOOLS) - names)
    assert not missing, f"EDH read tools not registered: {missing}"
    leaked = sorted(set(EDH_GATED_TOOLS) & names)
    assert not leaked, f"gated EDH tools registered with both gates off: {leaked}"


@pytest.mark.asyncio
async def test_the_server_registers_every_edh_tool_with_both_gates_on(enabled_server):
    """The server passes its own CONFIG to the add-on lane, so its gates govern these tools too."""
    names = {tool.name for tool in await enabled_server.mcp.list_tools()}
    missing = sorted(set(EDH_TOOLS) - names)
    assert not missing, f"EDH tools not registered with both gates on: {missing}"


# --- Read tools call one method, forward its argument, and return the body unaltered --------


@pytest.mark.parametrize(
    "tool,method,args",
    [
        ("list_edh_workstations", "list_edh_workstations", ()),
        ("get_edh_workstation", "get_edh_workstation", ("s-1",)),
        ("list_edh_catalog_images", "list_edh_catalog_images", ()),
        ("list_edh_catalog_instance_types", "list_edh_catalog_instance_types", ()),
        ("get_edh_catalog_lease", "get_edh_catalog_lease", ()),
        ("list_edh_admin_images", "list_edh_admin_images", ()),
        ("list_edh_admin_instances", "list_edh_admin_instances", ()),
        ("get_edh_capacity", "get_edh_capacity", ()),
        ("get_edh_health", "get_edh_health", ()),
        ("get_edh_admin_settings", "get_edh_admin_settings", ()),
        ("list_edh_container_catalog", "list_edh_container_catalog", ()),
        ("list_edh_ami_recipes", "list_edh_ami_recipes", ()),
        ("get_edh_ami_recipe", "get_edh_ami_recipe", ("r",)),
        ("list_edh_ami_recipe_builds", "list_edh_ami_recipe_builds", ("r",)),
    ],
)
def test_read_tools_forward_their_argument_and_return_the_body(client, reads, tool, method, args):
    body = {"Items": [{"sessionId": "s-1"}], "marker": tool}
    getattr(client.api, method).return_value = body
    assert reads[tool](*args) == body
    getattr(client.api, method).assert_called_once_with(*args)
    # One unpaginated page, no envelope: neither helper may touch the response.
    assert not client.paginate.called
    assert not client.unwrap_message.called


def test_list_edh_stale_instances_forwards_idle_minutes_by_keyword(client, reads):
    """The stale route's `idleMinutes` (server default 120) is the one narrowing parameter these
    reads take; a tool that dropped it would pin every agent to the default with nothing failing."""
    client.api.list_edh_stale_instances.return_value = {"Items": []}
    assert reads["list_edh_stale_instances"]() == {"Items": []}
    client.api.list_edh_stale_instances.assert_called_once_with(idle_minutes=None)
    client.api.list_edh_stale_instances.reset_mock()
    reads["list_edh_stale_instances"](idle_minutes=45)
    client.api.list_edh_stale_instances.assert_called_once_with(idle_minutes=45)
    assert not client.paginate.called


def test_list_edh_container_jobs_forwards_the_scope_by_keyword(client, reads):
    """The jobs route reads `backend` (server default batch) and, for eks, `cluster` and
    `namespace`; an eks deployment's jobs are unreachable through a tool that drops them, and an
    omitted value must stay omitted so the server default remains the server's."""
    client.api.list_edh_container_jobs.return_value = {"Items": []}
    assert reads["list_edh_container_jobs"]() == {"Items": []}
    client.api.list_edh_container_jobs.assert_called_once_with(
        backend=None, cluster=None, namespace=None)
    client.api.list_edh_container_jobs.reset_mock()
    reads["list_edh_container_jobs"](backend="eks", cluster="sim-cluster")
    client.api.list_edh_container_jobs.assert_called_once_with(
        backend="eks", cluster="sim-cluster", namespace=None)
    client.api.list_edh_container_jobs.reset_mock()
    reads["list_edh_container_jobs"](backend="eks", cluster="sim-cluster", namespace="render")
    client.api.list_edh_container_jobs.assert_called_once_with(
        backend="eks", cluster="sim-cluster", namespace="render")
    assert not client.paginate.called


def test_read_tools_return_errors_as_data(client, reads):
    client.api.get_edh_workstation.side_effect = RuntimeError("boom")
    assert reads["get_edh_workstation"]("s-1") == {"error": "boom", "error_type": "RuntimeError"}


# --- Feature pre-flight ----------------------------------------------------------------------


def test_a_disabled_add_on_is_reported_before_any_request(client, reads):
    """The add-on's routes do not exist on a deployment that has not enabled it, so a request would
    meet API Gateway's 403 and come back as a request no deployed route matches, naming no feature
    switch. The profile's feature-switch snapshot answers first, in the CLI's own words."""
    client.has_feature.return_value = False
    assert reads["list_edh_workstations"]() == {
        "error": EDH_WORKSTATIONS_DISABLED_MESSAGE, "error_type": "FeatureDisabledError"}
    client.has_feature.assert_called_once_with(FEATURE_EDH_WORKSTATIONS)
    client.api.list_edh_workstations.assert_not_called()


def test_the_container_tools_check_the_containers_switch(client, reads):
    """Container jobs are the add-on's second switch; a deployment with workstations but no container
    jobs is told so, not that the whole add-on is missing."""
    client.has_feature.side_effect = lambda name: name == FEATURE_EDH_WORKSTATIONS
    assert reads["list_edh_container_catalog"]() == {
        "error": EDH_CONTAINERS_DISABLED_MESSAGE, "error_type": "FeatureDisabledError"}
    client.has_feature.assert_called_with(FEATURE_EDH_CONTAINERS)
    client.api.list_edh_container_catalog.assert_not_called()
    client.api.list_edh_workstations.return_value = {"Items": []}
    assert reads["list_edh_workstations"]() == {"Items": []}


def test_every_edh_tool_opens_with_the_feature_pre_flight():
    """One statement per tool, before anything reaches the client: the containers switch for the
    container tools, the AMI recipes switch for the AMI recipe tools, the workstations switch for
    the rest, each with the CLI sentence of that switch. A tool that skipped it would answer a
    disabled deployment with an authentication failure again, and only a live call would show it."""
    tools = _tool_definitions()
    assert sorted(node.name for node in tools) == sorted(EDH_TOOLS), "the EDH tool inventory drifted"
    wrong = [
        node.name for node in tools
        if _first_statement_call(node) != ("client.require_feature", list(SWITCH_NAMES[_switch_of(node.name)]))
    ]
    assert not wrong, f"EDH tools whose first statement is not require_feature(<their switch>): {wrong}"


@pytest.mark.parametrize("tool", EDH_TOOLS)
def test_every_edh_tool_answers_its_switchs_sentence_and_sends_nothing(client, gated, tool):
    """The same contract, executed: with the switch absent, each tool returns its switch's sentence
    as data and never reaches the client."""
    client.has_feature.return_value = False
    switch = _switch_of(tool)
    assert _placeholder_call(gated[tool]) == {
        "error": SWITCH_MESSAGES[switch], "error_type": "FeatureDisabledError"}
    client.has_feature.assert_called_once_with(switch)
    assert client.api.method_calls == []


def test_a_disabled_add_on_is_reported_before_a_write_reaches_the_api(client, gated):
    """The feature pre-flight guards the gated EDH tools too: with the switch absent from the profile,
    a launch or a kill answers the CLI's disabled message and the client is never called."""
    client.has_feature.return_value = False
    assert gated["create_edh_workstation"]("12", "g5.2xlarge", "Design desk", 200, "linux") == {
        "error": EDH_WORKSTATIONS_DISABLED_MESSAGE, "error_type": "FeatureDisabledError"}
    client.api.create_edh_workstation.assert_not_called()
    assert gated["kill_edh_container_job"]("j-1") == {
        "error": EDH_CONTAINERS_DISABLED_MESSAGE, "error_type": "FeatureDisabledError"}
    client.api.kill_edh_container_job.assert_not_called()


# --- Errors the tools hand back --------------------------------------------------------------


@pytest.mark.parametrize("error_name, flag", [
    ("EdhOutcomeUnknownError", "outcomeUnknown"),
    ("EdhStartInProgressError", "startInProgress"),
])
def test_tool_result_carries_the_edh_outcome_flags(error_name, flag):
    @server.tool_result
    def tool():
        raise getattr(edh_exceptions, error_name)("x")

    assert tool() == {"error": "x", "error_type": error_name, flag: True}


def test_an_edh_mutation_edh_did_not_confirm_is_returned_as_its_own_error_type(client, gated):
    """An agent tells an unconfirmed start from an unanswered one by error_type and outcomeUnknown:
    the first may have started the workstation, so it lists before it retries."""
    message = ("Workstation start not confirmed (502): EDH did not confirm the request in time; it may "
               "still be in progress. Refresh before trying again. "
               "Run 'vamscli edh workstation list' to see its state.")
    client.api.start_edh_workstation.side_effect = edh_exceptions.EdhOutcomeUnknownError(message)
    assert gated["start_edh_workstation"]("s-1") == {
        "error": message, "error_type": "EdhOutcomeUnknownError", "outcomeUnknown": True}
    client.api.start_edh_workstation.side_effect = edh_exceptions.EdhUnavailableError("unanswered")
    assert gated["start_edh_workstation"]("s-1") == {
        "error": "unanswered", "error_type": "EdhUnavailableError"}


def test_a_missing_recipe_is_reported_as_the_recipe_not_found_error(monkeypatch):
    """The tool reports the CLI client's typed error, so an agent tells a recipe that does not exist
    from a failed request by `error_type`."""
    client, sent = _recording_client(monkeypatch)
    response = MagicMock()
    response.status_code = 404
    response.content = b'{"message": "Recipe not found"}'
    response.json.return_value = {"message": "Recipe not found"}

    def _not_found(endpoint, *args, **kwargs):
        sent.append(("get", endpoint))
        raise requests.exceptions.HTTPError("404 Client Error", response=response)

    monkeypatch.setattr(client.api, "get", _not_found)

    assert _register(client)["get_edh_ami_recipe"]("team-desktop") == {
        "error": "AMI recipe 'team-desktop' not found", "error_type": "EdhAmiRecipeNotFoundError"}
    assert sent == [("get", "/addon/edh/admin/ami-recipes/team-desktop")]


# --- The connect tool and the Windows sign-in ------------------------------------------------


def test_connect_forwards_the_session_id_and_returns_the_credential_bearing_url_untouched(client, gated):
    body = {"sessionId": "s-1", "connectionUrl": "https://dcv.example.internal/?authToken=abc",
            "osFamily": "linux"}
    client.api.get_edh_workstation_connection.return_value = body
    assert gated["get_edh_workstation_connect"]("s-1") == body
    client.api.get_edh_workstation_connection.assert_called_once_with("s-1")


def test_connect_cannot_be_pointed_at_another_route_through_its_id(monkeypatch):
    """The connect tool's id reaches the URL path through vamscli's real APIClient, which refuses an id
    that is not one path segment (`_edh_session_id`), so `<id>/windows-sign-in?x=` is an error and no
    request; beneath every method, `_make_request` also refuses a path with an empty, '.' or '..'
    segment."""
    client, sent = _recording_client(monkeypatch)
    connect = _register(client, enable_writes=True)["get_edh_workstation_connect"]
    # Control: a well-formed id is sent to the connect route.
    assert connect("s-1") == {}
    assert sent == [("get", "/addon/edh/workstations/s-1/connect")]
    sent.clear()

    result = connect("s-1/windows-sign-in?x=")

    assert result["error_type"] == "InvalidEdhRequestError"
    assert sent == []


def test_no_tool_reaches_the_windows_sign_in_through_an_id(monkeypatch):
    """get_edh_workstation is an autoApprove read: an id that carries another route's path must be
    refused before any request, so the agent receives an error and never the sign-in body."""
    client, sent = _recording_client(monkeypatch)
    get_workstation = _register(client)["get_edh_workstation"]
    # Control: a well-formed id is sent, so the empty record below means the id was refused.
    assert get_workstation("s-1") == {}
    assert sent == [("get", "/addon/edh/workstations/s-1")]
    sent.clear()

    result = get_workstation("s-1/windows-sign-in")

    assert result["error_type"] == "InvalidEdhRequestError"
    assert "password" not in result
    assert sent == []


def test_no_tool_reaches_the_windows_sign_in():
    """The Windows password of a desktop is shown to its owner in the web app and behind
    `vamscli edh workstation connect --show-windows-password` only; no tool returns it to an agent."""
    for text in (SOURCE_TEXT, SERVER_TEXT):
        names = _referenced_names(ast.parse(text))
        assert "get_edh_workstation_windows_sign_in" not in names
        assert "API_EDH_WORKSTATION_WINDOWS_SIGN_IN" not in names
        assert "windows-sign-in" not in text
    # Control: the connect tool's own client call is found, so the absences above mean something.
    assert "get_edh_workstation_connection" in _referenced_names(_TREE)


@pytest.mark.parametrize(
    "fragment", ["CREDENTIAL", "autoApprove", "session auth token", "VAMS_ENABLE_WRITES"])
def test_the_connect_docstring_states_the_exposure(fragment):
    docstring = _docstring_of("get_edh_workstation_connect")
    assert docstring, "get_edh_workstation_connect has no docstring, so this would be vacuous"
    assert fragment in docstring


# --- Workstation write tools -----------------------------------------------------------------


def test_create_edh_workstation_generates_a_uuid4_bootstrap_id_and_sends_every_required_field(client, gated):
    """`bootstrapId` is the launch's idempotency key, generated here when the agent has none;
    `osFamily` is required by the create model, so the tool always sends the one it was given."""
    gated["create_edh_workstation"]("12", "g5.2xlarge", "Design desk", 200, "linux")
    payload = client.api.create_edh_workstation.call_args.args[0]
    assert uuid.UUID(payload.pop("bootstrapId")).version == 4
    assert payload == {"imageId": "12", "instanceType": "g5.2xlarge", "name": "Design desk",
                       "rootSizeGb": 200, "osFamily": "linux"}


def test_create_edh_workstation_forwards_an_explicit_bootstrap_id_and_the_windows_family(client, gated):
    gated["create_edh_workstation"]("12", "g5.2xlarge", "Design desk", 200, "windows",
                                    bootstrap_id="b-retry")
    payload = client.api.create_edh_workstation.call_args.args[0]
    assert payload["bootstrapId"] == "b-retry"
    assert payload["osFamily"] == "windows"


def test_create_edh_workstation_has_no_default_os_family(client, gated):
    """The tool schema FastMCP derives from the signature marks `os_family` required; a call without
    it never reaches the API (tool_result hands the TypeError back as an error result)."""
    result = gated["create_edh_workstation"]("12", "g5.2xlarge", "Design desk", 200)
    assert result["error_type"] == "TypeError"
    client.api.create_edh_workstation.assert_not_called()


def test_create_edh_workstation_returns_the_accepted_record_unaltered(client, gated):
    client.api.create_edh_workstation.return_value = {"bootstrapId": "b-1", "state": "CREATING"}
    assert gated["create_edh_workstation"]("12", "g5.2xlarge", "d", 100, "linux") == {
        "bootstrapId": "b-1", "state": "CREATING"}


def test_create_edh_workstation_forwards_lease_hours_when_present(client, gated):
    """lease_hours is optional; when given, it is forwarded as leaseHours in the payload."""
    gated["create_edh_workstation"]("12", "g5.2xlarge", "d", 100, "linux", lease_hours=4)
    payload = client.api.create_edh_workstation.call_args.args[0]
    assert payload["leaseHours"] == 4


def test_create_edh_workstation_omits_lease_hours_when_not_given(client, gated):
    """When lease_hours is omitted, leaseHours is absent from the payload."""
    gated["create_edh_workstation"]("12", "g5.2xlarge", "d", 100, "linux")
    payload = client.api.create_edh_workstation.call_args.args[0]
    assert "leaseHours" not in payload


def test_renew_edh_workstation_delegates_to_api_client(client, gated):
    """renew_edh_workstation forwards the session_id and returns the updated record."""
    client.api.renew_edh_workstation.return_value = {"sessionId": "s-1", "leaseExpiresAt": "2026-09-17T12:00:00Z"}
    assert gated["renew_edh_workstation"]("s-1") == {"sessionId": "s-1", "leaseExpiresAt": "2026-09-17T12:00:00Z"}
    client.api.renew_edh_workstation.assert_called_once_with("s-1")


def test_renew_edh_workstation_docstring_names_the_session_bootstrap_outcome():
    """A renew of a running desktop may re-issue its session key; the docstring tells the agent that
    the response then carries `sessionBootstrap` with the start's values, and that an API-key
    session renews the lease without one."""
    docstring = _docstring_of("renew_edh_workstation")
    assert "`sessionBootstrap`" in docstring
    assert "provisioned|skipped|unavailable|disabled" in docstring
    assert "API key" in docstring


def test_update_edh_admin_settings_builds_partial_patch_from_provided_arguments(client, gated):
    """update_edh_admin_settings builds the data dict from only the arguments passed."""
    client.api.update_edh_admin_settings.return_value = {"lease": {"graceHours": 6}}
    gated["update_edh_admin_settings"](grace_hours=6)
    client.api.update_edh_admin_settings.assert_called_once_with({"graceHours": 6})


def test_update_edh_admin_settings_sends_all_four_fields_when_all_given(client, gated):
    client.api.update_edh_admin_settings.return_value = {"lease": {}}
    gated["update_edh_admin_settings"](enabled=True, lease_hours=24, forced=False, grace_hours=12)
    client.api.update_edh_admin_settings.assert_called_once_with({
        "enabled": True, "leaseHours": 24, "forced": False, "graceHours": 12})


def test_update_edh_admin_settings_sends_false_when_disabled(client, gated):
    client.api.update_edh_admin_settings.return_value = {"lease": {}}
    gated["update_edh_admin_settings"](enabled=False)
    client.api.update_edh_admin_settings.assert_called_once_with({"enabled": False})


def test_update_edh_admin_settings_refuses_empty_call_without_touching_client(client, gated):
    """When no arguments are passed, the tool raises ValueError before calling the API."""
    result = gated["update_edh_admin_settings"]()
    assert result["error_type"] == "ValueError"
    assert "at least one" in result["error"]
    client.api.update_edh_admin_settings.assert_not_called()


@pytest.mark.parametrize(
    "tool,method",
    [("start_edh_workstation", "start_edh_workstation"),
     ("stop_edh_workstation", "stop_edh_workstation"),
     ("stop_edh_admin_instance", "stop_edh_admin_instance"),
     ("delete_edh_workstation", "delete_edh_workstation"),
     ("deactivate_edh_admin_instance", "deactivate_edh_admin_instance")],
)
def test_edh_session_tools_forward_the_session_id_and_return_the_record(client, gated, tool, method):
    getattr(client.api, method).return_value = {"sessionId": "s-1", "state": "x"}
    assert gated[tool]("s-1") == {"sessionId": "s-1", "state": "x"}
    getattr(client.api, method).assert_called_once_with("s-1")


# --- Admin image tools -------------------------------------------------------------------------


def test_register_edh_vdi_image_payload_omits_absent_optionals(client, gated):
    gated["register_edh_vdi_image"]("ami-0123", "ubuntu-22-dcv", "ubuntu2204", 100, 3)
    client.api.register_edh_vdi_image.assert_called_once_with({
        "amiId": "ami-0123", "stackName": "ubuntu-22-dcv", "baseOs": "ubuntu2204",
        "rootSize": 100, "virtualDesktopProfileId": 3})
    client.api.register_edh_vdi_image.reset_mock()
    gated["register_edh_vdi_image"]("ami-0123", "win-dcv", "windows2022", 150, 3,
                                    launch_tenancy="dedicated", description="Windows desktop")
    payload = client.api.register_edh_vdi_image.call_args.args[0]
    assert payload["launchTenancy"] == "dedicated"
    assert payload["description"] == "Windows desktop"


def test_register_edh_simulation_image_payload(client, gated):
    gated["register_edh_simulation_image"]("ami-0456", "isaac-sim", 200, 7)
    client.api.register_edh_simulation_image.assert_called_once_with({
        "amiId": "ami-0456", "stackName": "isaac-sim", "rootSize": 200, "targetNodeProfileId": 7})
    client.api.register_edh_simulation_image.reset_mock()
    gated["register_edh_simulation_image"]("ami-0456", "isaac-sim", 200, 7,
                                           user_data_variables="MODE=headless", description="node")
    payload = client.api.register_edh_simulation_image.call_args.args[0]
    assert payload["userDataVariables"] == "MODE=headless"
    assert payload["description"] == "node"


def test_register_edh_simulation_image_sends_the_user_data_template_only_when_given(client, gated):
    """`targetNodeUserDataId` travels only when the caller names a template, so an omitted value
    leaves the server's default (template 1) to the server."""
    gated["register_edh_simulation_image"]("ami-0456", "isaac-sim", 200, 7)
    assert "targetNodeUserDataId" not in client.api.register_edh_simulation_image.call_args.args[0]
    client.api.register_edh_simulation_image.reset_mock()
    gated["register_edh_simulation_image"]("ami-0456", "isaac-sim", 200, 7, target_node_user_data_id=3)
    payload = client.api.register_edh_simulation_image.call_args.args[0]
    assert payload["targetNodeUserDataId"] == 3


@pytest.mark.parametrize(
    "tool,method,other",
    [("delete_edh_vdi_image", "delete_edh_vdi_image", "delete_edh_simulation_image"),
     ("delete_edh_simulation_image", "delete_edh_simulation_image", "delete_edh_vdi_image")],
)
def test_edh_image_deletes_reach_their_own_kind(client, gated, tool, method, other):
    """The two image kinds live in different EDH tables behind different routes; crossing them
    deletes nothing and reports not found."""
    getattr(client.api, method).return_value = {"deleted": "img-7"}
    assert gated[tool]("img-7") == {"deleted": "img-7"}
    getattr(client.api, method).assert_called_once_with("img-7")
    getattr(client.api, other).assert_not_called()


# --- Docstring contracts -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool,fragment",
    [
        ("create_edh_workstation", "compute"),
        ("create_edh_workstation", "autoApprove"),
        ("create_edh_workstation", "bootstrap_id"),
        ("start_edh_workstation", "compute"),
        ("start_edh_workstation", "autoApprove"),
        ("stop_edh_workstation", "compute"),
        ("stop_edh_workstation", "start_edh_workstation"),
        ("stop_edh_admin_instance", "compute"),
        ("stop_edh_admin_instance", "autoApprove"),
        ("stop_edh_admin_instance", "list_edh_admin_instances"),
        ("delete_edh_workstation", "Irreversible"),
        ("delete_edh_workstation", "stop_edh_workstation"),
        ("delete_edh_vdi_image", "Irreversible"),
        ("delete_edh_simulation_image", "Irreversible"),
        ("deactivate_edh_admin_instance", "Irreversible"),
        ("deactivate_edh_admin_instance", "stop_edh_admin_instance"),
    ],
)
def test_edh_tool_docstrings_state_the_compute_risk_and_the_reversible_alternative(gated, tool, fragment):
    """The docstring is the agent's contract: the compute tools say they start or interrupt a GPU
    desktop and stay out of autoApprove, and each irreversible tool names its reversible sibling."""
    docstring = gated[tool].__doc__ or ""
    assert docstring, f"{tool} has no docstring, so this assertion would be vacuous"
    assert fragment in docstring, f"{tool} docstring does not say {fragment!r}"


@pytest.mark.parametrize(
    "tool,fragment",
    [
        ("list_edh_workstations", "makes EDH issue no connection token"),
        ("get_edh_workstation", "makes EDH issue no connection token"),
        ("get_edh_workstation_connect", "at most one connection token per request EDH answers, for this desktop"),
    ],
)
def test_edh_workstation_read_docstrings_state_which_reads_issue_a_connection_token(gated, tool, fragment):
    """The docstring is the agent's contract: reading a workstation makes EDH issue no DCV connection
    token, and the connect tool makes it issue at most one per request EDH answers, for that desktop."""
    docstring = " ".join((gated[tool].__doc__ or "").split())
    assert docstring, f"{tool} has no docstring, so this assertion would be vacuous"
    assert fragment in docstring, f"{tool} docstring does not say {fragment!r}"


@pytest.mark.parametrize(
    "fragment",
    ["STOPPED", "ORPHANED", "`stopped`", "`missingInEdh`", "deactivate_edh_admin_instance"],
)
def test_the_stale_docstring_states_the_population_it_lists(fragment):
    """The stale view holds STOPPED/ORPHANED rows past the threshold and rows EDH no longer lists —
    never a running desktop — and the docstring is the agent's only statement of that, so it names
    both `staleReason` values and the one follow-up tool that acts on such rows."""
    docstring = _docstring_of("list_edh_stale_instances")
    assert docstring, "list_edh_stale_instances has no docstring, so this would be vacuous"
    assert fragment in docstring


@pytest.mark.parametrize(
    "fragment", ["`category`", "`amiRecipe`", "`baseline`", "`recipeId`", "`recipeDisplayName`",
                 "`recipeVersion`", "first"])
def test_the_catalog_images_docstring_names_the_category(fragment):
    """An agent choosing an image learns from the description which images an AMI recipe built and
    that they are listed first."""
    assert fragment in _docstring_of("list_edh_catalog_images")


def test_the_instance_types_docstring_states_the_order():
    assert "g5.2xlarge` before `g5.12xlarge" in _docstring_of("list_edh_catalog_instance_types")


@pytest.mark.parametrize(
    "fragment", ["`architecture` (the image's CPU architecture", "null when EDH reports none"])
def test_the_catalog_images_docstring_names_the_architecture(fragment):
    """An agent choosing an instance type learns from the image's description which CPU architecture
    the image runs, and that it may be unknown. The field is matched with its description: the name
    alone also occurs where the docstring points at the instance-type rule."""
    assert fragment in _docstring_of("list_edh_catalog_images")


@pytest.mark.parametrize(
    "fragment",
    ["`kind`", "`imageId`", "`stackName`", "`amiId`", "`baseOs`", "`osFamily`", "`rootSize`", "`profileId`",
     "`description`",
     "`architecture` (the CPU architecture EDH reports for the image, such as `x86_64` or `arm64`, or null)"])
def test_the_admin_images_docstring_names_the_item_fields(fragment):
    """An agent reading the admin inventory learns each image's fields, its architecture among them,
    from the description alone."""
    assert fragment in _docstring_of("list_edh_admin_images")


@pytest.mark.parametrize(
    "fragment",
    ["`{instanceType, architectures}`", "`architectures`", "runs",
     "runs an image when the image's `architecture` is null, the type's `architectures` is empty, or it "
     "contains the image's architecture",
     "pass only such a type to `create_edh_workstation`"])
def test_the_instance_types_docstring_states_the_architecture_rule(fragment):
    """The rule is the web wizard's and the CLI's; the docstring is the only place an agent learns it."""
    assert fragment in _docstring_of("list_edh_catalog_instance_types")


@pytest.mark.parametrize(
    "fragment",
    ["instanceType does not match the image's architecture",
     "only under architectures of another family than the image's (`x86_64` and `i386` are one family)"])
def test_the_create_docstring_names_the_architecture_refusal(fragment):
    """The refusal follows the backend's family rule, under which an `i386`-grouped type for an
    `x86_64` image is accepted."""
    assert fragment in _docstring_of("create_edh_workstation")


@pytest.mark.parametrize("tool", ["create_edh_ami_recipe", "update_edh_ami_recipe", "duplicate_edh_ami_recipe"])
@pytest.mark.parametrize(
    "fragment",
    ["another architecture family than",
     "is refused (400) with `buildInstanceType does not match the recipe's architecture` or "
     "`parentImage does not match the recipe's architecture`"])
def test_the_recipe_save_docstrings_name_the_architecture_refusals(tool, fragment):
    """The service answers these with 400 rather than the 409 of the other recipe refusals."""
    assert fragment in _docstring_of(tool)


@pytest.mark.parametrize(
    "fragment",
    ["`the parent image could not be found`",
     "`the parent image's architecture does not match the recipe's architecture`",
     "`buildInstanceType does not match the recipe's architecture`",
     "another architecture family than the recipe's `architecture`"])
def test_the_build_start_docstring_names_the_parent_and_architecture_refusals(fragment):
    assert fragment in _docstring_of("start_edh_ami_recipe_build")


# --- Container jobs ----------------------------------------------------------------------------


def test_submit_edh_container_job_payload_omits_absent_optionals(client, gated):
    gated["submit_edh_container_job"]("render-def", "render-1", "batch", "gpu-queue")
    client.api.submit_edh_container_job.assert_called_once_with({
        "jobDefinition": "render-def", "jobQueue": "gpu-queue", "name": "render-1", "backend": "batch"})


def test_submit_edh_container_job_has_no_default_job_queue(client, gated):
    """The handler refuses a body without `jobQueue` (400), so the schema marks `job_queue` required
    and a call without it never reaches the API (tool_result hands the TypeError back)."""
    result = gated["submit_edh_container_job"]("render-def", "render-1", "batch")
    assert result["error_type"] == "TypeError"
    client.api.submit_edh_container_job.assert_not_called()


def test_submit_edh_container_job_forwards_every_optional_by_model_key(client, gated):
    gated["submit_edh_container_job"](
        "render-def", "render-1", "eks", "sim-cluster", command=["python", "main.py"],
        environment={"MODE": "headless"}, vcpus=4, memory=16384, gpu=1)
    client.api.submit_edh_container_job.assert_called_once_with({
        "jobDefinition": "render-def", "jobQueue": "sim-cluster", "name": "render-1", "backend": "eks",
        "command": ["python", "main.py"], "environment": {"MODE": "headless"}, "vcpus": 4,
        "memory": 16384, "gpu": 1})


def test_kill_edh_container_job_forwards_the_id_and_scope_and_returns_the_record(client, gated):
    client.api.kill_edh_container_job.return_value = {"jobId": "j-1", "backend": "batch", "message": ""}
    assert gated["kill_edh_container_job"]("j-1") == {"jobId": "j-1", "backend": "batch", "message": ""}
    client.api.kill_edh_container_job.assert_called_once_with(
        "j-1", backend=None, cluster=None, namespace=None)
    client.api.kill_edh_container_job.reset_mock()
    gated["kill_edh_container_job"]("render-1", backend="eks", cluster="sim-cluster")
    client.api.kill_edh_container_job.assert_called_once_with(
        "render-1", backend="eks", cluster="sim-cluster", namespace=None)
    client.api.kill_edh_container_job.reset_mock()
    gated["kill_edh_container_job"]("render-1", backend="eks", cluster="sim-cluster", namespace="render")
    client.api.kill_edh_container_job.assert_called_once_with(
        "render-1", backend="eks", cluster="sim-cluster", namespace="render")


@pytest.mark.parametrize(
    "tool,fragment",
    [
        ("submit_edh_container_job", "compute"),
        ("submit_edh_container_job", "autoApprove"),
        ("submit_edh_container_job", "batch"),
        ("submit_edh_container_job", "MiB"),
        ("kill_edh_container_job", "Irreversible"),
        ("kill_edh_container_job", "list_edh_container_jobs"),
    ],
)
def test_edh_container_tool_docstrings_state_the_compute_risk(gated, tool, fragment):
    """The docstring is the agent's contract: submit says it starts billed compute on a named backend
    with `memory` in MiB and stays out of autoApprove; kill says it is irreversible and where the
    `job_id` it takes comes from."""
    docstring = gated[tool].__doc__ or ""
    assert docstring, f"{tool} has no docstring, so this assertion would be vacuous"
    assert fragment in docstring, f"{tool} docstring does not say {fragment!r}"


# --- AMI recipe duplication --------------------------------------------------------------------


def test_duplicate_forwards_the_source_the_copy_and_the_names_to_the_cli_client(client, gated):
    created = {"recipeId": "team-desktop", "source": "admin", "duplicatedFrom": "al2023-desktop"}
    client.api.duplicate_edh_ami_recipe.return_value = created
    assert gated["duplicate_edh_ami_recipe"]("al2023-desktop", "team-desktop") == created
    client.api.duplicate_edh_ami_recipe.assert_called_once_with(
        "al2023-desktop", "team-desktop", display_name=None, stack_name=None, keep_versions=None)
    client.api.duplicate_edh_ami_recipe.reset_mock()
    gated["duplicate_edh_ami_recipe"]("isaacsim", "isaacsim-lab", display_name="Isaac Sim Lab",
                                      stack_name="Isaac Sim Lab", keep_versions=5)
    client.api.duplicate_edh_ami_recipe.assert_called_once_with(
        "isaacsim", "isaacsim-lab", display_name="Isaac Sim Lab", stack_name="Isaac Sim Lab", keep_versions=5)
    # One client method composes the read and the create; the tool makes neither call itself.
    assert not client.api.get_edh_ami_recipe.called
    assert not client.api.create_edh_ami_recipe.called


def test_the_duplicate_tool_takes_the_cli_client_s_parameters():
    """The tool forwards each optional parameter of the CLI method by name, so the two stay in step."""
    from vamscli.utils.api_client import APIClient

    method = inspect.signature(APIClient.duplicate_edh_ami_recipe).parameters
    tool = [node for node in _tool_definitions() if node.name == "duplicate_edh_ami_recipe"]
    assert len(tool) == 1
    tool_parameters = [argument.arg for argument in tool[0].args.args]
    optional = [name for name, parameter in method.items() if parameter.default is not inspect.Parameter.empty]
    # Control: the CLI method has optional parameters to compare.
    assert "keep_versions" in optional
    assert [name for name in optional if name not in tool_parameters] == []


def test_duplicate_reads_the_source_and_creates_the_copy_through_the_real_cli_client(monkeypatch):
    source = {"recipeId": "al2023-desktop", "displayName": "AL2023", "stackName": "AL2023", "kind": "vdi",
              "assets": [{"id": "vamscli", "repoPath": "tools/VamsCLI"}], "source": "config"}

    class _Answer:
        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    client, _ = _recording_client(monkeypatch)
    sent = []
    monkeypatch.setattr(client.api, "get",
                        lambda endpoint, *args, **kwargs: sent.append(("get", endpoint, None)) or _Answer(source))
    monkeypatch.setattr(client.api, "post",
                        lambda endpoint, *args, **kwargs: sent.append(("post", endpoint, kwargs["data"]))
                        or _Answer({"recipeId": "team-desktop"}))
    duplicate = _register(client, enable_writes=True)["duplicate_edh_ami_recipe"]

    assert duplicate("al2023-desktop", "team-desktop") == {"recipeId": "team-desktop"}
    assert [(verb, endpoint) for verb, endpoint, _ in sent] == [
        ("get", "/addon/edh/admin/ami-recipes/al2023-desktop"), ("post", "/addon/edh/admin/ami-recipes")]
    assert sent[1][2] == {"recipeId": "team-desktop", "displayName": "AL2023 (copy)", "stackName": "AL2023 Copy",
                          "kind": "vdi", "duplicateOf": "al2023-desktop"}
    sent.clear()
    duplicate("al2023-desktop", "team-desktop", keep_versions=4)
    assert sent[1][2]["keepVersions"] == 4


def test_duplicate_on_a_deployment_without_ami_recipes_makes_no_request(client, gated):
    client.has_feature.return_value = False
    result = gated["duplicate_edh_ami_recipe"]("al2023-desktop", "team-desktop")
    assert result["error"] == EDH_AMI_RECIPES_DISABLED_MESSAGE
    assert not client.api.duplicate_edh_ami_recipe.called


@pytest.mark.parametrize(
    "fragment", ["`duplicateOf`", "`duplicatedFrom`", "`assets`", " (copy)", " Copy", "(409)",
                 "start_edh_ami_recipe_build", "update_edh_ami_recipe", "`keep_versions`", "`keepVersions`"])
def test_the_duplicate_docstring_states_what_the_copy_takes(gated, fragment):
    docstring = " ".join((gated["duplicate_edh_ami_recipe"].__doc__ or "").split())
    assert docstring, "duplicate_edh_ami_recipe has no docstring, so this assertion would be vacuous"
    assert fragment in docstring, f"duplicate_edh_ami_recipe docstring does not say {fragment!r}"


# --- AMI recipe deletion -----------------------------------------------------------------------


def test_a_refused_recipe_delete_returns_the_cli_error_as_data(client, gated):
    refusal = "AMI recipe deletion refused (409): a build's AMI removal is pending"
    client.api.delete_edh_ami_recipe.side_effect = edh_exceptions.EdhOperationError(refusal)
    assert gated["delete_edh_ami_recipe"]("team-desktop") == {"error": refusal, "error_type": "EdhOperationError"}
    client.api.delete_edh_ami_recipe.assert_called_once_with("team-desktop")


@pytest.mark.parametrize(
    "fragment", ["(409)", "a build's AMI removal is pending", "`amiRemovalPending`", "`keepVersions`",
                 "`amiDeregisteredAt`", "list_edh_ami_recipe_builds", "Irreversible"])
def test_the_recipe_delete_docstring_states_the_refusal(gated, fragment):
    docstring = " ".join((gated["delete_edh_ami_recipe"].__doc__ or "").split())
    assert docstring, "delete_edh_ami_recipe has no docstring, so this assertion would be vacuous"
    assert fragment in docstring, f"delete_edh_ami_recipe docstring does not say {fragment!r}"


# --- README contracts: the EDH subsection of "Add-on tools" -----------------------------------


@pytest.mark.parametrize(
    "heading,tools",
    [(READ_HEADING, EDH_READ_TOOLS), (WRITE_HEADING, EDH_WRITE_TOOLS),
     (DESTRUCTIVE_HEADING, EDH_DESTRUCTIVE_TOOLS)],
    ids=["read", "write", "destructive"],
)
def test_the_readme_lists_every_tool_under_its_gate(heading, tools):
    """An operator deciding what to enable reads the tier lists; each tool must sit in the one whose
    flag actually exposes it, and in no other."""
    tier = _edh_tier(heading)
    # The list opens the subsection and ends at its first full stop after a tool name.
    listed = re.findall(r"`(\w+)`", tier[len(heading):tier.index("`.") + 1])
    assert sorted(listed) == sorted(tools), f"the README's {heading!r} list is not that tier's tools"


def test_the_readme_lists_the_connect_and_duplicate_tools_with_the_writes():
    for name in ("get_edh_workstation_connect", "duplicate_edh_ami_recipe"):
        assert f"`{name}`" not in _edh_tier(READ_HEADING)
        assert f"`{name}`" in _edh_tier(WRITE_HEADING)


def test_the_core_auto_approve_sample_names_no_add_on_tool():
    """The core sample is for every deployment; the add-on's safe reads are a separate addition."""
    leaked = sorted(set(EDH_TOOLS) & set(_core_auto_approve_sample()))
    assert not leaked, f"EDH tools in the core autoApprove sample: {leaked}"


def test_the_auto_approve_addition_is_the_safe_reads():
    assert sorted(_edh_auto_approve_addition()) == sorted(SAFE_EDH_READS_IN_AUTOAPPROVE)


def test_the_connect_tool_is_never_auto_approved():
    """It returns a DCV URL carrying a session auth token — the same class as the presigned-URL
    tools, which the README keeps out of the sample for the same reason — and it is gated, so neither
    the sample nor the add-on's addition may name it."""
    assert "get_edh_workstation_connect" not in _core_auto_approve_sample()
    assert "get_edh_workstation_connect" not in _edh_auto_approve_addition()


def test_the_admin_session_listings_and_the_duplicate_are_not_auto_approved():
    # Rows name other users' sessions, like list_subscriptions names subscribers.
    addition = _edh_auto_approve_addition()
    for name in ("list_edh_admin_instances", "list_edh_stale_instances", "duplicate_edh_ami_recipe"):
        assert name not in addition


def test_the_readme_states_what_a_disabled_deployment_is_answered():
    """The README is where an operator learns what these tools answer without the add-on: both refusals,
    in the Read subsection that introduces the EDH tools."""
    read_section = _edh_tier(READ_HEADING)
    assert "add-on is not enabled for this deployment" in read_section
    assert "container jobs are not enabled for" in read_section


def test_the_security_notes_name_the_connect_tool():
    assert "`get_edh_workstation_connect`" in _edh_tier(SECURITY_HEADING)


def test_the_security_notes_say_no_tool_returns_the_windows_password():
    notes = _edh_tier(SECURITY_HEADING)
    assert "Windows sign-in" in notes
    assert "--show-windows-password" in notes


def test_the_readme_states_the_admin_images_architecture():
    assert ("Each `list_edh_admin_images` item also carries `architecture`, the CPU architecture EDH "
            "reports for the image, such as `x86_64` or `arm64`, or null when EDH reports none."
            in " ".join(_edh_section().split()))


@pytest.mark.parametrize(
    "fragment",
    ["also carries `architecture`", "`{instanceType, architectures}`",
     "runs an image when the image's `architecture` is null, the type's `architectures` is empty, or it "
     "contains the image's architecture",
     "`instanceType does not match the image's architecture`",
     "only under architectures of another family than the image's (`x86_64` and `i386` are one family)"])
def test_the_readme_states_the_catalog_architecture_fields_and_the_refusal(fragment):
    assert fragment in " ".join(_edh_section().split())


@pytest.mark.parametrize(
    "fragment",
    ["refuse (400) a `buildInstanceType` or `parentImage` that EC2 reports as running another "
     "architecture family than the recipe's `architecture`",
     "`buildInstanceType does not match the recipe's architecture`",
     "`parentImage does not match the recipe's architecture`",
     "`the parent image could not be found`",
     "`the parent image's architecture does not match the recipe's architecture`"])
def test_the_readme_states_the_recipe_architecture_refusals(fragment):
    assert fragment in " ".join(_edh_section().split())
