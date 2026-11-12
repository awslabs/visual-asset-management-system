#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every attribute value the pipeline writes fits the metadata value limit (#1355).

The asset metadata write-back refuses a value longer than MAX_METADATA_VALUE_LENGTH characters, and
with it the whole update, so a GLB with about a thousand placed parts, whose scene hierarchy alone
serializes past the limit, got no attributes at all. An oversize value is now shortened with a
``_truncated`` marker and the other values are written unchanged.
"""

import importlib.util
import json
import os
import re
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "common" not in sys.modules:
    _common_pkg = types.ModuleType("common")
    _common_logger = types.ModuleType("common.logger")
    _common_logger.safeLogger = lambda **kw: MagicMock()
    _common_pkg.logger = _common_logger
    sys.modules["common"] = _common_pkg
    sys.modules["common.logger"] = _common_logger
if "metadata_extractors" not in sys.modules:
    _extractors = types.ModuleType("metadata_extractors")
    _extractors.extract_cad_metadata = MagicMock(return_value={})
    _extractors.extract_mesh_metadata = MagicMock(return_value={"volume": 1.5})
    _extractors.get_handler_for_format = MagicMock(return_value="mesh")
    sys.modules["metadata_extractors"] = _extractors

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")

_REPO_ROOT = os.path.abspath(os.path.join(_LAMBDA_DIR, "..", "..", "..", ".."))
_BACKEND_METADATA_MODEL = os.path.join(_REPO_ROOT, "backend", "backend", "models", "metadata.py")
LIMIT = 400000


def _load():
    spec = importlib.util.spec_from_file_location(
        "meshcad_value_limit_lambda", os.path.join(_LAMBDA_DIR, "lambda.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
        spec.loader.exec_module(module)
    return module


def _scene_hierarchy(node_count):
    """The shape extract_scene_hierarchy returns for a scene of ``node_count`` placed parts."""
    return {
        "node_count": node_count + 1,
        "nodes": [{
            "name": f"assembly/subassembly_{i // 50}/part_{i:05d}",
            "geometry_name": f"part_{i:05d}_mesh",
            "transform": [[1.0, 0.0, 0.0, 0.123456789 * i], [0.0, 1.0, 0.0, -2.5],
                          [0.0, 0.0, 1.0, 1000.987654321], [0.0, 0.0, 0.0, 1.0]],
            "geometry_type": "Trimesh",
            "vertex_count": 1200 + i,
            "face_count": 2400 + i,
        } for i in range(node_count)],
    }


def _values(attribute_data):
    return {item["metadataKey"]: item["metadataValue"] for item in attribute_data["metadata"]}


@pytest.fixture(scope="module")
def mod():
    return _load()


@pytest.mark.unit
class TestMetadataValueLimit:
    def test_limit_matches_the_write_back_model(self, mod):
        with open(_BACKEND_METADATA_MODEL) as f:
            backend_limit = int(re.search(r"^MAX_METADATA_VALUE_LENGTH = (\d+)$", f.read(),
                                          re.MULTILINE).group(1))
        assert mod.MAX_METADATA_VALUE_LENGTH == backend_limit

    def test_large_scene_hierarchy_is_shortened_and_every_other_value_is_kept(self, mod):
        hierarchy = _scene_hierarchy(2000)
        assert len(json.dumps(hierarchy)) > LIMIT
        metadata = {
            "AB_geometric_metadata": {"dimensions": {"width": 1.0}, "units": "mm"},
            "AB_mesh_statistics": {"vertex_count": 2400000},
            "AB_format_specific": {"has_draco_compression": False},
            "AB_visual_metadata": {"materials": [], "textures": []},
            "AB_scene_hierarchy": hierarchy,
        }

        values = _values(mod.transform_to_attribute_format(metadata))

        assert list(values) == list(metadata)
        for key in ("AB_geometric_metadata", "AB_mesh_statistics", "AB_format_specific",
                    "AB_visual_metadata"):
            assert values[key] == json.dumps(metadata[key])
        written = values["AB_scene_hierarchy"]
        assert len(written) <= LIMIT
        fitted = json.loads(written)
        shown = fitted["_truncated"]["fields"]["nodes"]["shown"]
        assert fitted["_truncated"] == {
            "limit": LIMIT, "fields": {"nodes": {"shown": shown, "total": 2000}}}
        assert 0 < shown < 2000
        assert fitted["nodes"] == hierarchy["nodes"][:shown]
        assert fitted["node_count"] == 2001
        # The largest prefix that fits: one more node would not.
        one_more = dict(fitted, nodes=hierarchy["nodes"][:shown + 1])
        one_more["_truncated"] = {"limit": LIMIT,
                                  "fields": {"nodes": {"shown": shown + 1, "total": 2000}}}
        assert len(json.dumps(one_more)) > LIMIT

    def test_long_list_value_keeps_its_first_items_and_ends_with_a_marker(self, mod):
        components = [{"name": f"component_{i:05d}", "volume": i * 1.5, "solid_count": 1}
                      for i in range(9000)]
        assert len(json.dumps(components)) > LIMIT

        written = _values(mod.transform_to_attribute_format(
            {"assembly_hierarchy": components}))["assembly_hierarchy"]

        assert len(written) <= LIMIT
        fitted = json.loads(written)
        marker = fitted[-1]["_truncated"]
        assert marker["limit"] == LIMIT and marker["total"] == 9000
        assert 0 < marker["shown"] < 9000
        assert fitted[:-1] == components[:marker["shown"]]

    def test_dict_without_a_list_to_cut_is_replaced_by_its_marker(self, mod):
        custom = {f"property_{i:06d}": "x" * 40 for i in range(10000)}
        original_length = len(json.dumps(custom))
        assert original_length > LIMIT

        written = _values(mod.transform_to_attribute_format(
            {"custom_metadata": custom}))["custom_metadata"]

        assert json.loads(written) == {
            "_truncated": {"limit": LIMIT, "originalLength": original_length}}

    def test_long_text_value_is_cut_with_a_note(self, mod):
        written = _values(mod.transform_to_attribute_format(
            {"comments": "a" * (LIMIT + 10)}))["comments"]

        assert len(written) <= LIMIT
        assert written.startswith("a" * 1000)
        assert written.endswith(f" [truncated: {LIMIT + 10} characters, limit {LIMIT}]")

    def test_value_at_the_limit_is_written_unchanged(self, mod):
        value = "b" * LIMIT
        assert _values(mod.transform_to_attribute_format({"comments": value}))["comments"] == value

    def test_extraction_uploads_every_attribute_within_the_limit(self, mod, tmp_path):
        metadata = {"AB_mesh_statistics": {"vertex_count": 10},
                    "AB_scene_hierarchy": _scene_hierarchy(2000)}
        uploaded = {}

        def fake_download(bucket, key, file_path, version_id=""):
            with open(file_path, "wb") as f:
                f.write(b"glb")
            return file_path

        def fake_upload(bucket, key, file_path):
            with open(file_path) as f:
                uploaded[key] = json.load(f)
            return key

        with patch.object(mod, "download", side_effect=fake_download), \
                patch.object(mod, "upload", side_effect=fake_upload), \
                patch.object(mod, "get_handler_for_format", return_value="mesh"), \
                patch.object(mod, "check_input_text"), \
                patch.object(mod, "extract_mesh_metadata", return_value=metadata):
            result = mod.extract_metadata(
                "parts/assembly.glb", "s3://abkt/asset/parts/assembly.glb",
                "s3://obkt/pipelines/metaExtract/JOB/output/E1/metadata/")

        assert result["statusCode"] == 200
        (attribute_file,) = uploaded.values()
        written = _values(attribute_file)
        assert list(written) == ["AB_mesh_statistics", "AB_scene_hierarchy"]
        assert all(len(value) <= LIMIT for value in written.values())
        assert json.loads(written["AB_scene_hierarchy"])["_truncated"]["fields"]["nodes"]["total"] == 2000
