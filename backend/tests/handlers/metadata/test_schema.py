# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from decimal import Decimal
import json
from unittest.mock import Mock, call

from backend.handlers.metadataschema.schema import MetadataSchema
import pytest
from boto3.dynamodb.conditions import Key

from backend.handlers.metadataschema.schema import APIGatewayProxyEvent


def test_get_schema():
    mock_ddb = Mock()
    mock_ddb.Table = Mock(return_value=mock_ddb)
    mock_ddb.get_item = Mock(return_value={'Item': {'schema': '{}'}})

    metadataSchema = MetadataSchema(table_name="tablename", dynamodb=mock_ddb)

    metadataSchema.get_schema("databaseId123",  "schemaId123")
    assert mock_ddb.get_item.call_args == call(Key={'databaseId': 'databaseId123', 'field': 'schemaId123'})


def test_get_schema_not_found():
    mock_ddb = Mock()
    mock_ddb.Table = Mock(return_value=mock_ddb)
    mock_ddb.get_item = Mock(return_value={'Item': None})

    metadataSchema = MetadataSchema(table_name="tablename", dynamodb=mock_ddb)

    result = metadataSchema.get_schema("databaseId123",  "schemaId123")
    assert mock_ddb.get_item.call_args == call(Key={'databaseId': 'databaseId123', 'field': 'schemaId123'})
    assert result is None


def test_get_schema_not_found2():
    mock_ddb = Mock()
    mock_ddb.Table = Mock(return_value=mock_ddb)
    mock_ddb.get_item = Mock(return_value={})

    metadataSchema = MetadataSchema(table_name="tablename", dynamodb=mock_ddb)

    result = metadataSchema.get_schema("databaseId123",  "schemaId123")
    assert mock_ddb.get_item.call_args == call(Key={'databaseId': 'databaseId123', 'field': 'schemaId123'})
    assert result is None


def test_get_schema_error():
    mock_ddb = Mock()
    mock_ddb.Table = Mock(return_value=mock_ddb)
    mock_ddb.get_item = Mock(side_effect=Exception("error"))

    metadataSchema = MetadataSchema(table_name="tablename", dynamodb=mock_ddb)

    with pytest.raises(Exception):
        metadataSchema.get_schema("databaseId123",  "schemaId123")


def test_get_schemas():
    mock_ddb = Mock()
    mock_ddb.Table = Mock(return_value=mock_ddb)
    mock_ddb.query = Mock(return_value={'Items': [{'schema': '{}'}], 'Count': 1})

    metadataSchema = MetadataSchema(table_name="tablename", dynamodb=mock_ddb)

    metadataSchema.get_all_schemas("databaseId123")
    assert mock_ddb.query.call_args == call(
        KeyConditionExpression=Key("databaseId").eq("databaseId123"),
    )


def test_get_schemas_not_found():
    mock_ddb = Mock()
    mock_ddb.Table = Mock(return_value=mock_ddb)
    mock_ddb.query = Mock(return_value={'Items': [], 'Count': 0})

    metadataSchema = MetadataSchema(table_name="tablename", dynamodb=mock_ddb)

    metadataSchema.get_all_schemas("databaseId123")
    assert mock_ddb.query.call_args == call(
        KeyConditionExpression=Key("databaseId").eq("databaseId123"),
    )


def test_get_schemas_error():
    mock_ddb = Mock()
    mock_ddb.Table = Mock(return_value=mock_ddb)
    mock_ddb.query = Mock(side_effect=Exception("error"))

    metadataSchema = MetadataSchema(table_name="tablename", dynamodb=mock_ddb)

    with pytest.raises(Exception):
        metadataSchema.get_all_schemas("databaseId123")


def test_update_schema():
    mock_ddb = Mock()
    mock_ddb.Table = Mock(return_value=mock_ddb)
    mock_ddb.update_item = Mock()

    metadataSchema = MetadataSchema(table_name="tablename", dynamodb=mock_ddb)

    metadataSchema.update_schema("databaseId123", "schemaId123", {
        "field": "schemaId123",
        "databaseId": "databaseId123",
        "datatype": "string",
        "required": True,
        "dependsOn": ["schemaId122"],
    })

    print(mock_ddb.update_item.call_args)
    assert mock_ddb.update_item.call_args == call(
        Key={'databaseId': 'databaseId123', 'field': 'schemaId123'},
        UpdateExpression='SET #f0 = :v0, #f1 = :v1, #f2 = :v2',
        ExpressionAttributeNames={'#f0': 'datatype', '#f1': 'required', '#f2': 'dependsOn'},
        ExpressionAttributeValues={':v0': 'string', ':v1': True, ':v2':  ['schemaId122']}
    )


def test_update_schema_error():
    mock_ddb = Mock()
    mock_ddb.Table = Mock(return_value=mock_ddb)
    mock_ddb.update_item = Mock(side_effect=Exception("error"))

    metadataSchema = MetadataSchema(table_name="tablename", dynamodb=mock_ddb)

    with pytest.raises(Exception):
        metadataSchema.update_schema("databaseId123", "schemaId123", {
            "field": "schemaId123",
            "datatype": "string",
            "required": True,
            "dependsOn": ["schemaId122"],
        })


def test_delete_schema():
    mock_ddb = Mock()
    mock_ddb.Table = Mock(return_value=mock_ddb)
    mock_ddb.delete_item = Mock()

    metadataSchema = MetadataSchema(table_name="tablename", dynamodb=mock_ddb)

    metadataSchema.delete_schema("databaseId123", "schemaId123")

    assert mock_ddb.delete_item.call_args == call(Key={'databaseId': 'databaseId123', 'field': 'schemaId123'})


def test_delete_schema_error():
    mock_ddb = Mock()
    mock_ddb.Table = Mock(return_value=mock_ddb)
    mock_ddb.delete_item = Mock(side_effect=Exception("error"))

    metadataSchema = MetadataSchema(table_name="tablename", dynamodb=mock_ddb)

    with pytest.raises(Exception):
        metadataSchema.delete_schema("databaseId123", "schemaId123")


def test_lambda_handler_not_super_admin():
    """All users can get schemas"""
    from backend.handlers.metadataschema.schema import lambda_handler
    from backend.handlers.metadataschema.schema import APIGatewayProxyEvent
    mock_claims = Mock(return_value={'roles': []})
    mock_metadata_schema = Mock()
    metadata_schema_factory = Mock(return_value=mock_metadata_schema)
    values = [
        {"field": "schemaId123", "datatype": "string", "required": True, "dependsOn": ["schemaId122"]},
        {"field": "schemaId122", "datatype": "string", "required": True, },
    ]
    mock_metadata_schema.get_all_schemas = Mock(return_value=values)

    event = APIGatewayProxyEvent({
        "httpMethod": "GET",
        "path": "/metadataschema",
        "queryStringParameters": {},
        "pathParameters": {
                "databaseId": "databaseId123",
        },
        "requestContext": {
            "requestId": "woohoo!=",
            "http": {
                "method": "GET",
            }
        }
    })
    context = {}

    response = lambda_handler(event, context, claims_fn=mock_claims, metadata_schema_fn=metadata_schema_factory)

    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert response_body['requestid'] == 'woohoo!='


def test_lambda_handler_missing_databaseId():
    from backend.handlers.metadataschema.schema import lambda_handler
    from backend.handlers.metadataschema.schema import APIGatewayProxyEvent
    mock_claims = Mock(return_value={'roles': ['super-admin']})
    mock_metadata_schema = Mock()

    event = APIGatewayProxyEvent({
        "httpMethod": "GET",
        "path": "/metadataschema",
        "queryStringParameters": {},
        "pathParameters": {},
        "requestContext": {
                "requestId": "woohoo!=",
        }
    })
    context = {}

    response = lambda_handler(event, context, claims_fn=mock_claims, metadata_schema_fn=mock_metadata_schema)

    assert response['statusCode'] == 400
    response_body = json.loads(response['body'])
    assert response_body['error'] == 'Missing databaseId in path'
    assert response_body['requestid'] == 'woohoo!='


def test_lambda_handler_get():
    from backend.handlers.metadataschema.schema import lambda_handler
    from backend.handlers.metadataschema.schema import APIGatewayProxyEvent
    mock_claims = Mock(return_value={'roles': ['super-admin']})
    mock_metadata_schema = Mock()
    metadata_schema_factory = Mock(return_value=mock_metadata_schema)
    values = [
        {"field": "schemaId123", "datatype": "string", "required": True, "dependsOn": ["schemaId122"]},
        {"field": "schemaId122", "datatype": "string", "required": True, },
    ]
    mock_metadata_schema.get_all_schemas = Mock(return_value=values)

    event = APIGatewayProxyEvent({
        "httpMethod": "GET",
        "path": "/metadataschema",
        "queryStringParameters": {},
        "pathParameters": {
                "databaseId": "databaseId123",
        },
        "requestContext": {
            "requestId": "woohoo!=",
            "http": {
                "method": "GET",
            }
        }
    })
    context = {}

    response = lambda_handler(event, context, claims_fn=mock_claims, metadata_schema_fn=metadata_schema_factory)

    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert response_body['requestid'] == 'woohoo!='
    assert response_body['schemas'] == values


def create_event(method):

    return APIGatewayProxyEvent({
        "httpMethod": method,
        "path": "/metadataschema",
        "queryStringParameters": {},
        "pathParameters": {
            "databaseId": "databaseId123",
        },
        "requestContext": {
            "requestId": "woohoo!=",
            "http": {
                "method": method,
            }
        },
        "body": json.dumps({
            "field": "schemaId123",
            "datatype": "string",
            "required": True,
            "sequenceNumber": 1.0,
            "dependsOn": ["schemaId122"],
        }),
    })


def create_delete_event():

    return APIGatewayProxyEvent({
        "httpMethod": "DELETE",
        "path": "/metadataschema",
        "queryStringParameters": {},
        "pathParameters": {
                "databaseId": "databaseId123",
                "field": "schemaId123",
        },
        "requestContext": {
            "requestId": "woohoo!=",
            "http": {
                "method": "DELETE",
            }
        },
    })


def test_lambda_handler_post():

    event = create_event("POST")

    response, mock_metadata_schema = _invoke_lambda_handler_harness(event)

    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert response_body['requestid'] == 'woohoo!='

    mock_metadata_schema.update_schema.assert_called_once_with("databaseId123", "schemaId123", {
        "field": "schemaId123",
        "datatype": "string",
        "required": True,
        "sequenceNumber": 1.0,
        "dependsOn": ["schemaId122"],
    })


def test_lambda_handler_put():
    event = create_event("PUT")

    response, mock_metadata_schema = _invoke_lambda_handler_harness(event)

    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert response_body['requestid'] == 'woohoo!='

    mock_metadata_schema.update_schema.assert_called_once_with("databaseId123", "schemaId123", {
        "field": "schemaId123",
        "datatype": "string",
        "required": True,
        "sequenceNumber": 1.0,
        "dependsOn": ["schemaId122"],
    })


def test_lambda_handler_delete():

    # delete events have no body
    event = create_delete_event()
    response, mock_metadata_schema = _invoke_lambda_handler_harness(event)

    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert response_body['requestid'] == 'woohoo!='

    mock_metadata_schema.delete_schema.assert_called_once_with("databaseId123", "schemaId123")


def test_lambda_handler_get_schema_with_decimal_response():
    from backend.handlers.metadataschema.schema import lambda_handler
    from backend.handlers.metadataschema.schema import APIGatewayProxyEvent
    mock_claims = Mock(return_value={'roles': ['super-admin']})
    mock_metadata_schema = Mock()
    metadata_schema_factory = Mock(return_value=mock_metadata_schema)
    values = [
        {"field": "schemaId123", "datatype": "string", "sequenceNumber":  Decimal(
            5.0), "required": True, "dependsOn": ["schemaId122"]},
        {"field": "schemaId122", "datatype": "string", "sequenceNumber": Decimal(10.0), "required": True, },
    ]
    mock_metadata_schema.get_all_schemas = Mock(return_value=values)

    event = APIGatewayProxyEvent({
        "httpMethod": "GET",
        "path": "/metadataschema",
        "queryStringParameters": {},
        "pathParameters": {
                "databaseId": "databaseId123",
        },
        "requestContext": {
            "requestId": "woohoo!=",
            "http": {
                "method": "GET",
            }
        }
    })
    context = {}

    response = lambda_handler(event, context, claims_fn=mock_claims, metadata_schema_fn=metadata_schema_factory)

    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert response_body['requestid'] == 'woohoo!='
    assert response_body['schemas'] == values


def _invoke_lambda_handler_harness(event,):
    from backend.handlers.metadataschema.schema import lambda_handler
    mock_claims = Mock(return_value={'roles': ['super-admin']})
    mock_metadata_schema = Mock()
    metadata_schema_factory = Mock(return_value=mock_metadata_schema)

    context = {}

    response = lambda_handler(event, context, claims_fn=mock_claims, metadata_schema_fn=metadata_schema_factory)
    return response, mock_metadata_schema
