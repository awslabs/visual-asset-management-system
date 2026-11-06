# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Admin image catalog: the merged list with a kind field and per-item Tier-2, JSON-only register
that leaves as multipart fields toward EDH, and delete discriminated by the kind path segment. Each
listed image names the CPU architecture EDH registered for its stack, the value the launch catalog
names for the same record, or null when the record states none; it is not an enforcement field."""

import pytest

from backend.backend.handlers.addon.edh.common import edhClient as ec
from backend.backend.handlers.addon.edh.common.edhClient import (
    EdhNotFoundError, EdhApiError, EdhOutcomeUnknownError, EdhUnavailableError,
)
from backend.tests.handlers.addon.edh.edhAdminTestSupport import (
    ADMIN,
    IMAGES_PATH,
    FakeEdhClient,
    edh_proxy_records,
    fixture_payload,
    image_path,
    install_admin_fakes,
    make_event,
    response_body,
)
from backend.tests.handlers.addon.edh.edhTestSupport import DEFAULT_PROJECT
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
import backend.backend.handlers.addon.edh.edhAdminService as service
import backend.backend.handlers.addon.edh.edhWorkstationService as workstation_service

# The architecture EDH registered for a captured stack (EC2's Architecture of its AMI); the oracle for
# the architecture an admin image names.
CAPTURED_ARCHITECTURE_FIELD = "ami_arch"

DCV_STACKS = [
    {"id": 7, "ami_id": "ami-0123456789abcdef0", "stack_name": "linux-cad", "ami_base_os": "amazonlinux2023",
     "ami_root_disk_size": 100, "os_family": "linux", "virtual_desktop_profile_id": 1, "description": "Linux CAD",
     "ami_arch": "x86_64"},
    {"id": 8, "ami_id": "ami-0fedcba9876543210", "stack_name": "win-cad", "ami_base_os": "windows2022",
     "ami_root_disk_size": 200, "os_family": "windows", "virtual_desktop_profile_id": 2, "launch_tenancy": "dedicated",
     "ami_arch": "x86_64"},
]
NEW_STACK = {"id": 42, "ami_id": "ami-0123456789abcdef0", "stack_name": "new-stack", "ami_base_os": "amazonlinux2023",
             "ami_root_disk_size": 120, "os_family": "linux", "virtual_desktop_profile_id": 1}
TARGET_NODE_STACKS = [
    {"id": 3, "ami_id": "ami-0aaaaaaaaaaaaaaaa", "stack_name": "sim-base", "ami_root_disk_size": 50,
     "os_family": "linux", "target_node_profile_id": 4, "target_node_user_data_id": 1,
     "ami_user_data_variables": "FOO=bar", "ami_arch": "arm64"},
]
NEW_SIM_STACK = {"id": 43, "ami_id": "ami-0123456789abcdef0", "stack_name": "sim-2", "ami_root_disk_size": 60,
                 "os_family": "linux", "target_node_profile_id": 4, "target_node_user_data_id": 1}

VDI_BODY = {"amiId": "ami-0123456789abcdef0", "stackName": "new-stack", "baseOs": "amazonlinux2023",
            "rootSize": 120, "virtualDesktopProfileId": 1, "description": "d"}
SIM_BODY = {"amiId": "ami-0123456789abcdef0", "stackName": "sim-2", "rootSize": 60,
            "targetNodeProfileId": 4, "userDataVariables": "A=1"}


def _catalog_client(**results):
    # The dcv listing already shows the stack a register test creates, so the post-create read-back
    # finds it; a client whose catalog is what the test says is built with list_software_stacks=...
    results.setdefault("list_software_stacks",
                       lambda kind: list(DCV_STACKS) + [dict(NEW_STACK)] if kind == "dcv" else list(TARGET_NODE_STACKS))
    return FakeEdhClient(**results)


def _empty_catalog_client(**results):
    """A catalog WITHOUT NEW_STACK initially, but shows it after register is called."""
    class StatefulCatalog:
        def __init__(self):
            self.registered = False

        def list_stacks(self, kind):
            if kind == "dcv":
                return list(DCV_STACKS) + ([dict(NEW_STACK)] if self.registered else [])
            else:
                return list(TARGET_NODE_STACKS)

        def register_stack(self, kind, fields, edh_uid=None):
            self.registered = True
            return {"message": "registered successfully in SOCA"}

    catalog = StatefulCatalog()
    results.setdefault("list_software_stacks", catalog.list_stacks)
    results.setdefault("register_software_stack", catalog.register_stack)
    return FakeEdhClient(**results)


def _empty_simulation_catalog_client(**results):
    """A catalog WITHOUT NEW_SIM_STACK initially, but shows it after register is called."""
    class StatefulCatalog:
        def __init__(self):
            self.registered = False

        def list_stacks(self, kind):
            if kind == "target_nodes":
                return list(TARGET_NODE_STACKS) + ([dict(NEW_SIM_STACK)] if self.registered else [])
            else:
                return list(DCV_STACKS)

        def register_stack(self, kind, fields, edh_uid=None):
            self.registered = True
            return {"message": "registered successfully in SOCA"}

    catalog = StatefulCatalog()
    results.setdefault("list_software_stacks", catalog.list_stacks)
    results.setdefault("register_software_stack", catalog.register_stack)
    return FakeEdhClient(**results)


def _registered_catalog_client(**results):
    """A client whose listings, target_nodes included, show the stack a register test creates."""
    return _catalog_client(list_software_stacks=lambda kind: (
        list(DCV_STACKS) + [dict(NEW_STACK)] if kind == "dcv" else list(TARGET_NODE_STACKS) + [dict(NEW_SIM_STACK)]),
        **results)


@pytest.mark.unit
class TestListImages:
    def test_merges_both_edh_tables_with_a_kind_field(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client())

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert response["statusCode"] == 200
        items = response_body(response)["Items"]
        assert [(i["kind"], i["imageId"], i["stackName"]) for i in items] == [
            ("vdi", "7", "linux-cad"),
            ("vdi", "8", "win-cad"),
            ("vdi", "42", "new-stack"),
            ("simulation", "3", "sim-base"),
        ]
        assert items[0] == {"kind": "vdi", "imageId": "7", "stackName": "linux-cad",
                            "amiId": "ami-0123456789abcdef0", "baseOs": "amazonlinux2023",
                            "osFamily": "linux", "architecture": "x86_64", "rootSize": 100, "profileId": 1,
                            "description": "Linux CAD"}
        assert items[1]["osFamily"] == "windows"
        assert items[3]["osFamily"] == "linux" and items[3]["profileId"] == 4
        assert fakes.edh.calls_to("list_software_stacks") == [("dcv",), ("target_nodes",)]

    def test_each_item_is_enforced_as_an_edh_image_with_get(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client())

        fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert [call.args for call in fakes.enforcer.enforce.call_args_list] == [
            ({"object__type": "edhImage", "edhImageId": "7", "edhOsFamily": "linux"}, "GET"),
            ({"object__type": "edhImage", "edhImageId": "8", "edhOsFamily": "windows"}, "GET"),
            ({"object__type": "edhImage", "edhImageId": "42", "edhOsFamily": "linux"}, "GET"),
            ({"object__type": "edhImage", "edhImageId": "3", "edhOsFamily": "linux"}, "GET"),
        ]

    def test_a_denied_item_is_dropped_and_an_allowed_one_kept(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client(),
                                    enforce=lambda obj, act: obj["edhOsFamily"] == "linux")

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        # Positive control (the Linux images survive) beside the denial (the Windows image is gone).
        assert [i["imageId"] for i in response_body(response)["Items"]] == ["7", "42", "3"]

    def test_entries_that_are_not_records_are_skipped(self, monkeypatch):
        fakes = install_admin_fakes(
            monkeypatch, edh=_catalog_client(list_software_stacks=lambda kind: ["garbage", None]))

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert response_body(response)["Items"] == []

    def test_both_listings_are_audited_with_their_kind_route(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client())

        fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert [(r["operation"], r["edhMethod"], r["edhRoute"], r["edhUid"]) for r in edh_proxy_records(fakes)] == [
            ("adminImagesList", "GET", ec.EDH_SOFTWARE_STACK_ROUTES["dcv"], edh_uid_for(ADMIN)),
            ("adminImagesList", "GET", ec.EDH_SOFTWARE_STACK_ROUTES["target_nodes"], edh_uid_for(ADMIN)),
        ]


@pytest.mark.unit
class TestImageArchitecture:
    """Each listed image, desktop and simulation alike, names the CPU architecture EDH registered for its
    stack; an image whose record states none is still listed, with None."""

    def test_each_image_of_both_kinds_carries_its_records_architecture(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client())

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        # NEW_STACK states no architecture, so the positive values sit beside an absent one.
        assert [(i["kind"], i["imageId"], i["architecture"]) for i in response_body(response)["Items"]] == [
            ("vdi", "7", "x86_64"),
            ("vdi", "8", "x86_64"),
            ("vdi", "42", None),
            ("simulation", "3", "arm64"),
        ]

    @pytest.mark.parametrize("kind_label,records", [
        ("vdi", DCV_STACKS),
        ("simulation", TARGET_NODE_STACKS),
    ])
    def test_the_architecture_is_trimmed(self, kind_label, records):
        record = {**records[0], CAPTURED_ARCHITECTURE_FIELD: " arm64 "}
        assert service._image_item(kind_label, record)["architecture"] == "arm64"

    @pytest.mark.parametrize("architecture", [None, "", "   ", 64], ids=["absent", "empty", "blank", "not-text"])
    def test_an_image_without_an_architecture_is_listed_with_none(self, monkeypatch, architecture):
        stack = {key: value for key, value in DCV_STACKS[0].items() if key != CAPTURED_ARCHITECTURE_FIELD}
        if architecture is not None:
            stack[CAPTURED_ARCHITECTURE_FIELD] = architecture
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client(
            list_software_stacks=lambda kind: [dict(stack)] if kind == "dcv" else []))

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert [(i["imageId"], i["architecture"]) for i in response_body(response)["Items"]] == [("7", None)]
        # The architecture is display data: the enforcement dict stays the matrix fields.
        fakes.enforcer.enforce.assert_called_once_with(
            {"object__type": "edhImage", "edhImageId": "7", "edhOsFamily": "linux"}, "GET")

    @pytest.mark.parametrize("architecture", ["x86_64", " arm64 ", "arm64_mac", "", "   ", 64, None],
                             ids=["x86_64", "padded", "other", "empty", "blank", "not-text", "absent"])
    def test_an_admin_image_and_the_launch_catalog_name_the_same_architecture(self, architecture):
        record = {key: value for key, value in DCV_STACKS[0].items() if key != CAPTURED_ARCHITECTURE_FIELD}
        if architecture is not None:
            record[CAPTURED_ARCHITECTURE_FIELD] = architecture
        assert (service._image_item(service.IMAGE_KIND_VDI, record)["architecture"]
                == workstation_service._image_item(record)["architecture"])


@pytest.mark.unit
class TestRegisterImage:
    def test_vdi_register_sends_the_form_fields_and_reads_the_id_back(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_empty_catalog_client())

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("vdi"), body=VDI_BODY), {})

        assert response["statusCode"] == 201
        assert fakes.edh.calls_to("register_software_stack") == [("dcv", {
            "ami_id": "ami-0123456789abcdef0", "stack_name": "new-stack", "base_os": "amazonlinux2023",
            "root_size": "120", "virtual_desktop_profile_id": "1", "description": "d",
        }, edh_uid_for(ADMIN))]
        # Precheck + read-back after register
        assert fakes.edh.calls_to("list_software_stacks") == [("dcv",), ("dcv",)]
        assert response_body(response) == {"kind": "vdi", "imageId": "42", "stackName": "new-stack",
                                           "amiId": "ami-0123456789abcdef0", "osFamily": "linux"}

    def test_simulation_register_uses_the_target_nodes_kind(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_empty_simulation_catalog_client())

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("simulation"), body=SIM_BODY), {})

        assert response["statusCode"] == 201
        [(kind, fields, actor)] = fakes.edh.calls_to("register_software_stack")
        assert kind == "target_nodes"
        assert fields["target_node_profile_id"] == "4" and fields["user_data_variables"] == "A=1"
        assert fields["target_node_user_data_id"] == "1"
        assert actor == edh_uid_for(ADMIN)
        assert response_body(response)["osFamily"] == "linux"
        assert response_body(response)["imageId"] == "43"
        assert fakes.edh.projects.stack_ids("target_nodes") == ["43"]

    def test_tier_2_from_the_body_uses_the_stack_name_and_os_family(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client(), enforce=lambda obj, act: False)
        body = {**VDI_BODY, "stackName": "win-x", "baseOs": "windows2022"}

        response = fakes.module.lambda_handler(make_event("POST", image_path("vdi"), body=body), {})

        assert response["statusCode"] == 403
        assert fakes.edh.calls_to("register_software_stack") == []
        assert fakes.enforcer.enforce.call_args.args == (
            {"object__type": "edhImage", "edhImageId": "win-x", "edhOsFamily": "windows"}, "POST")

    def test_an_invalid_body_is_400_before_any_edh_call(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client())

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("vdi"), body={"amiId": "ami-1"}), {})

        assert response["statusCode"] == 400
        assert fakes.edh.calls == []

    def test_a_non_json_body_is_400(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client())

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("vdi"), body="not json"), {})

        assert response["statusCode"] == 400
        # VAMSGeneralErrorResponse prefixes its message (models/common.py:216-219).
        assert response_body(response)["message"] == "VAMS General Error: Request body must be a JSON object"

    def test_a_thumbnail_in_the_body_is_ignored_not_forwarded(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_empty_catalog_client())

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("vdi"), body={**VDI_BODY, "thumbnail": "AAAA"}), {})

        assert response["statusCode"] == 201
        [(_kind, fields, _actor)] = fakes.edh.calls_to("register_software_stack")
        assert "thumbnail" not in fields

    def test_the_register_body_may_name_the_user_data_template(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client())

        fakes.module.lambda_handler(
            make_event("POST", image_path("simulation"), body={**SIM_BODY, "targetNodeUserDataId": 3}), {})

        [(_kind, fields, _actor)] = fakes.edh.calls_to("register_software_stack")
        assert fields["target_node_user_data_id"] == "3"

    def test_the_register_is_audited_with_the_kind_route(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_empty_simulation_catalog_client())

        fakes.module.lambda_handler(make_event("POST", image_path("simulation"), body=SIM_BODY), {})

        # Precheck, register, read-back, assign
        assert [(r["operation"], r["edhMethod"], r["edhRoute"]) for r in edh_proxy_records(fakes)] == [
            ("adminImageRegister", "GET", ec.EDH_SOFTWARE_STACK_ROUTES["target_nodes"]),
            ("adminImageRegister", "POST", ec.EDH_SOFTWARE_STACK_ROUTES["target_nodes"]),
            ("adminImageRegister", "GET", ec.EDH_SOFTWARE_STACK_ROUTES["target_nodes"]),
            ("adminImageRegister", "PUT", ec.EDH_ROUTE_PROJECTS),
        ]


@pytest.mark.unit
class TestRegisterImageIdempotency:
    """register_image must be idempotent after a stranded first request."""

    def test_retry_after_duplicate_name_with_same_ami_adopts_and_succeeds(self, monkeypatch):
        """A retry that finds the stack with the same AMI adopts it and returns 201."""
        edh = FakeEdhClient(
            register_software_stack=EdhApiError("Stack name already exists"),
            list_software_stacks=lambda kind: list(DCV_STACKS) + [dict(NEW_STACK)] if kind == "dcv" else [],
        )
        fakes = install_admin_fakes(monkeypatch, edh=edh)

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("vdi"), body=VDI_BODY), {})

        assert response["statusCode"] == 201
        assert response_body(response) == {
            "kind": "vdi", "imageId": "42", "stackName": "new-stack",
            "amiId": "ami-0123456789abcdef0", "osFamily": "linux"
        }
        assert len([r for r in edh_proxy_records(fakes) if r["edhRoute"] == ec.EDH_ROUTE_PROJECTS]) >= 1

    def test_retry_after_duplicate_name_with_different_ami_stays_refused(self, monkeypatch):
        """A retry that finds the stack with a different AMI keeps the refusal."""
        conflicting_stack = {**NEW_STACK, "ami_id": "ami-DIFFERENT"}
        edh = FakeEdhClient(
            register_software_stack=EdhApiError("Stack name already exists"),
            list_software_stacks=lambda kind: list(DCV_STACKS) + [conflicting_stack] if kind == "dcv" else [],
        )
        fakes = install_admin_fakes(monkeypatch, edh=edh)

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("vdi"), body=VDI_BODY), {})

        assert response["statusCode"] == 400
        assert "already exists" in response_body(response)["message"]

    @staticmethod
    def _raced(registered_meanwhile):
        """A catalog that lists no stack of the name when the register reads it, and a register EDH refuses
        because a racing request registered ``registered_meanwhile`` in between: refused as EDH refuses a
        label that is taken, its wording in the refusal's body. The client, and the refusal."""
        raced = []
        refusal = EdhApiError(
            f"EDH refused POST {ec.EDH_SOFTWARE_STACK_ROUTES['dcv']} with 500", status=500,
            body_text="Unable to register ami-0123456789abcdef0 - new-stack:  AMI label new-stack already exists, "
                      "pick a different name or deactivate the existing one  (Request ID: 1234)")

        def list_stacks(kind):
            if kind != "dcv":
                return list(TARGET_NODE_STACKS)
            return list(DCV_STACKS) + ([dict(registered_meanwhile)] if raced else [])

        def register_stack(kind, fields, edh_uid):
            raced.append(kind)
            return refusal

        return FakeEdhClient(list_software_stacks=list_stacks, register_software_stack=register_stack), refusal

    def test_a_register_that_loses_a_race_to_the_same_ami_adopts_the_stack_the_race_registered(self, monkeypatch):
        edh, _refusal = self._raced(NEW_STACK)
        fakes = install_admin_fakes(monkeypatch, edh=edh)

        response = fakes.module.lambda_handler(make_event("POST", image_path("vdi"), body=VDI_BODY), {})

        assert response["statusCode"] == 201
        assert response_body(response)["imageId"] == "42"
        assert fakes.edh.projects.stack_ids("dcv") == ["42"]

    def test_a_register_that_loses_a_race_to_another_ami_answers_the_refusal(self, monkeypatch):
        edh, refusal = self._raced({**NEW_STACK, "ami_id": "ami-0fedcba9876543210"})
        fakes = install_admin_fakes(monkeypatch, edh=edh)

        response = fakes.module.lambda_handler(make_event("POST", image_path("vdi"), body=VDI_BODY), {})

        status_code, message = ec.edh_error_response_parts(refusal)
        assert response["statusCode"] == status_code
        assert response_body(response)["message"] == message
        assert fakes.edh.projects.stack_ids("dcv") == []

    def test_a_register_refusal_that_names_no_taken_label_answers_the_refusal(self, monkeypatch):
        refusal = EdhApiError(f"EDH refused POST {ec.EDH_SOFTWARE_STACK_ROUTES['dcv']} with 500", status=500,
                              body_text="Unable to register ami-0123456789abcdef0 - new-stack:  root_size must be "
                                        "a valid integer  (Request ID: 1234)")
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client(
            list_software_stacks=lambda kind: list(DCV_STACKS) if kind == "dcv" else list(TARGET_NODE_STACKS),
            register_software_stack=refusal))

        response = fakes.module.lambda_handler(make_event("POST", image_path("vdi"), body=VDI_BODY), {})

        status_code, message = ec.edh_error_response_parts(refusal)
        assert response["statusCode"] == status_code
        assert response_body(response)["message"] == message

    def test_precheck_before_register_adopts_existing_with_same_ami(self, monkeypatch):
        """Before registering, check for existing stack and adopt if AMI matches."""
        edh = FakeEdhClient(
            list_software_stacks=lambda kind: list(DCV_STACKS) + [dict(NEW_STACK)] if kind == "dcv" else [],
        )
        fakes = install_admin_fakes(monkeypatch, edh=edh)

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("vdi"), body=VDI_BODY), {})

        assert response["statusCode"] == 201
        assert fakes.edh.calls_to("register_software_stack") == []
        assert len([r for r in edh_proxy_records(fakes) if r["edhRoute"] == ec.EDH_ROUTE_PROJECTS]) >= 1

    def test_project_assign_failure_with_unknown_outcome_carries_flag(self, monkeypatch):
        """A project assign failure with an unknown outcome carries outcomeUnknown flag."""
        edh = FakeEdhClient(
            list_software_stacks=lambda kind: list(DCV_STACKS) + [dict(NEW_STACK)] if kind == "dcv" else [],
            assign_software_stack_to_project=EdhOutcomeUnknownError("timeout"),
        )
        fakes = install_admin_fakes(monkeypatch, edh=edh)

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("vdi"), body=VDI_BODY), {})

        assert response["statusCode"] == 502
        body = response_body(response)
        assert "not confirmed" in body["message"] or "assigned" in body["message"]
        assert body.get("outcomeUnknown") is True

    def test_project_assign_failure_without_unknown_outcome_has_no_flag(self, monkeypatch):
        """A project assign failure without an unknown outcome has no outcomeUnknown flag."""
        edh = FakeEdhClient(
            list_software_stacks=lambda kind: list(DCV_STACKS) + [dict(NEW_STACK)] if kind == "dcv" else [],
            assign_software_stack_to_project=EdhApiError("Project assignment failed"),
        )
        fakes = install_admin_fakes(monkeypatch, edh=edh)

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("vdi"), body=VDI_BODY), {})

        assert response["statusCode"] == 400
        body = response_body(response)
        assert body.get("outcomeUnknown") is not True


@pytest.mark.unit
class TestDeleteImage:
    def test_vdi_delete_resolves_the_record_enforces_and_deletes(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client())

        response = fakes.module.lambda_handler(
            make_event("DELETE", image_path("vdi", "8"), path_params={"imageId": "8"}), {})

        assert response["statusCode"] == 200
        assert response_body(response) == {"kind": "vdi", "imageId": "8", "deleted": True}
        assert fakes.edh.calls_to("list_software_stacks") == [("dcv",)]
        assert fakes.edh.calls_to("delete_software_stack") == [("dcv", "8", edh_uid_for(ADMIN))]
        assert fakes.enforcer.enforce.call_args.args == (
            {"object__type": "edhImage", "edhImageId": "8", "edhOsFamily": "windows"}, "DELETE")

    def test_simulation_delete_uses_the_target_nodes_kind(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client())

        response = fakes.module.lambda_handler(
            make_event("DELETE", image_path("simulation", "3"), path_params={"imageId": "3"}), {})

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("delete_software_stack") == [("target_nodes", "3", edh_uid_for(ADMIN))]

    def test_an_unknown_id_is_404_without_deleting(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client())

        response = fakes.module.lambda_handler(
            make_event("DELETE", image_path("vdi", "999"), path_params={"imageId": "999"}), {})

        assert response["statusCode"] == 404
        assert fakes.edh.calls_to("delete_software_stack") == []

    def test_a_tier_2_denial_is_403_without_deleting(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client(), enforce=lambda obj, act: False)

        response = fakes.module.lambda_handler(
            make_event("DELETE", image_path("vdi", "8"), path_params={"imageId": "8"}), {})

        assert response["statusCode"] == 403
        assert fakes.edh.calls_to("delete_software_stack") == []

    def test_null_path_parameters_on_a_rest_event_are_400(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client())

        response = fakes.module.lambda_handler(
            make_event("DELETE", image_path("vdi", "8"), path_params=None, rest=True), {})

        assert response["statusCode"] == 400
        assert fakes.edh.calls == []

    def test_edh_not_found_on_delete_maps_to_the_typed_response(self, monkeypatch):
        fakes = install_admin_fakes(
            monkeypatch, edh=_catalog_client(delete_software_stack=EdhNotFoundError("gone")))

        response = fakes.module.lambda_handler(
            make_event("DELETE", image_path("vdi", "8"), path_params={"imageId": "8"}), {})

        assert response["statusCode"] == 404
        assert "gone" not in response["body"]
        assert response_body(response)["message"] == "The requested EDH resource was not found."

    def test_the_delete_is_audited_with_the_kind_route(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client())

        fakes.module.lambda_handler(
            make_event("DELETE", image_path("vdi", "8"), path_params={"imageId": "8"}), {})

        assert [(r["operation"], r["edhMethod"], r["edhRoute"]) for r in edh_proxy_records(fakes)] == [
            ("adminImageDelete", "GET", ec.EDH_SOFTWARE_STACK_ROUTES["dcv"]),
            ("adminImageDelete", "DELETE", ec.EDH_SOFTWARE_STACK_ROUTES["dcv"]),
        ]


@pytest.mark.unit
class TestFixtureShapes:
    """The key names this module reads from software-stack records, checked against the sanitized
    EDH captures so a renamed EDH field fails here rather than as an empty catalog in production."""

    def test_the_dcv_capture_maps_to_a_complete_vdi_item(self):
        records = fixture_payload("software_stacks_dcv_list.json")
        assert records, "the dcv capture holds no software stacks"
        item = service._image_item(service.IMAGE_KIND_VDI, records[0])
        assert item["imageId"] != ""
        assert item["stackName"] != ""
        assert item["amiId"].startswith("ami-")
        assert item["osFamily"] in ("linux", "windows")
        assert item["baseOs"] != ""
        assert item["rootSize"] is not None
        assert item["profileId"] is not None
        assert item["osFamily"] == str(records[0][service.EDH_FIELD_STACK_OS_FAMILY]).lower()
        assert item["architecture"] is not None
        assert item["architecture"] == records[0][CAPTURED_ARCHITECTURE_FIELD]

    def test_the_target_nodes_capture_maps_to_a_complete_simulation_item(self):
        records = fixture_payload("software_stacks_target_nodes_list.json")
        assert records, "the target_nodes capture holds no software stacks"
        item = service._image_item(service.IMAGE_KIND_SIMULATION, records[0])
        assert item["imageId"] != ""
        assert item["stackName"] != ""
        assert item["osFamily"] == "linux"
        assert item["rootSize"] is not None
        assert item["profileId"] is not None
        assert item["osFamily"] == str(records[0][service.EDH_FIELD_STACK_OS_FAMILY]).lower()
        assert item["architecture"] is not None
        assert item["architecture"] == records[0][CAPTURED_ARCHITECTURE_FIELD]


@pytest.mark.unit
class TestOsFamilyFallback:
    """The edhOsFamily Tier-2 field of a record EDH did not classify: derived from the base OS for a
    dcv stack, Linux for a target-node stack."""

    def test_a_dcv_record_without_os_family_derives_it_from_base_os(self):
        record = {**DCV_STACKS[0], service.EDH_FIELD_STACK_BASE_OS: "windows2022"}
        record.pop(service.EDH_FIELD_STACK_OS_FAMILY)
        assert service._image_item(service.IMAGE_KIND_VDI, record)["osFamily"] == "windows"

    def test_edhs_own_classification_wins_over_the_base_os(self):
        # Control for the fallback: with the column present the base OS is not consulted.
        record = {**DCV_STACKS[0], service.EDH_FIELD_STACK_BASE_OS: "windows2022"}
        assert service._image_item(service.IMAGE_KIND_VDI, record)["osFamily"] == "linux"

    def test_a_target_node_record_without_os_family_is_linux(self):
        record = dict(TARGET_NODE_STACKS[0])
        record.pop(service.EDH_FIELD_STACK_OS_FAMILY)
        assert service._image_item(service.IMAGE_KIND_SIMULATION, record)["osFamily"] == "linux"


@pytest.mark.unit
class TestRegisterImageProjectAssignment:
    """EDH resolves a user's launchable stacks through the projects a stack is attached to, so the
    register route attaches the new stack to the launch project and reports a refusal to the caller."""

    def test_the_new_stack_is_attached_to_the_default_project(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_empty_catalog_client())

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("vdi"), body=VDI_BODY), {})

        assert response["statusCode"] == 201 and response_body(response)["imageId"] == "42"
        assert fakes.edh.calls_to("assign_software_stack_to_project") == [
            ("dcv", "42", DEFAULT_PROJECT, edh_uid_for(ADMIN))]
        assert fakes.edh.projects.stack_ids("dcv") == ["42"]
        assert fakes.edh.projects.puts[0]["allowed_users"] == "alice,bob"

    def test_the_assignment_is_audited_as_the_project_update(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_empty_catalog_client())

        fakes.module.lambda_handler(make_event("POST", image_path("vdi"), body=VDI_BODY), {})

        # Precheck, register, read-back, assign
        assert [(r["operation"], r["edhMethod"], r["edhRoute"]) for r in edh_proxy_records(fakes)] == [
            ("adminImageRegister", "GET", ec.EDH_SOFTWARE_STACK_ROUTES["dcv"]),
            ("adminImageRegister", "POST", ec.EDH_SOFTWARE_STACK_ROUTES["dcv"]),
            ("adminImageRegister", "GET", ec.EDH_SOFTWARE_STACK_ROUTES["dcv"]),
            ("adminImageRegister", "PUT", ec.EDH_ROUTE_PROJECTS),
        ]

    def test_a_stack_the_catalog_does_not_show_after_register_is_answered_as_unassigned(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client())

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("simulation"), body=SIM_BODY), {})

        assert response["statusCode"] == 502
        assert response_body(response)["message"] == (
            f"registered in EDH but not assigned to project '{DEFAULT_PROJECT}' (its EDH id could not be read back)")
        assert fakes.edh.calls_to("assign_software_stack_to_project") == []
        assert len(fakes.edh.calls_to("register_software_stack")) == 1

    def test_a_catalog_that_cannot_be_read_after_register_is_answered_as_unassigned(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_empty_catalog_client(
            list_software_stacks=EdhUnavailableError("EDH returned 504 for GET /api/dcv/virtual_desktops/software_stacks")))

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("vdi"), body=VDI_BODY), {})

        assert response["statusCode"] == 502
        body = response_body(response)
        assert body["message"] == (
            f"registered in EDH but not assigned to project '{DEFAULT_PROJECT}' (the catalog could not be read back)")
        assert "504" not in response["body"]
        assert "outcomeUnknown" not in body
        assert fakes.edh.calls_to("assign_software_stack_to_project") == []
        # Precheck GET fails, register succeeds, read-back GET fails
        assert [(r["edhMethod"], r["edhStatus"]) for r in edh_proxy_records(fakes)] == [
            ("GET", "EdhUnavailableError"), ("POST", "ok"), ("GET", "EdhUnavailableError")]

    def test_a_project_edh_does_not_have_is_the_typed_not_found_response(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client(
            assign_software_stack_to_project=EdhNotFoundError(
                f"EDH has no active project named '{DEFAULT_PROJECT}'")))

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("vdi"), body=VDI_BODY), {})

        assert response["statusCode"] == 404
        message = response_body(response)["message"]
        assert message.startswith(f"registered in EDH but not assigned to project '{DEFAULT_PROJECT}'")
        assert "EdhNotFoundError: The requested EDH resource was not found." in message
        assert "outcomeUnknown" not in response_body(response)
        assert [(r["edhMethod"], r["edhStatus"]) for r in edh_proxy_records(fakes)][-1] == ("PUT", "EdhNotFoundError")

    def test_an_assignment_failure_answers_with_the_project_assign_message(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client(
            assign_software_stack_to_project=EdhApiError("Budget not found")))

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("vdi"), body=VDI_BODY), {})

        assert response["statusCode"] == 400
        body = response_body(response)
        assert f"registered in EDH but not assigned to project '{DEFAULT_PROJECT}'" in body["message"]
        assert "EdhApiError" in body["message"]
        assert "outcomeUnknown" not in body
        assert [(r["edhMethod"], r["edhStatus"]) for r in edh_proxy_records(fakes)][-1] ==             ("PUT", "EdhApiError")

    def test_an_assignment_edh_did_not_answer_keeps_its_message_and_is_flagged_as_an_unknown_outcome(
            self, monkeypatch):
        # The project update may have landed, so the caller is told the outcome is unconfirmed.
        fakes = install_admin_fakes(monkeypatch, edh=_catalog_client(
            assign_software_stack_to_project=EdhOutcomeUnknownError("EDH did not answer PUT /api/projects")))

        response = fakes.module.lambda_handler(
            make_event("POST", image_path("vdi"), body=VDI_BODY), {})

        assert response["statusCode"] == 502
        body = response_body(response)
        assert body == {
            "message": (f"registered in EDH but not assigned to project '{DEFAULT_PROJECT}' "
                        "(EdhOutcomeUnknownError: EDH did not confirm the request in time; it may still be in "
                        "progress. Refresh before trying again.)"),
            "outcomeUnknown": True,
        }
        assert "/api/projects" not in response["body"]
