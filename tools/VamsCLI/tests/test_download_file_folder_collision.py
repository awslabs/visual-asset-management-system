"""A download is refused before anything is written when a file and a folder share a name (#2070).

A folder asset can hold a file '/a.txt' and files under '/a.txt/' (two ordinary keys in Amazon S3), and an
asset converted from a single file holds '/model.glb' and '/model.glb/...'. No local folder holds a file
and a folder of one name, so `vamscli assets download --recursive` wrote one and failed the other. It
now refuses the download up front, naming both paths and how to download each on its own (--file-key),
as a flattened download already refuses two files of one name.
"""

import json
from unittest.mock import patch

import pytest

from vamscli.main import cli
from vamscli.utils import download_manager
from vamscli.utils.exceptions import FileDownloadError
from tests.conftest import CoroutineClosingMock


@pytest.fixture
def assets_command_mocks(generic_command_mocks):
    return generic_command_mocks('assets')


class TestFileFolderCollision:
    @pytest.mark.parametrize("paths,expected", [
        (["/a.txt", "/a.txt/b.txt"], ("/a.txt", "/a.txt/b.txt")),
        (["/a.txt/b.txt", "/a.txt"], ("/a.txt", "/a.txt/b.txt")),
        (["/x/a.txt", "/x/a.txt/deep/er/c.bin"], ("/x/a.txt", "/x/a.txt/deep/er/c.bin")),
        (["/model.glb", "/model.glb/model.glb", "/model.glb/sub/x.obj"], ("/model.glb", "/model.glb/model.glb")),
        (["/a", "/a//x"], ("/a", "/a//x")),
    ], ids=["file-then-folder", "folder-then-file", "deeper", "converted-asset", "empty-segment"])
    def test_finds_the_file_and_a_path_in_its_folder(self, paths, expected):
        assert download_manager.file_folder_collision(paths) == expected

    @pytest.mark.parametrize("paths", [
        ["/a.txt", "/a.txtx/b"],
        ["/a.txt", "/sub/a.txt", "/b/a.txt/c"],
        ["/model.glb/model.glb", "/model.glb/sub/x.obj"],
        ["/x", "//x"],
        [],
    ], ids=["sibling-with-longer-name", "same-name-elsewhere", "moved-asset", "two-files-one-local-path", "none"])
    def test_no_false_positive(self, paths):
        assert download_manager.file_folder_collision(paths) is None

    def test_the_error_names_both_paths_and_how_to_download_each(self):
        with pytest.raises(FileDownloadError) as error:
            download_manager.check_no_file_folder_collision(["/a.txt", "/a.txt/b.txt"])
        message = str(error.value)
        assert "'/a.txt'" in message and "'/a.txt/b.txt'" in message
        assert "--file-key '/a.txt'" in message and "--file-key '/a.txt/' --recursive" in message


LISTING = {'items': [
    {'relativePath': '/a.txt', 'isFolder': False, 'size': 1},
    {'relativePath': '/a.txt/', 'isFolder': True},
    {'relativePath': '/a.txt/b.txt', 'isFolder': False, 'size': 1},
]}


class TestTheDownloadCommandRefusesBeforeWriting:
    @pytest.mark.parametrize("args", [
        ['--recursive'],
        [],
        ['--file-key', '/', '--recursive'],
    ], ids=["recursive", "whole-asset", "root-folder"])
    @patch('vamscli.commands.assets.asyncio.run', new_callable=CoroutineClosingMock)
    @patch('vamscli.commands.assets.generate_presigned_urls')
    def test_nothing_is_presigned_or_written(self, mock_urls, mock_run, args, cli_runner,
                                             assets_command_mocks, tmp_path):
        with assets_command_mocks as mocks:
            mocks['api_client'].list_asset_files.return_value = LISTING
            result = cli_runner.invoke(cli, ['assets', 'download', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                                             '--json-output'] + args)
        assert result.exit_code == 1, result.output
        message = json.loads(result.output).get('error', '')
        assert "'/a.txt'" in message and "'/a.txt/b.txt'" in message and '--file-key' in message
        mock_urls.assert_not_called()
        mock_run.assert_not_called()
        assert list(tmp_path.iterdir()) == []

    @patch('vamscli.commands.assets.asyncio.run', new_callable=CoroutineClosingMock)
    @patch('vamscli.commands.assets.generate_presigned_urls')
    def test_the_folder_alone_still_downloads(self, mock_urls, mock_run, cli_runner, assets_command_mocks,
                                              tmp_path):
        mock_run.return_value = ({'overall_success': True, 'total_files': 1, 'successful_files': 1,
                                  'failed_files': 0, 'successful_downloads': [], 'failed_downloads': []}, None)
        with assets_command_mocks as mocks:
            mocks['api_client'].list_asset_files.return_value = LISTING
            result = cli_runner.invoke(cli, ['assets', 'download', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                                             '--file-key', '/a.txt/', '--recursive', '--json-output'])
        assert 'is in a folder of the same name' not in result.output
        mock_run.assert_called_once()
