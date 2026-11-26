"""`file upload` and `sync file push` say why each failed file was not stored (#1072).

When the API refused an upload (a role that may read but not upload, or a file key over the upload
bound), `vamscli file upload` listed the file under "Failed files" with no reason, in plain and in
`--json-output` output; the reason was in the upload manager's result, and `--verbose` was the only way
to see it. Each sequence result now carries `failed_file_errors` (`[{"relative_key", "error"}]`), the
plain output prints `- <key>: <reason>`, `--json-output` keeps the sequence's `error` and
`failed_file_errors`, and `sync file push` lists each failed upload as it lists a failed delete.

A reason reaches stdout, which scripts and the connectors capture, so it is masked for credential
shapes and bounded, and an Amazon S3 error body never becomes part of one: a refused presigned part
upload can echo its canonical request, security token included.

The upload result keeps `failed_files` as a list of relative keys and adds no top-level `error`: the
Isaac Sim connector reads `sequence_results[].failed_files` as strings and treats a top-level `error`
as an error document (`vams_cli_service._try_parse_error`).
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vamscli.main import cli
from vamscli.utils.exceptions import InvalidAssetDataError, PartUploadError, PermissionDeniedError
from vamscli.utils.file_processor import FileInfo, UploadSequence
from vamscli.utils.logging import REDACTED
from vamscli.utils.upload_manager import (
    MAX_FAILURE_REASON_CHARS, NO_REASON_GIVEN, PartUploadInfo, SequenceInitResult, UploadManager,
    UploadProgress, failed_file_reasons,
)

from tests.test_sync_commands import mock_asyncio_run, setup_api_defaults

KEY_TOO_LONG = ("Invalid upload data: VAMS General Error: File key is too long to upload: a file key, "
                "including its asset location, may be at most 998 bytes")
NOT_AUTHORIZED = "Access forbidden. Not Authorized"
FAKE_SESSION_TOKEN = "IQoJb3JpZ2luX2VjEFAKEFAKEFAKEFAKEFAKE"


def _sequence(keys, size=1024, sequence_id=0):
    files = [FileInfo(local_path=f"/nonexistent/{index}.bin", relative_key=key, size=size)
             for index, key in enumerate(keys)]
    sequence = UploadSequence(files, sequence_id)
    sequence.calculate_parts()
    return sequence


def _init_response(sequence, upload_id="upload-1"):
    return {"uploadId": upload_id, "files": [
        {"relativeKey": f.relative_key, "uploadIdS3": f"s3-{f.relative_key}",
         "partUploadUrls": [{"UploadUrl": f"https://s3.invalid/{i}"}
                            for i in range(len(sequence.file_parts[f.relative_key]))]}
        for f in sequence.files]}


def _manager():
    api_client = MagicMock()
    manager = UploadManager(api_client)
    manager._api_semaphore = asyncio.Semaphore(2)
    return manager, api_client


def _complete(manager, sequence, part_status="completed", part_error=None):
    """Drive `_complete_sequence` with every part in `part_status`."""
    init_response = _init_response(sequence)
    parts = []
    for file_response in init_response["files"]:
        key = file_response["relativeKey"]
        file_info = next(f for f in sequence.files if f.relative_key == key)
        for index, url in enumerate(file_response["partUploadUrls"]):
            part = PartUploadInfo(file_info, sequence.file_parts[key][index], url["UploadUrl"],
                                  sequence.sequence_id)
            part.status = part_status
            part.etag = f"etag-{index}"
            part.error = part_error
            parts.append(part)
    init_result = SequenceInitResult(sequence.sequence_id, "upload-1", init_response, sequence, parts)
    return asyncio.run(manager._complete_sequence(
        init_result, "db1", "asset1", "assetFile", UploadProgress([sequence])))


def _upload_all(manager, sequences):
    async def run():
        async with manager:
            return await manager.upload_all_sequences(sequences, "db1", "asset1", "assetFile")
    return asyncio.run(run())


class TestEachFailedFileCarriesItsReason:
    def test_a_sequence_refused_at_initialization_names_the_refusal_for_every_file(self):
        api_client = MagicMock()
        api_client.initialize_upload.side_effect = InvalidAssetDataError(KEY_TOO_LONG)
        sequence = _sequence(["/a.bin", "/b.bin"])

        summary = _upload_all(UploadManager(api_client), [sequence])

        assert summary["overall_success"] is False
        reasons = {e["relative_key"]: e["error"] for e in failed_file_reasons(summary)}
        assert set(reasons) == {"/a.bin", "/b.bin"}
        for reason in reasons.values():
            assert "File key is too long to upload" in reason and "998 bytes" in reason
        assert summary["sequence_results"][0]["failed_file_errors"]

    def test_a_permission_denial_at_initialization_is_named(self):
        api_client = MagicMock()
        api_client.initialize_upload.side_effect = PermissionDeniedError(NOT_AUTHORIZED)

        summary = _upload_all(UploadManager(api_client), [_sequence(["/a.bin"])])

        assert failed_file_reasons(summary) == [{
            "relative_key": "/a.bin",
            "error": f"Failed to initialize upload for sequence 0: {NOT_AUTHORIZED}"}]

    def test_a_file_refused_at_completion_carries_the_servers_reason(self):
        manager, api_client = _manager()
        sequence = _sequence(["/model.glb", "/model.glb.previewFile.png"])
        api_client.complete_upload.return_value = {"overallSuccess": False, "fileResults": [
            {"relativeKey": "/model.glb", "success": True},
            {"relativeKey": "/model.glb.previewFile.png", "success": False,
             "error": "Base files does not exist for all preview files"}]}

        result = _complete(manager, sequence)

        assert result["failed_file_errors"] == [{
            "relative_key": "/model.glb.previewFile.png",
            "error": "Base files does not exist for all preview files"}]

    def test_a_completion_that_stored_nothing_carries_the_servers_message(self):
        manager, api_client = _manager()
        api_client.complete_upload.return_value = {
            "message": "No files were successfully uploaded", "overallSuccess": False}

        result = _complete(manager, _sequence(["/a.bin"]))

        assert result["failed_file_errors"] == [
            {"relative_key": "/a.bin", "error": "No files were successfully uploaded"}]

    def test_a_part_that_did_not_upload_is_named_with_its_error(self):
        manager, _api_client = _manager()

        result = _complete(manager, _sequence(["/a.bin"]), part_status="failed",
                           part_error="Amazon S3 answered HTTP 403 (AccessDenied)")

        assert result["failed_file_errors"] == [{
            "relative_key": "/a.bin",
            "error": "part 1 of 1 did not upload: Amazon S3 answered HTTP 403 (AccessDenied)"}]

    def test_a_successful_sequence_has_no_failed_file_errors(self):
        manager, api_client = _manager()
        sequence = _sequence(["/a.bin"])
        api_client.complete_upload.return_value = {
            "overallSuccess": True, "fileResults": [{"relativeKey": "/a.bin", "success": True}]}

        result = _complete(manager, sequence)

        assert result["failed_files"] == [] and result["failed_file_errors"] == []


class TestAReasonIsSafeToPrint:
    def test_an_s3_error_body_is_reduced_to_its_status_and_code(self):
        manager, _api_client = _manager()
        sequence = _sequence(["/a.bin"])
        part = PartUploadInfo(sequence.files[0], sequence.file_parts["/a.bin"][0],
                              "https://s3.invalid/0", 0)
        body = ("<Error><Code>SignatureDoesNotMatch</Code><CanonicalRequest>PUT /a.bin "
                f"X-Amz-Security-Token={FAKE_SESSION_TOKEN}&amp;X-Amz-Signature=abc</CanonicalRequest></Error>")

        class _Response:
            status = 403

            async def text(self):
                return body

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        manager.session = MagicMock()
        manager.session.put.return_value = _Response()
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(b"x" * 1024)
        part.file_info.local_path = handle.name
        try:
            with pytest.raises(PartUploadError) as raised:
                asyncio.run(manager._upload_single_part(part))
        finally:
            os.unlink(handle.name)

        assert str(raised.value) == "Amazon S3 answered HTTP 403 (SignatureDoesNotMatch)"
        assert FAKE_SESSION_TOKEN not in str(raised.value)

    def test_credential_shapes_in_a_reason_are_masked(self):
        api_client = MagicMock()
        api_client.initialize_upload.side_effect = InvalidAssetDataError(
            f"refused https://s3.invalid/k?X-Amz-Security-Token={FAKE_SESSION_TOKEN}&x=1")

        summary = _upload_all(UploadManager(api_client), [_sequence(["/a.bin"])])

        reason = failed_file_reasons(summary)[0]["error"]
        assert FAKE_SESSION_TOKEN not in reason and REDACTED in reason
        assert FAKE_SESSION_TOKEN not in json.dumps(
            {k: v for k, v in summary.items() if k != "progress"})

    def test_a_long_reason_is_bounded(self):
        api_client = MagicMock()
        api_client.initialize_upload.side_effect = InvalidAssetDataError("x" * 5000)

        summary = _upload_all(UploadManager(api_client), [_sequence(["/a.bin"])])

        reason = failed_file_reasons(summary)[0]["error"]
        assert len(reason) == MAX_FAILURE_REASON_CHARS and reason.endswith("...")


class TestOlderResultShapesStillListEveryFailedFile:
    def test_a_sequence_with_only_an_error_lends_it_to_its_files(self):
        result = {"sequence_results": [{"error": "sequence refused", "failed_files": ["/a", "/b"]}]}
        assert failed_file_reasons(result) == [
            {"relative_key": "/a", "error": "sequence refused"},
            {"relative_key": "/b", "error": "sequence refused"}]

    def test_a_sequence_with_no_reason_says_so(self):
        result = {"sequence_results": [{"failed_files": ["/a"]}]}
        assert failed_file_reasons(result) == [{"relative_key": "/a", "error": NO_REASON_GIVEN}]


@pytest.fixture
def local_file():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as handle:
        handle.write(b"test content")
    yield handle.name
    Path(handle.name).unlink()


class TestFileUploadPrintsTheReason:
    """The command runs the real upload manager against a mocked API client that refuses the upload."""

    def _run(self, cli_runner, generic_command_mocks, local_file, *extra):
        with generic_command_mocks("file") as mocks:
            mocks["api_client"].get_database.return_value = {
                "databaseId": "test-db", "restrictFileUploadsToExtensions": ""}
            mocks["api_client"].initialize_upload.side_effect = InvalidAssetDataError(KEY_TOO_LONG)
            result = cli_runner.invoke(cli, ["file", "upload", "-d", "test-db", "-a", "test-asset",
                                             local_file, *extra])
        return result

    def test_the_plain_output_names_the_refusal(self, cli_runner, generic_command_mocks, local_file):
        result = self._run(cli_runner, generic_command_mocks, local_file)

        assert result.exit_code == 1
        key = "/" + Path(local_file).name
        assert f"  - {key}: Failed to initialize upload for sequence 1: {KEY_TOO_LONG}" in result.output

    def test_the_json_output_carries_the_refusal_per_file(self, cli_runner, generic_command_mocks,
                                                          local_file):
        result = self._run(cli_runner, generic_command_mocks, local_file, "--json-output")

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        sequence = data["sequence_results"][0]
        key = "/" + Path(local_file).name
        assert sequence["failed_files"] == [key]
        assert sequence["failed_file_errors"] == [
            {"relative_key": key, "error": f"Failed to initialize upload for sequence 1: {KEY_TOO_LONG}"}]
        assert KEY_TOO_LONG in sequence["error"]
        # A transfer report, not an error document: the Isaac Sim connector reads a top-level
        # `error` as the latter.
        assert "error" not in data and data["overall_success"] is False


class TestSyncPushPrintsTheReason:
    def test_each_failed_upload_is_listed_with_its_reason(self, cli_runner, generic_command_mocks,
                                                          tmp_path):
        (tmp_path / "new.txt").write_text("new file")
        failed_summary = {
            "overall_success": False, "total_files": 1, "successful_files": 0, "failed_files": 1,
            "total_size": 8, "total_size_formatted": "8B", "upload_duration": 0.5, "average_speed": 0,
            "average_speed_formatted": "0B/s",
            "sequence_results": [{"error": "x", "failed_files": ["/new.txt"], "failed_file_errors": [
                {"relative_key": "/new.txt", "error": NOT_AUTHORIZED}]}]}
        with generic_command_mocks("sync") as mocks:
            setup_api_defaults(mocks["api_client"])
            with patch("vamscli.commands.sync.asyncio.run", side_effect=mock_asyncio_run(failed_summary)):
                result = cli_runner.invoke(cli, ["sync", "file", "push", str(tmp_path),
                                                 "-d", "db1", "-a", "asset1"])

        assert result.exit_code != 0
        assert f"  ✗ /new.txt: {NOT_AUTHORIZED}" in result.output
