#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The MEDIA branch handler: it reads the pipeline state, requires the pre-written analysis manifest,
downloads the versioned input, reads maxTextChars from the template configuration and extractGeoLocation
from the state, dispatches on fileClass (a .json always through the text sniffer), uploads the renders
beside the manifest, rewrites the manifest with sys_file, facts and warnings preserved, and returns the
whole state so a LambdaInvoke with outputPath "$.Payload" keeps every hop field. Unexpected failures
propagate (the state machine's Catch degrades the render), and nothing is left in the work directory either
way.

The handler is loaded by file path under a suite-private module name with boto3.client patched, so the
module-level S3 client is the fake for every test."""

import importlib.util
import json
import os
import sys
from unittest.mock import patch

import pytest

from system_genai_media_fixtures import (
    SYDNEY_GPS, FakeS3, csv_bytes, geojson_point_dict, jpeg_with_exif_bytes, minimal_pdf_bytes, png_bytes,
    tileset_dict,
)

_HANDLER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "handler.py")
_AUX = "aux-bucket"
_ASSETS = "asset-bucket"
_RUN = "run-bucket"
_PREFIX = "pipelines/system-genai-metadata/E1/"
_MANIFEST_KEY = _PREFIX + "analysis.json"
_MANIFEST_LOCATION = f"s3://{_AUX}/{_MANIFEST_KEY}"
_CONFIG_KEY = "pipelines/workflowExecutionInputs/E1/system-genai-metadata/config.json"
_CONFIG_LOCATION = f"s3://{_RUN}/{_CONFIG_KEY}"
_SEGMENT_PLAN_KEY = _PREFIX + "segments/plan.json"
_SEGMENT_ITEMS_KEY = _PREFIX + "segments/items.json"
_MAP_RESULTS_PREFIX = _PREFIX + "segments/results/"
_FULL_TEXT_KEY = _PREFIX + "text/full.txt"
_PAGES_KEY = _PREFIX + "text/pages.json"
_SEGMENT_FIELDS = ("videoSegmentPlanS3Location", "videoSegmentItemsS3Location", "videoSegmentItemsBucket",
                   "videoSegmentItemsKey", "videoSegmentResultsBucket", "videoSegmentResultsPrefix", "videoSegmentCount")
_TEXT_FIELDS = ("fullTextS3Location", "fullTextChars", "fullTextTruncated", "fullTextSkipped", "pageOffsetsS3Location")
# The rendered template configBody; the handler reads maxTextChars
# only -- extractGeoLocation reaches it as a state field that constructPipeline copies from this body.
_CONFIG_BODY = {"seedWithExistingMetadata": True, "includeSiblingFiles": True, "renderViews": 8,
                "maxTextChars": 12000, "writeAssetKeywords": False, "embeddingIncludeTextExcerpt": True,
                "writeExtractedMetadata": True, "extractGeoLocation": True,
                "classificationVocabulary": {"categories": {"Other": {"description": "Anything else", "subcategories": []}},
                                             "styles": ["other"], "materials": ["other"], "colors": ["grey"],
                                             "allowUnlisted": True}}


def _load(fake_s3):
    with patch("boto3.client", return_value=fake_s3) as client_factory:
        spec = importlib.util.spec_from_file_location("system_genai_media_handler_under_test", _HANDLER_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    # The client is built once, at import, with the retry configuration (backendPipelines/CLAUDE.md).
    assert client_factory.call_args.args == ("s3",)
    assert client_factory.call_args.kwargs["config"] is module.retry_config
    assert module.s3_client is fake_s3
    return module


def _pre_manifest(file_class="image", warnings=("pre-existing",)):
    return {
        "schemaVersion": 1,
        "fileClass": file_class,
        "renderBranch": "MEDIA",
        "attributes": {"sys_file": {"name": "photo.png", "ext": ".png", "sizeBytes": 123, "contentType": "image/png",
                                    "etag": "e1", "versionId": "v1"}},
        "renderImages": [],
        "textExcerpt": "",
        "facts": {"fileSize": "123 bytes"},
        "warnings": list(warnings),
        "renderSkipped": None,
    }


def _event(name, file_class, content_type="image/png"):
    """The state constructPipeline returns, trimmed to the hop fields a later
    task reads back; the handler must return every one of them unchanged."""
    return {
        "jobName": "PipelineJob_20260908_120000_000_1a2b3c4d",
        "externalSfnTaskToken": "",
        "assetId": "a1",
        "databaseId": "db1",
        "inputS3AssetFilePath": f"s3://{_ASSETS}/a1/{name}",
        "outputS3AssetMetadataPath": f"s3://{_ASSETS}/a1/",
        "inputOutputS3AssetAuxiliaryFilesPath": f"s3://{_AUX}/{_PREFIX}",
        "inputConfigurationS3Location": _CONFIG_LOCATION,
        "relativePath": f"/{name}",
        "versionId": "v1",
        "etag": "e1",
        "fileSize": 123,
        "contentType": content_type,
        "fileClass": file_class,
        "fileExt": os.path.splitext(name)[1].lower(),
        "renderBranch": "MEDIA",
        "renderSkipped": None,
        "analysisManifestS3Location": _MANIFEST_LOCATION,
        "vectorSearchEnabled": False,
        "extractGeoLocation": True,
        "status": "STARTING",
    }


def _video_event(**over):
    """A video with segments on: vector search on, a 10-second window, chunking on (irrelevant to a video)."""
    event = _event("clip.mp4", "video", "video/mp4")
    event.update({"vectorSearchEnabled": True, "videoSegmentSeconds": 10, "contentChunking": True})
    event.update(over)
    return event


def _text_event(name, file_class, content_type, **over):
    """A document/text/data file with chunking on and vector search on, segments off."""
    event = _event(name, file_class, content_type)
    event.update({"vectorSearchEnabled": True, "contentChunking": True, "videoSegmentSeconds": 0})
    event.update(over)
    return event


def _fake_video(duration):
    """Stands in for video.extract_video: the probed duration without running ffmpeg."""
    def extract(path, ctx):
        from media_extractors.common import BranchResult

        return BranchResult(file_class="video", attributes={"sys_media": {"kind": "video", "durationSeconds": duration}},
                            facts={"duration": "1 min 32 s"})

    return extract


def _puts_under(s3, prefix):
    return [key for _bucket, key, _content_type in s3.puts if key.startswith(prefix)]


@pytest.fixture
def s3():
    fake = FakeS3()
    fake.objects[(_AUX, _MANIFEST_KEY)] = json.dumps(_pre_manifest()).encode()
    fake.objects[(_RUN, _CONFIG_KEY)] = json.dumps(_CONFIG_BODY).encode()
    return fake


@pytest.mark.unit
class TestLambdaHandler:
    def test_image_end_to_end(self, s3, monkeypatch):
        s3.objects[(_ASSETS, "a1/photo.png")] = png_bytes(3000, 1000)
        module = _load(s3)
        created = []
        real_mkdtemp = module.tempfile.mkdtemp

        def recording_mkdtemp(*args, **kwargs):
            created.append(real_mkdtemp(*args, **kwargs))
            return created[-1]

        monkeypatch.setattr(module.tempfile, "mkdtemp", recording_mkdtemp)
        event = _event("photo.png", "image")
        response = module.lambda_handler(event, None)
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert manifest["schemaVersion"] == 1
        assert manifest["fileClass"] == "image" and manifest["renderBranch"] == "MEDIA"
        assert manifest["attributes"]["sys_file"]["etag"] == "e1"
        assert manifest["attributes"]["sys_image"]["width"] == 3000
        assert manifest["renderImages"] == [_PREFIX + "renders/media-01.png"]
        assert manifest["textExcerpt"] == ""
        assert manifest["facts"]["fileSize"] == "123 bytes" and manifest["facts"]["dimensions"] == "3000 x 1000 px"
        assert manifest["warnings"] == ["pre-existing"]
        assert manifest["renderSkipped"] is None
        assert (_AUX, _PREFIX + "renders/media-01.png", "image/png") in s3.puts
        assert (_AUX, _MANIFEST_KEY, "application/json") in s3.puts
        assert s3.objects[(_AUX, _PREFIX + "renders/media-01.png")].startswith(b"\x89PNG")
        assert s3.downloads == [(_ASSETS, "a1/photo.png", "v1")]
        # The whole state comes back (LambdaInvoke outputPath "$.Payload"), so every hop field survives for
        # GenerateMetadataTask; this task's own fields are laid over it.
        assert response == {**event, "renderImageCount": 1, "renderSkipped": None, "warningCount": 1}
        assert response["assetId"] == "a1" and response["relativePath"] == "/photo.png"
        assert created and not os.path.exists(created[0])

    def test_text_json_tileset_is_reclassified(self, s3):
        s3.objects[(_ASSETS, "a1/tileset.json")] = json.dumps(tileset_dict()).encode()
        module = _load(s3)
        response = module.lambda_handler(_event("tileset.json", "text", "application/json"), None)
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert response["fileClass"] == "tiles3d" and manifest["fileClass"] == "tiles3d"
        # The reclassification rides on the returned state next to the untouched hop fields.
        assert response["assetId"] == "a1" and response["relativePath"] == "/tileset.json"
        assert "sys_tiles3d" in manifest["attributes"] and "sys_file" in manifest["attributes"]
        assert manifest["renderImages"] == [] and response["renderImageCount"] == 0

    def test_json_under_the_data_class_is_sniffed_as_geojson(self, s3):
        # constructPipeline's classifier already said `data` for a GeoJSON .json; the extension, not the class,
        # routes it to the text sniffer, which writes sys_geo rather than trying to read the file as CSV.
        s3.objects[(_ASSETS, "a1/sites.json")] = json.dumps(geojson_point_dict()).encode()
        module = _load(s3)
        response = module.lambda_handler(_event("sites.json", "data", "application/geo+json"), None)
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert response["fileClass"] == "data" and manifest["fileClass"] == "data"
        assert manifest["attributes"]["sys_geo"]["footprint"] == {"type": "Point", "coordinates": [151.2, -33.9]}
        assert "sys_data" not in manifest["attributes"] and "sys_file" in manifest["attributes"]
        assert manifest["renderSkipped"] is None and response["renderImageCount"] == 0

    @pytest.mark.parametrize("enabled", [True, False])
    def test_gps_follows_the_state_flag(self, s3, enabled):
        s3.objects[(_ASSETS, "a1/shot.jpg")] = jpeg_with_exif_bytes(gps=SYDNEY_GPS)
        module = _load(s3)
        event = _event("shot.jpg", "image", "image/jpeg")
        event["extractGeoLocation"] = enabled
        response = module.lambda_handler(event, None)
        exif = s3.manifest(_AUX, _MANIFEST_KEY)["attributes"]["sys_image"]["exif"]
        assert exif["hasGps"] is True
        assert ("gps" in exif) is enabled
        if enabled:
            assert exif["gps"] == pytest.approx({"latitude": -33.865083, "longitude": 151.209944, "altitude": 12.5}, abs=1e-6)
        # The flag rides back on the state like every other hop field.
        assert response["extractGeoLocation"] is enabled

    def test_absent_extract_geo_location_is_the_template_default(self, s3):
        s3.objects[(_ASSETS, "a1/shot.jpg")] = jpeg_with_exif_bytes(gps=SYDNEY_GPS)
        module = _load(s3)
        event = _event("shot.jpg", "image", "image/jpeg")
        del event["extractGeoLocation"]
        module.lambda_handler(event, None)
        assert module.EXTRACT_GEO_LOCATION_DEFAULT is True
        assert "gps" in s3.manifest(_AUX, _MANIFEST_KEY)["attributes"]["sys_image"]["exif"]
        assert module.state_flag("false", True) is False and module.state_flag("TRUE", False) is True
        assert module.state_flag(None, False) is False and module.state_flag(3, True) is True

    def test_file_class_is_derived_from_the_extension_when_absent(self, s3):
        s3.objects[(_ASSETS, "a1/parts.csv")] = csv_bytes()
        module = _load(s3)
        event = _event("parts.csv", None, "text/csv")
        del event["fileClass"]
        response = module.lambda_handler(event, None)
        assert response["fileClass"] == "data"
        assert s3.manifest(_AUX, _MANIFEST_KEY)["attributes"]["sys_data"]["columns"] == ["name", "qty", "price"]

    def test_body_wrapped_string_payload_is_accepted(self, s3):
        s3.objects[(_ASSETS, "a1/parts.csv")] = csv_bytes()
        module = _load(s3)
        response = module.lambda_handler({"body": json.dumps(_event("parts.csv", "data"))}, None)
        assert response["fileClass"] == "data"

    def test_warnings_are_appended_to_the_pre_written_ones(self, s3):
        s3.objects[(_ASSETS, "a1/broken.png")] = b"not an image"
        module = _load(s3)
        response = module.lambda_handler(_event("broken.png", "image"), None)
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert manifest["warnings"][0] == "pre-existing" and "could not be decoded" in manifest["warnings"][1]
        assert manifest["renderSkipped"] == "error" and response["renderSkipped"] == "error"
        assert response["warningCount"] == 2

    def test_missing_manifest_raises_and_writes_nothing(self, s3):
        s3.objects.pop((_AUX, _MANIFEST_KEY))
        s3.objects[(_ASSETS, "a1/photo.png")] = png_bytes(8, 8)
        module = _load(s3)
        with pytest.raises(module.MediaTaskError, match="could not be read"):
            module.lambda_handler(_event("photo.png", "image"), None)
        assert s3.puts == [] and s3.downloads == []

    def test_class_outside_the_media_branch_is_rejected(self, s3):
        module = _load(s3)
        with pytest.raises(module.MediaTaskError, match="not served by the MEDIA branch"):
            module.lambda_handler(_event("model.glb", "mesh", "model/gltf-binary"), None)
        assert s3.puts == []

    @pytest.mark.parametrize("field", ["inputS3AssetFilePath", "analysisManifestS3Location"])
    @pytest.mark.parametrize("value", [None, "https://example.com/not-an-s3-uri"])
    def test_required_s3_locations(self, s3, field, value):
        module = _load(s3)
        event = _event("photo.png", "image")
        if value is None:
            del event[field]
        else:
            event[field] = value
        with pytest.raises(module.MediaTaskError, match=field):
            module.lambda_handler(event, None)
        assert s3.puts == [] and s3.downloads == []

    def test_extractor_failure_propagates_and_leaves_the_manifest_untouched(self, s3, monkeypatch):
        s3.objects[(_ASSETS, "a1/photo.png")] = png_bytes(8, 8)
        module = _load(s3)
        before = dict(s3.objects)
        created = []
        real_mkdtemp = module.tempfile.mkdtemp

        def recording_mkdtemp(*args, **kwargs):
            created.append(real_mkdtemp(*args, **kwargs))
            return created[-1]

        monkeypatch.setattr(module.tempfile, "mkdtemp", recording_mkdtemp)
        monkeypatch.setattr(module.images, "extract_image", lambda path, ctx: (_ for _ in ()).throw(RuntimeError("decoder crashed")))
        with pytest.raises(RuntimeError, match="decoder crashed"):
            module.lambda_handler(_event("photo.png", "image"), None)
        assert s3.objects == before
        assert created and not os.path.exists(created[0])

    def test_renders_land_beside_the_manifest(self, s3):
        # The aux bucket and prefix come from analysisManifestS3Location, not from any other state field.
        other_prefix = "pipelines/system-genai-metadata/E2/"
        s3.objects[(_AUX, other_prefix + "analysis.json")] = s3.objects[(_AUX, _MANIFEST_KEY)]
        s3.objects[(_ASSETS, "a1/photo.png")] = png_bytes(8, 8)
        module = _load(s3)
        event = _event("photo.png", "image")
        event["analysisManifestS3Location"] = f"s3://{_AUX}/{other_prefix}analysis.json"
        module.lambda_handler(event, None)
        assert s3.manifest(_AUX, other_prefix + "analysis.json")["renderImages"] == [other_prefix + "renders/media-01.png"]
        assert (_AUX, other_prefix + "renders/media-01.png", "image/png") in s3.puts
        assert (_AUX, _PREFIX + "renders/media-01.png", "image/png") not in s3.puts

    def test_unversioned_input_is_fetched_without_a_version_id(self, s3):
        s3.objects[(_ASSETS, "a1/photo.png")] = png_bytes(8, 8)
        module = _load(s3)
        event = _event("photo.png", "image")
        event["versionId"] = ""  # what openPipeline threads for an unversioned bucket
        module.lambda_handler(event, None)
        assert s3.downloads == [(_ASSETS, "a1/photo.png", None)]

    def test_max_text_chars_comes_from_the_input_configuration(self, s3):
        s3.objects[(_ASSETS, "a1/notes.txt")] = b"word " * 400
        s3.objects[(_RUN, _CONFIG_KEY)] = json.dumps({**_CONFIG_BODY, "maxTextChars": 50}).encode()
        module = _load(s3)
        module.lambda_handler(_event("notes.txt", "text", "text/plain"), None)
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert len(manifest["textExcerpt"]) <= 50 and manifest["warnings"] == ["pre-existing"]

    def test_unreadable_input_configuration_uses_the_default_with_a_warning(self, s3):
        s3.objects[(_ASSETS, "a1/notes.txt")] = b"word " * 400
        s3.objects.pop((_RUN, _CONFIG_KEY))
        module = _load(s3)
        module.lambda_handler(_event("notes.txt", "text", "text/plain"), None)
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        # 2,000 characters sit under the 12,000 default, so the excerpt is the whole text.
        assert len(manifest["textExcerpt"]) == len("word " * 400)
        assert manifest["warnings"][0] == "pre-existing" and "input configuration unreadable" in manifest["warnings"][1]

    def test_absent_input_configuration_location_uses_the_default_silently(self, s3):
        s3.objects[(_ASSETS, "a1/notes.txt")] = b"word " * 400
        module = _load(s3)
        event = _event("notes.txt", "text", "text/plain")
        del event["inputConfigurationS3Location"]
        module.lambda_handler(event, None)
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert len(manifest["textExcerpt"]) == len("word " * 400) and manifest["warnings"] == ["pre-existing"]


@pytest.mark.unit
class TestVideoSegmentPlan:
    def test_the_plan_and_the_bare_items_array_are_written_when_segments_are_on(self, s3, monkeypatch):
        s3.objects[(_ASSETS, "a1/clip.mp4")] = b"\x00"
        module = _load(s3)
        monkeypatch.setitem(module._EXTRACTORS, "video", _fake_video(92.48))
        response = module.lambda_handler(_video_event(), None)
        plan = s3.manifest(_AUX, _SEGMENT_PLAN_KEY)
        assert (plan["count"], plan["videoSegmentSeconds"], plan["effectiveIntervalSeconds"]) == (10, 10, 9.248)
        items = json.loads(s3.objects[(_AUX, _SEGMENT_ITEMS_KEY)])
        assert isinstance(items, list) and items == plan["segments"] and len(items) == 10
        assert items[0]["segmentKey"] == "t0000000000"
        assert set(items[0]) == {"segmentKey", "index", "startMs", "endMs", "label"}
        assert (_AUX, _SEGMENT_PLAN_KEY, "application/json") in s3.puts
        assert (_AUX, _SEGMENT_ITEMS_KEY, "application/json") in s3.puts
        assert response["videoSegmentPlanS3Location"] == f"s3://{_AUX}/{_SEGMENT_PLAN_KEY}"
        assert response["videoSegmentItemsS3Location"] == f"s3://{_AUX}/{_SEGMENT_ITEMS_KEY}"
        assert (response["videoSegmentItemsBucket"], response["videoSegmentItemsKey"]) == (_AUX, _SEGMENT_ITEMS_KEY)
        # The ResultWriter pair: the bucket NAME and a bucket-relative prefix -- an ASL cannot split a URI.
        assert (response["videoSegmentResultsBucket"], response["videoSegmentResultsPrefix"]) == (_AUX, _MAP_RESULTS_PREFIX)
        assert not response["videoSegmentResultsPrefix"].startswith("s3://")
        assert response["videoSegmentResultsPrefix"].endswith("/")
        assert set(_SEGMENT_FIELDS) <= set(response)
        assert response["videoSegmentCount"] == 10
        # The state, not the manifest, is the channel to the Map; every hop field still rides along.
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert not any(field in manifest for field in _SEGMENT_FIELDS) and manifest["fileClass"] == "video"
        assert response["assetId"] == "a1" and response["relativePath"] == "/clip.mp4"

    @pytest.mark.parametrize("over", [{"videoSegmentSeconds": 0}, {"vectorSearchEnabled": False}, {"videoSegmentSeconds": 1}])
    def test_no_plan_when_segments_are_off_vector_search_is_off_or_the_interval_is_below_the_minimum(
            self, s3, monkeypatch, over):
        s3.objects[(_ASSETS, "a1/clip.mp4")] = b"\x00"
        module = _load(s3)
        monkeypatch.setitem(module._EXTRACTORS, "video", _fake_video(92.48))
        response = module.lambda_handler(_video_event(**over), None)
        assert _puts_under(s3, _PREFIX + "segments/") == []
        assert not any(field in response for field in _SEGMENT_FIELDS)

    def test_a_short_video_produces_no_plan(self, s3, monkeypatch):
        s3.objects[(_ASSETS, "a1/clip.mp4")] = b"\x00"
        module = _load(s3)
        monkeypatch.setitem(module._EXTRACTORS, "video", _fake_video(15.0))
        response = module.lambda_handler(_video_event(), None)
        assert _puts_under(s3, _PREFIX + "segments/") == []
        assert not any(field in response for field in _SEGMENT_FIELDS) and response["fileClass"] == "video"

    def test_absent_segment_and_chunking_fields_take_the_template_defaults(self, s3, monkeypatch):
        s3.objects[(_ASSETS, "a1/clip.mp4")] = b"\x00"
        s3.objects[(_ASSETS, "a1/report.pdf")] = minimal_pdf_bytes(pages=2)
        module = _load(s3)
        monkeypatch.setitem(module._EXTRACTORS, "video", _fake_video(92.48))
        assert module.VIDEO_SEGMENT_SECONDS_DEFAULT == 0 and module.CONTENT_CHUNKING_DEFAULT is True
        # A video without the videoSegmentSeconds copy: the default is off, so no plan is written.
        event = _video_event()
        del event["videoSegmentSeconds"]
        del event["contentChunking"]
        response = module.lambda_handler(event, None)
        assert not any(field in response for field in _SEGMENT_FIELDS)
        assert _puts_under(s3, _PREFIX + "segments/") == []
        # A document without the contentChunking copy: the default is on, so its text is captured.
        event = _text_event("report.pdf", "document", "application/pdf")
        del event["contentChunking"]
        module.lambda_handler(event, None)
        assert (_AUX, _FULL_TEXT_KEY, "text/plain; charset=utf-8") in s3.puts
        assert s3.manifest(_AUX, _MANIFEST_KEY)["fullTextChars"] > 0
        assert module.state_flag(None, module.CONTENT_CHUNKING_DEFAULT) is True


@pytest.mark.unit
class TestFullTextCapture:
    def test_a_document_writes_its_full_text_and_page_offsets(self, s3):
        s3.objects[(_ASSETS, "a1/report.pdf")] = minimal_pdf_bytes(pages=3)
        module = _load(s3)
        response = module.lambda_handler(_text_event("report.pdf", "document", "application/pdf"), None)
        full = s3.objects[(_AUX, _FULL_TEXT_KEY)].decode("utf-8")
        assert "page 1" in full and "page 3" in full
        assert (_AUX, _FULL_TEXT_KEY, "text/plain; charset=utf-8") in s3.puts
        pages = json.loads(s3.objects[(_AUX, _PAGES_KEY)])
        assert [entry["page"] for entry in pages] == [1, 2, 3] and pages[0]["start"] == 0
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert manifest["fullTextS3Location"] == f"s3://{_AUX}/{_FULL_TEXT_KEY}"
        assert manifest["pageOffsetsS3Location"] == f"s3://{_AUX}/{_PAGES_KEY}"
        assert manifest["fullTextChars"] == len(full) and manifest["fullTextTruncated"] is False
        assert manifest["fullTextSkipped"] is None
        # The manifest, not the state, is the channel to generateEmbedding.
        assert not any(field in response for field in _TEXT_FIELDS) and response["fileClass"] == "document"

    @pytest.mark.parametrize("over", [{"contentChunking": False}, {"vectorSearchEnabled": False}])
    def test_no_capture_when_chunking_or_vector_search_is_off(self, s3, over):
        s3.objects[(_ASSETS, "a1/report.pdf")] = minimal_pdf_bytes(pages=2)
        module = _load(s3)
        module.lambda_handler(_text_event("report.pdf", "document", "application/pdf", **over), None)
        assert _puts_under(s3, _PREFIX + "text/") == []
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert not any(field in manifest for field in _TEXT_FIELDS)
        # The excerpt-bounded page scan is unchanged: at the default budget both pages are read.
        assert manifest["attributes"]["sys_document"]["pagesScannedForText"] == 2

    def test_the_full_text_is_cut_at_the_content_cap(self, s3, monkeypatch):
        s3.objects[(_ASSETS, "a1/notes.txt")] = b"word " * 100
        module = _load(s3)
        monkeypatch.setattr(module, "CONTENT_TEXT_MAX_CHARS", 100)
        module.lambda_handler(_text_event("notes.txt", "text", "text/plain"), None)
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert manifest["fullTextChars"] == 100 and manifest["fullTextTruncated"] is True
        assert len(s3.objects[(_AUX, _FULL_TEXT_KEY)]) == 100
        assert json.loads(s3.objects[(_AUX, _PAGES_KEY)]) == []

    def test_a_text_file_records_no_page_offsets(self, s3):
        s3.objects[(_ASSETS, "a1/notes.txt")] = b"word " * 100
        module = _load(s3)
        module.lambda_handler(_text_event("notes.txt", "text", "text/plain"), None)
        assert json.loads(s3.objects[(_AUX, _PAGES_KEY)]) == []
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert manifest["fullTextChars"] == 500 and manifest["fullTextTruncated"] is False

    def test_an_image_never_captures_text(self, s3):
        s3.objects[(_ASSETS, "a1/photo.png")] = png_bytes(8, 8)
        module = _load(s3)
        module.lambda_handler(_text_event("photo.png", "image", "image/png"), None)
        assert _puts_under(s3, _PREFIX + "text/") == []
        assert "fullTextS3Location" not in s3.manifest(_AUX, _MANIFEST_KEY)

    def test_a_geojson_data_file_captures_nothing(self, s3):
        # A GeoJSON .json is class data, but its text is coordinates: the extractor returns no full text.
        s3.objects[(_ASSETS, "a1/sites.json")] = json.dumps(geojson_point_dict()).encode()
        module = _load(s3)
        response = module.lambda_handler(_text_event("sites.json", "data", "application/geo+json"), None)
        assert response["fileClass"] == "data"
        assert _puts_under(s3, _PREFIX + "text/") == []
        assert "fullTextS3Location" not in s3.manifest(_AUX, _MANIFEST_KEY)

    def test_a_file_over_the_size_bound_is_not_content_embedded(self, s3):
        s3.objects[(_ASSETS, "a1/report.pdf")] = minimal_pdf_bytes(pages=2)
        module = _load(s3)
        bound = module.CONTENT_EMBED_MAX_FILE_BYTES
        response = module.lambda_handler(
            _text_event("report.pdf", "document", "application/pdf", fileSize=bound + 1), None)
        assert _puts_under(s3, _PREFIX + "text/") == []
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert manifest["fullTextSkipped"] == "size" and manifest["fullTextChars"] == 0
        assert "fullTextS3Location" not in manifest and "pageOffsetsS3Location" not in manifest
        assert "fullTextTruncated" not in manifest
        assert any(str(bound + 1) in warning and str(bound) in warning for warning in manifest["warnings"])
        assert manifest["warnings"][0] == "pre-existing"
        # The excerpt and the analysis are unchanged: only the content embedding is withheld.
        assert "page 1" in manifest["textExcerpt"]
        assert manifest["attributes"]["sys_document"]["pagesScannedForText"] == 2
        assert not any(field in response for field in _TEXT_FIELDS) and response["fileClass"] == "document"

    @pytest.mark.parametrize("offset", [-1, 0], ids=["one-under", "at-the-bound"])
    def test_a_file_within_the_size_bound_is_captured(self, s3, offset):
        # Positive control for the skip: the same event one byte under, and exactly at, the bound captures.
        s3.objects[(_ASSETS, "a1/report.pdf")] = minimal_pdf_bytes(pages=2)
        module = _load(s3)
        size = module.CONTENT_EMBED_MAX_FILE_BYTES + offset
        module.lambda_handler(_text_event("report.pdf", "document", "application/pdf", fileSize=size), None)
        assert (_AUX, _FULL_TEXT_KEY, "text/plain; charset=utf-8") in s3.puts
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert manifest["fullTextSkipped"] is None and manifest["fullTextChars"] > 0
        assert manifest["warnings"] == ["pre-existing"]


@pytest.mark.unit
class TestHelpers:
    def test_select_extractor_routes_the_office_formats(self):
        module = _load(FakeS3())
        assert module.select_extractor("document", ".docx") == (module.office.extract_docx, "document")
        assert module.select_extractor("document", ".pptx") == (module.office.extract_pptx, "document")
        assert module.select_extractor("data", ".xlsx") == (module.office.extract_xlsx, "data")
        assert module.select_extractor(None, ".XLSX") == (module.office.extract_xlsx, "data")
        assert module.select_extractor(None, ".docx") == (module.office.extract_docx, "document")

    def test_select_extractor_routes_every_media_class(self):
        module = _load(FakeS3())
        assert module.select_extractor("image", ".png") == (module.images.extract_image, "image")
        assert module.select_extractor("image", ".svg") == (module.svg.extract_svg, "image")
        assert module.select_extractor("video", ".mp4") == (module.video.extract_video, "video")
        assert module.select_extractor("audio", ".mp3") == (module.audio.extract_audio, "audio")
        assert module.select_extractor("document", ".pdf") == (module.documents.extract_pdf, "document")
        assert module.select_extractor("text", ".md") == (module.text.extract_text, "text")
        assert module.select_extractor("tiles3d", ".json") == (module.text.extract_text, "tiles3d")
        assert module.select_extractor("data", ".fcs") == (module.data.extract_data, "data")
        # A .json is sniffed whatever class the pipeline's classifier gave it (text / tiles3d / data for GeoJSON).
        assert module.select_extractor("text", ".json") == (module.text.extract_text, "text")
        assert module.select_extractor("data", ".json") == (module.text.extract_text, "data")
        assert module.select_extractor("data", ".csv") == (module.data.extract_data, "data")
        assert module.select_extractor(None, ".SVG") == (module.svg.extract_svg, "image")
        with pytest.raises(module.MediaTaskError):
            module.select_extractor(None, ".glb")

    def test_parse_s3_uri(self):
        module = _load(FakeS3())
        assert module.parse_s3_uri("s3://b/k/ey.json") == ("b", "k/ey.json")
        assert module.parse_s3_uri("s3://b") == ("b", "")
        assert module.parse_s3_uri("http://b/k") == ("", "")
        assert module.parse_s3_uri(None) == ("", "")

    def test_merge_manifest_preserves_and_overlays(self):
        module = _load(FakeS3())
        result = module.BranchResult(file_class="video", attributes={"sys_media": {"kind": "video"}},
                                     text_excerpt="", facts={"duration": "1 min"}, warnings=["w2"],
                                     render_skipped=None)
        merged = module.merge_manifest(_pre_manifest(warnings=("w1",)), result, ["p/renders/media-01.png"])
        assert merged["attributes"] == {"sys_file": _pre_manifest()["attributes"]["sys_file"], "sys_media": {"kind": "video"}}
        assert merged["facts"] == {"fileSize": "123 bytes", "duration": "1 min"}
        assert merged["warnings"] == ["w1", "w2"]
        assert merged["renderImages"] == ["p/renders/media-01.png"]
        assert merged["fileClass"] == "video" and merged["renderBranch"] == "MEDIA" and merged["schemaVersion"] == 1

    def test_render_prefix_for(self):
        module = _load(FakeS3())
        assert module.render_prefix_for(_MANIFEST_KEY) == _PREFIX
        assert module.render_prefix_for("analysis.json") == ""

    def test_load_max_text_chars(self):
        fake = FakeS3()
        module = _load(fake)
        assert module.load_max_text_chars(None) == (module.DEFAULT_MAX_TEXT_CHARS, [])
        assert module.load_max_text_chars("") == (module.DEFAULT_MAX_TEXT_CHARS, [])
        fake.objects[(_RUN, _CONFIG_KEY)] = json.dumps({**_CONFIG_BODY, "maxTextChars": 300}).encode()
        assert module.load_max_text_chars(_CONFIG_LOCATION) == (300, [])
        fake.objects[(_RUN, _CONFIG_KEY)] = json.dumps({**_CONFIG_BODY, "maxTextChars": "many"}).encode()
        value, warnings = module.load_max_text_chars(_CONFIG_LOCATION)
        assert value == module.DEFAULT_MAX_TEXT_CHARS and "not a positive integer" in warnings[0]
        fake.objects[(_RUN, _CONFIG_KEY)] = b"[1, 2]"
        assert module.load_max_text_chars(_CONFIG_LOCATION) == (
            module.DEFAULT_MAX_TEXT_CHARS, ["input configuration is not a JSON object; using the default maxTextChars"])
        fake.objects[(_RUN, _CONFIG_KEY)] = b"{not json"
        assert "input configuration unreadable (JSONDecodeError)" in module.load_max_text_chars(_CONFIG_LOCATION)[1][0]
        assert "input configuration unreadable (ClientError)" in module.load_max_text_chars(f"s3://{_RUN}/missing.json")[1][0]
        assert "not an s3:// URI" in module.load_max_text_chars("https://example.com/config.json")[1][0]
