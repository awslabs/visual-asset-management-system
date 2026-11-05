# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Any file gets its stored attributes: the `other` class takes the generic probe chain, the known classes
record every embedded tag, and with the GenAI layer off no extractor produces an analysis image."""

import io
import json
import tarfile
import zipfile

import pytest
from PIL import Image

from media_extractors import common, generic, images, office
from system_genai_media_fixtures import (
    FakeFfmpeg, FakeS3, docx_bytes, fake_read_frames, jpeg_with_exif_bytes, make_ctx, minimal_pdf_bytes, png_bytes,
    write,
)
import test_system_genai_media_handler as handler_suite


# What ffmpeg prints for a file that is not a media container: no `Input #0`, no streams, exit 1.
NOT_MEDIA = FakeFfmpeg(header="blob: Invalid data found when processing input\n", probe_returncode=1)


def _ctx(tmp_path, name, **over):
    ctx = make_ctx(tmp_path, name)
    for key, value in over.items():
        setattr(ctx, key, value)
    return ctx


@pytest.fixture(autouse=True)
def _no_real_ffmpeg(monkeypatch):
    # The generic probe's media step and the video extractor both reach imageio_ffmpeg.get_ffmpeg_exe().
    monkeypatch.setattr(generic.video.imageio_ffmpeg, "get_ffmpeg_exe", lambda: "/fake/ffmpeg")


@pytest.mark.unit
class TestGenericProbeChain:
    def test_a_jpeg_under_a_foreign_extension_is_analysed_as_an_image(self, tmp_path):
        path = write(tmp_path, "shot.bak", jpeg_with_exif_bytes())
        result = generic.extract_generic(path, _ctx(tmp_path, "shot.bak"), run=NOT_MEDIA)
        assert result.file_class == common.CLASS_IMAGE
        sys_image = result.attributes["sys_image"]
        assert (sys_image["width"], sys_image["height"]) == (40, 30)
        assert sys_image["exif"]["make"] == "ProtoMake"
        assert sys_image["exif"]["tags"]["Make"] == "ProtoMake" and sys_image["exif"]["tags"]["Model"] == "ProtoModel"
        assert sys_image["exif"]["tags"]["DateTimeOriginal"] == "2026:01:02 03:04:05"
        assert result.render_images and result.warnings[0].startswith("shot.bak: extension .bak is not an image")

    def test_a_media_container_under_a_foreign_extension_takes_the_video_extractor(self, tmp_path):
        path = write(tmp_path, "footage.dat", b"\x00")
        ffmpeg = FakeFfmpeg()
        result = generic.extract_generic(path, _ctx(tmp_path, "footage.dat"), run=ffmpeg)
        # The FakeFfmpeg header is the mp4 sample, so the probe sees a video stream and extract_video runs.
        assert result.file_class == common.CLASS_VIDEO
        assert result.attributes["sys_media"]["width"] == 1920
        assert result.warnings[0].startswith("footage.dat: extension .dat is not a video extension")

    def test_a_zip_container_is_described_with_its_opc_properties(self, tmp_path):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("docProps/core.xml",
                             '<?xml version="1.0"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                             'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Bundle</dc:title><dc:creator>QA</dc:creator></cp:coreProperties>')
            archive.writestr("models/a.obj", b"v 0 0 0\n")
            archive.writestr("models/b.mtl", b"newmtl x\n")
            archive.writestr("readme.txt", b"hello")
        path = write(tmp_path, "bundle.pkg", buffer.getvalue())
        result = generic.extract_generic(path, _ctx(tmp_path, "bundle.pkg"), run=NOT_MEDIA)
        assert result.file_class == common.CLASS_OTHER
        archive_facts = result.attributes["sys_archive"]
        assert archive_facts["containerFormat"] == "zip" and archive_facts["entryCount"] == 4
        assert archive_facts["uncompressedBytes"] > 0
        assert set(archive_facts["topLevelEntries"]) == {"docProps", "models", "readme.txt"}
        assert archive_facts["entryExtensions"] == {".xml": 1, ".obj": 1, ".mtl": 1, ".txt": 1}
        assert archive_facts["coreProperties"] == {"title": "Bundle", "creator": "QA"}
        assert result.facts["container"] == "zip archive" and result.facts["entries"] == "4 entries"
        assert result.render_skipped is None and result.render_images == []

    def test_a_tar_container_is_described(self, tmp_path):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo("data/readings.csv")
            payload = b"a,b\n1,2\n"
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        path = write(tmp_path, "readings.archive", buffer.getvalue())
        result = generic.extract_generic(path, _ctx(tmp_path, "readings.archive"), run=NOT_MEDIA)
        assert result.attributes["sys_archive"]["containerFormat"] == "tar"
        assert result.attributes["sys_archive"]["entries"] == ["data/readings.csv"]

    def test_readable_text_under_a_foreign_extension_takes_the_text_extractor(self, tmp_path):
        path = write(tmp_path, "notes.conf", b"key = value\nother = 2\n")
        result = generic.extract_generic(path, _ctx(tmp_path, "notes.conf"), run=NOT_MEDIA)
        assert result.file_class == common.CLASS_TEXT
        assert result.attributes["sys_text"]["lineCount"] == 2
        assert result.warnings[0].startswith("notes.conf: extension .conf is not a text extension")

    def test_unrecognised_bytes_keep_other_with_a_warning_and_no_attributes(self, tmp_path):
        path = write(tmp_path, "blob.bin", bytes(range(256)) * 4)
        result = generic.extract_generic(path, _ctx(tmp_path, "blob.bin"), run=NOT_MEDIA)
        assert result.file_class == common.CLASS_OTHER
        assert result.attributes == {} and result.render_skipped == common.RENDER_SKIPPED_UNSUPPORTED
        assert result.warnings == ["blob.bin: no probe recognised the format; sys_file attributes only"]

    def test_a_failing_probe_is_noted_not_fatal(self, tmp_path, monkeypatch):
        path = write(tmp_path, "blob.bin", bytes(range(256)) * 4)
        monkeypatch.setattr(generic, "_probe_zip", lambda p, c: (_ for _ in ()).throw(RuntimeError("boom")))
        result = generic.extract_generic(path, _ctx(tmp_path, "blob.bin"), run=NOT_MEDIA)
        assert result.file_class == common.CLASS_OTHER
        assert any("zip probe failed: boom" in warning for warning in result.warnings)


@pytest.mark.unit
class TestEmbeddedMetadataCapture:
    def test_image_records_every_exif_tag_beside_the_curated_subset(self, tmp_path):
        path = write(tmp_path, "shot.jpg", jpeg_with_exif_bytes())
        result = images.extract_image(path, _ctx(tmp_path, "shot.jpg"))
        exif = result.attributes["sys_image"]["exif"]
        # The curated keys the catalogue reads stay where they were.
        assert exif["make"] == "ProtoMake" and exif["orientation"] == 6 and exif["dateTimeOriginal"] == "2026:01:02 03:04:05"
        # Every tag by its TIFF name, the pointer tags excluded.
        assert exif["tags"] == {"Make": "ProtoMake", "Model": "ProtoModel", "Orientation": 6,
                                "DateTimeOriginal": "2026:01:02 03:04:05"}

    def test_png_text_chunks_are_recorded(self, tmp_path):
        image = Image.new("RGB", (8, 8), (1, 2, 3))
        from PIL import PngImagePlugin
        info = PngImagePlugin.PngInfo()
        info.add_text("Description", "Scanner export")
        info.add_text("Software", "ProtoScan 3")
        buffer = io.BytesIO()
        image.save(buffer, "PNG", pnginfo=info)
        path = write(tmp_path, "scan.png", buffer.getvalue())
        result = images.extract_image(path, _ctx(tmp_path, "scan.png"))
        assert result.attributes["sys_image"]["textChunks"] == {"Description": "Scanner export", "Software": "ProtoScan 3"}
        assert "exif" not in result.attributes["sys_image"]

    def test_image_renders_nothing_when_the_context_asks_for_no_renders(self, tmp_path):
        path = write(tmp_path, "photo.png", png_bytes(300, 100))
        result = images.extract_image(path, _ctx(tmp_path, "photo.png", render_images=False))
        assert result.render_images == [] and result.render_skipped is None
        assert result.attributes["sys_image"]["width"] == 300 and "analysisImage" not in result.facts

    def test_pdf_renders_no_pages_when_the_context_asks_for_no_renders(self, tmp_path):
        from media_extractors import documents
        path = write(tmp_path, "report.pdf", minimal_pdf_bytes(pages=2))
        result = documents.extract_pdf(path, _ctx(tmp_path, "report.pdf", render_images=False))
        assert result.render_images == [] and result.render_skipped is None
        assert result.attributes["sys_document"]["pageCount"] == 2 and result.text_excerpt

    def test_office_core_properties_are_all_recorded(self, tmp_path):
        import docx as docx_lib
        document = docx_lib.Document(io.BytesIO(docx_bytes()))
        core = document.core_properties
        core.title, core.author, core.subject, core.keywords = "T", "A", "S", "k1, k2"
        core.category, core.comments, core.content_status = "Spec", "Draft one", "Final"
        core.identifier, core.language, core.last_modified_by, core.version = "DOC-1", "en-US", "Editor", "3"
        core.revision = 7
        import datetime
        core.created = datetime.datetime(2024, 1, 2, 3, 4, 5)
        core.modified = datetime.datetime(2024, 2, 3, 4, 5, 6)
        core.last_printed = datetime.datetime(2024, 3, 4, 5, 6, 7)
        properties = office._core_properties(core)
        assert properties == {"title": "T", "author": "A", "subject": "S", "keywords": "k1, k2", "category": "Spec",
                              "comments": "Draft one", "contentStatus": "Final", "identifier": "DOC-1",
                              "language": "en-US", "lastModifiedBy": "Editor", "revision": "7", "version": "3",
                              "createdAt": "2024-01-02T03:04:05Z", "modifiedAt": "2024-02-03T04:05:06Z",
                              "lastPrintedAt": "2024-03-04T05:06:07Z"}


@pytest.mark.unit
class TestHandlerAnyFile:
    def test_an_unknown_extension_runs_the_generic_probe_and_reclassifies(self, monkeypatch):
        s3 = FakeS3()
        s3.objects[(handler_suite._AUX, handler_suite._MANIFEST_KEY)] = json.dumps(
            handler_suite._pre_manifest(file_class="other")).encode()
        s3.objects[(handler_suite._RUN, handler_suite._CONFIG_KEY)] = json.dumps(handler_suite._CONFIG_BODY).encode()
        s3.objects[(handler_suite._ASSETS, "a1/shot.bak")] = jpeg_with_exif_bytes()
        module = handler_suite._load(s3)
        monkeypatch.setattr(module.generic.video.imageio_ffmpeg, "get_ffmpeg_exe", lambda: "/fake/ffmpeg")
        monkeypatch.setattr(module.generic.video, "run_ffmpeg",
                            lambda args, run=None, timeout=None: NOT_MEDIA(["/fake/ffmpeg", "-f", "null", *args]))
        response = module.lambda_handler(handler_suite._event("shot.bak", "other", "application/octet-stream"), None)
        manifest = s3.manifest(handler_suite._AUX, handler_suite._MANIFEST_KEY)
        assert response["fileClass"] == "image" and manifest["fileClass"] == "image"
        assert manifest["attributes"]["sys_image"]["exif"]["tags"]["Make"] == "ProtoMake"
        assert response["renderImageCount"] == 1

    def test_genai_off_renders_nothing_and_writes_no_segment_plan(self, monkeypatch):
        s3 = FakeS3()
        s3.objects[(handler_suite._AUX, handler_suite._MANIFEST_KEY)] = json.dumps(handler_suite._pre_manifest()).encode()
        s3.objects[(handler_suite._RUN, handler_suite._CONFIG_KEY)] = json.dumps(handler_suite._CONFIG_BODY).encode()
        s3.objects[(handler_suite._ASSETS, "a1/photo.png")] = png_bytes(3000, 1000)
        module = handler_suite._load(s3)
        event = handler_suite._event("photo.png", "image")
        event["genAiAnalysisEnabled"] = False
        response = module.lambda_handler(event, None)
        manifest = s3.manifest(handler_suite._AUX, handler_suite._MANIFEST_KEY)
        assert manifest["attributes"]["sys_image"]["width"] == 3000
        assert manifest["renderImages"] == [] and response["renderImageCount"] == 0 and manifest["renderSkipped"] is None
        # A video with segments configured: the plan needs the analysis model, so none is written.
        s3.objects[(handler_suite._ASSETS, "a1/clip.mp4")] = b"\x00"
        monkeypatch.setitem(module._EXTRACTORS, "video", handler_suite._fake_video(92.48))
        response = module.lambda_handler(handler_suite._video_event(genAiAnalysisEnabled=False), None)
        assert handler_suite._puts_under(s3, handler_suite._PREFIX + "segments/") == []
        assert not any(field in response for field in handler_suite._SEGMENT_FIELDS)

    def test_render_images_false_alone_also_suppresses_the_analysis_image(self):
        s3 = FakeS3()
        s3.objects[(handler_suite._AUX, handler_suite._MANIFEST_KEY)] = json.dumps(handler_suite._pre_manifest()).encode()
        s3.objects[(handler_suite._RUN, handler_suite._CONFIG_KEY)] = json.dumps(handler_suite._CONFIG_BODY).encode()
        s3.objects[(handler_suite._ASSETS, "a1/photo.png")] = png_bytes(300, 100)
        module = handler_suite._load(s3)
        event = handler_suite._event("photo.png", "image")
        event["renderImages"] = False
        response = module.lambda_handler(event, None)
        assert response["renderImageCount"] == 0
