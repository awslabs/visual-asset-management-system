# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""REST API (v1) REQUEST Lambda authorizer.

Reuses the shared authorizer core (Cognito / External OAuth / API-key / IP / ignored
paths). Differs from the HTTP authorizer only in I/O: REST input (methodArn, identity)
and IAM-policy output. The policy Resource is a wildcard scoped to the API+stage so a
cached Allow applies to every method (authorization caching correctness).
"""
from aws_lambda_powertools import Logger
from common.auth.authorizerCore import authenticate_request
from customLogging.auditLogging import log_authorization_gateway

logger = Logger()


def _wildcard_resource(method_arn: str) -> str:
    # arn:partition:execute-api:region:acct:apiId/stage/VERB/resourcepath...
    head, _, tail = method_arn.partition(":execute-api:")
    # tail = "region:acct:apiId/stage/VERB/path..."
    prefix = tail.split("/")
    api_part = prefix[0]                      # "region:acct:apiId"
    stage = prefix[1] if len(prefix) > 1 else "*"
    # A SINGLE trailing "*" after the stage — it matches the HTTP method AND the full resource path
    # including "/" separators. A "*/*" form only matches {METHOD}/{single-segment}, so a policy
    # cached (authorizerResultTtlInSeconds, keyed on the Authorization header) from a one-segment
    # path like POST/uploads would NOT match a multi-segment path like POST/uploads/{id}/complete or
    # GET/database/{db}/assets/{a}/... — API Gateway then 403s the reused-cache request without
    # re-invoking the authorizer. The single-star wildcard covers every method + path for the API+stage.
    return f"{head}:execute-api:{api_part}/{stage}/*"


def _policy(principal_id: str, effect: str, resource: str, context: dict = None) -> dict:
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


def lambda_handler(event, context):
    method_arn = event.get("methodArn", "*")
    resource = _wildcard_resource(method_arn) if method_arn != "*" else "*"
    try:
        res = authenticate_request(event)
        log_authorization_gateway(event, res["authorized"], res.get("reason"))
        if not res["authorized"]:
            return _policy("user", "Deny", resource)
        ctx = res.get("context") or {}
        principal = ctx.get("sub") or ctx.get("cognito:username") or "user"
        return _policy(principal, "Allow", resource, ctx)
    except Exception as e:
        logger.error(f"Authorizer error: {str(e)}")
        return _policy("user", "Deny", resource)
