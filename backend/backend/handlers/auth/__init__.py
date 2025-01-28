#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import json
from customConfigCommon.customMFATokenScopeCheck import customMFATokenScopeCheckOverride

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

    #Conduct MFA sign-in check using custom scope check
    mfaEnabled = False
    try:
        mfaEnabled = customMFATokenScopeCheckOverride(tokens[0], request)
    except:
        pass

    return {
        "tokens": tokens,
        "roles": roles,
        "externalAttributes": externalAttributes,
        "mfaEnabled": mfaEnabled
    }
