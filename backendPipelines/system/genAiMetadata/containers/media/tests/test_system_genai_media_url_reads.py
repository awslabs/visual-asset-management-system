#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A video or audio file over the MEDIA download bound is read over a presigned URL by range (D110).

The MEDIA image downloads its input to ephemeral storage, bounded by INPUT_MAX_DOWNLOAD_BYTES; a larger file
was not fetched and got sys_file only, so a long video lost its stream facts, keyframes and segment windows.
ffmpeg reads a container header and seeks to a frame by HTTP range requests, so a video or audio file over the
bound is now read over a presigned URL of the input at the run's version, signed for the bucket's Region, and
never downloaded. The URL is a credential: no warning, error or manifest field carries it.

The real-ffmpeg test serves a generated MP4 from a local HTTP server that honours Range requests, as Amazon S3
does, and reads it with the image's bundled ffmpeg through the same extractor the handler calls.
"""

import http.server
import json
import os
import subprocess
import threading

import imageio_ffmpeg
import pytest

from media_extractors import audio, video
from media_extractors.common import BranchResult, ExtractContext
from system_genai_media_fixtures import FakeFfmpeg, FakeS3
from test_system_genai_media_handler import (
    _ASSETS, _AUX, _CONFIG_BODY, _CONFIG_KEY, _MANIFEST_KEY, _PREFIX, _RUN, _event, _load, _pre_manifest,
)

_SECRET = "X-Amz-Signature=deadbeefsecret"


@pytest.fixture
def s3():
    fake = FakeS3()
    fake.objects[(_RUN, _CONFIG_KEY)] = json.dumps(_CONFIG_BODY).encode()
    return fake


def _seed_manifest(s3, file_class):
    s3.objects[(_AUX, _MANIFEST_KEY)] = json.dumps(_pre_manifest(file_class)).encode()


def _large_video_event(module, **over):
    event = _event("long-shift.mp4", "video", "video/mp4")
    event.update({"fileSize": module.INPUT_MAX_DOWNLOAD_BYTES + 1, "vectorSearchEnabled": True,
                  "videoSegmentSeconds": 30, "genAiAnalysisEnabled": True, "analysisModelSupportsImages": True})
    event.update(over)
    return event


@pytest.mark.unit
class TestOverBoundVideoIsReadByUrl:
    def test_the_video_is_probed_by_url_and_never_downloaded(self, s3, monkeypatch):
        _seed_manifest(s3, "video")
        module = _load(s3)
        seen = []

        def by_url(path, ctx):
            seen.append(path)
            return BranchResult(file_class="video",
                                attributes={"sys_media": {"kind": "video", "durationSeconds": 7200.0}},
                                facts={"duration": "2 h"})

        monkeypatch.setattr(module.video, "extract_video", by_url)
        response = module.lambda_handler(_large_video_event(module), None)

        assert s3.downloads == []
        assert len(seen) == 1 and seen[0].startswith("https://")
        assert s3.presigns == [("get_object", {"Bucket": _ASSETS, "Key": "a1/long-shift.mp4", "VersionId": "v1"},
                                module.INPUT_PRESIGN_SECONDS)]
        assert module.INPUT_PRESIGN_SECONDS > 600  # outlives the function's timeout
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert manifest["attributes"]["sys_media"]["durationSeconds"] == 7200.0
        assert manifest["renderSkipped"] is None and response["renderSkipped"] is None
        assert any("read over a presigned URL by range" in warning for warning in manifest["warnings"])
        # The long video still gets its window plan.
        assert response["videoSegmentCount"] > 0
        assert (_AUX, _PREFIX + "segments/plan.json") in s3.objects

    def test_no_warning_or_manifest_field_carries_the_url(self, s3, monkeypatch):
        _seed_manifest(s3, "video")
        module = _load(s3)

        def leaky(path, ctx):
            return BranchResult(file_class="video", attributes={"sys_media": {"kind": "video", "durationSeconds": 9.0}},
                                warnings=[f"Keyframe at 4.5s failed (exit 1): {path}&{_SECRET} 403 Forbidden"])

        monkeypatch.setattr(module.video, "extract_video", leaky)
        module.lambda_handler(_large_video_event(module), None)
        stored = s3.objects[(_AUX, _MANIFEST_KEY)].decode()
        assert "https://" not in stored and "X-Amz-" not in stored and "deadbeef" not in stored
        assert any("<url>" in warning for warning in json.loads(stored)["warnings"])

    def test_a_failed_read_raises_without_the_url(self, s3, monkeypatch):
        _seed_manifest(s3, "video")
        module = _load(s3)

        def failing(path, ctx):
            raise RuntimeError(f"No video facts could be read: ffmpeg failed on {path}&{_SECRET}")

        monkeypatch.setattr(module.video, "extract_video", failing)
        with pytest.raises(module.MediaTaskError) as raised:
            module.lambda_handler(_large_video_event(module), None)
        message = str(raised.value)
        assert "https://" not in message and "X-Amz-" not in message and "<url>" in message
        assert raised.value.__suppress_context__
        assert s3.downloads == []

    def test_a_video_within_the_bound_is_still_downloaded(self, s3, monkeypatch):
        _seed_manifest(s3, "video")
        s3.objects[(_ASSETS, "a1/long-shift.mp4")] = b"\x00"
        module = _load(s3)
        seen = []

        def local(path, ctx):
            seen.append(path)
            return BranchResult(file_class="video", attributes={"sys_media": {"kind": "video", "durationSeconds": 9.0}})

        monkeypatch.setitem(module._EXTRACTORS, "video", local)
        module.lambda_handler(_large_video_event(module, fileSize=module.INPUT_MAX_DOWNLOAD_BYTES), None)
        assert s3.downloads == [(_ASSETS, "a1/long-shift.mp4", "v1")] and s3.presigns == []
        assert not seen[0].startswith("http")


@pytest.mark.unit
class TestOverBoundAudioIsReadByUrl:
    def test_audio_is_read_by_url_from_the_ffmpeg_header_alone(self, s3, monkeypatch):
        _seed_manifest(s3, "audio")
        module = _load(s3)
        calls = []

        def by_url(path, ctx, read_tags=True):
            calls.append((path, read_tags))
            sys_media = {"kind": "audio", "durationSeconds": 36000.0}
            return BranchResult(file_class="audio", attributes={"sys_media": sys_media})

        monkeypatch.setattr(module.audio, "extract_audio", by_url)
        event = _event("archive.wav", "audio", "audio/wav")
        event["fileSize"] = module.INPUT_MAX_DOWNLOAD_BYTES + 1
        module.lambda_handler(event, None)
        assert s3.downloads == []
        assert len(calls) == 1 and calls[0][0].startswith("https://") and calls[0][1] is False
        assert s3.manifest(_AUX, _MANIFEST_KEY)["attributes"]["sys_media"]["durationSeconds"] == 36000.0

    def test_the_audio_extractor_reads_no_tags_from_a_url(self, tmp_path, monkeypatch):
        header = (
            "Input #0, wav, from 'https://bucket.s3.amazonaws.com/archive.wav?X-Amz-Signature=s':\n"
            "  Metadata:\n"
            "    title           : Night shift log\n"
            "  Duration: 10:00:00.00, bitrate: 1411 kb/s\n"
            "  Stream #0:0: Audio: pcm_s16le ([1][0][0][0] / 0x0001), 44100 Hz, stereo, s16, 1411 kb/s\n"
        )

        def no_tinytag(*args, **kwargs):
            raise AssertionError("tinytag reads local files only")

        monkeypatch.setattr(audio.TinyTag, "get", no_tinytag)
        ctx = ExtractContext(file_name="archive.wav", extension=".wav", content_type="audio/wav",
                             max_text_chars=1000, work_dir=str(tmp_path))
        result = audio.extract_audio("https://bucket.s3.amazonaws.com/archive.wav", ctx,
                                     run=FakeFfmpeg(header=header), read_tags=False)
        sys_media = result.attributes["sys_media"]
        assert sys_media["kind"] == "audio" and sys_media["decodable"] is True
        assert sys_media["durationSeconds"] == 36000.0 and sys_media["sampleRate"] == 44100
        assert sys_media["channels"] == 2 and sys_media["tags"]["title"] == "Night shift log"


@pytest.mark.unit
class TestOverBoundOtherClassesKeepSysFile:
    @pytest.mark.parametrize("name, file_class, content_type", [
        ("manual.pdf", "document", "application/pdf"), ("table.csv", "data", "text/csv"),
        ("photo.png", "image", "image/png"), ("notes.txt", "text", "text/plain")])
    def test_a_document_data_image_or_text_file_over_the_bound_is_not_read(
            self, s3, name, file_class, content_type):
        _seed_manifest(s3, file_class)
        module = _load(s3)
        event = _event(name, file_class, content_type)
        event["fileSize"] = module.INPUT_MAX_DOWNLOAD_BYTES + 1
        response = module.lambda_handler(event, None)
        assert s3.downloads == [] and s3.presigns == []
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert set(manifest["attributes"]) == {"sys_file"} and response["renderSkipped"] == "size"
        assert any("not downloaded" in warning for warning in manifest["warnings"])


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves one payload with HTTP Range support, as Amazon S3 does, and records each request's Range."""

    payload = b""
    ranges = []

    def log_message(self, *args):  # silence the test output
        pass

    def do_GET(self):
        header = self.headers.get("Range")
        type(self).ranges.append(header)
        size = len(self.payload)
        start, end = 0, size - 1
        if header and header.startswith("bytes="):
            first, _, last = header[len("bytes="):].partition("-")
            start = int(first) if first else 0
            end = min(int(last), size - 1) if last else size - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        try:
            self.wfile.write(self.payload[start:end + 1])
        except (BrokenPipeError, ConnectionResetError):
            pass


@pytest.fixture
def served_video(tmp_path):
    """A 20-second MP4 with video and audio, made by the bundled ffmpeg, served by range on 127.0.0.1."""
    path = tmp_path / "clip.mp4"
    proc = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=20:size=320x240:rate=10",
         "-f", "lavfi", "-i", "sine=duration=20", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)],
        capture_output=True, timeout=120, check=False)
    if proc.returncode != 0:
        pytest.skip(f"the bundled ffmpeg could not encode a test video: {proc.stderr[-200:]!r}")
    _RangeHandler.payload = path.read_bytes()
    _RangeHandler.ranges = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yield "http://127.0.0.1:%d/clip.mp4?%s" % (port, _SECRET)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.unit
def test_the_bundled_ffmpeg_reads_a_video_over_http_by_range(served_video, tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    ctx = ExtractContext(file_name="clip.mp4", extension=".mp4", content_type="video/mp4", max_text_chars=1000,
                         work_dir=str(work_dir), render_images=True)
    result = video.extract_video(served_video, ctx)
    sys_media = result.attributes["sys_media"]
    assert sys_media["kind"] == "video" and sys_media["width"] == 320 and sys_media["height"] == 240
    assert 19.0 <= sys_media["durationSeconds"] <= 21.0
    assert sys_media["videoCodec"] == "h264" and sys_media["audioCodec"] == "aac"
    assert len(result.render_images) == 4 and all(os.path.exists(path) for path in result.render_images)
    # Each keyframe seeks with a ranged request of its own, and nothing but the frames is written locally.
    assert sum(1 for header in _RangeHandler.ranges if header) >= 4
    assert all(name.endswith(".png") for name in os.listdir(work_dir))
