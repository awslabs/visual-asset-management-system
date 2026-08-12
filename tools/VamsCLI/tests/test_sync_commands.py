"""Tests for sync commands."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from vamscli.commands.sync import _safe_local_path
from vamscli.main import cli
from vamscli.utils.exceptions import APIError, AssetNotFoundError, SyncPullError


@pytest.fixture
def sync_command_mocks(generic_command_mocks):
    """Provide mocks for sync command tests."""
    return generic_command_mocks


def make_remote_item(relative_path, size=100, timestamp='2026-01-01T00:00:00+00:00',
                     version_id='v1', etag='etag1'):
    return {
        'fileName': Path(relative_path).name,
        'relativePath': relative_path,
        'isFolder': False,
        'size': size,
        'dateCreatedCurrentVersion': timestamp,
        'versionId': version_id,
        'etag': etag,
        'isArchived': False,
    }


def mock_asyncio_run(return_value):
    """Build an asyncio.run replacement that closes the received coroutine.

    Closing prevents 'coroutine was never awaited' RuntimeWarnings from being emitted during a
    later test's garbage collection. Kept as a `side_effect` here because these call sites pass
    the value in at patch time; the equivalent for sites that assign `mock.return_value`
    afterwards is `new_callable=CoroutineClosingMock` from conftest.
    """
    def _run(coro):
        coro.close()
        return return_value
    return _run


def setup_api_defaults(api_client, remote_items=None):
    """Configure the standard API responses used by sync commands."""
    api_client.get_asset.return_value = {
        'assetId': 'asset1', 'databaseId': 'db1', 'assetName': 'Asset 1',
        'isDistributable': True
    }
    api_client.get_database.return_value = {
        'databaseId': 'db1', 'restrictFileUploadsToExtensions': '.all'
    }
    api_client.list_asset_files.return_value = {
        'items': remote_items or [], 'NextToken': None
    }
    api_client.download_asset_file.return_value = {
        'downloadUrl': 'https://example.com/presigned', 'expiresIn': 3600
    }
    api_client.archive_file.return_value = {'success': True, 'affectedFiles': []}
    api_client.delete_file.return_value = {'success': True, 'affectedFiles': []}
    api_client.create_asset_version.return_value = {
        'success': True, 'assetVersionId': '2', 'operation': 'create'
    }


class TestSyncGroupHelp:
    def test_sync_help(self, cli_runner):
        result = cli_runner.invoke(cli, ['sync', '--help'])
        assert result.exit_code == 0
        assert 'file' in result.output

    def test_sync_file_help(self, cli_runner):
        result = cli_runner.invoke(cli, ['sync', 'file', '--help'])
        assert result.exit_code == 0
        assert 'push' in result.output
        assert 'pull' in result.output

    def test_push_help(self, cli_runner):
        result = cli_runner.invoke(cli, ['sync', 'file', 'push', '--help'])
        assert result.exit_code == 0
        assert '--dryrun' in result.output
        assert '--allow-modify' in result.output
        assert '--allow-delete' in result.output
        assert '--permanent-delete' in result.output
        assert '--ignore-file' in result.output
        assert '--json-output' in result.output

    def test_pull_help(self, cli_runner):
        result = cli_runner.invoke(cli, ['sync', 'file', 'pull', '--help'])
        assert result.exit_code == 0
        assert '--dryrun' in result.output
        assert '--allow-modify' in result.output
        assert '--allow-delete' in result.output
        assert '--confirm' in result.output


class TestSyncPushValidation:
    def test_push_missing_required_options(self, cli_runner, sync_command_mocks, tmp_path):
        with sync_command_mocks('sync'):
            result = cli_runner.invoke(cli, ['sync', 'file', 'push', str(tmp_path)])
            assert result.exit_code == 2  # Click missing-option error

    def test_push_missing_directory(self, cli_runner, sync_command_mocks):
        with sync_command_mocks('sync'):
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', '-d', 'db1', '-a', 'asset1'
            ])
            assert result.exit_code == 1
            assert 'Local directory is required' in result.output

    def test_push_nonexistent_directory(self, cli_runner, sync_command_mocks, tmp_path):
        with sync_command_mocks('sync'):
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path / 'missing'),
                '-d', 'db1', '-a', 'asset1'
            ])
            assert result.exit_code == 1

    def test_push_permanent_delete_requires_allow_delete(self, cli_runner, sync_command_mocks, tmp_path):
        with sync_command_mocks('sync'):
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--permanent-delete'
            ])
            assert result.exit_code == 1
            assert '--allow-delete' in result.output

    def test_push_permanent_delete_requires_confirm(self, cli_runner, sync_command_mocks, tmp_path):
        with sync_command_mocks('sync'):
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--allow-delete', '--permanent-delete'
            ])
            assert result.exit_code == 1
            assert '--confirm' in result.output

    def test_push_dryrun_skips_confirm_requirement(self, cli_runner, sync_command_mocks, tmp_path):
        (tmp_path / 'a.txt').write_text('aaa')
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--allow-delete', '--permanent-delete', '--dryrun'
            ])
            assert result.exit_code == 0

    def test_push_version_comment_too_long(self, cli_runner, sync_command_mocks, tmp_path):
        with sync_command_mocks('sync'):
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--version-comment', 'x' * 300
            ])
            assert result.exit_code == 1
            assert '1-256' in result.output

    def test_push_json_input_rejects_string_booleans(self, cli_runner, sync_command_mocks, tmp_path):
        # A truthy string must never enable a destructive flag
        json_input = json.dumps({'allow_delete': True, 'permanent_delete': True,
                                 'confirm': 'no'})
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'], [make_remote_item('/gone.txt')])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--json-input', json_input
            ])
            assert result.exit_code == 1
            assert 'must be a boolean' in result.output
            mocks['api_client'].delete_file.assert_not_called()

    def test_push_asset_not_found(self, cli_runner, sync_command_mocks, tmp_path):
        with sync_command_mocks('sync') as mocks:
            mocks['api_client'].get_asset.side_effect = AssetNotFoundError("Asset not found")
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'bad'
            ])
            assert result.exit_code != 0


class TestSyncPushDryrun:
    def test_dryrun_reports_new_files(self, cli_runner, sync_command_mocks, tmp_path):
        (tmp_path / 'new.txt').write_text('new file')
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--dryrun'
            ])
            assert result.exit_code == 0
            assert 'new.txt' in result.output
            assert 'Dry run' in result.output
            mocks['api_client'].delete_file.assert_not_called()
            mocks['api_client'].archive_file.assert_not_called()

    def test_dryrun_json_output(self, cli_runner, sync_command_mocks, tmp_path):
        (tmp_path / 'new.txt').write_text('new file')
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'], [make_remote_item('/old.txt')])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--dryrun', '--json-output'
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['direction'] == 'push'
            assert data['dryrun'] is True
            assert data['plan']['summary']['transfer_count'] == 1
            assert data['plan']['transfers'][0]['syncPath'] == 'new.txt'
            # old.txt is a delete candidate but --allow-delete not given
            assert data['plan']['summary']['skipped_delete_count'] == 1

    def test_dryrun_modify_skipped_without_allow_modify(self, cli_runner, sync_command_mocks, tmp_path):
        (tmp_path / 'a.txt').write_text('different-size-content')
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'], [make_remote_item('/a.txt', size=5)])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--dryrun', '--json-output'
            ])
            data = json.loads(result.output)
            assert data['plan']['summary']['transfer_count'] == 0
            assert data['plan']['summary']['skipped_modify_count'] == 1

    def test_dryrun_respects_vamsignore(self, cli_runner, sync_command_mocks, tmp_path):
        (tmp_path / 'keep.txt').write_text('keep')
        (tmp_path / 'skip.log').write_text('skip')
        (tmp_path / '.vamsignore').write_text('*.log\n.vamsignore\n')
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--dryrun', '--json-output'
            ])
            data = json.loads(result.output)
            transfer_paths = [t['syncPath'] for t in data['plan']['transfers']]
            assert transfer_paths == ['keep.txt']
            assert data['plan']['summary']['ignored_count'] == 2

    def test_dryrun_ignore_file_override(self, cli_runner, sync_command_mocks, tmp_path):
        (tmp_path / 'keep.log').write_text('keep')
        (tmp_path / 'skip.bin').write_text('skip')
        override = tmp_path / 'custom.patterns'
        override.write_text('*.bin\ncustom.patterns\n')
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--dryrun', '--json-output', '--ignore-file', str(override)
            ])
            data = json.loads(result.output)
            transfer_paths = [t['syncPath'] for t in data['plan']['transfers']]
            assert transfer_paths == ['keep.log']


class TestSyncPushExecution:
    def test_push_uploads_new_files(self, cli_runner, sync_command_mocks, tmp_path):
        (tmp_path / 'new.txt').write_text('new file')
        upload_summary = {
            'overall_success': True, 'total_files': 1, 'successful_files': 1,
            'failed_files': 0, 'total_size': 8, 'total_size_formatted': '8B',
            'upload_duration': 0.5, 'average_speed': 16,
            'average_speed_formatted': '16B/s', 'sequence_results': []
        }
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'])
            with patch('vamscli.commands.sync.asyncio.run',
                       side_effect=mock_asyncio_run(upload_summary)):
                result = cli_runner.invoke(cli, [
                    'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                    '--json-output'
                ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['overall_success'] is True
            assert data['execution']['uploads']['successful_files'] == 1

    def test_push_archives_deleted_files(self, cli_runner, sync_command_mocks, tmp_path):
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'], [make_remote_item('/gone.txt')])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--allow-delete', '--json-output'
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            mocks['api_client'].archive_file.assert_called_once_with('db1', 'asset1', {
                'filePath': '/gone.txt', 'isPrefix': False
            })
            mocks['api_client'].delete_file.assert_not_called()
            assert data['execution']['deletes']['action'] == 'archive'

    def test_push_permanent_delete_with_confirm(self, cli_runner, sync_command_mocks, tmp_path):
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'], [make_remote_item('/gone.txt')])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--allow-delete', '--permanent-delete', '--confirm', '--json-output'
            ])
            assert result.exit_code == 0
            mocks['api_client'].delete_file.assert_called_once_with('db1', 'asset1', {
                'filePath': '/gone.txt', 'isPrefix': False,
                'confirmPermanentDelete': True
            })
            mocks['api_client'].archive_file.assert_not_called()

    def test_push_no_changes(self, cli_runner, sync_command_mocks, tmp_path):
        (tmp_path / 'same.txt').write_text('12345')
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'],
                               [make_remote_item('/same.txt', size=5,
                                                 timestamp='2099-01-01T00:00:00+00:00')])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--json-output'
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['plan']['summary']['transfer_count'] == 0
            assert 'execution' not in data

    def test_push_version_comment_creates_version(self, cli_runner, sync_command_mocks, tmp_path):
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'], [make_remote_item('/gone.txt')])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--allow-delete', '--version-comment', 'Synced from CI', '--json-output'
            ])
            assert result.exit_code == 0
            mocks['api_client'].create_asset_version.assert_called_once_with('db1', 'asset1', {
                'useLatestFiles': True, 'comment': 'Synced from CI'
            })

    def test_push_json_input(self, cli_runner, sync_command_mocks, tmp_path):
        (tmp_path / 'new.txt').write_text('new')
        json_input = json.dumps({'dryrun': True})
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--json-input', json_input, '--json-output'
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['dryrun'] is True

    def test_push_no_setup(self, cli_runner, no_setup_command_mocks, tmp_path):
        with no_setup_command_mocks('sync'):
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1'
            ])
            assert result.exit_code != 0


class TestSyncPullValidation:
    def test_pull_allow_delete_requires_confirm(self, cli_runner, sync_command_mocks, tmp_path):
        with sync_command_mocks('sync'):
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'pull', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--allow-delete'
            ])
            assert result.exit_code == 1
            assert '--confirm' in result.output

    def test_pull_not_distributable(self, cli_runner, sync_command_mocks, tmp_path):
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'])
            mocks['api_client'].get_asset.return_value = {
                'assetId': 'asset1', 'isDistributable': False
            }
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'pull', str(tmp_path), '-d', 'db1', '-a', 'asset1'
            ])
            assert result.exit_code == 1
            assert 'not distributable' in result.output


class TestSyncPullDryrun:
    def test_dryrun_reports_missing_files(self, cli_runner, sync_command_mocks, tmp_path):
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'], [make_remote_item('/remote.txt')])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'pull', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--dryrun', '--json-output'
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['direction'] == 'pull'
            assert data['plan']['transfers'][0]['syncPath'] == 'remote.txt'

    def test_dryrun_local_only_delete_candidate(self, cli_runner, sync_command_mocks, tmp_path):
        (tmp_path / 'local-only.txt').write_text('x')
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'pull', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--dryrun', '--json-output'
            ])
            data = json.loads(result.output)
            assert data['plan']['summary']['skipped_delete_count'] == 1

    def test_dryrun_asset_location_filter(self, cli_runner, sync_command_mocks, tmp_path):
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'], [
                make_remote_item('/models/inside.txt'),
                make_remote_item('/other/outside.txt'),
            ])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'pull', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--asset-location', '/models', '--dryrun', '--json-output'
            ])
            data = json.loads(result.output)
            paths = [t['syncPath'] for t in data['plan']['transfers']]
            assert paths == ['inside.txt']


class TestSafeLocalPath:
    def test_normal_path_resolves_under_root(self, tmp_path):
        result = _safe_local_path(tmp_path, 'sub/file.txt')
        assert result == (tmp_path / 'sub' / 'file.txt').resolve()

    def test_traversal_path_rejected(self, tmp_path):
        with pytest.raises(SyncPullError):
            _safe_local_path(tmp_path, '../evil.txt')

    def test_nested_traversal_rejected(self, tmp_path):
        with pytest.raises(SyncPullError):
            _safe_local_path(tmp_path, 'sub/../../evil.txt')

    def test_leading_slash_stripped(self, tmp_path):
        result = _safe_local_path(tmp_path, '/file.txt')
        assert result == (tmp_path / 'file.txt').resolve()


class TestSyncPushFailureAggregation:
    def test_push_upload_failure_reports_overall_failure(self, cli_runner, sync_command_mocks, tmp_path):
        (tmp_path / 'new.txt').write_text('new file')
        failed_summary = {
            'overall_success': False, 'total_files': 1, 'successful_files': 0,
            'failed_files': 1, 'total_size': 8, 'total_size_formatted': '8B',
            'upload_duration': 0.5, 'average_speed': 0,
            'average_speed_formatted': '0B/s', 'sequence_results': []
        }
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'])
            with patch('vamscli.commands.sync.asyncio.run',
                       side_effect=mock_asyncio_run(failed_summary)):
                result = cli_runner.invoke(cli, [
                    'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                    '--version-comment', 'Should not be created', '--json-output'
                ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['overall_success'] is False
            # Version creation must be skipped after a failed push
            mocks['api_client'].create_asset_version.assert_not_called()
            assert 'version' not in data['execution']

    def test_push_archive_failure_reported(self, cli_runner, sync_command_mocks, tmp_path):
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'], [make_remote_item('/gone.txt')])
            mocks['api_client'].archive_file.side_effect = APIError('archive failed')
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--allow-delete', '--json-output'
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['overall_success'] is False
            failed = data['execution']['deletes']['failed']
            assert len(failed) == 1
            assert failed[0]['relativeKey'] == '/gone.txt'
            assert 'archive failed' in failed[0]['error']


class TestSyncPullProducerPaths:
    """Pull tests that run the real producer/consumer wiring (unmocked asyncio.run)."""

    def _mock_download_manager(self):
        """DownloadManager stand-in whose streamed download drains the queue."""
        class FakeManager:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return False

            async def download_files_streamed(self, queue, progress):
                successful = []
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    successful.append({'relative_key': item.relative_key,
                                       'local_path': str(item.local_path),
                                       'size': item.file_size or 0})
                return {
                    'overall_success': True, 'total_files': len(successful),
                    'successful_files': len(successful), 'failed_files': 0,
                    'total_size': 0, 'total_size_formatted': '0 B',
                    'download_duration': 0.1, 'average_speed': 0,
                    'average_speed_formatted': '0 B/s',
                    'successful_downloads': successful, 'failed_downloads': []
                }
        return FakeManager

    def test_pull_url_failure_aggregated(self, cli_runner, sync_command_mocks, tmp_path):
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'], [
                make_remote_item('/good.txt'),
                make_remote_item('/bad.txt'),
            ])

            def bulk_side_effect(db, asset, keys, **kwargs):
                return {
                    'downloadUrl': 'https://example.com/presigned',
                    'files': [
                        {'key': k, 'success': k != '/bad.txt',
                         'downloadUrl': None if k == '/bad.txt' else 'https://example.com/presigned',
                         'error': 'presigned URL generation failed' if k == '/bad.txt' else None}
                        for k in keys
                    ]
                }

            mocks['api_client'].download_asset_files_bulk.side_effect = bulk_side_effect
            with patch('vamscli.commands.sync.DownloadManager', self._mock_download_manager()):
                result = cli_runner.invoke(cli, [
                    'sync', 'file', 'pull', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                    '--json-output'
                ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            downloads = data['execution']['downloads']
            assert data['overall_success'] is False
            assert downloads['overall_success'] is False
            assert downloads['successful_files'] == 1
            failed_keys = [f['relative_key'] for f in downloads['failed_downloads']]
            assert failed_keys == ['/bad.txt']

    def test_pull_traversal_entry_fails_cleanly_without_hang(self, cli_runner, sync_command_mocks, tmp_path):
        # A malicious remote relativePath must be recorded as a failure, not
        # escape the sync root or hang the producer/consumer wiring
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'], [
                make_remote_item('/../evil.txt'),
                make_remote_item('/good.txt'),
            ])
            mocks['api_client'].download_asset_files_bulk.side_effect = \
                lambda db, asset, keys, **kw: {
                    'downloadUrl': 'https://example.com/presigned',
                    'files': [{'key': k, 'success': True,
                               'downloadUrl': 'https://example.com/presigned'} for k in keys]
                }
            with patch('vamscli.commands.sync.DownloadManager', self._mock_download_manager()):
                result = cli_runner.invoke(cli, [
                    'sync', 'file', 'pull', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                    '--json-output'
                ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            downloads = data['execution']['downloads']
            assert downloads['successful_files'] == 1
            failed_keys = [f['relative_key'] for f in downloads['failed_downloads']]
            assert failed_keys == ['/../evil.txt']
            assert not (tmp_path.parent / 'evil.txt').exists()


class TestSyncConflictCheck:
    def test_pull_conflict_check_skips_local_edit(self, cli_runner, sync_command_mocks, tmp_path):
        # Local file differs from remote and matches no revision-history version
        (tmp_path / 'a.txt').write_text('locally edited content')
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'],
                               [make_remote_item('/a.txt', size=5,
                                                 timestamp='2099-01-01T00:00:00+00:00')])
            mocks['api_client'].get_file_info.return_value = {
                'versions': [{'versionId': 'v1', 'size': 5, 'isLatest': True,
                              'lastModified': '2099-01-01T00:00:00+00:00'}]
            }
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'pull', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--allow-modify', '--conflict-check', '--dryrun', '--json-output'
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['plan']['summary']['transfer_count'] == 0
            assert data['plan']['summary']['conflict_count'] == 1
            assert data['plan']['conflicts'][0]['conflict'] == 'local-modified'

    def test_push_conflict_check_skips_outdated_copy(self, cli_runner, sync_command_mocks, tmp_path):
        # Local file exactly matches an OLDER remote version (size + timestamp)
        import os
        target = tmp_path / 'a.txt'
        target.write_text('12345')  # size 5 matches historical v1
        old_epoch = 1767225600.0  # 2026-01-01T00:00:00+00:00
        os.utime(target, (old_epoch, old_epoch))
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'],
                               [make_remote_item('/a.txt', size=9,
                                                 timestamp='2026-01-02T00:00:00+00:00')])
            mocks['api_client'].get_file_info.return_value = {
                'versions': [
                    {'versionId': 'v2', 'size': 9, 'isLatest': True,
                     'lastModified': '2026-01-02T00:00:00+00:00'},
                    {'versionId': 'v1', 'size': 5, 'isLatest': False,
                     'lastModified': '2026-01-01T00:00:00+00:00'},
                ]
            }
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--allow-modify', '--conflict-check', '--dryrun', '--json-output'
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['plan']['summary']['conflict_count'] == 1
            assert data['plan']['conflicts'][0]['conflict'] == 'remote-newer'

    def test_conflict_check_not_run_for_missing_files(self, cli_runner, sync_command_mocks, tmp_path):
        (tmp_path / 'new.txt').write_text('brand new')
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--conflict-check', '--dryrun', '--json-output'
            ])
            assert result.exit_code == 0
            mocks['api_client'].get_file_info.assert_not_called()
            data = json.loads(result.output)
            assert data['plan']['summary']['transfer_count'] == 1


class TestSyncPullExecution:
    def test_pull_downloads_missing_files(self, cli_runner, sync_command_mocks, tmp_path):
        download_summary = {
            'overall_success': True, 'total_files': 1, 'successful_files': 1,
            'failed_files': 0, 'total_size': 100, 'total_size_formatted': '100B',
            'download_duration': 0.5, 'average_speed': 200,
            'average_speed_formatted': '200B/s',
            'successful_downloads': [{'relative_key': '/remote.txt',
                                      'local_path': str(tmp_path / 'remote.txt'),
                                      'size': 100}],
            'failed_downloads': []
        }
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'], [make_remote_item('/remote.txt')])
            with patch('vamscli.commands.sync.asyncio.run',
                       side_effect=mock_asyncio_run(download_summary)):
                result = cli_runner.invoke(cli, [
                    'sync', 'file', 'pull', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                    '--json-output'
                ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['overall_success'] is True
            assert data['execution']['downloads']['successful_files'] == 1

    def test_pull_deletes_local_files_with_confirm(self, cli_runner, sync_command_mocks, tmp_path):
        target = tmp_path / 'local-only.txt'
        target.write_text('x')
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'pull', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--allow-delete', '--confirm', '--json-output'
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert not target.exists()
            assert len(data['execution']['deletes']['succeeded']) == 1

    def test_pull_interactive_confirmation_abort(self, cli_runner, sync_command_mocks, tmp_path):
        target = tmp_path / 'local-only.txt'
        target.write_text('x')
        with sync_command_mocks('sync') as mocks:
            setup_api_defaults(mocks['api_client'])
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'pull', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--allow-delete', '--confirm'
            ], input='n\n')
            assert result.exit_code != 0
            assert target.exists()

    def test_pull_no_setup(self, cli_runner, no_setup_command_mocks, tmp_path):
        with no_setup_command_mocks('sync'):
            result = cli_runner.invoke(cli, [
                'sync', 'file', 'pull', str(tmp_path), '-d', 'db1', '-a', 'asset1'
            ])
            assert result.exit_code != 0
