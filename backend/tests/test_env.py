# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import unittest
from unittest.mock import patch, MagicMock

# Import actual implementation
from backend.backend.common import get_ssm_parameter_value

# Set environment variables for testing
os.environ['AWS_REGION'] = 'us-east-1'
os.environ['REGION'] = 'us-east-1'  # Some handlers use REGION instead of AWS_REGION
os.environ['COGNITO_AUTH_ENABLED'] = 'true'
os.environ['COGNITO_AUTH'] = 'cognito-idp.us-east-1.amazonaws.com/us-east-1_example'
os.environ['IDENTITY_POOL_ID'] = 'us-east-1:example'
os.environ['CRED_TOKEN_TIMEOUT_SECONDS'] = '3600'
os.environ['ROLE_ARN'] = 'arn:aws:iam::123456789012:role/example-role'
os.environ['AWS_PARTITION'] = 'aws'
os.environ['KMS_KEY_ARN'] = 'arn:aws:kms:us-east-1:123456789012:key/example-key'
os.environ['S3_BUCKET'] = 'example-bucket'
os.environ['DYNAMODB_TABLE'] = 'example-table'
os.environ['USER_ROLE_TABLE'] = 'example-user-role-table'
os.environ['AUTH_ENT_TABLE'] = 'example-auth-ent-table'
os.environ['USE_LOCAL_MOCKS'] = 'false'
os.environ['USE_EXTERNAL_OAUTH'] = 'false'

# Additional environment variables needed for tests
os.environ['ROLES_TABLE_NAME'] = 'example-roles-table'
os.environ['COMMENT_STORAGE_TABLE_NAME'] = 'commentStorageTable'
os.environ['METADATA_STORAGE_TABLE_NAME'] = 'metadataStorageTable'
os.environ['ASSET_STORAGE_TABLE_NAME'] = 'assetStorageTable'
os.environ['ASSET_BUCKET_NAME'] = 'test-asset-bucket'
os.environ['ASSET_LINKS_STORAGE_TABLE_NAME'] = 'assetLinksStorageTable'
os.environ['TABLE_NAME'] = 'example-constraints-table'
os.environ['AUTH_TABLE_NAME'] = 'example-auth-table'
os.environ['CONSTRAINTS_TABLE_NAME'] = 'example-constraint-table'
os.environ['USER_ROLES_TABLE_NAME'] = 'example-user-roles-table'

# AWS credentials for testing
os.environ['AWS_ACCESS_KEY_ID'] = 'test-access-key'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'test-secret-key'
os.environ['AWS_SESSION_TOKEN'] = 'test-session-token'

# Test parameter for SSM
os.environ['TEST_SSM_PARAM'] = '/test/parameter'


class TestEnvironment(unittest.TestCase):
    """Test environment variables and their usage in the application."""
    
    @patch('boto3.client')
    def test_get_ssm_parameter_value(self, mock_boto3_client):
        """Test that get_ssm_parameter_value correctly uses environment variables."""
        # Setup mock
        mock_ssm = MagicMock()
        mock_boto3_client.return_value = mock_ssm
        mock_ssm.get_parameter.return_value = {
            "Parameter": {
                "Value": "test-parameter-value"
            }
        }
        
        # Call the actual implementation
        result = get_ssm_parameter_value('TEST_SSM_PARAM', 'us-east-1')
        
        # Verify the result
        self.assertEqual(result, "test-parameter-value")
        
        # Verify the mock was called correctly
        mock_boto3_client.assert_called_once_with('ssm', region_name='us-east-1')
        mock_ssm.get_parameter.assert_called_once_with(
            Name='/test/parameter',
            WithDecryption=False
        )
    
    def test_environment_variables_set(self):
        """Test that environment variables are correctly set."""
        self.assertEqual(os.environ['AWS_REGION'], 'us-east-1')
        self.assertEqual(os.environ['COGNITO_AUTH_ENABLED'], 'true')
        self.assertEqual(os.environ['S3_BUCKET'], 'example-bucket')
        self.assertEqual(os.environ['METADATA_STORAGE_TABLE_NAME'], 'metadataStorageTable')


if __name__ == '__main__':
    unittest.main()
