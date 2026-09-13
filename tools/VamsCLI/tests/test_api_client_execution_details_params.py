"""APIClient.get_execution_details / get_execution_logs: query parameters, envelope, 404 message.

The pipeline/workflow/execution methods return the handler's raw {"message": ...} body, so a
consumer (the CLI command, the MCP server) unwraps it. The details read gained a query parameter,
and a dropped parameter is a silent server default that no assertion on the return value catches.
The logs read answers 404 for two different things — a missing execution and an unknown logId —
and only a handler message in the JSON body tells them apart; a 404 with no JSON body (API Gateway,
a WAF page) carries nothing but the requests URL text, which must never reach the user.
"""

import pytest
import requests
from unittest.mock import MagicMock, patch

from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import ExecutionNotFoundError


def _client():
    profile_manager = MagicMock()
    profile_manager.is_override_token.return_value = False
    profile_manager.load_auth_profile.return_value = {}
    return APIClient("https://x.execute-api.us-east-1.amazonaws.com/api",
                     profile_manager=profile_manager)


def _response(body):
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b"non-empty"
    resp.headers = {}
    resp.json.return_value = body
    return resp


_URL_TEXT = "404 Client Error: Not Found for url: https://x/workflows/executions/e1/logs?mode=full"


def _error_response(status_code, body=None, raw=None):
    """A non-2xx response as _make_request sees it. raise_for_status raises an HTTPError whose str()
    is the "<code> Client Error: Not Found for url: ..." text requests composes — never empty — so a
    branch that falls back on `str(e)` being '' is visible here. `body` is a JSON body; `raw` is a
    non-JSON body (an HTML error page) whose .json() raises as requests' would."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {}
    if raw is not None:
        resp.content = raw
        resp.json.side_effect = ValueError("No JSON object could be decoded")
    else:
        resp.content = b"non-empty" if body is not None else b""
        resp.json.return_value = body if body is not None else {}
    resp.raise_for_status.side_effect = requests.exceptions.HTTPError(_URL_TEXT, response=resp)
    return resp


class TestGetExecutionLogs404Message:
    def test_a_logs_404_carries_the_servers_message(self):
        """An unknown logId is a 404 whose body says so; the CLI and the MCP tool must show that
        text, or the user reads 'execution not found' for an execution that exists."""
        client = _client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _error_response(
                404, {"message": "Log source not found for this pipeline execution"})

            with pytest.raises(ExecutionNotFoundError, match="Log source not found for this pipeline execution"):
                client.get_execution_logs("e1", params={"mode": "full", "pipelineExecutionId": "pe1",
                                                        "logId": "0" * 16})

    def test_an_unknown_pipeline_execution_404_carries_the_servers_message(self):
        """The other step-scoped 404: a pipelineExecutionId the execution does not have."""
        client = _client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _error_response(
                404, {"message": "Pipeline execution not found for this execution"})

            with pytest.raises(ExecutionNotFoundError, match="Pipeline execution not found for this execution"):
                client.get_execution_logs("e1", params={"mode": "full", "pipelineExecutionId": "pe1"})

    def test_a_missing_execution_404_names_the_execution_id(self):
        """The handler's body for a missing execution is the id-less `Execution not found`; the client
        raises the id-bearing text the MCP docstring promises, as get_execution_details does."""
        client = _client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _error_response(404, {"message": "Execution not found"})

            with pytest.raises(ExecutionNotFoundError) as excinfo:
                client.get_execution_logs("e1", params={"mode": "truncated"})
            assert str(excinfo.value) == "Execution 'e1' not found"

    def test_a_body_less_logs_404_keeps_the_generic_message(self):
        """Control: a 404 with no body at all (API Gateway, a stale stage URL) reads as a missing
        execution, not as the requests URL text."""
        client = _client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _error_response(404)

            with pytest.raises(ExecutionNotFoundError) as excinfo:
                client.get_execution_logs("e1", params={"mode": "truncated"})
            assert str(excinfo.value) == "Execution 'e1' not found"

    def test_a_non_json_logs_404_keeps_the_generic_message(self):
        """A 404 whose body is not JSON (a WAF or gateway HTML page) also reads as a missing
        execution; neither the HTML nor the URL text is the message."""
        client = _client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _error_response(404, raw=b"<html><body>Not Found</body></html>")

            with pytest.raises(ExecutionNotFoundError) as excinfo:
                client.get_execution_logs("e1", params={"mode": "truncated"})
            assert str(excinfo.value) == "Execution 'e1' not found"


class TestGetExecutionDetailsParams:
    def test_default_call_sends_no_query_parameters(self):
        """Positive control for the endpoint, and the cheap default: no includeSubExecutions."""
        client = _client()
        body = {"message": {"workflowExecutionId": "e1", "pipelines": []}}
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _response(body)

            result = client.get_execution_details("e1")

            assert mock_req.call_args[0][0] == "GET"
            assert mock_req.call_args[0][1].endswith("/workflows/executions/e1/details")
            assert not mock_req.call_args.kwargs.get("params")
            # The envelope is left intact, as every pipeline/workflow/execution method does.
            assert result == body

    def test_include_sub_executions_is_forwarded_as_a_query_parameter(self):
        client = _client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _response({"message": {"workflowExecutionId": "e1"}})

            client.get_execution_details("e1", params={"includeSubExecutions": "true"})

            assert mock_req.call_args.kwargs["params"] == {"includeSubExecutions": "true"}
