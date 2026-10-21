# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Backs the ``/addon/edh/admin/ami-recipes*`` routes: recipes an administrator defines, and the
builds of every recipe. Tier-2 is enforced on the ``edhImage`` object type keyed by the recipe id."""

import json
import re

from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError

from common.apiRoutes import (
    API_EDH_ADMIN_AMI_RECIPE, API_EDH_ADMIN_AMI_RECIPE_BUILD, API_EDH_ADMIN_AMI_RECIPE_BUILD_REGISTER,
    API_EDH_ADMIN_AMI_RECIPE_BUILDS, API_EDH_ADMIN_AMI_RECIPES,
)
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_actions
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse, validation_error_message,
)
from models.addon.edh import AmiRecipeRequestModel, edh_recipe_id_pattern
from .common.edhIdentity import SYSTEM_USER_ID
from .common import edhAmiRecipes as recipes
from .common import edhAmiRecipeStore as store

logger = safeLogger(service_name="EdhAmiRecipeService")

claims_and_roles = {}

IMAGE_OBJECT_TYPE = "edhImage"
RECIPE_ID_PATTERN = re.compile(edh_recipe_id_pattern)
VERSION_PATTERN = re.compile(r'^[0-9]{1,6}$')


def _requesting_user_id():
    return claims_and_roles['tokens'][0] if claims_and_roles.get('tokens') else None


def _enforcer():
    return CasbinEnforcer(claims_and_roles)


def _image_auth_object(recipe_id, os_family):
    return {
        "object__type": IMAGE_OBJECT_TYPE,
        "edhImageId": str(recipe_id if recipe_id is not None else ""),
        "edhOsFamily": str(os_family if os_family is not None else ""),
    }


def _allowed(recipe, action):
    return len(claims_and_roles.get("tokens", [])) > 0 and _enforcer().enforce(
        _image_auth_object(recipe["recipeId"], recipe.get("osFamily")), action)


def _parse_body(event):
    try:
        body = json.loads(event.get('body') or '{}')
    except ValueError:
        raise VAMSGeneralErrorResponse("Request body must be a JSON object")
    if not isinstance(body, dict):
        raise VAMSGeneralErrorResponse("Request body must be a JSON object")
    return body


def _path_recipe_id(event):
    value = (event.get('pathParameters') or {}).get('recipeId')
    return str(value) if value is not None and RECIPE_ID_PATTERN.fullmatch(str(value)) else None


def _path_version(event):
    value = (event.get('pathParameters') or {}).get('version')
    return int(value) if value is not None and VERSION_PATTERN.fullmatch(str(value)) else None


def _conflict(event, err):
    return general_error(status_code=409, body={'message': str(err)}, event=event)


def _recipe_or_error(event, action):
    """(recipe, None) when the path names a recipe the caller may act on; (None, response) otherwise."""
    recipe_id = _path_recipe_id(event)
    if recipe_id is None:
        return None, validation_error(body={'message': 'recipeId is invalid'}, event=event)
    recipe = store.get_recipe(recipe_id)
    if recipe is None:
        return None, general_error(status_code=404, body={'message': 'Recipe not found'}, event=event)
    if not _allowed(recipe, action):
        return None, authorization_error()
    return recipe, None


def _build_or_error(event, recipe):
    version = _path_version(event)
    if version is None:
        return None, validation_error(body={'message': 'version is invalid'}, event=event)
    build = store.get_build(recipe["recipeId"], version)
    if build is None:
        return None, general_error(status_code=404, body={'message': 'Build not found'}, event=event)
    return build, None


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------
def list_recipes(event):
    items = [recipes.recipe_view(r) for r in store.list_recipes() if _allowed(r, "GET")]
    return success(body={'Items': items})


def create_recipe(event):
    model = parse(event=_parse_body(event), model=AmiRecipeRequestModel)
    if not _allowed({"recipeId": model.recipeId, "osFamily": model.osFamily}, "POST"):
        return authorization_error()
    # A duplicate copies its source's bundled files, so the caller must also be able to read the source.
    original = None
    if model.duplicateOf:
        original = store.get_recipe(model.duplicateOf)
        if original is None:
            return _conflict(event, recipes.DUPLICATE_SOURCE_MISSING)
        if not _allowed(original, "GET"):
            return authorization_error()
    try:
        created = recipes.create_admin_recipe(model, _requesting_user_id(), original=original)
    except recipes.RecipeConflict as err:
        return _conflict(event, err)
    except recipes.RecipeInvalid as err:
        return validation_error(body={'message': str(err)}, event=event)
    audit = {"operation": "create", "recipeId": model.recipeId}
    if model.duplicateOf:
        audit["duplicatedFrom"] = model.duplicateOf
    log_actions(event, "edhAmiRecipe", audit)
    return success(status_code=201, body=recipes.recipe_view(created))


def get_recipe(event):
    recipe, error = _recipe_or_error(event, "GET")
    return error or success(body=recipes.recipe_view(recipe))


def update_recipe(event):
    recipe, error = _recipe_or_error(event, "PUT")
    if error is not None:
        return error
    body = _parse_body(event)
    body["recipeId"] = recipe["recipeId"]
    model = parse(event=body, model=AmiRecipeRequestModel)
    try:
        updated = recipes.update_admin_recipe(recipe, model, _requesting_user_id())
    except recipes.RecipeConflict as err:
        return _conflict(event, err)
    except recipes.RecipeInvalid as err:
        return validation_error(body={'message': str(err)}, event=event)
    log_actions(event, "edhAmiRecipe", {"operation": "update", "recipeId": recipe["recipeId"]})
    return success(body=recipes.recipe_view(updated))


def delete_recipe(event):
    recipe, error = _recipe_or_error(event, "DELETE")
    if error is not None:
        return error
    if recipe.get("source") == store.SOURCE_CONFIG:
        return _conflict(event, "a config recipe is removed from the configuration, not here")
    try:
        warnings = recipes.delete_recipe(recipe)
    except recipes.RecipeConflict as err:
        return _conflict(event, err)
    log_actions(event, "edhAmiRecipe", {"operation": "delete", "recipeId": recipe["recipeId"]})
    return success(body={"recipeId": recipe["recipeId"], "deleted": True, "warnings": warnings})


# ---------------------------------------------------------------------------
# Builds
# ---------------------------------------------------------------------------
def list_builds(event):
    recipe, error = _recipe_or_error(event, "GET")
    return error or success(body={'Items': store.list_builds(recipe["recipeId"])})


def start_build(event):
    recipe, error = _recipe_or_error(event, "POST")
    if error is not None:
        return error
    try:
        build, outcome = recipes.start_build(recipe, _requesting_user_id(), only_if_changed=False)
    except recipes.RecipeConflict as err:
        return _conflict(event, err)
    if build is None:
        return _conflict(event, "the recipe is disabled")
    log_actions(event, "edhAmiRecipe", {"operation": "build", "recipeId": recipe["recipeId"],
                                        "version": build["version"], "outcome": outcome})
    # A build already in progress for this content is answered as it stands; nothing new was accepted.
    status_code = 200 if outcome == recipes.OUTCOME_IN_PROGRESS else 202
    return success(status_code=status_code, body={**build, "outcome": outcome})


def register_build(event):
    recipe, error = _recipe_or_error(event, "POST")
    if error is not None:
        return error
    build, error = _build_or_error(event, recipe)
    if error is not None:
        return error
    try:
        registered = recipes.retry_registration(recipe, build)
    except recipes.RecipeConflict as err:
        return _conflict(event, err)
    log_actions(event, "edhAmiRecipe", {"operation": "register", "recipeId": recipe["recipeId"],
                                        "version": build["version"]})
    return success(body=registered)


def retire_build(event):
    recipe, error = _recipe_or_error(event, "DELETE")
    if error is not None:
        return error
    build, error = _build_or_error(event, recipe)
    if error is not None:
        return error
    try:
        retired = recipes.retire_build(recipe, build)
    except recipes.RecipeConflict as err:
        return _conflict(event, err)
    log_actions(event, "edhAmiRecipe", {"operation": "retire", "recipeId": recipe["recipeId"], "version": build["version"]})
    return success(body=retired)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def handle_get(event, path):
    if API_EDH_ADMIN_AMI_RECIPES.matches(path):
        return list_recipes(event)
    if API_EDH_ADMIN_AMI_RECIPE_BUILDS.matches(path):
        return list_builds(event)
    if API_EDH_ADMIN_AMI_RECIPE.matches(path):
        return get_recipe(event)
    return validation_error(body={'message': 'Unsupported route'}, event=event)


def handle_post(event, path):
    if API_EDH_ADMIN_AMI_RECIPES.matches(path):
        return create_recipe(event)
    if API_EDH_ADMIN_AMI_RECIPE_BUILD_REGISTER.matches(path):
        return register_build(event)
    if API_EDH_ADMIN_AMI_RECIPE_BUILDS.matches(path):
        return start_build(event)
    return validation_error(body={'message': 'Unsupported route'}, event=event)


def handle_put(event, path):
    if API_EDH_ADMIN_AMI_RECIPE.matches(path):
        return update_recipe(event)
    return validation_error(body={'message': 'Unsupported route'}, event=event)


def handle_delete(event, path):
    if API_EDH_ADMIN_AMI_RECIPE_BUILD.matches(path):
        return retire_build(event)
    if API_EDH_ADMIN_AMI_RECIPE.matches(path):
        return delete_recipe(event)
    return validation_error(body={'message': 'Unsupported route'}, event=event)


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    try:
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            if CasbinEnforcer(claims_and_roles).enforceAPI(event):
                method_allowed_on_api = True
        if not method_allowed_on_api:
            return authorization_error()
        if claims_and_roles["tokens"][0] == SYSTEM_USER_ID:
            return authorization_error()

        if method == 'GET':
            return handle_get(event, path)
        if method == 'POST':
            return handle_post(event, path)
        if method == 'PUT':
            return handle_put(event, path)
        if method == 'DELETE':
            return handle_delete(event, path)
        return validation_error(body={'message': "Method not allowed"}, event=event)
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception("VAMSGeneralErrorResponse")
        return validation_error(body={'message': str(v)}, event=event)
    except Exception:
        logger.exception("Internal error")
        return internal_error()
