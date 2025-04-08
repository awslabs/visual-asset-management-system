# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import patch, MagicMock

# Import the actual lambda handler and utility function
from backend.backend.handlers.search.search import lambda_handler, property_token_filter_to_opensearch_query


def test_example_body_with_query_only2():
    """Test property_token_filter_to_opensearch_query with only operation"""
    example_body = {
        "operation": "AND"
    }

    result = property_token_filter_to_opensearch_query(example_body)

    # Assert the expected structure
    assert "query" in result
    assert "bool" in result["query"]
    assert "must" in result["query"]["bool"]
    assert "filter" in result["query"]["bool"]
    assert "should" in result["query"]["bool"]
    assert "must_not" in result["query"]["bool"]


def test_example_body_with_query_only():
    """Test property_token_filter_to_opensearch_query with query and operation"""
    example_body = {
        "query": "one two three",
        "operation": "AND"
    }

    result = property_token_filter_to_opensearch_query(example_body)

    # Assert the expected structure
    assert "query" in result
    assert "bool" in result["query"]
    assert "must" in result["query"]["bool"]
    assert "filter" in result["query"]["bool"]
    assert "should" in result["query"]["bool"]
    assert "must_not" in result["query"]["bool"]
    
    # Since we're testing the actual implementation, we need to check for the presence
    # of the wildcard search in the should criteria, not the multi_match in must
    assert len(result["query"]["bool"]["should"]) > 0


def test_example_body():
    """Test property_token_filter_to_opensearch_query with tokens"""
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

    result = property_token_filter_to_opensearch_query(example_body)

    # Assert the expected structure
    assert "query" in result
    assert "bool" in result["query"]
    assert "must" in result["query"]["bool"]
    assert len(result["query"]["bool"]["must"]) > 0
    
    # Check that the first must condition is a multi_match for "all" property key
    assert "multi_match" in result["query"]["bool"]["must"][0]
    assert result["query"]["bool"]["must"][0]["multi_match"]["query"] == "one two three"


def test_example_without_propertyKey():
    """Test property_token_filter_to_opensearch_query with tokens without propertyKey"""
    example_body = {
        "tokens": [
            {
                "operator": "=",
                "value": "one two three"
            }
        ],
        "operation": "AND"
    }

    result = property_token_filter_to_opensearch_query(example_body)

    # Assert the expected structure
    assert "query" in result
    assert "bool" in result["query"]
    assert "must" in result["query"]["bool"]
    assert len(result["query"]["bool"]["must"]) > 0
    
    # Check that the first must condition is a multi_match for missing property key
    assert "multi_match" in result["query"]["bool"]["must"][0]
    assert result["query"]["bool"]["must"][0]["multi_match"]["query"] == "one two three"


def test_with_propertyKey():
    """Test property_token_filter_to_opensearch_query with specific propertyKey"""
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

    result = property_token_filter_to_opensearch_query(example_body)

    # Assert the expected structure
    assert "query" in result
    assert "bool" in result["query"]
    assert "must" in result["query"]["bool"]
    assert len(result["query"]["bool"]["must"]) > 0
    
    # Check that the first must condition is a match for "name" property key
    assert "match" in result["query"]["bool"]["must"][0]
    assert result["query"]["bool"]["must"][0]["match"]["name"] == "one two three"


def test_with_multiple_tokens_two_different_propertyKeys():
    """Test property_token_filter_to_opensearch_query with multiple tokens and propertyKeys"""
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

    result = property_token_filter_to_opensearch_query(example_body)

    # Assert the expected structure
    assert "query" in result
    assert "bool" in result["query"]
    assert "must" in result["query"]["bool"]
    assert len(result["query"]["bool"]["must"]) >= 2
    
    # Check that the must conditions include matches for both property keys
    name_match = False
    desc_match = False
    
    for condition in result["query"]["bool"]["must"]:
        if "match" in condition:
            if "name" in condition["match"]:
                assert condition["match"]["name"] == "one two three"
                name_match = True
            if "description" in condition["match"]:
                assert condition["match"]["description"] == "four five six"
                desc_match = True
    
    assert name_match and desc_match


def test_muliple_tokens_multiple_operators():
    """Test property_token_filter_to_opensearch_query with multiple tokens and operators"""
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

    result = property_token_filter_to_opensearch_query(example_body)

    # Assert the expected structure
    assert "query" in result
    assert "bool" in result["query"]
    assert "must" in result["query"]["bool"]
    assert "must_not" in result["query"]["bool"]
    
    # Check that the must condition includes match for name
    assert len(result["query"]["bool"]["must"]) > 0
    name_match = False
    for condition in result["query"]["bool"]["must"]:
        if "match" in condition and "name" in condition["match"]:
            assert condition["match"]["name"] == "one two three"
            name_match = True
    assert name_match
    
    # Check that the must_not condition includes match for description
    assert len(result["query"]["bool"]["must_not"]) > 0
    desc_match = False
    for condition in result["query"]["bool"]["must_not"]:
        if "match" in condition and "description" in condition["match"]:
            assert condition["match"]["description"] == "four five six"
            desc_match = True
    assert desc_match


def test_or_operation():
    """Test property_token_filter_to_opensearch_query with OR operation"""
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

    result = property_token_filter_to_opensearch_query(example_body)

    # Assert the expected structure
    assert "query" in result
    assert "bool" in result["query"]
    assert "should" in result["query"]["bool"]
    
    # Check that the should condition includes matches for both property keys
    assert len(result["query"]["bool"]["should"]) >= 2
    name_match = False
    desc_match = False
    
    for condition in result["query"]["bool"]["should"]:
        if "match" in condition:
            if "name" in condition["match"]:
                assert condition["match"]["name"] == "one two three"
                name_match = True
            if "description" in condition["match"]:
                assert condition["match"]["description"] == "four five six"
                desc_match = True
    
    assert name_match and desc_match


def test_or_operation_with_must_not():
    """Test property_token_filter_to_opensearch_query with OR operation and must_not"""
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

    result = property_token_filter_to_opensearch_query(example_body)

    # Assert the expected structure
    assert "query" in result
    assert "bool" in result["query"]
    assert "should" in result["query"]["bool"]
    assert "must_not" in result["query"]["bool"]
    
    # Check that the should condition includes match for name
    assert len(result["query"]["bool"]["should"]) > 0
    name_match = False
    for condition in result["query"]["bool"]["should"]:
        if "match" in condition and "name" in condition["match"]:
            assert condition["match"]["name"] == "one two three"
            name_match = True
    assert name_match
    
    # Check that the must_not condition includes match for description
    assert len(result["query"]["bool"]["must_not"]) > 0
    desc_match = False
    for condition in result["query"]["bool"]["must_not"]:
        if "match" in condition and "description" in condition["match"]:
            assert condition["match"]["description"] == "four five six"
            desc_match = True
    assert desc_match


def test_pagination_options():
    """Test property_token_filter_to_opensearch_query with pagination options"""
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

    result = property_token_filter_to_opensearch_query(example_body)

    # Assert pagination parameters
    assert result["from"] == 10
    assert result["size"] == 20


# Tests for the lambda_handler function

@patch('backend.backend.handlers.search.search.os')
@patch('backend.backend.handlers.search.search.request_to_claims')
@patch('backend.backend.handlers.search.search.CasbinEnforcer')
@patch('backend.backend.handlers.search.search.SearchAOS.from_env')
def test_lambda_handler_get_mapping(mock_search_aos_from_env, mock_casbin_enforcer, mock_request_to_claims, mock_os):
    """Test lambda_handler with GET request for mapping"""
    # Setup mocks
    mock_os.environ.get.return_value = "false"  # AOS_DISABLED = false
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_search_aos = MagicMock()
    mock_search_aos.mapping.return_value = {"mappings": {"properties": {"field1": {}, "field2": {}}}}
    mock_search_aos_from_env.return_value = mock_search_aos
    
    # Create test event
    event = {
        "requestContext": {
            "http": {"method": "GET"}
        },
        "headers": {
            "Authorization": "Bearer test-token"
        }
    }
    
    # Call the lambda handler
    response = lambda_handler(event, {})
    
    # Assert the response
    assert response["statusCode"] == 200
    response_body = json.loads(response["body"])
    assert "mappings" in response_body
    assert "properties" in response_body["mappings"]
    
    # Verify mocks were called correctly
    mock_request_to_claims.assert_called_once_with(event)
    mock_casbin_enforcer.assert_called_once()
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once_with(event)
    mock_search_aos.mapping.assert_called_once()


@patch('backend.backend.handlers.search.search.os')
@patch('backend.backend.handlers.search.search.request_to_claims')
@patch('backend.backend.handlers.search.search.CasbinEnforcer')
@patch('backend.backend.handlers.search.search.SearchAOS.from_env')
def test_lambda_handler_post_search(mock_search_aos_from_env, mock_casbin_enforcer, mock_request_to_claims, mock_os):
    """Test lambda_handler with POST request for search"""
    # Setup mocks
    mock_os.environ.get.return_value = "false"  # AOS_DISABLED = false
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer_instance.enforce.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_search_aos = MagicMock()
    mock_search_aos.mapping.return_value = {"mappings": {"properties": {"field1": {}, "field2": {}}}}
    mock_search_aos.search.return_value = {
        "hits": {
            "total": {"value": 1},
            "hits": [{
                "_source": {
                    "str_databaseid": "test-asset-id",
                    "str_assetname": "Test Asset",
                    "list_tags": ["tag1", "tag2"],
                    "str_assettype": "document"
                }
            }]
        }
    }
    mock_search_aos_from_env.return_value = mock_search_aos
    
    # Create test event
    event = {
        "body": json.dumps({
            "query": "test query",
            "operation": "AND"
        }),
        "requestContext": {
            "http": {"method": "POST"}
        },
        "headers": {
            "Authorization": "Bearer test-token"
        }
    }
    
    # Call the lambda handler
    response = lambda_handler(event, {})
    
    # Assert the response
    assert response["statusCode"] == 200
    response_body = json.loads(response["body"])
    assert "hits" in response_body
    assert "total" in response_body["hits"]
    assert "hits" in response_body["hits"]
    assert len(response_body["hits"]["hits"]) == 1
    
    # Verify mocks were called correctly
    mock_request_to_claims.assert_called_once_with(event)
    mock_casbin_enforcer.assert_called_once()
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once_with(event)
    mock_search_aos.search.assert_called_once()
    mock_casbin_enforcer_instance.enforce.assert_called_once()


@patch('backend.backend.handlers.search.search.os')
@patch('backend.backend.handlers.search.search.request_to_claims')
@patch('backend.backend.handlers.search.search.CasbinEnforcer')
def test_lambda_handler_unauthorized(mock_casbin_enforcer, mock_request_to_claims, mock_os):
    """Test lambda_handler with unauthorized request"""
    # Setup mocks
    mock_os.environ.get.return_value = "false"  # AOS_DISABLED = false
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = False
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Create test event
    event = {
        "requestContext": {
            "http": {"method": "GET"}
        },
        "headers": {
            "Authorization": "Bearer test-token"
        }
    }
    
    # Call the lambda handler
    response = lambda_handler(event, {})
    
    # Assert the response
    assert response["statusCode"] == 403
    response_body = json.loads(response["body"])
    assert response_body["message"] == "Not Authorized"
    
    # Verify mocks were called correctly
    mock_request_to_claims.assert_called_once_with(event)
    mock_casbin_enforcer.assert_called_once()
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once_with(event)


@patch('backend.backend.handlers.search.search.os')
@patch('backend.backend.handlers.search.search.request_to_claims')
@patch('backend.backend.handlers.search.search.CasbinEnforcer')
def test_lambda_handler_aos_disabled(mock_casbin_enforcer, mock_request_to_claims, mock_os):
    """Test lambda_handler with AOS disabled"""
    # Setup mocks
    mock_os.environ.get.return_value = "true"  # AOS_DISABLED = true
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Create test event
    event = {
        "requestContext": {
            "http": {"method": "GET"}
        },
        "headers": {
            "Authorization": "Bearer test-token"
        }
    }
    
    # Call the lambda handler
    response = lambda_handler(event, {})
    
    # Assert the response
    assert response["statusCode"] == 400
    response_body = json.loads(response["body"])
    assert "Search is not available" in response_body["message"]
    
    # Verify mocks were called correctly
    mock_request_to_claims.assert_called_once_with(event)
    mock_casbin_enforcer.assert_called_once()
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once_with(event)


@patch('backend.backend.handlers.search.search.os')
@patch('backend.backend.handlers.search.search.request_to_claims')
@patch('backend.backend.handlers.search.search.CasbinEnforcer')
@patch('backend.backend.handlers.search.search.SearchAOS.from_env')
def test_lambda_handler_missing_body(mock_search_aos_from_env, mock_casbin_enforcer, mock_request_to_claims, mock_os):
    """Test lambda_handler with missing body in POST request"""
    # Setup mocks
    mock_os.environ.get.return_value = "false"  # AOS_DISABLED = false
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Create test event with missing body
    event = {
        "requestContext": {
            "http": {"method": "POST"}
        },
        "headers": {
            "Authorization": "Bearer test-token"
        }
    }
    
    # Call the lambda handler
    response = lambda_handler(event, {})
    
    # Assert the response
    assert response["statusCode"] == 400
    response_body = json.loads(response["body"])
    assert "error" in response_body
    assert "Missing request body" in response_body["error"]
