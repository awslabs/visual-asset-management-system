#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""URL query parameters are removed from logged text (D112).

ffmpeg reads a large input over a presigned URL and may quote it in its stderr, and imageio-ffmpeg quotes ffmpeg's
stderr in its errors. The media image runs scrub_urls over the whole text before any cut, so neither a URL nor a
run holding a signing parameter of either signature version, in any case, reaches a log line, a manifest field or
an error.

Each shape below (a Signature Version 2 URL, a Signature Version 4 URL, a URL cut before its scheme, a URL cut
inside its session token, and a lower-case token parameter in a request line) is placed at the head, in the middle
and at the tail of a long ffmpeg output, and driven through the extract handler's read by URL (the error and the
manifest warnings), the segment handler (its log lines and the skipped window's record) and the ranged ZIP reader.
"""

import functools
import json
import re

import pytest
from botocore.exceptions import EndpointConnectionError

from media_extractors import common, video
from system_genai_media_fixtures import FakeS3, fake_read_frames
from test_system_genai_media_handler import (
    _ASSETS, _AUX, _CONFIG_BODY, _CONFIG_KEY, _MANIFEST_KEY, _RUN, _event, _load, _pre_manifest,
)
import test_system_genai_media_segment_handler as segment_tests

_AKID = "AKIDLEAKCHECK0001"
_SIGV2 = "SigV2LeakValue%2Bq%3D"
_SIGV4 = "5ac0ffee" * 8
_TOKEN_BODY = "Zm9vYmFy%2Bx%2F"
_TOKEN = "IQoJb3JpZ2luX2VjELEAKTOKEN" + _TOKEN_BODY * 70 + "LEAKTOKENEND"
_EXPIRES = "1794779800"
_KEY = "a1/long-shift.mp4"

LEAKS = {
    "sigv2": (f"https://vams-assets.s3.amazonaws.com/{_KEY}?AWSAccessKeyId={_AKID}&Signature={_SIGV2}"
              f"&x-amz-security-token={_TOKEN}&Expires={_EXPIRES}"),
    "sigv4": (f"https://s3.us-east-1.amazonaws.com/vams-assets/{_KEY}?X-Amz-Algorithm=AWS4-HMAC-SHA256"
              f"&X-Amz-Credential={_AKID}%2F20261115%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20261115T000000Z"
              f"&X-Amz-Expires=900&X-Amz-Security-Token={_TOKEN}&X-Amz-SignedHeaders=host&X-Amz-Signature={_SIGV4}"),
    "cut-before-scheme": (f"vams-assets.s3.amazonaws.com/{_KEY}?AWSAccessKeyId={_AKID}&Signature={_SIGV2}"
                          f"&x-amz-security-token={_TOKEN}&Expires={_EXPIRES}"),
    "cut-inside-token": _TOKEN_BODY * 6 + f"LEAKTOKENEND&Expires={_EXPIRES}",
    "lowercase-token": f"GET /vams-assets/{_KEY}?x-amz-security-token={_TOKEN}&versionId=v1 HTTP/1.1",
}
POSITIONS = ("head", "middle", "tail")
# Every signing value above, and every signing parameter name in any case: none may be in the scrubbed text.
_SECRETS = (_AKID, _SIGV2, _SIGV4, "LEAKTOKEN", _TOKEN_BODY, "LEAKTOKENEND", _EXPIRES)
_NAMES = re.compile(r"x-amz-|awsaccesskeyid|signature=|expires=", re.IGNORECASE)
_NOISE = [f"[mov,mp4,m4a,3gp,3g2,mj2 @ 0x55d1c{index:04d}] stream {index}: probing packet {index} of 40, "
          f"no keyframe yet" for index in range(40)]


def _stderr(leak, position):
    """A long ffmpeg output with ``leak`` on the first line, in the middle, or on the line before ffmpeg's last."""
    lines = list(_NOISE)
    line = f"[https @ 0x55d1c0a0] HTTP error 403 Forbidden for '{leak}': Server returned 403 Forbidden"
    if position == "head":
        lines.insert(0, line)
    elif position == "middle":
        lines.insert(len(lines) // 2, line)
    else:
        lines.append(line)
    return "\n".join(lines) + "\nError opening input: Server returned 403 Forbidden (access denied)\n"


def _assert_clean(text):
    for secret in _SECRETS:
        assert secret not in text, (secret, text[:2000])
    match = _NAMES.search(text)
    assert match is None, (match, text[:2000])
    assert "https://" not in text


CASES = [pytest.param(shape, position, id=f"{shape}-{position}") for shape in LEAKS for position in POSITIONS]


@pytest.mark.unit
@pytest.mark.parametrize("shape, position", CASES)
def test_the_scrub_leaves_no_signing_value_in_the_whole_text(shape, position):
    text = _stderr(LEAKS[shape], position)
    _assert_clean(common.scrub_urls(text))
    # The noise around the URL is kept.
    assert "probing packet 39 of 40" in common.scrub_urls(text)


@pytest.mark.unit
@pytest.mark.parametrize("shape, position", CASES)
def test_the_scrubbed_tail_is_cut_after_the_scrub(shape, position):
    tail = common.scrubbed_tail(_stderr(LEAKS[shape], position))
    _assert_clean(tail)
    assert len(tail) <= common.FFMPEG_STDERR_TAIL_CHARS and tail.endswith("(access denied)")


@pytest.mark.unit
def test_the_scrub_keeps_text_that_signs_nothing():
    for text in ("Output file is empty, nothing was encoded", "ExpiresIn 900 s", "s3://bucket/a1/key.mp4 missing",
                 "Header Expires: Thu, 01 Jan 2026", "versionId=v1 size=42"):
        assert common.scrub_urls(text) == text
    assert common.scrub_urls("from 'https://b.s3.amazonaws.com/k?versionId=v1':") == "from '<url>':"


class _LeakingFfmpeg:
    """subprocess.run stand-in for a read over a presigned URL that fails: the header probe exits 8 and every
    keyframe exits 1, each printing ``stderr``."""

    def __init__(self, stderr):
        self.stderr = stderr.encode()

    def __call__(self, argv, **kwargs):
        import subprocess

        return subprocess.CompletedProcess(argv, 8 if "null" in argv else 1, stdout=b"", stderr=self.stderr)


def _imageio_failing_with(stderr):
    def read_frames(path):
        raise IOError("Could not load meta information\n=== stderr ===\n" + stderr)
        yield  # pragma: no cover - a generator like the real one
    return read_frames


@pytest.fixture
def s3():
    fake = FakeS3()
    fake.objects[(_RUN, _CONFIG_KEY)] = json.dumps(_CONFIG_BODY).encode()
    return fake


def _over_bound_video(module, s3, monkeypatch, stderr, read_frames):
    s3.objects[(_AUX, _MANIFEST_KEY)] = json.dumps(_pre_manifest("video")).encode()
    monkeypatch.setattr(module.video, "extract_video", functools.partial(
        video.extract_video, run=_LeakingFfmpeg(stderr), read_frames=read_frames))
    event = _event("long-shift.mp4", "video", "video/mp4")
    event["fileSize"] = module.INPUT_MAX_DOWNLOAD_BYTES + 1
    return event


@pytest.mark.unit
@pytest.mark.parametrize("shape, position", CASES)
def test_a_failed_read_by_url_raises_and_logs_no_signing_value(s3, monkeypatch, shape, position):
    module = _load(s3)
    stderr = _stderr(LEAKS[shape], position)
    event = _over_bound_video(module, s3, monkeypatch, stderr, _imageio_failing_with(stderr))
    with pytest.raises(module.MediaTaskError) as raised:
        module.lambda_handler(event, None)
    assert "No video facts could be read" in str(raised.value)
    _assert_clean(str(raised.value))
    _assert_clean(str(module.logger.mock_calls))


@pytest.mark.unit
@pytest.mark.parametrize("shape, position", CASES)
def test_the_manifest_warnings_of_a_read_by_url_carry_no_signing_value(s3, monkeypatch, shape, position):
    module = _load(s3)
    event = _over_bound_video(module, s3, monkeypatch, _stderr(LEAKS[shape], position), fake_read_frames)
    module.lambda_handler(event, None)
    stored = s3.objects[(_AUX, _MANIFEST_KEY)].decode()
    warnings = json.loads(stored)["warnings"]
    assert any(warning.startswith("ffmpeg header probe exited 8:") for warning in warnings), warnings
    assert sum(1 for warning in warnings if warning.startswith("Keyframe at")) == video.VIDEO_KEYFRAME_COUNT
    _assert_clean(stored)
    _assert_clean(str(module.logger.mock_calls))


@pytest.mark.unit
@pytest.mark.parametrize("shape, position", CASES)
def test_a_segment_window_logs_and_records_no_signing_value(monkeypatch, shape, position):
    s3 = segment_tests._seed(FakeS3())
    module = segment_tests._load(monkeypatch, s3, segment_tests.FakeBedrockRuntime([segment_tests._reply()]),
                                 segment_tests.FakeEvents())
    module.ffmpeg_run = _LeakingFfmpeg(_stderr(LEAKS[shape], position))
    state = dict(segment_tests._STATE, fileSize=module.INPUT_MAX_DOWNLOAD_BYTES + 1)
    response = segment_tests._run(module, state=state)
    assert response["status"] == "SKIPPED"
    failed = segment_tests._failed_record(s3)
    assert "no frame could be read" in failed["cause"]
    _assert_clean(failed["cause"])
    _assert_clean(str(module.logger.mock_calls))
    _assert_clean(segment_tests._everything_written(s3))


class _UnreachableS3(FakeS3):
    """A ranged GET that fails the way botocore does when the endpoint cannot be reached; its message names the
    request URL, query string included."""

    def __init__(self, url):
        super().__init__()
        self.url = url

    def get_object(self, Bucket, Key, Range=None, VersionId=None):
        if Bucket == _ASSETS:
            raise EndpointConnectionError(endpoint_url=self.url)
        return super().get_object(Bucket, Key, Range=Range, VersionId=VersionId)


@pytest.mark.unit
@pytest.mark.parametrize("shape", ["sigv2", "sigv4", "lowercase-token"])
def test_a_failed_ranged_zip_read_raises_with_no_url(shape):
    s3 = _UnreachableS3(LEAKS[shape].replace("GET ", "").replace(" HTTP/1.1", ""))
    s3.objects[(_RUN, _CONFIG_KEY)] = json.dumps(_CONFIG_BODY).encode()
    manifest = _pre_manifest("other")
    manifest["attributes"]["sys_file"]["detectedFormat"] = "zip"
    s3.objects[(_AUX, _MANIFEST_KEY)] = json.dumps(manifest).encode()
    module = _load(s3)
    event = _event("bundle.zip", "other", "application/zip")
    event["fileSize"] = 10_000
    with pytest.raises(module.MediaTaskError) as raised:
        module.lambda_handler(event, None)
    assert "EndpointConnectionError" in str(raised.value) and "<url>" in str(raised.value)
    _assert_clean(str(raised.value))
    _assert_clean(str(module.logger.mock_calls))
