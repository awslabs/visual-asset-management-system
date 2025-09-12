#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import json
from customConfigCommon.customAuthClaimsCheck import customAuthClaimsCheckOverride

def request_to_claims(request):
    if 'requestContext' not in request:
        # #Lambda cross-calling input. Only checked when requestContext is not present through API Gateway call.
        # if 'lambdaCrossCall' in request:
        #     return {
        #         "tokens": [request["lambdaCrossCall"]["userName"]],
        #         "roles": [],
        #         "externalAttributes": []
        #     }
        # else:
        return {
            "tokens": [],
            "roles": [],
            "externalAttributes": [],
            "mfaEnabled": False
        }

    claims = request['requestContext']['authorizer']['jwt']['claims']
    tokens = []
    roles = []
    externalAttributes = []
    mfaEnabled = False

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

    claims_and_roles = {
            "tokens": tokens,
            "roles": roles,
            "externalAttributes": externalAttributes,
            "mfaEnabled": mfaEnabled
        }

    #Conduct custom claims check, including MFA sign-in
    try:
        claims_and_roles = customAuthClaimsCheckOverride(claims_and_roles, request)
    except:
        pass

    return claims_and_roles
