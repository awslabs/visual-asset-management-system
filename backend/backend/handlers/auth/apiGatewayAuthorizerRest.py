# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""REST API (v1) REQUEST Lambda authorizer.

Reuses the shared authorizer core (Cognito / External OAuth / API-key / IP / ignored
paths). Differs from the HTTP authorizer only in I/O: REST input (methodArn, identity)
and IAM-policy output. An authenticated Allow uses a wildcard Resource scoped to the
API+stage so a cached Allow applies to every method (authorization caching correctness);
an ignored-path Allow establishes no identity and is scoped to the ignored paths.
"""
from aws_lambda_powertools import Logger
from common.auth.apiEvent import normalize_event
from common.auth.authorizerCore import authenticate_request, API_FRONTED, IGNORED_PATHS
from common.auth.clientIp import resolve_client_ip
from customLogging.auditLogging import log_authorization_gateway

logger = Logger()


def _api_stage_prefix(method_arn: str) -> str:
    # arn:partition:execute-api:region:acct:apiId/stage/VERB/resourcepath...
    head, _, tail = method_arn.partition(":execute-api:")
    # tail = "region:acct:apiId/stage/VERB/path..."
    prefix = tail.split("/")
    api_part = prefix[0]                      # "region:acct:apiId"
    stage = prefix[1] if len(prefix) > 1 else "*"
    return f"{head}:execute-api:{api_part}/{stage}"


def _ignored_path_resources(method_arn: str) -> list:
    """Resources for an ignored-path Allow: the ignored paths, not the whole stage.

    The bypass authenticates nobody and returns no context, so its Allow must not carry
    the API+stage wildcard. It is scoped to every configured ignored path rather than to
    the one requested because the anonymous routes share a single authorizer whose cache
    key is the source IP alone (identitySource context.identity.sourceIp): the cached
    policy is replayed for whichever ignored path the same caller asks for next, so a
    policy naming only the requested path would 403 its sibling for the rest of the TTL.
    Staying independent of the requested path is what keeps the policy cacheable. The
    method segment is "*" because ignored paths are configured without a verb.
    """
    prefix = _api_stage_prefix(method_arn)
    resources = [f"{prefix}/*/{path.lstrip('/')}" for path in IGNORED_PATHS if path]
    # No configured ignored paths means the bypass is unreachable; scope to the request.
    return resources or [method_arn]


def _wildcard_resource(method_arn: str) -> str:
    # A SINGLE trailing "*" after the stage — it matches the HTTP method AND the full resource path
    # including "/" separators. A "*/*" form only matches {METHOD}/{single-segment}, so a policy
    # cached (authorizerResultTtlInSeconds, keyed on the Authorization header) from a one-segment
    # path like POST/uploads would NOT match a multi-segment path like POST/uploads/{id}/complete or
    # GET/database/{db}/assets/{a}/... — API Gateway then 403s the reused-cache request without
    # re-invoking the authorizer. The single-star wildcard covers every method + path for the API+stage.
    return f"{_api_stage_prefix(method_arn)}/*"


def _policy(principal_id: str, effect: str, resource, context: dict = None) -> dict:
    # resource is a single ARN or a list of ARNs (IAM accepts either form).
    out = {
        "principalId": principal_id or "user",
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{
                "Action": "execute-api:Invoke",
                "Effect": effect,
                "Resource": resource,
            }],
        },
    }
    if context:
        # REST authorizer context: only string/number/bool values are passed through.
        out["context"] = {k: str(v) for k, v in context.items() if v is not None}
    return out


def _set_audit_client_ip(event) -> None:
    """Put the caller IP where the authorization audit writer reads it.

    The writer takes the caller IP from the v2-style ``requestContext.http.sourceIp``; a
    REST authorizer event carries no ``requestContext.http`` block and holds the TCP peer
    at ``requestContext.identity.sourceIp``, so the record would name no caller at all.
    ``normalize_event`` injects the block from the REST shape, and the value is then the
    address ``resolve_client_ip`` resolved — the same address the IP allow-list decision
    was made on, so a denial reason and the IP it refers to always agree.

    Trust follows resolve_client_ip exactly: forwarding headers count only on a CloudFront
    deployment, where the record names the viewer rather than the edge; on ALB and direct
    execute-api deployments every header is caller-controlled and the unforgeable TCP peer
    is used. ``requestContext.identity.sourceIp`` is left untouched, so the immediate peer
    stays visible in the event echo the audit entry carries.

    Best-effort: a failure here degrades the audit record rather than the authorization
    outcome, which this authorizer returns for every API route.
    """
    try:
        normalize_event(event)
        client_ip = resolve_client_ip(event, fronted=API_FRONTED)
        if not client_ip:
            return
        request_context = event.get("requestContext")
        if not isinstance(request_context, dict):
            return
        http = request_context.get("http")
        if not isinstance(http, dict):
            http = {}
            request_context["http"] = http
        http["sourceIp"] = client_ip
    except Exception as e:
        logger.warning(f"Could not resolve client IP for the authorization audit record: {str(e)}")


def lambda_handler(event, context):
    method_arn = event.get("methodArn", "*")
    resource = _wildcard_resource(method_arn) if method_arn != "*" else "*"
    try:
        res = authenticate_request(event)
        # After authenticate_request so nothing the auth decision reads is touched first.
        _set_audit_client_ip(event)
        log_authorization_gateway(event, res["authorized"], res.get("reason"))
        if not res["authorized"]:
            return _policy("user", "Deny", resource)
        if res.get("ignoredPath"):
            ignored_resource = (
                _ignored_path_resources(method_arn) if method_arn != "*" else "*"
            )
            return _policy("user", "Allow", ignored_resource)
        ctx = res.get("context") or {}
        principal = ctx.get("sub") or ctx.get("cognito:username") or "user"
        return _policy(principal, "Allow", resource, ctx)
    except Exception as e:
        logger.error(f"Authorizer error: {str(e)}")
        return _policy("user", "Deny", resource)
