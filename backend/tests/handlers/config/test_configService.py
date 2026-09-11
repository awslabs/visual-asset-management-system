# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
import json
from unittest.mock import MagicMock, patch

from backend.backend.handlers.config import configService


@pytest.fixture(scope="function")
def config_event():
    """API Gateway event for GET /secure-config"""
    return {
        "requestContext": {
            "http": {
                "method": "GET",
                "path": "/secure-config"
            }
        },
        "queryStringParameters": {},
        "headers": {
            "authorization": "Bearer test-token"
        }
    }


def _make_paginator(items):
    """Build a mock DynamoDB scan paginator returning the given low-level items"""
    mock_paginator = MagicMock()
    mock_paginate = MagicMock()
    mock_paginate.build_full_result.return_value = {"Items": items}
    mock_paginator.paginate.return_value = mock_paginate
    return mock_paginator


@pytest.mark.unit
class TestConfigService:
    """Unit tests for the configService lambda handler"""

    def test_get_secure_config_success(self, config_event):
        """Successful retrieval returns feature flags and empty optional URLs"""
        mock_dynamodb_client = MagicMock()
        mock_dynamodb_client.get_paginator.return_value = _make_paginator([
            {"featureName": {"S": "feature1"}, "enabled": {"BOOL": True}},
            {"featureName": {"S": "feature2"}, "enabled": {"BOOL": False}},
        ])

        mock_enforcer = MagicMock()
        mock_enforcer.enforceAPI.return_value = True

        with patch.object(configService, "dynamodb_client", mock_dynamodb_client), \
             patch.object(configService, "CasbinEnforcer", return_value=mock_enforcer), \
             patch.object(configService, "request_to_claims", return_value={"tokens": ["test-user"], "roles": []}), \
             patch.object(configService, "location_service_api_key_arn_ssm_param", None), \
             patch.object(configService, "web_deployed_url_ssm_param", None):
            response = configService.lambda_handler(config_event, MagicMock())

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["featuresEnabled"] == "feature1,feature2"
        assert body["locationServiceApiUrl"] == ""
        assert body["webDeployedUrl"] == ""

        mock_dynamodb_client.get_paginator.assert_called_once_with("scan")

    def test_get_secure_config_with_ssm_values(self, config_event):
        """Location Service URL and web deployed URL resolve from SSM when configured"""
        mock_dynamodb_client = MagicMock()
        mock_dynamodb_client.get_paginator.return_value = _make_paginator([])

        mock_ssm_client = MagicMock()
        mock_ssm_client.get_parameter.side_effect = [
            {"Parameter": {"Value": "arn:aws:geo:us-east-1:123456789012:api-key/test-key"}},
            {"Parameter": {"Value": "https://example.com "}},
        ]
        mock_geo_client = MagicMock()
        mock_geo_client.describe_key.return_value = {"Key": "test-api-key-value"}

        mock_enforcer = MagicMock()
        mock_enforcer.enforceAPI.return_value = True

        with patch.object(configService, "dynamodb_client", mock_dynamodb_client), \
             patch.object(configService, "ssm_client", mock_ssm_client), \
             patch.object(configService, "geo_client", mock_geo_client), \
             patch.object(configService, "CasbinEnforcer", return_value=mock_enforcer), \
             patch.object(configService, "request_to_claims", return_value={"tokens": ["test-user"], "roles": []}), \
             patch.object(configService, "location_service_api_key_arn_ssm_param", "/test/location-key-arn"), \
             patch.object(configService, "location_service_url_format", "https://maps.test/<apiKey>"), \
             patch.object(configService, "web_deployed_url_ssm_param", "/test/web-url"):
            response = configService.lambda_handler(config_event, MagicMock())

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["featuresEnabled"] == ""
        assert body["locationServiceApiUrl"] == "https://maps.test/test-api-key-value"
        assert body["webDeployedUrl"] == "https://example.com"
        mock_geo_client.describe_key.assert_called_once_with(KeyName="test-key")

    def test_get_secure_config_unauthorized(self, config_event):
        """API-level authorization denial returns 403"""
        mock_enforcer = MagicMock()
        mock_enforcer.enforceAPI.return_value = False

        with patch.object(configService, "CasbinEnforcer", return_value=mock_enforcer), \
             patch.object(configService, "request_to_claims", return_value={"tokens": ["test-user"], "roles": []}):
            response = configService.lambda_handler(config_event, MagicMock())

        assert response["statusCode"] == 403

    def test_get_secure_config_no_tokens_denied(self, config_event):
        """Empty token list fails closed with 403"""
        with patch.object(configService, "request_to_claims", return_value={"tokens": [], "roles": []}):
            response = configService.lambda_handler(config_event, MagicMock())

        assert response["statusCode"] == 403

    def test_get_secure_config_dynamodb_error(self, config_event):
        """A DynamoDB failure surfaces as a 500 internal error"""
        mock_dynamodb_client = MagicMock()
        mock_dynamodb_client.get_paginator.side_effect = Exception("DynamoDB error")

        mock_enforcer = MagicMock()
        mock_enforcer.enforceAPI.return_value = True

        with patch.object(configService, "dynamodb_client", mock_dynamodb_client), \
             patch.object(configService, "CasbinEnforcer", return_value=mock_enforcer), \
             patch.object(configService, "request_to_claims", return_value={"tokens": ["test-user"], "roles": []}):
            response = configService.lambda_handler(config_event, MagicMock())

        assert response["statusCode"] == 500
        assert json.loads(response["body"])["message"] == "Internal Server Error"

    def test_method_not_allowed(self, config_event):
        """Non-GET methods return a validation error"""
        config_event["requestContext"]["http"]["method"] = "POST"

        mock_enforcer = MagicMock()
        mock_enforcer.enforceAPI.return_value = True

        with patch.object(configService, "CasbinEnforcer", return_value=mock_enforcer), \
             patch.object(configService, "request_to_claims", return_value={"tokens": ["test-user"], "roles": []}):
            response = configService.lambda_handler(config_event, MagicMock())

        assert response["statusCode"] == 400
