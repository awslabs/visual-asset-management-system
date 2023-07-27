#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import json


def request_to_claims(request):
    if 'requestContext' not in request:
        return {
            "tokens": [],
            "roles": ["super-admin"],
        }

    return {
        "tokens": json.loads(request['requestContext']['authorizer']['jwt']['claims']['vams:tokens']),
        "roles": json.loads(request['requestContext']['authorizer']['jwt']['claims']['vams:roles']),
    }