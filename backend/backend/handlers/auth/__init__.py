#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import json
from customConfigCommon.customAuthClaimsCheck import customAuthClaimsCheckOverride
from common.auth.apiEvent import normalize_event
from customLogging.logger import safeLogger

logger = safeLogger(service="RequestToClaims")

def request_to_claims(request):
    normalize_event(request)

    #Lambda cross-calling input short-circuit. 
    if 'lambdaCrossCall' in request:
        return {
            "tokens": [request["lambdaCrossCall"].get("userName", "SYSTEM_USER")],
            "roles": [],
            "externalAttributes": [],
            "mfaEnabled": True
        }
    elif 'requestContext' not in request or 'authorizer' not in request['requestContext']:
        return {
            "tokens": [],
            "roles": [],
            "externalAttributes": [],
            "mfaEnabled": False
        }

    claims = {}
    tokens = []
    roles = []
    externalAttributes = []
    mfaEnabled = False

    #Handle claims from: HTTP API JWT authorizer, HTTP API lambda authorizer (v2),
    #or REST API REQUEST lambda authorizer (flat string map under 'authorizer').
    authorizer_ctx = request['requestContext']['authorizer']
    if 'jwt' in authorizer_ctx and 'claims' in authorizer_ctx['jwt']:
        claims = authorizer_ctx['jwt']['claims']
    elif 'lambda' in authorizer_ctx:
        claims = authorizer_ctx['lambda']
    elif isinstance(authorizer_ctx, dict):
        # REST REQUEST authorizer: context is a flat map of string values.
        claims = {k: v for k, v in authorizer_ctx.items() if k != 'principalId'}
    else:
        claims = {}


    #For tokens, look at other fields if vams:tokens does not exist in claims
    if 'vams:tokens' in claims:
        tokens = json.loads(claims['vams:tokens'])
    elif 'cognito:username' in claims:
        tokens = [claims['cognito:username']]
    elif 'username' in claims:
        tokens = [claims['username']]
    elif 'sub' in claims:
        tokens = [claims['sub']]
    elif 'upn' in claims:
        tokens = [claims['upn']]
    elif 'email' in claims:
        tokens = [claims['email']]

    if 'vams:roles' in claims:
        roles = json.loads(claims['vams:roles'])
    if 'vams:externalAttributes' in claims:
        externalAttributes = json.loads(claims['vams:externalAttributes'])

    #MFA sign-in status is resolved at authorization time by the API Gateway authorizer
    #(common/auth/authorizerCore.py via the customMFATokenScopeCheckOverride hook) and
    #passed through the authorizer context as vams:mfaEnabled
    if 'vams:mfaEnabled' in claims:
        mfaValue = claims['vams:mfaEnabled']
        mfaEnabled = mfaValue == 'true' if isinstance(mfaValue, str) else bool(mfaValue)

    claims_and_roles = {
            "tokens": tokens,
            "roles": roles,
            "externalAttributes": externalAttributes,
            "mfaEnabled": mfaEnabled
        }

    #Conduct custom claims check. If a customer-supplied hook raises, fail closed by
    #dropping roles (rather than silently passing the un-filtered claims through) so a
    #broken claims-restriction hook cannot grant more access than intended.
    try:
        claims_and_roles = customAuthClaimsCheckOverride(claims_and_roles, request)
    except Exception as e:
        logger.exception(f"customAuthClaimsCheckOverride failed; denying roles: {e}")
        claims_and_roles["roles"] = []

    return claims_and_roles
