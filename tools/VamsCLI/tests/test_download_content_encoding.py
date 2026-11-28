"""A download saves each file byte for byte as stored, whatever its Content-Encoding.

Amazon S3 serves an object written with a Content-Encoding (a gzip-compressed glTF, for example)
with that header and a Content-Length of the stored, encoded size. The CLI saves those stored
bytes, as `aws s3 cp` does, so the size check against Content-Length, `sync`'s size comparison and
the size VAMS lists for the file all compare like with like.

The bodies come from a real aiohttp server bound to the loopback interface, because the decoding
under test happens in aiohttp's own response parser, which a stubbed session never reaches.
"""

import asyncio
import gzip
import zlib

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from vamscli.utils.download_manager import DownloadFileInfo, DownloadManager, DownloadProgress

STORED_GLB = b"glTF\x02\x00\x00\x00" + bytes(range(256)) * 16

# Each body is what the bucket stores for that Content-Encoding. The `br` body is opaque bytes: it
# is saved as stored and never decoded, whether or not a Brotli decoder is installed.
ENCODED_BODIES = {
    "gzip": gzip.compress(STORED_GLB, mtime=0),
    "deflate": zlib.compress(STORED_GLB),
    "br": b"\x8b\x7f\x80opaque-brotli-stream\x03",
}


class _Origin:
    """An HTTP origin on 127.0.0.1 answering each path with a stored body and its headers."""

    def __init__(self, objects):
        self._objects = objects
        self.request_headers = []
        app = web.Application()
        app.router.add_get("/{name}", self._answer)
        self._server = TestServer(app, host="127.0.0.1", port=0)

    async def _answer(self, request):
        self.request_headers.append(dict(request.headers))
        body, headers = self._objects[request.match_info["name"]]
        return web.Response(body=body, headers=headers)

    def url(self, name):
        return str(self._server.make_url("/" + name))

    async def __aenter__(self):
        await self._server.start_server()
        return self

    async def __aexit__(self, *exc):
        await self._server.close()


def _encoded(encoding):
    return (
        ENCODED_BODIES[encoding],
        {"Content-Type": "model/gltf-binary", "Content-Encoding": encoding},
    )


def _plain():
    return STORED_GLB, {"Content-Type": "model/gltf-binary"}


async def _download_one(url, target, file_size):
    async with DownloadManager(api_client=None, max_retries=0) as manager:
        info = DownloadFileInfo(
            relative_key="/" + target.name, local_path=target, download_url=url, file_size=file_size
        )
        return await manager._download_single_file(info, DownloadProgress([info]))


class TestStoredBytesAreSaved:
    @pytest.mark.parametrize("encoding", sorted(ENCODED_BODIES))
    def test_an_encoded_body_is_saved_as_stored(self, tmp_path, encoding):
        stored, headers = _encoded(encoding)
        target = tmp_path / "model.glb"

        async def run():
            async with _Origin({"model.glb": (stored, headers)}) as origin:
                return await _download_one(origin.url("model.glb"), target, len(stored))

        result = asyncio.run(run())

        assert target.read_bytes() == stored
        assert result["size"] == len(stored)
        assert not (tmp_path / "model.glb.vamsdownload").exists()

    def test_a_plain_body_is_saved_unchanged(self, tmp_path):
        target = tmp_path / "model.glb"

        async def run():
            async with _Origin({"model.glb": _plain()}) as origin:
                return await _download_one(origin.url("model.glb"), target, len(STORED_GLB))

        result = asyncio.run(run())

        assert target.read_bytes() == STORED_GLB
        assert result["size"] == len(STORED_GLB)

    def test_the_request_asks_for_no_transfer_encoding(self, tmp_path):
        async def run():
            async with _Origin({"model.glb": _plain()}) as origin:
                await _download_one(origin.url("model.glb"), tmp_path / "model.glb", None)
                return origin.request_headers

        request_headers = asyncio.run(run())

        assert len(request_headers) == 1
        assert request_headers[0].get("Accept-Encoding") == "identity"

    def test_a_download_of_encoded_and_plain_files_completes(self, tmp_path):
        """Shared by `assets download`, `assets export --download-files` and `sync file pull`."""
        stored, headers = _encoded("gzip")
        objects = {"model-gz.glb": (stored, headers), "model.glb": _plain()}

        async def run():
            async with _Origin(objects) as origin:
                files = [
                    DownloadFileInfo(
                        relative_key="/" + name,
                        local_path=tmp_path / name,
                        download_url=origin.url(name),
                        file_size=len(objects[name][0]),
                    )
                    for name in sorted(objects)
                ]
                async with DownloadManager(api_client=None, max_retries=0) as manager:
                    return await manager.download_files(files)

        result = asyncio.run(run())

        assert result["overall_success"] is True, result["failed_downloads"]
        assert (tmp_path / "model-gz.glb").read_bytes() == stored
        assert (tmp_path / "model.glb").read_bytes() == STORED_GLB
        assert result["total_size"] == len(stored) + len(STORED_GLB)


class TestTheOriginExercisesDecoding:
    def test_a_default_client_decodes_the_served_gzip_body(self):
        """Control: aiohttp's default session decodes this body, so the tests above test the CLI's.

        The served Content-Length is the stored, encoded size, while a decoding client reads the
        larger decoded body: the pair that made the CLI's size check fail.
        """
        stored, headers = _encoded("gzip")

        async def run():
            async with _Origin({"model.glb": (stored, headers)}) as origin:
                async with ClientSession() as session:
                    async with session.get(origin.url("model.glb")) as response:
                        return response.headers.get("Content-Length"), await response.read()

        content_length, body = asyncio.run(run())

        assert int(content_length) == len(stored)
        assert body == STORED_GLB
        assert len(body) != len(stored)
