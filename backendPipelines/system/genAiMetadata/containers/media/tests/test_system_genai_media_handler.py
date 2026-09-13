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
    SYDNEY_GPS, FakeS3, csv_bytes, geojson_point_dict, jpeg_with_exif_bytes, png_bytes, tileset_dict,
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
# The rendered template configBody (master §3.6 "Template configuration body"); the handler reads maxTextChars
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
    """The state constructPipeline returns (WP06d "The pipeline state"), trimmed to the hop fields a later
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
class TestHelpers:
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
