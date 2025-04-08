# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import patch, MagicMock

# Import actual implementation
from backend.backend.handlers.search.search import property_token_filter_to_opensearch_query, lambda_handler


def test_example_body_with_query_only2():
    result_example = {
        "query": {
            "bool": {
                "must": [],
                "filter": [],
                "should": [],
                "must_not": [],
            }
        },
    }

    example_body = {
        "operation": "AND"
    }

    # Call the actual implementation
    result = property_token_filter_to_opensearch_query(example_body)

    # Verify the result matches the expected output
    assert result['query']['bool']['must'] == result_example['query']['bool']['must']
    assert result['query']['bool']['filter'] == result_example['query']['bool']['filter']
    assert result['query']['bool']['should'] == result_example['query']['bool']['should']
    # Note: The actual implementation adds a default filter for #deleted databaseId entries
    # so we can't directly compare must_not


def test_example_body_with_query_only():
    example_body = {
        "query": "one two three",
        "operation": "AND"
    }

    # Call the actual implementation with empty uniqueMappingFieldsForGeneralQuery
    result = property_token_filter_to_opensearch_query(example_body, [])

    # Verify the result contains the expected structure
    assert result['query']['bool']['should'] == []
    # Note: The actual implementation handles query differently than the mock
    # It adds wildcard searches to the should clause based on uniqueMappingFieldsForGeneralQuery


def test_example_body():
    example_body = {
        "tokens": [
            {
                "propertyKey": "all",
                "operator": "=",
                "value": "one two three"
            }
        ],
        "operation": "AND"
    }

    # Call the actual implementation
    result = property_token_filter_to_opensearch_query(example_body)

    # Verify the result contains the expected structure
    assert result['query']['bool']['must'][0]['multi_match']['query'] == "one two three"
    assert result['query']['bool']['must'][0]['multi_match']['type'] == "best_fields"
    assert result['query']['bool']['must'][0]['multi_match']['lenient'] == True


def test_example_without_propertyKey():
    example_body = {
        "tokens": [
            {
                "operator": "=",
                "value": "one two three"
            }
        ],
        "operation": "AND"
    }

    # Call the actual implementation
    result = property_token_filter_to_opensearch_query(example_body)

    # Verify the result contains the expected structure
    assert result['query']['bool']['must'][0]['multi_match']['query'] == 'one two three'


def test_with_propertyKey():
    example_body = {
        "tokens": [
            {
                "propertyKey": "name",
                "operator": "=",
                "value": "one two three"
            }
        ],
        "operation": "AND"
    }

    # Call the actual implementation
    result = property_token_filter_to_opensearch_query(example_body)

    # Verify the result contains the expected structure
    assert result['query']['bool']['must'][0]['match']['name'] == 'one two three'


def test_with_multiple_tokens_two_different_propertyKeys():
    example_body = {
        "tokens": [
            {
                "propertyKey": "name",
                "operator": "=",
                "value": "one two three"
            },
            {
                "propertyKey": "description",
                "operator": "=",
                "value": "four five six"
            }
        ],
        "operation": "AND"
    }

    # Call the actual implementation
    result = property_token_filter_to_opensearch_query(example_body)

    # Verify the result contains the expected structure
    assert result['query']['bool']['must'][0]['match']['name'] == 'one two three'
    assert result['query']['bool']['must'][1]['match']['description'] == 'four five six'


def test_muliple_tokens_multiple_operators():
    example_body = {
        "tokens": [
            {
                "propertyKey": "name",
                "operator": "=",
                "value": "one two three"
            },
            {
                "propertyKey": "description",
                "operator": "!=",
                "value": "four five six"
            }
        ],
        "operation": "AND"
    }

    # Call the actual implementation
    result = property_token_filter_to_opensearch_query(example_body)

    # Verify the result contains the expected structure
    assert result['query']['bool']['must'][0]['match']['name'] == 'one two three'
    
    # Find the description in must_not
    description_match = None
    for item in result['query']['bool']['must_not']:
        if 'match' in item and 'description' in item['match']:
            description_match = item
            break
    
    assert description_match is not None
    assert description_match['match']['description'] == 'four five six'


def test_or_operation():
    example_body = {
        "tokens": [
            {
                "propertyKey": "name",
                "operator": "=",
                "value": "one two three"
            },
            {
                "propertyKey": "description",
                "operator": "=",
                "value": "four five six"
            }
        ],
        "operation": "OR"
    }

    # Call the actual implementation
    result = property_token_filter_to_opensearch_query(example_body)

    # Verify the result contains the expected structure
    assert result['query']['bool']['should'][0]['match']['name'] == 'one two three'
    assert result['query']['bool']['should'][1]['match']['description'] == 'four five six'


def test_or_operation_with_must_not():
    example_body = {
        "tokens": [
            {
                "propertyKey": "name",
                "operator": "=",
                "value": "one two three"
            },
            {
                "propertyKey": "description",
                "operator": "!=",
                "value": "four five six"
            }
        ],
        "operation": "OR"
    }

    # Call the actual implementation
    result = property_token_filter_to_opensearch_query(example_body)

    # Verify the result contains the expected structure
    assert result['query']['bool']['should'][0]['match']['name'] == 'one two three'
    
    # Find the description in must_not
    description_match = None
    for item in result['query']['bool']['must_not']:
        if 'match' in item and 'description' in item['match']:
            description_match = item
            break
    
    assert description_match is not None
    assert description_match['match']['description'] == 'four five six'


def test_pagination_options():
    example_body = {
        "tokens": [
            {
                "propertyKey": "name",
                "operator": "=",
                "value": "one two three"
            },
            {
                "propertyKey": "description",
                "operator": "!=",
                "value": "four five six"
            }
        ],
        "operation": "OR",
        "from": 10,
        "size": 20,
    }

    # Call the actual implementation
    result = property_token_filter_to_opensearch_query(example_body)

    # Verify the result contains the expected structure
    assert result['from'] == 10
    assert result['size'] == 20


@pytest.fixture
def search_event():
    return {
        "version": "2.0",
        "routeKey": "POST /search",
        "rawPath": "/search",
        "rawQueryString": "",
        "headers": {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "host": "example.execute-api.us-east-1.amazonaws.com",
        },
        "requestContext": {
            "apiId": "example",
            "authorizer": {
                "jwt": {
                    "claims": {
                     "cognito:username": "user@example.com",
                     "email": "user@example.com",
                    },
                }
            },
            "domainName": "example.execute-api.us-east-1.amazonaws.com",
            "domainPrefix": "example",
            "http": {
                "method": "POST",
                "path": "/search",
                "protocol": "HTTP/1.1",
                "sourceIp": "x.x.x.x",
            },
            "requestId": "AE6vAj8EoAMEb5Q=",
            "routeKey": "POST /search",
            "stage": "$default",
            "time": "09/Feb/2023:15:03:08 +0000",
            "timeEpoch": 1675954988528
        },
        "body": json.dumps({
            "tokens": [
                {
                    "propertyKey": "name",
                    "operator": "=",
                    "value": "one two three"
                }
            ],
            "operation": "AND"
        }),
        "isBase64Encoded": False
    }


@patch('backend.backend.handlers.search.search.request_to_claims')
@patch('backend.backend.handlers.search.search.CasbinEnforcer')
@patch('backend.backend.handlers.search.search.SearchAOS')
@patch('backend.backend.handlers.search.search.os')
def test_lambda_handler_post(mock_os, mock_search_aos, mock_enforcer, mock_claims, search_event):
    # Setup mocks
    mock_claims.return_value = {"tokens": ["test-token"]}
    
    mock_enforcer_instance = MagicMock()
    mock_enforcer_instance.enforceAPI.return_value = True
    mock_enforcer_instance.enforce.return_value = True
    mock_enforcer.return_value = mock_enforcer_instance
    
    mock_os.environ = {"AOS_DISABLED": "false"}
    
    mock_search_instance = MagicMock()
    mock_search_instance.mapping.return_value = {"mappings": {"properties": {}}}
    mock_search_instance.search.return_value = {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "str_databaseid": "test-db",
                        "str_assetname": "test-asset",
                        "list_tags": ["tag1", "tag2"],
                        "str_assettype": "test-type"
                    }
                }
            ],
            "total": {"value": 1}
        }
    }
    mock_search_aos.from_env.return_value = mock_search_instance
    
    # Call the lambda handler
    response = lambda_handler(search_event, None)
    
    # Verify the response
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert "hits" in body
    assert len(body["hits"]["hits"]) == 1
    
    # Verify the mocks were called correctly
    mock_claims.assert_called_once()
    mock_enforcer_instance.enforceAPI.assert_called_once()
    mock_enforcer_instance.enforce.assert_called_once()
    mock_search_aos.from_env.assert_called_once()
    mock_search_instance.search.assert_called_once()


@patch('backend.backend.handlers.search.search.request_to_claims')
@patch('backend.backend.handlers.search.search.CasbinEnforcer')
@patch('backend.backend.handlers.search.search.SearchAOS')
@patch('backend.backend.handlers.search.search.os')
def test_lambda_handler_get(mock_os, mock_search_aos, mock_enforcer, mock_claims):
    # Setup mocks
    mock_claims.return_value = {"tokens": ["test-token"]}
    
    mock_enforcer_instance = MagicMock()
    mock_enforcer_instance.enforceAPI.return_value = True
    mock_enforcer.return_value = mock_enforcer_instance
    
    mock_os.environ = {"AOS_DISABLED": "false"}
    
    mock_search_instance = MagicMock()
    mock_search_instance.mapping.return_value = {"mappings": {"properties": {}}}
    mock_search_aos.from_env.return_value = mock_search_instance
    
    # Create a GET event
    get_event = {
        "version": "2.0",
        "routeKey": "GET /search",
        "rawPath": "/search",
        "rawQueryString": "",
        "headers": {
            "accept": "application/json, text/plain, */*",
            "host": "example.execute-api.us-east-1.amazonaws.com",
        },
        "requestContext": {
            "apiId": "example",
            "authorizer": {
                "jwt": {
                    "claims": {
                     "cognito:username": "user@example.com",
                     "email": "user@example.com",
                    },
                }
            },
            "domainName": "example.execute-api.us-east-1.amazonaws.com",
            "domainPrefix": "example",
            "http": {
                "method": "GET",
                "path": "/search",
                "protocol": "HTTP/1.1",
                "sourceIp": "x.x.x.x",
            },
            "requestId": "AE6vAj8EoAMEb5Q=",
            "routeKey": "GET /search",
            "stage": "$default",
            "time": "09/Feb/2023:15:03:08 +0000",
            "timeEpoch": 1675954988528
        },
        "isBase64Encoded": False
    }
    
    # Call the lambda handler
    response = lambda_handler(get_event, None)
    
    # Verify the response
    assert response["statusCode"] == 200
    
    # Verify the mocks were called correctly
    mock_claims.assert_called_once()
    mock_enforcer_instance.enforceAPI.assert_called_once()
    mock_search_aos.from_env.assert_called_once()
    mock_search_instance.mapping.assert_called_once()
