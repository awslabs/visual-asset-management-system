import pytest
import json
import boto3
from unittest.mock import patch, MagicMock

@pytest.fixture(scope="function")
def send_email_event():
    """
    Generates an event for the sendEmail lambda function
    
    Returns:
        dict: Lambda event dictionary for sending an email
    """
    return {
        "asset_id": "test-asset-id"
    }

@pytest.fixture(scope="function")
def mock_dynamodb_scan():
    """
    Mock for DynamoDB scan operation
    
    Returns:
        MagicMock: Mock for DynamoDB scan
    """
    mock_scan = MagicMock()
    mock_scan.return_value = {
        "Items": [
            {
                "assetId": {"S": "test-asset-id"},
                "assetName": {"S": "Test Asset"},
                "snsTopic": {"S": "arn:aws:sns:us-east-1:123456789012:test-topic"},
                "description": {"S": "Test Description"},
                "currentVersion": {
                    "M": {
                        "Version": {"S": "1.0.0"},
                        "DateModified": {"S": "2023-07-06T21:32:15.066148Z"}
                    }
                }
            }
        ]
    }
    return mock_scan

@pytest.fixture(scope="function")
def mock_sns_publish():
    """
    Mock for SNS publish operation
    
    Returns:
        MagicMock: Mock for SNS publish
    """
    mock_publish = MagicMock()
    mock_publish.return_value = {
        "MessageId": "test-message-id"
    }
    return mock_publish

def test_send_email_success(send_email_event, mock_dynamodb_scan, mock_sns_publish, monkeypatch):
    """
    Test the sendEmail lambda handler with a successful email send
    
    Args:
        send_email_event: Lambda event dictionary for sending an email
        mock_dynamodb_scan: Mock for DynamoDB scan
        mock_sns_publish: Mock for SNS publish
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
    
    # Mock boto3 clients
    mock_dynamodb_client = MagicMock()
    mock_dynamodb_client.scan = mock_dynamodb_scan
    
    mock_sns_client = MagicMock()
    mock_sns_client.publish = mock_sns_publish
    
    # Patch boto3.client to return our mocks
    with patch("boto3.client", side_effect=lambda service, **kwargs: 
               mock_dynamodb_client if service == "dynamodb" else mock_sns_client):
        # Import the module here to ensure our mocks are in place
        from handlers.sendEmail import sendEmail
        
        # Call the lambda handler
        response = sendEmail.lambda_handler(send_email_event, None)
        
        # Verify the response
        assert response["statusCode"] == 200
        assert json.loads(response["body"])["message"] == "Email sent successfully"
        
        # Verify DynamoDB scan was called with correct parameters
        mock_dynamodb_scan.assert_called_once_with(
            TableName="test-asset-table",
            ProjectionExpression='assetId, assetName, snsTopic, description, currentVersion',
            FilterExpression='assetId = :asset_id',
            ExpressionAttributeValues={':asset_id': {'S': 'test-asset-id'}},
        )
        
        # Verify SNS publish was called with correct parameters
        mock_sns_publish.assert_called_once()
        args, kwargs = mock_sns_publish.call_args
        assert kwargs["TopicArn"] == "arn:aws:sns:us-east-1:123456789012:test-topic"
        assert "Test Asset" in kwargs["Subject"]
        assert "1.0.0" in kwargs["Subject"]
        assert "Test Asset" in kwargs["Message"]
        assert "1.0.0" in kwargs["Message"]
        assert "2023-07-06T21:32:15.066148Z" in kwargs["Message"]

def test_send_email_asset_not_found(send_email_event, monkeypatch):
    """
    Test the sendEmail lambda handler when the asset is not found
    
    Args:
        send_email_event: Lambda event dictionary for sending an email
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
    
    # Mock boto3 clients
    mock_dynamodb_client = MagicMock()
    mock_dynamodb_client.scan.return_value = {"Items": []}
    
    mock_sns_client = MagicMock()
    
    # Patch boto3.client to return our mocks
    with patch("boto3.client", side_effect=lambda service, **kwargs: 
               mock_dynamodb_client if service == "dynamodb" else mock_sns_client):
        # Import the module here to ensure our mocks are in place
        from handlers.sendEmail import sendEmail
        
        # Call the lambda handler
        response = sendEmail.lambda_handler(send_email_event, None)
        
        # Verify the response
        assert response["statusCode"] == 400
        assert "doesn't exits" in json.loads(response["body"])["message"]
        
        # Verify SNS publish was not called
        mock_sns_client.publish.assert_not_called()

def test_send_email_dynamodb_error(send_email_event, monkeypatch):
    """
    Test the sendEmail lambda handler when DynamoDB throws an error
    
    Args:
        send_email_event: Lambda event dictionary for sending an email
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
    
    # Mock boto3 clients
    mock_dynamodb_client = MagicMock()
    mock_dynamodb_client.scan.side_effect = Exception("DynamoDB error")
    
    mock_sns_client = MagicMock()
    
    # Patch boto3.client to return our mocks
    with patch("boto3.client", side_effect=lambda service, **kwargs: 
               mock_dynamodb_client if service == "dynamodb" else mock_sns_client):
        # Import the module here to ensure our mocks are in place
        from handlers.sendEmail import sendEmail
        
        # Call the lambda handler
        response = sendEmail.lambda_handler(send_email_event, None)
        
        # Verify the response
        assert response["statusCode"] == 500
        assert json.loads(response["body"])["message"] == "Internal Server Error"
        
        # Verify SNS publish was not called
        mock_sns_client.publish.assert_not_called()

def test_send_email_sns_error(send_email_event, mock_dynamodb_scan, monkeypatch):
    """
    Test the sendEmail lambda handler when SNS throws an error
    
    Args:
        send_email_event: Lambda event dictionary for sending an email
        mock_dynamodb_scan: Mock for DynamoDB scan
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
    
    # Mock boto3 clients
    mock_dynamodb_client = MagicMock()
    mock_dynamodb_client.scan = mock_dynamodb_scan
    
    mock_sns_client = MagicMock()
    mock_sns_client.publish.side_effect = Exception("SNS error")
    
    # Patch boto3.client to return our mocks
    with patch("boto3.client", side_effect=lambda service, **kwargs: 
               mock_dynamodb_client if service == "dynamodb" else mock_sns_client):
        # Import the module here to ensure our mocks are in place
        from handlers.sendEmail import sendEmail
        
        # Call the lambda handler
        response = sendEmail.lambda_handler(send_email_event, None)
        
        # Verify the response
        assert response["statusCode"] == 500
        assert json.loads(response["body"])["message"] == "Internal Server Error"
