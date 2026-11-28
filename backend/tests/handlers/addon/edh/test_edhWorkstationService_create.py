# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The launch route: validation, the image checked against EDH's catalog, Tier-2 with the image's own
OS family, the instance type checked against the image's architecture, the idempotent row, and the
hand-over.

Every refusal asserts what was NOT done (no row written, no worker invoked, no EDH mutation), and the
hand-over asserts what the worker receives and what happens to the CREATING row when the invoke
fails -- because that row is what a repeated POST with the same bootstrapId answers from. The route's
one EDH call is the catalog read that resolves the image; the launch's own EDH calls are the worker's.
"""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.edh.common.edhClient import EdhUnavailableError
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    API_KEY_CLAIMS,
    BOOTSTRAP_OWN,
    CATALOG_STACK,
    SESSION_OWN,
    USER,
    WORKSTATIONS_PATH,
    FakeEdhClient,
    edh_proxy_records,
    install_service_fakes,
    make_event,
    other_row,
    own_row,
    response_body,
)
from backend.tests.handlers.addon.edh.edhTestSupport import load_fixture
import backend.backend.handlers.addon.edh.edhWorkstationService as service

# The refusal of a type that does not run the image's architecture.
ARCHITECTURE_MISMATCH_MESSAGE = "instanceType does not match the image's architecture"
# The one type CATALOG_STACK's own profile lists solely under arm64, for its x86_64 image.
GRAVITON_TYPE = "m7g.large"
# Architectures EC2 reports outside ARCHITECTURE_FAMILIES, each a family of its own, and a type of each.
ARM64_MAC = "arm64_mac"
ARM64_MAC_TYPE = "mac2.metal"
X86_64_MAC = "x86_64_mac"
X86_64_MAC_TYPE = "mac1.metal"
# The allow-list of the architecture cases: it admits every type they request, so each answer is the
# architecture check's own.
ARCHITECTURE_ALLOW_LIST = (
    "g5.xlarge", "g5.2xlarge", "m7g.large", "t2.medium", "c6i.large", ARM64_MAC_TYPE, X86_64_MAC_TYPE)
# A captured stack's nested profile and its allowed-types value, as EDH's list response names them.
CAPTURED_PROFILE_FIELD = "profile"
CAPTURED_PROFILE_INSTANCE_TYPES_FIELD = "allowed_instance_types"
# The refusal of a type the image's recipe does not offer.
NOT_OFFERED_MESSAGE = "instanceType is not offered for this image"
# The types the recipe of CATALOG_STACK's image offers, in the recipe's order; g5.xlarge is not among them.
# Each is on the allow-list of the architecture cases and runs the image's architecture, so a refusal of
# another type is the recipe check's own.
RECIPE_LAUNCH_TYPES = ["g5.2xlarge", "c6i.large"]


def _body(**overrides):
    body = {
        "bootstrapId": BOOTSTRAP_OWN,
        "imageId": "42",
        "instanceType": "g5.xlarge",
        "name": "Design desk",
        "rootSizeGb": 100,
        "osFamily": "linux",
    }
    body.update(overrides)
    return body


def _duplicate_key_error():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}}, "PutItem")


def _stack(base_os, os_family):
    """The catalog stack of imageId 42 with the given base OS and EDH classification (None: absent)."""
    stack = dict(CATALOG_STACK)
    stack[service.EDH_FIELD_STACK_BASE_OS] = base_os
    stack.pop(service.EDH_FIELD_STACK_OS_FAMILY)
    if os_family is not None:
        stack[service.EDH_FIELD_STACK_OS_FAMILY] = os_family
    return stack


def _catalog_reads(fakes):
    """The wrappers the route called on the EDH client, in order."""
    return [name for name, _ in fakes.edh.calls]


def _launch(monkeypatch, *, claims=None, edh=None, enforce=None):
    fakes = install_service_fakes(monkeypatch, claims=claims, edh=edh, enforce=enforce)
    monkeypatch.setattr(service, "allowed_instance_types", ("g5.xlarge", "g5.2xlarge"))
    return fakes


def _post(fakes, body=None, rest=False):
    return fakes.module.lambda_handler(
        make_event("POST", WORKSTATIONS_PATH, body=_body() if body is None else body, rest=rest), {})


def _payload(fakes):
    """The JSON payload the worker was invoked with."""
    return json.loads(fakes.lambda_client.invoke.call_args[1]["Payload"])


def _arch_stack(**fields):
    """CATALOG_STACK with ``fields`` set on it; a field given as None is absent."""
    stack = {**CATALOG_STACK, **fields}
    return {key: value for key, value in stack.items() if value is not None}


def _profile(allowed_instance_types):
    """A stack's nested profile; a mapping is JSON-encoded as EDH lists it, anything else is kept as is."""
    if isinstance(allowed_instance_types, dict):
        allowed_instance_types = json.dumps(allowed_instance_types)
    return {service.EDH_FIELD_PROFILE_INSTANCE_TYPES: allowed_instance_types}


def _launch_arch(monkeypatch, stack=None, enforce=None):
    """The launch route over one catalog stack, with the session mint recorded."""
    fakes = install_service_fakes(
        monkeypatch, edh=FakeEdhClient(list_software_stacks=[stack or dict(CATALOG_STACK)]), enforce=enforce)
    monkeypatch.setattr(service, "allowed_instance_types", ARCHITECTURE_ALLOW_LIST)
    fakes.mint = MagicMock(return_value=("disabled", None))
    monkeypatch.setattr(service.bootstrap, "bootstrap_session", fakes.mint)
    return fakes


@pytest.mark.unit
class TestLaunchRefusals:
    def test_api_key_sessions_cannot_launch(self, monkeypatch):
        fakes = _launch(monkeypatch, claims=API_KEY_CLAIMS)

        response = _post(fakes)

        assert response["statusCode"] == 403
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()
        fakes.client_factory.assert_not_called()

    def test_a_malformed_body_is_a_400_before_any_write(self, monkeypatch):
        fakes = _launch(monkeypatch)

        response = _post(fakes, _body(rootSizeGb=5))

        assert response["statusCode"] == 400
        assert "rootSizeGb" in response_body(response)["message"]
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()
        fakes.client_factory.assert_not_called()

    def test_a_non_json_body_is_400(self, monkeypatch):
        fakes = _launch(monkeypatch)

        response = _post(fakes, "not json")

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "Request body is not valid JSON"
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()
        fakes.client_factory.assert_not_called()

    def test_a_json_body_that_is_not_an_object_is_400(self, monkeypatch):
        fakes = _launch(monkeypatch)

        response = _post(fakes, "[]")

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "Request body is not valid JSON"
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()
        fakes.client_factory.assert_not_called()

    def test_an_omitted_os_family_is_a_400_before_any_write(self, monkeypatch):
        """osFamily stays required: the body's value is checked against the image's, never filled in."""
        fakes = _launch(monkeypatch)
        body = _body()
        del body["osFamily"]

        response = _post(fakes, body)

        assert response["statusCode"] == 400
        assert "osFamily" in response_body(response)["message"]
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()
        fakes.client_factory.assert_not_called()
        fakes.enforcer.enforce.assert_not_called()

    def test_an_instance_type_outside_the_allow_list_is_a_400(self, monkeypatch):
        fakes = _launch(monkeypatch)

        response = _post(fakes, _body(instanceType="p4d.24xlarge"))

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "instanceType is not permitted in this deployment"
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()

    def test_an_empty_allow_list_accepts_any_instance_type(self, monkeypatch):
        """Control for the allow-list refusal."""
        fakes = _launch(monkeypatch)
        monkeypatch.setattr(service, "allowed_instance_types", ())

        response = _post(fakes, _body(instanceType="p4d.24xlarge"))

        assert response["statusCode"] == 202
        fakes.lambda_client.invoke.assert_called_once()

    def test_an_unknown_image_id_is_a_400_before_any_write(self, monkeypatch):
        fakes = _launch(monkeypatch)

        response = _post(fakes, _body(imageId="99"))

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "imageId is not a registered VDI image"
        assert _catalog_reads(fakes) == ["list_software_stacks"]
        fakes.enforcer.enforce.assert_not_called()
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()

    def test_the_os_family_is_derived_from_the_image_not_the_body(self, monkeypatch):
        """EDH's own classification is absent, so the family comes from the base OS; the body's Linux
        does not match a Windows image."""
        fakes = _launch(monkeypatch, edh=FakeEdhClient(list_software_stacks=[_stack("windows2022", None)]))

        response = _post(fakes, _body(osFamily="linux"))

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "osFamily does not match the image"
        fakes.enforcer.enforce.assert_not_called()
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()

    def test_an_image_whose_os_family_cannot_be_determined_is_a_400(self, monkeypatch):
        fakes = _launch(monkeypatch, edh=FakeEdhClient(list_software_stacks=[_stack(None, None)]))

        response = _post(fakes)

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "The image's OS family cannot be determined"
        fakes.enforcer.enforce.assert_not_called()
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()

    def test_an_edh_failure_on_the_catalog_read_is_mapped_before_any_write(self, monkeypatch):
        fakes = _launch(monkeypatch, edh=FakeEdhClient(list_software_stacks=EdhUnavailableError("read timeout")))

        response = _post(fakes)

        assert response["statusCode"] == 502
        assert "read timeout" not in response["body"]
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()

    def test_tier2_is_enforced_with_the_image_derived_family_before_any_write(self, monkeypatch):
        fakes = _launch(monkeypatch, edh=FakeEdhClient(list_software_stacks=[_stack("windows2022", "windows")]),
                        enforce=lambda obj, act: False)

        response = _post(fakes, _body(imageId="42", osFamily="windows", instanceType="g5.2xlarge"))

        assert response["statusCode"] == 403
        fakes.enforcer.enforce.assert_called_once_with(
            {"object__type": "edhWorkstation", "edhImageId": "42", "edhOsFamily": "windows",
             "edhInstanceType": "g5.2xlarge"},
            "POST",
        )
        assert _catalog_reads(fakes) == ["list_software_stacks"]
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()


@pytest.mark.unit
class TestLaunchArchitecture:
    def test_the_launch_check_reads_the_family_table_the_recipe_checks_read(self):
        # One table serves the launch check here and the AMI recipe checks.
        models = sys.modules[service.CreateWorkstationRequestModel.__module__]
        assert service.ARCHITECTURE_FAMILIES is models.ARCHITECTURE_FAMILIES

    def test_a_type_the_images_profile_lists_only_under_another_family_is_a_400_before_any_write(self, monkeypatch):
        fakes = _launch_arch(monkeypatch)

        response = _post(fakes, _body(instanceType=GRAVITON_TYPE))

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == ARCHITECTURE_MISMATCH_MESSAGE
        fakes.table.put_item.assert_not_called()
        fakes.mint.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()
        assert _catalog_reads(fakes) == ["list_software_stacks"]

    def test_tier2_is_answered_before_the_architecture(self, monkeypatch):
        fakes = _launch_arch(monkeypatch, enforce=lambda obj, act: False)

        response = _post(fakes, _body(instanceType=GRAVITON_TYPE))

        assert response["statusCode"] == 403
        fakes.enforcer.enforce.assert_called_once_with(
            {"object__type": "edhWorkstation", "edhImageId": "42", "edhOsFamily": "linux",
             "edhInstanceType": GRAVITON_TYPE},
            "POST",
        )
        fakes.table.put_item.assert_not_called()
        fakes.mint.assert_not_called()

    def test_the_architecture_is_answered_before_the_root_size_floor(self, monkeypatch):
        fakes = _launch_arch(monkeypatch)

        response = _post(fakes, _body(instanceType=GRAVITON_TYPE, rootSizeGb=50))

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == ARCHITECTURE_MISMATCH_MESSAGE

    def test_the_refusal_names_neither_the_type_nor_the_image(self, monkeypatch):
        fakes = _launch_arch(monkeypatch, _arch_stack(**{service.EDH_FIELD_STACK_ID: 4217}))

        response = _post(fakes, _body(imageId="4217", instanceType=GRAVITON_TYPE))

        assert response["statusCode"] == 400
        assert GRAVITON_TYPE not in response["body"]
        assert "4217" not in response["body"]

    def test_the_captured_stack_is_checked_against_its_own_nested_profile(self, monkeypatch):
        """A captured stack carries its own profile, whose allowed types are the JSON string keyed by
        architecture; a type added to its arm64 group alone is refused for the stack's x86_64 image."""
        capture = load_fixture("software_stacks_dcv")
        stack = list(capture["response"]["json"]["message"].values())[0]
        profile = stack[CAPTURED_PROFILE_FIELD]
        by_architecture = json.loads(profile[CAPTURED_PROFILE_INSTANCE_TYPES_FIELD])
        assert not any(GRAVITON_TYPE in arch_types for arch_types in by_architecture.values())
        by_architecture.setdefault("arm64", []).append(GRAVITON_TYPE)
        stack = {**stack, CAPTURED_PROFILE_FIELD: {
            **profile, CAPTURED_PROFILE_INSTANCE_TYPES_FIELD: json.dumps(by_architecture)}}
        fakes = _launch_arch(monkeypatch, stack)

        response = _post(fakes, _body(imageId=str(stack[service.EDH_FIELD_STACK_ID]), instanceType=GRAVITON_TYPE,
                                      osFamily=service._stack_os_family(stack)))

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == ARCHITECTURE_MISMATCH_MESSAGE

    @pytest.mark.parametrize("stack, instance_type", [
        (_arch_stack(**{service.EDH_FIELD_STACK_ARCHITECTURE: ARM64_MAC, service.EDH_FIELD_STACK_PROFILE: _profile(
            {ARM64_MAC: [ARM64_MAC_TYPE], X86_64_MAC: [X86_64_MAC_TYPE]})}), X86_64_MAC_TYPE),
        (_arch_stack(**{service.EDH_FIELD_STACK_PROFILE: _profile(
            {"x86_64": ["g5.xlarge"], X86_64_MAC: [X86_64_MAC_TYPE]})}), X86_64_MAC_TYPE),
    ], ids=["unmapped-image-with-a-type-of-another-unmapped-group", "x86_64-image-with-a-type-of-an-unmapped-group"])
    def test_an_architecture_outside_the_families_is_a_family_of_its_own(self, monkeypatch, stack, instance_type):
        fakes = _launch_arch(monkeypatch, stack)

        response = _post(fakes, _body(instanceType=instance_type))

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == ARCHITECTURE_MISMATCH_MESSAGE
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()

    @pytest.mark.parametrize("stack, instance_type", [
        (dict(CATALOG_STACK), "g5.xlarge"),
        (_arch_stack(**{service.EDH_FIELD_STACK_PROFILE: _profile(
            {"x86_64": ["g5.xlarge"], "arm64": [], "i386": ["t2.medium"]})}), "t2.medium"),
        (_arch_stack(**{service.EDH_FIELD_STACK_PROFILE: _profile(
            {"arm64": ["m7g.large"], "x86_64": ["m7g.large"]})}), "m7g.large"),
        (_arch_stack(**{service.EDH_FIELD_STACK_ARCHITECTURE: "arm64"}), GRAVITON_TYPE),
        (_arch_stack(**{service.EDH_FIELD_STACK_ARCHITECTURE: ARM64_MAC, service.EDH_FIELD_STACK_PROFILE: _profile(
            {ARM64_MAC: [ARM64_MAC_TYPE], X86_64_MAC: [X86_64_MAC_TYPE]})}), ARM64_MAC_TYPE),
    ], ids=["listed-under-the-images-architecture", "i386-grouped-on-x86_64", "listed-under-both-families",
            "arm64-image-with-an-arm64-type", "unmapped-image-with-a-type-of-its-own-group"])
    def test_a_type_the_profile_lists_under_the_images_family_launches(self, monkeypatch, stack, instance_type):
        fakes = _launch_arch(monkeypatch, stack)

        response = _post(fakes, _body(instanceType=instance_type))

        assert response["statusCode"] == 202
        assert _payload(fakes)["instanceType"] == instance_type

    @pytest.mark.parametrize("stack, instance_type", [
        (_arch_stack(**{service.EDH_FIELD_STACK_ARCHITECTURE: None}), GRAVITON_TYPE),
        (_arch_stack(**{service.EDH_FIELD_STACK_ARCHITECTURE: "  "}), GRAVITON_TYPE),
        (_arch_stack(**{service.EDH_FIELD_STACK_PROFILE: None}), GRAVITON_TYPE),
        (_arch_stack(**{service.EDH_FIELD_STACK_PROFILE: "not a record"}), GRAVITON_TYPE),
        (_arch_stack(**{service.EDH_FIELD_STACK_PROFILE: _profile('{"arm64": ["m7g.large"')}), GRAVITON_TYPE),
        (_arch_stack(**{service.EDH_FIELD_STACK_PROFILE: _profile(17)}), GRAVITON_TYPE),
        (_arch_stack(**{service.EDH_FIELD_STACK_PROFILE: _profile(json.dumps([GRAVITON_TYPE]))}), GRAVITON_TYPE),
        (dict(CATALOG_STACK), "c6i.large"),
    ], ids=["no-architecture", "blank-architecture", "no-profile", "profile-not-a-record",
            "unparseable-allowed-types", "allowed-types-not-a-collection", "allowed-types-not-grouped",
            "type-in-no-group"])
    def test_an_unknown_side_leaves_the_decision_to_edh(self, monkeypatch, stack, instance_type):
        fakes = _launch_arch(monkeypatch, stack)

        response = _post(fakes, _body(instanceType=instance_type))

        assert response["statusCode"] == 202
        fakes.lambda_client.invoke.assert_called_once()


def _recipe_build(launch_types=None, stack_id="42"):
    """The store's answer naming one AVAILABLE desktop recipe build of the image; ``launch_types`` None
    leaves the recipe without a list."""
    recipe = {"recipeId": "isaacsim", "displayName": "Isaac Sim workstation", "kind": "vdi"}
    if launch_types is not None:
        recipe["launchInstanceTypes"] = launch_types
    build = {"recipeId": "isaacsim", "version": 3, "status": "AVAILABLE", "edhStackId": stack_id,
             "edhKind": "vdi"}
    return {stack_id: (recipe, build)}


def _launch_recipe(monkeypatch, *, answer=None, raises=None, enforce=None, builds_enabled=True):
    """The launch route over CATALOG_STACK, its recipe builds answered by a stub store, and a stub logger."""
    fakes = _launch_arch(monkeypatch, enforce=enforce)
    monkeypatch.setattr(service, "ami_builds_enabled", builds_enabled)
    fakes.logger = MagicMock()
    monkeypatch.setattr(service, "logger", fakes.logger)
    lookup = fakes.recipe_store.available_builds_by_stack_id
    if raises is not None:
        lookup.side_effect = raises
    else:
        lookup.return_value = _recipe_build(RECIPE_LAUNCH_TYPES) if answer is None else answer
    return fakes


@pytest.mark.unit
class TestLaunchRecipeInstanceTypes:
    """An image a recipe build registered launches only on the types its recipe lists, when it lists
    any; the recipe builds are the catalog's own read, and a read that fails refuses nothing."""

    def test_a_type_outside_the_recipes_list_is_a_400_before_any_write(self, monkeypatch):
        fakes = _launch_recipe(monkeypatch)

        response = _post(fakes, _body(instanceType="g5.xlarge"))

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == NOT_OFFERED_MESSAGE
        fakes.recipe_store.available_builds_by_stack_id.assert_called_once_with("vdi")
        fakes.table.put_item.assert_not_called()
        fakes.mint.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()

    @pytest.mark.parametrize("instance_type", RECIPE_LAUNCH_TYPES)
    def test_a_type_the_recipe_lists_launches(self, monkeypatch, instance_type):
        fakes = _launch_recipe(monkeypatch)

        response = _post(fakes, _body(instanceType=instance_type))

        assert response["statusCode"] == 202
        assert _payload(fakes)["instanceType"] == instance_type

    @pytest.mark.parametrize("answer", [
        _recipe_build(None), _recipe_build([]), _recipe_build(RECIPE_LAUNCH_TYPES, stack_id="77"), {},
    ], ids=["recipe-without-a-list", "recipe-with-an-empty-list", "another-images-recipe", "baseline-image"])
    def test_no_list_for_the_image_refuses_nothing(self, monkeypatch, answer):
        fakes = _launch_recipe(monkeypatch, answer=answer)

        response = _post(fakes, _body(instanceType="g5.xlarge"))

        assert response["statusCode"] == 202
        fakes.lambda_client.invoke.assert_called_once()

    @pytest.mark.parametrize("raises, named", [
        (ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}}, "Scan"),
         "AccessDeniedException"),
        (KeyError("Resource name parameter not found in SSM"), "KeyError"),
    ], ids=["table-unreadable", "table-name-unpublished"])
    def test_a_recipe_lookup_that_fails_refuses_nothing_and_warns(self, monkeypatch, raises, named):
        fakes = _launch_recipe(monkeypatch, raises=raises)

        response = _post(fakes, _body(instanceType="g5.xlarge"))

        assert response["statusCode"] == 202
        fakes.lambda_client.invoke.assert_called_once()
        fakes.logger.warning.assert_called_once()
        assert named in fakes.logger.warning.call_args.args[0]

    def test_with_ami_builds_off_the_recipe_table_is_not_read(self, monkeypatch):
        fakes = _launch_recipe(monkeypatch, builds_enabled=False)

        response = _post(fakes, _body(instanceType="g5.xlarge"))

        assert response["statusCode"] == 202
        fakes.recipe_store.available_builds_by_stack_id.assert_not_called()

    def test_tier2_is_answered_before_the_recipes_list(self, monkeypatch):
        fakes = _launch_recipe(monkeypatch, enforce=lambda obj, act: False)

        response = _post(fakes, _body(instanceType="g5.xlarge"))

        assert response["statusCode"] == 403
        fakes.enforcer.enforce.assert_called_once_with(
            {"object__type": "edhWorkstation", "edhImageId": "42", "edhOsFamily": "linux",
             "edhInstanceType": "g5.xlarge"},
            "POST",
        )
        fakes.recipe_store.available_builds_by_stack_id.assert_not_called()
        fakes.table.put_item.assert_not_called()

    def test_the_architecture_is_answered_before_the_recipes_list(self, monkeypatch):
        fakes = _launch_recipe(monkeypatch)

        response = _post(fakes, _body(instanceType=GRAVITON_TYPE))

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == ARCHITECTURE_MISMATCH_MESSAGE

    def test_the_recipes_list_is_answered_before_the_root_size_floor(self, monkeypatch):
        fakes = _launch_recipe(monkeypatch)

        response = _post(fakes, _body(instanceType="g5.xlarge", rootSizeGb=50))

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == NOT_OFFERED_MESSAGE

    def test_the_refusal_names_neither_the_type_nor_the_image_nor_the_list(self, monkeypatch):
        fakes = _launch_recipe(monkeypatch)

        response = _post(fakes, _body(instanceType="g5.xlarge"))

        assert response["statusCode"] == 400
        assert "g5.xlarge" not in response["body"] and "42" not in response["body"]
        assert not any(instance_type in response["body"] for instance_type in RECIPE_LAUNCH_TYPES)
        assert "isaacsim" not in response["body"]


@pytest.mark.unit
class TestLaunch:
    def test_a_launch_writes_the_creating_row_and_answers_202_with_it(self, monkeypatch):
        fakes = _launch(monkeypatch)

        response = _post(fakes)

        assert response["statusCode"] == 202
        # put_item received a copy of the row, so this Item is the CREATING snapshot (a MagicMock
        # stores the reference it was given).
        put = fakes.table.put_item.call_args[1]
        # The put is conditional on the key, so a repeated bootstrapId cannot overwrite the row.
        assert "bootstrapId" in put["ConditionExpression"]
        assert put["Item"]["bootstrapId"] == BOOTSTRAP_OWN
        assert put["Item"]["userId"] == USER
        assert put["Item"]["edhUid"] == edh_uid_for(USER)
        assert put["Item"]["state"] == "CREATING"
        assert put["Item"]["osFamily"] == "linux"
        assert put["Item"]["connectionAvailable"] is False
        assert "sessionId" not in put["Item"]
        assert "lastError" not in put["Item"]

        body = response_body(response)
        assert (body["bootstrapId"], body["sessionId"], body["state"], body["edhState"],
                body["connectionAvailable"], body["lastError"]) == (
            BOOTSTRAP_OWN, None, "CREATING", None, False, None)
        assert body["userId"] == USER
        fakes.table.update_item.assert_not_called()

    def test_the_launch_hands_the_row_to_the_worker_asynchronously(self, monkeypatch):
        fakes = _launch(monkeypatch)

        _post(fakes)

        fakes.lambda_client.invoke.assert_called_once()
        invoke = fakes.lambda_client.invoke.call_args[1]
        assert invoke["FunctionName"] == service.launch_worker_function_name
        assert invoke["InvocationType"] == "Event"
        assert _payload(fakes) == {
            "bootstrapId": BOOTSTRAP_OWN,
            "userId": USER,
            "edhUid": edh_uid_for(USER),
            "imageId": "42",
            "instanceType": "g5.xlarge",
            "osFamily": "linux",
            "rootSizeGb": 100,
            "name": "Design desk",
        }
        assert set(_payload(fakes)) == set(service.WORKER_PAYLOAD_KEYS)

    def test_the_launch_makes_one_catalog_read_and_no_edh_mutation(self, monkeypatch):
        """The API path reads the dcv catalog once, to resolve the image; the launch's EDH calls are
        the worker's."""
        fakes = _launch(monkeypatch)

        response = _post(fakes)

        assert response["statusCode"] == 202
        fakes.client_factory.assert_called_once_with(profile="api")
        assert fakes.edh.calls == [("list_software_stacks", ("dcv",))]
        records = edh_proxy_records(fakes)
        assert [(record["operation"], record["edhStatus"]) for record in records] == [("catalogImages", "ok")]
        assert (records[0]["edhMethod"], records[0]["edhRoute"]) == service.EDH_AUDIT_ROUTES["list_software_stacks"]

    def test_the_stored_family_is_the_images_when_the_body_agrees(self, monkeypatch):
        fakes = _launch(monkeypatch, edh=FakeEdhClient(list_software_stacks=[_stack("windows2022", None)]))

        response = _post(fakes, _body(osFamily="windows"))

        assert response["statusCode"] == 202
        assert fakes.table.put_item.call_args[1]["Item"]["osFamily"] == "windows"
        assert _payload(fakes)["osFamily"] == "windows"

    def test_the_row_is_written_before_the_worker_is_invoked(self, monkeypatch):
        fakes = _launch(monkeypatch)
        order = []
        fakes.table.put_item.side_effect = lambda **kw: order.append("put")
        fakes.lambda_client.invoke.side_effect = lambda **kw: order.append("invoke") or {"StatusCode": 202}

        response = _post(fakes)

        assert response["statusCode"] == 202
        assert order == ["put", "invoke"]

    def test_a_rest_shaped_event_is_served(self, monkeypatch):
        fakes = _launch(monkeypatch)

        response = _post(fakes, rest=True)

        assert response["statusCode"] == 202


@pytest.mark.unit
class TestIdempotency:
    def test_a_repeated_bootstrap_id_returns_the_existing_row_without_a_launch(self, monkeypatch):
        fakes = _launch(monkeypatch)
        fakes.table.put_item.side_effect = _duplicate_key_error()
        fakes.table.get_item.return_value = {"Item": own_row(state="ACTIVE")}

        response = _post(fakes)

        assert response["statusCode"] == 200
        body = response_body(response)
        assert (body["bootstrapId"], body["sessionId"], body["state"]) == (BOOTSTRAP_OWN, SESSION_OWN, "ACTIVE")
        # The put just proved the row exists: an eventually consistent read could miss it and refuse
        # the owner's own retry as a foreign bootstrapId.
        fakes.table.get_item.assert_called_once_with(Key={"bootstrapId": BOOTSTRAP_OWN}, ConsistentRead=True)
        fakes.lambda_client.invoke.assert_not_called()
        assert _catalog_reads(fakes) == ["list_software_stacks"]
        fakes.table.update_item.assert_not_called()

    def test_a_foreign_bootstrap_id_is_a_409_and_audited(self, monkeypatch):
        fakes = _launch(monkeypatch)
        fakes.table.put_item.side_effect = _duplicate_key_error()
        fakes.table.get_item.return_value = {"Item": other_row(bootstrapId=BOOTSTRAP_OWN)}

        response = _post(fakes)

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "bootstrapId is already in use"
        fakes.lambda_client.invoke.assert_not_called()
        assert _catalog_reads(fakes) == ["list_software_stacks"]
        fakes.log_authorization.assert_called_once()
        claims, authorized, custom_data = fakes.log_authorization.call_args.args
        assert authorized is False
        assert custom_data["obj"] == {"object__type": "edhWorkstation", "bootstrapId": BOOTSTRAP_OWN}
        assert custom_data["action"] == "POST"

    def test_another_conditional_failure_is_not_swallowed(self, monkeypatch):
        fakes = _launch(monkeypatch)
        fakes.table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow down"}}, "PutItem")

        response = _post(fakes)

        assert response["statusCode"] == 500
        fakes.lambda_client.invoke.assert_not_called()
        assert _catalog_reads(fakes) == ["list_software_stacks"]


@pytest.mark.unit
class TestWorkerHandover:
    def test_a_failed_handover_frees_the_row_and_is_a_500(self, monkeypatch):
        fakes = _launch(monkeypatch)
        fakes.lambda_client.invoke.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "no function"}}, "Invoke")

        response = _post(fakes)

        assert response["statusCode"] == 500
        fakes.table.delete_item.assert_called_once_with(Key={"bootstrapId": BOOTSTRAP_OWN})
        fakes.table.update_item.assert_not_called()
        assert _catalog_reads(fakes) == ["list_software_stacks"]

    def test_the_worker_function_name_is_the_environment_binding(self):
        assert service.launch_worker_function_name == os.environ["EDH_LAUNCH_WORKER_FUNCTION_NAME"]


def _set_policy(monkeypatch, **policy):
    """Stub read_lease_policy to return the given policy dict."""
    full_policy = {
        "enabled": policy.get("enabled", False),
        "leaseHours": policy.get("leaseHours", 8),
        "forced": policy.get("forced", False),
        "graceHours": policy.get("graceHours", 24),
        "updatedAt": None,
        "updatedBy": None,
    }
    monkeypatch.setattr(service, "read_lease_policy", lambda: full_policy)


@pytest.mark.unit
class TestLeasesAtLaunch:
    def test_forced_policy_stamps_admin_hours(self, monkeypatch):
        """Forced policy ignores the request and stamps the admin-configured hours."""
        fakes = _launch(monkeypatch)
        _set_policy(monkeypatch, enabled=True, leaseHours=8, forced=True, graceHours=24)

        response = _post(fakes, _body(leaseHours=3))

        assert response["statusCode"] == 202
        item = fakes.table.put_item.call_args[1]["Item"]
        assert item["leaseHoursAtLaunch"] == 8
        assert item["leaseExpiresAt"] is not None

    def test_optional_policy_honours_request(self, monkeypatch):
        """Optional policy honours the caller's requested hours."""
        fakes = _launch(monkeypatch)
        _set_policy(monkeypatch, enabled=True, leaseHours=8, forced=False, graceHours=24)

        response = _post(fakes, _body(leaseHours=3))

        assert response["statusCode"] == 202
        item = fakes.table.put_item.call_args[1]["Item"]
        assert item["leaseHoursAtLaunch"] == 3
        assert item["leaseExpiresAt"] is not None

    def test_optional_policy_no_request_stamps_no_lease(self, monkeypatch):
        """Optional policy with no request leaves no lease keys on the row."""
        fakes = _launch(monkeypatch)
        _set_policy(monkeypatch, enabled=True, leaseHours=8, forced=False, graceHours=24)

        response = _post(fakes)

        assert response["statusCode"] == 202
        item = fakes.table.put_item.call_args[1]["Item"]
        assert "leaseExpiresAt" not in item
        assert "leaseHoursAtLaunch" not in item

    def test_optional_policy_rejects_hours_over_the_cap(self, monkeypatch):
        """Optional policy rejects a request above the admin cap before writing anything."""
        fakes = _launch(monkeypatch)
        _set_policy(monkeypatch, enabled=True, leaseHours=8, forced=False, graceHours=24)

        response = _post(fakes, _body(leaseHours=9))

        assert response["statusCode"] == 400
        assert "leasehours" in response_body(response)["message"].lower()
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()

    def test_disabled_policy_with_request_is_400_and_no_write(self, monkeypatch):
        """Leases disabled with a request is rejected before writing anything."""
        fakes = _launch(monkeypatch)
        _set_policy(monkeypatch, enabled=False, leaseHours=8, forced=False, graceHours=24)

        response = _post(fakes, _body(leaseHours=3))

        assert response["statusCode"] == 400
        assert "disabled" in response_body(response)["message"].lower()
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()

    def test_disabled_policy_no_request_launches_normally(self, monkeypatch):
        """Leases disabled with no request launches normally (inert when off)."""
        fakes = _launch(monkeypatch)
        _set_policy(monkeypatch, enabled=False, leaseHours=8, forced=False, graceHours=24)

        response = _post(fakes)

        assert response["statusCode"] == 202
        item = fakes.table.put_item.call_args[1]["Item"]
        assert "leaseExpiresAt" not in item
        assert "leaseHoursAtLaunch" not in item
