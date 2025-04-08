# Mock module for pretokengenv1
from unittest.mock import MagicMock
import os

# Mock boto3 resources
dynamodb = MagicMock()
dynamodb.Table = MagicMock(return_value=MagicMock())

# Mock lambda handler
def lambda_handler(event, context):
    """Mock lambda handler for pretokengenv1"""
    return {
        'response': {
            'claimsOverrideDetails': {
                'claimsToAddOrOverride': {
                    'custom:role': 'admin',
                    'custom:permissions': '["read", "write", "delete"]'
                }
            }
        }
    }
