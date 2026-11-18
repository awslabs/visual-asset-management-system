# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The shared Stubber helper rejects what a fake would let through, and serves what a real call needs.

A hand-written fake answers any keyword argument, so a wrong parameter NAME (``SearchCondition`` for
``SearchConditionExpression``) passes offline and fails only against the live service. These tests are
the mutation checks for the helper that closes that gap: each negative -- rejected name, unexpected
value, unconsumed expectation, malformed response -- sits beside the positive control that shows the
same call passing when it is right, so a helper that stopped validating would be caught here first.

Every test that touches ``search_vectors`` needs botocore >= 1.43.64 (tests/common/test_vector_api_floor.py);
below the floor the client has no such method and the failure is an AttributeError, never a skip.
"""

import json

import pytest
from botocore.exceptions import ClientError, ParamValidationError
from botocore.stub import StubAssertionError

from backend.tests.vectorStub import ANY, invoke_model_response, stubbed_bedrock_runtime, stubbed_dynamodb

TABLE = "vector-table"
INDEX = "vec-amazon-titan-embed-text-v2-0-1024"

SEARCH_PARAMS = {
    "TableName": TABLE,
    "IndexName": INDEX,
    "SearchVector": [{"N": "0.5"}, {"N": "0.25"}],
    "TopK": 5,
}
SEARCH_RESPONSE = {
    "SearchResults": [
        {"Item": {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/m.glb#v1"}}, "Score": 0.12}
    ]
}


@pytest.mark.unit
class TestDynamoDbStub:
    def test_matching_call_is_served(self):
        with stubbed_dynamodb([{"method": "search_vectors", "expected_params": SEARCH_PARAMS, "response": SEARCH_RESPONSE}]) as client:
            response = client.search_vectors(**SEARCH_PARAMS)
        assert response["SearchResults"][0]["Score"] == 0.12

    def test_wrong_parameter_name_is_rejected_before_the_stub_is_consulted(self):
        """The reason the helper exists: a fake would accept ``SearchCondition``."""
        with stubbed_dynamodb([{"method": "search_vectors", "expected_params": None, "response": SEARCH_RESPONSE}]) as client:
            with pytest.raises(ParamValidationError):
                client.search_vectors(TableName=TABLE, IndexName=INDEX, SearchVector=[{"N": "0.5"}], TopK=1,
                                      SearchCondition="databaseId = :d")
            client.search_vectors(TableName=TABLE, IndexName=INDEX, SearchVector=[{"N": "0.5"}], TopK=1)

    def test_unexpected_parameter_value_raises(self):
        with pytest.raises(StubAssertionError):
            with stubbed_dynamodb([{"method": "search_vectors", "expected_params": SEARCH_PARAMS, "response": SEARCH_RESPONSE}]) as client:
                client.search_vectors(**{**SEARCH_PARAMS, "TopK": 6})

    def test_unconsumed_expectation_fails_on_exit(self):
        with pytest.raises(StubAssertionError):
            with stubbed_dynamodb([{"method": "search_vectors", "expected_params": SEARCH_PARAMS, "response": SEARCH_RESPONSE}]):
                pass

    def test_exception_inside_the_block_is_not_masked_by_the_pending_check(self):
        with pytest.raises(RuntimeError):
            with stubbed_dynamodb([{"method": "search_vectors", "expected_params": SEARCH_PARAMS, "response": SEARCH_RESPONSE}]):
                raise RuntimeError("the test's own failure must surface, not StubAssertionError")

    def test_error_entry_raises_a_client_error_with_the_code(self):
        calls = [{"method": "search_vectors", "expected_params": SEARCH_PARAMS,
                  "error": {"code": "ValidationException", "message": "The table does not have the specified index", "http_status_code": 400}}]
        with stubbed_dynamodb(calls) as client:
            with pytest.raises(ClientError) as excinfo:
                client.search_vectors(**SEARCH_PARAMS)
        assert excinfo.value.response["Error"]["Code"] == "ValidationException"
        assert "does not have the specified index" in excinfo.value.response["Error"]["Message"]

    def test_error_is_raised_once_not_retried(self):
        calls = [{"method": "search_vectors", "expected_params": SEARCH_PARAMS,
                  "error": {"code": "ThrottlingException", "message": "slow down", "http_status_code": 400}}]
        with stubbed_dynamodb(calls) as client:
            with pytest.raises(ClientError):
                client.search_vectors(**SEARCH_PARAMS)
        # A retrying client would have needed a second stubbed response; the clean exit proves one call.

    def test_malformed_response_is_rejected_when_the_stub_is_built(self):
        bad = {"SearchResults": [{"Item": {}, "Score": "high"}]}
        with pytest.raises(ParamValidationError):
            with stubbed_dynamodb([{"method": "search_vectors", "expected_params": None, "response": bad}]):
                pass

    def test_any_matches_a_parameter_the_test_does_not_pin(self):
        params = {**SEARCH_PARAMS, "SearchVector": ANY}
        with stubbed_dynamodb([{"method": "search_vectors", "expected_params": params, "response": SEARCH_RESPONSE}]) as client:
            client.search_vectors(**{**SEARCH_PARAMS, "SearchVector": [{"N": "0.9"}, {"N": "0.1"}]})

    def test_ordinary_operations_are_stubbed_the_same_way(self):
        key = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/m.glb#v1"}}
        calls = [{"method": "put_item", "expected_params": {"TableName": TABLE, "Item": key}, "response": {}}]
        with stubbed_dynamodb(calls) as client:
            assert client.put_item(TableName=TABLE, Item=key) == {}


@pytest.mark.unit
class TestBedrockRuntimeStub:
    def test_invoke_model_body_streams_the_payload(self):
        body = json.dumps({"inputText": "hello", "dimensions": 256, "normalize": True})
        calls = [{"method": "invoke_model",
                  "expected_params": {"modelId": "amazon.titan-embed-text-v2:0", "contentType": "application/json",
                                      "accept": "application/json", "body": body},
                  "response": invoke_model_response({"embedding": [0.1, 0.2], "inputTextTokenCount": 1})}]
        with stubbed_bedrock_runtime(calls) as client:
            response = client.invoke_model(modelId="amazon.titan-embed-text-v2:0", contentType="application/json",
                                           accept="application/json", body=body)
        assert json.loads(response["body"].read()) == {"embedding": [0.1, 0.2], "inputTextTokenCount": 1}

    def test_wrong_body_is_rejected(self):
        calls = [{"method": "invoke_model", "expected_params": {"modelId": "m", "body": "expected"},
                  "response": invoke_model_response({"embedding": []})}]
        with pytest.raises(StubAssertionError):
            with stubbed_bedrock_runtime(calls) as client:
                client.invoke_model(modelId="m", body="actual")

    def test_bedrock_error_entry(self):
        calls = [{"method": "invoke_model", "expected_params": None,
                  "error": {"code": "AccessDeniedException", "message": "no model access", "http_status_code": 403}}]
        with stubbed_bedrock_runtime(calls) as client:
            with pytest.raises(ClientError) as excinfo:
                client.invoke_model(modelId="m", body="x")
        assert excinfo.value.response["Error"]["Code"] == "AccessDeniedException"

    def test_response_helper_is_a_valid_invoke_model_response(self):
        response = invoke_model_response({"embedding": [1.0]})
        assert response["contentType"] == "application/json"
        assert json.loads(response["body"].read()) == {"embedding": [1.0]}
