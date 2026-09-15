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

Both route checks evaluate many route/method pairs within one request, so the
authorization-denial audit records they produce are collected and written to
CloudWatch in batches (one event per denial) rather than one write per denial.
"""

import json
import os

from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.apiRoutes import API_AUTH_ROUTES, API_AUTH_ROUTES_API, API_AUTH_ROUTES_API_ALLOWED, get_public_api_routes
from common.resourceNames import ResourceKeys, get_log_group_name
from customLogging import auditLogging
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

# Path segment substituted for each ``{param}`` of a route template when the template is
# probed against a user's policy. ':' is outside the VAMS identifier character set, so the
# token cannot collide with a real resource id.
API_ROUTE_PROBE_SEGMENT = "vams:any"

# CloudWatch PutLogEvents accepts at most 10,000 events and 1,048,576 bytes per call,
# counting 26 bytes of overhead per event, and rejects the whole batch past either limit.
AUDIT_BATCH_MAX_EVENTS = 10000
AUDIT_BATCH_MAX_BYTES = 1048576
AUDIT_EVENT_OVERHEAD_BYTES = 26


def _use_local_mocks() -> bool:
    """True when local-mock mode is enabled (all route checks auto-approve).

    Local-mock mode belongs to the local development server (``localDev_api_server.py``),
    which serves the frontend without a deployment. It is refused wherever
    ``VAMS_RESOURCE_PARAM_PREFIX`` is set -- that is the SSM prefix a deployed handler
    resolves its resource names from, so its presence marks a real deployment and route
    checks are enforced there however the switch was set.
    """
    if os.environ.get('USE_LOCAL_MOCKS', '').lower() != 'true':
        return False
    if os.environ.get('VAMS_RESOURCE_PARAM_PREFIX'):
        logger.error(
            "USE_LOCAL_MOCKS is set alongside VAMS_RESOURCE_PARAM_PREFIX, which is only set on a "
            "deployment; local-mock mode stays disabled and route checks are enforced normally"
        )
        return False
    return True


def _route_probe_path(path_template: str) -> str:
    """Return the concrete request path a route template is probed with.

    ``{param}`` and ``{param+}`` segments are replaced with a single placeholder segment,
    so the ``route__path`` handed to Casbin has the shape of the concrete request path the
    API authorization check evaluates on a real call.
    """
    if "{" not in path_template:
        return path_template
    return "/".join(
        API_ROUTE_PROBE_SEGMENT if segment.startswith("{") and segment.endswith("}") else segment
        for segment in path_template.split("/")
    )


class _DenialAuditBatch:
    """Runs the Casbin checks of a bulk route check and batches the denial audit records.

    ``CasbinEnforcer.enforce`` writes one CloudWatch audit record per denial, so evaluating
    every route/method pair costs one synchronous write per denied pair. Checks run here
    against the enforcer's service object and each denial is kept; :meth:`flush` writes them
    through ``_write_batch_to_cloudwatch`` -- the same records, in the same authorization log
    group, one event per denial.
    """

    def __init__(self, claims, casbin_enforcer):
        tokens = claims.get("tokens") or []
        self._claims = claims
        self._user = tokens[0] if tokens else "UNKNOWN"
        self._casbin_enforcer = casbin_enforcer
        self._denials = []

    def enforce(self, obj, act):
        """Return the Casbin verdict for ``obj``/``act``, keeping a denial for the batch."""
        allowed = self._casbin_enforcer.service_object.enforce(obj, act)
        if not allowed:
            self._denials.append((dict(obj), act))
        return allowed

    def _denial_record(self, obj, act):
        """One denial record, in the format ``log_authorization`` writes."""
        return " ".join([
            "[AUTHORIZATION][authorized: False]",
            f"[user: {self._user}]",
            f"[roles: {json.dumps(self._claims.get('roles', []))}]",
            f"[mfaEnabled: {self._claims.get('mfaEnabled', False)}]",
            json.dumps({"action": act, "obj": obj}),
        ])

    def flush(self):
        """Write every kept denial, chunked to stay inside the PutLogEvents batch limits."""
        denials, self._denials = self._denials, []
        if not denials:
            return
        try:
            log_group_name = get_log_group_name(ResourceKeys.AUDIT_LOG_AUTHORIZATION)
            audit_event = {
                'requestContext': {'authorizer': {'jwt': {'claims': {'sub': self._user}}}}
            }
            # The writer appends the audit event echo to every entry, so it counts against
            # the batch byte budget once per event.
            event_overhead = AUDIT_EVENT_OVERHEAD_BYTES + len(
                f" --- [event: {json.dumps(audit_event)}]".encode('utf-8')
            )
            batch = []
            batch_bytes = 0
            for obj, act in denials:
                record = self._denial_record(obj, act)
                record_bytes = len(record.encode('utf-8')) + event_overhead
                if batch and (len(batch) >= AUDIT_BATCH_MAX_EVENTS
                              or batch_bytes + record_bytes > AUDIT_BATCH_MAX_BYTES):
                    auditLogging._write_batch_to_cloudwatch(log_group_name, batch, audit_event)
                    batch = []
                    batch_bytes = 0
                batch.append(record)
                batch_bytes += record_bytes
            if batch:
                auditLogging._write_batch_to_cloudwatch(log_group_name, batch, audit_event)
        except Exception as e:
            logger.exception(f"Failed to write authorization denial audit records: {e}")


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
    denial_audit = None
    if not use_local_mocks and len(claims_and_roles["tokens"]) > 0:
        denial_audit = _DenialAuditBatch(claims_and_roles, CasbinEnforcer(claims_and_roles))

    try:
        for route in request_model.routes:
            route_obj = {
                "method": route.method,
                "route__path": route.route__path,
                "object__type": "web",
            }
            if use_local_mocks:
                allowed_routes.append(AllowedWebRouteModel(**route_obj))
            elif denial_audit and denial_audit.enforce(route_obj, route.method):
                allowed_routes.append(AllowedWebRouteModel(**route_obj))
    finally:
        if denial_audit is not None:
            denial_audit.flush()

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
    (object__type ``api``, route__path = the route's probe path, which is the
    template with each ``{param}`` segment replaced -- the vocabulary the API
    authorization check uses on a real request). Routes served without the
    authorizer (``unauthenticated=True``) are always included. Routes with no
    allowed methods are omitted. The ``path`` returned is the route template.
    """
    denial_audit = _DenialAuditBatch(claims_and_roles, CasbinEnforcer(claims_and_roles))
    allowed_routes = []

    try:
        for route in get_public_api_routes():
            if route.unauthenticated:
                allowed_methods = list(route.methods)
            else:
                request_object = {
                    "object__type": "api",
                    "route__path": _route_probe_path(route.path),
                }
                allowed_methods = [
                    method for method in route.methods
                    if denial_audit.enforce(request_object, method)
                ]
            if allowed_methods:
                allowed_routes.append(
                    AllowedApiRouteModel(
                        path=route.path,
                        methods=allowed_methods,
                        category=route.category,
                    )
                )
    finally:
        denial_audit.flush()

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
