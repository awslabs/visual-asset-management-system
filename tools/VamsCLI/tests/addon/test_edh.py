# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the `edh` command group (EDH Workstations add-on)."""

import ast
import json
import re
import uuid
from pathlib import Path

import pytest

import vamscli.commands.addon.edh as edh_commands
from vamscli.addon_constants import (
    EDH_AMI_RECIPES_DISABLED_MESSAGE,
    EDH_CONTAINERS_DISABLED_MESSAGE,
    EDH_WORKSTATIONS_DISABLED_MESSAGE,
)
from vamscli.main import cli
from vamscli.utils.addon.edh_exceptions import (
    EdhAmiRecipeBuildNotFoundError,
    EdhAmiRecipeNotFoundError,
    EdhContainerJobNotFoundError,
    EdhImageNotFoundError,
    EdhOperationError,
    EdhOutcomeUnknownError,
    EdhStartInProgressError,
    EdhUnavailableError,
    EdhWorkstationNotFoundError,
)
from vamscli.utils.exceptions import SetupRequiredError

WORKSTATION = {
    "bootstrapId": "b-1",
    "sessionId": "s-1",
    "userId": "user@example.com",
    "edhUid": "vabcdefghij23456",
    "state": "ACTIVE",
    "edhState": "running",
    "name": "Design desk",
    "imageId": "12",
    "osFamily": "linux",
    "instanceType": "g5.2xlarge",
    "createdAt": "2026-09-08T10:00:00Z",
    "updatedAt": "2026-09-08T10:05:00Z",
    "connectionAvailable": True,
    "lastError": None,
    "launchAttempts": 1,
}

# A launch EDH's placement refused, with the AWS error VAMS captured from EDH's record of it.
CAPACITY_FAILURE = {
    "event": "placement_failed",
    "errorCode": "InsufficientInstanceCapacity",
    "operation": "CreateCapacityReservation",
    "observedAt": "2026-10-22T10:00:00Z",
}

ORPHANED_WORKSTATION = {
    **WORKSTATION, "bootstrapId": "b-orphan", "sessionId": None, "state": "ORPHANED", "edhState": None,
    "connectionAvailable": False, "lastError": "CAPACITY_UNAVAILABLE", "edhFailure": CAPACITY_FAILURE,
}

# Each captured failure shape and the text after `EDH failure:` that shows it.
EDH_FAILURE_TEXTS = [
    (CAPACITY_FAILURE, "InsufficientInstanceCapacity (CreateCapacityReservation)"),
    ({"event": "placement_failed", "errorCode": "VcpuLimitExceeded", "observedAt": "2026-10-22T10:00:00Z"},
     "VcpuLimitExceeded"),
    ({"event": "session-failed", "checkpoint": "dcv_session_create", "observedAt": "2026-10-22T10:00:00Z"},
     "desktop setup failed at dcv_session_create"),
    ({"event": "session-failed", "errorCode": "InsufficientInstanceCapacity", "operation": "RunInstances",
      "checkpoint": "instance_launch", "observedAt": "2026-10-22T10:00:00Z"},
     "InsufficientInstanceCapacity (RunInstances)"),
]

# Bodies that carry no failure to show: none captured, or one naming neither a code nor a checkpoint.
NO_EDH_FAILURES = [
    {},
    {"edhFailure": None},
    {"edhFailure": {"event": "placement_failed", "observedAt": "2026-10-22T10:00:00Z"}},
    {"edhFailure": {"event": "session-failed", "errorCode": "", "checkpoint": None,
                    "observedAt": "2026-10-22T10:00:00Z"}},
]

CONNECT = {
    "sessionId": "s-1",
    "connectionUrl": "https://dcv.example.internal:8443/?authToken=abc123def#s-1",
    "osFamily": "linux",
}

WINDOWS_CONNECT = {**CONNECT, "osFamily": "windows"}

# The shape of a generated desktop password; a placeholder, never a real credential.
WINDOWS_PASSWORD = "winpw-" + "x" * 12

WINDOWS_DESKTOP_SIGN_IN = {
    "sessionId": "s-1",
    "osFamily": "windows",
    "username": "vabcdefghij23456",
    "credential": "desktopPassword",
    "password": WINDOWS_PASSWORD,
}

LINUX_SIGN_IN = {
    "sessionId": "s-1",
    "osFamily": "linux",
    "username": "vabcdefghij23456",
    "credential": "none",
    "password": None,
}

# Catalog bodies carrying the architecture fields, in the API's order: images whose architecture is
# x86_64, arm64, null and absent, and instance types listed under no group, one group and two groups.
ARCH_CATALOG_IMAGES = {'Items': [
    {'imageId': '35', 'name': 'Amazon Linux 2023 Desktop v5', 'baseOs': 'amazonlinux2023',
     'osFamily': 'linux', 'architecture': 'x86_64', 'rootSize': 100, 'category': 'amiRecipe',
     'recipeId': 'al2023-desktop', 'recipeDisplayName': 'Amazon Linux 2023 Desktop', 'recipeVersion': 5},
    {'imageId': '40', 'name': 'Ubuntu 22 Graviton', 'baseOs': 'ubuntu2204', 'osFamily': 'linux',
     'architecture': 'arm64', 'rootSize': 50, 'category': 'baseline'},
    {'imageId': '12', 'name': 'Ubuntu 22 DCV', 'baseOs': 'ubuntu2204', 'osFamily': 'linux',
     'architecture': None, 'rootSize': 50, 'category': 'baseline'},
    {'imageId': '7', 'name': 'Rocky Linux 9 DCV', 'baseOs': 'rocky9', 'osFamily': 'linux',
     'rootSize': 50, 'category': 'baseline'},
]}

ARCH_INSTANCE_TYPES = {'Items': [
    {'instanceType': 'c6i.large', 'architectures': []},
    {'instanceType': 'g5.xlarge', 'architectures': ['x86_64']},
    {'instanceType': 'm7g.large', 'architectures': ['arm64']},
    {'instanceType': 't3.large', 'architectures': ['x86_64', 'arm64']},
    {'instanceType': 'u-24tb1.112xlarge', 'architectures': ['x86_64']},
]}

# The instance types of a deployment whose desktop profile groups every type under x86_64 alone.
X86_64_ONLY_INSTANCE_TYPES = {'Items': [
    {'instanceType': 'g5.xlarge', 'architectures': ['x86_64']},
    {'instanceType': 'u-24tb1.112xlarge', 'architectures': ['x86_64']},
]}

# A catalog with an Isaac Sim recipe image whose recipe lists the types its desktops launch on, out of
# order and naming one the deployment does not allow and one of another architecture; a recipe image
# whose recipe lists none; and a baseline image.
LAUNCH_CATALOG_IMAGES = {'Items': [
    {'imageId': '36', 'name': 'NVIDIA Isaac Sim desktop v3', 'baseOs': 'ubuntu2404', 'osFamily': 'linux',
     'architecture': 'x86_64', 'rootSize': 150, 'category': 'amiRecipe', 'recipeId': 'isaacsim',
     'recipeDisplayName': 'NVIDIA Isaac Sim desktop', 'recipeVersion': 3,
     'launchInstanceTypes': ['g6e.xlarge', 'g5.2xlarge', 'p5.48xlarge', 'm7g.large']},
    {'imageId': '35', 'name': 'Amazon Linux 2023 Desktop v5', 'baseOs': 'amazonlinux2023',
     'osFamily': 'linux', 'architecture': 'x86_64', 'rootSize': 100, 'category': 'amiRecipe',
     'recipeId': 'al2023-desktop', 'recipeDisplayName': 'Amazon Linux 2023 Desktop', 'recipeVersion': 5},
    {'imageId': '12', 'name': 'Ubuntu 22 DCV', 'baseOs': 'ubuntu2204', 'osFamily': 'linux',
     'architecture': None, 'rootSize': 50, 'category': 'baseline'},
]}

LAUNCH_INSTANCE_TYPES = {'Items': [
    {'instanceType': 'g5.2xlarge', 'architectures': ['x86_64']},
    {'instanceType': 'g5.xlarge', 'architectures': ['x86_64']},
    {'instanceType': 'g6e.xlarge', 'architectures': ['x86_64']},
    {'instanceType': 'm7g.large', 'architectures': ['arm64']},
    {'instanceType': 'm7i.large', 'architectures': ['x86_64']},
]}

# An admin images body in the API's item shape: a desktop image whose architecture EDH reports and a
# simulation image for which it reports none.
ADMIN_IMAGES = {'Items': [
    {'kind': 'vdi', 'imageId': '12', 'stackName': 'ubuntu-22-dcv', 'amiId': 'ami-0123456789abcdef0',
     'baseOs': 'ubuntu2204', 'osFamily': 'linux', 'architecture': 'x86_64', 'rootSize': 100,
     'profileId': 1, 'description': 'Ubuntu desktop'},
    {'kind': 'simulation', 'imageId': '3', 'stackName': 'sim-base', 'amiId': 'ami-0fedcba9876543210',
     'baseOs': '', 'osFamily': 'linux', 'architecture': None, 'rootSize': 50, 'profileId': 4,
     'description': ''},
]}


# The errors the client raises for an AMI recipe or build the API does not find, and the hints the
# commands print with them.
RECIPE_NOT_FOUND = "AMI recipe 'team-desktop' not found"
BUILD_NOT_FOUND = "AMI recipe 'team-desktop' build v7 not found"
RECIPE_LISTING_HINT = "Use 'vamscli edh admin ami-recipe list' to see every recipe."
BUILD_LISTING_HINT = "Use 'vamscli edh admin ami-recipe builds team-desktop' to see its builds."

# Each ami-recipe command that addresses one recipe: its arguments after the recipe id and the client
# method it calls. `{file}` stands for a recipe file.
RECIPE_COMMANDS = [
    ("get", [], "get_edh_ami_recipe"),
    ("update", ["--file", "{file}"], "update_edh_ami_recipe"),
    ("duplicate", ["--recipe-id", "team-desktop-copy"], "duplicate_edh_ami_recipe"),
    ("delete", ["--confirm"], "delete_edh_ami_recipe"),
    ("build", [], "start_edh_ami_recipe_build"),
    ("builds", [], "list_edh_ami_recipe_builds"),
    ("register", ["7"], "register_edh_ami_recipe_build"),
    ("retire", ["7", "--confirm"], "retire_edh_ami_recipe_build"),
]

# The ami-recipe commands that address one build of a recipe.
BUILD_COMMANDS = [
    ("register", ["7"], "register_edh_ami_recipe_build"),
    ("retire", ["7", "--confirm"], "retire_edh_ami_recipe_build"),
]

CATALOG_IMAGES = {'Items': [
    {'imageId': '35', 'name': 'Amazon Linux 2023 Desktop v5', 'baseOs': 'amazonlinux2023',
     'osFamily': 'linux', 'rootSize': 100, 'description': 'Built from an AMI recipe',
     'category': 'amiRecipe', 'recipeId': 'al2023-desktop',
     'recipeDisplayName': 'Amazon Linux 2023 Desktop', 'recipeVersion': 5},
    {'imageId': '12', 'name': 'Ubuntu 22 DCV', 'baseOs': 'ubuntu2204', 'osFamily': 'linux',
     'rootSize': 50, 'description': None, 'category': 'baseline'},
]}

DUPLICATED = {"recipeId": "team-desktop", "source": "admin", "duplicatedFrom": "al2023-desktop",
              "assets": [{"id": "vamscli", "repoPath": "tools/VamsCLI"}]}
STACK_NAME_TAKEN = ("AMI recipe creation refused (409): stackName is already used by recipe "
                    "'al2023-desktop'")

# The client's error for a recipe delete the service refuses while a build's AMI removal is pending.
DELETE_REFUSED = "AMI recipe deletion refused (409): a build's AMI removal is pending"

# The backend model whose keepVersions bounds `ami-recipe duplicate --keep-versions` applies.
EDH_MODELS = Path(__file__).resolve().parents[4] / "backend/backend/models/addon/edh.py"

# A command row of Click's `Commands:` block: two spaces, the name, then the padded help text.
# Wrapped help continues at a deeper indent, so it never matches.
COMMAND_ROW = re.compile(r'^  (\S+)', re.M)


def _listed_commands(help_output: str) -> set:
    """The names in the Commands block of a group's --help. Only that block counts: Click prints the
    group's own help sentence above it, and that sentence names sub-groups whether or not they exist."""
    commands_block = help_output.split('Commands:', 1)[1]
    return set(COMMAND_ROW.findall(commands_block))


class TestEdhGroup:
    def test_the_group_is_registered_with_its_sub_groups(self, cli_runner):
        result = cli_runner.invoke(cli, ['edh', '--help'])
        assert result.exit_code == 0
        listed = _listed_commands(result.output)
        for sub_group in ('workstation', 'catalog', 'admin', 'container'):
            assert sub_group in listed, listed


class TestEdhFeatureGate:
    def test_a_command_refuses_when_the_workstations_switch_is_absent(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['profile_manager'].has_feature_switch.return_value = False
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'list'])
            assert result.exit_code != 0
            # The sentence is the shared constant, so the MCP server's pre-flight answers identically.
            assert EDH_WORKSTATIONS_DISABLED_MESSAGE in result.output
            mocks['profile_manager'].has_feature_switch.assert_called_with('EDH_WORKSTATIONS')
            assert not mocks['api_client'].list_edh_workstations.called

    def test_the_gate_passes_when_the_switch_is_present(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['profile_manager'].has_feature_switch.return_value = True
            mocks['api_client'].list_edh_workstations.return_value = {'Items': []}
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'list'])
            assert result.exit_code == 0
            assert 'No workstations found.' in result.output

    def test_no_setup(self, cli_runner, no_setup_command_mocks):
        # The setup gate raises before the command body runs; main() renders the message, and
        # CliRunner bypasses main(), so the error is observed on the result rather than in the output.
        with no_setup_command_mocks('addon.edh'):
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'list'])
            assert result.exit_code != 0
            assert isinstance(result.exception, SetupRequiredError)
            assert 'Setup required' in str(result.exception)


class TestWorkstationList:
    def test_list_renders_the_rows(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_workstations.return_value = {'Items': [WORKSTATION]}
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'list'])
            assert result.exit_code == 0
            assert 'Found 1 workstation(s)' in result.output
            assert 's-1' in result.output
            assert 'Design desk' in result.output
            assert 'g5.2xlarge' in result.output

    def test_list_json_output_is_the_raw_body(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_workstations.return_value = {'Items': [WORKSTATION]}
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'list', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'Items': [WORKSTATION]}

    def test_list_reports_an_unavailable_cluster(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_workstations.side_effect = EdhUnavailableError(
                "Workstation listing failed (502): the EDH cluster did not answer.")
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'list'])
            assert result.exit_code != 0
            assert 'EDH Error' in result.output
            assert 'did not answer' in result.output

    def test_list_shows_the_last_error_and_marks_a_session_less_launch(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            orphaned = {**WORKSTATION, 'bootstrapId': 'b-orphan', 'sessionId': None, 'state': 'ORPHANED',
                        'edhState': None, 'connectionAvailable': False, 'lastError': 'QUOTA_EXCEEDED'}
            mocks['api_client'].list_edh_workstations.return_value = {'Items': [WORKSTATION, orphaned]}
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'list'])
            assert result.exit_code == 0
            assert 'Found 2 workstation(s)' in result.output
            assert 'QUOTA_EXCEEDED' in result.output
            # A row without a session id shows its bootstrap id, marked, plus the footnote.
            assert 'b-orphan*' in result.output
            assert 'bootstrap id' in result.output
            assert 's-1 ' in result.output and 's-1*' not in result.output

    def test_list_text_keeps_its_columns_and_leaves_the_edh_failure_to_get(self, cli_runner, generic_command_mocks):
        # The table keeps its width; the captured failure is shown by `get` and carried by --json-output.
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_workstations.return_value = {'Items': [WORKSTATION]}
            plain = cli_runner.invoke(cli, ['edh', 'workstation', 'list'])
            mocks['api_client'].list_edh_workstations.return_value = {
                'Items': [WORKSTATION, ORPHANED_WORKSTATION]}
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'list'])
            assert result.exit_code == 0
            header = next(line for line in plain.output.splitlines() if 'Identifier' in line)
            assert header in result.output.splitlines()
            assert 'CAPACITY_UNAVAILABLE' in result.output
            assert 'InsufficientInstanceCapacity' not in result.output
            assert 'EDH failure' not in result.output
            orphan_row = next(line for line in result.output.splitlines() if 'b-orphan*' in line)
            # The Image column (D14) names the image; a record without the image fields shows its id.
            assert orphan_row.split() == ['b-orphan*', 'Design', 'desk', 'ORPHANED', 'CAPACITY_UNAVAILABLE',
                                          '12', 'g5.2xlarge', 'linux', '2026-09-08T10:00:00Z']

    def test_list_json_output_carries_the_edh_failure_unchanged(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            body = {'Items': [WORKSTATION, ORPHANED_WORKSTATION]}
            mocks['api_client'].list_edh_workstations.return_value = body
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'list', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['Items'][1]['edhFailure'] == CAPACITY_FAILURE


class TestWorkstationCreate:
    BASE = ['edh', 'workstation', 'create', '--image-id', '12', '--instance-type', 'g5.2xlarge',
            '--name', 'Design desk']
    ARGS = BASE + ['--os-family', 'linux', '--root-size-gb', '200']

    def test_create_generates_a_uuid4_bootstrap_id_and_sends_every_required_field(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].create_edh_workstation.return_value = {**WORKSTATION, 'state': 'CREATING'}
            result = cli_runner.invoke(cli, self.ARGS)
            assert result.exit_code == 0
            payload = mocks['api_client'].create_edh_workstation.call_args.args[0]
            assert uuid.UUID(payload.pop('bootstrapId')).version == 4
            assert payload == {'imageId': '12', 'instanceType': 'g5.2xlarge', 'name': 'Design desk',
                               'rootSizeGb': 200, 'osFamily': 'linux'}
            assert 'Workstation launch accepted' in result.output
            assert 'CREATING' in result.output

    def test_create_forwards_an_explicit_bootstrap_id_and_the_windows_family(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].create_edh_workstation.return_value = WORKSTATION
            result = cli_runner.invoke(
                cli, self.BASE + ['--os-family', 'windows', '--root-size-gb', '200',
                                  '--bootstrap-id', '6f1d2c3b-4a5e-4f60-8b7c-9d0e1f2a3b4c'])
            assert result.exit_code == 0
            payload = mocks['api_client'].create_edh_workstation.call_args.args[0]
            assert payload['bootstrapId'] == '6f1d2c3b-4a5e-4f60-8b7c-9d0e1f2a3b4c'
            assert payload['osFamily'] == 'windows'

    def test_create_rejects_a_bootstrap_id_that_is_not_a_uuid(self, cli_runner, generic_command_mocks):
        # The model takes a 36-character UUID; anything else would travel to the API and come back 400.
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, self.ARGS + ['--bootstrap-id', 'retry-1'])
            assert result.exit_code == 2
            assert "Invalid value for '--bootstrap-id'" in result.output
            assert not mocks['api_client'].create_edh_workstation.called

    def test_create_sends_the_bootstrap_id_in_its_hyphenated_form(self, cli_runner, generic_command_mocks):
        # click.UUID also accepts the 32-hex spelling, which the model's 36-character bound refuses.
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].create_edh_workstation.return_value = WORKSTATION
            result = cli_runner.invoke(
                cli, self.ARGS + ['--bootstrap-id', '6F1D2C3B4A5E4F608B7C9D0E1F2A3B4C'])
            assert result.exit_code == 0
            payload = mocks['api_client'].create_edh_workstation.call_args.args[0]
            assert payload['bootstrapId'] == '6f1d2c3b-4a5e-4f60-8b7c-9d0e1f2a3b4c'

    def test_create_requires_the_os_family(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, self.BASE + ['--root-size-gb', '200'])
            assert result.exit_code == 2
            assert "Missing option '--os-family'" in result.output
            assert not mocks['api_client'].create_edh_workstation.called

    def test_create_rejects_a_root_size_outside_the_range(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, self.ARGS[:-1] + ['5'])
            assert result.exit_code == 2
            assert 'not in the range' in result.output
            assert not mocks['api_client'].create_edh_workstation.called

    def test_create_rejects_an_unknown_os_family(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, self.BASE + ['--os-family', 'macos', '--root-size-gb', '200'])
            assert result.exit_code == 2
            assert "Invalid value for '--os-family'" in result.output
            assert not mocks['api_client'].create_edh_workstation.called

    def test_create_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].create_edh_workstation.return_value = {**WORKSTATION, 'state': 'CREATING'}
            result = cli_runner.invoke(cli, self.ARGS + ['--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['state'] == 'CREATING'

    def test_create_explains_the_asynchronous_launch(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].create_edh_workstation.return_value = {
                **WORKSTATION, 'bootstrapId': 'b-new', 'sessionId': None, 'state': 'CREATING',
                'edhState': None, 'connectionAvailable': False, 'launchAttempts': 0}
            result = cli_runner.invoke(cli, self.ARGS)
            assert result.exit_code == 0
            assert 'Workstation launch accepted (HTTP 202)' in result.output
            assert 'not assigned yet' in result.output
            assert 'asynchronously' in result.output
            assert 'vamscli edh workstation get b-new' in result.output
            # The bootstrap id keeps addressing the workstation after EDH assigns its session id
            # (in-dev D22).
            assert 'The session id is assigned once EDH has created the' in result.output
            assert 'the bootstrap id keeps identifying this workstation' in result.output
            assert 'until then' not in result.output

    def test_create_passes_lease_hours_when_given(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].create_edh_workstation.return_value = WORKSTATION
            result = cli_runner.invoke(cli, self.ARGS + ['--lease-hours', '4'])
            assert result.exit_code == 0
            payload = mocks['api_client'].create_edh_workstation.call_args.args[0]
            assert payload['leaseHours'] == 4

    def test_create_help_says_the_instance_type_must_run_the_images_architecture(self, cli_runner):
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'create', '--help'])
        assert result.exit_code == 0
        # Click wraps the help text, at a hyphen too, so the printed help is compared with its whitespace
        # collapsed and the option's whole sentence is read from the option itself.
        assert "it must run the image's architecture" in ' '.join(result.output.split())
        create = cli.commands['edh'].commands['workstation'].commands['create']
        option = next(param for param in create.params if param.name == 'instance_type')
        assert option.help == ("EC2 instance type; it must run the image's architecture, and a recipe image "
                               "that lists launch instance types takes one of them "
                               "(see 'vamscli edh catalog instance-types --image-id')")


class TestWorkstationGetStartStop:
    @pytest.mark.parametrize(
        "command,method,message",
        [("get", "get_edh_workstation", "Workstation retrieved"),
         ("start", "start_edh_workstation", "Workstation start requested"),
         ("stop", "stop_edh_workstation", "Workstation stop requested")],
    )
    def test_the_command_forwards_the_session_id_and_renders_the_record(
            self, cli_runner, generic_command_mocks, command, method, message):
        with generic_command_mocks('addon.edh') as mocks:
            getattr(mocks['api_client'], method).return_value = WORKSTATION
            result = cli_runner.invoke(cli, ['edh', 'workstation', command, 's-1'])
            assert result.exit_code == 0
            getattr(mocks['api_client'], method).assert_called_once_with('s-1')
            assert message in result.output
            assert 'Design desk' in result.output

    @pytest.mark.parametrize("command,method", [("get", "get_edh_workstation"),
                                                ("start", "start_edh_workstation"),
                                                ("stop", "stop_edh_workstation")])
    def test_json_output_is_the_raw_record(self, cli_runner, generic_command_mocks, command, method):
        with generic_command_mocks('addon.edh') as mocks:
            getattr(mocks['api_client'], method).return_value = WORKSTATION
            result = cli_runner.invoke(cli, ['edh', 'workstation', command, 's-1', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == WORKSTATION

    @pytest.mark.parametrize("outcome", ["provisioned", "skipped", "unavailable", "disabled"])
    def test_start_json_output_carries_the_session_bootstrap_outcome(self, cli_runner, generic_command_mocks,
                                                                     outcome):
        # The start response names the credential outcome beside the record, as the launch response does.
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.return_value = {
                **WORKSTATION, 'state': 'CREATING', 'edhState': 'pending', 'sessionBootstrap': outcome}
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'start', 's-1', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['sessionBootstrap'] == outcome

    @pytest.mark.parametrize("outcome", ["provisioned", "unavailable"])
    def test_start_text_output_names_the_session_sign_in_outcome(self, cli_runner, generic_command_mocks,
                                                                 outcome):
        # A text-mode user sees whether the desktop's vamscli sign-in was placed, as --json-output shows.
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.return_value = {
                **WORKSTATION, 'state': 'CREATING', 'sessionBootstrap': outcome, 'rootSizeGb': 60}
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'start', 's-1'])
            assert result.exit_code == 0
            assert f"Session Sign-in: {outcome}" in result.output
            assert "Root Size (GB):  60" in result.output

    @pytest.mark.parametrize("failure,text", EDH_FAILURE_TEXTS)
    def test_get_text_shows_the_failure_vams_captured_from_edh(self, cli_runner, generic_command_mocks,
                                                                failure, text):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation.return_value = {
                **ORPHANED_WORKSTATION, 'edhFailure': failure}
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'get', 'b-orphan'])
            assert result.exit_code == 0
            lines = result.output.splitlines()
            # The detail sits beneath the reason code, in the record's own label column.
            last_error = lines.index("  Last Error:      CAPACITY_UNAVAILABLE")
            assert lines[last_error + 1] == f"  EDH failure:     {text}"

    @pytest.mark.parametrize("extra", NO_EDH_FAILURES)
    def test_get_text_shows_no_edh_failure_line_without_one(self, cli_runner, generic_command_mocks, extra):
        with generic_command_mocks('addon.edh') as mocks:
            record = {key: value for key, value in ORPHANED_WORKSTATION.items() if key != 'edhFailure'}
            mocks['api_client'].get_edh_workstation.return_value = {**record, **extra}
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'get', 'b-orphan'])
            assert result.exit_code == 0
            assert "Last Error:      CAPACITY_UNAVAILABLE" in result.output
            assert "EDH failure" not in result.output

    def test_get_json_output_carries_the_edh_failure_unchanged(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation.return_value = ORPHANED_WORKSTATION
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'get', 'b-orphan', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == ORPHANED_WORKSTATION

    def test_get_help_names_the_edh_failure(self, cli_runner):
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'get', '--help'])
        assert result.exit_code == 0
        assert 'EDH failure' in result.output

    def test_get_not_found_hints_at_the_listing(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation.side_effect = EdhWorkstationNotFoundError(
                "Workstation 's-404' not found")
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'get', 's-404'])
            assert result.exit_code != 0
            assert 'Workstation Not Found' in result.output
            assert "vamscli edh workstation list" in result.output

    def test_a_refusal_for_the_records_state_is_reported_as_an_edh_error(self, cli_runner, generic_command_mocks):
        # The service answers 409 for a launch that has no EDH session yet; the client maps it to
        # EdhOperationError and the command shows the service's message.
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].stop_edh_workstation.side_effect = EdhOperationError(
                "Workstation stop refused (409): Workstation has no EDH session")
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'stop', 'b-1'])
            assert result.exit_code != 0
            assert 'EDH Error' in result.output
            assert 'no EDH session' in result.output


class TestWorkstationNotConfirmed:
    """A start, stop or delete EDH did not confirm: reported as not confirmed, never as a success."""

    NOT_CONFIRMED = ("Workstation start not confirmed (502): EDH did not confirm the request in time; "
                     "it may still be in progress. Refresh before trying again. "
                     "Run 'vamscli edh workstation list' to see its state.")

    @pytest.mark.parametrize("command,method,extra", [
        ("start", "start_edh_workstation", []),
        ("stop", "stop_edh_workstation", []),
        ("delete", "delete_edh_workstation", ['--confirm'])])
    def test_the_message_names_the_listing_and_the_exit_is_non_zero(
            self, cli_runner, generic_command_mocks, command, method, extra):
        with generic_command_mocks('addon.edh') as mocks:
            getattr(mocks['api_client'], method).side_effect = EdhOutcomeUnknownError(self.NOT_CONFIRMED)
            result = cli_runner.invoke(cli, ['edh', 'workstation', command, 's-1'] + extra)
            assert result.exit_code == 1
            assert 'not confirmed (502)' in result.output
            assert "Run 'vamscli edh workstation list' to see its state." in result.output
            assert 'failed' not in result.output

    def test_json_output_carries_the_flag_beside_the_error(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.side_effect = EdhOutcomeUnknownError(
                self.NOT_CONFIRMED)
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'start', 's-1', '--json-output'])
            assert result.exit_code == 1
            assert json.loads(result.output) == {
                'error': self.NOT_CONFIRMED,
                'error_type': 'EdhOutcomeUnknownError',
                'outcomeUnknown': True,
            }

    def test_an_unanswered_request_carries_no_flag(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.side_effect = EdhUnavailableError(
                "Workstation start failed (502): the EDH cluster did not answer. "
                "EDH is unavailable. Try again later.")
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'start', 's-1'])
            assert result.exit_code == 1
            assert 'Workstation start failed (502): the EDH cluster did not answer.' in result.output
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'start', 's-1', '--json-output'])
            assert result.exit_code == 1
            assert json.loads(result.output) == {
                'error': ("Workstation start failed (502): the EDH cluster did not answer. "
                          "EDH is unavailable. Try again later."),
                'error_type': 'EdhUnavailableError',
            }

    def test_a_start_already_in_progress_names_the_listing_and_carries_its_flag(
            self, cli_runner, generic_command_mocks):
        message = ("Workstation start already in progress (409): A start of this workstation is "
                   "already in progress. Run 'vamscli edh workstation list' to see its state.")
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.side_effect = EdhStartInProgressError(message)
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'start', 's-1'])
            assert result.exit_code == 1
            assert 'already in progress (409)' in result.output
            assert "Run 'vamscli edh workstation list' to see its state." in result.output
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'start', 's-1', '--json-output'])
            assert result.exit_code == 1
            assert json.loads(result.output) == {
                'error': message,
                'error_type': 'EdhStartInProgressError',
                'startInProgress': True,
            }

    @pytest.mark.parametrize("args,method,listing", [
        (['admin', 'instances', 'stop', 's-9'], 'stop_edh_admin_instance',
         'vamscli edh admin instances list'),
        (['container', 'job', 'kill', 'j-1', '--confirm'], 'kill_edh_container_job',
         'vamscli edh container job list')])
    def test_admin_and_container_actions_point_at_their_own_listing(
            self, cli_runner, generic_command_mocks, args, method, listing):
        message = (f"Action not confirmed (504): EDH did not confirm the request in time; it may still "
                   f"be in progress. Refresh before trying again. Run '{listing}' to see its state.")
        with generic_command_mocks('addon.edh') as mocks:
            getattr(mocks['api_client'], method).side_effect = EdhOutcomeUnknownError(message)
            result = cli_runner.invoke(cli, ['edh'] + args + ['--json-output'])
            assert result.exit_code == 1
            payload = json.loads(result.output)
            assert payload['outcomeUnknown'] is True
            assert listing in payload['error']


class TestWorkstationDelete:
    def test_delete_requires_confirm_in_cli_mode(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'delete', 's-1'])
            assert result.exit_code != 0
            assert 'requires explicit confirmation' in result.output
            assert '--confirm' in result.output
            assert not mocks['api_client'].delete_edh_workstation.called

    def test_delete_requires_confirm_in_json_mode_and_emits_one_json_object(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'delete', 's-1', '--json-output'])
            assert result.exit_code == 1
            # json.loads over the WHOLE output: a trailing "Error: ..." line from a ClickException
            # would fail here, which is the purity contract this refusal has to keep.
            assert json.loads(result.output) == {
                'error': 'Confirmation required',
                'message': 'Workstation deletion requires the --confirm flag',
                'sessionId': 's-1',
            }
            assert not mocks['api_client'].delete_edh_workstation.called

    def test_delete_with_confirm_calls_the_api(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].delete_edh_workstation.return_value = {**WORKSTATION, 'state': 'DELETED'}
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'delete', 's-1', '--confirm'])
            assert result.exit_code == 0
            mocks['api_client'].delete_edh_workstation.assert_called_once_with('s-1')
            assert 'Workstation deleted' in result.output


class TestWorkstationConnect:
    def test_cli_mode_prints_the_host_and_never_the_url(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation_connection.return_value = CONNECT
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'connect', 's-1'])
            assert result.exit_code == 0
            assert 'dcv.example.internal' in result.output
            assert 'authToken' not in result.output
            assert 'https://' not in result.output
            assert '--json-output' in result.output

    def test_json_output_carries_the_full_url(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation_connection.return_value = CONNECT
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'connect', 's-1', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['connectionUrl'] == CONNECT['connectionUrl']

    @pytest.mark.temporary  # pins the rewording of the connect help away from "shell history"
    def test_connect_help_names_where_the_url_is_written(self, cli_runner):
        # A shell records the command line, not the command's output; the URL reaches stdout.
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'connect', '--help'])
        assert result.exit_code == 0
        assert 'terminal scrollback' in result.output
        assert 'shell history' not in result.output

    def test_connect_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation_connection.side_effect = EdhWorkstationNotFoundError(
                "Workstation 's-404' not found")
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'connect', 's-404'])
            assert result.exit_code != 0
            assert 'Workstation Not Found' in result.output


class TestWorkstationConnectSignIn:
    def test_without_the_flag_the_sign_in_is_never_requested(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation_connection.return_value = WINDOWS_CONNECT
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'connect', 's-1', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == WINDOWS_CONNECT
            mocks['api_client'].get_edh_workstation_os_sign_in.assert_not_called()

    def test_a_windows_workstation_in_cli_mode_names_the_flag_and_no_password(
            self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation_connection.return_value = WINDOWS_CONNECT
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'connect', 's-1'])
            assert result.exit_code == 0
            assert 'sign-in screen' in result.output
            assert 'lock screen' not in result.output
            assert '--show-sign-in' in result.output
            assert 'User Name:' not in result.output

    def test_a_linux_workstation_gets_no_windows_hint(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation_connection.return_value = CONNECT
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'connect', 's-1'])
            assert result.exit_code == 0
            assert '--show-sign-in' not in result.output
            assert 'Windows' not in result.output
            mocks['api_client'].get_edh_workstation_os_sign_in.assert_not_called()

    def test_the_windows_only_flag_is_gone(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation_connection.return_value = WINDOWS_CONNECT
            result = cli_runner.invoke(
                cli, ['edh', 'workstation', 'connect', 's-1', '--show-windows-password'])
            assert result.exit_code == 2
            assert 'No such option' in result.output
            mocks['api_client'].get_edh_workstation_connection.assert_not_called()
            mocks['api_client'].get_edh_workstation_os_sign_in.assert_not_called()

    def test_the_flag_prints_the_sign_in_and_still_never_the_url(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation_connection.return_value = WINDOWS_CONNECT
            mocks['api_client'].get_edh_workstation_os_sign_in.return_value = WINDOWS_DESKTOP_SIGN_IN
            result = cli_runner.invoke(
                cli, ['edh', 'workstation', 'connect', 's-1', '--show-sign-in'])
            assert result.exit_code == 0
            mocks['api_client'].get_edh_workstation_os_sign_in.assert_called_once_with('s-1')
            assert 'vabcdefghij23456' in result.output
            assert WINDOWS_PASSWORD in result.output
            assert 'a credential' in result.output
            assert 'Send Ctrl + Alt + Del' in result.output
            assert 'authToken' not in result.output

    def test_the_flag_with_json_output_adds_the_sign_in_beside_the_connection(
            self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation_connection.return_value = WINDOWS_CONNECT
            mocks['api_client'].get_edh_workstation_os_sign_in.return_value = WINDOWS_DESKTOP_SIGN_IN
            result = cli_runner.invoke(
                cli, ['edh', 'workstation', 'connect', 's-1', '--show-sign-in', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {
                **WINDOWS_CONNECT,
                'signIn': {
                    'username': 'vabcdefghij23456',
                    'credential': 'desktopPassword',
                    'password': WINDOWS_PASSWORD,
                },
            }

    def test_a_directory_account_is_told_to_use_its_directory_password(
            self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation_connection.return_value = WINDOWS_CONNECT
            mocks['api_client'].get_edh_workstation_os_sign_in.return_value = {
                **WINDOWS_DESKTOP_SIGN_IN, 'username': 'CORP\\alice', 'credential': 'directoryPassword',
                'password': None}
            result = cli_runner.invoke(
                cli, ['edh', 'workstation', 'connect', 's-1', '--show-sign-in'])
            assert result.exit_code == 0
            assert 'CORP\\alice' in result.output
            assert 'directory password' in result.output
            assert 'a credential' not in result.output

    def test_a_linux_workstation_is_told_no_password_is_needed(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation_connection.return_value = CONNECT
            mocks['api_client'].get_edh_workstation_os_sign_in.return_value = LINUX_SIGN_IN
            result = cli_runner.invoke(
                cli, ['edh', 'workstation', 'connect', 's-1', '--show-sign-in'])
            assert result.exit_code == 0
            mocks['api_client'].get_edh_workstation_os_sign_in.assert_called_once_with('s-1')
            assert 'none needed: Amazon DCV signs the session in' in result.output
            assert 'does not lock its screen' in result.output
            assert 'a credential' not in result.output
            assert 'Ctrl + Alt + Del' not in result.output

    def test_a_linux_workstation_with_json_output_gets_credential_none(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation_connection.return_value = CONNECT
            mocks['api_client'].get_edh_workstation_os_sign_in.return_value = LINUX_SIGN_IN
            result = cli_runner.invoke(
                cli, ['edh', 'workstation', 'connect', 's-1', '--show-sign-in', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {
                **CONNECT,
                'signIn': {'username': 'vabcdefghij23456', 'credential': 'none', 'password': None},
            }

    def test_the_log_file_never_records_the_password(self, cli_runner, generic_command_mocks, monkeypatch):
        logged = []
        monkeypatch.setattr('vamscli.utils.json_output.log_info',
                            lambda message, *args, **kwargs: logged.append(str(message)))
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation_connection.return_value = WINDOWS_CONNECT
            mocks['api_client'].get_edh_workstation_os_sign_in.return_value = WINDOWS_DESKTOP_SIGN_IN
            result = cli_runner.invoke(
                cli, ['edh', 'workstation', 'connect', 's-1', '--show-sign-in', '--json-output'])
            assert result.exit_code == 0
        # Control: output_result itself logged the merged result (output_status logs through the same
        # function, so a bare non-empty check would not show that).
        assert any(entry.startswith("output_result:") and "signIn" in entry for entry in logged)
        assert not [entry for entry in logged if WINDOWS_PASSWORD in entry]

    def test_a_refused_sign_in_after_a_good_connect_is_one_json_error(self, cli_runner, generic_command_mocks):
        """RF3: the connection answered, but only the error document is printed, so no URL reaches
        stdout and the command exits 1."""
        message = "Workstation desktop sign-in refused (409): Windows password is not available yet"
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation_connection.return_value = WINDOWS_CONNECT
            mocks['api_client'].get_edh_workstation_os_sign_in.side_effect = EdhOperationError(message)
            result = cli_runner.invoke(
                cli, ['edh', 'workstation', 'connect', 's-1', '--show-sign-in', '--json-output'])
            assert result.exit_code == 1
            assert json.loads(result.output) == {'error': message, 'error_type': 'EdhOperationError'}
            assert 'authToken' not in result.output


class TestWorkstationRenew:
    def test_renew_forwards_the_session_id_and_renders_the_workstation(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].renew_edh_workstation.return_value = WORKSTATION
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'renew', 's-1'])
            assert result.exit_code == 0
            mocks['api_client'].renew_edh_workstation.assert_called_once_with('s-1')
            assert 'Lease renewed' in result.output
            assert 'Design desk' in result.output

    def test_renew_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].renew_edh_workstation.return_value = WORKSTATION
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'renew', 's-1', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['sessionId'] == 's-1'

    @pytest.mark.parametrize("outcome", ["provisioned", "skipped", "unavailable", "disabled"])
    def test_renew_json_output_carries_the_session_bootstrap_outcome(self, cli_runner, generic_command_mocks,
                                                                     outcome):
        # A renew that re-issued the desktop's sign-in names the outcome beside the record, as a start does.
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].renew_edh_workstation.return_value = {**WORKSTATION, 'sessionBootstrap': outcome}
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'renew', 's-1', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['sessionBootstrap'] == outcome

    def test_renew_operation_error_409(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].renew_edh_workstation.side_effect = EdhOperationError("Leases disabled")
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'renew', 's-1'])
            assert result.exit_code != 0
            assert 'EDH Error' in result.output
            assert 'Leases disabled' in result.output

    def test_renew_shows_lease_fields_in_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            record = {**WORKSTATION, 'leaseExpiresAt': '2026-09-19T10:00:00+00:00',
                      'leaseStoppedAt': None, 'leaseHoursAtLaunch': 24}
            mocks['api_client'].renew_edh_workstation.return_value = record
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'renew', 's-1'])
            assert result.exit_code == 0
            assert '2026-09-19T10:00:00+00:00' in result.output
            assert '24' in result.output
            assert 'None' not in result.output

class TestCatalog:
    def test_images_lists_one_row_per_image_with_its_category(self, cli_runner, generic_command_mocks):
        # Rows keep the API's order (recipe images first); RECIPE names the recipe and its build version.
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_images.return_value = CATALOG_IMAGES
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'images'])
            assert result.exit_code == 0
            assert 'Found 2 image(s)' in result.output
            lines = result.output.splitlines()
            header = next(line for line in lines if line.startswith('CATEGORY'))
            for column in ('IMAGE', 'NAME', 'OS', 'BASE OS', 'ROOT GIB', 'RECIPE'):
                assert column in header, header
            recipe_row = next(line for line in lines if line.startswith('Custom AMI Recipe'))
            baseline_row = next(line for line in lines if line.startswith('Baseline Image'))
            assert lines.index(recipe_row) < lines.index(baseline_row)
            assert '35' in recipe_row.split() and 'Amazon Linux 2023 Desktop v5' in recipe_row
            assert recipe_row.rstrip().endswith('al2023-desktop v5')
            assert 'Ubuntu 22 DCV' in baseline_row and '12' in baseline_row.split()
            assert baseline_row.rstrip().endswith(' -')
            # CLI mode prints the table only; --json-output carries every field.
            assert '"imageId"' not in result.output

    def test_a_name_longer_than_its_column_keeps_the_columns_aligned(self, cli_runner, generic_command_mocks):
        long_name = 'VAMS Windows Server 2022 Desktop v1'
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_images.return_value = {'Items': [
                {'imageId': '34', 'name': long_name, 'osFamily': 'windows', 'baseOs': 'windows2022',
                 'rootSize': 100, 'category': 'amiRecipe', 'recipeId': 'windows2022-desktop',
                 'recipeVersion': 1},
                {'imageId': '1', 'name': 'Amazon Linux 2', 'osFamily': 'linux', 'baseOs': 'amazonlinux2',
                 'rootSize': 40, 'category': 'baseline'}]}
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'images'])
            assert result.exit_code == 0
            lines = result.output.splitlines()
            header = next(line for line in lines if line.startswith('CATEGORY'))
            os_column = header.index(' OS ') + 1
            rows = [line for line in lines if long_name in line or 'Amazon Linux 2' in line]
            assert (rows[0][os_column:].startswith('windows') and rows[1][os_column:].startswith('linux')), (
                ' | '.join([header] + rows))

    def test_images_header_places_arch_between_os_and_base_os(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_images.return_value = ARCH_CATALOG_IMAGES
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'images'])
            assert result.exit_code == 0
            header = next(line for line in result.output.splitlines() if line.startswith('CATEGORY'))
            assert header.split() == ['CATEGORY', 'IMAGE', 'NAME', 'OS', 'ARCH', 'BASE', 'OS', 'ROOT', 'GIB',
                                      'LAUNCH', 'TYPES', 'RECIPE']

    def test_an_image_row_shows_its_architecture_or_a_dash(self, cli_runner, generic_command_mocks):
        # A null architecture and an absent one both read as unknown.
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_images.return_value = ARCH_CATALOG_IMAGES
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'images'])
            assert result.exit_code == 0
            lines = result.output.splitlines()
            arch_column = next(line for line in lines if line.startswith('CATEGORY')).index('ARCH')

            def arch_of(name):
                return next(line for line in lines if name in line)[arch_column:].split()[0]

            assert arch_of('Amazon Linux 2023 Desktop v5') == 'x86_64'
            assert arch_of('Ubuntu 22 Graviton') == 'arm64'
            assert arch_of('Ubuntu 22 DCV') == '-'
            assert arch_of('Rocky Linux 9 DCV') == '-'

    def test_a_long_name_or_architecture_keeps_every_column_aligned(self, cli_runner, generic_command_mocks):
        # The name outgrows its 34-character minimum and arm64_mac the ARCH column's 7.
        items = [
            {'imageId': '34', 'name': 'VAMS Windows Server 2022 Desktop v1 (GPU)', 'osFamily': 'windows',
             'architecture': 'x86_64', 'baseOs': 'windows2022', 'rootSize': 100, 'category': 'amiRecipe',
             'recipeId': 'windows2022-desktop', 'recipeVersion': 1},
            {'imageId': '41', 'name': 'macOS Sonoma', 'osFamily': 'linux', 'architecture': 'arm64_mac',
             'baseOs': 'macos14', 'rootSize': 200, 'category': 'baseline'},
            {'imageId': '12', 'name': 'Ubuntu 22 DCV', 'osFamily': 'linux', 'architecture': None,
             'baseOs': 'ubuntu2204', 'rootSize': 50, 'category': 'baseline'},
        ]
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_images.return_value = {'Items': items}
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'images'])
            assert result.exit_code == 0
            lines = result.output.splitlines()
            header = next(line for line in lines if line.startswith('CATEGORY'))
            columns = {name: header.index(name) for name in ('OS ', 'ARCH', 'BASE OS', 'ROOT GIB')}
            for item in items:
                row = next(line for line in lines if item['name'] in line)
                expected = {'OS ': item['osFamily'], 'ARCH': item['architecture'] or '-',
                            'BASE OS': item['baseOs'], 'ROOT GIB': str(item['rootSize'])}
                for name, offset in columns.items():
                    assert row[offset:].split()[0] == expected[name], ' | '.join([header, row])
                    assert row[offset - 1] == ' ', ' | '.join([header, row])

    def test_an_image_without_a_category_shows_a_dash(self, cli_runner, generic_command_mocks):
        # An image the API returns with no category field still lists, with a dash for its category.
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_images.return_value = {'Items': [
                {'imageId': '12', 'name': 'Ubuntu 22 DCV', 'baseOs': 'ubuntu2204', 'osFamily': 'linux',
                 'rootSize': 50}]}
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'images'])
            assert result.exit_code == 0
            row = next(line for line in result.output.splitlines() if 'Ubuntu 22 DCV' in line)
            assert row.split()[0] == '-'

    def test_images_empty(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_images.return_value = {'Items': []}
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'images'])
            assert result.exit_code == 0
            assert 'No images found.' in result.output

    def test_images_json_output_is_the_raw_body(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_images.return_value = CATALOG_IMAGES
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'images', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == CATALOG_IMAGES

    @staticmethod
    def _instance_type_rows(output):
        """The table header and its rows keyed by instance type."""
        lines = output.splitlines()
        header = next(line for line in lines if line.startswith('INSTANCE TYPE'))
        rows = lines[lines.index(header) + 1:]
        return header, {row.split()[0]: row for row in rows}

    def test_instance_types_print_a_table_of_each_type_and_its_architectures(
            self, cli_runner, generic_command_mocks):
        # ARCH joins the type's architecture groups; a type the profile does not group shows a dash.
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_instance_types.return_value = ARCH_INSTANCE_TYPES
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'instance-types'])
            assert result.exit_code == 0
            assert 'Found 5 instance type(s)' in result.output
            header, rows = self._instance_type_rows(result.output)
            assert header.split() == ['INSTANCE', 'TYPE', 'ARCH']
            arch_column = header.index('ARCH')
            assert list(rows) == ['c6i.large', 'g5.xlarge', 'm7g.large', 't3.large', 'u-24tb1.112xlarge']
            assert rows['m7g.large'][arch_column:] == 'arm64'
            assert rows['t3.large'][arch_column:] == 'x86_64,arm64'
            assert rows['c6i.large'][arch_column:] == '-'
            # The type column grows past its 16-character minimum for a longer name.
            assert rows['u-24tb1.112xlarge'][arch_column:] == 'x86_64'
            assert 'instanceType' not in result.output
            assert not mocks['api_client'].list_edh_catalog_images.called

    def test_instance_types_json_output_is_the_raw_body(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_instance_types.return_value = ARCH_INSTANCE_TYPES
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'instance-types', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == ARCH_INSTANCE_TYPES
            assert not mocks['api_client'].list_edh_catalog_images.called

    def test_instance_types_help_describes_the_image_filter(self, cli_runner):
        result = cli_runner.invoke(cli, ['edh', 'catalog', 'instance-types', '--help'])
        assert result.exit_code == 0
        help_text = ' '.join(result.output.split())
        assert ("--image-id TEXT Only the instance types that run this catalog image's architecture "
                "(see 'vamscli edh catalog images').") in help_text
        assert 'vamscli edh catalog instance-types --image-id IMAGE_ID' in help_text

    @pytest.mark.parametrize('image_id,expected', [
        ('35', ['c6i.large', 'g5.xlarge', 't3.large', 'u-24tb1.112xlarge']),
        ('40', ['c6i.large', 'm7g.large', 't3.large']),
    ], ids=['x86_64-image', 'arm64-image'])
    def test_image_id_lists_only_the_types_that_run_the_images_architecture(
            self, cli_runner, generic_command_mocks, image_id, expected):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_images.return_value = ARCH_CATALOG_IMAGES
            mocks['api_client'].list_edh_catalog_instance_types.return_value = ARCH_INSTANCE_TYPES
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'instance-types', '--image-id', image_id])
            assert result.exit_code == 0, result.output
            _, rows = self._instance_type_rows(result.output)
            assert list(rows) == expected
            assert f'Found {len(expected)} instance type(s)' in result.output
            mocks['api_client'].list_edh_catalog_images.assert_called_once_with()

    @pytest.mark.parametrize('image_id', ['12', '7'], ids=['architecture-null', 'architecture-absent'])
    def test_image_id_of_an_image_without_an_architecture_lists_every_type(
            self, cli_runner, generic_command_mocks, image_id):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_images.return_value = ARCH_CATALOG_IMAGES
            mocks['api_client'].list_edh_catalog_instance_types.return_value = ARCH_INSTANCE_TYPES
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'instance-types', '--image-id', image_id])
            assert result.exit_code == 0, result.output
            _, rows = self._instance_type_rows(result.output)
            assert list(rows) == [item['instanceType'] for item in ARCH_INSTANCE_TYPES['Items']]

    def test_image_id_with_json_output_prints_the_filtered_items(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_images.return_value = ARCH_CATALOG_IMAGES
            mocks['api_client'].list_edh_catalog_instance_types.return_value = ARCH_INSTANCE_TYPES
            result = cli_runner.invoke(
                cli, ['edh', 'catalog', 'instance-types', '--json-output', '--image-id', '40'])
            assert result.exit_code == 0, result.output
            items = ARCH_INSTANCE_TYPES['Items']
            assert json.loads(result.output) == {'Items': [items[0], items[2], items[3]]}

    def test_image_id_that_no_type_runs_names_the_images_architecture(self, cli_runner, generic_command_mocks):
        # Only a list the filter emptied names the architecture; a deployment that allows no type
        # keeps the generic message, and the JSON body stays the filtered items.
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_images.return_value = ARCH_CATALOG_IMAGES
            mocks['api_client'].list_edh_catalog_instance_types.return_value = X86_64_ONLY_INSTANCE_TYPES
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'instance-types', '--image-id', '40'])
            assert result.exit_code == 0, result.output
            assert 'Found 0 instance type(s)' in result.output
            assert ("No instance type in this deployment runs this image's architecture (arm64). "
                    "Choose another image or ask an administrator.") in result.output
            assert 'No instance types found.' not in result.output
            assert 'INSTANCE TYPE' not in result.output

            result = cli_runner.invoke(
                cli, ['edh', 'catalog', 'instance-types', '--image-id', '40', '--json-output'])
            assert result.exit_code == 0, result.output
            assert json.loads(result.output) == {'Items': []}

            mocks['api_client'].list_edh_catalog_instance_types.return_value = {'Items': []}
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'instance-types', '--image-id', '40'])
            assert result.exit_code == 0, result.output
            assert 'No instance types found.' in result.output
            assert 'No instance type in this deployment runs' not in result.output

    def test_an_unknown_image_id_exits_non_zero_naming_the_images_command(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_images.return_value = ARCH_CATALOG_IMAGES
            mocks['api_client'].list_edh_catalog_instance_types.return_value = ARCH_INSTANCE_TYPES
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'instance-types', '--image-id', 'nope'])
            assert result.exit_code != 0
            assert 'Image Not Found' in result.output
            assert "Image 'nope' is not in your image catalog" in result.output
            assert 'vamscli edh catalog images' in result.output
            # No instance type is listed for an image the caller cannot launch.
            assert 'g5.xlarge' not in result.output

    def test_an_unknown_image_id_in_json_mode_is_one_error_object(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_images.return_value = ARCH_CATALOG_IMAGES
            mocks['api_client'].list_edh_catalog_instance_types.return_value = ARCH_INSTANCE_TYPES
            result = cli_runner.invoke(
                cli, ['edh', 'catalog', 'instance-types', '--image-id', 'nope', '--json-output'])
            assert result.exit_code == 1
            error = json.loads(result.output)
            assert error['error_type'] == 'EdhImageNotFoundError'
            assert error['error'] == ("Image 'nope' is not in your image catalog. Use 'vamscli edh catalog "
                                      "images' to see the images you may launch.")

    @pytest.mark.parametrize('item,image,runs', [
        ({'instanceType': 'g5.xlarge', 'architectures': ['x86_64']}, {'architecture': 'x86_64'}, True),
        ({'instanceType': 'm7g.large', 'architectures': ['arm64']}, {'architecture': 'x86_64'}, False),
        ({'instanceType': 't3.large', 'architectures': ['x86_64', 'arm64']}, {'architecture': 'arm64'}, True),
        ({'instanceType': 'c6i.large', 'architectures': []}, {'architecture': 'x86_64'}, True),
        ({'instanceType': 'c6i.large'}, {'architecture': 'x86_64'}, True),
        ({'instanceType': 'm7g.large', 'architectures': ['arm64']}, {'architecture': None}, True),
        ({'instanceType': 'm7g.large', 'architectures': ['arm64']}, {}, True),
        ({'instanceType': 'm7g.large', 'architectures': ['arm64']}, {'architecture': ''}, True),
        ({'instanceType': 't2.micro', 'architectures': ['i386']}, {'architecture': 'x86_64'}, False),
        ({'instanceType': 'mac1.metal', 'architectures': ['x86_64_mac']}, {'architecture': 'x86_64'}, False),
        ('g5.xlarge', {'architecture': 'x86_64'}, True),
    ], ids=['listed-under-it', 'listed-under-another', 'one-of-two-groups', 'no-group', 'no-groups-field',
            'image-architecture-null', 'image-architecture-absent', 'image-architecture-empty',
            'i386-is-not-x86_64', 'exact-not-prefix', 'bare-name'])
    def test_the_matching_rule_is_the_web_wizards(self, item, image, runs):
        # The catalog rule is exact: an i386-grouped type is not offered for an x86_64 image.
        assert edh_commands.instance_type_runs_image(item, image) is runs

    def test_instance_types_empty(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_instance_types.return_value = {'Items': []}
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'instance-types'])
            assert result.exit_code == 0
            assert 'No instance types found.' in result.output

    def test_lease_renders_the_policy_as_json(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_catalog_lease.return_value = {
                'lease': {'enabled': True, 'leaseHours': 24, 'forced': False, 'graceHours': 12}}
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'lease'])
            assert result.exit_code == 0
            assert 'Lease policy retrieved' in result.output
            assert '"enabled": true' in result.output

    def test_lease_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            body = {'lease': {'enabled': True, 'leaseHours': 24}}
            mocks['api_client'].get_edh_catalog_lease.return_value = body
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'lease', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == body

    def test_catalog_is_gated_on_the_workstations_switch(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['profile_manager'].has_feature_switch.return_value = False
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'images'])
            assert result.exit_code != 0
            assert 'EDH Workstations add-on is not enabled' in result.output
            assert not mocks['api_client'].list_edh_catalog_images.called


class TestAdminImages:
    def test_list_renders_each_image(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_admin_images.return_value = {
                'Items': [{'imageId': '12', 'stackName': 'ubuntu-22-dcv', 'kind': 'vdi'}]}
            result = cli_runner.invoke(cli, ['edh', 'admin', 'images', 'list'])
            assert result.exit_code == 0
            assert 'Found 1 image(s)' in result.output
            assert '"stackName": "ubuntu-22-dcv"' in result.output

    def test_list_shows_each_images_architecture_or_null(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_admin_images.return_value = ADMIN_IMAGES
            result = cli_runner.invoke(cli, ['edh', 'admin', 'images', 'list'])
            assert result.exit_code == 0
            assert 'Found 2 image(s)' in result.output
            assert '"architecture": "x86_64"' in result.output
            assert '"architecture": null' in result.output

    def test_list_json_output_carries_the_architecture_of_every_image(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_admin_images.return_value = ADMIN_IMAGES
            result = cli_runner.invoke(cli, ['edh', 'admin', 'images', 'list', '--json-output'])
            assert result.exit_code == 0
            payload = json.loads(result.output)
            assert payload == ADMIN_IMAGES
            assert [item['architecture'] for item in payload['Items']] == ['x86_64', None]

    def test_register_vdi_builds_the_model_body_and_omits_absent_optionals(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].register_edh_vdi_image.return_value = {'registered': 'ubuntu-22-dcv'}
            result = cli_runner.invoke(cli, [
                'edh', 'admin', 'images', 'register-vdi', '--ami-id', 'ami-0123', '--stack-name',
                'ubuntu-22-dcv', '--base-os', 'ubuntu2204', '--root-size', '100',
                '--virtual-desktop-profile-id', '3'])
            assert result.exit_code == 0
            mocks['api_client'].register_edh_vdi_image.assert_called_once_with({
                'amiId': 'ami-0123', 'stackName': 'ubuntu-22-dcv', 'baseOs': 'ubuntu2204',
                'rootSize': 100, 'virtualDesktopProfileId': 3})
            assert 'VDI image registered' in result.output

    def test_register_vdi_forwards_the_optionals(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].register_edh_vdi_image.return_value = {}
            result = cli_runner.invoke(cli, [
                'edh', 'admin', 'images', 'register-vdi', '--ami-id', 'ami-0123', '--stack-name',
                'win-dcv', '--base-os', 'windows2022', '--root-size', '150',
                '--virtual-desktop-profile-id', '3', '--launch-tenancy', 'dedicated',
                '--description', 'Windows desktop', '--json-output'])
            assert result.exit_code == 0
            payload = mocks['api_client'].register_edh_vdi_image.call_args.args[0]
            assert payload['launchTenancy'] == 'dedicated'
            assert payload['description'] == 'Windows desktop'

    def test_register_simulation_builds_the_model_body(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].register_edh_simulation_image.return_value = {}
            result = cli_runner.invoke(cli, [
                'edh', 'admin', 'images', 'register-simulation', '--ami-id', 'ami-0456',
                '--stack-name', 'isaac-sim', '--root-size', '200', '--target-node-profile-id', '7',
                '--user-data-variables', 'MODE=headless', '--description', 'Isaac Sim node'])
            assert result.exit_code == 0
            mocks['api_client'].register_edh_simulation_image.assert_called_once_with({
                'amiId': 'ami-0456', 'stackName': 'isaac-sim', 'rootSize': 200,
                'targetNodeProfileId': 7, 'userDataVariables': 'MODE=headless',
                'description': 'Isaac Sim node'})
            assert 'Simulation image registered' in result.output

    def test_register_simulation_sends_the_user_data_template_only_when_given(self, cli_runner, generic_command_mocks):
        """The body carries targetNodeUserDataId only with the option, so the server keeps its own
        default (template 1) for a call that leaves it out."""
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].register_edh_simulation_image.return_value = {}
            base = ['edh', 'admin', 'images', 'register-simulation', '--ami-id', 'ami-0456',
                    '--stack-name', 'isaac-sim', '--root-size', '200', '--target-node-profile-id', '7']
            result = cli_runner.invoke(cli, base)
            assert result.exit_code == 0
            payload = mocks['api_client'].register_edh_simulation_image.call_args.args[0]
            assert 'targetNodeUserDataId' not in payload
            mocks['api_client'].register_edh_simulation_image.reset_mock()
            result = cli_runner.invoke(cli, base + ['--target-node-user-data-id', '3'])
            assert result.exit_code == 0
            payload = mocks['api_client'].register_edh_simulation_image.call_args.args[0]
            assert payload['targetNodeUserDataId'] == 3

    @pytest.mark.parametrize("command,method,extra,root_size", [
        ("register-vdi", "register_edh_vdi_image",
         ['--base-os', 'ubuntu2204', '--virtual-desktop-profile-id', '3'], '0'),
        ("register-simulation", "register_edh_simulation_image", ['--target-node-profile-id', '7'], '2049'),
    ])
    def test_register_rejects_a_root_size_outside_the_range(self, cli_runner, generic_command_mocks,
                                                            command, method, extra, root_size):
        # The model bounds rootSize to 1-2048; the option refuses the rest before any request.
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, [
                'edh', 'admin', 'images', command, '--ami-id', 'ami-0123', '--stack-name', 'stack',
                '--root-size', root_size] + extra)
            assert result.exit_code == 2
            assert "Invalid value for '--root-size'" in result.output
            assert not getattr(mocks['api_client'], method).called

    def test_register_simulation_rejects_a_user_data_template_below_one(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, [
                'edh', 'admin', 'images', 'register-simulation', '--ami-id', 'ami-0456',
                '--stack-name', 'isaac-sim', '--root-size', '200', '--target-node-profile-id', '7',
                '--target-node-user-data-id', '0'])
            assert result.exit_code == 2
            assert "Invalid value for '--target-node-user-data-id'" in result.output
            assert not mocks['api_client'].register_edh_simulation_image.called

    @pytest.mark.parametrize("command,method", [("delete-vdi", "delete_edh_vdi_image"),
                                                ("delete-simulation", "delete_edh_simulation_image")])
    def test_deletes_require_confirm(self, cli_runner, generic_command_mocks, command, method):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, ['edh', 'admin', 'images', command, 'img-7'])
            assert result.exit_code != 0
            assert 'requires explicit confirmation' in result.output
            assert not getattr(mocks['api_client'], method).called
            result = cli_runner.invoke(cli, ['edh', 'admin', 'images', command, 'img-7', '--json-output'])
            assert result.exit_code == 1
            assert json.loads(result.output) == {
                'error': 'Confirmation required',
                'message': 'Image deletion requires the --confirm flag',
                'imageId': 'img-7',
            }
            assert not getattr(mocks['api_client'], method).called

    @pytest.mark.parametrize("command,method,other", [
        ("delete-vdi", "delete_edh_vdi_image", "delete_edh_simulation_image"),
        ("delete-simulation", "delete_edh_simulation_image", "delete_edh_vdi_image")])
    def test_deletes_reach_their_own_kind_route(self, cli_runner, generic_command_mocks, command, method, other):
        with generic_command_mocks('addon.edh') as mocks:
            getattr(mocks['api_client'], method).return_value = {'deleted': 'img-7'}
            result = cli_runner.invoke(cli, ['edh', 'admin', 'images', command, 'img-7', '--confirm'])
            assert result.exit_code == 0
            getattr(mocks['api_client'], method).assert_called_once_with('img-7')
            assert not getattr(mocks['api_client'], other).called
            assert 'Image deleted' in result.output

    def test_delete_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].delete_edh_vdi_image.side_effect = EdhImageNotFoundError("Image 'img-404' not found")
            result = cli_runner.invoke(cli, ['edh', 'admin', 'images', 'delete-vdi', 'img-404', '--confirm'])
            assert result.exit_code != 0
            assert 'Image Not Found' in result.output
            assert "vamscli edh admin images list" in result.output


class TestAdminInstances:
    @pytest.mark.parametrize("command,method,noun", [
        ("list", "list_edh_admin_instances", "session(s)"),
        ("stale", "list_edh_stale_instances", "stale session(s)")])
    def test_listings_render_each_session(self, cli_runner, generic_command_mocks, command, method, noun):
        with generic_command_mocks('addon.edh') as mocks:
            getattr(mocks['api_client'], method).return_value = {
                'Items': [{'sessionId': 's-9', 'owner': 'vabcdefghij23456'}]}
            result = cli_runner.invoke(cli, ['edh', 'admin', 'instances', command])
            assert result.exit_code == 0
            assert f'Found 1 {noun}' in result.output
            assert '"sessionId": "s-9"' in result.output

    @pytest.mark.parametrize("command,method", [
        ("list", "list_edh_admin_instances"), ("stale", "list_edh_stale_instances")])
    @pytest.mark.parametrize("failure,text", EDH_FAILURE_TEXTS)
    def test_listings_show_the_edh_failure_beneath_the_session_that_carries_it(
            self, cli_runner, generic_command_mocks, command, method, failure, text):
        with generic_command_mocks('addon.edh') as mocks:
            getattr(mocks['api_client'], method).return_value = {'Items': [
                {'sessionId': 's-9', 'state': 'ACTIVE', 'lastError': None, 'edhFailure': None},
                {'sessionId': None, 'bootstrapId': 'b-8', 'state': 'ORPHANED',
                 'lastError': 'CAPACITY_UNAVAILABLE', 'edhFailure': failure},
            ]}
            result = cli_runner.invoke(cli, ['edh', 'admin', 'instances', command])
            assert result.exit_code == 0
            # Each session is still its JSON block, the failure map included; the failed one gains the line.
            assert '"bootstrapId": "b-8"' in result.output
            assert '"edhFailure": {' in result.output
            assert result.output.count("EDH failure:") == 1
            lines = result.output.splitlines()
            detail = lines.index(f"  EDH failure:     {text}")
            assert lines[detail - 1] == "}"
            assert lines.index('  "bootstrapId": "b-8",') < detail

    @pytest.mark.parametrize("command,method", [
        ("list", "list_edh_admin_instances"), ("stale", "list_edh_stale_instances")])
    @pytest.mark.parametrize("extra", NO_EDH_FAILURES)
    def test_listings_show_no_edh_failure_line_without_one(self, cli_runner, generic_command_mocks,
                                                           command, method, extra):
        with generic_command_mocks('addon.edh') as mocks:
            getattr(mocks['api_client'], method).return_value = {'Items': [
                {'sessionId': None, 'bootstrapId': 'b-8', 'state': 'ORPHANED', 'lastError': 'LAUNCH_ERROR',
                 **extra}]}
            result = cli_runner.invoke(cli, ['edh', 'admin', 'instances', command])
            assert result.exit_code == 0
            assert '"bootstrapId": "b-8"' in result.output
            assert "EDH failure:" not in result.output

    @pytest.mark.parametrize("command,method", [
        ("list", "list_edh_admin_instances"), ("stale", "list_edh_stale_instances")])
    def test_listings_json_output_carries_the_edh_failure_unchanged(self, cli_runner, generic_command_mocks,
                                                                    command, method):
        with generic_command_mocks('addon.edh') as mocks:
            body = {'Items': [{'sessionId': None, 'bootstrapId': 'b-8', 'state': 'ORPHANED',
                               'lastError': 'CAPACITY_UNAVAILABLE', 'edhFailure': CAPACITY_FAILURE}]}
            getattr(mocks['api_client'], method).return_value = body
            result = cli_runner.invoke(cli, ['edh', 'admin', 'instances', command, '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == body

    @pytest.mark.parametrize("command", ["list", "stale"])
    def test_listing_help_names_the_edh_failure(self, cli_runner, command):
        result = cli_runner.invoke(cli, ['edh', 'admin', 'instances', command, '--help'])
        assert result.exit_code == 0
        assert 'edhFailure' in result.output

    def test_stale_forwards_idle_minutes_only_when_given(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_stale_instances.return_value = {'Items': []}
            result = cli_runner.invoke(cli, ['edh', 'admin', 'instances', 'stale'])
            assert result.exit_code == 0
            mocks['api_client'].list_edh_stale_instances.assert_called_once_with(idle_minutes=None)
            mocks['api_client'].list_edh_stale_instances.reset_mock()
            result = cli_runner.invoke(cli, ['edh', 'admin', 'instances', 'stale', '--idle-minutes', '45'])
            assert result.exit_code == 0
            mocks['api_client'].list_edh_stale_instances.assert_called_once_with(idle_minutes=45)

    def test_stale_rejects_a_non_positive_idle_threshold(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, ['edh', 'admin', 'instances', 'stale', '--idle-minutes', '0'])
            assert result.exit_code == 2
            assert "Invalid value for '--idle-minutes'" in result.output
            assert not mocks['api_client'].list_edh_stale_instances.called

    def test_stop_needs_no_confirmation(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].stop_edh_admin_instance.return_value = {'sessionId': 's-9', 'state': 'STOPPED'}
            result = cli_runner.invoke(cli, ['edh', 'admin', 'instances', 'stop', 's-9'])
            assert result.exit_code == 0
            mocks['api_client'].stop_edh_admin_instance.assert_called_once_with('s-9')
            assert 'Instance stop requested' in result.output

    def test_deactivate_requires_confirm(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, ['edh', 'admin', 'instances', 'deactivate', 's-9'])
            assert result.exit_code != 0
            assert 'requires explicit confirmation' in result.output
            assert not mocks['api_client'].deactivate_edh_admin_instance.called

    def test_deactivate_with_confirm_calls_the_api(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].deactivate_edh_admin_instance.return_value = {'sessionId': 's-9'}
            result = cli_runner.invoke(cli, ['edh', 'admin', 'instances', 'deactivate', 's-9', '--confirm', '--json-output'])
            assert result.exit_code == 0
            mocks['api_client'].deactivate_edh_admin_instance.assert_called_once_with('s-9')
            assert json.loads(result.output) == {'sessionId': 's-9'}

    def test_stop_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].stop_edh_admin_instance.side_effect = EdhWorkstationNotFoundError("Session 's-404' not found")
            result = cli_runner.invoke(cli, ['edh', 'admin', 'instances', 'stop', 's-404'])
            assert result.exit_code != 0
            assert 'Session Not Found' in result.output


class TestAdminCapacityAndHealth:
    @pytest.mark.parametrize("command,method,message", [
        ("capacity", "get_edh_capacity", "Capacity retrieved"),
        ("health", "get_edh_health", "Health retrieved")])
    def test_the_body_is_rendered_as_json_in_both_modes(self, cli_runner, generic_command_mocks, command, method, message):
        with generic_command_mocks('addon.edh') as mocks:
            getattr(mocks['api_client'], method).return_value = {'status': 'ok', 'detail': {'gpus': 4}}
            result = cli_runner.invoke(cli, ['edh', 'admin', command])
            assert result.exit_code == 0
            assert message in result.output
            assert '"gpus": 4' in result.output
            result = cli_runner.invoke(cli, ['edh', 'admin', command, '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'status': 'ok', 'detail': {'gpus': 4}}

    def test_a_failed_health_probe_surfaces_the_reason_in_both_modes(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_health.side_effect = EdhUnavailableError(
                "Health check failed (502): EDH rejected the deployment credential. [reason: credentialRejected]")
            result = cli_runner.invoke(cli, ['edh', 'admin', 'health'])
            assert result.exit_code != 0
            assert 'EDH Error' in result.output
            assert 'credentialRejected' in result.output
            result = cli_runner.invoke(cli, ['edh', 'admin', 'health', '--json-output'])
            assert result.exit_code == 1
            payload = json.loads(result.output)
            assert payload['error_type'] == 'EdhUnavailableError'
            assert 'credentialRejected' in payload['error']


class TestAdminSettings:
    def test_settings_get_renders_the_policy_as_json(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_admin_settings.return_value = {
                'lease': {'enabled': True, 'leaseHours': 24, 'forced': False, 'graceHours': 12}}
            result = cli_runner.invoke(cli, ['edh', 'admin', 'settings', 'get'])
            assert result.exit_code == 0
            assert 'Lease policy' in result.output
            assert '"enabled": true' in result.output

    def test_settings_get_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            body = {'lease': {'enabled': True}}
            mocks['api_client'].get_edh_admin_settings.return_value = body
            result = cli_runner.invoke(cli, ['edh', 'admin', 'settings', 'get', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == body

    def test_settings_set_sends_exactly_the_flags_given(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].update_edh_admin_settings.return_value = {'lease': {'enabled': True}}
            result = cli_runner.invoke(cli, ['edh', 'admin', 'settings', 'set', '--enabled',
                                             '--lease-hours', '12', '--forced', '--grace-hours', '6'])
            assert result.exit_code == 0
            sent = mocks['api_client'].update_edh_admin_settings.call_args.args[0]
            assert sent == {'enabled': True, 'leaseHours': 12, 'forced': True, 'graceHours': 6}
            assert 'Lease policy updated' in result.output

    def test_settings_set_sends_a_partial_patch(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].update_edh_admin_settings.return_value = {'lease': {'graceHours': 6}}
            result = cli_runner.invoke(cli, ['edh', 'admin', 'settings', 'set', '--grace-hours', '6'])
            assert result.exit_code == 0
            sent = mocks['api_client'].update_edh_admin_settings.call_args.args[0]
            assert sent == {'graceHours': 6}

    def test_settings_set_refuses_an_empty_patch(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, ['edh', 'admin', 'settings', 'set'])
            assert result.exit_code == 2
            assert 'Pass at least one of' in result.output
            assert not mocks['api_client'].update_edh_admin_settings.called

    def test_settings_set_sends_false_for_the_negative_flags(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].update_edh_admin_settings.return_value = {'lease': {'enabled': False, 'forced': False, 'leaseHours': 8, 'graceHours': 0}}
            result = cli_runner.invoke(cli, ['edh', 'admin', 'settings', 'set', '--disabled', '--no-forced', '--grace-hours', '0'])
            assert result.exit_code == 0
            sent = mocks['api_client'].update_edh_admin_settings.call_args.args[0]
            assert sent == {'enabled': False, 'forced': False, 'graceHours': 0}


class TestContainer:
    SUBMIT = ['edh', 'container', 'job', 'submit', '--job-definition', 'render-def', '--name',
              'render-1', '--job-queue', 'gpu-queue', '--backend', 'batch']
    SUBMIT_BODY = {'jobDefinition': 'render-def', 'name': 'render-1', 'jobQueue': 'gpu-queue',
                   'backend': 'batch'}
    JOB = {'jobId': 'j-1', 'jobName': 'render-1', 'status': 'RUNNING', 'backend': 'batch',
           'edhJobDefinition': 'render-def', 'jobHandle': 'batch:j-1'}

    def test_container_commands_are_gated_on_the_containers_switch(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['profile_manager'].has_feature_switch.return_value = False
            result = cli_runner.invoke(cli, ['edh', 'container', 'catalog'])
            assert result.exit_code != 0
            assert EDH_CONTAINERS_DISABLED_MESSAGE in result.output
            mocks['profile_manager'].has_feature_switch.assert_called_with('EDH_CONTAINERS')
            assert not mocks['api_client'].list_edh_container_catalog.called

    def test_catalog_renders_the_body_as_json(self, cli_runner, generic_command_mocks):
        # The container catalog is kind-tagged Items plus the eksEnabled flag, rendered verbatim so the
        # flag survives (an Items-only formatter would drop it).
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_container_catalog.return_value = {
                'Items': [{'kind': 'jobDefinition', 'name': 'render-def'},
                          {'kind': 'jobQueue', 'name': 'gpu-queue'}],
                'eksEnabled': False}
            result = cli_runner.invoke(cli, ['edh', 'container', 'catalog'])
            assert result.exit_code == 0
            assert 'Container catalog retrieved' in result.output
            assert '"name": "render-def"' in result.output
            assert '"eksEnabled": false' in result.output

    def test_job_list_renders_each_job(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_container_jobs.return_value = {'Items': [self.JOB]}
            result = cli_runner.invoke(cli, ['edh', 'container', 'job', 'list'])
            assert result.exit_code == 0
            assert 'Found 1 job(s)' in result.output
            assert '"jobId": "j-1"' in result.output
            assert '"edhJobDefinition": "render-def"' in result.output

    def test_job_list_forwards_the_scope_only_when_given(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_container_jobs.return_value = {'Items': []}
            result = cli_runner.invoke(cli, ['edh', 'container', 'job', 'list'])
            assert result.exit_code == 0
            mocks['api_client'].list_edh_container_jobs.assert_called_once_with(
                backend=None, cluster=None, namespace=None)
            mocks['api_client'].list_edh_container_jobs.reset_mock()
            result = cli_runner.invoke(
                cli, ['edh', 'container', 'job', 'list', '--backend', 'eks', '--cluster', 'sim-cluster'])
            assert result.exit_code == 0
            mocks['api_client'].list_edh_container_jobs.assert_called_once_with(
                backend='eks', cluster='sim-cluster', namespace=None)
            mocks['api_client'].list_edh_container_jobs.reset_mock()
            result = cli_runner.invoke(cli, ['edh', 'container', 'job', 'list', '--backend', 'eks',
                                             '--cluster', 'sim-cluster', '--namespace', 'render'])
            assert result.exit_code == 0
            mocks['api_client'].list_edh_container_jobs.assert_called_once_with(
                backend='eks', cluster='sim-cluster', namespace='render')
            result = cli_runner.invoke(cli, ['edh', 'container', 'job', 'list', '--backend', 'slurm'])
            assert result.exit_code == 2

    def test_job_list_requires_a_cluster_for_eks_and_refuses_one_for_batch(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, ['edh', 'container', 'job', 'list', '--backend', 'eks'])
            assert result.exit_code == 2
            assert "Missing option '--cluster'" in result.output
            result = cli_runner.invoke(cli, ['edh', 'container', 'job', 'list', '--cluster', 'sim-cluster'])
            assert result.exit_code == 2
            assert "'--cluster'" in result.output and 'eks backend' in result.output
            result = cli_runner.invoke(cli, ['edh', 'container', 'job', 'list', '--namespace', 'render'])
            assert result.exit_code == 2
            assert "'--namespace'" in result.output and 'eks backend' in result.output
            assert not mocks['api_client'].list_edh_container_jobs.called

    def test_submit_builds_the_model_body_and_omits_absent_optionals(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].submit_edh_container_job.return_value = {'jobId': 'j-1'}
            result = cli_runner.invoke(cli, self.SUBMIT)
            assert result.exit_code == 0
            mocks['api_client'].submit_edh_container_job.assert_called_once_with(self.SUBMIT_BODY)
            assert 'Container job submitted' in result.output

    def test_submit_requires_a_job_queue(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            without_queue = [part for part in self.SUBMIT if part not in ('--job-queue', 'gpu-queue')]
            result = cli_runner.invoke(cli, without_queue)
            assert result.exit_code == 2
            assert "Missing option '--job-queue'" in result.output
            assert not mocks['api_client'].submit_edh_container_job.called

    def test_submit_forwards_every_optional(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].submit_edh_container_job.return_value = {'jobId': 'j-1'}
            result = cli_runner.invoke(cli, self.SUBMIT + [
                '--command', 'python', '--command', 'main.py',
                '--env', 'A=1', '--env', 'B=x=y', '--vcpus', '4', '--memory', '16384', '--gpu', '1',
                '--json-output'])
            assert result.exit_code == 0
            payload = mocks['api_client'].submit_edh_container_job.call_args.args[0]
            assert payload['jobQueue'] == 'gpu-queue'
            assert payload['command'] == ['python', 'main.py']
            # Split on the FIRST '=' only, so a value may itself contain '='.
            assert payload['environment'] == {'A': '1', 'B': 'x=y'}
            assert payload['vcpus'] == 4
            assert payload['memory'] == 16384
            assert payload['gpu'] == 1
            assert json.loads(result.output) == {'jobId': 'j-1'}

    def test_submit_rejects_vcpus_below_one(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, self.SUBMIT + ['--vcpus', '0'])
            assert result.exit_code == 2
            assert "Invalid value for '--vcpus'" in result.output
            assert not mocks['api_client'].submit_edh_container_job.called

    def test_submit_rejects_a_negative_gpu_count_but_accepts_zero(self, cli_runner, generic_command_mocks):
        """The model bounds gpu at >= 0, so a CPU-only job names zero GPUs explicitly."""
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, self.SUBMIT + ['--gpu', '-1'])
            assert result.exit_code == 2
            assert "Invalid value for '--gpu'" in result.output
            assert not mocks['api_client'].submit_edh_container_job.called
            mocks['api_client'].submit_edh_container_job.return_value = {'jobId': 'j-1'}
            result = cli_runner.invoke(cli, self.SUBMIT + ['--gpu', '0'])
            assert result.exit_code == 0
            assert mocks['api_client'].submit_edh_container_job.call_args.args[0]['gpu'] == 0

    def test_submit_rejects_memory_below_512_mib(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, self.SUBMIT + ['--memory', '256'])
            assert result.exit_code == 2
            assert "Invalid value for '--memory'" in result.output and '512' in result.output
            assert not mocks['api_client'].submit_edh_container_job.called

    def test_submit_rejects_an_env_pair_without_an_equals_sign(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, self.SUBMIT + ['--env', 'NOVALUE'])
            assert result.exit_code == 2
            assert 'KEY=VALUE' in result.output
            assert not mocks['api_client'].submit_edh_container_job.called

    def test_submit_rejects_an_unknown_backend(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, self.SUBMIT[:-1] + ['slurm'])
            assert result.exit_code == 2
            assert "Invalid value for '--backend'" in result.output
            assert not mocks['api_client'].submit_edh_container_job.called

    def test_kill_requires_confirm(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, ['edh', 'container', 'job', 'kill', 'j-1'])
            assert result.exit_code != 0
            assert 'requires explicit confirmation' in result.output
            assert not mocks['api_client'].kill_edh_container_job.called
            result = cli_runner.invoke(cli, ['edh', 'container', 'job', 'kill', 'j-1', '--json-output'])
            assert result.exit_code == 1
            assert json.loads(result.output) == {
                'error': 'Confirmation required',
                'message': 'Job kill requires the --confirm flag',
                'jobId': 'j-1',
            }
            assert not mocks['api_client'].kill_edh_container_job.called

    def test_kill_with_confirm_calls_the_api(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].kill_edh_container_job.return_value = {
                'jobId': 'j-1', 'backend': 'batch', 'message': 'Job termination requested'}
            result = cli_runner.invoke(cli, ['edh', 'container', 'job', 'kill', 'j-1', '--confirm'])
            assert result.exit_code == 0
            mocks['api_client'].kill_edh_container_job.assert_called_once_with(
                'j-1', backend=None, cluster=None, namespace=None)
            assert 'Container job kill requested' in result.output
            assert '"jobId": "j-1"' in result.output

    def test_kill_forwards_the_scope(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            killed = {'jobId': 'render-1', 'backend': 'eks', 'message': '', 'cluster': 'sim-cluster',
                      'namespace': 'default'}
            mocks['api_client'].kill_edh_container_job.return_value = killed
            result = cli_runner.invoke(cli, ['edh', 'container', 'job', 'kill', 'render-1', '--backend', 'eks',
                                             '--cluster', 'sim-cluster', '--confirm', '--json-output'])
            assert result.exit_code == 0
            mocks['api_client'].kill_edh_container_job.assert_called_once_with(
                'render-1', backend='eks', cluster='sim-cluster', namespace=None)
            assert json.loads(result.output) == killed
            mocks['api_client'].kill_edh_container_job.reset_mock()
            # The scope is checked before the confirmation, so an eks kill without a cluster is a usage error.
            result = cli_runner.invoke(cli, ['edh', 'container', 'job', 'kill', 'render-1', '--backend', 'eks'])
            assert result.exit_code == 2
            assert "Missing option '--cluster'" in result.output
            assert not mocks['api_client'].kill_edh_container_job.called

    def test_kill_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].kill_edh_container_job.side_effect = EdhContainerJobNotFoundError("Job 'j-404' not found")
            result = cli_runner.invoke(cli, ['edh', 'container', 'job', 'kill', 'j-404', '--confirm'])
            assert result.exit_code != 0
            assert 'Job Not Found' in result.output
            assert "vamscli edh container job list" in result.output


class TestAmiRecipeCommands:
    def test_the_group_is_registered(self, cli_runner):
        result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "--help"])
        assert result.exit_code == 0
        for name in ("list", "get", "create", "update", "duplicate", "delete", "build", "builds", "register",
                     "retire"):
            assert name in result.output

    def test_build_calls_the_client_and_reports_the_version(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].start_edh_ami_recipe_build.return_value = {"version": 3, "status": "BUILDING", "outcome": "started"}
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "build", "al2023-desktop"])
            assert result.exit_code == 0 and "v3" in result.output
            mocks["api_client"].start_edh_ami_recipe_build.assert_called_once_with("al2023-desktop")

    def test_a_build_already_in_progress_is_named_and_is_not_a_failure(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].start_edh_ami_recipe_build.return_value = {"version": 7, "status": "BUILDING", "outcome": "in-progress"}
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "build", "isaacsim"])
            assert result.exit_code == 0
            assert "Build already in progress: isaacsim v7" in result.output

    def test_json_output_passes_the_build_answer_through(self, cli_runner, generic_command_mocks):
        answer = {"recipeId": "isaacsim", "version": 7, "status": "BUILDING", "outcome": "in-progress"}
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].start_edh_ami_recipe_build.return_value = dict(answer)
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "build", "isaacsim", "--json-output"])
            assert result.exit_code == 0 and json.loads(result.output) == answer

    def test_create_reads_the_file_and_inlines_file_components(self, cli_runner, generic_command_mocks, tmp_path):
        (tmp_path / "components").mkdir()
        (tmp_path / "components" / "010-baseline.yml").write_text("schemaVersion: 1.0\n")
        (tmp_path / "recipe.json").write_text(json.dumps({"recipeId": "r", "components": [{"file": "components/010-baseline.yml"}, {"arn": "arn:x"}]}))
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].create_edh_ami_recipe.return_value = {"recipeId": "r"}
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "create", "--file", str(tmp_path / "recipe.json"), "--json-output"])
            assert result.exit_code == 0
            body = mocks["api_client"].create_edh_ami_recipe.call_args.args[0]
            assert body["components"] == [{"name": "010-baseline", "data": "schemaVersion: 1.0\n"}, {"arn": "arn:x"}]

    def test_a_component_file_outside_the_recipe_directory_is_refused(self, cli_runner, generic_command_mocks, tmp_path):
        (tmp_path / "outside.yml").write_text("schemaVersion: 1.0\n")
        recipe_dir = tmp_path / "recipe"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.json").write_text(json.dumps({"recipeId": "r", "components": [{"file": "../outside.yml"}]}))
        with generic_command_mocks("addon.edh") as mocks:
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "create", "--file", str(recipe_dir / "recipe.json")])
            assert result.exit_code != 0
            assert "outside.yml" in result.output
            mocks["api_client"].create_edh_ami_recipe.assert_not_called()

    def test_delete_and_retire_require_confirm(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("addon.edh") as mocks:
            assert cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "delete", "r"]).exit_code != 0
            mocks["api_client"].delete_edh_ami_recipe.assert_not_called()
            assert cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "retire", "r", "2"]).exit_code != 0
            mocks["api_client"].retire_edh_ami_recipe_build.assert_not_called()
            mocks["api_client"].retire_edh_ami_recipe_build.return_value = {"status": "RETIRED"}
            assert cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "retire", "r", "2", "--confirm"]).exit_code == 0
            mocks["api_client"].retire_edh_ami_recipe_build.assert_called_once_with("r", 2)

    def test_the_feature_gate_names_ami_recipes(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("addon.edh") as mocks:
            mocks["profile_manager"].has_feature_switch.return_value = False
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "list"])
            assert result.exit_code != 0 and "AMI recipes" in result.output


def _recipe_command(command, rest, tmp_path):
    """The argv of an ami-recipe command for recipe team-desktop, `{file}` replaced by a recipe file."""
    recipe_file = tmp_path / "recipe.json"
    recipe_file.write_text(json.dumps({"recipeId": "team-desktop", "components": []}))
    return ["edh", "admin", "ami-recipe", command, "team-desktop",
            *[str(recipe_file) if arg == "{file}" else arg for arg in rest]]


class TestAmiRecipeNotFound:
    @pytest.mark.parametrize("command,rest,method", RECIPE_COMMANDS)
    def test_a_missing_recipe_names_the_recipe_listing(self, cli_runner, generic_command_mocks, tmp_path,
                                                       command, rest, method):
        with generic_command_mocks("addon.edh") as mocks:
            getattr(mocks["api_client"], method).side_effect = EdhAmiRecipeNotFoundError(RECIPE_NOT_FOUND)
            result = cli_runner.invoke(cli, _recipe_command(command, rest, tmp_path))
            assert result.exit_code != 0
            assert f"Recipe Not Found: {RECIPE_NOT_FOUND}" in result.output
            assert RECIPE_LISTING_HINT in result.output
            assert "EDH Error" not in result.output

    @pytest.mark.parametrize("command,rest,method", BUILD_COMMANDS)
    def test_a_missing_build_names_the_recipes_builds(self, cli_runner, generic_command_mocks, tmp_path,
                                                      command, rest, method):
        with generic_command_mocks("addon.edh") as mocks:
            getattr(mocks["api_client"], method).side_effect = EdhAmiRecipeBuildNotFoundError(BUILD_NOT_FOUND)
            result = cli_runner.invoke(cli, _recipe_command(command, rest, tmp_path))
            assert result.exit_code != 0
            assert f"Build Not Found: {BUILD_NOT_FOUND}" in result.output
            assert BUILD_LISTING_HINT in result.output
            assert "EDH Error" not in result.output

    def test_get_of_a_missing_recipe_in_json_mode_is_one_error_object(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].get_edh_ami_recipe.side_effect = EdhAmiRecipeNotFoundError(RECIPE_NOT_FOUND)
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "get", "team-desktop", "--json-output"])
            assert result.exit_code == 1
            assert json.loads(result.output) == {"error": RECIPE_NOT_FOUND, "error_type": "EdhAmiRecipeNotFoundError"}

    def test_register_of_a_missing_build_in_json_mode_is_one_error_object(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].register_edh_ami_recipe_build.side_effect = EdhAmiRecipeBuildNotFoundError(
                BUILD_NOT_FOUND)
            result = cli_runner.invoke(
                cli, ["edh", "admin", "ami-recipe", "register", "team-desktop", "7", "--json-output"])
            assert result.exit_code == 1
            assert json.loads(result.output) == {"error": BUILD_NOT_FOUND,
                                                 "error_type": "EdhAmiRecipeBuildNotFoundError"}


def _model_keep_versions_bounds():
    """The ge and le bounds of AmiRecipeRequestModel.keepVersions, read from the backend model's
    source with the module constants they name resolved."""
    tree = ast.parse(EDH_MODELS.read_text(encoding="utf-8"))
    constants = {node.targets[0].id: node.value.value for node in tree.body
                 if isinstance(node, ast.Assign) and len(node.targets) == 1
                 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant)}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "AmiRecipeRequestModel":
            for statement in node.body:
                if (isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
                        and statement.target.id == "keepVersions"):
                    keywords = {keyword.arg: keyword.value for keyword in statement.value.keywords}
                    return tuple(constants[value.id] if isinstance(value, ast.Name) else value.value
                                 for value in (keywords["ge"], keywords["le"]))
    raise AssertionError(f"no AmiRecipeRequestModel.keepVersions in {EDH_MODELS}")


class TestAmiRecipeDelete:
    def test_a_delete_refused_while_an_ami_removal_is_pending_reports_the_service_text(
            self, cli_runner, generic_command_mocks):
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].delete_edh_ami_recipe.side_effect = EdhOperationError(DELETE_REFUSED)
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "delete", "team-desktop", "--confirm"])
            assert result.exit_code != 0
            assert f"EDH Error: {DELETE_REFUSED}" in result.output
            mocks["api_client"].delete_edh_ami_recipe.assert_called_once_with("team-desktop")
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "delete", "team-desktop", "--confirm",
                                             "--json-output"])
            assert result.exit_code == 1
            assert json.loads(result.output) == {"error": DELETE_REFUSED, "error_type": "EdhOperationError"}

    def test_the_delete_help_states_the_refusal(self, cli_runner):
        result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "delete", "--help"])
        assert result.exit_code == 0
        help_text = " ".join(result.output.split())
        assert "refused while a build's AMI removal is pending" in help_text
        assert "amiRemovalPending" in help_text and "keepVersions" in help_text


class TestAmiRecipeDuplicate:
    def test_duplicate_names_the_source_and_the_copy_and_leaves_the_names_to_the_client(
            self, cli_runner, generic_command_mocks):
        # The client method reads the source and creates the copy; omitted names take its defaults.
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].duplicate_edh_ami_recipe.return_value = dict(DUPLICATED)
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "duplicate", "al2023-desktop",
                                             "--recipe-id", "team-desktop"])
            assert result.exit_code == 0, result.output
            mocks["api_client"].duplicate_edh_ami_recipe.assert_called_once_with(
                "al2023-desktop", "team-desktop", display_name=None, stack_name=None, keep_versions=None)
            assert "AMI recipe duplicated" in result.output
            assert '"duplicatedFrom": "al2023-desktop"' in result.output

    def test_duplicate_forwards_the_display_and_stack_names(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].duplicate_edh_ami_recipe.return_value = dict(DUPLICATED)
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "duplicate", "isaacsim",
                                             "--recipe-id", "isaacsim-lab", "--display-name", "Isaac Sim Lab",
                                             "--stack-name", "Isaac Sim Lab"])
            assert result.exit_code == 0, result.output
            mocks["api_client"].duplicate_edh_ami_recipe.assert_called_once_with(
                "isaacsim", "isaacsim-lab", display_name="Isaac Sim Lab", stack_name="Isaac Sim Lab",
                keep_versions=None)

    @pytest.mark.parametrize("keep_versions", [edh_commands.KEEP_VERSIONS_MIN, 5, edh_commands.KEEP_VERSIONS_MAX])
    def test_duplicate_forwards_the_keep_versions(self, cli_runner, generic_command_mocks, keep_versions):
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].duplicate_edh_ami_recipe.return_value = dict(DUPLICATED)
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "duplicate", "al2023-desktop",
                                             "--recipe-id", "team-desktop", "--keep-versions", str(keep_versions)])
            assert result.exit_code == 0, result.output
            mocks["api_client"].duplicate_edh_ami_recipe.assert_called_once_with(
                "al2023-desktop", "team-desktop", display_name=None, stack_name=None, keep_versions=keep_versions)

    @pytest.mark.parametrize(
        "value", [str(edh_commands.KEEP_VERSIONS_MIN - 1), str(edh_commands.KEEP_VERSIONS_MAX + 1), "three"])
    def test_a_keep_versions_outside_the_bounds_sends_nothing(self, cli_runner, generic_command_mocks, value):
        with generic_command_mocks("addon.edh") as mocks:
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "duplicate", "al2023-desktop",
                                             "--recipe-id", "team-desktop", "--keep-versions", value])
            assert result.exit_code == 2
            assert "--keep-versions" in result.output
            assert not mocks["api_client"].duplicate_edh_ami_recipe.called

    def test_the_keep_versions_bounds_are_the_recipe_model_s(self):
        """--keep-versions refuses only what the service would answer 400 to."""
        assert _model_keep_versions_bounds() == (edh_commands.KEEP_VERSIONS_MIN, edh_commands.KEEP_VERSIONS_MAX)

    def test_duplicate_json_output_is_the_created_recipe(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].duplicate_edh_ami_recipe.return_value = dict(DUPLICATED)
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "duplicate", "al2023-desktop",
                                             "--recipe-id", "team-desktop", "--json-output"])
            assert result.exit_code == 0
            assert json.loads(result.output) == DUPLICATED

    def test_duplicate_requires_the_recipe_id_of_the_copy(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("addon.edh") as mocks:
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "duplicate", "al2023-desktop"])
            assert result.exit_code == 2
            assert "Missing option '--recipe-id'" in result.output
            assert not mocks["api_client"].duplicate_edh_ami_recipe.called

    def test_a_refused_duplicate_reports_the_service_text(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].duplicate_edh_ami_recipe.side_effect = EdhOperationError(STACK_NAME_TAKEN)
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "duplicate", "al2023-desktop",
                                             "--recipe-id", "team-desktop"])
            assert result.exit_code != 0
            assert STACK_NAME_TAKEN in result.output
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "duplicate", "al2023-desktop",
                                             "--recipe-id", "team-desktop", "--json-output"])
            assert result.exit_code == 1
            assert json.loads(result.output) == {"error": STACK_NAME_TAKEN, "error_type": "EdhOperationError"}

    def test_duplicate_is_gated_on_the_ami_recipes_switch(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("addon.edh") as mocks:
            mocks["profile_manager"].has_feature_switch.return_value = False
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "duplicate", "al2023-desktop",
                                             "--recipe-id", "team-desktop"])
            assert result.exit_code != 0
            assert EDH_AMI_RECIPES_DISABLED_MESSAGE in result.output
            mocks["profile_manager"].has_feature_switch.assert_called_with("EDH_AMI_RECIPES")
            assert not mocks["api_client"].duplicate_edh_ami_recipe.called


class TestLaunchInstanceTypes:
    """A recipe's launch instance types: sent as the recipe file holds them, counted in the recipe and
    catalog image tables, shown in full by `ami-recipe get`, and applied by `catalog instance-types
    --image-id` as the web launch wizard applies them."""

    RECIPES = {"Items": [
        {"recipeId": "isaacsim", "kind": "vdi", "osFamily": "linux", "architecture": "x86_64", "source": "config",
         "stackName": "VAMS Isaac Sim Desktop", "launchInstanceTypes": ["g5.2xlarge", "g6e.xlarge", "gr6.4xlarge"],
         "latestBuild": {"version": 3, "status": "AVAILABLE"}},
        {"recipeId": "al2023-desktop", "kind": "vdi", "osFamily": "linux", "architecture": "x86_64",
         "source": "config", "stackName": "VAMS AL2023 Desktop", "latestBuild": None},
    ]}

    @pytest.mark.parametrize("command", ["create", "update"])
    def test_a_recipe_file_s_list_is_sent_as_it_stands(self, cli_runner, generic_command_mocks, tmp_path, command):
        launch_types = ["g6e.xlarge", "g5.2xlarge"]
        (tmp_path / "recipe.json").write_text(json.dumps(
            {"recipeId": "team-desktop", "components": [{"arn": "arn:x"}], "launchInstanceTypes": launch_types}))
        method = "create_edh_ami_recipe" if command == "create" else "update_edh_ami_recipe"
        argv = ["edh", "admin", "ami-recipe", command]
        if command == "update":
            argv.append("team-desktop")
        with generic_command_mocks("addon.edh") as mocks:
            getattr(mocks["api_client"], method).return_value = {"recipeId": "team-desktop"}
            result = cli_runner.invoke(cli, [*argv, "--file", str(tmp_path / "recipe.json"), "--json-output"])
            assert result.exit_code == 0, result.output
            body = getattr(mocks["api_client"], method).call_args.args[-1]
            assert body["launchInstanceTypes"] == launch_types

    def test_the_recipe_list_counts_the_launch_types_or_says_any(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].list_edh_ami_recipes.return_value = self.RECIPES
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "list"])
            assert result.exit_code == 0, result.output
            lines = result.output.splitlines()
            header = next(line for line in lines if line.startswith("RECIPE"))
            column = header.index("LAUNCH TYPES")
            assert header.index("LATEST") < column < header.index("STACK")
            isaac = next(line for line in lines if line.startswith("isaacsim"))
            al2023 = next(line for line in lines if line.startswith("al2023-desktop"))
            assert isaac[column:].split()[:2] == ["3", "listed"]
            assert al2023[column:].split()[0] == "any"
            assert isaac.rstrip().endswith("VAMS Isaac Sim Desktop")

    def test_get_shows_the_whole_list(self, cli_runner, generic_command_mocks):
        recipe = self.RECIPES["Items"][0]
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].get_edh_ami_recipe.return_value = dict(recipe)
            result = cli_runner.invoke(cli, ["edh", "admin", "ami-recipe", "get", "isaacsim"])
            assert result.exit_code == 0, result.output
            assert '"launchInstanceTypes"' in result.output
            for instance_type in recipe["launchInstanceTypes"]:
                assert f'"{instance_type}"' in result.output

    def test_the_catalog_images_table_counts_an_image_s_launch_types(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].list_edh_catalog_images.return_value = LAUNCH_CATALOG_IMAGES
            result = cli_runner.invoke(cli, ["edh", "catalog", "images"])
            assert result.exit_code == 0, result.output
            lines = result.output.splitlines()
            header = next(line for line in lines if line.startswith("CATEGORY"))
            column = header.index("LAUNCH TYPES")
            assert header.index("ROOT GIB") < column < header.index("RECIPE")
            rows = {row.split()[2] if row.startswith("Baseline") else row.split()[3]: row
                    for row in lines[lines.index(header) + 1:] if row.strip()}
            assert rows["36"][column:].split()[:2] == ["4", "listed"]
            assert rows["36"].rstrip().endswith("isaacsim v3")
            assert rows["35"][column:].split()[0] == "any"
            assert rows["12"][column:].split()[0] == "any"

    @pytest.mark.parametrize("image_id,expected", [
        ("36", ["g5.2xlarge", "g6e.xlarge"]),
        ("35", ["g5.2xlarge", "g5.xlarge", "g6e.xlarge", "m7i.large"]),
        ("12", ["g5.2xlarge", "g5.xlarge", "g6e.xlarge", "m7g.large", "m7i.large"]),
    ], ids=["recipe-with-a-list", "recipe-without-a-list", "baseline-image"])
    def test_image_id_lists_only_the_types_the_image_s_recipe_offers(
            self, cli_runner, generic_command_mocks, image_id, expected):
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].list_edh_catalog_images.return_value = LAUNCH_CATALOG_IMAGES
            mocks["api_client"].list_edh_catalog_instance_types.return_value = LAUNCH_INSTANCE_TYPES
            result = cli_runner.invoke(cli, ["edh", "catalog", "instance-types", "--image-id", image_id])
            assert result.exit_code == 0, result.output
            lines = result.output.splitlines()
            header = next(line for line in lines if line.startswith("INSTANCE TYPE"))
            assert [row.split()[0] for row in lines[lines.index(header) + 1:] if row.strip()] == expected
            assert f"Found {len(expected)} instance type(s)" in result.output

    def test_image_id_whose_recipe_offers_nothing_allowed_says_so(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("addon.edh") as mocks:
            mocks["api_client"].list_edh_catalog_images.return_value = LAUNCH_CATALOG_IMAGES
            mocks["api_client"].list_edh_catalog_instance_types.return_value = {"Items": [
                {"instanceType": "m7i.large", "architectures": ["x86_64"]}]}
            result = cli_runner.invoke(cli, ["edh", "catalog", "instance-types", "--image-id", "36"])
            assert result.exit_code == 0, result.output
            assert "Found 0 instance type(s)" in result.output
            assert ("No instance type this deployment allows is offered for this image. Choose another "
                    "image or ask an administrator.") in result.output
            assert "runs this image's architecture" not in result.output

            result = cli_runner.invoke(
                cli, ["edh", "catalog", "instance-types", "--image-id", "36", "--json-output"])
            assert result.exit_code == 0, result.output
            assert json.loads(result.output) == {"Items": []}

    @pytest.mark.parametrize("item,image,offered", [
        ({"instanceType": "g5.2xlarge"}, {"launchInstanceTypes": ["g6e.xlarge", "g5.2xlarge"]}, True),
        ({"instanceType": "g5.xlarge"}, {"launchInstanceTypes": ["g6e.xlarge", "g5.2xlarge"]}, False),
        ({"instanceType": "g5.xlarge"}, {"launchInstanceTypes": []}, True),
        ({"instanceType": "g5.xlarge"}, {"launchInstanceTypes": None}, True),
        ({"instanceType": "g5.xlarge"}, {}, True),
        ("g5.2xlarge", {"launchInstanceTypes": ["g5.2xlarge"]}, True),
        ("g5.xlarge", {"launchInstanceTypes": ["g5.2xlarge"]}, False),
    ], ids=["listed", "not-listed", "empty-list", "null-list", "no-list", "bare-name-listed",
            "bare-name-not-listed"])
    def test_the_offering_rule_is_the_web_wizards(self, item, image, offered):
        assert edh_commands.instance_type_offered_for_image(item, image) is offered

    def test_create_help_names_the_recipe_s_list(self, cli_runner):
        result = cli_runner.invoke(cli, ["edh", "workstation", "create", "--help"])
        assert result.exit_code == 0
        help_text = " ".join(result.output.split())
        assert "a recipe image that lists launch instance types takes one of them" in help_text
