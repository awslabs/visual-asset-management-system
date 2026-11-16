#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The media route through the container's orchestrator (#1550): what a run does for an image, a TGA
or TIFF and a video, against stubbed S3.

An image the web shows as its own preview (displayable format, size strictly under 5 MiB) is neither
downloaded nor previewed when the option that downscales such images is off; everything else is
decoded and its preview uploaded beside the input, at the input's relative path, as for 3D previews."""

import logging
import os
import shutil

from unittest.mock import MagicMock, patch

import pytest

import media_samples as samples
from conftest import is_stubbed
from preview_pipeline import core, media_preview
from preview_pipeline.utils.pipeline.objects import (
    PipelineStage,
    PipelineStatus,
    PipelineType,
    StageOutput,
)

pytestmark = pytest.mark.skipif(
    is_stubbed("PIL") or is_stubbed("PIL.Image"), reason="needs the real Pillow")

LIMIT = 5 * 1024 * 1024
OUTPUT_DIR = "pipelines/preview3dThumbnail/JOB/output/E1/files/"


def _stage(key, ext, version=None):
    output = StageOutput(bucketName="run-bucket", objectDir=OUTPUT_DIR)
    input_file = {"bucketName": "asset-bucket", "objectKey": key, "fileExtension": ext}
    if version:
        input_file["versionId"] = version
    return PipelineStage(
        type=PipelineType.PREVIEW_3D_THUMBNAIL, inputFile=input_file,
        outputFiles=output.__dict__, outputMetadata=output.__dict__, temporaryFiles=output.__dict__)


class _Run:
    """One run of the stage with S3 stubbed: ``source`` is the local file the 'download' copies."""

    def __init__(self, tmp_path, source, key, size=None, params=None, existing=None, version=None,
                 environ=None, upload_fails=False):
        self.tmp_path = tmp_path
        self.download = MagicMock(side_effect=lambda bucket, k, path, **kw: shutil.copyfile(source, path)
                                  or path)
        self.upload = MagicMock(side_effect=lambda bucket, k, path: None if upload_fails else k)
        self.uploaded_bytes = {}
        # size: None = the file's own size, "unknown" = a HEAD that reports none
        self.head = MagicMock(return_value=None if size == "unknown" else
                              (os.path.getsize(source) if size is None else size))
        self.load_3d = MagicMock()
        ext = os.path.splitext(key)[1]
        directories = {}

        def create_dir(parts):
            path = tmp_path / "-".join(parts)
            path.mkdir(parents=True, exist_ok=True)
            return str(path)

        def upload(bucket, k, path):
            self.uploaded_bytes[k] = os.path.getsize(path)
            return self.upload(bucket, k, path)

        env = {k: v for k, v in (environ or {}).items()}
        with patch.object(core, "_create_dir", create_dir), \
                patch.object(core.s3, "list_objects_with_prefix",
                             MagicMock(return_value=existing or [])), \
                patch.object(core, "_load_file", self.load_3d), \
                patch.object(core.s3, "get_object_size", self.head), \
                patch.object(core.s3, "download", self.download), \
                patch.object(core.s3, "upload", upload), \
                patch.dict(os.environ, env, clear=False):
            for name in (media_preview.DOWNSCALE_ENV,):
                if name not in env:
                    os.environ.pop(name, None)
            self.result = core._run_preview_pipeline(_stage(key, ext, version), {}, params or {}, False, "xid")


def _png(tmp_path, size=None):
    if size is None:
        return samples.write_png(str(tmp_path / "src.png"))
    return samples.write_png_of_size(str(tmp_path / "src.png"), size)


@pytest.mark.unit
class TestSelfPreviewableSkip:
    def test_a_small_displayable_image_is_neither_downloaded_nor_previewed(self, tmp_path, caplog):
        caplog.set_level(logging.INFO)
        run = _Run(tmp_path, _png(tmp_path), "xid/pics/photo.png")
        assert run.result.status == PipelineStatus.COMPLETE
        run.download.assert_not_called()
        run.upload.assert_not_called()
        assert "'photo.png' is shown as its own preview; no preview written." in caplog.text

    def test_the_head_uses_the_input_version(self, tmp_path):
        run = _Run(tmp_path, _png(tmp_path), "xid/photo.png", version="v-7")
        assert run.head.call_args.kwargs == {"version_id": "v-7"}

    def test_exactly_five_mib_is_previewed(self, tmp_path):
        run = _Run(tmp_path, _png(tmp_path, LIMIT), "xid/pics/photo.png")
        assert run.result.status == PipelineStatus.COMPLETE
        run.download.assert_called_once()
        key = run.upload.call_args.args[1]
        assert key == OUTPUT_DIR + "pics/photo.png.previewFile.jpg"
        assert run.uploaded_bytes[key] < LIMIT

    def test_one_byte_under_five_mib_is_skipped(self, tmp_path):
        run = _Run(tmp_path, _png(tmp_path, LIMIT - 1), "xid/photo.png")
        run.download.assert_not_called()
        run.upload.assert_not_called()

    def test_an_unknown_size_is_downloaded_and_checked_again_after_the_download(self, tmp_path):
        run = _Run(tmp_path, _png(tmp_path), "xid/photo.png", size="unknown")
        run.download.assert_called_once()
        run.upload.assert_not_called()  # the downloaded file is small, so it is its own preview

    def test_tif_under_the_limit_still_gets_a_jpeg(self, tmp_path):
        source = samples.write_tiff_pillow(str(tmp_path / "src.tif"))
        run = _Run(tmp_path, source, "xid/scan.tif")
        assert run.upload.call_args.args[1] == OUTPUT_DIR + "scan.tif.previewFile.jpg"

    def test_the_option_on_writes_a_smaller_jpeg_for_a_large_photo(self, tmp_path):
        import numpy as np
        pixels = (np.random.default_rng(5).random((2400, 3600, 3)) * 80 + 90).astype(np.uint8)
        source = samples.write_jpeg(str(tmp_path / "src.jpg"), pixels=pixels, quality=92)
        run = _Run(tmp_path, source, "xid/photo.jpg", environ={media_preview.DOWNSCALE_ENV: "true"})
        assert run.upload.call_args.args[1] == OUTPUT_DIR + "photo.jpg.previewFile.jpg"

    def test_the_option_on_for_a_small_png_writes_nothing(self, tmp_path, caplog):
        caplog.set_level(logging.INFO)
        run = _Run(tmp_path, _png(tmp_path), "xid/small.png", environ={media_preview.DOWNSCALE_ENV: "true"})
        assert run.result.status == PipelineStatus.COMPLETE
        run.upload.assert_not_called()
        assert "No smaller preview possible" in caplog.text


@pytest.mark.unit
class TestMediaRun:
    def test_a_large_image_uploads_a_jpeg_beside_the_input_and_never_loads_3d(self, tmp_path):
        run = _Run(tmp_path, _png(tmp_path, LIMIT + 500_000), "xid/a/b/big.png")
        assert run.result.status == PipelineStatus.COMPLETE, run.result.errorMessage
        assert run.upload.call_args.args[1] == OUTPUT_DIR + "a/b/big.png.previewFile.jpg"
        assert run.upload.call_args.args[0] == "run-bucket"
        run.load_3d.assert_not_called()

    def test_the_input_version_is_threaded_to_the_download(self, tmp_path):
        run = _Run(tmp_path, _png(tmp_path, LIMIT + 1), "xid/big.png", version="ver-3")
        assert run.download.call_args.kwargs == {"version_id": "ver-3"}

    def test_a_video_uploads_a_gif(self, tmp_path):
        source = samples.write_video(str(tmp_path / "src.mp4"), "mp4", "mpeg4")
        run = _Run(tmp_path, source, "xid/clips/take1.mp4")
        assert run.result.status == PipelineStatus.COMPLETE, run.result.errorMessage
        assert run.upload.call_args.args[1] == OUTPUT_DIR + "clips/take1.mp4.previewFile.gif"

    def test_an_audio_only_file_completes_without_an_upload(self, tmp_path, caplog):
        caplog.set_level(logging.INFO)
        source = samples.write_audio_only(str(tmp_path / "src.mkv"))
        run = _Run(tmp_path, source, "xid/talk.mkv")
        assert run.result.status == PipelineStatus.COMPLETE
        run.upload.assert_not_called()
        assert "No video stream in 'talk.mkv'; no preview written." in caplog.text

    def test_output_type_is_honoured(self, tmp_path):
        source = samples.write_video(str(tmp_path / "src.mp4"), "mp4", "mpeg4")
        run = _Run(tmp_path, source, "xid/take1.mp4", params={"outputType": "jpg"})
        assert run.upload.call_args.args[1].endswith("take1.mp4.previewFile.jpg")

    def test_overwrite_false_keeps_an_existing_preview(self, tmp_path):
        run = _Run(tmp_path, _png(tmp_path, LIMIT + 1), "xid/big.png",
                   params={"overwriteExistingPreviewFiles": False},
                   existing=["xid/big.png.previewFile.jpg"])
        assert run.result.status == PipelineStatus.COMPLETE
        run.download.assert_not_called()
        run.upload.assert_not_called()

    def test_overwrite_true_replaces_it(self, tmp_path):
        run = _Run(tmp_path, _png(tmp_path, LIMIT + 1), "xid/big.png",
                   existing=["xid/big.png.previewFile.jpg"])
        run.upload.assert_called_once()

    def test_a_decompression_bomb_fails_with_a_message_naming_the_type(self, tmp_path):
        from PIL import Image
        bomb = tmp_path / "src.png"
        Image.new("1", (40000, 40000)).save(bomb, "PNG")
        with open(bomb, "ab") as handle:
            handle.write(b"\0" * LIMIT)
        run = _Run(tmp_path, str(bomb), "xid/bomb.png")
        assert run.result.status == PipelineStatus.FAILED
        assert "PNG" in run.result.errorMessage and "pixels" in run.result.errorMessage
        assert "Traceback" not in run.result.errorMessage
        run.upload.assert_not_called()

    def test_a_corrupt_tga_fails_with_a_message(self, tmp_path):
        source = tmp_path / "src.tga"
        source.write_bytes(os.urandom(200))
        run = _Run(tmp_path, str(source), "xid/bad.tga")
        assert run.result.status == PipelineStatus.FAILED
        assert "TGA" in run.result.errorMessage

    def test_a_zero_length_video_fails_with_a_message(self, tmp_path):
        source = tmp_path / "src.mov"
        source.write_bytes(b"")
        run = _Run(tmp_path, str(source), "xid/zero.mov")
        assert run.result.status == PipelineStatus.FAILED
        assert "MOV" in run.result.errorMessage

    def test_a_failed_upload_is_reported(self, tmp_path):
        run = _Run(tmp_path, _png(tmp_path, LIMIT + 1), "xid/big.png", upload_fails=True)
        assert run.result.status == PipelineStatus.FAILED
        assert "Failed to upload preview" in run.result.errorMessage

    def test_an_out_of_memory_decode_names_the_remedy(self, tmp_path):
        source = _png(tmp_path, LIMIT + 1)
        with patch.object(core.media_preview, "generate", MagicMock(side_effect=MemoryError)):
            run = _Run(tmp_path, source, "xid/big.png")
        assert run.result.status == PipelineStatus.FAILED
        assert "memory" in run.result.errorMessage.lower()

    def test_an_unexpected_decoder_error_has_no_traceback_in_the_message(self, tmp_path):
        source = _png(tmp_path, LIMIT + 1)
        with patch.object(core.media_preview, "generate", MagicMock(side_effect=RuntimeError("boom"))):
            run = _Run(tmp_path, source, "xid/big.png")
        assert run.result.status == PipelineStatus.FAILED
        assert "PNG" in run.result.errorMessage and "boom" in run.result.errorMessage
        assert "Traceback" not in run.result.errorMessage


@pytest.mark.unit
class TestThreeDRoutingIsUnchanged:
    def test_a_3d_extension_is_not_media(self):
        for ext in (".glb", ".e57", ".ply", ".usdz", ".stl", ".obj", ".step", ".las", ".lcc2"):
            assert not media_preview.is_media(ext)
            assert ext in core._all_extensions()

    def test_a_media_extension_is_not_in_the_3d_list(self):
        for ext in (".png", ".tif", ".tga", ".mp4", ".webp"):
            assert media_preview.is_media(ext)
            assert ext not in core._all_extensions()
            assert ext in core._media_extensions()

    def test_the_local_test_finder_sees_both(self, tmp_path):
        (tmp_path / "a.mp4").write_bytes(b"x")
        assert core._find_local_test_file(str(tmp_path)).endswith("a.mp4")
