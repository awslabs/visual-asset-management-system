#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
from customLogging.logger import safeLogger

logger = safeLogger(service="PreTokenGen")


def lambda_handler(event, context):

    logger.info(event)

    try:
        email = event['request']['userAttributes']['email']
    except Exception as e:
        logger.warning("Email not found in userAttributes")
        email = ""

    result = {}
    result.update(event)
    result.update({
        "response": {
            "claimsAndScopeOverrideDetails": {
                "idTokenGeneration": {
                    "claimsToAddOrOverride": {
                        "vams:externalAttributes": json.dumps([]), #TODO: Future use to add external system user attributes to claims that can be incorporated into ABAC system constraints
                        "vams:roles": json.dumps([]), #Resolved at authorization time by the API Gateway authorizer (common/auth/authorizerCore.py) so role changes take effect without re-issuing a token
                        "vams:tokens": (
                            json.dumps([event['userName']])
                        ),
                        "email": email
                    }
                },
                "accessTokenGeneration": {
                    "claimsToAddOrOverride": {
                        "vams:externalAttributes": json.dumps([]), #TODO: Future use to add external system user attributes to claims that can be incorporated into ABAC system constraints
                        "vams:roles": json.dumps([]), #Resolved at authorization time by the API Gateway authorizer (common/auth/authorizerCore.py) so role changes take effect without re-issuing a token
                        "vams:tokens": (
                            json.dumps([event['userName']])
                        ),
                        "email": email
                    }
                }
            },
        }
    })

    logger.info(result)
    return result
