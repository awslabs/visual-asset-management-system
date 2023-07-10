#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import json

import os
import boto3

dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')
db_database = None


# TODO maybe this should be part of a class constructor instead

try:
    db_database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
except:
    print("Failed Loading Environment Variables")
    raise Exception("Failed Loading Environment Variables")


# @DeprecationWarning
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


def create_ddb_kwargs_for_token_filters(tokens):
    attrs = {":claim{}".format(n): {"S": v} for n, v in list(enumerate(tokens))}
    attrs[":deleted"] = { "S": "#deleted" }
    kwargs = {
        "ExpressionAttributeNames": {
            "#acl":  "acl",
            "#dbid": "databaseId",
        },
        "ExpressionAttributeValues": attrs,
        "FilterExpression": "NOT contains(#dbid, :deleted) AND ({})".format(" OR ".join("contains(#acl, {v})".format(v=claim) for claim in attrs.keys())),
        "Limit": 1000,
        "TableName": db_database,
        "ProjectionExpression": "databaseId"
    }
    return kwargs


# given the set of strings in the tokens set, return all the records in the db_database where the acl overlaps with tokens

def get_database_set(tokens):
    kwargs = create_ddb_kwargs_for_token_filters(tokens)
    result = dynamodb_client.scan(**kwargs)
    return [item['databaseId']['S'] for item in result['Items']]


def create_attr_values(prefix, values):
    return {":{prefix}{n}".format(prefix=prefix, n=n): {"S": v} for n, v in list(enumerate(values))}


def create_ddb_filter(databaseList):
    attrs = create_attr_values("db", databaseList)
    kwargs = {
        "ExpressionAttributeNames": {
            "#db": "databaseId",
        },
        "ExpressionAttributeValues": attrs,
        "FilterExpression": "#db in ({dbs})".format(dbs=", ".join(attrs.keys()))
    }
    return kwargs
