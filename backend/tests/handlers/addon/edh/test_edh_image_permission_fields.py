# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Each ``edhImage`` field names one identifier, decided by the real policy generation and Casbin.

The image routes carry the stack id in ``edhImageId`` and the stack name in ``edhStackName``; the AMI
recipe routes carry the recipe id in ``edhRecipeId``; a field a route has no value for is ``""``. The
objects are the services' own ``_image_auth_object`` results, so a service that put an identifier in
another field fails here. The shipped templates and the seeded admin grant write ``edhImageId``
``contains`` ``.*``, which must still match the empty id the recipe and registration routes carry.

Only the two table reads are stubbed, as in ``tests/handlers/auth/test_permission_templates_addon_routes.py``.
"""

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module
from backend.backend.handlers.addon.edh import edhAdminService as admin_service
from backend.backend.handlers.addon.edh import edhAmiRecipeService as recipe_service
from backend.backend.handlers.addon.edh import edhWorkstationService as workstation_service
from backend.backend.handlers.auth import authConstraintsTemplateService as template_service

TEMPLATES_DIR = pathlib.Path(__file__).resolve().parents[5] / "documentation" / "permissionsTemplates"
ROLE_NAME = "edh-image-role"
USER_ID = "edh-image-user"
CLAIMS = {"tokens": [USER_ID]}

# One object per kind of edhImage route, as the services build it.
CATALOG_IMAGE = workstation_service._image_auth_object("42", "Team Desktop", "linux")
ADMIN_IMAGE = admin_service._image_auth_object("42", "Team Desktop", "linux")
REGISTRATION = admin_service._image_auth_object("", "Team Desktop", "linux")
RECIPE = recipe_service._image_auth_object("al2023-desktop", "linux")
ALL_OBJECTS = {"catalog image": CATALOG_IMAGE, "admin image": ADMIN_IMAGE, "registration": REGISTRATION,
               "recipe": RECIPE}


def _constraint(name, criteria, actions=("GET", "POST", "PUT", "DELETE")):
    stored = {
        "name": name,
        "description": name,
        "objectType": "edhImage",
        "criteriaAnd": criteria,
        "criteriaOr": [],
        "groupPermissions": [{"action": action, "type": "allow"} for action in actions],
    }
    return template_service.build_constraint_data(stored, ROLE_NAME, CLAIMS)


def _template_image_constraints(template_name):
    document = json.loads((TEMPLATES_DIR / template_name).read_text(encoding="utf-8"))
    constraints = [c for c in document["constraints"] if c["objectType"] == "edhImage"]
    assert constraints, f"{template_name} has no edhImage constraint"
    substituted = template_service.substitute_variables(constraints, {"ROLE_NAME": ROLE_NAME})
    return [template_service.build_constraint_data(c, ROLE_NAME, CLAIMS) for c in substituted]


@pytest.fixture
def enforcer_for():
    """Build the real CasbinEnforcer from stored constraints; the caches are cleared and restored."""
    state = {"constraints": []}
    previous_policy_map = authz_module.casbin_user_policy_map
    previous_enforcer_map = authz_module.casbin_user_enforcer_map

    def build(constraints):
        state["constraints"] = constraints
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}
        return authz_module.CasbinEnforcer({"tokens": [USER_ID], "roles": [ROLE_NAME], "mfaEnabled": True})

    with patch.object(
        authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
        return_value=[{"userId": USER_ID, "roleName": ROLE_NAME}],
    ), patch.object(
        authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
        side_effect=lambda role_names: state["constraints"],
    ), patch.object(authz_module, "log_authorization", MagicMock()):
        try:
            yield build
        finally:
            authz_module.casbin_user_policy_map = previous_policy_map
            authz_module.casbin_user_enforcer_map = previous_enforcer_map


def _allowed(enforcer, action="GET"):
    return {name for name, obj in ALL_OBJECTS.items() if enforcer.enforce(dict(obj), action)}


@pytest.mark.unit
class TestEachFieldNamesOneIdentifier:

    def test_the_objects_carry_each_identifier_in_its_own_field(self):
        assert (CATALOG_IMAGE["edhImageId"], CATALOG_IMAGE["edhStackName"], CATALOG_IMAGE["edhRecipeId"]) == (
            "42", "Team Desktop", "")
        assert ADMIN_IMAGE == CATALOG_IMAGE
        assert (REGISTRATION["edhImageId"], REGISTRATION["edhStackName"], REGISTRATION["edhRecipeId"]) == (
            "", "Team Desktop", "")
        assert (RECIPE["edhImageId"], RECIPE["edhStackName"], RECIPE["edhRecipeId"]) == ("", "", "al2023-desktop")

    def test_a_stack_id_constraint_admits_only_that_stack(self, enforcer_for):
        enforcer = enforcer_for([_constraint("ids", [
            {"field": "edhImageId", "operator": "equals", "value": "42"}])])
        assert _allowed(enforcer) == {"catalog image", "admin image"}

    def test_a_stack_name_constraint_admits_the_stack_and_its_registration(self, enforcer_for):
        enforcer = enforcer_for([_constraint("names", [
            {"field": "edhStackName", "operator": "equals", "value": "Team Desktop"}])])
        assert _allowed(enforcer, "GET") == {"catalog image", "admin image", "registration"}
        assert _allowed(enforcer, "POST") == {"catalog image", "admin image", "registration"}

    def test_a_recipe_id_constraint_admits_only_that_recipe(self, enforcer_for):
        enforcer = enforcer_for([_constraint("recipes", [
            {"field": "edhRecipeId", "operator": "equals", "value": "al2023-desktop"}])])
        assert _allowed(enforcer) == {"recipe"}

    def test_a_recipe_id_matching_a_stack_id_does_not_admit_the_stack(self, enforcer_for):
        # Before the split one field held both, so a recipe named "42" matched stack 42 too.
        enforcer = enforcer_for([_constraint("recipes", [
            {"field": "edhRecipeId", "operator": "equals", "value": "42"}])])
        assert _allowed(enforcer) == set()

    def test_an_unmatched_value_admits_nothing(self, enforcer_for):
        # Control: the enforcer is not allow-all.
        enforcer = enforcer_for([_constraint("names", [
            {"field": "edhStackName", "operator": "equals", "value": "Other Desktop"}])])
        assert _allowed(enforcer) == set()


@pytest.mark.unit
class TestTheWildcardGrantsKeepEveryRoute:

    def test_contains_wildcard_on_the_stack_id_matches_the_empty_id(self, enforcer_for):
        # The seeded admin constraint and both templates write this criterion.
        enforcer = enforcer_for([_constraint("all", [
            {"field": "edhImageId", "operator": "contains", "value": ".*"}])])
        for action in ("GET", "POST", "PUT", "DELETE"):
            assert _allowed(enforcer, action) == set(ALL_OBJECTS), action

    def test_the_admin_template_admits_every_image_and_recipe_route(self, enforcer_for):
        enforcer = enforcer_for(_template_image_constraints("edh-admin.json"))
        for action in ("GET", "POST", "PUT", "DELETE"):
            assert _allowed(enforcer, action) == set(ALL_OBJECTS), action

    def test_the_user_template_admits_reading_only(self, enforcer_for):
        enforcer = enforcer_for(_template_image_constraints("edh-user.json"))
        assert _allowed(enforcer, "GET") == set(ALL_OBJECTS)
        assert _allowed(enforcer, "POST") == set()
