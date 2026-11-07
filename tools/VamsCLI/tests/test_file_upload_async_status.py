"""Outcome of files over 1 GiB, which are ingested after an upload completes (#1123).

`file upload` lists each upload whose files go to asynchronous ingestion under `async_ingestion`;
with --wait-async it waits for their outcome and exits non-zero when one failed or the wait timed
out. `file upload-status` reads one upload's status. Both keep --json-output pure (one JSON
document on stdout)."""

import importlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from vamscli.main import cli
from vamscli.utils.exceptions import APIError, InvalidAssetDataError, UploadStatusNotFoundError
from tests.conftest import CoroutineClosingMock  # noqa: E402

# The module, not the click group `vamscli.commands.file` resolves to as an attribute.
file_cmd = importlib.import_module("vamscli.commands.file")

UPLOAD = "y0b1c2d3-0000-4000-8000-000000000001"


@pytest.fixture
def file_command_mocks(generic_command_mocks):
    return generic_command_mocks('file')


def _upload_result(asynchronous=True):
    return {
        "overall_success": True, "total_files": 2, "successful_files": 2, "failed_files": 0,
        "total_size": 12, "total_size_formatted": "12B", "upload_duration": 1.0,
        "average_speed": 12.0, "average_speed_formatted": "12B/s",
        "sequence_results": [{"sequence_number": 1, "successful_files": 2, "failed_files": [],
                              "completion_result": {"uploadId": UPLOAD, "overallSuccess": True,
                                                    "largeFileAsynchronousHandling": asynchronous}}],
    }


def _status(status, files):
    return {"uploadId": UPLOAD, "databaseId": "test-db", "assetId": "test-asset", "status": status,
            "asyncFiles": [{"relativeKey": key, "status": s, "error": err, "updatedAt": ""}
                           for key, s, err in files]}


@pytest.fixture
def staged_file():
    with tempfile.NamedTemporaryFile(delete=False, suffix='.e57') as tmp:
        tmp.write(b"test content")
    yield tmp.name
    Path(tmp.name).unlink()


def _upload(cli_runner, mocks, staged_file, *extra, asynchronous=True):
    mocks['api_client'].get_database.return_value = {'databaseId': 'test-db',
                                                     'restrictFileUploadsToExtensions': ''}
    with patch('vamscli.commands.file.asyncio.run', new_callable=CoroutineClosingMock) as run, \
            patch.object(file_cmd.time, 'sleep'):
        run.return_value = _upload_result(asynchronous)
        return cli_runner.invoke(cli, ['file', 'upload', '-d', 'test-db', '-a', 'test-asset',
                                       staged_file, '--json-output', *extra])


class TestUploadReportsAsyncIngestion:
    def test_without_waiting_the_upload_is_reported_processing_with_a_check_command(
            self, cli_runner, file_command_mocks, staged_file):
        with file_command_mocks as mocks:
            result = _upload(cli_runner, mocks, staged_file)
        assert result.exit_code == 0
        body = json.loads(result.stdout)
        assert body["async_ingestion"] == [{
            "upload_id": UPLOAD, "status": "processing_async", "files": [],
            "check_command": f"vamscli file upload-status -d test-db -a test-asset -u {UPLOAD}"}]
        mocks['api_client'].get_upload_status.assert_not_called()

    def test_waiting_reports_each_file_landed(self, cli_runner, file_command_mocks, staged_file):
        with file_command_mocks as mocks:
            mocks['api_client'].get_upload_status.side_effect = [
                _status("processing_async", [("/scan.e57", "PENDING", "")]),
                _status("completed", [("/scan.e57", "SUCCEEDED", "")]),
            ]
            result = _upload(cli_runner, mocks, staged_file, '--wait-async')
        assert result.exit_code == 0
        body = json.loads(result.stdout)
        assert body["overall_success"] is True
        assert body["async_ingestion"][0]["files"] == [
            {"relative_key": "/scan.e57", "status": "SUCCEEDED", "error": ""}]
        mocks['api_client'].get_upload_status.assert_called_with('test-db', 'test-asset', UPLOAD)

    def test_a_failed_large_file_fails_the_command(self, cli_runner, file_command_mocks, staged_file):
        """M6: a large file whose copy failed after completion is now reported, not only audited."""
        with file_command_mocks as mocks:
            mocks['api_client'].get_upload_status.return_value = _status(
                "completed_with_errors", [("/scan.e57", "FAILED", "The copy did not complete.")])
            result = _upload(cli_runner, mocks, staged_file, '--wait-async')
        assert result.exit_code == 1
        body = json.loads(result.stdout)
        assert body["overall_success"] is False
        assert body["failed_files"] == 1 and body["successful_files"] == 1
        assert body["async_ingestion"][0]["files"][0]["error"] == "The copy did not complete."

    def test_a_wait_that_times_out_fails_the_command(self, cli_runner, file_command_mocks, staged_file):
        with file_command_mocks as mocks:
            mocks['api_client'].get_upload_status.return_value = _status(
                "processing_async", [("/scan.e57", "PENDING", "")])
            result = _upload(cli_runner, mocks, staged_file, '--wait-async', '--async-timeout', '0')
        assert result.exit_code == 1
        body = json.loads(result.stdout)
        assert body["async_ingestion"][0]["timed_out"] is True

    def test_an_upload_without_large_files_carries_no_async_section(
            self, cli_runner, file_command_mocks, staged_file):
        with file_command_mocks as mocks:
            result = _upload(cli_runner, mocks, staged_file, '--wait-async', asynchronous=False)
        assert result.exit_code == 0
        assert "async_ingestion" not in json.loads(result.stdout)
        mocks['api_client'].get_upload_status.assert_not_called()


class TestWaitLoop:
    def test_it_polls_until_every_upload_is_done_and_stops_at_the_deadline(self):
        client = type("C", (), {})()
        calls = []

        def status(db, asset, upload_id):
            calls.append(upload_id)
            return _status("processing_async", [("/a", "PENDING", "")])

        client.get_upload_status = status
        now = [0.0]
        slept = []
        entries = file_cmd.wait_for_async_ingestion(
            client, "db", "a", ["u1"], 40, sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)),
            clock=lambda: now[0])
        assert entries[0]["timed_out"] is True
        assert slept == [15, 15, 10]
        assert len(calls) == 4

    def test_an_upload_without_a_status_is_reported_unknown_at_once(self):
        client = type("C", (), {})()
        calls = []

        def status(db, asset, upload_id):
            calls.append(upload_id)
            raise UploadStatusNotFoundError("Upload 'u1' not found")

        client.get_upload_status = status
        entries = file_cmd.wait_for_async_ingestion(client, "db", "a", ["u1"], 60, sleep=lambda s: None)
        assert entries == [{"upload_id": "u1", "status": "unknown", "files": [],
                            "error": "Upload 'u1' not found"}]
        assert calls == ["u1"]  # a final answer is not polled again

    def test_an_invalid_request_is_reported_unknown_at_once(self):
        client = type("C", (), {})()
        client.get_upload_status = lambda db, asset, upload_id: (_ for _ in ()).throw(
            InvalidAssetDataError("Invalid upload status request: bad id"))
        entries = file_cmd.wait_for_async_ingestion(client, "db", "a", ["u1"], 60, sleep=lambda s: None)
        assert entries[0]["status"] == "unknown" and "timed_out" not in entries[0]

    def test_a_transient_error_is_retried_and_the_wait_still_succeeds(self):
        """L5: one 5xx or network error during an hour-long --wait-async no longer ends the wait."""
        client = type("C", (), {})()
        answers = [APIError("Failed to get upload status: 503 Server Error"),
                   _status("processing_async", [("/a", "PENDING", "")]),
                   APIError("Failed to get upload status: connection reset"),
                   _status("completed", [("/a", "SUCCEEDED", "")])]

        def status(db, asset, upload_id):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        client.get_upload_status = status
        now = [0.0]
        entries = file_cmd.wait_for_async_ingestion(
            client, "db", "a", ["u1"], 3600, sleep=lambda s: now.__setitem__(0, now[0] + s),
            clock=lambda: now[0])
        assert answers == []
        assert entries[0]["status"] == "completed"
        assert "timed_out" not in entries[0] and "error" not in entries[0]
        result = {"overall_success": True, "successful_files": 1, "failed_files": 0,
                  "async_ingestion": entries}
        file_cmd.apply_async_outcome(result, waited=True)
        assert result["overall_success"] is True

    def test_errors_until_the_deadline_end_as_unknown_and_timed_out(self):
        client = type("C", (), {})()
        client.get_upload_status = lambda db, asset, upload_id: (_ for _ in ()).throw(
            APIError("Failed to get upload status: 500"))
        now = [0.0]
        entries = file_cmd.wait_for_async_ingestion(
            client, "db", "a", ["u1"], 40, sleep=lambda s: now.__setitem__(0, now[0] + s),
            clock=lambda: now[0])
        assert entries[0]["status"] == "unknown" and entries[0]["timed_out"] is True
        result = {"overall_success": True, "async_ingestion": entries}
        file_cmd.apply_async_outcome(result, waited=True)
        assert result["overall_success"] is False


def _http_response(code, headers=None, body=b'{"message": "x"}'):
    import requests
    response = requests.Response()
    response.status_code = code
    response._content = body
    response.headers["Content-Type"] = "application/json"
    response.headers.update(headers or {})
    response.url = "https://api.example/uploads/u1"
    return response


def _client_answering(response):
    """An APIClient whose HTTP session answers every request with ``response``: the request goes
    through the real _make_request, so its status mapping is what a deployment would produce."""
    from unittest.mock import MagicMock
    from vamscli.utils.api_client import APIClient
    client = APIClient.__new__(APIClient)
    client.base_url = "https://api.example"
    client.request_timeout = 5
    client.session = MagicMock()
    client.session.request.return_value = response
    client.profile_manager = MagicMock()
    client.profile_manager.is_override_token.return_value = False
    client.profile_manager.is_token_expired.return_value = False
    return client


NO_ROUTE_403 = {"x-amzn-ErrorType": "MissingAuthenticationTokenException"}


class TestStatusThroughTheRequestPath:
    """#1123 round 3 M9: get_upload_status used to let _make_request turn every non-2xx into a plain
    APIError, so a 404, a 400 and a no-route 403 were retried by --wait-async until its timeout
    (242 polls over an hour). The answers below come through the real request path."""

    def _call(self, response):
        from vamscli.utils.api_client import APIClient
        client = _client_answering(response)
        with patch.object(APIClient, "_validate_token_before_request"), \
                patch.object(APIClient, "_get_headers", return_value={"Authorization": "Bearer x"}):
            return client, client.get_upload_status("db", "a1", "u1")

    @pytest.mark.parametrize("code,headers,error", [
        (404, None, UploadStatusNotFoundError),
        (400, None, InvalidAssetDataError),
        (403, NO_ROUTE_403, UploadStatusNotFoundError),
    ])
    def test_a_final_answer_is_its_own_error(self, code, headers, error):
        with pytest.raises(error):
            self._call(_http_response(code, headers))

    def test_a_404_keeps_its_own_message(self):
        with pytest.raises(UploadStatusNotFoundError) as raised:
            self._call(_http_response(404))
        assert "keeps no status" in str(raised.value)

    def test_a_server_error_is_transient(self):
        with patch("time.sleep"), pytest.raises(APIError) as raised:
            self._call(_http_response(503))
        assert not isinstance(raised.value, (UploadStatusNotFoundError, InvalidAssetDataError))

    @pytest.mark.parametrize("code,headers", [(404, None), (400, None), (403, NO_ROUTE_403)])
    def test_the_wait_ends_at_once_on_a_final_answer(self, code, headers):
        from vamscli.utils.api_client import APIClient
        client = _client_answering(_http_response(code, headers))
        now = [0.0]
        with patch.object(APIClient, "_validate_token_before_request"), \
                patch.object(APIClient, "_get_headers", return_value={"Authorization": "Bearer x"}):
            entries = file_cmd.wait_for_async_ingestion(
                client, "db", "a1", ["u1"], 3600,
                sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0])
        assert entries[0]["status"] == "unknown" and not entries[0].get("timed_out")
        assert client.session.request.call_count == 1
        assert now[0] == 0.0

    def test_the_wait_retries_a_server_error_until_the_deadline(self):
        from vamscli.utils.api_client import APIClient
        client = _client_answering(_http_response(503))
        now = [0.0]
        with patch.object(APIClient, "_validate_token_before_request"), \
                patch.object(APIClient, "_get_headers", return_value={"Authorization": "Bearer x"}), \
                patch("time.sleep"):
            entries = file_cmd.wait_for_async_ingestion(
                client, "db", "a1", ["u1"], 60,
                sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0])
        assert entries[0]["timed_out"] is True


class TestUploadStatusCommand:
    def test_json_output_is_the_api_response(self, cli_runner, file_command_mocks):
        with file_command_mocks as mocks:
            mocks['api_client'].get_upload_status.return_value = _status(
                "completed", [("/scan.e57", "SUCCEEDED", "")])
            result = cli_runner.invoke(cli, ['file', 'upload-status', '-d', 'test-db', '-a', 'test-asset',
                                             '-u', UPLOAD, '--json-output'])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["status"] == "completed"

    def test_a_failed_file_exits_non_zero_and_is_shown(self, cli_runner, file_command_mocks):
        with file_command_mocks as mocks:
            mocks['api_client'].get_upload_status.return_value = _status(
                "completed_with_errors", [("/scan.e57", "FAILED", "The copy did not complete.")])
            result = cli_runner.invoke(cli, ['file', 'upload-status', '-d', 'test-db', '-a', 'test-asset',
                                             '-u', UPLOAD])
        assert result.exit_code == 1
        assert "FAILED: /scan.e57 - The copy did not complete." in result.output

    def test_not_found_is_an_error_in_json(self, cli_runner, file_command_mocks):
        with file_command_mocks as mocks:
            mocks['api_client'].get_upload_status.side_effect = APIError("Upload 'x' not found")
            result = cli_runner.invoke(cli, ['file', 'upload-status', '-d', 'test-db', '-a', 'test-asset',
                                             '-u', UPLOAD, '--json-output'])
        assert result.exit_code != 0
        assert "not found" in json.loads(result.stdout)["error"]


class TestApiClient:
    def test_the_status_route_and_query(self):
        from vamscli.utils.api_client import APIClient
        client = APIClient.__new__(APIClient)
        response = type("R", (), {"json": lambda self: {"status": "completed"}})()
        with patch.object(APIClient, "get", return_value=response) as get:
            assert client.get_upload_status("db", "a", UPLOAD) == {"status": "completed"}
        assert get.call_args.args[0] == f"/uploads/{UPLOAD}"
        assert get.call_args.kwargs["params"] == {"databaseId": "db", "assetId": "a"}
