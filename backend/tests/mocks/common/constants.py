# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# Standard JSON response for API Gateway Lambda functions
STANDARD_JSON_RESPONSE = {
    "statusCode": 200,
    "headers": {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type,Authorization"
    },
    "body": ""
}

# Other constants used in the application
DEFAULT_REGION = "us-east-1"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
