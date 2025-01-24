#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import json

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
            "externalAttributes": []
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
    elif 'email' in claims:
        tokens = [claims['email']]
    elif 'username' in claims:
        tokens = [claims['username']]
    elif 'sub' in claims:
        tokens = [claims['sub']]

    if 'vams:roles' in claims:
        roles = json.loads(claims['vams:roles'])
    if 'vams:externalAttributes' in claims:
        externalAttributes = json.loads(claims['vams:externalAttributes'])

    return {
        "tokens": tokens,
        "roles": roles,
        "externalAttributes": externalAttributes
    }
