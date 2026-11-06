# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The image a workstation runs, in the CLI (feature #536, D14): the Image column of `edh workstation
list` with its ` (v<n> available)` suffix, the `Image:`, `Recipe:` and `Recipe version:` lines of
`edh workstation get`, and the image fields carried unchanged by `--json-output`.

The CLI half of the CLI/web check reads the same fixture as the web's
WorkstationImageCell.cases.test.tsx, web/src/addons/edh/workstationImageCases.json, and asserts the
Image column and the `get` lines it agrees with the web's image name and newer-image badge.
"""

import json
from pathlib import Path

import pytest

import vamscli.commands.addon.edh as edh_commands
from vamscli.main import cli

REPO_ROOT = Path(__file__).resolve().parents[4]
IMAGE_CASES = REPO_ROOT / "web/src/addons/edh/workstationImageCases.json"
CASES = json.loads(IMAGE_CASES.read_text(encoding="utf-8"))["cases"]

# The fields a list row and a `get` record carry beside the ones each case sets.
BASE = {
    "bootstrapId": "b-1",
    "sessionId": "s-1",
    "userId": "user@example.com",
    "edhUid": "vabcdefghij23456",
    "name": "Sim box",
    "edhState": "running",
    "activity": None,
    "lastError": None,
    "osFamily": "linux",
    "instanceType": "g6e.4xlarge",
    "createdAt": "2026-10-20T10:00:00Z",
    "updatedAt": "2026-10-20T10:05:00Z",
    "connectionAvailable": True,
    "launchAttempts": 1,
}

OLDER_BUILD = {**BASE, "state": "ACTIVE", "imageId": "31", "imageName": "isaacsim-6-2-v13",
               "recipeId": "isaacsim", "recipeDisplayName": "Isaac Sim 6.2", "recipeVersion": 13,
               "latestRecipeVersion": 14, "imageLatest": False}
BASELINE = {**BASE, "bootstrapId": "b-2", "sessionId": "s-2", "name": "Plain box", "state": "STOPPED",
            "imageId": "12", "imageName": "Ubuntu 22 DCV", "recipeId": None, "recipeDisplayName": None,
            "recipeVersion": None, "latestRecipeVersion": None, "imageLatest": None}


def _ids(cases):
    return [case["name"] for case in cases]


def _row(case):
    return {**BASE, **case["record"]}


def _image_cell(output: str) -> str:
    """The Image column of the single data row of `edh workstation list` text output."""
    lines = output.splitlines()
    header = next(line for line in lines if "Identifier" in line)
    row = next(line for line in lines if line.startswith("  s-1"))
    return row[header.index("Image"):header.index("Instance")].strip()


# --- The fixture shared with the web --------------------------------------------------------------


def test_the_shared_fixture_covers_every_state_and_every_image_latest_value():
    """Control: the file the web test reads too, with every state and every imageLatest value."""
    assert {case["record"]["state"] for case in CASES} == {
        "CREATING", "ACTIVE", "STOPPED", "DELETED", "ORPHANED"}
    assert {case["record"].get("imageLatest", "absent") for case in CASES} == {True, False, None, "absent"}
    assert {case["record"]["state"] for case in CASES if case["badge"]} == {"ACTIVE", "STOPPED", "CREATING"}


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_the_image_name_is_the_one_the_web_shows_without_a_catalog_name(case):
    assert edh_commands.workstation_image_name(_row(case)) == case["shownName"]


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_the_image_column_reads_as_agreed(cli_runner, generic_command_mocks, case):
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].list_edh_workstations.return_value = {'Items': [_row(case)]}
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'list'])
        assert result.exit_code == 0, result.output
        assert _image_cell(result.output) == case["listImage"]
        # The suffix appears exactly where the web shows its badge, naming the same build.
        if case["badge"]:
            latest = case["badge"].split(": ")[1]
            assert case["listImage"] == f"{case['shownName']} ({latest} available)"
        else:
            assert case["listImage"] == case["shownName"]


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_get_prints_the_image_and_the_recipe_lines(cli_runner, generic_command_mocks, case):
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].get_edh_workstation.return_value = _row(case)
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'get', 's-1'])
        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        image = lines.index(f"  Image:           {case['shownName']}")
        assert lines[image + 1] == f"  Image ID:        {case['record']['imageId']}"
        expected = []
        if case["getRecipe"] is not None:
            expected.append(f"  Recipe:          {case['getRecipe']}")
        if case["getRecipeVersion"] is not None:
            expected.append(f"  Recipe version:  {case['getRecipeVersion']}")
        assert lines[image + 2:image + 2 + len(expected)] == expected
        if case["getRecipe"] is None:
            assert not any(line.startswith("  Recipe:") for line in lines)
        if case["getRecipeVersion"] is None:
            assert not any(line.startswith("  Recipe version:") for line in lines)


# --- Layout and passthrough -----------------------------------------------------------------------


def test_the_list_keeps_its_columns_aligned_beside_a_long_image_name(cli_runner, generic_command_mocks):
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].list_edh_workstations.return_value = {'Items': [OLDER_BUILD, BASELINE]}
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'list'])
        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        header = next(line for line in lines if "Identifier" in line)
        rows = [line for line in lines if line.startswith("  s-")]
        assert len(rows) == 2
        instance = header.index("Instance")
        assert rows[0][instance:].split()[0] == "g6e.4xlarge"
        assert rows[1][instance:].split()[0] == "g6e.4xlarge"
        assert "Isaac Sim 6.2 v13 (v14 available)" in rows[0]
        assert "Ubuntu 22 DCV" in rows[1] and "available" not in rows[1]


@pytest.mark.parametrize("command,method,body", [
    (['edh', 'workstation', 'list', '--json-output'], 'list_edh_workstations',
     {'Items': [OLDER_BUILD, BASELINE]}),
    (['edh', 'workstation', 'get', 's-1', '--json-output'], 'get_edh_workstation', OLDER_BUILD),
    (['edh', 'admin', 'instances', 'list', '--json-output'], 'list_edh_admin_instances',
     {'Items': [OLDER_BUILD, BASELINE]}),
])
def test_json_output_carries_every_image_field_unchanged(cli_runner, generic_command_mocks, command, method,
                                                          body):
    with generic_command_mocks('addon.edh') as mocks:
        getattr(mocks['api_client'], method).return_value = body
        result = cli_runner.invoke(cli, command)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == body


def test_the_admin_session_blocks_carry_the_image_fields(cli_runner, generic_command_mocks):
    # The admin listing prints each session as its JSON item; it has no image column to add a suffix to.
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].list_edh_admin_instances.return_value = {'Items': [OLDER_BUILD]}
        result = cli_runner.invoke(cli, ['edh', 'admin', 'instances', 'list'])
        assert result.exit_code == 0, result.output
        assert '"recipeDisplayName": "Isaac Sim 6.2"' in result.output
        assert '"imageLatest": false' in result.output
        assert '"latestRecipeVersion": 14' in result.output


def test_the_help_names_the_image_column_and_lines(cli_runner):
    listing = ' '.join(cli_runner.invoke(cli, ['edh', 'workstation', 'list', '--help']).output.split())
    assert 'Image column' in listing
    assert '(v<n> available)' in listing
    record = ' '.join(cli_runner.invoke(cli, ['edh', 'workstation', 'get', '--help']).output.split())
    for fragment in ('Image', 'Recipe version', 'imageLatest'):
        assert fragment in record, fragment
