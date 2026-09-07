"""Tests for DownloadManager file writing behavior."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vamscli.constants import MAX_DOWNLOAD_KEYS_PER_REQUEST
from vamscli.utils.download_manager import (
    DownloadFileInfo, DownloadManager, DownloadProgress, parse_remote_timestamp,
    generate_presigned_urls
)
from vamscli.utils.exceptions import APIError, DownloadError


class FakeStreamResponse:
    """Minimal aiohttp response stand-in for _download_single_file."""

    def __init__(self, status=200, chunks=None, content_length=None):
        self.status = status
        self.headers = {}
        if content_length is not None:
            self.headers['Content-Length'] = str(content_length)
        self.content = MagicMock()
        self.content.iter_chunked = self._iter_chunked
        self._chunks = chunks or []

    def _iter_chunked(self, size):
        async def _gen():
            for chunk in self._chunks:
                yield chunk
        return _gen()

    async def text(self):
        return 'error body'

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def make_manager(response):
    manager = DownloadManager(api_client=None)
    manager.session = MagicMock()
    manager.session.get = MagicMock(return_value=response)
    return manager


def run_download(manager, file_info):
    progress = DownloadProgress([file_info])
    return asyncio.run(manager._download_single_file(file_info, progress))


class TestParseRemoteTimestamp:
    def test_parses_iso_with_offset(self):
        assert parse_remote_timestamp('2026-01-01T00:00:00+00:00') == 1767225600.0

    def test_parses_z_suffix(self):
        assert parse_remote_timestamp('2026-01-01T00:00:00Z') == 1767225600.0

    def test_none_and_invalid(self):
        assert parse_remote_timestamp(None) is None
        assert parse_remote_timestamp('') is None
        assert parse_remote_timestamp('not-a-date') is None


class TestDownloadSingleFile:
    def test_writes_file_and_sets_mtime(self, tmp_path):
        target = tmp_path / 'sub' / 'file.txt'
        mtime = 1767225600.0
        file_info = DownloadFileInfo(
            relative_key='/file.txt', local_path=target,
            download_url='https://example.com/x', file_size=9,
            last_modified=mtime
        )
        manager = make_manager(FakeStreamResponse(chunks=[b'chunk', b'data'], content_length=9))

        result = run_download(manager, file_info)

        assert result['size'] == 9
        assert target.read_bytes() == b'chunkdata'
        assert abs(target.stat().st_mtime - mtime) < 1

    def test_no_mtime_leaves_write_time(self, tmp_path):
        target = tmp_path / 'file.txt'
        file_info = DownloadFileInfo(
            relative_key='/file.txt', local_path=target,
            download_url='https://example.com/x', file_size=4
        )
        manager = make_manager(FakeStreamResponse(chunks=[b'data'], content_length=4))
        run_download(manager, file_info)
        assert target.exists()

    def test_size_mismatch_raises_and_cleans_up(self, tmp_path):
        target = tmp_path / 'file.txt'
        file_info = DownloadFileInfo(
            relative_key='/file.txt', local_path=target,
            download_url='https://example.com/x', file_size=100
        )
        manager = make_manager(FakeStreamResponse(chunks=[b'short'], content_length=100))

        with pytest.raises(DownloadError, match='Size mismatch'):
            run_download(manager, file_info)

        assert not target.exists()
        assert not (tmp_path / 'file.txt.vamsdownload').exists()

    def test_failed_download_does_not_touch_existing_file(self, tmp_path):
        target = tmp_path / 'file.txt'
        target.write_text('original content')
        file_info = DownloadFileInfo(
            relative_key='/file.txt', local_path=target,
            download_url='https://example.com/x'
        )
        manager = make_manager(FakeStreamResponse(status=500))

        with pytest.raises(DownloadError):
            run_download(manager, file_info)

        assert target.read_text() == 'original content'
        assert not (tmp_path / 'file.txt.vamsdownload').exists()

    def test_falls_back_to_file_info_size_without_content_length(self, tmp_path):
        target = tmp_path / 'file.txt'
        file_info = DownloadFileInfo(
            relative_key='/file.txt', local_path=target,
            download_url='https://example.com/x', file_size=4
        )
        manager = make_manager(FakeStreamResponse(chunks=[b'data']))
        result = run_download(manager, file_info)
        assert result['size'] == 4
        assert target.read_bytes() == b'data'

    def test_no_expected_size_accepts_any_length(self, tmp_path):
        target = tmp_path / 'file.txt'
        file_info = DownloadFileInfo(
            relative_key='/file.txt', local_path=target,
            download_url='https://example.com/x'
        )
        manager = make_manager(FakeStreamResponse(chunks=[b'whatever']))
        result = run_download(manager, file_info)
        assert result['size'] == 8


class TestGeneratePresignedUrls:
    def _bulk_response(self, keys, fail_keys=()):
        return {
            'downloadUrl': 'https://example.com/first',
            'files': [
                {'key': k, 'downloadUrl': None if k in fail_keys else f'https://example.com{k}',
                 'versionId': 'v1', 'success': k not in fail_keys,
                 'error': 'File not found in S3' if k in fail_keys else None}
                for k in keys
            ]
        }

    def test_bulk_urls_returned_per_key(self):
        api = MagicMock()
        api.download_asset_files_bulk.return_value = self._bulk_response(['/a.txt', '/b.txt'])

        result = generate_presigned_urls(api, 'db1', 'asset1', ['/a.txt', '/b.txt'])

        assert result['/a.txt']['downloadUrl'] == 'https://example.com/a.txt'
        assert result['/b.txt']['versionId'] == 'v1'
        api.download_asset_files_bulk.assert_called_once()
        api.download_asset_file.assert_not_called()

    def test_failed_keys_carry_error(self):
        api = MagicMock()
        api.download_asset_files_bulk.return_value = self._bulk_response(
            ['/good.txt', '/bad.txt'], fail_keys={'/bad.txt'})

        result = generate_presigned_urls(api, 'db1', 'asset1', ['/good.txt', '/bad.txt'])

        assert 'downloadUrl' in result['/good.txt']
        assert result['/bad.txt'] == {'error': 'File not found in S3'}

    def test_chunks_at_backend_limit(self):
        api = MagicMock()
        keys = [f'/f{i}.txt' for i in range(MAX_DOWNLOAD_KEYS_PER_REQUEST + 10)]
        api.download_asset_files_bulk.side_effect = lambda db, a, chunk, **kw: \
            self._bulk_response(chunk)

        result = generate_presigned_urls(api, 'db1', 'asset1', keys)

        assert api.download_asset_files_bulk.call_count == 2
        first_chunk = api.download_asset_files_bulk.call_args_list[0].args[2]
        second_chunk = api.download_asset_files_bulk.call_args_list[1].args[2]
        assert len(first_chunk) == MAX_DOWNLOAD_KEYS_PER_REQUEST
        assert len(second_chunk) == 10
        assert len(result) == len(keys)

    def test_bulk_request_failure_marks_chunk_failed(self):
        # Request-level failure marks every key of that chunk failed
        api = MagicMock()
        api.download_asset_files_bulk.side_effect = APIError('Invalid download request')

        result = generate_presigned_urls(api, 'db1', 'asset1', ['/a.txt', '/b.txt'])

        assert 'error' in result['/a.txt']
        assert 'error' in result['/b.txt']
        api.download_asset_file.assert_not_called()

    def test_keys_missing_from_response_marked_failed(self):
        # A response that omits a requested key still yields an entry for it
        api = MagicMock()
        api.download_asset_files_bulk.return_value = self._bulk_response(['/a.txt'])

        result = generate_presigned_urls(api, 'db1', 'asset1', ['/a.txt', '/b.txt'])

        assert 'downloadUrl' in result['/a.txt']
        assert result['/b.txt'] == {'error': 'URL generation failed'}

    def test_empty_keys_returns_empty(self):
        api = MagicMock()
        assert generate_presigned_urls(api, 'db1', 'asset1', []) == {}
        api.download_asset_files_bulk.assert_not_called()

    def test_asset_version_params_forwarded(self):
        api = MagicMock()
        api.download_asset_files_bulk.return_value = self._bulk_response(['/a.txt'])

        generate_presigned_urls(api, 'db1', 'asset1', ['/a.txt'],
                                asset_version_id='2')

        kwargs = api.download_asset_files_bulk.call_args.kwargs
        assert kwargs['asset_version_id'] == '2'

    def test_per_file_version_entries_passed_through(self):
        # Object entries {key, versionId} flow to the bulk API; result keyed by path
        api = MagicMock()
        api.download_asset_files_bulk.return_value = {
            'downloadUrl': 'https://example.com/a',
            'files': [
                {'key': '/a.txt', 'success': True, 'downloadUrl': 'https://example.com/a',
                 'versionId': 'ver-a'},
                {'key': '/b.txt', 'success': True, 'downloadUrl': 'https://example.com/b',
                 'versionId': None},
            ]
        }
        keys = [{'key': '/a.txt', 'versionId': 'ver-a'}, '/b.txt']

        result = generate_presigned_urls(api, 'db1', 'asset1', keys)

        sent = api.download_asset_files_bulk.call_args.args[2]
        assert sent == keys  # entries forwarded verbatim
        assert result['/a.txt'] == {'downloadUrl': 'https://example.com/a', 'versionId': 'ver-a'}
        assert result['/b.txt']['downloadUrl'] == 'https://example.com/b'

    def test_per_file_version_chunk_failure_keyed_by_path(self):
        # On chunk failure, error map is keyed by the entry's path (dict or str)
        api = MagicMock()
        api.download_asset_files_bulk.side_effect = APIError('bad request')

        result = generate_presigned_urls(api, 'db1', 'asset1',
                                         [{'key': '/a.txt', 'versionId': 'v'}, '/b.txt'])

        assert 'error' in result['/a.txt']
        assert 'error' in result['/b.txt']


class TestDownloadFileInfoCompatibility:
    def test_positional_construction_without_last_modified(self):
        info = DownloadFileInfo('/key', Path('/tmp/x'), 'https://url', 10, 'v1')
        assert info.last_modified is None

    def test_full_construction(self):
        info = DownloadFileInfo('/key', Path('/tmp/x'), 'https://url', 10, 'v1', 123.0)
        assert info.last_modified == 123.0
