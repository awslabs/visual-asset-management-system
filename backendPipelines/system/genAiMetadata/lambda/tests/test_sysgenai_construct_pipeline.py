#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""constructPipeline turns the sub-state-machine input into the pipeline state: it classifies the file,
applies the Lambda limits, describes the object as ``sys_file`` and pre-writes the analysis manifest
so every later state — including a render task that crashes — finds one at the manifest location."""

import hashlib
import json
import struct
from unittest.mock import MagicMock

import pytest

import sysgenai_harness as h

AUX_URI = "s3://aux/pipelines/system-genai-metadata/E1/"
MANIFEST_KEY = "pipelines/system-genai-metadata/E1/analysis.json"
CONFIG_KEY = "pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
DEFAULT_CONFIG = {"seedWithExistingMetadata": True, "includeSiblingFiles": True, "renderViews": 8,
                  "maxTextChars": 12000, "writeAssetKeywords": False, "embeddingIncludeTextExcerpt": True,
                  "writeExtractedMetadata": True, "extractGeoLocation": True,
                  "classificationVocabulary": {"categories": {}, "styles": [], "materials": [], "colors": [],
                                               "allowUnlisted": True}}


def _state(**over):
    state = {
        "jobName": "PipelineJob_20260908_101010_123_abcdef01",
        "externalSfnTaskToken": h.TASK_TOKEN,
        "inputS3AssetFilePath": "s3://abkt/xidM/models/pump.glb",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/sgm/sgm/output/E1/files/",
        "outputS3AssetPreviewPath": "s3://abkt/pipelines/sgm/sgm/output/E1/previews/",
        "outputS3AssetMetadataPath": "s3://abkt/pipelines/sgm/sgm/output/E1/metadata/",
        "outputS3AssetResultsPath": "s3://abkt/pipelines/sgm/sgm/output/E1/results/",
        "inputOutputS3AssetAuxiliaryFilesPath": AUX_URI,
        "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
        "inputConfigurationS3Location": f"s3://abkt/{CONFIG_KEY}",
        "assetId": "xidM",
        "databaseId": "dbM",
        "bucketId": "bkt-01",
        "relativePath": "/models/pump.glb",
        "versionId": "v1",
        "workflowExecutionId": "E1",
        "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
    }
    state.update(over)
    return state


def _ply(*lines):
    return ("ply\nformat binary_little_endian 1.0\n" + "\n".join(lines) + "\nend_header\n").encode("ascii")


def _las(point_count, minor=2):
    header = bytearray(375)
    header[0:4] = b"LASF"
    header[24] = 1
    header[25] = minor
    struct.pack_into("<I", header, 107, point_count)
    return bytes(header)


def _run(state, s3, env=None):
    mod = h.load_handler("constructPipeline", env)
    mod.s3_client = s3
    return mod, mod.lambda_handler(state, MagicMock())


def _s3_with(key, content, content_type="application/octet-stream", config=None, **head_over):
    """An input object with a scripted HeadObject and, unless ``config=False``, the rendered template
    configuration at the state's ``inputConfigurationS3Location``."""
    s3 = h.FakeS3({("abkt", key): content})
    head = {"ContentLength": len(content), "ETag": '"abc123"', "ContentType": content_type, "VersionId": "v1"}
    head.update(head_over)
    s3.heads[("abkt", key)] = head
    if config is not False:
        s3.put_json("abkt", CONFIG_KEY, DEFAULT_CONFIG if config is None else config)
    return s3


def _input_gets(s3, key):
    """The GetObject calls made against the INPUT object (the configuration read is a different key)."""
    return [(bucket, k, kwargs) for bucket, k, kwargs in s3.gets if (bucket, k) == ("abkt", key)]


@pytest.mark.unit
class TestMeshFile:
    def test_state_and_manifest_for_a_blender_mesh(self):
        content = b"glTF" + bytes(range(256)) * 4
        s3 = _s3_with("xidM/models/pump.glb", content, "model/gltf-binary")
        mod, state = _run(_state(), s3)

        assert state["fileClass"] == "mesh"
        assert state["renderBranch"] == "BLENDER"
        assert state["renderSkipped"] is None
        assert state["fileExt"] == ".glb"
        assert state["etag"] == "abc123"
        assert state["fileSize"] == len(content)
        assert state["contentType"] == "model/gltf-binary"
        assert state["versionId"] == "v1"
        assert state["pipelineExecutionId"] == "P1"
        assert state["vectorSearchEnabled"] is True
        assert state["status"] == "STARTING"
        assert state["analysisManifestS3Location"] == f"s3://aux/{MANIFEST_KEY}"
        # The split locations and the template values the branch handlers read from their event.
        assert (state["inputBucket"], state["inputKey"]) == ("abkt", "xidM/models/pump.glb")
        assert (state["auxBucket"], state["auxTempPrefix"]) == ("aux", "pipelines/system-genai-metadata/E1/")
        assert (state["renderViews"], state["maxTextChars"], state["includeSiblingFiles"]) == (8, 12000, True)
        assert state["extractGeoLocation"] is True
        assert state["maxPointCloudPoints"] == 20_000_000 and state["render"] is True
        # Every input field survives.
        for key, value in _state().items():
            assert state[key] == value, key

        assert s3.puts == [("aux", MANIFEST_KEY)]
        manifest = s3.json_at("aux", MANIFEST_KEY)
        assert manifest["schemaVersion"] == 1
        assert manifest["fileClass"] == "mesh" and manifest["renderBranch"] == "BLENDER"
        assert manifest["renderImages"] == [] and manifest["textExcerpt"] == ""
        assert manifest["facts"] == {} and manifest["warnings"] == []
        assert manifest["renderSkipped"] is None
        assert list(manifest["attributes"]) == ["sys_file"]
        assert manifest["attributes"]["sys_file"] == {
            "name": "pump.glb", "ext": ".glb", "sizeBytes": len(content),
            "contentType": "model/gltf-binary", "etag": "abc123", "versionId": "v1",
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    def test_head_object_pins_the_manifest_version(self):
        s3 = _s3_with("xidM/models/pump.glb", b"glTF....")
        recorded = MagicMock(side_effect=s3.head_object)
        s3.head_object = recorded
        _run(_state(versionId="v1"), s3)
        assert recorded.call_args.kwargs == {"Bucket": "abkt", "Key": "xidM/models/pump.glb", "VersionId": "v1"}

    def test_an_unversioned_manifest_takes_the_current_version(self):
        s3 = _s3_with("xidM/models/pump.glb", b"glTF....", VersionId="current-9")
        recorded = MagicMock(side_effect=s3.head_object)
        s3.head_object = recorded
        _mod, state = _run(_state(versionId=""), s3)
        assert "VersionId" not in recorded.call_args.kwargs
        assert state["versionId"] == "current-9"
        assert s3.json_at("aux", MANIFEST_KEY)["attributes"]["sys_file"]["versionId"] == "current-9"

    def test_table_decided_extensions_are_not_sniffed(self):
        s3 = _s3_with("xidM/models/pump.glb", b"glTF....")
        _run(_state(), s3)
        assert not any(kwargs.get("Range") for _b, _k, kwargs in s3.gets)


@pytest.mark.unit
class TestInputConfiguration:
    def test_the_template_values_are_copied_into_the_state(self):
        """WP06a reads renderViews/maxPointCloudPoints and WP06c reads maxTextChars from their EVENT,
        and an ASL cannot read S3, so the operator's template values reach a render task only through
        the state constructPipeline returns."""
        s3 = _s3_with("xidM/models/pump.glb", b"glTF....",
                      config=dict(DEFAULT_CONFIG, renderViews=4, maxTextChars=500, includeSiblingFiles=False))
        _mod, state = _run(_state(), s3, {"MAX_POINT_CLOUD_POINTS": "5000000"})
        assert (state["renderViews"], state["maxTextChars"], state["includeSiblingFiles"]) == (4, 500, False)
        assert state["maxPointCloudPoints"] == 5_000_000
        assert state["render"] is True

    def test_a_missing_configuration_yields_the_template_defaults(self):
        s3 = _s3_with("xidM/models/pump.glb", b"glTF....", config=False)
        _mod, state = _run(_state(), s3)
        assert (state["renderViews"], state["maxTextChars"], state["includeSiblingFiles"]) == (8, 12000, True)
        assert state["extractGeoLocation"] is True

    def test_extract_geo_location_is_copied_for_the_media_branch(self):
        """WP06c's image extractor reads extractGeoLocation from its EVENT to decide whether
        sys_image.exif.gps is written (registry §3.6), so the template's EXTRACT_GEO_LOCATION reaches
        it only through this copy."""
        s3 = _s3_with("xidM/photos/site.jpg", b"\xff\xd8\xff\xe1" + b"\x00" * 64, "image/jpeg",
                      config=dict(DEFAULT_CONFIG, extractGeoLocation=False))
        _mod, state = _run(_state(inputS3AssetFilePath="s3://abkt/xidM/photos/site.jpg",
                                  relativePath="/photos/site.jpg"), s3)
        assert state["extractGeoLocation"] is False
        assert (state["fileClass"], state["renderBranch"]) == ("image", "MEDIA")
        s3 = _s3_with("xidM/photos/site.jpg", b"\xff\xd8\xff\xe1" + b"\x00" * 64, "image/jpeg",
                      config=dict(DEFAULT_CONFIG, extractGeoLocation="false"))
        _mod, state = _run(_state(inputS3AssetFilePath="s3://abkt/xidM/photos/site.jpg"), s3)
        assert state["extractGeoLocation"] is False  # a string rendering of the tag is read the same way


@pytest.mark.unit
class TestNoneBranch:
    def test_proprietary_cad_is_attributes_only(self):
        s3 = _s3_with("xidM/models/bracket.sldprt", b"\x00" * 64)
        _mod, state = _run(_state(inputS3AssetFilePath="s3://abkt/xidM/models/bracket.sldprt",
                                  relativePath="/models/bracket.sldprt"), s3)
        assert (state["fileClass"], state["renderBranch"], state["renderSkipped"]) == ("cad", "NONE", "unsupported")
        manifest = s3.json_at("aux", MANIFEST_KEY)
        assert manifest["renderSkipped"] == "unsupported"
        assert list(manifest["attributes"]) == ["sys_file"]

    def test_an_unknown_extension_is_other(self):
        s3 = _s3_with("xidM/models/thing.zzz", b"\x00" * 64)
        _mod, state = _run(_state(inputS3AssetFilePath="s3://abkt/xidM/models/thing.zzz"), s3)
        assert (state["fileClass"], state["renderBranch"], state["renderSkipped"]) == ("other", "NONE", "unsupported")
        assert state["fileExt"] == ".zzz"

    def test_a_file_without_extension_reports_none(self):
        s3 = _s3_with("xidM/models/README", b"hello")
        _mod, state = _run(_state(inputS3AssetFilePath="s3://abkt/xidM/models/README"), s3)
        assert state["fileExt"] == "none"
        assert state["fileClass"] == "other"


@pytest.mark.unit
class TestSniffedExtensions:
    def test_a_ply_is_classified_from_one_header_read(self):
        content = _ply("element vertex 8", "property float x", "element face 12")
        s3 = _s3_with("xidM/models/scan.ply", content)
        mod, state = _run(_state(inputS3AssetFilePath="s3://abkt/xidM/models/scan.ply"), s3)
        assert (state["fileClass"], state["renderBranch"]) == ("mesh", "BLENDER")
        ranges = [kwargs["Range"] for _b, _k, kwargs in s3.gets if kwargs.get("Range")]
        assert ranges == [f"bytes=0-{mod.fileClassifier.SNIFF_BYTES - 1}"]

    def test_a_splat_ply_goes_to_render3d(self):
        content = _ply("element vertex 5000", "property float x", "property float f_dc_0")
        s3 = _s3_with("xidM/models/scene.ply", content)
        _mod, state = _run(_state(inputS3AssetFilePath="s3://abkt/xidM/models/scene.ply"), s3)
        assert (state["fileClass"], state["renderBranch"]) == ("splat", "RENDER3D")

    def test_a_tileset_json_is_tiles3d(self):
        content = json.dumps({"asset": {"version": "1.1"}, "geometricError": 100, "root": {}}).encode()
        s3 = _s3_with("xidM/tiles/tileset.json", content, "application/json")
        _mod, state = _run(_state(inputS3AssetFilePath="s3://abkt/xidM/tiles/tileset.json"), s3)
        assert (state["fileClass"], state["renderBranch"]) == ("tiles3d", "MEDIA")

    def test_an_unreadable_header_classifies_as_other(self):
        s3 = h.FakeS3()
        s3.heads[("abkt", "xidM/models/scan.ply")] = {"ContentLength": 10, "ETag": '"e"',
                                                       "ContentType": "application/octet-stream", "VersionId": "v1"}
        _mod, state = _run(_state(inputS3AssetFilePath="s3://abkt/xidM/models/scan.ply"), s3)
        assert (state["fileClass"], state["renderBranch"], state["renderSkipped"]) == ("other", "NONE", "unsupported")


@pytest.mark.unit
class TestSizeGate:
    def test_an_oversized_file_skips_the_render(self):
        s3 = _s3_with("xidM/models/pump.glb", b"glTF....", ContentLength=3 * 1024 * 1024)
        _mod, state = _run(_state(), s3, {"MAX_INPUT_FILE_SIZE_MB": "2"})
        assert (state["fileClass"], state["renderBranch"], state["renderSkipped"]) == ("mesh", "NONE", "size")
        manifest = s3.json_at("aux", MANIFEST_KEY)
        assert manifest["renderSkipped"] == "size"
        assert manifest["renderBranch"] == "NONE"
        assert any("size" in warning for warning in manifest["warnings"])

    def test_the_fargate_renderer_takes_the_oversized_file(self):
        s3 = _s3_with("xidM/models/pump.glb", b"glTF....", ContentLength=3 * 1024 * 1024)
        _mod, state = _run(_state(), s3, {"MAX_INPUT_FILE_SIZE_MB": "2", "USE_FARGATE_RENDERER": "true"})
        assert (state["renderBranch"], state["renderSkipped"]) == ("FARGATE", None)
        assert s3.json_at("aux", MANIFEST_KEY)["renderBranch"] == "FARGATE"

    def test_a_file_at_the_limit_renders(self):
        s3 = _s3_with("xidM/models/pump.glb", b"glTF....", ContentLength=2 * 1024 * 1024)
        _mod, state = _run(_state(), s3, {"MAX_INPUT_FILE_SIZE_MB": "2"})
        assert (state["renderBranch"], state["renderSkipped"]) == ("BLENDER", None)

    def test_the_point_cap_reads_the_las_header(self):
        s3 = _s3_with("xidM/scans/site.las", _las(30_000_000))
        _mod, state = _run(_state(inputS3AssetFilePath="s3://abkt/xidM/scans/site.las"), s3,
                           {"MAX_POINT_CLOUD_POINTS": "20000000"})
        assert (state["fileClass"], state["renderBranch"], state["renderSkipped"]) == ("pointcloud", "NONE", "size")

    def test_a_point_cloud_under_the_cap_renders(self):
        s3 = _s3_with("xidM/scans/site.las", _las(1_000_000))
        _mod, state = _run(_state(inputS3AssetFilePath="s3://abkt/xidM/scans/site.las"), s3)
        assert (state["renderBranch"], state["renderSkipped"]) == ("RENDER3D", None)

    def test_a_point_cloud_without_a_header_count_renders(self):
        s3 = _s3_with("xidM/scans/site.e57", b"ASTM-E57 3D Imaging Data File" + b"\x00" * 64)
        _mod, state = _run(_state(inputS3AssetFilePath="s3://abkt/xidM/scans/site.e57"), s3)
        assert (state["fileClass"], state["renderBranch"], state["renderSkipped"]) == ("pointcloud", "RENDER3D", None)

    def test_the_point_cap_does_not_apply_to_a_mesh_ply(self):
        content = _ply("element vertex 99000000", "element face 3")
        s3 = _s3_with("xidM/models/big.ply", content)
        _mod, state = _run(_state(inputS3AssetFilePath="s3://abkt/xidM/models/big.ply"), s3,
                           {"MAX_POINT_CLOUD_POINTS": "20000000"})
        assert (state["fileClass"], state["renderBranch"], state["renderSkipped"]) == ("mesh", "BLENDER", None)

    def test_a_none_branch_is_never_relabelled_size(self):
        s3 = _s3_with("xidM/models/bracket.sldprt", b"\x00" * 64, ContentLength=5 * 1024 * 1024)
        _mod, state = _run(_state(inputS3AssetFilePath="s3://abkt/xidM/models/bracket.sldprt"), s3,
                           {"MAX_INPUT_FILE_SIZE_MB": "2", "USE_FARGATE_RENDERER": "true"})
        assert (state["renderBranch"], state["renderSkipped"]) == ("NONE", "unsupported")


@pytest.mark.unit
class TestSysFile:
    def test_sha256_is_omitted_above_the_streaming_limit(self):
        s3 = _s3_with("xidM/models/pump.glb", b"glTF....", ContentLength=600 * 1024 * 1024)
        _mod, state = _run(_state(), s3)
        sys_file = s3.json_at("aux", MANIFEST_KEY)["attributes"]["sys_file"]
        assert "sha256" not in sys_file
        assert sys_file["sizeBytes"] == 600 * 1024 * 1024
        # A table-decided extension is never sniffed and the digest was skipped, so the input object is
        # not read at all. (``all([])`` would be True whatever the handler did; the list is asserted.)
        assert _input_gets(s3, "xidM/models/pump.glb") == []

    def test_a_sniffed_file_above_the_limit_is_read_once_by_range(self):
        content = _ply("element vertex 8", "property float x", "element face 12")
        s3 = _s3_with("xidM/models/scan.ply", content, ContentLength=600 * 1024 * 1024)
        mod, _state_out = _run(_state(inputS3AssetFilePath="s3://abkt/xidM/models/scan.ply"), s3)
        assert "sha256" not in s3.json_at("aux", MANIFEST_KEY)["attributes"]["sys_file"]
        # Exactly one ranged header read and no unranged (whole-object) read.
        assert [kwargs.get("Range") for _b, _k, kwargs in _input_gets(s3, "xidM/models/scan.ply")] == [
            f"bytes=0-{mod.fileClassifier.SNIFF_BYTES - 1}"]

    def test_sha256_is_streamed_for_a_file_within_the_limit(self):
        content = bytes(range(256)) * 1000
        s3 = _s3_with("xidM/models/pump.glb", content)
        _run(_state(), s3)
        sys_file = s3.json_at("aux", MANIFEST_KEY)["attributes"]["sys_file"]
        assert sys_file["sha256"] == hashlib.sha256(content).hexdigest()

    def test_vector_search_flag_follows_the_environment(self):
        s3 = _s3_with("xidM/models/pump.glb", b"glTF....")
        _mod, state = _run(_state(), s3, {"VECTOR_SEARCH_ENABLED": "false"})
        assert state["vectorSearchEnabled"] is False
