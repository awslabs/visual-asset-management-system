# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vamscli --help` names the EDH group only for a profile of a deployment that enables the add-on.

The group is an `AddonGroup`: its listing reads the feature-switch snapshot the profile stored at
`vamscli auth login`, a local read that sends no request. The group stays registered, so a direct
`vamscli edh ...` still runs and each command refuses on its own; a command of an optional feature
(AMI recipes, container jobs) names the add-on when the whole add-on is off.
"""

import re
from unittest.mock import MagicMock

import pytest

from vamscli.addon_constants import (
    EDH_AMI_RECIPES_DISABLED_MESSAGE,
    EDH_CONTAINERS_DISABLED_MESSAGE,
    EDH_WORKSTATIONS_DISABLED_MESSAGE,
    FEATURE_EDH_WORKSTATIONS,
)
from vamscli.main import cli
from vamscli.utils import addon_group
from vamscli.utils.addon_group import AddonGroup, profile_named_on_command_line

COMMAND_ROW = re.compile(r"^  ([a-z][a-z0-9-]*)\s", re.MULTILINE)


def _listed(help_output):
    return set(COMMAND_ROW.findall(help_output.split("Commands:", 1)[1]))


@pytest.fixture
def profiles(monkeypatch):
    """Set what the group reads: each profile name maps to the switches its snapshot holds."""
    def _apply(switches_by_profile, active="default", argv=("vamscli", "--help")):
        def _manager(name):
            manager = MagicMock()
            manager.has_feature_switch.side_effect = (
                lambda switch: switch in switches_by_profile.get(name, ()))
            return manager
        monkeypatch.setattr(addon_group, "ProfileManager", _manager)
        monkeypatch.setattr(addon_group, "read_active_profile_name", lambda: active)
        monkeypatch.setattr(addon_group.sys, "argv", list(argv))
    return _apply


def test_the_group_is_an_addon_group_on_the_add_on_switch():
    edh = cli.commands["edh"]
    assert isinstance(edh, AddonGroup)
    assert edh.feature_switch == FEATURE_EDH_WORKSTATIONS


def test_help_with_the_add_on_off_does_not_list_edh(cli_runner, profiles):
    profiles({"default": ["AUTHPROVIDER_COGNITO", "LOCATIONSERVICES"]})
    result = cli_runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    listed = _listed(result.output)
    assert "database" in listed
    assert "edh" not in listed


def test_help_for_a_profile_that_never_read_its_switches_does_not_list_edh(cli_runner, profiles):
    profiles({})
    assert "edh" not in _listed(cli_runner.invoke(cli, ["--help"]).output)


def test_help_with_the_add_on_on_lists_edh(cli_runner, profiles):
    profiles({"default": ["AUTHPROVIDER_COGNITO", FEATURE_EDH_WORKSTATIONS]})
    result = cli_runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "edh" in _listed(result.output)


def test_help_follows_the_profile_named_on_the_command_line(cli_runner, profiles):
    profiles({"default": [], "edhdesk": [FEATURE_EDH_WORKSTATIONS]},
             argv=("vamscli", "--profile", "edhdesk", "--help"))
    assert "edh" in _listed(cli_runner.invoke(cli, ["--profile", "edhdesk", "--help"]).output)


def test_the_profile_option_is_read_off_the_command_line():
    assert profile_named_on_command_line(["vamscli", "--profile", "p1", "--help"]) == "p1"
    assert profile_named_on_command_line(["vamscli", "--verbose", "--profile=p2", "--help"]) == "p2"
    assert profile_named_on_command_line(["vamscli", "--help"]) is None
    # A --profile after the command belongs to the command, not to the root group.
    assert profile_named_on_command_line(["vamscli", "edh", "--profile", "p3"]) is None


def test_the_group_help_still_answers_with_the_add_on_off(cli_runner, profiles):
    profiles({"default": ["AUTHPROVIDER_COGNITO"]})
    result = cli_runner.invoke(cli, ["edh", "--help"])
    assert result.exit_code == 0
    assert "workstation" in _listed(result.output)


@pytest.mark.parametrize("command", [
    ["edh", "workstation", "list"],
    ["edh", "admin", "ami-recipe", "list"],
    ["edh", "container", "catalog"],
])
def test_a_command_with_the_add_on_off_refuses_naming_the_add_on(cli_runner, generic_command_mocks, command):
    with generic_command_mocks("addon.edh") as mocks:
        mocks["profile_manager"].has_feature_switch.side_effect = lambda switch: switch == "AUTHPROVIDER_COGNITO"
        mocks["profile_manager"].get_feature_switches_info.return_value = {
            "has_feature_switches": True, "enabled": ["AUTHPROVIDER_COGNITO"]}
        result = cli_runner.invoke(cli, command)
        assert result.exit_code == 1
        assert EDH_WORKSTATIONS_DISABLED_MESSAGE in result.output
        assert EDH_AMI_RECIPES_DISABLED_MESSAGE not in result.output
        assert EDH_CONTAINERS_DISABLED_MESSAGE not in result.output
        assert "Traceback" not in result.output
        assert mocks["api_client"].method_calls == []
