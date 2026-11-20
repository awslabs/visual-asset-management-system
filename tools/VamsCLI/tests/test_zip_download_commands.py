"""The 'vamscli zip-download' commands: arguments reaching the APIClient, --json-output purity,
the disabled-feature business error, --wait, the archive download, and the URL's secrets never shown."""

import io
import json
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest
import requests
import requests.adapters

from vamscli.main import cli
from vamscli.utils.api_client import APIClient
from vamscli.utils.logging import REDACTED
from vamscli.utils.exceptions import (
    ZipDownloadDisabledError,
    ZipDownloadLimitError,
    ZipDownloadNotFoundError,
    ZipDownloadNotReadyError,
    ZipDownloadStateError,
)

PRESIGNED = "https://bucket.s3.amazonaws.com/vams.preparedDownloads/a.zip?X-Amz-Signature=abc123secret"
JOB = {
    "requestId": "r1", "databaseId": "db", "assetId": "a", "assetName": "Asset",
    "status": "QUEUED", "archiveFormat": "zip", "compression": "store",
    "archiveFileName": "Asset.zip", "selectionSummary": {"files": 1, "folders": 1,
                                                         "wholeAsset": False},
    "progress": {"filesDone": 0, "filesTotal": 0, "bytesDone": 0, "bytesTotal": 0},
    "createdAt": "2026-11-06T00:00:00Z", "expiresAt": "2026-11-13T00:00:00Z",
}
SCOPE = ["-d", "db", "-a", "a"]

# A presigned archive URL as the API issues it (role-signed, so it carries a session token too).
# Neither value is real.
SIGNATURE = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"  # pragma: allowlist secret
SESSION_TOKEN = "IQoJb3JpZ2luX2VjEXAMPLESESSIONTOKEN%2Fpart%2Bmore%3D"  # pragma: allowlist secret
ARCHIVE_HOST = "https://bucket.s3.us-east-1.amazonaws.com/"
SIGNED_ARCHIVE_URL = (
    f"{ARCHIVE_HOST}vams.preparedDownloads/r1/Asset.zip?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    f"&X-Amz-Security-Token={SESSION_TOKEN}&X-Amz-SignedHeaders=host&X-Amz-Signature={SIGNATURE}")
ARCHIVE = b"PK\x05\x06" + b"\x00" * 18  # an empty zip


class _Deployment(requests.adapters.BaseAdapter):
    """A transport answering the zip download routes of job r1 and its archive URL."""

    def __init__(self):
        super().__init__()
        self.sent = []

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        self.sent.append(request)
        path = urlparse(request.url).path
        status, body = 404, {"message": "Not Found"}
        if request.url.startswith(ARCHIVE_HOST):
            status, body = 200, ARCHIVE
        elif request.method == "POST" and path.endswith("/database/db/assets/a/download/zip"):
            status, body = 200, JOB
        elif request.method == "GET" and path.endswith("/database/db/assets/a/download/zip/r1"):
            status, body = 200, {**JOB, "status": "SUCCEEDED", "archiveSizeBytes": len(ARCHIVE)}
        elif request.method == "POST" and path.endswith("/database/db/assets/a/download/zip/r1/url"):
            status, body = 200, {"url": SIGNED_ARCHIVE_URL, "expiresIn": 900,
                                 "archiveFileName": "Asset.zip", "archiveSizeBytes": len(ARCHIVE)}
        response = requests.Response()
        response.status_code = status
        if isinstance(body, bytes):
            response.raw = io.BytesIO(body)
        else:
            response.headers["Content-Type"] = "application/json"
            response.raw = io.BytesIO(json.dumps(body).encode("utf-8"))
        response.url = request.url
        response.request = request
        return response

    def close(self):
        pass


def _real_client(transport):
    profile_manager = MagicMock()
    profile_manager.is_override_token.return_value = False
    profile_manager.is_token_expired.return_value = False
    profile_manager.load_auth_profile.return_value = {"access_token": "t"}
    profile_manager.get_feature_switches_info.return_value = {
        "has_feature_switches": True, "enabled": ["ZIPDOWNLOADS"]}
    client = APIClient("https://api.example.com", profile_manager=profile_manager)
    client.session.mount("https://", transport)
    return client


def _invoke(cli_runner, *args):
    return cli_runner.invoke(cli, ["zip-download", *args])


class TestCreate:
    def test_selection_and_options_reach_the_api(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("zip_download") as mocks:
            mocks["api_client"].create_zip_download.return_value = JOB
            result = _invoke(cli_runner, "create", *SCOPE, "--file", "/m.glb@v1", "--file", "x.txt",
                             "--folder", "textures", "--format", "zip", "--compression", "deflate",
                             "--name", "out", "--json-output")

            assert result.exit_code == 0, result.output
            assert json.loads(result.output) == JOB
            args = mocks["api_client"].create_zip_download.call_args.args
            assert args[:2] == ("db", "a")
            assert args[2] == {
                "selection": [
                    {"type": "file", "relativePath": "/m.glb", "versionId": "v1"},
                    {"type": "file", "relativePath": "/x.txt"},
                    {"type": "folder", "relativePath": "/textures/"},
                ],
                "archiveFormat": "zip", "compression": "deflate", "archiveFileName": "out",
            }

    def test_whole_asset_pinned_to_an_asset_version(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("zip_download") as mocks:
            mocks["api_client"].create_zip_download.return_value = JOB
            result = _invoke(cli_runner, "create", *SCOPE, "--whole-asset",
                             "--asset-version-id", "3", "--format", "tar.gz")
            assert result.exit_code == 0, result.output
            body = mocks["api_client"].create_zip_download.call_args.args[2]
            assert body["selection"] == [{"type": "asset", "relativePath": "/"}]
            assert body["assetVersionId"] == "3" and body["archiveFormat"] == "tar.gz"
            assert "Request ID: r1" in result.output

    @pytest.mark.parametrize("argv", [
        [],
        ["--whole-asset", "--file", "/a"],
        ["--file", "/a@v1", "--asset-version-id", "3"],
        ["--whole-asset", "--format", "tar", "--compression", "deflate"],
        ["--whole-asset", "--timeout", "5"],
    ])
    def test_refused_combinations_are_pure_json_errors(self, cli_runner, generic_command_mocks, argv):
        with generic_command_mocks("zip_download") as mocks:
            result = _invoke(cli_runner, "create", *SCOPE, *argv, "--json-output")
            assert result.exit_code == 1
            data = json.loads(result.output)
            assert data["error_type"] == "InvalidZipDownloadRequestError"
            mocks["api_client"].create_zip_download.assert_not_called()

    def test_wait_polls_until_succeeded(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("zip_download") as mocks, \
                patch("vamscli.utils.zip_downloads.time.sleep"):
            mocks["api_client"].create_zip_download.return_value = JOB
            mocks["api_client"].get_zip_download.side_effect = [
                {**JOB, "status": "RUNNING"}, {**JOB, "status": "SUCCEEDED", "archiveSizeBytes": 10}]
            result = _invoke(cli_runner, "create", *SCOPE, "--whole-asset", "--wait", "--json-output")
            assert result.exit_code == 0, result.output
            assert json.loads(result.output)["status"] == "SUCCEEDED"

    def test_wait_reports_a_failed_job(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("zip_download") as mocks:
            mocks["api_client"].create_zip_download.return_value = JOB
            mocks["api_client"].get_zip_download.return_value = {
                **JOB, "status": "FAILED", "errorMessage": "ZIPDOWNLOAD: /a.exe is a blocked type"}
            result = _invoke(cli_runner, "create", *SCOPE, "--whole-asset", "--wait", "--json-output")
            assert result.exit_code == 1
            data = json.loads(result.output)
            assert data["error_type"] == "ZipDownloadFailedError"
            assert data["status"] == "FAILED" and data["requestId"] == "r1"
            assert "blocked type" in data["errorMessage"]

    def test_download_to_never_shows_the_url_secrets_even_with_verbose(
            self, cli_runner, generic_command_mocks, tmp_path, monkeypatch):
        """A real APIClient on a stubbed transport, so the URL response reaches the --verbose echo
        and the archive request goes through the session as it does against a deployment."""
        monkeypatch.setenv("NETRC", str(tmp_path / "no-netrc"))  # no ambient netrc credentials
        deployment = _Deployment()
        with generic_command_mocks("zip_download") as mocks, \
                patch("vamscli.utils.zip_downloads.time.sleep"):
            mocks["patches"]["cmd_api"].side_effect = lambda *a, **k: _real_client(deployment)

            for json_flag in ([], ["--json-output"]):
                target = tmp_path / ("json" if json_flag else "text")
                target.mkdir()
                result = cli_runner.invoke(cli, ["--verbose", "zip-download", "create", *SCOPE,
                                                 "--whole-asset", "--download-to", str(target),
                                                 *json_flag])
                assert result.exit_code == 0, result.output
                # Control: the URL response did reach the echo, so the checks below are not vacuous.
                assert "X-Amz-Signature=" + REDACTED in result.stderr
                assert "X-Amz-Security-Token=" + REDACTED in result.stderr
                for text in (result.stdout, result.stderr):
                    assert SIGNATURE not in text and SESSION_TOKEN not in text
                assert (target / "Asset.zip").read_bytes() == ARCHIVE

            data = json.loads(result.stdout)
            assert data["download"]["localPath"] == str(target / "Asset.zip")
            assert data["download"]["bytesWritten"] == len(ARCHIVE)

        archive_requests = [r for r in deployment.sent if r.url.startswith(ARCHIVE_HOST)]
        assert len(archive_requests) == 2
        assert all("Authorization" not in r.headers for r in archive_requests)

    def test_a_failure_after_the_job_exists_names_its_request_id(self, cli_runner, generic_command_mocks, tmp_path):
        """A retry must download the existing job, not create a second one."""
        from vamscli.utils.exceptions import ZipDownloadTransferError

        with generic_command_mocks("zip_download") as mocks:
            api = mocks["api_client"]
            api.create_zip_download.return_value = JOB
            api.get_zip_download.return_value = {**JOB, "status": "SUCCEEDED", "archiveSizeBytes": 4}
            api.get_zip_download_url.return_value = {"url": PRESIGNED, "archiveFileName": "Asset.zip"}
            api.download_zip_archive.side_effect = ZipDownloadTransferError("connection reset")

            result = _invoke(cli_runner, "create", *SCOPE, "--whole-asset", "--download-to", str(tmp_path),
                             "--json-output")
            assert result.exit_code == 1
            data = json.loads(result.output)
            assert data["requestId"] == "r1" and data["error_type"] == "ZipDownloadTransferError"

            text = _invoke(cli_runner, "create", *SCOPE, "--whole-asset", "--download-to", str(tmp_path))
            assert text.exit_code == 1
            assert "vamscli zip-download download -d db -a a -r r1" in text.output

    def test_a_failure_before_the_job_exists_names_no_request_id(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("zip_download") as mocks:
            mocks["api_client"].create_zip_download.side_effect = ZipDownloadLimitError(
                "You already have the maximum number of active zip downloads")
            result = _invoke(cli_runner, "create", *SCOPE, "--whole-asset", "--json-output")
            assert result.exit_code == 1
            assert "requestId" not in json.loads(result.output)

    def test_the_destination_check_does_not_guess_the_archive_name(
            self, cli_runner, generic_command_mocks, tmp_path):
        """A file named like the --name (or 'archive') is not the archive the server names: the job is
        created, and an existing file of the real name is caught once the name is known."""
        (tmp_path / "archive").write_text("unrelated")
        (tmp_path / "out").write_text("unrelated")
        with generic_command_mocks("zip_download") as mocks:
            api = mocks["api_client"]
            api.create_zip_download.return_value = JOB
            api.get_zip_download.return_value = {**JOB, "status": "SUCCEEDED", "archiveSizeBytes": 4}
            api.get_zip_download_url.return_value = {"url": PRESIGNED, "archiveFileName": "out.zip"}
            api.download_zip_archive.return_value = 4
            for name in ([], ["--name", "out"]):
                result = _invoke(cli_runner, "create", *SCOPE, "--whole-asset", *name,
                                 "--download-to", str(tmp_path), "--json-output")
                assert result.exit_code == 0, result.output
            (tmp_path / "out.zip").write_text("already here")
            result = _invoke(cli_runner, "create", *SCOPE, "--whole-asset", "--name", "out",
                             "--download-to", str(tmp_path), "--json-output")
            assert result.exit_code == 1
            assert "already exists" in json.loads(result.output)["error"]
            assert json.loads(result.output)["requestId"] == "r1"

    def test_a_destination_directory_that_does_not_exist_is_refused_before_the_job(
            self, cli_runner, generic_command_mocks, tmp_path):
        with generic_command_mocks("zip_download") as mocks:
            result = _invoke(cli_runner, "create", *SCOPE, "--whole-asset",
                             "--download-to", str(tmp_path / "missing" / "a.zip"), "--json-output")
            assert result.exit_code == 1
            mocks["api_client"].create_zip_download.assert_not_called()

    def test_download_wait_fetches_another_users_finished_archive(self, cli_runner, generic_command_mocks, tmp_path):
        """The job read answers only your own jobs; the URL route answers for a shared one."""
        with generic_command_mocks("zip_download") as mocks:
            api = mocks["api_client"]
            api.get_zip_download.side_effect = ZipDownloadNotFoundError("Zip download not found")
            api.get_zip_download_url.return_value = {"url": PRESIGNED, "archiveFileName": "Asset.zip",
                                                     "archiveSizeBytes": 4}
            api.download_zip_archive.return_value = 4
            result = _invoke(cli_runner, "download", *SCOPE, "-r", "r1", "-o", str(tmp_path), "--wait",
                             "--json-output")
            assert result.exit_code == 0, result.output
            assert json.loads(result.output)["localPath"] == str(tmp_path / "Asset.zip")

    def test_download_wait_of_a_job_it_cannot_read_says_it_may_not_exist(self, cli_runner, generic_command_mocks,
                                                                         tmp_path):
        """The job read answers not found alike for another user's job and for a mistyped id."""
        with generic_command_mocks("zip_download") as mocks:
            api = mocks["api_client"]
            api.get_zip_download.side_effect = ZipDownloadNotFoundError("Zip download not found")
            api.get_zip_download_url.side_effect = ZipDownloadNotFoundError("Zip download not found")
            result = _invoke(cli_runner, "download", *SCOPE, "-r", "r1", "-o", str(tmp_path), "--wait")
            assert result.exit_code == 1
            assert "is not one of your jobs or does not exist" in result.output

    def test_download_wait_of_another_users_unfinished_job_is_refused_by_the_url_route(
            self, cli_runner, generic_command_mocks, tmp_path):
        with generic_command_mocks("zip_download") as mocks:
            api = mocks["api_client"]
            api.get_zip_download.side_effect = ZipDownloadNotFoundError("Zip download not found")
            api.get_zip_download_url.side_effect = ZipDownloadNotReadyError("The archive is not ready for download")
            result = _invoke(cli_runner, "download", *SCOPE, "-r", "r1", "-o", str(tmp_path), "--wait",
                             "--json-output")
            assert result.exit_code == 1
            assert json.loads(result.output)["error"] == "The archive is not ready for download"
            api.download_zip_archive.assert_not_called()

    def test_active_cap(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("zip_download") as mocks:
            mocks["api_client"].create_zip_download.side_effect = ZipDownloadLimitError(
                "You already have the maximum number of active zip downloads")
            result = _invoke(cli_runner, "create", *SCOPE, "--whole-asset")
            assert result.exit_code != 0
            assert "Zip Download Limit Reached" in result.output


class TestDisabled:
    """The feature-off business error, in text and in --json-output, on every command."""

    @pytest.mark.parametrize("argv,method", [
        (["create", *SCOPE, "--whole-asset"], "create_zip_download"),
        (["list"], "list_zip_downloads"),
        (["get", *SCOPE, "-r", "r1"], "get_zip_download"),
        (["download", *SCOPE, "-r", "r1", "-o", "."], "get_zip_download_url"),
        (["cancel", *SCOPE, "-r", "r1", "--confirm"], "cancel_zip_download"),
        (["delete", *SCOPE, "-r", "r1", "--confirm"], "delete_zip_download"),
    ])
    def test_disabled(self, cli_runner, generic_command_mocks, argv, method):
        with generic_command_mocks("zip_download") as mocks:
            getattr(mocks["api_client"], method).side_effect = ZipDownloadDisabledError(
                "Zip downloads are not available for this deployment")

            result = _invoke(cli_runner, *argv, "--json-output")
            assert result.exit_code == 1
            assert json.loads(result.output) == {
                "error": "Zip downloads are not available for this deployment",
                "error_type": "ZipDownloadDisabledError",
            }

            result = _invoke(cli_runner, *argv)
            assert result.exit_code != 0
            assert "Zip downloads are not available for this deployment" in result.output


class TestListGet:
    def test_list_forwards_scope_and_paging(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("zip_download") as mocks:
            mocks["api_client"].list_zip_downloads.return_value = {"Items": [JOB], "NextToken": "n"}
            result = _invoke(cli_runner, "list", *SCOPE, "--max-items", "5", "--page-size", "2",
                             "--starting-token", "tok", "--json-output")
            assert result.exit_code == 0, result.output
            assert json.loads(result.output)["NextToken"] == "n"
            assert mocks["api_client"].list_zip_downloads.call_args.kwargs == {
                "database_id": "db", "asset_id": "a", "max_items": 5, "page_size": 2,
                "starting_token": "tok", "all_users": False}

    def test_list_all_users_forwards_the_flag_and_prints_the_requester(self, cli_runner,
                                                                       generic_command_mocks):
        shared = dict(JOB, requestedBy="bob", isOwn=False, canDownload=True, canRemove=False)
        with generic_command_mocks("zip_download") as mocks:
            mocks["api_client"].list_zip_downloads.return_value = {"Items": [shared]}
            result = _invoke(cli_runner, "list", *SCOPE, "--all-users", "--json-output")
            assert result.exit_code == 0, result.output
            assert json.loads(result.output)["Items"][0]["requestedBy"] == "bob"
            assert mocks["api_client"].list_zip_downloads.call_args.kwargs["all_users"] is True
            text = _invoke(cli_runner, "list", *SCOPE, "--all-users")
            assert text.exit_code == 0, text.output
            assert "Requested by: bob" in text.output

    def test_list_text(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("zip_download") as mocks:
            mocks["api_client"].list_zip_downloads.return_value = {"Items": []}
            result = _invoke(cli_runner, "list")
            assert result.exit_code == 0, result.output
            assert "No zip downloads found." in result.output

    def test_get_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("zip_download") as mocks:
            mocks["api_client"].get_zip_download.side_effect = ZipDownloadNotFoundError(
                "Zip download not found")
            result = _invoke(cli_runner, "get", *SCOPE, "-r", "r1", "--json-output")
            assert result.exit_code == 1
            assert json.loads(result.output)["error_type"] == "ZipDownloadNotFoundError"


class TestDownload:
    def test_not_ready_without_wait(self, cli_runner, generic_command_mocks, tmp_path):
        with generic_command_mocks("zip_download") as mocks:
            mocks["api_client"].get_zip_download_url.side_effect = ZipDownloadNotReadyError(
                "The archive is not ready for download")
            result = _invoke(cli_runner, "download", *SCOPE, "-r", "r1", "-o", str(tmp_path),
                             "--json-output")
            assert result.exit_code == 1
            assert json.loads(result.output)["error_type"] == "ZipDownloadNotReadyError"
            mocks["api_client"].download_zip_archive.assert_not_called()

    def test_wait_then_download_to_a_file(self, cli_runner, generic_command_mocks, tmp_path):
        with generic_command_mocks("zip_download") as mocks:
            api = mocks["api_client"]
            api.get_zip_download.return_value = {**JOB, "status": "SUCCEEDED"}
            api.get_zip_download_url.return_value = {"url": PRESIGNED, "archiveFileName": "Asset.zip"}
            api.download_zip_archive.return_value = 7
            target = tmp_path / "mine.zip"
            result = _invoke(cli_runner, "download", *SCOPE, "-r", "r1", "-o", str(target), "--wait",
                             "--json-output")
            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            assert data == {"requestId": "r1", "archiveFileName": "Asset.zip",
                            "localPath": str(target), "bytesWritten": 7,
                            "bytesWrittenFormatted": data["bytesWrittenFormatted"]}
            assert PRESIGNED not in result.output

    def test_existing_file_is_kept_without_overwrite(self, cli_runner, generic_command_mocks, tmp_path):
        target = tmp_path / "mine.zip"
        target.write_bytes(b"keep")
        with generic_command_mocks("zip_download") as mocks:
            mocks["api_client"].get_zip_download_url.return_value = {
                "url": PRESIGNED, "archiveFileName": "Asset.zip"}
            result = _invoke(cli_runner, "download", *SCOPE, "-r", "r1", "-o", str(target))
            assert result.exit_code != 0
            assert "--overwrite" in result.output
            mocks["api_client"].download_zip_archive.assert_not_called()
        assert target.read_bytes() == b"keep"


class TestCancelDelete:
    def test_cancel_requires_confirm(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("zip_download") as mocks:
            result = _invoke(cli_runner, "cancel", *SCOPE, "-r", "r1", "--json-output")
            assert result.exit_code == 1
            assert "--confirm" in json.loads(result.output)["error"]
            mocks["api_client"].cancel_zip_download.assert_not_called()

    def test_cancel(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("zip_download") as mocks:
            mocks["api_client"].cancel_zip_download.return_value = {
                "message": "Zip download cancelled", "action": "cancelled"}
            result = _invoke(cli_runner, "cancel", *SCOPE, "-r", "r1", "--confirm", "--json-output")
            assert result.exit_code == 0, result.output
            assert json.loads(result.output)["action"] == "cancelled"
            mocks["api_client"].cancel_zip_download.assert_called_once_with("db", "a", "r1")

    @pytest.mark.parametrize("json_output", [True, False])
    def test_cancel_of_a_job_that_ended_meanwhile(self, cli_runner, generic_command_mocks, json_output):
        """#1125 D22: the API kept a job that finished before the cancel was recorded."""
        answer = {"message": "The zip download finished before it could be cancelled",
                  "action": "ended", "status": "SUCCEEDED"}
        with generic_command_mocks("zip_download") as mocks:
            mocks["api_client"].cancel_zip_download.return_value = answer
            args = ["cancel", *SCOPE, "-r", "r1", "--confirm"] + (["--json-output"] if json_output else [])
            result = _invoke(cli_runner, *args)
            assert result.exit_code == 0, result.output
            if json_output:
                assert json.loads(result.output) == answer
            else:
                assert "finished (SUCCEEDED) before it could be cancelled" in result.output
                assert "deleted" not in result.output
                assert "✓ Zip download cancelled!" not in result.output

    def test_cancel_of_a_finished_job_is_a_state_error(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("zip_download") as mocks:
            mocks["api_client"].cancel_zip_download.side_effect = ZipDownloadStateError(
                "Zip download 'r1' has already finished (SUCCEEDED); delete it instead")
            result = _invoke(cli_runner, "cancel", *SCOPE, "-r", "r1", "--confirm", "--json-output")
            assert result.exit_code == 1
            assert json.loads(result.output)["error_type"] == "ZipDownloadStateError"

    def test_delete(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("zip_download") as mocks:
            mocks["api_client"].delete_zip_download.return_value = {
                "message": "Zip download deleted", "action": "deleted"}
            result = _invoke(cli_runner, "delete", *SCOPE, "-r", "r1", "--confirm")
            assert result.exit_code == 0, result.output
            assert "Zip download deleted" in result.output


class TestProgressText:
    def test_bytes_show_while_the_first_file_still_streams(self):
        """#1125 D11: the container reports bytes inside a member, so a one-file job shows bytes done
        before its file is finished."""
        from vamscli.utils.zip_downloads import progress_text
        text = progress_text({"status": "RUNNING", "progress": {
            "filesDone": 0, "filesTotal": 1, "bytesDone": 512 * 1024 * 1024,
            "bytesTotal": 1024 * 1024 * 1024 * 1024}})
        assert text.startswith("RUNNING, 0/1 files, ")
        assert "512" in text and " of " in text
