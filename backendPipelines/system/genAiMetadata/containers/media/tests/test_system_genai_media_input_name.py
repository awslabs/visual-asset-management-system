#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A warning or a failure about the input names the user's file, not the downloaded copy (the substance of #1188,
fixed in v2.6.7 for the retired CAD/Mesh Metadata Extraction pipeline).

The MEDIA handler and the segment handler download the input to ``input<ext>`` in a working directory of their own,
so a library that quoted the path it was given (Pillow, ffmpeg, python-docx, python-pptx) produced "cannot identify
image file '/tmp/media-1a2b/input.png'" whatever the operator selected, and an asset holding several files gave no
way to tell which one it was. The handlers now name the file's path within its asset wherever a message quotes the
copy. The extractors and ffmpeg here are the real ones, on bytes that are no format at all."""

import json

import pytest

from media_extractors.common import name_input
from system_genai_media_fixtures import FakeBedrockRuntime, FakeEvents, FakeFfmpeg, FakeS3
from test_system_genai_media_handler import _ASSETS, _AUX, _CONFIG_BODY, _CONFIG_KEY, _MANIFEST_KEY, _RUN
from test_system_genai_media_handler import _event, _load, _pre_manifest
import test_system_genai_media_segment_handler as segments

_GARBAGE = b"\x00\x01garbage that is no format at all\xff\xfe" * 20
_FOLDER = "/shots/2026"


def _run_media(name, file_class, content_type, body):
    """The MEDIA handler on ``body`` stored as ``<folder>/<name>``: the manifest's warnings, or the failure."""
    s3 = FakeS3()
    s3.objects[(_RUN, _CONFIG_KEY)] = json.dumps(_CONFIG_BODY).encode()
    s3.objects[(_AUX, _MANIFEST_KEY)] = json.dumps(_pre_manifest(file_class, warnings=())).encode()
    module = _load(s3)
    event = _event(name, file_class, content_type)
    key = f"a1{_FOLDER}/{name}"
    event.update({"inputS3AssetFilePath": f"s3://{_ASSETS}/{key}", "relativePath": f"{_FOLDER}/{name}",
                  "fileSize": len(body)})
    s3.objects[(_ASSETS, key)] = body
    module.lambda_handler(event, None)
    return json.loads(s3.objects[(_AUX, _MANIFEST_KEY)])["warnings"]


@pytest.mark.unit
class TestAWarningNamesTheUsersFile:
    @pytest.mark.parametrize("name, file_class, content_type, body, says", [
        ("photo.png", "image", "image/png", _GARBAGE, "Image could not be decoded"),
        ("photo.jpg", "image", "image/jpeg", b"\xff\xd8\xff\xe0" + _GARBAGE, "Image could not be decoded"),
        ("report.docx", "document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         _GARBAGE, "DOCX could not be opened"),
        ("deck.pptx", "document", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
         _GARBAGE, "PPTX could not be opened"),
    ], ids=["png", "jpg", "docx", "pptx"])
    def test_the_warning_names_the_file_and_not_the_copy(self, name, file_class, content_type, body, says):
        extension = name.rsplit(".", 1)[1]
        warnings = _run_media(name, file_class, content_type, body)
        reported = [warning for warning in warnings if says in warning]
        assert reported, warnings
        assert f"{_FOLDER}/{name}" in reported[0]
        assert not any(f"input.{extension}" in warning for warning in warnings), warnings


@pytest.mark.unit
class TestAFailureNamesTheUsersFile:
    def test_an_unreadable_video_fails_naming_the_file(self):
        # ffmpeg ends its stderr with "Error opening input file <path>."; the failure keeps its type.
        with pytest.raises(RuntimeError) as failure:
            _run_media("clip.mp4", "video", "video/mp4", _GARBAGE)
        assert "No video facts could be read" in str(failure.value)
        assert f"{_FOLDER}/clip.mp4" in str(failure.value)
        assert "input.mp4" not in str(failure.value)

    def test_a_failure_of_a_type_not_built_from_a_message_is_a_media_task_error(self, monkeypatch):
        class TwoPartError(Exception):
            def __init__(self, path, reason):
                super().__init__(f"{reason}: {path}")

        s3 = FakeS3()
        s3.objects[(_RUN, _CONFIG_KEY)] = json.dumps(_CONFIG_BODY).encode()
        s3.objects[(_AUX, _MANIFEST_KEY)] = json.dumps(_pre_manifest()).encode()
        s3.objects[(_ASSETS, "a1/photo.png")] = _GARBAGE
        module = _load(s3)

        def refuse(path, ctx):
            raise TwoPartError(path, "decoder refused")

        monkeypatch.setattr(module.images, "extract_image", refuse)
        with pytest.raises(module.MediaTaskError, match=r"^decoder refused: /photo\.png$"):
            module.lambda_handler(_event("photo.png", "image"), None)


@pytest.mark.unit
class TestASegmentWarningNamesTheUsersFile:
    def test_a_frame_failure_on_the_downloaded_copy_names_the_file(self, monkeypatch):
        class QuotingFfmpeg(FakeFfmpeg):
            def __call__(self, argv, **kwargs):
                import subprocess

                source = argv[argv.index("-i") + 1] if "-i" in argv else ""
                if "-ss" in argv and not source.startswith("https://"):
                    self.calls.append(list(argv))
                    stderr = f"[in#0 @ 0x1] Error opening input: Invalid data\nError opening input file {source}.\n"
                    return subprocess.CompletedProcess(argv, 1, stdout=b"", stderr=stderr.encode())
                return super().__call__(argv, **kwargs)

        s3 = segments._seed(FakeS3())
        module = segments._load(monkeypatch, s3, FakeBedrockRuntime([segments._reply()]), FakeEvents())
        module.ffmpeg_run = QuotingFfmpeg(fail_when_url=True)
        response = segments._run(module)
        assert response["status"] == "SKIPPED" and s3.downloads, response
        cause = segments._failed_record(s3)["cause"]
        assert "Error opening input file /clips/tour.mp4." in cause, cause
        assert "input.mp4" not in cause


@pytest.mark.unit
class TestNameInput:
    _COPY = "/tmp/media-1a2b3c/input.mp4"

    def test_the_copy_s_path_becomes_the_name(self):
        text = f"Error opening input file {self._COPY}. cannot identify image file '{self._COPY}'"
        assert name_input(text, self._COPY, "/clips/tour.mp4") == (
            "Error opening input file /clips/tour.mp4. cannot identify image file '/clips/tour.mp4'")

    def test_an_end_of_the_path_at_the_start_of_a_stderr_tail_becomes_the_name(self):
        # scrubbed_tail keeps the last characters, so the tail can start inside the path.
        assert name_input("2b3c/input.mp4: Invalid data", self._COPY, "/clips/tour.mp4") == (
            "/clips/tour.mp4: Invalid data")

    def test_another_path_that_ends_in_the_same_name_is_left_alone(self):
        text = "archive entry docs/input.mp4 and /data/other/input.mp4"
        assert name_input(text, self._COPY, "/clips/tour.mp4") == text

    def test_a_name_with_regex_characters_is_written_as_it_is(self):
        assert name_input(f"at {self._COPY}", self._COPY, r"/a\1 (b).mp4") == r"at /a\1 (b).mp4"

    def test_no_local_path_leaves_the_text(self):
        assert name_input("anything", "", "/x.png") == "anything"
