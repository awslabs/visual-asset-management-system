"""
Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0

Test APIClient URL composition for stage-inclusive and fronted base URLs.
"""

import json

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
    """The asset-scoped execution list route is GET-only, and its workflow filter travels as QUERY
    parameters.

    It previously sent workflowDatabaseId as a GET request BODY, pairing it with the
    `.../executions/{workflowId}` path form. That form compares against the joined
    `workflowDatabaseId:workflowId` key, so `workflow_id` without `workflow_database_id` filtered
    against ':<workflowId>' and returned an empty list rather than that workflow's executions —
    verified live before the change. Query parameters are matched per field, so either half narrows
    the list on its own.
    """

    def _client(self):
        mock_profile_manager = MagicMock()
        mock_profile_manager.is_override_token.return_value = False
        mock_profile_manager.load_auth_profile.return_value = {}
        return APIClient("https://x.execute-api.us-east-1.amazonaws.com/api",
                         profile_manager=mock_profile_manager)

    def test_workflow_database_filter_uses_a_query_parameter(self):
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
            assert mock_req.call_args.kwargs.get("params") == {"workflowDatabaseId": "global"}
            # No GET body: fetch/XHR cannot send one, so the browser and the CLI must agree on the
            # query form.
            assert mock_req.call_args.kwargs.get("json") is None

    def test_workflow_id_alone_is_sent_and_stays_on_the_base_path(self):
        """The regression this guards: a workflow id must filter WITHOUT its database. Appending it
        to the path instead selects the joined-key form, which matches nothing on its own."""
        client = self._client()
        with patch.object(client.session, "request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b'{"message": {"Items": []}}'
            mock_resp.headers = {}
            mock_resp.json.return_value = {"message": {"Items": []}}
            mock_req.return_value = mock_resp

            client.list_workflow_executions("db1", "asset1", workflow_id="wf-alpha")

            assert mock_req.call_args.kwargs.get("params") == {"workflowId": "wf-alpha"}
            called_url = mock_req.call_args[0][1]
            assert called_url.endswith("/workflows/executions"), \
                f"Expected the base path (not /executions/wf-alpha), got {called_url}"

    def test_both_halves_are_sent_together(self):
        client = self._client()
        with patch.object(client.session, "request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b'{"message": {"Items": []}}'
            mock_resp.headers = {}
            mock_resp.json.return_value = {"message": {"Items": []}}
            mock_req.return_value = mock_resp

            client.list_workflow_executions("db1", "asset1", workflow_id="wf-alpha",
                                            workflow_database_id="wdb-one",
                                            params={"pageSize": 50})

            assert mock_req.call_args.kwargs.get("params") == {
                "pageSize": 50, "workflowId": "wf-alpha", "workflowDatabaseId": "wdb-one"}

    def test_caller_params_are_not_mutated(self):
        """The filters are merged into a COPY: a caller reusing its params dict across pages would
        otherwise accumulate them (harmless here) or see them appear in its own dict (surprising)."""
        client = self._client()
        with patch.object(client.session, "request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b'{"message": {"Items": []}}'
            mock_resp.headers = {}
            mock_resp.json.return_value = {"message": {"Items": []}}
            mock_req.return_value = mock_resp

            caller_params = {"pageSize": 50}
            client.list_workflow_executions("db1", "asset1", workflow_id="wf-alpha",
                                            params=caller_params)

            assert caller_params == {"pageSize": 50}

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
            # No filter -> no filter keys at all, so the route applies its own defaults.
            assert mock_req.call_args.kwargs.get("params") == {}


class TestGetExecutionDetailsMetadata:
    """The paged detail-metadata read: the endpoint path, the query parameters, and the raw
    {'message': ...} envelope the pipeline/workflow/execution methods return intact."""

    def _client(self):
        mock_profile_manager = MagicMock()
        mock_profile_manager.is_override_token.return_value = False
        mock_profile_manager.load_auth_profile.return_value = {}
        return APIClient("https://x.execute-api.us-east-1.amazonaws.com/api",
                         profile_manager=mock_profile_manager)

    @staticmethod
    def _response(body):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"non-empty"
        resp.headers = {}
        resp.json.return_value = body
        return resp

    def test_endpoint_and_params(self):
        client = self._client()
        body = {"message": {"Items": [{"pipelineId": "p1"}], "collection": "output",
                            "NextToken": "tok"}}
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = self._response(body)

            result = client.get_execution_details_metadata(
                "e1", params={"collection": "output", "pageSize": 500, "pipelineId": "p1"})

            called_url = mock_req.call_args[0][1]
            assert called_url.endswith("/workflows/executions/e1/details/metadata")
            assert mock_req.call_args[0][0] == "GET"
            assert mock_req.call_args.kwargs["params"] == {
                "collection": "output", "pageSize": 500, "pipelineId": "p1"}
            # The envelope is left intact, as every pipeline/workflow/execution method does.
            assert result == body

    def test_missing_execution_raises_not_found(self):
        from vamscli.utils.exceptions import ExecutionNotFoundError

        client = self._client()
        with patch.object(client, "get") as mock_get:
            resp = MagicMock()
            resp.status_code = 404
            resp.content = b'{"message": "Execution not found"}'
            resp.json.return_value = {"message": "Execution not found"}
            mock_get.side_effect = requests.exceptions.HTTPError(response=resp)

            with pytest.raises(ExecutionNotFoundError):
                client.get_execution_details_metadata("missing")

    def test_bad_token_raises_invalid_execution_data(self):
        """The handler answers an unusable startingToken with a 400, which must surface as the
        invalid-request error the command explains rather than a generic APIError."""
        from vamscli.utils.exceptions import InvalidExecutionDataError

        client = self._client()
        with patch.object(client, "get") as mock_get:
            resp = MagicMock()
            resp.status_code = 400
            resp.content = b'{"message": "startingToken is invalid."}'
            resp.json.return_value = {"message": "startingToken is invalid."}
            mock_get.side_effect = requests.exceptions.HTTPError(response=resp)

            with pytest.raises(InvalidExecutionDataError):
                client.get_execution_details_metadata("e1", params={"startingToken": "stale"})


class TestPweDomainErrorMappingIsReachable:
    """The pipeline/workflow/execution methods map a non-2xx themselves, so their mapping has to
    survive the transport layer.

    `_make_request` converts an HTTPError to a generic `APIError` for every other method. These
    methods opt out (`raise_http_errors=True`) so the original error reaches their own arm — which is
    what lets a structured backend body reach the user and what makes the command layer's
    `except PipelineNotFoundError` / `except WorkflowAlreadyRunningError` blocks run. The tests below
    drive the real `session.request` boundary rather than patching `client.get`, because patching the
    verb wrapper skips the very conversion that has to be avoided.
    """

    def _client(self):
        mock_profile_manager = MagicMock()
        mock_profile_manager.is_override_token.return_value = False
        mock_profile_manager.is_token_expired.return_value = False
        mock_profile_manager.load_auth_profile.return_value = {}
        return APIClient("https://x.execute-api.us-east-1.amazonaws.com/api",
                         profile_manager=mock_profile_manager)

    @staticmethod
    def _response(status_code, body):
        resp = MagicMock()
        resp.status_code = status_code
        resp.content = json.dumps(body).encode()
        resp.headers = {}
        resp.json.return_value = body
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
        return resp

    def test_structured_trigger_error_reaches_the_user_as_readable_lines(self):
        """The finding's scenario: a trigger-set 400 whose message is a dict of error lists."""
        from vamscli.utils.exceptions import InvalidWorkflowTriggerDataError

        client = self._client()
        body = {"message": {"triggerTemplateErrors": [
            "template 'X' (pipeline 'Y') is chosen as a trigger default but has required tag(s) "
            "with no default value: q."]}}
        with patch.object(client.session, "request", return_value=self._response(400, body)):
            with pytest.raises(InvalidWorkflowTriggerDataError) as excinfo:
                client.set_workflow_trigger("db1", "wf1", "allFileTypes",
                                            {"defaultTemplateIds": {"global:conv": "t1"}})

        message = str(excinfo.value)
        assert "required tag(s) with no default value: q." in message
        # Neither the raw dict repr nor the generic wrapper the shared conversion produces.
        assert "triggerTemplateErrors" not in message
        assert "Invalid request (400)" not in message

    def test_missing_pipeline_raises_the_domain_not_found_error(self):
        from vamscli.utils.exceptions import PipelineNotFoundError

        client = self._client()
        with patch.object(client.session, "request",
                          return_value=self._response(404, {"message": "Pipeline not found"})):
            with pytest.raises(PipelineNotFoundError):
                client.get_pipeline("db1", "p1")

    def test_conflicting_execution_raises_already_running(self):
        from vamscli.utils.exceptions import WorkflowAlreadyRunningError

        client = self._client()
        body = {"message": "A conflicting execution is already running for this asset."}
        with patch.object(client.session, "request", return_value=self._response(400, body)):
            with pytest.raises(WorkflowAlreadyRunningError):
                client.execute_workflow("global", "wf1", {"inputFiles": []})

    def test_in_progress_permanent_delete_raises_execution_in_progress(self):
        from vamscli.utils.exceptions import ExecutionInProgressError

        client = self._client()
        body = {"message": "Execution is in progress and cannot be deleted."}
        with patch.object(client.session, "request", return_value=self._response(400, body)):
            with pytest.raises(ExecutionInProgressError):
                client.permanent_delete_execution("e1")

    def test_asset_and_database_methods_keep_the_shared_conversion(self):
        """The opt-out is per call: everything else still gets the generic APIError wrapper."""
        from vamscli.utils.exceptions import APIError

        client = self._client()
        with patch.object(client.session, "request",
                          return_value=self._response(400, {"message": "bad asset data"})):
            with pytest.raises(APIError) as excinfo:
                # create_asset maps 400 itself, but only ever sees the converted error, so the
                # generic wrapper is what surfaces.
                client.create_asset({"databaseId": "db1"})

        assert "Invalid request (400): bad asset data" in str(excinfo.value)


class TestPweEnvelopeAsymmetry:
    """Pipeline/workflow/execution methods return the raw {'message': ...} envelope; asset and
    database methods return the payload directly. The MCP server unwraps the former, so
    normalizing either side would silently change the shape its tools hand agents."""

    def _client(self):
        mock_profile_manager = MagicMock()
        mock_profile_manager.is_override_token.return_value = False
        mock_profile_manager.load_auth_profile.return_value = {}
        return APIClient("https://x.execute-api.us-east-1.amazonaws.com/api",
                         profile_manager=mock_profile_manager)

    @staticmethod
    def _response(body):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = json.dumps(body).encode()
        resp.headers = {}
        resp.json.return_value = body
        return resp

    def test_pipeline_read_keeps_the_envelope(self):
        client = self._client()
        body = {"message": {"pipelineId": "p1", "pipelineName": "Convert"}}
        with patch.object(client.session, "request", return_value=self._response(body)):
            assert client.get_pipeline("db1", "p1") == body

    def test_execution_list_keeps_the_envelope(self):
        client = self._client()
        body = {"message": {"Items": [{"workflowExecutionId": "e1"}], "NextToken": "tok"}}
        with patch.object(client.session, "request", return_value=self._response(body)):
            assert client.list_executions() == body

    def test_database_read_has_no_envelope(self):
        client = self._client()
        body = {"databaseId": "db1", "description": "d"}
        with patch.object(client.session, "request", return_value=self._response(body)):
            assert client.get_database("db1") == body


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
