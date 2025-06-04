# VAMS Backend Test Framework

This directory contains the test framework for the VAMS backend. The framework is built using pytest and provides utilities for testing AWS Lambda functions and API Gateway endpoints.

## Test Structure

The tests are organized by resource type:

-   `tests/functions/`: Tests for AWS Lambda functions
-   `tests/handlers/`: Tests for API Gateway handlers
-   `tests/models/`: Tests for data models

## Test Utilities

The test framework provides several utilities to help with testing:

### Lambda Test Utilities (`utils/lambda_test_utils.py`)

This module provides utilities for testing AWS Lambda functions:

-   `LambdaContext`: A mock AWS Lambda context object
-   `APIGatewayEvent`: A builder for API Gateway event objects
-   `DynamoDBHelper`: A helper class for setting up DynamoDB tables
-   `mock_aws_service`: A decorator to mock AWS services
-   `create_lambda_response`: A function to create standardized Lambda responses
-   `assert_lambda_response`: A function to assert that a Lambda response matches expected values
-   `MockLambdaInvoker`: A helper class for mocking Lambda invocations

### API Test Utilities (`utils/api_test_utils.py`)

This module provides utilities for testing API endpoints:

-   `APITestCase`: A base class for API endpoint test cases
-   `DynamoDBTestCase`: A base class for API endpoint test cases that use DynamoDB
-   `S3TestCase`: A base class for API endpoint test cases that use S3
-   `CombinedTestCase`: A base class for API endpoint test cases that use both DynamoDB and S3
-   `create_cognito_auth_claims`: A function to create Cognito authentication claims
-   `create_api_gateway_event_with_auth`: A function to create API Gateway events with Cognito authentication

## Test Fixtures

The test framework provides several fixtures in `conftest.py`:

-   `lambda_context`: A fixture that provides a mock Lambda context object
-   `api_gateway_event`: A fixture that provides a function to create API Gateway events
-   `mock_env_vars`: A fixture to mock environment variables
-   `ddb_resource`: A fixture that provides a mocked DynamoDB resource
-   `s3_resource`: A fixture that provides a mocked S3 resource
-   `s3_client`: A fixture that provides a mocked S3 client
-   `sfn_client`: A fixture that provides a mocked Step Functions client
-   `opensearch_client`: A fixture that provides a mocked OpenSearch client
-   `comments_table`: A fixture that provides a mocked comments table
-   `metadata_table`: A fixture that provides a mocked metadata table
-   `asset_table`: A fixture that provides a mocked asset table
-   `asset_bucket`: A fixture that provides a mocked S3 bucket for assets
-   `mock_cognito_auth`: A fixture to create mock Cognito authentication claims
-   `mock_lambda_client`: A fixture to mock the Lambda client

## Test Classes

The test framework provides several test classes in `conftest.py`:

-   `TestComment`: A class to easily create comments for testing
-   `TestAsset`: A class to easily create assets for testing

## Running Tests

To run the tests, use the following commands:

```bash
# Run all tests
poetry run pytest

# Run tests with verbose output
poetry run pytest -v

# Run tests with coverage
poetry run coverage run -m pytest
poetry run coverage report

# Run tests in watch mode
poetry run ptw

# Run specific tests
poetry run pytest tests/functions/assets/upload_asset_workflow/test_lambda_handler.py
```

## Test Markers

The test framework provides several markers to categorize tests:

-   `unit`: Marks tests as unit tests
-   `integration`: Marks tests as integration tests
-   `slow`: Marks tests as slow (skipped by default)
-   `aws`: Marks tests that interact with AWS services

To run tests with a specific marker:

```bash
# Run unit tests
poetry run pytest -m unit

# Run integration tests
poetry run pytest -m integration

# Run slow tests
poetry run pytest -m slow

# Run AWS tests
poetry run pytest -m aws
```

## Writing Tests

### Testing Lambda Functions

To test a Lambda function, use the `lambda_context` fixture and the `APIGatewayEvent` class:

```python
def test_lambda_handler(lambda_context):
    # Create an API Gateway event
    event = APIGatewayEvent(
        method="GET",
        path="/assets",
        query_params={"databaseId": "test-database-id"}
    ).build()

    # Invoke the Lambda handler
    response = lambda_handler(event, lambda_context)

    # Assert the response
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"message": "Success"}
```

### Testing API Endpoints

To test an API endpoint, use the `APITestCase` class:

```python
class TestAssetService(APITestCase):
    def test_get_assets(self, api_gateway_event, lambda_context):
        # Create an API Gateway event
        event = api_gateway_event(
            method="GET",
            path="/assets",
            query_params={"databaseId": "test-database-id"}
        )

        # Invoke the Lambda handler
        response = asset_service.lambda_handler(event, lambda_context)

        # Assert the response
        self.assert_response(
            response,
            expected_status_code=200,
            expected_body={"message": "Success"}
        )
```

### Testing with DynamoDB

To test with DynamoDB, use the `DynamoDBTestCase` class:

```python
class TestAssetService(DynamoDBTestCase):
    # Define tables to create
    TABLES = [
        {
            "table_name": "assetStorageTable",
            "key_schema": [
                {"AttributeName": "databaseId", "KeyType": "HASH"},
                {"AttributeName": "assetId", "KeyType": "RANGE"}
            ],
            "attribute_definitions": [
                {"AttributeName": "databaseId", "AttributeType": "S"},
                {"AttributeName": "assetId", "AttributeType": "S"}
            ]
        }
    ]

    def test_get_asset(self, api_gateway_event, lambda_context):
        # Create test data
        self.put_item("assetStorageTable", {
            "databaseId": "test-database-id",
            "assetId": "test-asset-id",
            "assetName": "Test Asset"
        })

        # Create an API Gateway event
        event = api_gateway_event(
            method="GET",
            path="/assets/{assetId}",
            path_params={"assetId": "test-asset-id"},
            query_params={"databaseId": "test-database-id"}
        )

        # Invoke the Lambda handler
        response = asset_service.lambda_handler(event, lambda_context)

        # Assert the response
        self.assert_response(
            response,
            expected_status_code=200,
            expected_body={"assetId": "test-asset-id", "assetName": "Test Asset"}
        )
```

### Testing with S3

To test with S3, use the `S3TestCase` class:

```python
class TestAssetService(S3TestCase):
    # Define buckets to create
    BUCKETS = ["test-asset-bucket"]

    def test_get_asset_file(self, api_gateway_event, lambda_context):
        # Create test data
        self.put_object(
            "test-asset-bucket",
            "test-asset-id/file.glb",
            b"test data",
            {"assetId": "test-asset-id"}
        )

        # Create an API Gateway event
        event = api_gateway_event(
            method="GET",
            path="/assets/{assetId}/file",
            path_params={"assetId": "test-asset-id"}
        )

        # Invoke the Lambda handler
        response = asset_service.lambda_handler(event, lambda_context)

        # Assert the response
        self.assert_response(
            response,
            expected_status_code=200,
            expected_body={"url": "https://test-asset-bucket.s3.amazonaws.com/test-asset-id/file.glb"}
        )
```

### Testing with Both DynamoDB and S3

To test with both DynamoDB and S3, use the `CombinedTestCase` class:

```python
class TestAssetService(CombinedTestCase):
    # Define tables to create
    TABLES = [
        {
            "table_name": "assetStorageTable",
            "key_schema": [
                {"AttributeName": "databaseId", "KeyType": "HASH"},
                {"AttributeName": "assetId", "KeyType": "RANGE"}
            ],
            "attribute_definitions": [
                {"AttributeName": "databaseId", "AttributeType": "S"},
                {"AttributeName": "assetId", "AttributeType": "S"}
            ]
        }
    ]

    # Define buckets to create
    BUCKETS = ["test-asset-bucket"]

    def test_upload_asset(self, api_gateway_event, lambda_context):
        # Create an API Gateway event
        event = api_gateway_event(
            method="POST",
            path="/assets",
            body={
                "databaseId": "test-database-id",
                "assetName": "Test Asset",
                "assetType": "model/gltf-binary"
            }
        )

        # Invoke the Lambda handler
        response = asset_service.lambda_handler(event, lambda_context)

        # Assert the response
        self.assert_response(
            response,
            expected_status_code=200
        )

        # Assert that the asset was created in DynamoDB
        asset = self.get_item(
            "assetStorageTable",
            {"databaseId": "test-database-id", "assetId": response["body"]["assetId"]}
        )
        assert asset["assetName"] == "Test Asset"

        # Assert that the asset was created in S3
        objects = self.list_objects("test-asset-bucket", prefix=response["body"]["assetId"])
        assert len(objects) == 1
```
