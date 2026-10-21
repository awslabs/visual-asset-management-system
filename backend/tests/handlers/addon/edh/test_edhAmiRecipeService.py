# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tier-1, dispatch, Tier-2 objects, the 404/409 rules and the response codes of edhAmiRecipeService."""
import json
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from backend.backend.handlers.addon.edh import edhAmiRecipeService as service
from backend.backend.handlers.addon.edh.common import edhAmiRecipeStore as store_mod
from backend.backend.handlers.addon.edh.common import edhAmiRecipes as recipes_mod
from backend.backend.handlers.addon.edh.common import edhClient as ec_mod
from backend.tests.handlers.addon.edh.edhAdminTestSupport import (
    ADMIN, ADMIN_CLAIMS, NO_IDENTITY_CLAIMS, SYSTEM_CLAIMS, make_event, response_body,
)
from backend.tests.handlers.addon.edh.edhAmiTestSupport import RECIPE_BODY, create_recipe_table, fake_context

RECIPES_PATH = "/addon/edh/admin/ami-recipes"
RECIPE = {**RECIPE_BODY, "source": "admin", "enabled": True, "contentHash": "h" * 64, "latestVersion": 1,
          "createdBy": ADMIN, "updatedBy": ADMIN, "createdAt": "2026-01-01T00:00:00+00:00", "updatedAt": "2026-01-01T00:00:00+00:00"}
BUILD = {"recipeId": "al2023-desktop", "version": 1, "status": "AVAILABLE", "amiId": "ami-0abc0abc0abc0abc0"}

# A Windows source and a Linux body, so the GET on the source is told apart from one built from the body.
SOURCE_RECIPE = {**RECIPE, "source": "config", "osFamily": "windows",
                 "assets": [{"id": "vamscli", "repoPath": "tools/VamsCLI"}],
                 "assetPrefix": "addon/edh/amiRecipes/aaaa/al2023-desktop/assets"}
COPY_BODY = {**RECIPE_BODY, "recipeId": "al2023-copy", "stackName": "VAMS AL2023 Desktop Copy",
             "duplicateOf": "al2023-desktop"}

# A Graviton type, which EC2 lists under arm64 only, and the refusal of it on an x86_64 recipe.
GRAVITON = "m7g.large"
BUILD_TYPE_MISMATCH = "buildInstanceType does not match the recipe's architecture"


def install(monkeypatch, *, claims=None, enforce=None, recipe=RECIPE, build=BUILD):
    claims = dict(ADMIN_CLAIMS if claims is None else claims)
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    enforcer.enforce.side_effect = enforce if enforce is not None else (lambda obj, act: True)
    store = MagicMock()
    store.get_recipe.return_value = recipe
    store.list_recipes.return_value = [recipe] if recipe else []
    store.get_build.return_value = build
    store.list_builds.return_value = [build] if build else []
    store.latest_build.return_value = build
    store.SOURCE_ADMIN, store.SOURCE_CONFIG = "admin", "config"
    lifecycle = MagicMock()
    lifecycle.RecipeConflict = recipes_mod.RecipeConflict
    lifecycle.RecipeInvalid = recipes_mod.RecipeInvalid
    lifecycle.OUTCOME_STARTED, lifecycle.OUTCOME_QUEUED = recipes_mod.OUTCOME_STARTED, recipes_mod.OUTCOME_QUEUED
    lifecycle.OUTCOME_REFUSED_DISABLED = recipes_mod.OUTCOME_REFUSED_DISABLED
    lifecycle.OUTCOME_IN_PROGRESS = recipes_mod.OUTCOME_IN_PROGRESS
    lifecycle.DUPLICATE_SOURCE_MISSING = recipes_mod.DUPLICATE_SOURCE_MISSING
    lifecycle.recipe_view.side_effect = lambda r: {**r, "latestBuild": build}
    lifecycle.create_admin_recipe.return_value = recipe or RECIPE
    lifecycle.update_admin_recipe.return_value = recipe or RECIPE
    lifecycle.start_build.return_value = ({**BUILD, "status": "BUILDING"}, recipes_mod.OUTCOME_STARTED)
    lifecycle.retry_registration.return_value = {**BUILD, "status": "AVAILABLE"}
    lifecycle.retire_build.return_value = {**BUILD, "status": "RETIRED"}
    lifecycle.delete_recipe.return_value = []
    monkeypatch.setattr(service, "request_to_claims", lambda event: dict(claims))
    monkeypatch.setattr(service, "CasbinEnforcer", MagicMock(return_value=enforcer))
    monkeypatch.setattr(service, "store", store)
    monkeypatch.setattr(service, "recipes", lifecycle)
    monkeypatch.setattr(service, "log_actions", MagicMock())
    return SimpleNamespace(enforcer=enforcer, store=store, recipes=lifecycle)


def recipe_path(recipe_id="al2023-desktop", *rest):
    return "/".join([RECIPES_PATH, recipe_id, *rest])


@pytest.mark.unit
class TestTierOne:
    def test_a_tier1_denial_is_403(self, monkeypatch):
        fakes = install(monkeypatch)
        fakes.enforcer.enforceAPI.return_value = False
        assert service.lambda_handler(make_event("GET", RECIPES_PATH), {})["statusCode"] == 403
        fakes.store.list_recipes.assert_not_called()

    def test_no_identity_and_the_system_user_are_denied(self, monkeypatch):
        for claims in (NO_IDENTITY_CLAIMS, SYSTEM_CLAIMS):
            install(monkeypatch, claims=claims)
            assert service.lambda_handler(make_event("GET", RECIPES_PATH), {})["statusCode"] == 403


@pytest.mark.unit
class TestRecipes:
    def test_list_filters_by_tier2_and_returns_views(self, monkeypatch):
        fakes = install(monkeypatch, enforce=lambda obj, act: obj["edhImageId"] == "al2023-desktop" and act == "GET")
        response = service.lambda_handler(make_event("GET", RECIPES_PATH), {})
        items = response_body(response)["Items"]
        assert response["statusCode"] == 200 and items[0]["latestBuild"] == BUILD
        fakes.enforcer.enforce.assert_called_with(
            {"object__type": "edhImage", "edhImageId": "al2023-desktop", "edhOsFamily": "linux"}, "GET")

    def test_create_answers_201_with_the_view(self, monkeypatch):
        fakes = install(monkeypatch, recipe=None)
        response = service.lambda_handler(make_event("POST", RECIPES_PATH, body=RECIPE_BODY), {})
        assert response["statusCode"] == 201
        model, user = fakes.recipes.create_admin_recipe.call_args.args
        assert model.recipeId == "al2023-desktop" and user == ADMIN
        # A create that duplicates nothing reads no source.
        assert fakes.recipes.create_admin_recipe.call_args.kwargs == {"original": None}
        fakes.store.get_recipe.assert_not_called()

    def test_create_with_an_invalid_body_is_400(self, monkeypatch):
        install(monkeypatch, recipe=None)
        response = service.lambda_handler(make_event("POST", RECIPES_PATH, body={**RECIPE_BODY, "kind": "nope"}), {})
        assert response["statusCode"] == 400

    def test_a_conflict_is_409(self, monkeypatch):
        fakes = install(monkeypatch)
        fakes.recipes.create_admin_recipe.side_effect = recipes_mod.RecipeConflict("recipeId is already in use")
        response = service.lambda_handler(make_event("POST", RECIPES_PATH, body=RECIPE_BODY), {})
        assert response["statusCode"] == 409 and "already in use" in response_body(response)["message"]

    def test_get_unknown_is_404_and_a_bad_id_is_400(self, monkeypatch):
        fakes = install(monkeypatch, recipe=None)
        assert service.lambda_handler(make_event("GET", recipe_path("nope"), path_params={"recipeId": "nope"}), {})["statusCode"] == 404
        assert service.lambda_handler(make_event("GET", recipe_path("Bad_Id"), path_params={"recipeId": "Bad_Id"}), {})["statusCode"] == 400

    def test_put_uses_the_put_action_and_the_path_id(self, monkeypatch):
        fakes = install(monkeypatch)
        response = service.lambda_handler(
            make_event("PUT", recipe_path(), body={**RECIPE_BODY, "recipeId": "ignored"}, path_params={"recipeId": "al2023-desktop"}), {})
        assert response["statusCode"] == 200
        fakes.enforcer.enforce.assert_called_with(
            {"object__type": "edhImage", "edhImageId": "al2023-desktop", "edhOsFamily": "linux"}, "PUT")
        existing, model, user = fakes.recipes.update_admin_recipe.call_args.args
        assert model.recipeId == "al2023-desktop"

    def test_put_that_changes_the_os_family_also_needs_put_on_the_new_family(self, monkeypatch):
        # A role constrained to Linux recipes cannot rewrite a Linux recipe into a Windows one.
        seen = []

        def linux_only(obj, act):
            seen.append((obj["edhOsFamily"], act))
            return obj["edhOsFamily"] == "linux"

        fakes = install(monkeypatch, enforce=linux_only)
        body = {**RECIPE_BODY, "osFamily": "windows", "baseOs": "windows2022"}
        response = service.lambda_handler(
            make_event("PUT", recipe_path(), body=body, path_params={"recipeId": "al2023-desktop"}), {})
        assert response["statusCode"] == 403
        assert ("windows", "PUT") in seen
        fakes.recipes.update_admin_recipe.assert_not_called()

    def test_put_that_changes_the_os_family_is_saved_when_both_families_are_allowed(self, monkeypatch):
        fakes = install(monkeypatch)
        body = {**RECIPE_BODY, "osFamily": "windows", "baseOs": "windows2022"}
        response = service.lambda_handler(
            make_event("PUT", recipe_path(), body=body, path_params={"recipeId": "al2023-desktop"}), {})
        assert response["statusCode"] == 200
        _existing, model, _user = fakes.recipes.update_admin_recipe.call_args.args
        assert model.osFamily == "windows"

    def test_delete_returns_the_warnings(self, monkeypatch):
        fakes = install(monkeypatch)
        fakes.recipes.delete_recipe.return_value = ["delete_component x: Dep"]
        response = service.lambda_handler(make_event("DELETE", recipe_path(), path_params={"recipeId": "al2023-desktop"}), {})
        assert response["statusCode"] == 200 and response_body(response) == {"recipeId": "al2023-desktop", "deleted": True, "warnings": ["delete_component x: Dep"]}


@pytest.mark.unit
class TestDuplicate:
    def test_a_duplicate_needs_post_on_the_new_id_and_get_on_the_source(self, monkeypatch):
        fakes = install(monkeypatch, recipe=SOURCE_RECIPE)

        response = service.lambda_handler(make_event("POST", RECIPES_PATH, body=COPY_BODY), {})

        assert response["statusCode"] == 201
        fakes.store.get_recipe.assert_called_once_with("al2023-desktop")
        assert [call.args for call in fakes.enforcer.enforce.call_args_list] == [
            ({"object__type": "edhImage", "edhImageId": "al2023-copy", "edhOsFamily": "linux"}, "POST"),
            ({"object__type": "edhImage", "edhImageId": "al2023-desktop", "edhOsFamily": "windows"}, "GET"),
        ]
        model, user = fakes.recipes.create_admin_recipe.call_args.args
        assert model.duplicateOf == "al2023-desktop" and user == ADMIN
        # The row authorized is the row copied.
        assert fakes.recipes.create_admin_recipe.call_args.kwargs == {"original": SOURCE_RECIPE}
        service.log_actions.assert_called_once_with(
            ANY, "edhAmiRecipe", {"operation": "create", "recipeId": "al2023-copy", "duplicatedFrom": "al2023-desktop"})

    def test_a_source_the_caller_may_not_read_is_403(self, monkeypatch):
        fakes = install(monkeypatch, recipe=SOURCE_RECIPE, enforce=lambda obj, act: act == "POST")

        response = service.lambda_handler(make_event("POST", RECIPES_PATH, body=COPY_BODY), {})

        assert response["statusCode"] == 403
        fakes.recipes.create_admin_recipe.assert_not_called()

    def test_the_source_is_authorized_under_its_own_os_family(self, monkeypatch):
        # A caller limited to Linux images cannot copy a Windows recipe by declaring the copy Linux.
        fakes = install(monkeypatch, recipe=SOURCE_RECIPE, enforce=lambda obj, act: obj["edhOsFamily"] == "linux")

        response = service.lambda_handler(make_event("POST", RECIPES_PATH, body=COPY_BODY), {})

        assert response["statusCode"] == 403
        fakes.recipes.create_admin_recipe.assert_not_called()

    def test_post_on_the_new_id_is_checked_before_the_source_is_read(self, monkeypatch):
        fakes = install(monkeypatch, recipe=SOURCE_RECIPE, enforce=lambda obj, act: act == "GET")

        response = service.lambda_handler(make_event("POST", RECIPES_PATH, body=COPY_BODY), {})

        assert response["statusCode"] == 403
        fakes.store.get_recipe.assert_not_called()
        fakes.recipes.create_admin_recipe.assert_not_called()

    def test_a_missing_source_is_409(self, monkeypatch):
        fakes = install(monkeypatch, recipe=None)

        response = service.lambda_handler(make_event("POST", RECIPES_PATH, body=COPY_BODY), {})

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "the recipe to duplicate does not exist"
        fakes.recipes.create_admin_recipe.assert_not_called()

    def test_an_invalid_source_id_is_400(self, monkeypatch):
        fakes = install(monkeypatch, recipe=SOURCE_RECIPE)

        response = service.lambda_handler(
            make_event("POST", RECIPES_PATH, body={**COPY_BODY, "duplicateOf": "Bad_Id"}), {})

        assert response["statusCode"] == 400
        fakes.store.get_recipe.assert_not_called()

    def test_a_lifecycle_refusal_of_the_duplicate_is_409(self, monkeypatch):
        fakes = install(monkeypatch, recipe=SOURCE_RECIPE)
        fakes.recipes.create_admin_recipe.side_effect = recipes_mod.RecipeConflict(
            "the assets of a duplicated recipe cannot be changed")

        response = service.lambda_handler(make_event("POST", RECIPES_PATH, body=COPY_BODY), {})

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "the assets of a duplicated recipe cannot be changed"


@pytest.mark.unit
class TestBuilds:
    def test_list_builds(self, monkeypatch):
        install(monkeypatch)
        response = service.lambda_handler(make_event("GET", recipe_path("al2023-desktop", "builds"), path_params={"recipeId": "al2023-desktop"}), {})
        assert response["statusCode"] == 200 and response_body(response)["Items"] == [BUILD]

    def test_start_build_answers_202_and_never_skips_unchanged_content(self, monkeypatch):
        fakes = install(monkeypatch)
        response = service.lambda_handler(make_event("POST", recipe_path("al2023-desktop", "builds"), path_params={"recipeId": "al2023-desktop"}), {})
        assert response["statusCode"] == 202 and response_body(response)["status"] == "BUILDING"
        assert fakes.recipes.start_build.call_args.kwargs == {"only_if_changed": False}
        assert fakes.recipes.start_build.call_args.args[1] == ADMIN

    def test_a_build_already_in_progress_answers_200_with_that_build(self, monkeypatch):
        fakes = install(monkeypatch)
        fakes.recipes.start_build.return_value = (
            {**BUILD, "version": 4, "status": "BUILDING"}, recipes_mod.OUTCOME_IN_PROGRESS)
        response = service.lambda_handler(make_event("POST", recipe_path("al2023-desktop", "builds"), path_params={"recipeId": "al2023-desktop"}), {})
        body = response_body(response)
        assert response["statusCode"] == 200
        assert body["outcome"] == recipes_mod.OUTCOME_IN_PROGRESS
        assert body["version"] == 4 and body["status"] == "BUILDING"

    def test_a_queued_build_answers_202(self, monkeypatch):
        fakes = install(monkeypatch)
        fakes.recipes.start_build.return_value = (
            {**BUILD, "version": 2, "status": "QUEUED"}, recipes_mod.OUTCOME_QUEUED)
        response = service.lambda_handler(make_event("POST", recipe_path("al2023-desktop", "builds"), path_params={"recipeId": "al2023-desktop"}), {})
        assert response["statusCode"] == 202 and response_body(response)["outcome"] == recipes_mod.OUTCOME_QUEUED

    def test_a_disabled_recipe_refuses_a_build_with_409(self, monkeypatch):
        fakes = install(monkeypatch)
        fakes.recipes.start_build.return_value = (None, recipes_mod.OUTCOME_REFUSED_DISABLED)
        response = service.lambda_handler(make_event("POST", recipe_path("al2023-desktop", "builds"), path_params={"recipeId": "al2023-desktop"}), {})
        assert response["statusCode"] == 409

    def test_register_and_retire_take_the_version_from_the_path(self, monkeypatch):
        fakes = install(monkeypatch)
        params = {"recipeId": "al2023-desktop", "version": "1"}
        assert service.lambda_handler(make_event("POST", recipe_path("al2023-desktop", "builds", "1", "register"), path_params=params), {})["statusCode"] == 200
        fakes.store.get_build.assert_called_with("al2023-desktop", 1)
        assert service.lambda_handler(make_event("DELETE", recipe_path("al2023-desktop", "builds", "1"), path_params=params), {})["statusCode"] == 200
        fakes.store.get_build.return_value = None
        assert service.lambda_handler(make_event("DELETE", recipe_path("al2023-desktop", "builds", "9"), path_params={"recipeId": "al2023-desktop", "version": "9"}), {})["statusCode"] == 404

    def test_a_retire_that_keeps_the_ami_answers_200_and_one_whose_deregistration_fails_500(self, monkeypatch):
        fakes = install(monkeypatch)
        event = make_event("DELETE", recipe_path("al2023-desktop", "builds", "1"),
                           path_params={"recipeId": "al2023-desktop", "version": "1"})
        kept = {**BUILD, "status": "RETIRED", "retiredAt": "t", "amiRemovalPending": True, "amiInUseBy": ["i-1"]}
        fakes.recipes.retire_build.return_value = kept
        response = service.lambda_handler(event, {})
        assert response["statusCode"] == 200 and response_body(response) == kept
        fakes.recipes.retire_build.side_effect = ClientError(
            {"Error": {"Code": "RequestLimitExceeded", "Message": "slow down"}}, "DeregisterImage")
        assert service.lambda_handler(event, {})["statusCode"] == 500

    @pytest.mark.parametrize("error, status", [
        (ec_mod.EdhUnavailableError("EDH returned 502 for DELETE /api/dcv/virtual_desktops/software_stacks"), 502),
        (ec_mod.EdhAuthError("EDH rejected the root key", status=401), 502),
        (ec_mod.EdhNotFoundError("Software Stack not found or already deactivated"), 404),
        (ec_mod.EdhApiError("EDH refused DELETE /api/dcv/virtual_desktops/software_stacks with 500"), 400),
    ], ids=["unavailable", "auth", "not_found", "refused"])
    def test_an_edh_error_on_a_recipe_route_keeps_its_status_and_curated_message(self, monkeypatch, error, status):
        # The recipe routes reach EDH (register, retire, delete); a typed EDH error answers as it does on
        # every other EDH route -- its own status and curated message -- not as a 500 internal error.
        fakes = install(monkeypatch)
        params = {"recipeId": "al2023-desktop", "version": "1"}
        fakes.recipes.retire_build.side_effect = error
        response = service.lambda_handler(make_event("DELETE", recipe_path("al2023-desktop", "builds", "1"),
                                                     path_params=params), {})
        assert response["statusCode"] == status
        assert response_body(response)["message"] == ec_mod.edh_error_response_parts(error)[1]
        fakes.recipes.retry_registration.side_effect = error
        response = service.lambda_handler(make_event("POST", recipe_path("al2023-desktop", "builds", "1", "register"),
                                                     path_params=params), {})
        assert response["statusCode"] == status

    def test_a_retire_whose_recipe_is_deleted_meanwhile_answers_409(self, monkeypatch):
        fakes = install(monkeypatch)
        fakes.recipes.retire_build.side_effect = recipes_mod.RecipeConflict(recipes_mod.RECIPE_DELETING)
        response = service.lambda_handler(make_event("DELETE", recipe_path("al2023-desktop", "builds", "1"),
                                                     path_params={"recipeId": "al2023-desktop", "version": "1"}), {})
        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "the recipe is being deleted"


@pytest.fixture
def real_lifecycle(monkeypatch):
    """The service over the real recipe lifecycle and a moto table, with recording AWS fakes whose
    DescribeInstanceTypes lists the Graviton type under arm64 only."""
    with mock_aws():
        create_recipe_table(monkeypatch)
        ctx = fake_context()
        ctx.ec2.describe_instance_types.return_value = {"InstanceTypes": [
            {"InstanceType": GRAVITON, "ProcessorInfo": {"SupportedArchitectures": ["arm64"]}}]}
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        enforcer.enforce.return_value = True
        monkeypatch.setattr(recipes_mod, "_ctx", lambda: ctx)
        monkeypatch.setattr(service, "request_to_claims", lambda event: dict(ADMIN_CLAIMS))
        monkeypatch.setattr(service, "CasbinEnforcer", MagicMock(return_value=enforcer))
        monkeypatch.setattr(service, "log_actions", MagicMock())
        yield ctx


@pytest.mark.unit
class TestArchitectureRefusals:
    """The lifecycle's architecture refusal reaches the caller as 400 with its own message, for a create,
    a duplicate and an update, and nothing is written or audited."""

    def test_a_create_is_400_and_writes_nothing(self, real_lifecycle):
        response = service.lambda_handler(
            make_event("POST", RECIPES_PATH, body={**RECIPE_BODY, "buildInstanceType": GRAVITON}), {})

        assert response["statusCode"] == 400
        assert response_body(response) == {"message": BUILD_TYPE_MISMATCH}
        assert store_mod.get_recipe("al2023-desktop") is None
        service.log_actions.assert_not_called()

    def test_a_duplicate_is_400_and_writes_nothing(self, real_lifecycle):
        assert service.lambda_handler(make_event("POST", RECIPES_PATH, body=RECIPE_BODY), {})["statusCode"] == 201
        service.log_actions.reset_mock()

        response = service.lambda_handler(
            make_event("POST", RECIPES_PATH, body={**COPY_BODY, "buildInstanceType": GRAVITON}), {})

        assert response["statusCode"] == 400
        assert response_body(response) == {"message": BUILD_TYPE_MISMATCH}
        assert store_mod.get_recipe("al2023-copy") is None
        service.log_actions.assert_not_called()

    def test_an_update_is_400_and_leaves_the_recipe_as_it_was(self, real_lifecycle):
        assert service.lambda_handler(make_event("POST", RECIPES_PATH, body=RECIPE_BODY), {})["statusCode"] == 201
        service.log_actions.reset_mock()

        response = service.lambda_handler(
            make_event("PUT", recipe_path(), body={**RECIPE_BODY, "buildInstanceType": GRAVITON},
                       path_params={"recipeId": "al2023-desktop"}), {})

        assert response["statusCode"] == 400
        assert response_body(response) == {"message": BUILD_TYPE_MISMATCH}
        assert store_mod.get_recipe("al2023-desktop")["buildInstanceType"] == "m7i.large"
        service.log_actions.assert_not_called()


@pytest.mark.unit
class TestDeleteRefusals:
    """A delete the lifecycle refuses reaches the caller as 409 with its own message, and the recipe and
    its builds stay."""

    def test_a_delete_while_a_builds_ami_removal_is_pending_is_409_and_keeps_the_recipe(self, real_lifecycle):
        assert service.lambda_handler(make_event("POST", RECIPES_PATH, body=RECIPE_BODY), {})["statusCode"] == 201
        store_mod.put_build({"recipeId": "al2023-desktop", "version": 1, "status": "RETIRED",
                             "amiId": "ami-0abc0abc0abc0abc0", "amiRemovalPending": True})
        service.log_actions.reset_mock()

        response = service.lambda_handler(
            make_event("DELETE", recipe_path(), path_params={"recipeId": "al2023-desktop"}), {})

        assert response["statusCode"] == 409
        assert response_body(response) == {"message": "a build's AMI removal is pending"}
        assert store_mod.get_recipe("al2023-desktop") is not None
        assert [b["version"] for b in store_mod.list_builds("al2023-desktop")] == [1]
        real_lifecycle.ib.delete_image_pipeline.assert_not_called()
        service.log_actions.assert_not_called()
