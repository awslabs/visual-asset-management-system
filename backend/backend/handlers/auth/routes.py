# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Auth routes service handler for VAMS API.

Serves three endpoints:
  * ``POST /auth/routes`` -- checks a caller-supplied list of web routes
    against Casbin and returns the subset the user may access. This is the
    primary auth service the web frontend calls at login to filter pages and
    navigation, so it intentionally performs no Tier-1 (API-level) Casbin
    check on itself; each submitted route is still individually enforced.
  * ``GET /auth/routes/api`` -- returns the full list of VAMS API routes from
    the master route definitions (``common/apiRoutes.py``). Used by the web
    constraints editor to offer valid route values when authoring API
    constraints, and by the CLI.
  * ``GET /auth/routes/api/allowed`` -- returns the API routes (and the HTTP
    methods on each) that the requesting user is authorized to call, by
    feeding every route/method pair through the user's Casbin policy.
"""

import json
import os

from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.apiRoutes import API_AUTH_ROUTES, API_AUTH_ROUTES_API, API_AUTH_ROUTES_API_ALLOWED, get_public_api_routes
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from models.common import (
    validation_error_message,
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse
)
from models.authRoutes import (
    CheckWebRoutesRequestModel, CheckWebRoutesResponseModel, AllowedWebRouteModel,
    GetApiRoutesResponseModel, ApiRouteModel,
    GetAllowedApiRoutesResponseModel, AllowedApiRouteModel,
)

logger = safeLogger(service_name="Routes")

# Global variables for claims and roles
claims_and_roles = {}


def _use_local_mocks() -> bool:
    """True when local-mock mode is enabled (all route checks auto-approve)."""
    return os.environ.get('USE_LOCAL_MOCKS', '').lower() == 'true'


def check_web_routes(event):
    """Check a list of web routes against the user's Casbin policy.

    Returns the subset of submitted routes the user may access. Performs no
    Tier-1 API check by design (this endpoint is the primary auth service the
    frontend uses to verify other routes); each route is enforced individually.
    """
    body = event.get('body')
    if not body:
        return validation_error(body={'message': "Request body is required"}, event=event)

    if isinstance(body, str):
        body = json.loads(body)

    request_model = parse(body, model=CheckWebRoutesRequestModel)

    allowed_routes = []
    use_local_mocks = _use_local_mocks()
    casbin_enforcer = None
    if not use_local_mocks and len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)

    for route in request_model.routes:
        route_obj = {
            "method": route.method,
            "route__path": route.route__path,
            "object__type": "web",
        }
        if use_local_mocks:
            allowed_routes.append(AllowedWebRouteModel(**route_obj))
        elif casbin_enforcer and casbin_enforcer.enforce(route_obj, route.method):
            allowed_routes.append(AllowedWebRouteModel(**route_obj))

    response_model = CheckWebRoutesResponseModel(
        allowedRoutes=allowed_routes,
        email=claims_and_roles["tokens"][0] if claims_and_roles["tokens"] else "",
    )
    return success(body=response_model.dict())


def get_api_routes(event):
    """Return the full list of VAMS API routes from the master definitions."""
    routes = [
        ApiRouteModel(
            path=route.path,
            methods=list(route.methods),
            category=route.category,
            unauthenticated=route.unauthenticated,
        )
        for route in get_public_api_routes()
    ]
    response_model = GetApiRoutesResponseModel(routes=routes)
    return success(body=response_model.dict())


def get_allowed_api_routes(event):
    """Return the API routes and methods the requesting user may call.

    Every public route/method pair is fed through the user's Casbin policy
    (object__type ``api``, route__path = the route template). Routes served
    without the authorizer (``unauthenticated=True``) are always included.
    Routes with no allowed methods are omitted.
    """
    casbin_enforcer = CasbinEnforcer(claims_and_roles)
    allowed_routes = []

    for route in get_public_api_routes():
        if route.unauthenticated:
            allowed_methods = list(route.methods)
        else:
            request_object = {
                "object__type": "api",
                "route__path": route.path,
            }
            allowed_methods = [
                method for method in route.methods
                if casbin_enforcer.enforce(request_object, method)
            ]
        if allowed_methods:
            allowed_routes.append(
                AllowedApiRouteModel(
                    path=route.path,
                    methods=allowed_methods,
                    category=route.category,
                )
            )

    response_model = GetAllowedApiRoutesResponseModel(
        routes=allowed_routes,
        userId=claims_and_roles["tokens"][0] if claims_and_roles["tokens"] else "",
    )
    return success(body=response_model.dict())


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for auth routes APIs"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']

        if len(claims_and_roles["tokens"]) == 0 and not _use_local_mocks():
            return authorization_error()

        # POST /auth/routes intentionally has no Tier-1 API Casbin check on
        # itself (it is the primary auth service used to verify other routes).
        if method == 'POST' and API_AUTH_ROUTES.matches(path):
            return check_web_routes(event)

        # The API route listing endpoints enforce standard Tier-1 API authorization.
        if method == 'GET' and API_AUTH_ROUTES_API_ALLOWED.matches(path):
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
            return get_allowed_api_routes(event)

        if method == 'GET' and API_AUTH_ROUTES_API.matches(path):
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
            return get_api_routes(event)

        return validation_error(body={'message': "Invalid API path or method"}, event=event)

    except json.JSONDecodeError as e:
        logger.exception(f"Invalid JSON in request body: {e}")
        return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
