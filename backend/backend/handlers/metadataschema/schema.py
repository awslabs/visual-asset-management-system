# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import boto3
import os
import traceback
from backend.logging.logger import safeLogger
from backend.common.dynamodb import to_update_expr
from boto3.dynamodb.conditions import Key, Attr
from aws_lambda_powertools.utilities.typing import LambdaContext  
from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEvent
from decimal import Decimal

logger = safeLogger(service=__name__, child=True, level="INFO")

class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            if o % 1 > 0:
                return float(o)
            else:
                return int(o)
        return super(DecimalEncoder, self).default(o)

class ValidationError(Exception):
    def __init__(self, code: int, resp: object):
        self.code = code
        self.resp = resp


# Dynamodb Schema:
# Partition key: databaseId
# Sort key: field name
# Each partition contains the schema for a single VAMS database
# Each field in the schema is a dictionary with the following keys:
# - field: the name of the field
# - datatype: string, the datatype of the field
# - required: boolean, whether the field is required
# - dependsOn: array, other fields that this field depends on and must be filled out first
# -


class MetadataSchema:

    def __init__(self, table_name: str, dynamodb=None):
        self.attrs = "field,datatype,required,dependsOn".split(",")
        self.keys_attrs = {f"#{f}": f for f in self.attrs}

        self.table_name = table_name
        self.dynamodb = dynamodb or boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(table_name)

    @staticmethod
    def from_env():
        return MetadataSchema(os.environ["METADATA_SCHEMA_STORAGE_TABLE_NAME"])

    def get_schema(self, databaseId: str, field: str):
        resp = self.table.get_item(Key={"databaseId": databaseId, "field": field})
        if "Item" in resp:
            return resp["Item"]
        else:
            return None

    def update_schema(self, databaseId: str, field: str, schema: dict):
        # if the keys are in the schema dict, remove them
        if 'field' in schema: del schema['field']
        if 'databaseId' in schema: del schema['databaseId']
        keys_map, values_map, expr = to_update_expr(schema)
        resp = self.table.update_item(
            Key={
                "databaseId": databaseId,
                "field": field
            },
            UpdateExpression=expr,
            ExpressionAttributeNames=keys_map,
            ExpressionAttributeValues=values_map,
        )
        return resp

    def delete_schema(self, databaseId: str, field: str):
        resp = self.table.delete_item(
            Key={
                "databaseId": databaseId,
                "field": field
            }
        )
        return resp

    def get_all_schemas(self, databaseId: str):
        resp = self.table.query(
            KeyConditionExpression=Key("databaseId").eq(databaseId)
        )
        return resp["Items"]


def get_request_to_claims(event: APIGatewayProxyEvent):
    from backend.handlers.auth import request_to_claims
    return request_to_claims(event)

# databaseId is part of pathParameters
def lambda_handler(event: APIGatewayProxyEvent, context: LambdaContext, 
                   claims_fn=get_request_to_claims, 
                   metadata_schema_fn=MetadataSchema.from_env):

    logger.info("event: ", event)
    print("event", event)

    response = {
        'statusCode': 200,
        'body': {
            "requestid": event['requestContext']['requestId'],
        },
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Credentials': True,
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
        }
    }

    try:

        claims_and_roles = claims_fn(event)

        if "super-admin" not in claims_and_roles['roles']:
            raise ValidationError(403, "Not Authorized")

        if "databaseId" not in event["pathParameters"]:
            raise ValidationError(400, "Missing databaseId in path")

        schema = metadata_schema_fn()
        databaseId = event["pathParameters"]["databaseId"]
        method = event['requestContext']['http']['method']

        # list
        if method == "GET":
            resp = schema.get_all_schemas(databaseId)
            print("resp", resp)
            response['body']['schemas'] = resp

        # create/update
        if method == "POST" or method == "PUT":
            body = json.loads(event["body"])
            schema.update_schema(databaseId, body["field"], body)

        # delete
        if method == "DELETE":
            if "field" not in event['pathParameters']:
                raise ValidationError(400, "Missing field in path on delete request")

            schema.delete_schema(databaseId, event['pathParameters']['field'])

        response['body'] = json.dumps(response['body'], cls=DecimalEncoder)
        return response
    except ValidationError as e:
        response['statusCode'] = e.code
        response['body'] = json.dumps({
            "error": e.resp,
            "requestid": event['requestContext']['requestId'],
        })
        return response
    except Exception as e:
        logger.warning(traceback.format_exc(), event)
        response['statusCode'] = 500
        response['body'] = json.dumps({
            "error": str(e),
            "requestid": event['requestContext']['requestId'],
            "stacktrace": traceback.format_exc()
        })
        return response
