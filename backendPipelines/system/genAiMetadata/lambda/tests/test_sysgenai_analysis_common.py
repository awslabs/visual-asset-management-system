#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The results-channel constants match the backend consumer, and the shared helpers behave.

``execution.status.json`` is a reserved results file name the process-output step reads to record an
execution FAILED. The pipeline cannot import the backend constant, so the two are pinned equal by
reading the backend module by path — a rename on either side fails here rather than as a run that
reports SUCCEEDED with attributes and no metadata.

The existing-metadata helpers turn the execution envelope's four scopes into the ``key: value`` lines
both analysis Lambdas consume — the prompt sections and the embedding text — so every rule (scope
order, key sort, blank and pipeline-owned keys skipped, whitespace, the GeoJSON rendering, the value
cut, the whole-line budget) is pinned once here; the GeoJSON type names are pinned equal to the
classifier's set the same way the status file name is pinned to the backend constant."""

import importlib.util
import json
import os

import pytest

import sysgenai_harness as h

common = h.load_local("analysisCommon")
fc = h.load_local("fileClassifier")

_S3_PATH_PATTERNS = os.path.join(h.REPO_ROOT, "backend", "backend", "common", "s3PathPatterns.py")


def _backend_s3_path_patterns():
    spec = importlib.util.spec_from_file_location("sysgenai_backend_s3PathPatterns", _S3_PATH_PATTERNS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestReservedNames:
    def test_status_file_name_matches_the_backend_constant(self):
        backend = _backend_s3_path_patterns()
        assert hasattr(backend, "EXECUTION_STATUS_RESULTS_FILENAME"), (
            "backend/backend/common/s3PathPatterns.py has no EXECUTION_STATUS_RESULTS_FILENAME: WP05 "
            "has not landed, so nothing would read the status file this pipeline writes")
        assert common.EXECUTION_STATUS_RESULTS_FILENAME == backend.EXECUTION_STATUS_RESULTS_FILENAME
        assert common.EXECUTION_STATUS_RESULTS_FILENAME == "execution.status.json"

    def test_other_names_and_codes(self):
        assert common.ANALYSIS_SUMMARY_RESULTS_FILENAME == "analysis-summary.json"
        assert common.ANALYSIS_MANIFEST_FILENAME == "analysis.json"
        assert common.ANALYSIS_MANIFEST_SCHEMA_VERSION == 1
        assert (common.ERROR_BEDROCK_ACCESS_DENIED, common.ERROR_BEDROCK_MODEL,
                common.ERROR_BEDROCK_THROTTLED, common.ERROR_BEDROCK_EMBEDDING,
                common.ERROR_BEDROCK_GUARDRAIL_INTERVENED) == (
            "BedrockAccessDenied", "BedrockModelError", "BedrockThrottled", "BedrockEmbeddingError",
            "BedrockGuardrailIntervened")


@pytest.mark.unit
class TestS3Helpers:
    def test_join_key_uses_exactly_one_slash(self):
        assert common.join_key("pipelines/x/E1/", "analysis.json") == "pipelines/x/E1/analysis.json"
        assert common.join_key("pipelines/x/E1", "analysis.json") == "pipelines/x/E1/analysis.json"
        assert common.join_key("pipelines/x/E1/", "/analysis.json") == "pipelines/x/E1/analysis.json"
        assert common.join_key("", "analysis.json") == "analysis.json"

    def test_uri_join(self):
        assert common.uri_join("s3://aux/pipelines/x/E1/", "analysis.json") == \
            "s3://aux/pipelines/x/E1/analysis.json"

    def test_write_then_read_json_round_trips(self):
        s3 = h.FakeS3()
        uri = common.write_json(s3, "s3://aux/p/analysis.json", {"a": 1, "b": [1, 2]})
        assert uri == "s3://aux/p/analysis.json"
        assert s3.puts == [("aux", "p/analysis.json")]
        assert common.read_json(s3, uri) == {"a": 1, "b": [1, 2]}

    def test_read_json_rejects_a_non_object(self):
        s3 = h.FakeS3({("aux", "p/list.json"): b"[1, 2]"})
        with pytest.raises(ValueError):
            common.read_json(s3, "s3://aux/p/list.json")


@pytest.mark.unit
class TestExecutionStatus:
    def test_status_file_shape_and_location(self):
        s3 = h.FakeS3()
        uri = common.write_execution_status(
            s3, "s3://abkt/pipelines/p/j/output/E1/results/", common.ERROR_BEDROCK_ACCESS_DENIED,
            "AccessDeniedException: no model access")
        assert uri == "s3://abkt/pipelines/p/j/output/E1/results/execution.status.json"
        body = s3.json_at("abkt", "pipelines/p/j/output/E1/results/execution.status.json")
        assert body == {"status": "FAILED", "error": "BedrockAccessDenied",
                        "cause": "AccessDeniedException: no model access"}

    def test_cause_is_truncated_to_the_limit(self):
        s3 = h.FakeS3()
        common.write_execution_status(s3, "s3://abkt/results/", common.ERROR_BEDROCK_MODEL, "x" * 5000)
        body = s3.json_at("abkt", "results/execution.status.json")
        assert len(body["cause"]) == common.STATUS_CAUSE_MAX_CHARS == 1024


@pytest.mark.unit
class TestBedrockErrorCode:
    @pytest.mark.parametrize("code,message,expected", [
        ("AccessDeniedException", "not authorized", "BedrockAccessDenied"),
        ("ValidationException", "Your account is not authorized to invoke this API operation. "
                                "FTUFormNotFilled", "BedrockAccessDenied"),
        ("ThrottlingException", "Too many requests", "BedrockThrottled"),
        ("ServiceUnavailableException", "", "BedrockThrottled"),
        ("ModelNotReadyException", "", "BedrockThrottled"),
        ("ValidationException", "Input is too long for requested model.", "BedrockModelError"),
        ("ResourceNotFoundException", "Could not resolve the foundation model", "BedrockModelError"),
        ("ModelErrorException", "", "BedrockModelError"),
    ])
    def test_mapping(self, code, message, expected):
        assert common.bedrock_error_code(h.client_error(code, message)) == expected

    def test_a_non_client_error_is_a_model_error(self):
        assert common.bedrock_error_code(RuntimeError("boom")) == "BedrockModelError"


@pytest.mark.unit
class TestCoercions:
    @pytest.mark.parametrize("value,default,expected", [
        (True, False, True), (False, True, False), ("true", False, True), ("False", True, False),
        ("1", False, True), ("0", True, False), (None, True, True), ("maybe", False, False),
    ])
    def test_as_bool(self, value, default, expected):
        assert common.as_bool(value, default) is expected

    def test_as_int(self):
        assert common.as_int(8, 4) == 8
        assert common.as_int("12000", 4) == 12000
        assert common.as_int("lots", 4) == 4
        assert common.as_int(None, 4) == 4


@pytest.mark.unit
class TestAnalysisManifest:
    def test_new_manifest_shape(self):
        sys_file = {"name": "pump.glb", "ext": ".glb", "sizeBytes": 10, "contentType": "model/gltf-binary",
                    "etag": "e", "versionId": "v1"}
        manifest = common.new_analysis_manifest("mesh", "BLENDER", sys_file)
        assert manifest == {
            "schemaVersion": 1, "fileClass": "mesh", "renderBranch": "BLENDER",
            "attributes": {"sys_file": sys_file}, "renderImages": [], "textExcerpt": "",
            "facts": {}, "warnings": [], "renderSkipped": None,
        }
        assert list(manifest) == ["schemaVersion", "fileClass", "renderBranch", "attributes",
                                  "renderImages", "textExcerpt", "facts", "warnings", "renderSkipped"]

    def test_render_skipped_is_recorded(self):
        manifest = common.new_analysis_manifest("cad", "NONE", {}, common.RENDER_SKIPPED_UNSUPPORTED)
        assert manifest["renderSkipped"] == "unsupported"


def _view(file_metadata=None, asset_metadata=None, database_metadata=None, file_attributes=None):
    """A legacy view as to_legacy_vams_view projects it from a v2 envelope: every value a string."""
    return {"VAMS": {
        "assetData": {"assetName": "Gear Pump"},
        "assetMetadata": asset_metadata if asset_metadata is not None else {},
        "fileMetadata": file_metadata if file_metadata is not None else {},
        "fileAttributes": file_attributes if file_attributes is not None else {},
        "databaseMetadata": database_metadata if database_metadata is not None else {},
    }}


@pytest.mark.unit
class TestExistingMetadata:
    def test_constants_match_the_registry(self):
        assert common.EXISTING_METADATA_SCOPES == ("fileMetadata", "assetMetadata", "databaseMetadata",
                                                   "fileAttributes")
        assert common.EXISTING_METADATA_EXCLUDED_PREFIXES == ("ext_", "genai_", "sys_")
        assert common.EXISTING_METADATA_MAX_CHARS == 12000
        assert common.EXISTING_VALUE_MAX_CHARS == 400

    def test_geojson_type_names_match_the_classifier(self):
        """Restated, not imported (this module stays a leaf); the classifier's set is the one WP06c's
        text extractor consults too, so the copies must name the same nine types."""
        assert common.GEOJSON_TYPE_NAMES == fc.GEOJSON_ROOT_TYPES
        assert len(common.GEOJSON_TYPE_NAMES) == 9

    def test_an_ordinary_pair_passes_through_unchanged(self):
        # Positive control for every skip/cut rule below: a plain key and value are one line, nothing dropped.
        assert common.existing_metadata_lines(_view(file_metadata={"PART_NO": "GP-100"})) == (
            [("fileMetadata", "PART_NO: GP-100")], 0)

    def test_scopes_contribute_file_then_asset_then_database_then_attributes(self):
        kept, dropped = common.existing_metadata_lines(_view(
            file_metadata={"PART_NO": "GP-100"}, asset_metadata={"PROJECT": "Alpha"},
            database_metadata={"SITE": "Plant 7"}, file_attributes={"SOURCE_SCANNER": "Leica RTC360"}))
        assert kept == [("fileMetadata", "PART_NO: GP-100"), ("assetMetadata", "PROJECT: Alpha"),
                        ("databaseMetadata", "SITE: Plant 7"), ("fileAttributes", "SOURCE_SCANNER: Leica RTC360")]
        assert dropped == 0

    def test_keys_sort_within_a_scope(self):
        kept, _dropped = common.existing_metadata_lines(_view(file_metadata={"b": "2", "a": "3", "A": "1"}))
        assert [line for _scope, line in kept] == ["A: 1", "a: 3", "b: 2"]

    def test_blank_keys_and_blank_or_null_values_are_skipped(self):
        kept, dropped = common.existing_metadata_lines(_view(
            file_metadata={"empty": "", "spaces": "   ", "none": None, "": "no key", "  ": "blank key", "kept": "x"}))
        assert kept == [("fileMetadata", "kept: x")] and dropped == 0

    def test_pipeline_owned_and_system_keys_are_skipped(self):
        """ext_*, genai_* and sys_* are what this pipeline writes; an earlier run's copy is never fed back
        in. A legacy autoGeneratedKeywords row, an AB_* row, a key that merely begins with the letters
        'ext' and a key that carries the pipeline's prefix in another case (Sys_Notes — the prefix match is
        case-sensitive) are existing metadata and stay."""
        kept, dropped = common.existing_metadata_lines(_view(
            file_metadata={"ext_units": "m", "genai_title": "Old title", "autoGeneratedKeywords": "pump", "extra": "kept"},
            asset_metadata={"genai_asset_keywords": "old", "AB_source": "scan", "Sys_Notes": "kept too"},
            file_attributes={"sys_file": '{"name":"pump.glb"}', "SOURCE_SCANNER": "Leica RTC360"}))
        assert [line for _scope, line in kept] == ["autoGeneratedKeywords: pump", "extra: kept", "AB_source: scan",
                                                    "Sys_Notes: kept too", "SOURCE_SCANNER: Leica RTC360"]
        assert dropped == 0  # a skipped key is not a dropped line

    def test_values_are_whitespace_normalised(self):
        assert common.render_existing_value("  two\n lines\t here ") == "two lines here"
        kept, _dropped = common.existing_metadata_lines(_view(file_metadata={"NOTE": "a\n\nb"}))
        assert kept == [("fileMetadata", "NOTE: a b")]

    def test_a_geojson_value_renders_as_its_type_without_coordinates(self):
        assert common.render_existing_value({"type": "Point", "coordinates": [8.54, 47.37]}) == "Point geometry"
        assert common.render_existing_value(
            '{"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": null}]}'
        ) == "FeatureCollection geometry"
        assert common.render_existing_value({"type": "GeometryCollection", "geometries": []}) == "GeometryCollection geometry"
        # A bare type word is not GeoJSON, and an unknown type is not either: both render as their JSON.
        assert common.render_existing_value({"type": "Point"}) == '{"type":"Point"}'
        assert common.render_existing_value({"type": "Pointy", "coordinates": [1, 2]}) == '{"coordinates":[1,2],"type":"Pointy"}'
        # A geometry whose coordinates alone exceed the value limit is still its type, never a cut JSON string.
        ring = ",".join(f"[{8.5 + i / 1000:.3f},{47.3 + i / 1000:.3f}]" for i in range(60))
        long_polygon = '{"type":"Polygon","coordinates":[[' + ring + ']]}'
        assert len(long_polygon) > common.EXISTING_VALUE_MAX_CHARS
        assert common.render_existing_value(long_polygon) == "Polygon geometry"
        assert common.render_existing_value(json.loads(long_polygon)) == "Polygon geometry"
        kept, _dropped = common.existing_metadata_lines(_view(
            file_metadata={"location": '{"type":"Polygon","coordinates":[[[8.5,47.3],[8.6,47.3],[8.6,47.4],[8.5,47.3]]]}'}))
        assert kept == [("fileMetadata", "location: Polygon geometry")]

    def test_a_long_value_is_cut_with_an_ellipsis(self):
        rendered = common.render_existing_value("x" * 1000)
        assert rendered == "x" * 400 + "\u2026" and len(rendered) == common.EXISTING_VALUE_MAX_CHARS + 1
        assert common.render_existing_value("x" * 400) == "x" * 400  # at the limit, untouched

    def test_lines_are_kept_whole_and_everything_after_the_first_drop_goes(self):
        """10 + 10 + 18 > 30: the third line would cross the budget and is dropped; the fourth would fit on its
        own and is dropped anyway — every line after the first drop goes, across scopes too."""
        kept, dropped = common.existing_metadata_lines(_view(
            file_metadata={"a": "x" * 7, "b": "y" * 7, "c": "z" * 15}, asset_metadata={"d": "1"}), max_chars=30)
        assert kept == [("fileMetadata", "a: " + "x" * 7), ("fileMetadata", "b: " + "y" * 7)]
        assert dropped == 2

    def test_a_line_that_exactly_fills_the_budget_is_kept(self):
        """10 + 10 = 20 lands exactly on the budget: the second line is kept; the third is the first drop."""
        kept, dropped = common.existing_metadata_lines(_view(
            file_metadata={"a": "x" * 7, "b": "y" * 7, "c": "z"}), max_chars=20)
        assert kept == [("fileMetadata", "a: " + "x" * 7), ("fileMetadata", "b: " + "y" * 7)]
        assert sum(len(line) for _scope, line in kept) == 20 and dropped == 1

    def test_the_default_budget_is_the_module_constant(self):
        """40 lines of 409 characters (16,360) against 12,000: 29 whole lines (11,861) are kept, the 30th
        would cross the budget, and it, the remaining 10 file lines and the asset line are dropped."""
        notes = {f"NOTE_{i:02d}": "n" * 400 for i in range(40)}
        kept, dropped = common.existing_metadata_lines(_view(file_metadata=notes, asset_metadata={"PROJECT": "Alpha"}))
        assert len(kept) == 29 and dropped == 12
        assert sum(len(line) for _scope, line in kept) == 29 * 409 <= common.EXISTING_METADATA_MAX_CHARS
        assert all(len(line) == 409 for _scope, line in kept)  # whole lines only, never a cut line
        assert ("assetMetadata", "PROJECT: Alpha") not in kept
