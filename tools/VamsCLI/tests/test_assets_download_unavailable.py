"""A download that could not fetch files must say so and exit non-zero.

Two defects are pinned here, both found by running the real CLI against a deployment and both present
in v2.5.3:

1. **Files the API refuses to presign were dropped silently.** Each one printed a warning and took a
   `continue`, never entering `failed_downloads` and never affecting `overall_success`. The clearest
   trigger is a non-distributable asset: the listing returns its files, the bulk presign rejects every
   one with "Asset not distributable", and the command then reported `overall_success: true`,
   `total_files: 0` and exit 0 — an empty output directory presented as a complete download. Under
   `--json-output` the warnings are suppressed, so a machine consumer got no signal whatsoever.

2. **`list(set(conflicts))` in the flattened-download conflict path called a Click command.**
   `assets list` is defined in `commands/assets.py` as `def list(...)`, which shadows the builtin at
   module scope, and a Click command object is callable — so that line ran the entire `assets list`
   command as a nested CLI program. The observed symptom was a download printing "Listing all
   assets..." and an asset listing in place of its own result.

Both cases drive the NON-streamed folder path (`--file-key <dir>/ --recursive
--flatten-download-tree`), because that is the only path where the presign loop runs in the command
body: on the streaming paths the loop lives inside a coroutine that these tests' mocked `asyncio.run`
never executes, so a test there would pass whatever the code did.
"""

import json
from unittest.mock import Mock, patch

import pytest

from vamscli.main import cli
from tests.conftest import CoroutineClosingMock


@pytest.fixture
def assets_command_mocks(generic_command_mocks):
    """Assets-specific command mocks."""
    return generic_command_mocks('assets')


def _listing(*relative_paths):
    return {
        'items': [
            {'relativePath': p, 'isFolder': False, 'size': 1024} for p in relative_paths
        ]
    }


def _download_result(successful_keys):
    """What DownloadManager.download_files returns for an all-succeeded run."""
    return {
        'overall_success': True,
        'total_files': len(successful_keys),
        'successful_files': len(successful_keys),
        'failed_files': 0,
        'total_size': 1024 * len(successful_keys),
        'total_size_formatted': '1.0 KB',
        'download_duration': 0.5,
        'average_speed': 2048,
        'average_speed_formatted': '2.0 KB/s',
        'successful_downloads': [
            {'relative_key': k, 'local_path': f'/tmp/{k.lstrip("/")}', 'size': 1024}
            for k in successful_keys
        ],
        'failed_downloads': [],
    }


class TestUnavailableDownloadsAreFailures:
    """A file the API would not presign is a failure, not a silent skip."""

    @patch('vamscli.commands.assets.asyncio.run', new_callable=CoroutineClosingMock)
    @patch('vamscli.commands.assets.generate_presigned_urls')
    def test_every_file_refused_reports_failure_and_exits_nonzero(
        self, mock_urls, mock_asyncio_run, cli_runner, assets_command_mocks
    ):
        with assets_command_mocks as mocks:
            mocks['api_client'].list_asset_files.return_value = _listing(
                '/folder/a.txt', '/folder/b.txt'
            )
            # The shape the bulk presign returns when it rejects a file: an entry with an error and no
            # downloadUrl.
            mock_urls.return_value = {
                '/folder/a.txt': {'error': 'Asset not distributable'},
                '/folder/b.txt': {'error': 'Asset not distributable'},
            }
            # Nothing was queued, so the manager reports an empty run — which is exactly what used to
            # be reported to the user as a success.
            mock_asyncio_run.return_value = _download_result([])

            result = cli_runner.invoke(cli, [
                'assets', 'download', '/tmp',
                '-d', 'test-database', '-a', 'test-asset',
                '--file-key', '/folder/', '--recursive', '--flatten-download-tree',
                '--json-output',
            ])

            assert result.exit_code == 1, (
                'a download that fetched nothing must not exit 0; output: ' + result.output
            )
            data = json.loads(result.output)
            # On this path nothing is ever queued, so the pre-existing "nothing to download" guard is
            # reached before a result exists and the outcome is an error payload rather than a download
            # result. What matters is that the error names the REASON: "No files to download" reads as
            # an empty asset, which is a different situation with a different remedy.
            message = data.get('error', '')
            assert 'could not be prepared for download' in message, data
            assert 'not distributable' in message, data
            assert '/folder/a.txt' in message, data

    @patch('vamscli.commands.assets.asyncio.run', new_callable=CoroutineClosingMock)
    @patch('vamscli.commands.assets.generate_presigned_urls')
    def test_partially_refused_still_counts_what_succeeded(
        self, mock_urls, mock_asyncio_run, cli_runner, assets_command_mocks
    ):
        with assets_command_mocks as mocks:
            mocks['api_client'].list_asset_files.return_value = _listing(
                '/folder/a.txt', '/folder/b.txt'
            )
            mock_urls.return_value = {
                '/folder/a.txt': {'downloadUrl': 'https://example.invalid/a'},
                '/folder/b.txt': {'error': 'Object is archived'},
            }
            mock_asyncio_run.return_value = _download_result(['/folder/a.txt'])

            result = cli_runner.invoke(cli, [
                'assets', 'download', '/tmp',
                '-d', 'test-database', '-a', 'test-asset',
                '--file-key', '/folder/', '--recursive', '--flatten-download-tree',
                '--json-output',
            ])

            assert result.exit_code == 1
            data = json.loads(result.output)
            assert data['overall_success'] is False
            # The successful file must still be reported as successful; folding the refusals in must
            # not erase what did transfer.
            assert data['successful_files'] == 1
            assert data['failed_files'] == 1
            assert data['total_files'] == 2
            assert [f['relative_key'] for f in data['failed_downloads']] == ['/folder/b.txt']

    @patch('vamscli.commands.assets.asyncio.run', new_callable=CoroutineClosingMock)
    @patch('vamscli.commands.assets.generate_presigned_urls')
    def test_all_urls_available_still_exits_zero(
        self, mock_urls, mock_asyncio_run, cli_runner, assets_command_mocks
    ):
        """Positive control: without this, always failing would satisfy the cases above."""
        with assets_command_mocks as mocks:
            mocks['api_client'].list_asset_files.return_value = _listing(
                '/folder/a.txt', '/folder/b.txt'
            )
            mock_urls.return_value = {
                '/folder/a.txt': {'downloadUrl': 'https://example.invalid/a'},
                '/folder/b.txt': {'downloadUrl': 'https://example.invalid/b'},
            }
            mock_asyncio_run.return_value = _download_result(['/folder/a.txt', '/folder/b.txt'])

            result = cli_runner.invoke(cli, [
                'assets', 'download', '/tmp',
                '-d', 'test-database', '-a', 'test-asset',
                '--file-key', '/folder/', '--recursive', '--flatten-download-tree',
                '--json-output',
            ])

            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            assert data['overall_success'] is True
            assert data['failed_files'] == 0
            assert data['failed_downloads'] == []


class TestFlattenConflictDoesNotInvokeTheListCommand:
    """The command's own conflict branch must use the builtin `list`, not `assets list`.

    Reaching that branch takes a specific combination. A folder download under
    `--flatten-download-tree` cannot get there, because `FileTreeBuilder.flatten_file_list` rejects
    duplicate names upstream. The route that does is a SINGLE file plus `--file-previews`: the preview
    is appended with `local_path=file_local_path` — deliberately the same name as its source file — so
    `files_to_download` holds two entries whose flattened names collide.

    With the shadowed builtin, `list(set(conflicts))` called the `assets list` Click command with
    `{'x.txt'}` as its argv. Click rejected the unexpected argument and exited 2, so the user got a
    usage message for a command they did not run instead of the conflict being handled.
    """

    @patch('vamscli.commands.assets.asyncio.run', new_callable=CoroutineClosingMock)
    def test_single_file_plus_preview_conflict_is_reported_not_a_usage_error(
        self, mock_asyncio_run, cli_runner, assets_command_mocks
    ):
        with assets_command_mocks as mocks:
            # The file itself, then its preview — both resolve to the same flattened local name.
            mocks['api_client'].download_asset_file.side_effect = [
                {'downloadUrl': 'https://example.invalid/file', 'expiresIn': 86400},
                {'downloadUrl': 'https://example.invalid/preview', 'expiresIn': 86400},
            ]
            mock_asyncio_run.return_value = _download_result([])

            result = cli_runner.invoke(cli, [
                'assets', 'download', '/tmp',
                '-d', 'test-database', '-a', 'test-asset',
                '--file-key', '/folder/dup.txt', '--file-previews', '--flatten-download-tree',
                '--json-output',
            ])

            # In JSON mode the conflict is a hard error rather than an interactive prompt.
            assert result.exit_code != 0
            assert 'Filename conflicts detected' in result.output, result.output
            assert 'dup.txt' in result.output
            # The regression itself: the shadowed `list` ran a different Click command from inside the
            # download. Either of these strings means that happened.
            assert 'Listing all assets' not in result.output
            assert 'unexpected extra argument' not in result.output
            assert 'Try ' not in result.output  # Click's "Try '... --help' for help." usage footer
            mocks['api_client'].list_assets.assert_not_called()
