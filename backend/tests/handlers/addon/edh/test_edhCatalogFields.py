# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Model-to-EDH field mapping for software-stack registration. Every value is a string because the
request leaves as form fields; optional fields are omitted rather than sent empty. The field names are
checked against the sanitized EDH registration captures so a renamed EDH field fails here. A listed
software-stack record's architecture is EDH's ``ami_arch``, trimmed, or None when the record states
none plainly."""

import json
import os
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.addon.edh.common import edhCatalogFields as cf
from backend.backend.handlers.addon.edh.common.edhClient import (
    EDH_SOFTWARE_STACK_REGISTER_FIELDS,
    EdhError,
    EdhNotFoundError,
)
from backend.backend.models.addon.edh import (
    RegisterSimulationImageRequestModel,
    RegisterVdiImageRequestModel,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
# The column EDH's software-stack list routes name a stack's CPU architecture in (EC2's Architecture of
# its AMI); the oracle for the record field the reader reads.
CAPTURED_ARCHITECTURE_FIELD = "ami_arch"


def _capture_body(file_name):
    """The form fields a sanitized capture recorded, or None when that capture was never made."""
    path = os.path.join(FIXTURES_DIR, file_name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)["request"]["body"] or {}


def _vdi(**overrides):
    values = {"amiId": "ami-0123456789abcdef0", "stackName": "linux-cad", "baseOs": "amazonlinux2023",
              "rootSize": 100, "virtualDesktopProfileId": 1}
    values.update(overrides)
    return RegisterVdiImageRequestModel(**values)


def _simulation(**overrides):
    values = {"amiId": "ami-0123456789abcdef0", "stackName": "sim-base", "rootSize": 50,
              "targetNodeProfileId": 4}
    values.update(overrides)
    return RegisterSimulationImageRequestModel(**values)


@pytest.mark.unit
class TestOsFamily:

    @pytest.mark.parametrize("base_os,expected", [
        ("windows2022", "windows"),
        ("Windows Server 2019", "windows"),
        ("amazonlinux2023", "linux"),
        ("ubuntu2204", "linux"),
        ("", "linux"),
        (None, "linux"),
    ])
    def test_windows_prefix_maps_to_windows_everything_else_to_linux(self, base_os, expected):
        assert cf.os_family_from_base_os(base_os) == expected


@pytest.mark.unit
class TestVdiStackFields:

    def test_required_fields_are_strings_and_optionals_are_omitted(self):
        fields = cf.vdi_stack_fields(_vdi())
        assert fields == {
            "ami_id": "ami-0123456789abcdef0",
            "stack_name": "linux-cad",
            "base_os": "amazonlinux2023",
            "root_size": "100",
            "virtual_desktop_profile_id": "1",
        }
        assert all(isinstance(value, str) for value in fields.values())

    def test_optionals_are_sent_when_present_and_no_thumbnail_ever_is(self):
        fields = cf.vdi_stack_fields(_vdi(stackName="win", baseOs="windows2022", rootSize=200,
                                          virtualDesktopProfileId=2, launchTenancy="dedicated",
                                          description="Windows CAD"))
        assert fields["launch_tenancy"] == "dedicated"
        assert fields["description"] == "Windows CAD"
        assert "thumbnail" not in fields

    def test_the_field_tuple_is_exactly_what_the_mapping_can_emit(self):
        everything = cf.vdi_stack_fields(_vdi(launchTenancy="default", description="d"))
        assert set(everything) == set(cf.VDI_STACK_FIELDS)

    def test_the_field_names_match_the_edh_capture(self):
        sent = set(_capture_body("software_stacks_dcv_register.json") or {})
        assert sent, "the dcv registration capture carries no body"
        # Required fields under this spelling; nothing the capture sent is outside the route's parser.
        assert set(cf.vdi_stack_fields(_vdi())) <= sent <= EDH_SOFTWARE_STACK_REGISTER_FIELDS["dcv"]
        assert set(cf.VDI_STACK_FIELDS) <= EDH_SOFTWARE_STACK_REGISTER_FIELDS["dcv"]


@pytest.mark.unit
class TestSimulationStackFields:

    def test_required_fields(self):
        assert cf.simulation_stack_fields(_simulation()) == {
            "ami_id": "ami-0123456789abcdef0",
            "stack_name": "sim-base",
            "root_size": "50",
            "target_node_profile_id": "4",
            "target_node_user_data_id": "1",
        }

    def test_optionals(self):
        fields = cf.simulation_stack_fields(_simulation(userDataVariables="FOO=bar", description="sim"))
        assert fields["user_data_variables"] == "FOO=bar"
        assert fields["description"] == "sim"

    def test_the_field_tuple_is_exactly_what_the_mapping_can_emit(self):
        everything = cf.simulation_stack_fields(_simulation(userDataVariables="A=1", description="d"))
        assert set(everything) == set(cf.SIMULATION_STACK_FIELDS)

    def test_the_field_names_match_the_edh_capture(self):
        body = _capture_body("software_stacks_target_nodes_register.json")
        if body is None:
            pytest.skip("no simulation AMI was available to the spike; "
                        "the target_nodes register capture does not exist")
        sent = set(body)
        assert sent, "the target_nodes registration capture carries no body"
        assert (set(cf.simulation_stack_fields(_simulation())) <= sent
                <= EDH_SOFTWARE_STACK_REGISTER_FIELDS["target_nodes"])
        assert set(cf.SIMULATION_STACK_FIELDS) <= EDH_SOFTWARE_STACK_REGISTER_FIELDS["target_nodes"]
        assert "base_os" not in cf.SIMULATION_STACK_FIELDS

    def test_the_user_data_template_is_always_sent(self):
        assert cf.simulation_stack_fields(_simulation())["target_node_user_data_id"] == "1"
        assert cf.simulation_stack_fields(_simulation(targetNodeUserDataId=4))["target_node_user_data_id"] == "4"


@pytest.mark.unit
class TestStackArchitecture:

    def test_the_record_field_is_edhs_architecture_column(self):
        assert cf.EDH_STACK_RECORD_FIELD_ARCHITECTURE == CAPTURED_ARCHITECTURE_FIELD

    @pytest.mark.parametrize("value,expected", [
        ("x86_64", "x86_64"),
        (" arm64 ", "arm64"),
        ("arm64_mac", "arm64_mac"),
    ])
    def test_a_stated_architecture_is_returned_trimmed(self, value, expected):
        assert cf.stack_architecture({CAPTURED_ARCHITECTURE_FIELD: value}) == expected

    @pytest.mark.parametrize("record", [
        {},
        {CAPTURED_ARCHITECTURE_FIELD: None},
        {CAPTURED_ARCHITECTURE_FIELD: ""},
        {CAPTURED_ARCHITECTURE_FIELD: "   "},
        {CAPTURED_ARCHITECTURE_FIELD: 64},
        {CAPTURED_ARCHITECTURE_FIELD: ["x86_64"]},
    ], ids=["absent", "null", "empty", "blank", "number", "list"])
    def test_an_architecture_the_record_does_not_state_as_text_is_none(self, record):
        assert cf.stack_architecture(record) is None

    def test_no_other_field_is_read_as_the_architecture(self):
        # Control for the field name: a value under any other key is not an architecture.
        assert cf.stack_architecture({"architecture": "x86_64", "arch": "arm64"}) is None


@pytest.mark.unit
class TestKindConstants:

    def test_kind_labels_map_to_edh_client_kinds(self):
        assert cf.KIND_BY_IMAGE_KIND == {"vdi": "dcv", "simulation": "target_nodes"}
        assert cf.KIND_DCV == "dcv"
        assert cf.KIND_TARGET_NODES == "target_nodes"


@pytest.mark.unit
class TestEnsureStackInProject:
    """The project assignment every registration path shares. EDH resolves a user's launchable stacks
    through the projects a stack is attached to, so a stack no project carries cannot start a desktop;
    the project is the one the deployment configures as the launch project."""

    def test_the_project_is_the_one_every_edh_lambda_carries_in_its_environment(self):
        assert cf.EDH_VDI_DEFAULT_PROJECT == os.environ["EDH_VDI_DEFAULT_PROJECT"]

    def test_the_stack_is_assigned_to_that_project_and_the_result_says_whether_it_changed(self):
        client = MagicMock()
        client.assign_software_stack_to_project.return_value = True
        assert cf.ensure_stack_in_project(client, "dcv", "20") is True
        client.assign_software_stack_to_project.assert_called_once_with(
            "dcv", "20", cf.EDH_VDI_DEFAULT_PROJECT)

    def test_a_membership_the_project_already_carries_is_reported_as_unchanged(self):
        client = MagicMock()
        client.assign_software_stack_to_project.return_value = False
        assert cf.ensure_stack_in_project(client, "target_nodes", 20) is False

    def test_an_audited_caller_supplies_the_invocation(self):
        """The admin routes send the update through their audit wrapper, so the call is theirs to make."""
        client = MagicMock()
        calls = []

        def invoke(*args):
            calls.append(args)
            return True

        assert cf.ensure_stack_in_project(client, "dcv", "20", invoke=invoke) is True
        assert calls == [("dcv", "20", cf.EDH_VDI_DEFAULT_PROJECT)]
        client.assign_software_stack_to_project.assert_not_called()

    def test_the_failure_message_names_the_project_and_the_detail(self):
        # Exception case: includes type and mapped reason
        msg = cf.project_assign_error(EdhNotFoundError("EDH has no active project named 'default'"))
        assert msg.startswith(
            f"registered in EDH but not assigned to project "
            f"'{cf.EDH_VDI_DEFAULT_PROJECT}' (EdhNotFoundError")
        assert "The requested EDH resource was not found" in msg

        # Generic error
        assert cf.project_assign_error(EdhError("generic")).startswith(cf.PROJECT_ASSIGN_FAILED)

        # String case: deployment-time warnings
        assert cf.project_assign_error("the catalog could not be read back") == (
            f"registered in EDH but not assigned to project "
            f"'{cf.EDH_VDI_DEFAULT_PROJECT}' (the catalog could not be read back)")
