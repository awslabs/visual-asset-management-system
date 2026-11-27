"""Two files of one download are never saved at one local path (#1977).

A local folder cannot have an empty name, so `/cloud.las` (the key `A1/cloud.las`) and `//cloud.las` (the key
`A1//cloud.las`, an empty segment after the asset's folder) both map to `<target>/cloud.las`. Before, both were
downloaded there and the one that finished last replaced the other, with a success result. Now the file whose
path has no empty segment is saved, and the other one is reported as a failed download naming it, so the
command exits non-zero and `--json-output` lists it in `failed_downloads`. A flattened export of two files of one
name is held to the same rule. The command tests drive the real `DownloadManager` with only the HTTP session
faked, as tests/test_download_path_confinement.py does.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from vamscli.commands.assetsExport import download_export_files
from vamscli.main import cli
from vamscli.utils.download_manager import (
    DOWNLOAD_PATH_OUTSIDE_TARGET_REASON, DOWNLOAD_PATH_TAKEN_REASON, confined_local_paths)
from tests.test_download_total_size import FakeSession, FakeStreamResponse

ROOT = "/cloud.las"
EMPTY_SEGMENT = "//cloud.las"
# S3 lists 'A1//cloud.las' before 'A1/cloud.las' ('/' sorts before letters), so the file list does too
LISTED = (EMPTY_SEGMENT, ROOT)
CONTENT = {ROOT: b"root-file", EMPTY_SEGMENT: b"empty-segment-file"}


def _url(path):
    return f"https://example.invalid/f{path}"


def _session():
    return FakeSession({_url(p): FakeStreamResponse(chunks=[CONTENT[p]], content_length=len(CONTENT[p]))
                        for p in LISTED})


class TestConfinedLocalPaths:
    def test_the_path_without_an_empty_segment_keeps_the_local_path(self, tmp_path):
        placements = confined_local_paths(tmp_path, [(p, p) for p in LISTED])
        assert placements == [(None, f"{DOWNLOAD_PATH_TAKEN_REASON} ({ROOT})"), (tmp_path / "cloud.las", None)]

    def test_otherwise_the_first_listed_file_keeps_it(self, tmp_path):
        placements = confined_local_paths(tmp_path, [("/a/x", "x"), ("/b/x", "x"), ("/c/y", "y")])
        assert placements == [(tmp_path / "x", None), (None, f"{DOWNLOAD_PATH_TAKEN_REASON} (/a/x)"),
                              (tmp_path / "y", None)]

    def test_a_path_outside_the_target_keeps_its_own_reason(self, tmp_path):
        placements = confined_local_paths(tmp_path, [("/../x", "/../x"), ("/x", "/x")])
        assert placements == [(None, DOWNLOAD_PATH_OUTSIDE_TARGET_REASON), (tmp_path / "x", None)]

    def test_files_of_different_paths_are_all_placed(self, tmp_path):
        paths = ["/a.las", "/sub/a.las", "//sub/b.las"]
        assert [p for p, _ in confined_local_paths(tmp_path, [(p, p) for p in paths])] == [
            tmp_path / "a.las", tmp_path / "sub" / "a.las", tmp_path / "sub" / "b.las"]


@pytest.fixture
def assets_command_mocks(generic_command_mocks):
    return generic_command_mocks("assets")


class TestAssetsDownload:
    @pytest.mark.parametrize("extra_args", [
        [],  # whole asset, streamed
        ["--file-key", "/", "--recursive"],  # folder, streamed
        ["--file-key", "/", "--recursive", "--file-previews"],  # folder, not streamed
    ])
    def test_the_root_file_is_saved_and_the_other_is_reported(
            self, cli_runner, assets_command_mocks, tmp_path, extra_args):
        target = tmp_path / "out"
        with (
            assets_command_mocks as mocks,
            patch("vamscli.commands.assets.generate_presigned_urls",
                  return_value={p: {"downloadUrl": _url(p)} for p in LISTED}),
            patch("vamscli.utils.download_manager.aiohttp.TCPConnector", MagicMock()),
            patch("vamscli.utils.download_manager.aiohttp.ClientSession", return_value=_session()),
        ):
            mocks["api_client"].list_asset_files.return_value = {
                "items": [{"relativePath": p, "isFolder": False, "size": len(CONTENT[p])} for p in LISTED]}
            # No file has a preview
            mocks["api_client"].download_asset_file.side_effect = Exception("no preview")
            result = cli_runner.invoke(cli, [
                "assets", "download", str(target), "-d", "test-database", "-a", "test-asset", *extra_args,
                "--json-output"])

        assert (target / "cloud.las").read_bytes() == CONTENT[ROOT]
        assert sorted(p.name for p in target.rglob("*")) == ["cloud.las"]
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["overall_success"] is False and data["successful_files"] == 1
        assert [(f["relative_key"], f["error"]) for f in data["failed_downloads"]] == [
            (EMPTY_SEGMENT, f"{DOWNLOAD_PATH_TAKEN_REASON} ({ROOT})")]

    def test_the_text_output_names_the_file_not_downloaded(self, cli_runner, assets_command_mocks, tmp_path):
        with (
            assets_command_mocks as mocks,
            patch("vamscli.commands.assets.generate_presigned_urls",
                  return_value={p: {"downloadUrl": _url(p)} for p in LISTED}),
            patch("vamscli.utils.download_manager.aiohttp.TCPConnector", MagicMock()),
            patch("vamscli.utils.download_manager.aiohttp.ClientSession", return_value=_session()),
        ):
            mocks["api_client"].list_asset_files.return_value = {
                "items": [{"relativePath": p, "isFolder": False, "size": len(CONTENT[p])} for p in LISTED]}
            result = cli_runner.invoke(cli, [
                "assets", "download", str(tmp_path / "out"), "-d", "test-database", "-a", "test-asset",
                "--hide-progress"])

        assert result.exit_code == 1
        assert f"{EMPTY_SEGMENT}: {DOWNLOAD_PATH_TAKEN_REASON} ({ROOT})" in result.output


class TestExportDownload:
    @pytest.mark.parametrize("organize_by_asset, flatten", [(False, False), (True, False), (False, True)])
    def test_two_files_of_one_local_path_are_not_merged(self, tmp_path, organize_by_asset, flatten):
        export_result = {"assets": [{"assetid": "A1", "files": [
            {"relativePath": p, "fileName": "cloud.las", "size": len(CONTENT[p]), "presignedFileDownloadUrl": _url(p)}
            for p in LISTED]}]}
        with (
            patch("vamscli.utils.download_manager.aiohttp.TCPConnector", MagicMock()),
            patch("vamscli.utils.download_manager.aiohttp.ClientSession", return_value=_session()),
        ):
            result = asyncio.run(download_export_files(
                export_result, tmp_path, organize_by_asset, flatten, 2, 30, True, True))

        saved = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert len(saved) == 1 and saved[0].read_bytes() == CONTENT[ROOT]
        assert result["overall_success"] is False
        assert result["successful_files"] == 1 and result["failed_files"] == 1
        assert result["failed_downloads"][-1] == {
            "relative_key": f"A1/{EMPTY_SEGMENT}", "local_path": "",
            "error": f"{DOWNLOAD_PATH_TAKEN_REASON} ({ROOT})"}
