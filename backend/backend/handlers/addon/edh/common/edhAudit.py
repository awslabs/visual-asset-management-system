# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The action record of an EDH add-on API route.

An EDH route's record is the ``edhProxy`` record of each call it makes to the EDH service: inside the
request's audit context that record is named as the route's registered action. A route that makes no
EDH call (a listing served from the add-on's own table, a catalog read) records its action itself, so
every successful request has a record and none has two.
"""

import functools

from customLogging.auditLogging import current_audit_context, log_action

# The path parameters an EDH record carries as its entity id, in the order they are looked up.
_ENTITY_PATH_PARAMETERS = ("sessionId", "instanceId", "imageId", "recipeId", "version", "jobId",
                           "capacityReservationId")


def _status(response):
    status = response.get("statusCode") if isinstance(response, dict) else getattr(response, "statusCode", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def record_route_action(event, response) -> None:
    """Record the route's action after a successful response when the request recorded none."""
    ctx = current_audit_context()
    if ctx is None or ctx.route_spec is None:
        return
    status = _status(response)
    if status is None or status >= 400 or ctx.route_spec.action in ctx.recorded_actions:
        return
    path_parameters = event.get("pathParameters") if isinstance(event, dict) else None
    path_parameters = path_parameters if isinstance(path_parameters, dict) else {}
    entity_id = next((path_parameters[name] for name in _ENTITY_PATH_PARAMETERS if path_parameters.get(name)),
                     None)
    query = event.get("queryStringParameters") if isinstance(event, dict) else None
    log_action(event, ctx.route_spec.action, entity_id=entity_id,
               request_parameters=dict(query) if isinstance(query, dict) and query else None)


def records_route_action(handler):
    """Decorate an EDH API entrypoint (under ``audited_handler``) to call :func:`record_route_action`."""

    @functools.wraps(handler)
    def wrapper(event, context=None, *args, **kwargs):
        response = handler(event, context, *args, **kwargs)
        record_route_action(event, response)
        return response

    return wrapper
