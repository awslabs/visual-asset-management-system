"""
Pytest configuration file for VAMS backend tests.

This file contains fixtures and configuration for pytest tests.
"""

import boto3
import json
import os
import sys
import pytest
from datetime import datetime
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from moto import mock_aws

# Set AWS region and core table env vars BEFORE any handler module is imported below.
# Several handlers create boto3 clients at module load time; without a region this
# fails collection with botocore.exceptions.NoRegionError. The fuller env var set is
# (re)applied further down, but it must exist before the handler imports at the bottom
# of this file run.
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("REGION", "us-east-1")

# Mock the customLogging.logger.safeLogger function
class MockSafeLogger:
    def __init__(self, service=None, service_name=None):
        self.service = service_name if service_name is not None else service
        
    def info(self, message):
        pass
        
    def warning(self, message):
        pass
        
    def error(self, message):
        pass
        
    def exception(self, message):
        pass

# Create a mock safeLogger function that returns a MockSafeLogger instance
def mock_safe_logger(service=None, service_name=None):
    return MockSafeLogger(service, service_name)

# Create mock modules
sys.modules['handlers'] = MagicMock()
sys.modules['handlers.auth'] = MagicMock()
sys.modules['handlers.auth'].request_to_claims = lambda event: {"tokens": ["test_token"]}
sys.modules['handlers.authz'] = MagicMock()
sys.modules['handlers.authz'].CasbinEnforcer = MagicMock()
sys.modules['common'] = MagicMock()
sys.modules['common.validators'] = MagicMock()
sys.modules['common.validators'].validate = lambda params: (True, "")
# Regex pattern constants used in Pydantic Field(regex=...) must be real strings,
# not auto-generated MagicMock attributes; re.compile() rejects a MagicMock.
sys.modules['common.validators'].bucket_existing_key_pattern = r'^[a-zA-Z0-9._\-/]{1,1024}$'
sys.modules['common.dynamodb'] = MagicMock()
sys.modules['common.dynamodb'].get_asset_object_from_id = lambda asset_id: {"assetId": asset_id}
sys.modules['common.constants'] = MagicMock()
sys.modules['common.constants'].STANDARD_JSON_RESPONSE = {
    "statusCode": 200,
    "headers": {
        "Content-Type": "application/json",
        'Cache-Control': 'no-cache, no-store',
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type,Authorization"
    },
    "body": ""
}
# s3MetadataKeys is pure constants (no AWS deps), so load the REAL module by path
# rather than a MagicMock. A bare MagicMock for `common` cannot resolve the
# `from common.s3MetadataKeys import ...` submodule import at collection time.
import importlib.util as _s3mk_importlib_util
_s3mk_spec = _s3mk_importlib_util.spec_from_file_location(
    'common.s3MetadataKeys',
    os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backend', 'common', 's3MetadataKeys.py')
)
_s3mk_module = _s3mk_importlib_util.module_from_spec(_s3mk_spec)
_s3mk_spec.loader.exec_module(_s3mk_module)
sys.modules['common.s3MetadataKeys'] = _s3mk_module
# s3PathPatterns is pure constants (no AWS deps), so load the REAL module by path
# rather than a MagicMock (same approach as s3MetadataKeys above).
_s3pp_spec = _s3mk_importlib_util.spec_from_file_location(
    'common.s3PathPatterns',
    os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backend', 'common', 's3PathPatterns.py')
)
_s3pp_module = _s3mk_importlib_util.module_from_spec(_s3pp_spec)
_s3pp_spec.loader.exec_module(_s3pp_module)
sys.modules['common.s3PathPatterns'] = _s3pp_module
# dynamoDbMetadataKeys is pure constants (no AWS deps), so load the REAL module
# by path rather than a MagicMock (same approach as s3MetadataKeys above).
_ddbmk_spec = _s3mk_importlib_util.spec_from_file_location(
    'common.dynamoDbMetadataKeys',
    os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backend', 'common', 'dynamoDbMetadataKeys.py')
)
_ddbmk_module = _s3mk_importlib_util.module_from_spec(_ddbmk_spec)
_ddbmk_spec.loader.exec_module(_ddbmk_module)
sys.modules['common.dynamoDbMetadataKeys'] = _ddbmk_module
# apiRoutes is pure constants (no AWS deps), so load the REAL module by path
# rather than a MagicMock (same approach as s3MetadataKeys above).
_apir_spec = _s3mk_importlib_util.spec_from_file_location(
    'common.apiRoutes',
    os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backend', 'common', 'apiRoutes.py')
)
_apir_module = _s3mk_importlib_util.module_from_spec(_apir_spec)
_apir_spec.loader.exec_module(_apir_module)
sys.modules['common.apiRoutes'] = _apir_module
# s3 is a simple validation module with no AWS side effects at import, but it is not
# in the root conftest mock layer. Load the mock s3 module by path so tests that import
# handlers which depend on common.s3 can collect cleanly.
_s3_spec = _s3mk_importlib_util.spec_from_file_location(
    'common.s3',
    os.path.join(os.path.dirname(__file__), 'mocks', 'common', 's3.py')
)
_s3_module = _s3mk_importlib_util.module_from_spec(_s3_spec)
_s3_spec.loader.exec_module(_s3_module)
sys.modules['common.s3'] = _s3_module
# resourceNames resolves table/bucket/log-group names (env-var override first, SSM
# second). Handlers import it at module level, so it must be resolvable at collection
# time. Load the REAL module by path — its boto3 ssm client is created lazily and the
# conftest env vars satisfy the override path, so no AWS call happens during tests.
_rn_spec = _s3mk_importlib_util.spec_from_file_location(
    'common.resourceNames',
    os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backend', 'common', 'resourceNames.py')
)
_rn_module = _s3mk_importlib_util.module_from_spec(_rn_spec)
_rn_spec.loader.exec_module(_rn_module)
sys.modules['common.resourceNames'] = _rn_module
# handlers.assets.assetVersions is imported by assetFiles and metadataService at module load.
# Create a minimal mock package hierarchy: handlers → handlers.assets → handlers.assets.assetVersions
if 'handlers' not in sys.modules:
    sys.modules['handlers'] = MagicMock()
if 'handlers.assets' not in sys.modules:
    _h_assets = MagicMock()
    _h_assets.__name__ = 'handlers.assets'
    _h_assets.__package__ = 'handlers.assets'
    sys.modules['handlers.assets'] = _h_assets
_h_assetVersions = MagicMock()
_h_assetVersions.validate_asset_version_exists = MagicMock()
_h_assetVersions.get_all_asset_versions = MagicMock()
_h_assetVersions.get_asset_metadata_version = MagicMock()
sys.modules['handlers.assets.assetVersions'] = _h_assetVersions
# Load the real mock customLogging package (tests/mocks/customLogging) instead of a bare
# MagicMock. A bare MagicMock has no real submodules, so any handler that does
# `from customLogging.auditLogging import ...` at import time fails collection with
# "No module named 'customLogging.auditLogging'; 'customLogging' is not a package".
# The mock package provides logger.safeLogger, logger.mask_sensitive_data, and
# auditLogging no-ops. Loaded by file path so it is independent of sys.path ordering.
import importlib.util as _importlib_util


def _load_mock_module(_module_name, _file_path, _is_package=False):
    _search = [os.path.dirname(_file_path)] if _is_package else None
    _spec = _importlib_util.spec_from_file_location(
        _module_name, _file_path, submodule_search_locations=_search
    )
    _module = _importlib_util.module_from_spec(_spec)
    sys.modules[_module_name] = _module
    _spec.loader.exec_module(_module)
    return _module


_mocks_customlogging = os.path.join(os.path.dirname(__file__), 'mocks', 'customLogging')
if os.path.isdir(_mocks_customlogging):
    _load_mock_module('customLogging', os.path.join(_mocks_customlogging, '__init__.py'), _is_package=True)
    _load_mock_module('customLogging.logger', os.path.join(_mocks_customlogging, 'logger.py'))
    _load_mock_module('customLogging.auditLogging', os.path.join(_mocks_customlogging, 'auditLogging.py'))
    # Keep the previously-used override so existing tests relying on this exact callable still work.
    sys.modules['customLogging.logger'].safeLogger = mock_safe_logger
else:
    # Fallback to the previous behavior if the mock package is missing.
    sys.modules['customLogging'] = MagicMock()
    sys.modules['customLogging.logger'] = MagicMock()
    sys.modules['customLogging.logger'].safeLogger = mock_safe_logger
sys.modules['customConfigCommon'] = MagicMock()
sys.modules['customConfigCommon.customAuthClaimsCheck'] = MagicMock()
sys.modules['customConfigCommon.customAuthClaimsCheck'].customAuthClaimsCheckOverride = lambda claims_and_roles, request: claims_and_roles

# Add missing mock modules
sys.modules['customConfigCommon.customAuthLoginProfile'] = MagicMock()
sys.modules['customConfigCommon.customAuthLoginProfile'].customAuthProfileLoginWriteOverride = lambda event, claims: event

sys.modules['handlers.metadata'] = MagicMock()
sys.modules['handlers.metadata'].build_response = lambda status_code, body: {"statusCode": status_code, "body": json.dumps(body)}
sys.modules['handlers.metadata'].create_or_update = MagicMock()
sys.modules['handlers.metadata'].validate_event = MagicMock(return_value=(True, None))
sys.modules['handlers.metadata'].validate_body = MagicMock(return_value=(True, None))
sys.modules['handlers.metadata'].ValidationError = type('ValidationError', (Exception,), {})

sys.modules['handlers.workflows'] = MagicMock()
sys.modules['handlers.workflows'].update_pipeline_workflows = MagicMock()

# Add the necessary paths to the Python path
sys.path.append(os.path.abspath('.'))
sys.path.append(os.path.abspath('..'))
sys.path.append(os.path.abspath('backend'))
sys.path.append(os.path.abspath('backend/backend'))
sys.path.append(os.path.abspath('tests/mocks'))

# Import test utilities
from backend.tests.utils.lambda_test_utils import (
    LambdaContext,
    APIGatewayEvent,
    mock_aws_service
)
from backend.tests.utils.api_test_utils import (
    create_cognito_auth_claims,
    create_api_gateway_event_with_auth
)

# Set default environment variables for tests
os.environ["COMMENT_STORAGE_TABLE_NAME"] = "commentStorageTable"
os.environ["METADATA_STORAGE_TABLE_NAME"] = "metadataStorageTable"
os.environ["ASSET_STORAGE_TABLE_NAME"] = "assetStorageTable"
os.environ["ASSET_BUCKET_NAME"] = "test-asset-bucket"
os.environ["REGION"] = "us-east-1"
os.environ["AWS_REGION"] = "us-east-1"

# Add new environment variables required by updated handlers
os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"] = "s3AssetBucketsStorageTable"
os.environ["DATABASE_STORAGE_TABLE_NAME"] = "databaseStorageTable"
os.environ["S3_ASSET_AUXILIARY_BUCKET"] = "test-asset-auxiliary-bucket"
os.environ["ASSET_UPLOAD_TABLE_NAME"] = "assetUploadTable"
os.environ["ASSET_LINKS_STORAGE_TABLE_NAME"] = "assetLinksStorageTable"
os.environ["ASSET_VERSIONS_STORAGE_TABLE_NAME"] = "assetVersionsStorageTable"
os.environ["ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME"] = "assetFileVersionsStorageTable"
os.environ["SUBSCRIPTIONS_STORAGE_TABLE_NAME"] = "subscriptionsStorageTable"
os.environ["SEND_EMAIL_FUNCTION_NAME"] = "sendEmailFunction"

# Add environment variables for ingestAsset.py
os.environ["CREATE_ASSET_LAMBDA_FUNCTION_NAME"] = "createAssetLambdaFunction"
os.environ["FILE_UPLOAD_LAMBDA_FUNCTION_NAME"] = "fileUploadLambdaFunction"

# Add environment variables for databaseService.py
os.environ["WORKFLOW_STORAGE_TABLE_NAME"] = "workflowStorageTable"
os.environ["PIPELINE_STORAGE_TABLE_NAME"] = "pipelineStorageTable"

# Add environment variables for assetLinksService.py
os.environ["ASSET_LINKS_STORAGE_TABLE_V2_NAME"] = "assetLinksStorageTableV2"
os.environ["ASSET_LINKS_METADATA_STORAGE_TABLE_NAME"] = "assetLinksMetadataStorageTable"
os.environ["AUTH_TABLE_NAME"] = "authTable"
os.environ['CONSTRAINTS_TABLE_NAME'] = 'constraintTable'
os.environ["USER_ROLES_TABLE_NAME"] = "userRolesTable"
os.environ["ROLES_TABLE_NAME"] = "rolesTable"

# Tag / tag type tables (tags + tagTypes handlers read these at module load)
os.environ["TAGS_STORAGE_TABLE_NAME"] = "tagsTable"
os.environ["TAG_TYPES_STORAGE_TABLE_NAME"] = "tagTypesTable"

# Metadata service tables (metadataService reads these at module load)
os.environ["DATABASE_METADATA_STORAGE_TABLE_NAME"] = "databaseMetadataTable"
os.environ["ASSET_FILE_METADATA_STORAGE_TABLE_NAME"] = "assetFileMetadataTable"
os.environ["FILE_ATTRIBUTE_STORAGE_TABLE_NAME"] = "fileAttributeTable"
os.environ["METADATA_SCHEMA_STORAGE_TABLE_V2_NAME"] = "metadataSchemaTableV2"


@pytest.fixture(scope="function")
def lambda_context():
    """
    Fixture that provides a mock Lambda context object.
    
    Returns:
        LambdaContext: A mock Lambda context object
    """
    return LambdaContext()


@pytest.fixture(scope="function")
def api_gateway_event():
    """
    Fixture that provides a function to create API Gateway events.
    
    Returns:
        function: A function that creates API Gateway events
    """
    def _create_event(
        method="GET",
        path="/",
        headers=None,
        query_params=None,
        path_params=None,
        body=None,
        cognito_auth=None
    ):
        return APIGatewayEvent(
            method=method,
            path=path,
            headers=headers,
            query_params=query_params,
            path_params=path_params,
            body=body,
            cognito_auth=cognito_auth
        ).build()
    
    return _create_event


@pytest.fixture(scope="function")
def mock_env_vars():
    """
    Fixture to mock environment variables for testing.
    
    Usage:
        def test_something(mock_env_vars):
            mock_env_vars({
                "TABLE_NAME": "test-table",
                "REGION": "us-east-1"
            })
            # Test code that uses these environment variables
    """
    original_environ = dict(os.environ)
    
    def _set_env_vars(env_vars: Dict[str, str]):
        os.environ.update(env_vars)
        
    yield _set_env_vars
    
    # Restore original environment variables
    os.environ.clear()
    os.environ.update(original_environ)


@pytest.fixture(scope="function")
def ddb_resource():
    """
    Create the dynamoDB resource for testing
    
    Returns:
        boto3.resource: Mocked DynamoDB resource
    """
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="us-east-1")


@pytest.fixture(scope="function")
def s3_resource():
    """
    Create the S3 resource for testing
    
    Returns:
        boto3.resource: Mocked S3 resource
    """
    with mock_aws():
        yield boto3.resource("s3", region_name="us-east-1")


@pytest.fixture(scope="function")
def s3_client():
    """
    Create the S3 client for testing
    
    Returns:
        boto3.client: Mocked S3 client
    """
    with mock_aws():
        yield boto3.client("s3", region_name="us-east-1")


@pytest.fixture(scope="function")
def sfn_client():
    """
    Create the Step Functions client for testing
    
    Returns:
        boto3.client: Mocked Step Functions client
    """
    with mock_aws():
        yield boto3.client("stepfunctions", region_name="us-east-1")


@pytest.fixture(scope="function")
def opensearch_client():
    """
    Create the OpenSearch client for testing
    
    Returns:
        boto3.client: Mocked OpenSearch client
    """
    with mock_aws():
        yield boto3.client("opensearch", region_name="us-east-1")


@pytest.fixture(scope="function")
def comments_table(ddb_resource):
    """
    Create a table to store comments for testing
    
    Args:
        ddb_resource: Mocked DynamoDB resource
        
    Returns:
        boto3.resource.Table: Mocked comments table
    """
    comment_table_name = "commentStorageTable"
    comments_table = ddb_resource.create_table(
        TableName=comment_table_name,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "assetId", "KeyType": "HASH"},
            {"AttributeName": "assetVersionId:commentId", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "assetId", "AttributeType": "S"},
            {"AttributeName": "assetVersionId:commentId", "AttributeType": "S"},
        ],
    )

    return comments_table


@pytest.fixture(scope="function")
def metadata_table(ddb_resource):
    """
    Create a table to store metadata for testing
    
    Args:
        ddb_resource: Mocked DynamoDB resource
        
    Returns:
        boto3.resource.Table: Mocked metadata table
    """
    metadata_table_name = "metadataStorageTable"
    metadata_table = ddb_resource.create_table(
        TableName=metadata_table_name,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "databaseId", "KeyType": "HASH"},
            {"AttributeName": "assetId", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "databaseId", "AttributeType": "S"},
            {"AttributeName": "assetId", "AttributeType": "S"},
        ],
    )

    return metadata_table


@pytest.fixture(scope="function")
def asset_table(ddb_resource):
    """
    Create a table to store assets for testing
    
    Args:
        ddb_resource: Mocked DynamoDB resource
        
    Returns:
        boto3.resource.Table: Mocked asset table
    """
    asset_table_name = "assetStorageTable"
    asset_table = ddb_resource.create_table(
        TableName=asset_table_name,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "databaseId", "KeyType": "HASH"},
            {"AttributeName": "assetId", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "databaseId", "AttributeType": "S"},
            {"AttributeName": "assetId", "AttributeType": "S"},
        ],
    )

    return asset_table


@pytest.fixture(scope="function")
def asset_bucket(s3_resource):
    """
    Create an S3 bucket for storing assets
    
    Args:
        s3_resource: Mocked S3 resource
        
    Returns:
        boto3.resource.Bucket: Mocked S3 bucket
    """
    bucket_name = "test-asset-bucket"
    bucket = s3_resource.create_bucket(Bucket=bucket_name)
    return bucket


@pytest.fixture(scope="function")
def mock_cognito_auth():
    """
    Fixture to create mock Cognito authentication claims
    
    Returns:
        function: A function that creates Cognito authentication claims
    """
    def _create_auth(
        sub="test-user-id",
        email="test@example.com",
        username="testuser",
        groups=None,
        custom_attributes=None
    ):
        return create_cognito_auth_claims(
            sub=sub,
            email=email,
            username=username,
            groups=groups,
            custom_attributes=custom_attributes
        )
    
    return _create_auth


class TestComment:
    """
    Class to easily create comments for testing purposes
    """

    __test__ = False

    def __init__(
        self,
        asset_id="test-id",
        asset_version_id_and_comment_id="test-version-id:test-comment-id",
        comment_body="test comment body",
        comment_owner_id="test_email@amazon.com",
        comment_owner_username="test_email@amazon.com",
        date_created="2023-07-06T21:32:15.066148Z",
    ) -> None:
        """
        Creates a test_comment object based on the specified parameters
        
        Args:
            asset_id: Asset ID for the test comment to be attached to
            asset_version_id_and_comment_id: Version ID for the comment to be attached to and
                unique comment identifier
            comment_body: Body of the comment
            comment_owner_id: Cognito sub for the creator of the comment
            comment_owner_username: Cognito username for the creator of the comment
            date_created: Date the comment was created
        """
        self.asset_id = asset_id
        self.asset_version_id_and_comment_id = asset_version_id_and_comment_id
        self.comment_body = comment_body
        self.comment_owner_id = comment_owner_id
        self.comment_owner_username = comment_owner_username
        self.date_created = date_created

    def get_comment(self):
        """
        Return a dict that can be converted to JSON based on the comment information stored in the class
        
        Returns:
            dict: Dictionary representation of the test comment
        """
        return {
            "assetId": self.asset_id,
            "assetVersionId:commentId": self.asset_version_id_and_comment_id,
            "commentBody": self.comment_body,
            "commentOwnerID": self.comment_owner_id,
            "commentOwnerUsername": self.comment_owner_username,
            "dateCreated": self.date_created,
        }


class TestAsset:
    """
    Class to easily create assets for testing purposes
    """
    
    __test__ = False
    
    def __init__(
        self,
        asset_id="test-asset-id",
        database_id="test-database-id",
        asset_name="Test Asset",
        asset_type="model/gltf-binary",
        asset_size=1024,
        asset_owner_id="test_email@amazon.com",
        asset_owner_username="test_email@amazon.com",
        date_created=None,
        date_modified=None,
        metadata=None
    ) -> None:
        """
        Creates a test asset object based on the specified parameters
        
        Args:
            asset_id: Asset ID
            database_id: Database ID
            asset_name: Asset name
            asset_type: Asset MIME type
            asset_size: Asset size in bytes
            asset_owner_id: Cognito sub for the creator of the asset
            asset_owner_username: Cognito username for the creator of the asset
            date_created: Date the asset was created
            date_modified: Date the asset was last modified
            metadata: Additional metadata for the asset
        """
        self.asset_id = asset_id
        self.database_id = database_id
        self.asset_name = asset_name
        self.asset_type = asset_type
        self.asset_size = asset_size
        self.asset_owner_id = asset_owner_id
        self.asset_owner_username = asset_owner_username
        self.date_created = date_created or datetime.now().isoformat()
        self.date_modified = date_modified or self.date_created
        self.metadata = metadata or {}
        
    def get_asset(self):
        """
        Return a dict that can be converted to JSON based on the asset information stored in the class
        
        Returns:
            dict: Dictionary representation of the test asset
        """
        asset = {
            "assetId": self.asset_id,
            "databaseId": self.database_id,
            "assetName": self.asset_name,
            "assetType": self.asset_type,
            "assetSize": self.asset_size,
            "assetOwnerID": self.asset_owner_id,
            "assetOwnerUsername": self.asset_owner_username,
            "dateCreated": self.date_created,
            "dateModified": self.date_modified
        }
        
        # Add metadata if provided
        if self.metadata:
            asset.update(self.metadata)
            
        return asset


@pytest.fixture(scope="function")
def mock_lambda_client():
    """
    Fixture to mock the Lambda client
    
    Returns:
        MagicMock: Mocked Lambda client
    """
    mock_client = MagicMock()
    mock_client.invoke.return_value = {
        "StatusCode": 200,
        "Payload": MagicMock(
            read=MagicMock(
                return_value=json.dumps(
                    {"statusCode": 200, "body": json.dumps({"message": "Success"})}
                ).encode()
            )
        )
    }
    
    with patch("boto3.client", return_value=mock_client):
        yield mock_client
