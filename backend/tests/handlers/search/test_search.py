# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from backend.handlers.search.search \
    import property_token_filter_to_opensearch_query


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

    result = property_token_filter_to_opensearch_query(example_body)

    assert result['query'] == result_example['query']


def test_example_body_with_query_only():
    result_example = {
        "query": {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "type": "cross_fields",
                            "query": "one two three",
                            "lenient": True,
                        }
                    }
                ],
                "filter": [],
                "should": [],
                "must_not": [],
            }
        },
    }

    example_body = {
        "query": "one two three",
        "operation": "AND"
    }

    result = property_token_filter_to_opensearch_query(example_body)

    assert result['query'] == result_example['query']


def test_example_body():

    result_example = {
        "query": {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "type": "best_fields",
                            "query": "one two three",
                            "lenient": True,
                        }
                    }
                ],
                "filter": [],
                "should": [],
                "must_not": [],
            }
        },
    }

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

    assert result['query'] == result_example['query']


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

    result = property_token_filter_to_opensearch_query(example_body)

    assert result['query']['bool']['must'][0]['multi_match']['query'] \
        == 'one two three'


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

    result = property_token_filter_to_opensearch_query(example_body)

    assert result['query']['bool']['must'][0]['match']['name'] \
        == 'one two three'


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

    result = property_token_filter_to_opensearch_query(example_body)

    assert result['query']['bool']['must'][0]['match']['name'] \
        == 'one two three'
    assert result['query']['bool']['must'][1]['match']['description'] \
        == 'four five six'


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

    result = property_token_filter_to_opensearch_query(example_body)

    assert result['query']['bool']['must'][0]['match']['name'] \
        == 'one two three'
    assert result['query']['bool']['must_not'][0]['match']['description'] \
        == 'four five six'


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

    result = property_token_filter_to_opensearch_query(example_body)

    assert result['query']['bool']['should'][0]['match']['name'] \
        == 'one two three'
    assert result['query']['bool']['should'][1]['match']['description'] \
        == 'four five six'


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

    result = property_token_filter_to_opensearch_query(example_body)

    assert result['query']['bool']['should'][0]['match']['name'] \
        == 'one two three'
    assert result['query']['bool']['must_not'][0]['match']['description'] \
        == 'four five six'


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

    result = property_token_filter_to_opensearch_query(example_body)

    assert result['from'] == 10
    assert result['size'] == 20
