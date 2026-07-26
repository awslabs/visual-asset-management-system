"""
Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0

Test APIClient URL composition for stage-inclusive and fronted base URLs.
"""

import pytest
import requests
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


class TestListWorkflowExecutionsHttpMethod:
    """The asset-scoped execution list route is GET-only; the optional workflowDatabaseId filter must
    be sent as a GET body, not a POST (no POST is registered on that resource)."""

    def _client(self):
        mock_profile_manager = MagicMock()
        mock_profile_manager.is_override_token.return_value = False
        mock_profile_manager.load_auth_profile.return_value = {}
        return APIClient("https://x.execute-api.us-east-1.amazonaws.com/api",
                         profile_manager=mock_profile_manager)

    def test_workflow_database_filter_uses_get_with_body(self):
        client = self._client()
        with patch.object(client.session, "request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b'{"message": {"Items": []}}'
            mock_resp.headers = {}
            mock_resp.json.return_value = {"message": {"Items": []}}
            mock_req.return_value = mock_resp

            client.list_workflow_executions("db1", "asset1", workflow_database_id="global")

            method = mock_req.call_args[0][0]
            assert method == "GET", f"Expected GET (route is GET-only), got {method}"
            assert mock_req.call_args.kwargs.get("json") == {"workflowDatabaseId": "global"}

    def test_no_filter_uses_plain_get(self):
        client = self._client()
        with patch.object(client.session, "request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b'{"message": {"Items": []}}'
            mock_resp.headers = {}
            mock_resp.json.return_value = {"message": {"Items": []}}
            mock_req.return_value = mock_resp

            client.list_workflow_executions("db1", "asset1")

            assert mock_req.call_args[0][0] == "GET"
            assert mock_req.call_args.kwargs.get("json") is None


class TestPweErrorMessageFlattening:
    """A structured {'message': {...}} error body (e.g. triggerTemplateErrors) must be flattened
    into readable lines rather than shown as raw JSON."""

    @staticmethod
    def _http_error(body: dict) -> requests.exceptions.HTTPError:
        resp = MagicMock()
        resp.content = b"non-empty"
        resp.json.return_value = body
        return requests.exceptions.HTTPError(response=resp)

    def test_trigger_template_errors_list_flattened(self):
        err = self._http_error({"message": {"triggerTemplateErrors": [
            "template 'X' (pipeline 'Y') is chosen as a trigger default but has required tag(s) "
            "with no default value: q."]}})
        msg = APIClient._pwe_error_message(err)
        assert "required tag" in msg
        assert "triggerTemplateErrors" not in msg  # key stripped, only the line shown

    def test_plain_string_message_passthrough(self):
        err = self._http_error({"message": "simple error"})
        assert APIClient._pwe_error_message(err) == "simple error"

    def test_top_level_list_message_flattened(self):
        err = self._http_error({"message": ["line one", "line two"]})
        assert APIClient._pwe_error_message(err) == "line one\nline two"
