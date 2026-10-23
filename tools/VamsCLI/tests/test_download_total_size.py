"""A download result's `total_size` is the number of bytes written to disk.

The result used to report the PLANNED size: the sum of the `file_size` values the caller passed into the
manager. Several download paths do not know a file's size up front — a single `--file-key` download and
every `--file-previews` entry pass `file_size=None` — so `assets download --json-output` reported
`total_size: 0` and `total_size_formatted: "0 B"` for files that were written in full. The planned figure
also counted files whose download then failed.

The CLI test drives the real `DownloadManager` (only the HTTP session is faked), because a test that mocks
`asyncio.run` replays a canned result and would pass whatever the manager reported.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vamscli.main import cli
from vamscli.utils.download_manager import (
    DownloadFileInfo, DownloadManager, StreamingDownloadProgress
)


class FakeStreamResponse:
    """Minimal aiohttp response stand-in: a status, optional Content-Length, and body chunks."""

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


class FakeSession:
    """aiohttp.ClientSession stand-in that answers each URL with a canned response."""

    def __init__(self, responses):
        self._responses = responses

    def get(self, url):
        return self._responses[url]

    async def close(self):
        return None


def _manager(responses):
    manager = DownloadManager(api_client=None, max_retries=0)
    manager.session = FakeSession(responses)
    return manager


class TestManagerReportsBytesWritten:

    def test_unknown_planned_size_reports_bytes_written(self, tmp_path):
        target = tmp_path / 'model.glb'
        files = [DownloadFileInfo(
            relative_key='/model.glb', local_path=target,
            download_url='https://example.invalid/model', file_size=None,
        )]
        manager = _manager({'https://example.invalid/model': FakeStreamResponse(chunks=[b'chunk', b'data'])})

        result = asyncio.run(manager.download_files(files))

        assert target.read_bytes() == b'chunkdata'
        assert result['total_size'] == 9
        assert result['total_size_formatted'] == '9.0 B'

    def test_failed_file_is_not_counted(self, tmp_path):
        files = [
            DownloadFileInfo(relative_key='/ok.bin', local_path=tmp_path / 'ok.bin',
                             download_url='https://example.invalid/ok', file_size=9),
            DownloadFileInfo(relative_key='/bad.bin', local_path=tmp_path / 'bad.bin',
                             download_url='https://example.invalid/bad', file_size=100),
        ]
        manager = _manager({
            'https://example.invalid/ok': FakeStreamResponse(chunks=[b'chunkdata'], content_length=9),
            'https://example.invalid/bad': FakeStreamResponse(status=500),
        })

        result = asyncio.run(manager.download_files(files))

        assert result['successful_files'] == 1
        assert result['failed_files'] == 1
        assert result['total_size'] == 9

    def test_streamed_download_reports_bytes_written(self, tmp_path):
        target = tmp_path / 'scan.laz'
        manager = _manager({'https://example.invalid/scan': FakeStreamResponse(chunks=[b'abc', b'defg'])})

        async def _run():
            queue: asyncio.Queue = asyncio.Queue()
            await queue.put(DownloadFileInfo(
                relative_key='/scan.laz', local_path=target,
                download_url='https://example.invalid/scan', file_size=None,
            ))
            await queue.put(None)
            return await manager.download_files_streamed(queue, StreamingDownloadProgress())

        result = asyncio.run(_run())

        assert target.read_bytes() == b'abcdefg'
        assert result['total_size'] == 7
        assert result['total_size_formatted'] == '7.0 B'


@pytest.fixture
def assets_command_mocks(generic_command_mocks):
    return generic_command_mocks('assets')


class TestAssetsDownloadJsonTotalSize:

    def test_single_file_download_json_reports_bytes_written(self, cli_runner, assets_command_mocks, tmp_path):
        url = 'https://example.invalid/model'
        body = [b'x' * 4096, b'y' * 1000]
        with assets_command_mocks as mocks, \
                patch('vamscli.utils.download_manager.aiohttp.TCPConnector', MagicMock()), \
                patch('vamscli.utils.download_manager.aiohttp.ClientSession',
                      return_value=FakeSession({url: FakeStreamResponse(chunks=body, content_length=5096)})):
            mocks['api_client'].download_asset_file.return_value = {'downloadUrl': url, 'expiresIn': 86400}

            result = cli_runner.invoke(cli, [
                'assets', 'download', str(tmp_path),
                '-d', 'test-database', '-a', 'test-asset',
                '--file-key', '/model.glb', '--json-output',
            ])

            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            assert Path(tmp_path, 'model.glb').stat().st_size == 5096
            assert data['successful_files'] == 1
            assert data['total_size'] == 5096
            assert data['total_size_formatted'] == '5.0 KB'
