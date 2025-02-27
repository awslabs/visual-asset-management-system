#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
import requests

from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from common.constants import STANDARD_JSON_RESPONSE
from customLogging.logger import safeLogger

logger = safeLogger(service_name="Routes")

def lambda_handler(event, _):

    response = STANDARD_JSON_RESPONSE

    if isinstance(event["body"], str):
        event["body"] = json.loads(event["body"])

    routes = event["body"]["routes"]
    allowed_routes = []

    try:
        claims_and_roles = request_to_claims(event)

        #No API Casbin check on this call as it's a primary authService to verify other routes
        for route_obj in routes:
            route_obj.update({
                "object__type": "web"
            })

            if 'USE_LOCAL_MOCKS' in os.environ:
                allowed_routes.append(route_obj)
            else:
                if len(claims_and_roles["tokens"]) > 0:
                    print("casbin enforce", claims_and_roles["tokens"][0])
                    casbin_enforcer = CasbinEnforcer(claims_and_roles)
                    if casbin_enforcer.enforce(route_obj, route_obj["method"]):
                        allowed_routes.append(route_obj)

        response["body"] = json.dumps({"allowedRoutes": allowed_routes, "email": claims_and_roles["tokens"][0]})
        return response

    except Exception as e:
        response["statusCode"] = 500
        logger.exception(e)
        response["body"] = json.dumps({"message": "Internal Server Error"})

        return response

if __name__ == "__main__":
    test_response = lambda_handler(None, None)
    logger.info(test_response)