"""
Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0

Test APIClient URL composition for stage-inclusive and fronted base URLs.
"""

import pytest
from unittest.mock import MagicMock, patch
from vamscli.utils.api_client import APIClient


class TestStageInclusiveBaseUrl:
    """Test that APIClient preserves the stage segment in REST API URLs."""

    def test_direct_stage_url_preserves_stage(self):
        """Test that a direct REST API invoke URL with stage segment composes correctly."""
        # Direct REST invoke URL includes the stage (e.g., /api) in the path
        base_url = "https://abc123.execute-api.us-east-1.amazonaws.com/api"
        mock_profile_manager = MagicMock()
        mock_profile_manager.is_override_token.return_value = False
        mock_profile_manager.load_auth_profile.return_value = {}

        client = APIClient(base_url, profile_manager=mock_profile_manager)

        with patch.object(client.session, "request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b"{}"
            mock_resp.headers = {}
            mock_resp.json.return_value = {}
            mock_req.return_value = mock_resp

            client._make_request("GET", "/database", include_auth=False)

            # Verify the composed URL preserves the /api stage segment
            called_url = mock_req.call_args[0][1]
            assert called_url == "https://abc123.execute-api.us-east-1.amazonaws.com/api/database", \
                f"Expected stage segment /api to be preserved, got {called_url}"

    def test_fronted_url_composes_endpoint(self):
        """Test that a fronted URL (custom domain) composes correctly."""
        base_url = "https://vams.example.com/api"
        mock_profile_manager = MagicMock()
        mock_profile_manager.is_override_token.return_value = False
        mock_profile_manager.load_auth_profile.return_value = {}

        client = APIClient(base_url, profile_manager=mock_profile_manager)

        with patch.object(client.session, "request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b"{}"
            mock_resp.headers = {}
            mock_resp.json.return_value = {}
            mock_req.return_value = mock_resp

            client._make_request("GET", "/database", include_auth=False)

            called_url = mock_req.call_args[0][1]
            assert called_url == "https://vams.example.com/api/database", \
                f"Expected fronted URL to compose correctly, got {called_url}"

    def test_deeper_endpoint_preserves_stage(self):
        """Test that deeper endpoints under a stage-inclusive URL compose correctly."""
        base_url = "https://abc123.execute-api.us-east-1.amazonaws.com/api"
        mock_profile_manager = MagicMock()
        mock_profile_manager.is_override_token.return_value = False
        mock_profile_manager.load_auth_profile.return_value = {}

        client = APIClient(base_url, profile_manager=mock_profile_manager)

        with patch.object(client.session, "request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b"{}"
            mock_resp.headers = {}
            mock_resp.json.return_value = {}
            mock_req.return_value = mock_resp

            # Use a deeper endpoint path
            database_id = "test-db-123"
            endpoint = f"/database/{database_id}/assets"
            client._make_request("GET", endpoint, include_auth=False)

            called_url = mock_req.call_args[0][1]
            expected = f"https://abc123.execute-api.us-east-1.amazonaws.com/api/database/{database_id}/assets"
            assert called_url == expected, \
                f"Expected stage segment /api to be preserved in deeper path, got {called_url}"

    def test_base_url_rstrip_handles_trailing_slash(self):
        """Test that base URLs with trailing slashes are normalized correctly."""
        # APIClient.__init__ does base_url.rstrip('/'), so a trailing slash should be removed
        base_url_with_slash = "https://abc123.execute-api.us-east-1.amazonaws.com/api/"
        mock_profile_manager = MagicMock()
        mock_profile_manager.is_override_token.return_value = False
        mock_profile_manager.load_auth_profile.return_value = {}

        client = APIClient(base_url_with_slash, profile_manager=mock_profile_manager)

        with patch.object(client.session, "request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b"{}"
            mock_resp.headers = {}
            mock_resp.json.return_value = {}
            mock_req.return_value = mock_resp

            client._make_request("GET", "/database", include_auth=False)

            called_url = mock_req.call_args[0][1]
            # The trailing slash should be stripped, stage preserved, endpoint joined
            assert called_url == "https://abc123.execute-api.us-east-1.amazonaws.com/api/database", \
                f"Expected trailing slash to be handled, got {called_url}"
