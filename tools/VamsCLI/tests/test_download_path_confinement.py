"""A download writes each file inside the directory it was given.

`assets download` (whole asset, folder, asset tree, single key) and `assets export --download` place each
file at `<target>/<the file's asset-relative path>`. A path that would land outside `<target>` is not
written; the file is reported as a failed download with a fixed reason, and the command exits non-zero.
The command tests drive the real `DownloadManager`, with only the HTTP session faked, because a mocked
`asyncio.run` never runs the streamed paths' producer.
"""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vamscli.commands.assetsExport import download_export_files
from vamscli.main import cli
from vamscli.utils.download_manager import DOWNLOAD_PATH_OUTSIDE_TARGET_REASON, confined_local_path
from tests.test_download_total_size import FakeSession, FakeStreamResponse


class TestConfinedLocalPath:
    @pytest.mark.parametrize(
        "relative",
        [
            "/a.txt",
            "a.txt",
            "/folder/sub/a.txt",
            "folder//a.txt",
            "/x..y/a..b.txt",
            "/.hidden",
        ],
    )
    def test_a_path_inside_the_target_is_joined(self, tmp_path, relative):
        assert confined_local_path(tmp_path, relative) == tmp_path / relative.lstrip("/")

    @pytest.mark.parametrize(
        "relative",
        [
            "",
            "/",
            "//",
            "..",
            "/..",
            "../a.txt",
            "/../a.txt",
            "/folder/../../a.txt",
            "a/..",
            "folder\\..\\..\\a.txt",
            "/a\x00b.txt",
        ],
    )
    def test_a_path_that_leaves_the_target_is_refused(self, tmp_path, relative):
        assert confined_local_path(tmp_path, relative) is None

    def test_a_symlink_out_of_the_target_is_refused(self, tmp_path):
        target = tmp_path / "out"
        target.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        try:
            os.symlink(elsewhere, target / "link", target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not available here")
        assert confined_local_path(target, "/link/a.txt") is None
        assert confined_local_path(target, "/real/a.txt") == target / "real" / "a.txt"

    def test_a_relative_target_directory_is_resolved_for_the_check(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert confined_local_path(Path("out"), "/a.txt") == Path("out") / "a.txt"
        assert confined_local_path(Path("out"), "/../a.txt") is None


@pytest.fixture
def assets_command_mocks(generic_command_mocks):
    return generic_command_mocks("assets")


def _listing(*relative_paths):
    return {"items": [{"relativePath": p, "isFolder": False, "size": 2} for p in relative_paths]}


def _session(*relative_paths):
    return FakeSession(
        {
            f"https://example.invalid{p}": FakeStreamResponse(chunks=[b"ok"], content_length=2)
            for p in relative_paths
        }
    )


def _urls(*relative_paths):
    return {p: {"downloadUrl": f"https://example.invalid{p}"} for p in relative_paths}


OUTSIDE = ("/../escape.txt", "/folder/../../escape2.txt")


class TestAssetsDownloadStaysInTheTarget:
    @pytest.mark.parametrize(
        "extra_args",
        [
            [],  # whole asset, streamed
            ["--file-key", "/", "--recursive"],  # folder, streamed
            ["--file-key", "/", "--recursive", "--flatten-download-tree"],  # folder, not streamed
        ],
    )
    def test_a_listed_path_that_leaves_the_target_is_not_written(
        self, cli_runner, assets_command_mocks, tmp_path, extra_args
    ):
        target = tmp_path / "out"
        keys = ("/ok.txt", *OUTSIDE)
        with (
            assets_command_mocks as mocks,
            patch("vamscli.commands.assets.generate_presigned_urls", return_value=_urls(*keys)),
            patch("vamscli.utils.download_manager.aiohttp.TCPConnector", MagicMock()),
            patch(
                "vamscli.utils.download_manager.aiohttp.ClientSession", return_value=_session(*keys)
            ),
        ):
            mocks["api_client"].list_asset_files.return_value = _listing(*keys)

            result = cli_runner.invoke(
                cli,
                [
                    "assets",
                    "download",
                    str(target),
                    "-d",
                    "test-database",
                    "-a",
                    "test-asset",
                    *extra_args,
                    "--json-output",
                ],
            )

        assert (target / "ok.txt").read_bytes() == b"ok"
        assert not list(tmp_path.glob("escape*"))
        data = json.loads(result.output)
        refused = {f["relative_key"]: f["error"] for f in data["failed_downloads"]}
        if "--flatten-download-tree" in extra_args:
            # Flattened, each file keeps only its name, which stays inside the target.
            assert result.exit_code == 0, result.output
            assert refused == {} and (target / "escape.txt").exists()
        else:
            assert result.exit_code == 1, result.output
            assert refused == {key: DOWNLOAD_PATH_OUTSIDE_TARGET_REASON for key in OUTSIDE}
            assert data["overall_success"] is False and data["successful_files"] == 1

    def test_a_file_key_that_leaves_the_target_is_refused_before_any_request(
        self, cli_runner, assets_command_mocks, tmp_path
    ):
        with assets_command_mocks as mocks:
            result = cli_runner.invoke(
                cli,
                [
                    "assets",
                    "download",
                    str(tmp_path / "out"),
                    "-d",
                    "test-database",
                    "-a",
                    "test-asset",
                    "--file-key",
                    "/../escape.txt",
                    "--json-output",
                ],
            )
            mocks["api_client"].download_asset_file.assert_not_called()
        assert result.exit_code == 1
        assert DOWNLOAD_PATH_OUTSIDE_TARGET_REASON in json.loads(result.output)["error"]


class TestFlattenedNamesStayInTheTarget:
    def test_a_name_of_dot_dot_is_refused(self, cli_runner, assets_command_mocks, tmp_path):
        """`Path('a/..').name` is '..': flattening must not turn it into the parent directory."""
        keys = ("/ok.txt", "/folder/..")
        with (
            assets_command_mocks as mocks,
            patch("vamscli.commands.assets.generate_presigned_urls", return_value=_urls(*keys)),
            patch("vamscli.utils.download_manager.aiohttp.TCPConnector", MagicMock()),
            patch(
                "vamscli.utils.download_manager.aiohttp.ClientSession", return_value=_session(*keys)
            ),
        ):
            mocks["api_client"].list_asset_files.return_value = _listing(*keys)
            result = cli_runner.invoke(
                cli,
                [
                    "assets",
                    "download",
                    str(tmp_path / "out"),
                    "-d",
                    "test-database",
                    "-a",
                    "test-asset",
                    "--file-key",
                    "/",
                    "--recursive",
                    "--flatten-download-tree",
                    "--json-output",
                ],
            )
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert {f["relative_key"]: f["error"] for f in data["failed_downloads"]} == {
            "/folder/..": DOWNLOAD_PATH_OUTSIDE_TARGET_REASON
        }


class TestExportDownloadStaysInTheTarget:
    @pytest.mark.parametrize(
        "organize_by_asset, flatten", [(False, False), (True, False), (False, True)]
    )
    def test_an_exported_path_that_leaves_the_target_is_not_written(
        self, tmp_path, organize_by_asset, flatten
    ):
        target = tmp_path / "out"
        export_result = {
            "assets": [
                {
                    "assetid": "asset-1",
                    "files": [
                        {
                            "relativePath": "/ok.txt",
                            "fileName": "ok.txt",
                            "size": 2,
                            "presignedFileDownloadUrl": "https://example.invalid/ok.txt",
                        },
                        {
                            "relativePath": "/../../escape.txt",
                            "fileName": "..",
                            "size": 2,
                            "presignedFileDownloadUrl": "https://example.invalid/escape.txt",
                        },
                    ],
                }
            ]
        }
        session = FakeSession(
            {
                "https://example.invalid/ok.txt": FakeStreamResponse(
                    chunks=[b"ok"], content_length=2
                ),
                "https://example.invalid/escape.txt": FakeStreamResponse(
                    chunks=[b"no"], content_length=2
                ),
            }
        )
        with (
            patch("vamscli.utils.download_manager.aiohttp.TCPConnector", MagicMock()),
            patch("vamscli.utils.download_manager.aiohttp.ClientSession", return_value=session),
        ):
            result = asyncio.run(
                download_export_files(
                    export_result, target, organize_by_asset, flatten, 2, 30, True, True
                )
            )

        assert not list(tmp_path.rglob("escape*"))
        assert result["overall_success"] is False
        assert result["successful_files"] == 1 and result["failed_files"] == 1
        assert result["failed_downloads"][-1]["error"] == DOWNLOAD_PATH_OUTSIDE_TARGET_REASON
