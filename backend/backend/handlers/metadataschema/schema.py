# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import boto3
import os
from customLogging.logger import safeLogger
from common.dynamodb import to_update_expr
from boto3.dynamodb.conditions import Key
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEvent
from decimal import Decimal
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from common.dynamodb import validate_pagination_info
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer

claims_and_roles = {}

logger = safeLogger(service="MetadataSchema")
dynamodb_client = boto3.client('dynamodb')


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
        metadataSchema = resp["Item"]
        allowed = False

        if "Item" in resp:
            metadataSchema.update({"object__type": "metadataSchema"})
            for user_name in claims_and_roles["tokens"]:
                casbin_enforcer = CasbinEnforcer(user_name)
                if casbin_enforcer.enforce(f"user::{user_name}", metadataSchema, "GET"):
                    allowed = True
                    break
            return resp["Item"] if allowed else None
        else:
            return None

    def update_schema(self, databaseId: str, field: str, schema: dict):
        schema_object = schema
        schema_object.update({"object__type": "metadataSchema"})
        allowed = False

        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", schema_object, "POST"):
                allowed = True

        if allowed:
            # if the keys are in the schema dict, remove them
            if 'field' in schema:
                del schema['field']
            if 'databaseId' in schema:
                del schema['databaseId']
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
        else:
            return 403

    def delete_schema(self, databaseId: str, field: str):
        schema_object = {
            'databaseId': databaseId,
            'field': field,
            'object__type': 'metadataSchema'
        }
        allowed = False
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", schema_object, "DELETE"):
                allowed = True

        if allowed:
            resp = self.table.delete_item(
                Key={
                    "databaseId": databaseId,
                    "field": field
                }
            )
            return resp
        else:
            return 403

    def get_all_schemas(self, databaseId: str, query_params):
        result = {
            "Items": []
        }

        if databaseId:
            paginator = self.dynamodb.meta.client.get_paginator('query')
            pageIterator = paginator.paginate(
                TableName=self.table_name,
                KeyConditionExpression=Key("databaseId").eq(databaseId),
                PaginationConfig={
                    'MaxItems': int(query_params['maxItems']),
                    'PageSize': int(query_params['pageSize']),
                    'StartingToken': query_params['startingToken']
                }
            ).build_full_result()

            schemas = pageIterator["Items"]

            for metadataSchema in schemas:
                metadataSchema.update({
                    "object__type": "metadataSchema"
                })
                for user_name in claims_and_roles["tokens"]:
                    casbin_enforcer = CasbinEnforcer(user_name)
                    if casbin_enforcer.enforce(f"user::{user_name}", metadataSchema, "GET"):
                        result["Items"].append(metadataSchema)
                        break

            if "NextToken" in pageIterator:
                result["NextToken"] = pageIterator["NextToken"]


        else:
            deserializer = TypeDeserializer()
            paginator = dynamodb_client.get_paginator('scan')
            pageIterator = paginator.paginate(
                TableName=self.table_name,
                PaginationConfig={
                    'MaxItems': int(query_params['maxItems']),
                    'PageSize': int(query_params['pageSize']),
                    'StartingToken': query_params['startingToken']
                }
            ).build_full_result()

            schemas = pageIterator["Items"]

            for metadataSchema in schemas:
                deserialized_document = {k: deserializer.deserialize(v) for k, v in metadataSchema.items()}

                metadataSchema.update({
                    "object__type": "metadataSchema"
                })
                for user_name in claims_and_roles["tokens"]:
                    casbin_enforcer = CasbinEnforcer(user_name)
                    if casbin_enforcer.enforce(f"user::{user_name}", deserialized_document, "GET"):
                        result["Items"].append(deserialized_document)
                        break
            
            if "NextToken" in pageIterator:
                result["NextToken"] = pageIterator["NextToken"]

        return result


def get_request_to_claims(event: APIGatewayProxyEvent):
    return request_to_claims(event)

# databaseId is part of pathParameters


def lambda_handler(event: APIGatewayProxyEvent, context: LambdaContext,
                   claims_fn=get_request_to_claims,
                   metadata_schema_fn=MetadataSchema.from_env):

    logger.info(event)

    response = STANDARD_JSON_RESPONSE
    response['body'] = {"requestid": event['requestContext']['requestId']}

    try:
        global claims_and_roles
        # claims_and_roles = request_to_claims(event)
        claims_and_roles = claims_fn(event)

        path_parameters = event.get('pathParameters', {})
        if 'databaseId' not in path_parameters:
            message = "No database ID in API Call"
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            logger.error(response)
            return response

        logger.info("Validating parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': path_parameters['databaseId'],
                'validator': 'ID'
            },
        })

        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response
        
        queryParameters = event.get('queryStringParameters', {})
        validate_pagination_info(queryParameters)

        schema = metadata_schema_fn()
        databaseId = event.get("pathParameters", {}).get("databaseId")
        method = event['requestContext']['http']['method']

        method_allowed_on_api = False
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        # list
        if method == "GET" and method_allowed_on_api:
            resp = schema.get_all_schemas(databaseId, queryParameters)
            logger.info(resp)
            response['body'] = json.dumps({"message": resp}, cls=DecimalEncoder)
            return response

        # create/update
        elif (method == "POST" or method == "PUT") and method_allowed_on_api:
            body = json.loads(event["body"])
            if "field" not in body:
                raise ValidationError(400, "Missing field in path on POST/PUT request")
            resp = schema.update_schema(databaseId, body["field"], body)
            if resp == 403:
                response['statusCode'] = 403
                response['body'] = json.dumps({"message": "Not Authorized"})
                return response
        # delete
        elif method == "DELETE" and method_allowed_on_api:
            if "field" not in event['pathParameters']:
                raise ValidationError(400, "Missing field in path on delete request")

            resp = schema.delete_schema(databaseId, event['pathParameters']['field'])
            if resp == 403:
                response['statusCode'] = 403
                response['body'] = json.dumps({"message": "Not Authorized"})
                return response
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response

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
        logger.exception(event)
        response['statusCode'] = 500
        response['body'] = json.dumps({
            "error": "Internal Server Error",
            "requestid": event['requestContext']['requestId'],
        })
        return response
