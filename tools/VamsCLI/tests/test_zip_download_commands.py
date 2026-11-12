"""The 'vamscli zip-download' commands: arguments reaching the APIClient, --json-output purity,
the disabled-feature business error, --wait, the archive download, and the URL never printed."""

import json
from unittest.mock import patch

import pytest

from vamscli.main import cli
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

    def test_download_to_streams_the_archive_and_never_prints_the_url(
            self, cli_runner, generic_command_mocks, tmp_path):
        with generic_command_mocks("zip_download") as mocks:
            api = mocks["api_client"]
            api.create_zip_download.return_value = JOB
            api.get_zip_download.return_value = {**JOB, "status": "SUCCEEDED", "archiveSizeBytes": 4}
            api.get_zip_download_url.return_value = {
                "url": PRESIGNED, "expiresIn": 900, "archiveFileName": "Asset.zip",
                "archiveSizeBytes": 4}
            api.download_zip_archive.return_value = 4

            for json_flag in ([], ["--json-output"]):
                result = _invoke(cli_runner, "create", *SCOPE, "--whole-asset", "--download-to",
                                 str(tmp_path), *json_flag)
                assert result.exit_code == 0, result.output
                assert "X-Amz-Signature" not in result.output and PRESIGNED not in result.output

            data = json.loads(result.output)
            assert data["download"]["localPath"] == str(tmp_path / "Asset.zip")
            assert data["download"]["bytesWritten"] == 4
            url, path = api.download_zip_archive.call_args.args
            assert url == PRESIGNED and path == str(tmp_path / "Asset.zip")

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
