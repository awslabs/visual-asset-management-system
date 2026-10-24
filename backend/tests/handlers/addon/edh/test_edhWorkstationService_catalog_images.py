# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The image catalog: EDH's dcv software stacks, each enforced as an edhImage with GET.

Per-item filtering is the one Tier-2 shape that appends on success, so a denied item is absent
rather than the whole response failing. The enforcement dict is asserted key-for-key because
``_scrub_object_fields`` drops anything outside the edhImage matrix without an error. The OS family
is EDH's own classification when the record carries one, else what the base OS implies; a stack
with neither is not offered rather than offered as Linux. Each item names the CPU architecture EDH
registered for the stack, or null when the record states none; a stack without one is still offered.
A stack an AVAILABLE AMI recipe build registered is an ``amiRecipe`` image and leads the list; every
other stack is ``baseline``. With AMI builds off the recipe table is not read and every stack is
``baseline``.
"""

from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from backend.backend.handlers.addon.edh.common import edhAmiRecipeStore as recipe_store
from backend.backend.handlers.addon.edh.common.edhClient import EdhUnavailableError
from backend.tests.handlers.addon.edh.edhAmiTestSupport import create_recipe_table
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    IMAGES_PATH,
    FakeEdhClient,
    edh_proxy_records,
    install_service_fakes,
    make_event,
    response_body,
)
from backend.tests.handlers.addon.edh.edhTestSupport import load_fixture
import backend.backend.handlers.addon.edh.edhWorkstationService as service

# EDH's own classification of a captured stack; the oracle for the OS family the handler derives.
CAPTURED_OS_FAMILY_FIELD = "os_family"
# The architecture EDH registered for a captured stack (EC2's Architecture of its AMI); the oracle for
# the architecture the catalog names.
CAPTURED_ARCHITECTURE_FIELD = "ami_arch"


def _stack(stack_id, name, base_os, root_size=100, description=None, architecture=None):
    """A dcv stack as EDH lists it; ``architecture`` None leaves the record without one."""
    stack = {
        service.EDH_FIELD_STACK_ID: stack_id,
        service.EDH_FIELD_STACK_NAME: name,
        service.EDH_FIELD_STACK_BASE_OS: base_os,
        service.EDH_FIELD_STACK_ROOT_SIZE: root_size,
        service.EDH_FIELD_STACK_DESCRIPTION: description,
    }
    if architecture is not None:
        stack[service.EDH_FIELD_STACK_ARCHITECTURE] = architecture
    return stack


def _stacks():
    return [
        _stack(42, "Ubuntu 22 GPU", "ubuntu2204", 100, "GPU image", architecture="x86_64"),
        _stack(43, "Windows Server 2022", "windows2022", 200, architecture="x86_64"),
    ]


@pytest.mark.unit
class TestImageCatalog:
    def test_items_carry_the_catalog_shape_and_the_derived_os_family(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_software_stacks=_stacks()))

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert response["statusCode"] == 200
        assert response_body(response)["Items"] == [
            {"imageId": "42", "name": "Ubuntu 22 GPU", "baseOs": "ubuntu2204", "osFamily": "linux",
             "architecture": "x86_64", "rootSize": 100, "description": "GPU image", "category": "baseline"},
            {"imageId": "43", "name": "Windows Server 2022", "baseOs": "windows2022", "osFamily": "windows",
             "architecture": "x86_64", "rootSize": 200, "description": None, "category": "baseline"},
        ]
        assert fakes.edh.calls_to("list_software_stacks") == [("dcv",)]

    def test_an_item_carries_the_stacks_architecture_trimmed(self, monkeypatch):
        stacks = [_stack(47, "Ubuntu 22 Graviton", "ubuntu2204", architecture=" arm64 "), *_stacks()]
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_software_stacks=stacks))

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert {item["imageId"]: item["architecture"] for item in response_body(response)["Items"]} == {
            "47": "arm64", "42": "x86_64", "43": "x86_64"}

    @pytest.mark.parametrize("architecture", [None, "", "   ", 64], ids=["absent", "empty", "blank", "not-text"])
    def test_a_stack_without_an_architecture_is_offered_with_none(self, monkeypatch, architecture):
        stack = _stack(48, "Legacy image", "ubuntu2204")
        if architecture is not None:
            stack[service.EDH_FIELD_STACK_ARCHITECTURE] = architecture
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_software_stacks=[stack]))

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert [(item["imageId"], item["architecture"]) for item in response_body(response)["Items"]] == [
            ("48", None)]
        fakes.enforcer.enforce.assert_called_once_with(
            {"object__type": "edhImage", "edhImageId": "48", "edhOsFamily": "linux"}, "GET")

    def test_each_item_is_enforced_as_an_edh_image_with_get(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_software_stacks=_stacks()))

        fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert [call.args for call in fakes.enforcer.enforce.call_args_list] == [
            ({"object__type": "edhImage", "edhImageId": "42", "edhOsFamily": "linux"}, "GET"),
            ({"object__type": "edhImage", "edhImageId": "43", "edhOsFamily": "windows"}, "GET"),
        ]

    def test_a_denied_item_is_dropped_and_an_allowed_one_kept(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch,
            edh=FakeEdhClient(list_software_stacks=_stacks()),
            enforce=lambda obj, act: obj["edhOsFamily"] == "linux",
        )

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        # Positive control (the Linux image survives) beside the denial (the Windows image is gone).
        assert [item["imageId"] for item in response_body(response)["Items"]] == ["42"]

    def test_edhs_own_classification_wins_over_the_base_os(self, monkeypatch):
        classified = {**_stack(45, "Relabelled", "windows2022"), CAPTURED_OS_FAMILY_FIELD: "linux"}
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_software_stacks=[classified]))

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert [item["osFamily"] for item in response_body(response)["Items"]] == ["linux"]
        fakes.enforcer.enforce.assert_called_once_with(
            {"object__type": "edhImage", "edhImageId": "45", "edhOsFamily": "linux"}, "GET")

    def test_a_stack_with_neither_classification_nor_base_os_is_not_listed(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(list_software_stacks=[_stack(46, "Mystery", None), _stack(42, "Ubuntu", "ubuntu2204")]))

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        # Positive control (the classified image survives) beside the omission (the unclassified one is gone).
        assert [item["imageId"] for item in response_body(response)["Items"]] == ["42"]
        assert [call.args[0]["edhImageId"] for call in fakes.enforcer.enforce.call_args_list] == ["42"]

    def test_entries_that_are_not_records_are_skipped(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(list_software_stacks=["garbage", None, _stack(42, "Ubuntu", "ubuntu2204")]))

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert [item["imageId"] for item in response_body(response)["Items"]] == ["42"]

    def test_the_captured_catalog_is_read_through_the_field_constants(self, monkeypatch):
        """The stack field constants name columns the EDH list response carries: every captured stack
        yields a base OS and a root size, and the OS family derived from the base OS agrees with EDH's
        own classification of that stack."""
        capture = load_fixture("software_stacks_dcv")
        stacks = list(capture["response"]["json"]["message"].values())
        assert len(stacks) == capture["expect"]["count"]
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_software_stacks=stacks))

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert response["statusCode"] == 200
        items = response_body(response)["Items"]
        assert [item["imageId"] for item in items] == [str(stack[service.EDH_FIELD_STACK_ID]) for stack in stacks]
        assert all(isinstance(item["baseOs"], str) and item["baseOs"] for item in items)
        assert all(isinstance(item["rootSize"], int) for item in items)
        assert [item["osFamily"] for item in items] == [stack[CAPTURED_OS_FAMILY_FIELD] for stack in stacks]
        assert [item["architecture"] for item in items] == [stack[CAPTURED_ARCHITECTURE_FIELD] for stack in stacks]
        assert {item["architecture"] for item in items} == {"x86_64"}

    def test_edh_unavailable_is_a_502(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(list_software_stacks=EdhUnavailableError("read timeout")))

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert response["statusCode"] == 502
        fakes.enforcer.enforce.assert_not_called()

    def test_the_call_is_audited_as_an_edh_proxy_record(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_software_stacks=_stacks()))

        fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        records = edh_proxy_records(fakes)
        assert [record["operation"] for record in records] == ["catalogImages"]
        assert records[0]["edhStatus"] == "ok"
        assert (records[0]["edhMethod"], records[0]["edhRoute"]) == service.EDH_AUDIT_ROUTES["list_software_stacks"]

    def test_a_rest_shaped_event_is_served(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_software_stacks=_stacks()))

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH, rest=True), {})

        assert response["statusCode"] == 200
        assert len(response_body(response)["Items"]) == 2


@pytest.fixture
def recipe_table(monkeypatch):
    """A moto recipe table the handler reads through the real store."""
    with mock_aws():
        create_recipe_table(monkeypatch)
        yield recipe_store


def _recipe_world():
    """Two recipes with an AVAILABLE build each and a RETIRED one, and the dcv catalog EDH lists."""
    recipe_store.put_recipe({"recipeId": "al2023-desktop", "displayName": "Amazon Linux 2023 desktop",
                             "kind": "vdi", "stackName": "VAMS AL2023 Desktop"})
    recipe_store.put_recipe({"recipeId": "isaacsim", "displayName": "Isaac Sim workstation",
                             "kind": "vdi", "stackName": "VAMS Isaac Sim"})
    for build in (
        {"recipeId": "al2023-desktop", "version": 5, "status": "AVAILABLE", "edhStackId": "35", "edhKind": "vdi"},
        {"recipeId": "isaacsim", "version": 9, "status": "RETIRED", "edhStackId": "30", "edhKind": "vdi"},
        {"recipeId": "isaacsim", "version": 10, "status": "AVAILABLE", "edhStackId": "36", "edhKind": "vdi"},
    ):
        recipe_store.put_build(build)
    return [
        _stack(20, "Windows Server 2022", "windows2022"),
        _stack(36, "VAMS Isaac Sim v10", "ubuntu2204"),
        _stack(22, "ubuntu 22 GPU", "ubuntu2204"),
        _stack(30, "VAMS Isaac Sim v9", "ubuntu2204"),
        _stack(35, "VAMS AL2023 Desktop v5", "amazonlinux2023", architecture="x86_64"),
        _stack(9, "Ubuntu 9", "ubuntu2204"),
    ]


def _baseline_stacks():
    return [
        _stack(36, "VAMS Isaac Sim v10", "ubuntu2204"),
        _stack(22, "ubuntu 22 GPU", "ubuntu2204"),
        _stack(9, "Ubuntu 9", "ubuntu2204"),
    ]


def _with_real_store(monkeypatch, stacks, enforce=None):
    fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_software_stacks=stacks), enforce=enforce)
    monkeypatch.setattr(service, "recipe_store", recipe_store)
    return fakes


@pytest.mark.unit
class TestImageCategories:
    def test_recipe_images_lead_and_each_group_is_sorted_by_name_with_numbers_in_order(self, monkeypatch, recipe_table):
        fakes = _with_real_store(monkeypatch, _recipe_world())

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert response["statusCode"] == 200
        items = response_body(response)["Items"]
        # "Ubuntu 9" before "ubuntu 22 GPU": case is ignored and a run of digits compares as a number.
        assert [(item["imageId"], item["category"]) for item in items] == [
            ("35", "amiRecipe"), ("36", "amiRecipe"),
            ("9", "baseline"), ("22", "baseline"), ("30", "baseline"), ("20", "baseline"),
        ]

    def test_a_recipe_image_names_its_recipe_and_build_version(self, monkeypatch, recipe_table):
        fakes = _with_real_store(monkeypatch, _recipe_world())

        items = response_body(fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {}))["Items"]

        assert items[0] == {
            "imageId": "35", "name": "VAMS AL2023 Desktop v5", "baseOs": "amazonlinux2023", "osFamily": "linux",
            "architecture": "x86_64", "rootSize": 100, "description": None, "category": "amiRecipe",
            "recipeId": "al2023-desktop", "recipeDisplayName": "Amazon Linux 2023 desktop", "recipeVersion": 5,
        }
        assert (items[1]["recipeId"], items[1]["recipeDisplayName"], items[1]["recipeVersion"]) == (
            "isaacsim", "Isaac Sim workstation", 10)
        assert all(not {"recipeId", "recipeDisplayName", "recipeVersion"} & set(item) for item in items[2:])

    def test_a_recipe_image_carries_its_recipes_launch_instance_types(self, monkeypatch, recipe_table):
        """The list is the recipe's, in its order; a recipe without one and a baseline image carry none."""
        stacks = _recipe_world()
        recipe_store.update_recipe("isaacsim", launchInstanceTypes=["g6e.xlarge", "g5.2xlarge"])
        fakes = _with_real_store(monkeypatch, stacks)

        items = response_body(fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {}))["Items"]

        by_id = {item["imageId"]: item for item in items}
        assert by_id["36"]["launchInstanceTypes"] == ["g6e.xlarge", "g5.2xlarge"]
        assert by_id["35"]["category"] == "amiRecipe" and "launchInstanceTypes" not in by_id["35"]
        assert by_id["30"]["category"] == "baseline" and "launchInstanceTypes" not in by_id["30"]
        assert all("launchInstanceTypes" not in item for item in items if item["category"] == "baseline")

    @pytest.mark.parametrize("value", [[], None], ids=["empty", "null"])
    def test_an_empty_or_null_stored_list_is_no_list(self, monkeypatch, recipe_table, value):
        stacks = _recipe_world()
        recipe_store.update_recipe("isaacsim", launchInstanceTypes=value)
        fakes = _with_real_store(monkeypatch, stacks)

        items = response_body(fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {}))["Items"]

        recipe_image = next(item for item in items if item["imageId"] == "36")
        assert recipe_image["recipeId"] == "isaacsim" and "launchInstanceTypes" not in recipe_image

    def test_a_recipe_image_keeps_its_architecture_once_categorised(self, monkeypatch, recipe_table):
        fakes = _with_real_store(monkeypatch, _recipe_world())

        items = response_body(fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {}))["Items"]

        by_id = {item["imageId"]: item for item in items}
        assert (by_id["35"]["category"], by_id["35"]["architecture"]) == ("amiRecipe", "x86_64")
        assert (by_id["36"]["category"], by_id["36"]["architecture"]) == ("amiRecipe", None)
        assert (by_id["22"]["category"], by_id["22"]["architecture"]) == ("baseline", None)

    def test_the_stack_of_a_retired_build_is_a_baseline_image(self, monkeypatch, recipe_table):
        fakes = _with_real_store(monkeypatch, _recipe_world())

        items = response_body(fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {}))["Items"]

        retired = next(item for item in items if item["imageId"] == "30")
        assert retired["category"] == "baseline" and "recipeId" not in retired

    def test_a_simulation_build_names_no_desktop_image_whatever_its_stack_id(self, monkeypatch, recipe_table):
        """EDH numbers its target-node stacks apart from its dcv stacks, so the stack id a simulation
        build registered can equal a desktop image's."""
        stacks = _recipe_world()
        recipe_store.put_recipe({"recipeId": "sim-node", "displayName": "Simulation node", "kind": "simulation",
                                 "stackName": "VAMS Sim Node"})
        for build in (
            {"recipeId": "sim-node", "version": 2, "status": "AVAILABLE", "edhStackId": "22", "edhKind": "simulation"},
            # A later version than the desktop build that registered dcv stack 35.
            {"recipeId": "sim-node", "version": 7, "status": "AVAILABLE", "edhStackId": "35", "edhKind": "simulation"},
        ):
            recipe_store.put_build(build)
        fakes = _with_real_store(monkeypatch, stacks)

        items = response_body(fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {}))["Items"]

        by_id = {item["imageId"]: item for item in items}
        assert by_id["22"]["category"] == "baseline" and "recipeId" not in by_id["22"]
        assert (by_id["35"]["recipeId"], by_id["35"]["recipeVersion"]) == ("al2023-desktop", 5)
        assert [item["imageId"] for item in items if item["category"] == "amiRecipe"] == ["35", "36"]

    def test_tier2_filtering_is_unchanged(self, monkeypatch, recipe_table):
        fakes = _with_real_store(monkeypatch, _recipe_world(), enforce=lambda obj, act: obj["edhImageId"] != "35")

        items = response_body(fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {}))["Items"]

        assert [item["imageId"] for item in items] == ["36", "9", "22", "30", "20"]
        assert fakes.enforcer.enforce.call_args_list
        assert [call.args for call in fakes.enforcer.enforce.call_args_list] == [
            ({"object__type": "edhImage", "edhImageId": image_id, "edhOsFamily": os_family}, "GET")
            for image_id, os_family in (("20", "windows"), ("36", "linux"), ("22", "linux"), ("30", "linux"),
                                        ("35", "linux"), ("9", "linux"))
        ]

    def test_a_catalog_with_nothing_to_offer_reads_no_recipe_builds(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_software_stacks=_stacks()),
                                      enforce=lambda obj, act: False)

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert response["statusCode"] == 200 and response_body(response)["Items"] == []
        fakes.recipe_store.available_builds_by_stack_id.assert_not_called()

    def test_a_recipe_table_that_cannot_be_read_lists_every_image_as_baseline_and_warns(self, monkeypatch):
        logger = MagicMock()
        with mock_aws():
            # No table: the scan is refused with a ClientError, as it is for a Lambda without a grant.
            monkeypatch.setattr(recipe_store, "dynamodb", boto3.resource("dynamodb", region_name="us-east-1"))
            fakes = _with_real_store(monkeypatch, _baseline_stacks())
            monkeypatch.setattr(service, "logger", logger)
            response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert response["statusCode"] == 200
        items = response_body(response)["Items"]
        assert [(item["imageId"], item["category"]) for item in items] == [
            ("9", "baseline"), ("22", "baseline"), ("36", "baseline")]
        logger.warning.assert_called_once()
        assert "ResourceNotFoundException" in logger.warning.call_args.args[0]

    def test_an_unpublished_recipe_table_name_lists_every_image_as_baseline_and_warns(self, monkeypatch):
        logger = MagicMock()
        missing = KeyError("Resource name parameter not found in SSM: addon/dynamoTables/edhAmiRecipeStorage")
        monkeypatch.setattr(recipe_store, "table_name", None)
        monkeypatch.setattr(recipe_store, "get_table_name", MagicMock(side_effect=missing))
        fakes = _with_real_store(monkeypatch, _baseline_stacks())
        monkeypatch.setattr(service, "logger", logger)

        response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})

        assert response["statusCode"] == 200
        assert [(item["imageId"], item["category"]) for item in response_body(response)["Items"]] == [
            ("9", "baseline"), ("22", "baseline"), ("36", "baseline")]
        logger.warning.assert_called_once()
        assert "KeyError" in logger.warning.call_args.args[0]


def _desktop_build(stack_id):
    """The store's answer naming one dcv stack an AVAILABLE desktop recipe build registered."""
    recipe = {"recipeId": "al2023-desktop", "displayName": "Amazon Linux 2023 desktop", "kind": "vdi"}
    build = {"recipeId": "al2023-desktop", "version": 5, "status": "AVAILABLE", "edhStackId": stack_id,
             "edhKind": "vdi"}
    return {stack_id: (recipe, build)}


def _access_denied():
    """What a scan of the recipe table answers a Lambda that holds no grant on it."""
    return ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}}, "Scan")


def _serve(monkeypatch, builds_enabled, *, answer=None, raises=None):
    """The catalog served with AMI builds on or off, through a stub store and a stub logger."""
    fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_software_stacks=_baseline_stacks()))
    monkeypatch.setattr(service, "ami_builds_enabled", builds_enabled)
    logger = MagicMock()
    monkeypatch.setattr(service, "logger", logger)
    if raises is not None:
        fakes.recipe_store.available_builds_by_stack_id.side_effect = raises
    else:
        fakes.recipe_store.available_builds_by_stack_id.return_value = answer
    response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})
    assert response["statusCode"] == 200
    return fakes.recipe_store.available_builds_by_stack_id, logger, response_body(response)["Items"]


def _logged_at_warning_or_above(logger):
    return [call for call in logger.method_calls if call[0] in ("warning", "warn", "error", "exception", "critical")]


@pytest.mark.unit
class TestImageCategoriesWithAmiBuildsOff:
    """With AMI builds off the Lambda holds no grant on the recipe table, so the catalog does not read
    it: every image is baseline and nothing is logged. Each case is paired with the same request with
    builds on, which reads the same store, so the missing read is the flag's doing."""

    def test_no_recipe_build_is_read_and_every_image_is_baseline(self, monkeypatch):
        store_read, logger, items = _serve(monkeypatch, False, answer=_desktop_build("36"))

        store_read.assert_not_called()
        assert [(item["imageId"], item["category"]) for item in items] == [
            ("9", "baseline"), ("22", "baseline"), ("36", "baseline")]
        assert all(not {"recipeId", "recipeDisplayName", "recipeVersion"} & set(item) for item in items)
        assert _logged_at_warning_or_above(logger) == []

    def test_with_builds_on_the_same_store_names_the_recipe_image(self, monkeypatch):
        store_read, logger, items = _serve(monkeypatch, True, answer=_desktop_build("36"))

        store_read.assert_called_once_with("vdi")
        assert [(item["imageId"], item["category"]) for item in items] == [
            ("36", "amiRecipe"), ("9", "baseline"), ("22", "baseline")]
        assert _logged_at_warning_or_above(logger) == []

    def test_a_table_the_lambda_cannot_read_logs_no_warning(self, monkeypatch):
        store_read, logger, items = _serve(monkeypatch, False, raises=_access_denied())

        store_read.assert_not_called()
        assert [(item["imageId"], item["category"]) for item in items] == [
            ("9", "baseline"), ("22", "baseline"), ("36", "baseline")]
        assert _logged_at_warning_or_above(logger) == []

    def test_with_builds_on_a_failed_read_still_warns(self, monkeypatch):
        store_read, logger, items = _serve(monkeypatch, True, raises=_access_denied())

        store_read.assert_called_once_with("vdi")
        assert [(item["imageId"], item["category"]) for item in items] == [
            ("9", "baseline"), ("22", "baseline"), ("36", "baseline")]
        logger.warning.assert_called_once()
        assert "AccessDeniedException" in logger.warning.call_args.args[0]
