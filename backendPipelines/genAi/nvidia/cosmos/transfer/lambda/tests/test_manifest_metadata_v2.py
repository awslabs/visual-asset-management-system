#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the v2 grouped-by-asset metadata read path in the vendored pipeline manifestHelper
(WB7). ``fetch_metadata`` returns the v2 body as-is while still unwrapping the legacy v1 envelope;
``get_asset_file_record`` / ``asset_metadata_for`` / ``file_metadata_for`` / ``file_attributes_for``
/ ``database_metadata_for`` pull specific records; ``to_legacy_vams_view`` projects either envelope
version onto the legacy ``{"VAMS": {...}}`` shape every pipeline reader already digs into;
``resolve_input_setting`` resolves one setting from either the raw envelope or a projected view;
``resolved_file_key`` derives the per-file metadata key from the resolved manifest, and
``run_vams_view`` projects the envelope for the subject a resolved manifest describes.

The envelope shape mirrors backend ``executionRecords.build_grouped_metadata_envelope`` exactly:
``{"schemaVersion": 2, "assets": [ {databaseId, assetId, assetData, files: [ {fileKey, metadata,
attributes?} ]} ], "databases": [ {databaseId, metadata} ]}`` — asset-level metadata is the fileKey
'/' record; per-file metadata/attributes are per-file records keyed by the normalized relative file
key; database metadata is a top-level sibling of assets[], present only when a run captured it.

Every pipeline vendors its own copy of ``manifestHelper.py``, so a final check asserts the copies
are byte-identical to this one — the read path exercised here is then the one they all run."""

import os
import sys
import json
import glob
import types
import hashlib
from unittest.mock import MagicMock

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

import manifestHelper as mh  # noqa: E402


def _v2_envelope():
    """A grouped envelope with two assets, asset-level + per-file metadata + file attributes."""
    return {
        "schemaVersion": 2,
        "assets": [
            {
                "databaseId": "db1", "assetId": "assetA",
                "assetData": {"assetName": "A", "description": "", "tags": []},
                "files": [
                    {"fileKey": "/", "metadata": {"COSMOS_TRANSFER_CONTROL_TYPE": "edge"}},
                    {"fileKey": "/clips/in.mp4",
                     "metadata": {"COSMOS_TRANSFER_PROMPT": "make it snow"},
                     "attributes": {"fps": "30"}},
                ],
            },
            {
                "databaseId": "db2", "assetId": "assetB",
                "assetData": {"assetName": "B", "description": "", "tags": []},
                "files": [
                    {"fileKey": "/", "metadata": {"PART": "pump"}},
                ],
            },
        ],
    }


def _s3_returning(body_obj):
    """A MagicMock s3 client whose get_object returns the given JSON body."""
    s3 = MagicMock()
    payload = MagicMock()
    payload.read.return_value = json.dumps(body_obj).encode("utf-8")
    s3.get_object.return_value = {"Body": payload}
    return s3


@pytest.mark.unit
class TestFetchMetadataEnvelopeVersions:
    def test_v2_body_returned_as_is(self):
        s3 = _s3_returning(_v2_envelope())
        body = mh.fetch_metadata(s3, "s3://b/metadata.json")
        assert body.get("schemaVersion") == 2 and "assets" in body

    def test_v1_envelope_still_unwrapped(self):
        s3 = _s3_returning({"schemaVersion": 1, "metadata": {"VAMS": {"assetMetadata": {"K": "v"}}}})
        body = mh.fetch_metadata(s3, "s3://b/metadata.json")
        assert body == {"VAMS": {"assetMetadata": {"K": "v"}}}

    def test_empty_location(self):
        assert mh.fetch_metadata(MagicMock(), "") == {}


@pytest.mark.unit
class TestRecordAccessors:
    def test_get_asset_file_record_asset_level(self):
        rec = mh.get_asset_file_record(_v2_envelope(), "db1", "assetA", "/")
        assert rec["metadata"]["COSMOS_TRANSFER_CONTROL_TYPE"] == "edge"

    def test_get_asset_file_record_normalizes_key(self):
        # A caller passing the un-slashed key resolves the same '/clips/in.mp4' record.
        rec = mh.get_asset_file_record(_v2_envelope(), "db1", "assetA", "clips/in.mp4")
        assert rec["metadata"]["COSMOS_TRANSFER_PROMPT"] == "make it snow"

    def test_get_asset_file_record_absent(self):
        assert mh.get_asset_file_record(_v2_envelope(), "db1", "assetA", "/nope.mp4") is None
        assert mh.get_asset_file_record(_v2_envelope(), "dbX", "assetX", "/") is None

    def test_asset_metadata_for(self):
        assert mh.asset_metadata_for(_v2_envelope(), "db2", "assetB") == {"PART": "pump"}

    def test_file_metadata_and_attributes_for(self):
        env = _v2_envelope()
        assert mh.file_metadata_for(env, "db1", "assetA", "/clips/in.mp4") == {"COSMOS_TRANSFER_PROMPT": "make it snow"}
        assert mh.file_attributes_for(env, "db1", "assetA", "/clips/in.mp4") == {"fps": "30"}

    def test_accessors_v1_fallback(self):
        v1 = {"VAMS": {"assetMetadata": {"A": "1"}, "fileMetadata": {"F": "2"}, "fileAttributes": {"T": "3"}}}
        assert mh.asset_metadata_for(v1, "db", "a") == {"A": "1"}
        assert mh.file_metadata_for(v1, "db", "a", "/x") == {"F": "2"}
        assert mh.file_attributes_for(v1, "db", "a", "/x") == {"T": "3"}


@pytest.mark.unit
class TestToLegacyVamsView:
    def test_v2_projected_to_legacy_shape(self):
        view = mh.to_legacy_vams_view(_v2_envelope(), "db1", "assetA", "/clips/in.mp4")
        assert view["VAMS"]["assetMetadata"] == {"COSMOS_TRANSFER_CONTROL_TYPE": "edge"}
        assert view["VAMS"]["fileMetadata"] == {"COSMOS_TRANSFER_PROMPT": "make it snow"}
        assert view["VAMS"]["fileAttributes"] == {"fps": "30"}

    def test_v2_asset_level_only_when_file_key_is_root(self):
        view = mh.to_legacy_vams_view(_v2_envelope(), "db2", "assetB", "/")
        assert view["VAMS"]["assetMetadata"] == {"PART": "pump"}
        assert view["VAMS"]["fileMetadata"] == {}
        assert view["VAMS"]["fileAttributes"] == {}

    def test_v1_passthrough_unchanged(self):
        v1 = {"VAMS": {"assetMetadata": {"K": "v"}}}
        assert mh.to_legacy_vams_view(v1, "db", "a", "/x") == v1

    def test_non_dict_yields_empty(self):
        assert mh.to_legacy_vams_view("", "db", "a", "/") == {}
        assert mh.to_legacy_vams_view(None) == {}


def _v2_envelope_with_databases(databases):
    """The grouped envelope's two-asset shape plus a top-level 'databases' list."""
    env = _v2_envelope()
    env["databases"] = databases
    return env


@pytest.mark.unit
class TestDatabaseMetadataScope:
    """Database metadata belongs to no asset, so it rides the envelope's top-level 'databases' list
    and projects as the legacy view's fifth scope. Resolution mirrors backend
    ``executionRecords.get_database_metadata`` exactly, so the render path and the pipeline read path
    agree on which database a value came from."""

    def test_legacy_view_projects_five_scopes(self):
        env = _v2_envelope_with_databases([{"databaseId": "db1", "metadata": {"SITE": "plant-1"}}])
        view = mh.to_legacy_vams_view(env, "db1", "assetA", "/clips/in.mp4")["VAMS"]
        assert set(view) == {"assetData", "assetMetadata", "fileMetadata", "fileAttributes",
                            "databaseMetadata"}
        assert view["assetData"] == {"assetName": "A", "description": "", "tags": []}
        assert view["assetMetadata"] == {"COSMOS_TRANSFER_CONTROL_TYPE": "edge"}
        assert view["fileMetadata"] == {"COSMOS_TRANSFER_PROMPT": "make it snow"}
        assert view["fileAttributes"] == {"fps": "30"}
        assert view["databaseMetadata"] == {"SITE": "plant-1"}

    def test_a_lone_database_resolves_whatever_the_subject(self):
        # A named metadata-source database is not necessarily an input asset's database, and a
        # file-less run has no asset to project through at all, so its subject databaseId is empty.
        # One captured database is unambiguous, so it resolves for every subject.
        env = _v2_envelope_with_databases([{"databaseId": "dbsrc", "metadata": {"dm": "4"}}])
        for db, asset, fk in (("db1", "assetA", "/"), ("db1", "assetA", "/clips/in.mp4"),
                              ("nosuchdb", "nosuchasset", "/"), ("", "", "/")):
            view = mh.to_legacy_vams_view(env, db, asset, fk)["VAMS"]
            assert view["databaseMetadata"] == {"dm": "4"}, (db, asset, fk)

    def test_several_databases_stay_attributed_to_their_own(self):
        # With more than one captured database the requested id is the only thing that can tell them
        # apart, so each subject sees its own and an unrelated subject sees nothing.
        env = _v2_envelope_with_databases([{"databaseId": "db1", "metadata": {"dm": "1"}},
                                          {"databaseId": "db2", "metadata": {"dm": "2"}}])
        assert mh.to_legacy_vams_view(env, "db1", "assetA", "/")["VAMS"]["databaseMetadata"] == {"dm": "1"}
        assert mh.to_legacy_vams_view(env, "db2", "assetB", "/")["VAMS"]["databaseMetadata"] == {"dm": "2"}
        assert mh.to_legacy_vams_view(env, "db3", "assetC", "/")["VAMS"]["databaseMetadata"] == {}

    def test_get_database_metadata_reads_one_entry(self):
        env = _v2_envelope_with_databases([{"databaseId": "db1", "metadata": {"a": "1"}},
                                          {"databaseId": "db2", "metadata": {"b": "2"}}])
        assert mh.get_database_metadata(env, "db2") == {"b": "2"}
        assert mh.get_database_metadata(env, "nosuch") == {}
        assert mh.get_database_metadata({}, "db1") == {}

    def test_database_metadata_for_accessor(self):
        env = _v2_envelope_with_databases([{"databaseId": "db1", "metadata": {"dm": "4"}}])
        assert mh.database_metadata_for(env, "db1") == {"dm": "4"}
        # A v1 body falls back to the legacy scope, like the sibling accessors.
        assert mh.database_metadata_for({"VAMS": {"databaseMetadata": {"L": "v"}}}, "db") == {"L": "v"}

    def test_an_envelope_without_the_section_behaves_as_before(self):
        # An envelope carrying no 'databases' key must yield {} rather than a KeyError, and every
        # other scope must be untouched.
        env = _v2_envelope()
        assert "databases" not in env
        view = mh.to_legacy_vams_view(env, "db1", "assetA", "/clips/in.mp4")["VAMS"]
        assert view["databaseMetadata"] == {}
        assert view["assetMetadata"] == {"COSMOS_TRANSFER_CONTROL_TYPE": "edge"}
        assert view["fileMetadata"] == {"COSMOS_TRANSFER_PROMPT": "make it snow"}
        assert view["fileAttributes"] == {"fps": "30"}
        assert view["assetData"] == {"assetName": "A", "description": "", "tags": []}
        assert mh.database_metadata_for(env, "db1") == {}
        assert mh.get_database_metadata(env, "db1") == {}

    def test_schema_version_stays_two(self):
        # Every pipeline copy gates on schemaVersion == 2 by EQUALITY, so a bump would send them all
        # down the v1 unwrap branch and lose the metadata entirely.
        assert mh.METADATA_SCHEMA_VERSION_GROUPED == 2


@pytest.mark.unit
class TestResolveInputSettingFromGroupedEnvelope:
    """``resolve_input_setting`` resolves whether it is handed the RAW grouped envelope
    ``fetch_metadata`` returns or a legacy view a caller already projected. A grouped envelope names
    no subject, so a scope supplies a value only where the envelope leaves no doubt which asset (or
    database) it came from."""

    @staticmethod
    def _one_asset_envelope():
        return {
            "schemaVersion": 2,
            "assets": [{
                "databaseId": "db1", "assetId": "assetA",
                "assetData": {"assetName": "A"},
                "files": [
                    {"fileKey": "/", "metadata": {"COSMOS_PREDICT_PROMPT": "from the asset"}},
                    {"fileKey": "/clips/in.mp4", "metadata": {"COSMOS_PREDICT_PROMPT": "from the file"},
                     "attributes": {"fps": "30"}},
                ],
            }],
            "databases": [{"databaseId": "db1", "metadata": {"COSMOS_PREDICT_PROMPT": "from the db"}}],
        }

    def test_asset_scope_resolves_from_a_raw_envelope(self):
        got = mh.resolve_input_setting(
            {}, self._one_asset_envelope(), ("PROMPT",), "COSMOS_PREDICT_PROMPT")
        assert got == "from the asset"

    def test_file_scope_resolves_from_a_raw_envelope(self):
        got = mh.resolve_input_setting(
            {}, self._one_asset_envelope(), ("PROMPT",), "COSMOS_PREDICT_PROMPT",
            metadata_scopes=("fileMetadata", "assetMetadata"))
        assert got == "from the file"

    def test_database_scope_is_reachable_once_the_scope_exists(self):
        env = self._one_asset_envelope()
        env["assets"][0]["files"] = []
        got = mh.resolve_input_setting(
            {}, env, ("PROMPT",), "COSMOS_PREDICT_PROMPT",
            metadata_scopes=("assetMetadata", "databaseMetadata"))
        assert got == "from the db"

    def test_a_file_less_run_still_resolves_its_lone_database(self):
        # The arity-none shape: no asset to project through, one named metadata-source database.
        env = {"schemaVersion": 2, "assets": [],
               "databases": [{"databaseId": "dbsrc", "metadata": {"COSMOS_PREDICT_PROMPT": "snowy"}}]}
        got = mh.resolve_input_setting(
            {}, env, ("PROMPT",), "COSMOS_PREDICT_PROMPT",
            metadata_scopes=("assetMetadata", "databaseMetadata"))
        assert got == "snowy"

    def test_several_assets_leave_the_asset_scope_unresolved(self):
        # Two assets give no way to say which one a run-level value belongs to, so the scope stays
        # empty rather than picking one arbitrarily. The lone database is still unambiguous.
        env = _v2_envelope_with_databases([{"databaseId": "db1", "metadata": {"K": "db"}}])
        assert mh.resolve_input_setting(
            {}, env, ("PROMPT",), "COSMOS_TRANSFER_CONTROL_TYPE") == ""
        assert mh.resolve_input_setting(
            {}, env, ("PROMPT",), "K", metadata_scopes=("databaseMetadata",)) == "db"

    def test_several_files_leave_the_file_scope_unresolved(self):
        env = self._one_asset_envelope()
        env["assets"][0]["files"].append(
            {"fileKey": "/clips/other.mp4", "metadata": {"COSMOS_PREDICT_PROMPT": "another file"}})
        assert mh.resolve_input_setting(
            {}, env, ("PROMPT",), "COSMOS_PREDICT_PROMPT",
            metadata_scopes=("fileMetadata",)) == ""
        # The asset scope is still unambiguous with one asset group.
        assert mh.resolve_input_setting(
            {}, env, ("PROMPT",), "COSMOS_PREDICT_PROMPT") == "from the asset"

    def test_a_projected_legacy_view_still_resolves(self):
        view = mh.to_legacy_vams_view(self._one_asset_envelope(), "db1", "assetA", "/clips/in.mp4")
        assert mh.resolve_input_setting(
            {}, view, ("PROMPT",), "COSMOS_PREDICT_PROMPT",
            metadata_scopes=("fileMetadata", "assetMetadata")) == "from the file"

    def test_a_json_string_envelope_resolves(self):
        got = mh.resolve_input_setting(
            {}, json.dumps(self._one_asset_envelope()), ("PROMPT",), "COSMOS_PREDICT_PROMPT")
        assert got == "from the asset"

    def test_config_first_precedence_is_unchanged(self):
        env = self._one_asset_envelope()
        assert mh.resolve_input_setting(
            {"PROMPT": "cfg"}, env, ("PROMPT",), "COSMOS_PREDICT_PROMPT") == "cfg"
        # A blank/whitespace configuration value still falls through to metadata.
        assert mh.resolve_input_setting(
            {"PROMPT": "   "}, env, ("PROMPT",), "COSMOS_PREDICT_PROMPT") == "from the asset"

    def test_a_v1_body_still_resolves(self):
        v1 = {"VAMS": {"assetMetadata": {"COSMOS_PREDICT_PROMPT": "legacy"}}}
        assert mh.resolve_input_setting({}, v1, ("PROMPT",), "COSMOS_PREDICT_PROMPT") == "legacy"

    def test_an_envelope_without_the_section_resolves_its_other_scopes(self):
        env = self._one_asset_envelope()
        del env["databases"]
        assert mh.resolve_input_setting(
            {}, env, ("PROMPT",), "COSMOS_PREDICT_PROMPT") == "from the asset"
        assert mh.resolve_input_setting(
            {}, env, ("PROMPT",), "COSMOS_PREDICT_PROMPT",
            metadata_scopes=("databaseMetadata",)) == ""


@pytest.mark.unit
class TestResolvedFileKey:
    def test_from_manifest_first_input_file(self):
        resolved = {"inputFiles": [{"relativePath": "/clips/in.mp4"}]}
        assert mh.resolved_file_key(resolved) == "/clips/in.mp4"

    def test_normalizes_unslashed(self):
        resolved = {"inputFiles": [{"relativePath": "clips/in.mp4"}]}
        assert mh.resolved_file_key(resolved) == "/clips/in.mp4"

    def test_no_manifest_defaults_to_asset_level(self):
        assert mh.resolved_file_key({"inputFiles": []}) == "/"
        assert mh.resolved_file_key({}) == "/"


@pytest.mark.unit
class TestRunVamsView:
    """``run_vams_view`` picks the subject a resolved manifest describes and projects the envelope for
    it, which is the shape every ``vamsExecute`` lambda hands to ``resolve_input_setting``. The chain
    resolve_inputs -> run_vams_view -> resolve_input_setting is exercised end to end here, since the
    subject the projection uses is what decides whether a value is found at all."""

    def test_an_input_file_is_the_subject(self):
        env = _v2_envelope()
        manifest = {"inputFiles": [{"relativePath": "/clips/in.mp4", "databaseId": "db1",
                                    "assetId": "assetA", "bucket": "b", "key": "db1/assetA/clips/in.mp4"}]}
        resolved = mh.resolve_inputs({}, manifest)
        view = mh.run_vams_view(env, resolved)["VAMS"]
        assert view["assetMetadata"] == {"COSMOS_TRANSFER_CONTROL_TYPE": "edge"}
        assert view["fileMetadata"] == {"COSMOS_TRANSFER_PROMPT": "make it snow"}
        assert view["fileAttributes"] == {"fps": "30"}

    def test_an_input_file_wins_over_a_metadata_source_asset(self):
        # assetB is a named metadata source; assetA is the input file's asset. The input file is the
        # run's subject, so its asset's value is the one a setting resolves to.
        env = _v2_envelope()
        env["assets"][0]["files"][0]["metadata"] = {"COSMOS_TRANSFER_PROMPT": "from-input"}
        env["assets"][1]["files"][0]["metadata"] = {"COSMOS_TRANSFER_PROMPT": "from-source"}
        manifest = {"inputFiles": [{"relativePath": "/clips/in.mp4", "databaseId": "db1",
                                    "assetId": "assetA", "bucket": "b", "key": "db1/assetA/clips/in.mp4"}]}
        resolved = mh.resolve_inputs({}, manifest)
        assert mh.resolve_input_setting(
            {}, mh.run_vams_view(env, resolved), ("PROMPT",), "COSMOS_TRANSFER_PROMPT") == "from-input"

    def test_a_file_less_run_resolves_its_metadata_source_asset(self):
        # An arity-'none' run has no input file, so resolve_inputs falls back to the OUTPUT target —
        # the asset results are written to, which carries no captured metadata. The envelope's first
        # asset group is the run's metadata-source asset, and that is the subject.
        env = {"schemaVersion": 2, "assets": [
            {"databaseId": "dbA", "assetId": "srcAsset",
             "assetData": {"assetName": "src", "description": "", "tags": []},
             "files": [{"fileKey": "/", "metadata": {"COSMOS_PREDICT_PROMPT": "a robot dog running"}}]}],
            "databases": [{"databaseId": "dbA", "metadata": {"SITE": "plant-1"}}]}
        resolved = mh.resolve_inputs({"outputAssetId": "destAsset", "outputDatabaseId": "dbA"}, None)
        assert (resolved["assetId"], resolved["databaseId"]) == ("destAsset", "dbA")
        view = mh.run_vams_view(env, resolved)
        assert view["VAMS"]["assetData"] == {"assetName": "src", "description": "", "tags": []}
        assert view["VAMS"]["assetMetadata"] == {"COSMOS_PREDICT_PROMPT": "a robot dog running"}
        assert view["VAMS"]["databaseMetadata"] == {"SITE": "plant-1"}
        assert mh.resolve_input_setting(
            {}, view, ("PROMPT", "prompt"), "COSMOS_PREDICT_PROMPT") == "a robot dog running"

    def test_a_file_less_run_with_only_a_database_resolves_that_database(self):
        env = {"schemaVersion": 2, "assets": [],
               "databases": [{"databaseId": "dbZ", "metadata": {"COSMOS_PREDICT_PROMPT": "db-wide"}}]}
        resolved = mh.resolve_inputs({"outputAssetId": "destAsset", "outputDatabaseId": "dbOut"}, None)
        view = mh.run_vams_view(env, resolved)
        assert view["VAMS"]["assetMetadata"] == {}
        assert mh.resolve_input_setting(
            {}, view, ("PROMPT",), "COSMOS_PREDICT_PROMPT",
            metadata_scopes=("assetMetadata", "databaseMetadata")) == "db-wide"

    def test_config_still_wins_over_the_resolved_subject(self):
        env = {"schemaVersion": 2, "assets": [
            {"databaseId": "dbA", "assetId": "srcAsset", "assetData": {},
             "files": [{"fileKey": "/", "metadata": {"COSMOS_PREDICT_PROMPT": "standing"}}]}]}
        resolved = mh.resolve_inputs({"outputAssetId": "destAsset", "outputDatabaseId": "dbA"}, None)
        view = mh.run_vams_view(env, resolved)
        assert mh.resolve_input_setting(
            {"PROMPT": "typed"}, view, ("PROMPT",), "COSMOS_PREDICT_PROMPT") == "typed"
        assert mh.resolve_input_setting(
            {"PROMPT": "  "}, view, ("PROMPT",), "COSMOS_PREDICT_PROMPT") == "standing"

    def test_a_v1_body_passes_through(self):
        v1 = {"VAMS": {"assetMetadata": {"COSMOS_PREDICT_PROMPT": "legacy"}}}
        resolved = mh.resolve_inputs({"outputAssetId": "destAsset"}, None)
        assert mh.run_vams_view(v1, resolved) == v1
        assert mh.resolve_input_setting(
            {}, mh.run_vams_view(v1, resolved), ("PROMPT",), "COSMOS_PREDICT_PROMPT") == "legacy"

    def test_an_envelope_without_databases_leaves_that_scope_empty(self):
        resolved = mh.resolve_inputs({"outputAssetId": "destAsset", "outputDatabaseId": "db1"}, None)
        view = mh.run_vams_view(_v2_envelope(), resolved)["VAMS"]
        assert view["databaseMetadata"] == {}
        assert view["assetMetadata"] == {"COSMOS_TRANSFER_CONTROL_TYPE": "edge"}

    def test_a_missing_or_unusable_body_yields_empty(self):
        resolved = mh.resolve_inputs({}, None)
        assert mh.run_vams_view({}, resolved) == {}
        assert mh.run_vams_view(None, resolved) == {}
        assert mh.run_vams_view("", resolved) == {}
        assert mh.run_vams_view(_v2_envelope(), None)["VAMS"]["assetMetadata"] == {
            "COSMOS_TRANSFER_CONTROL_TYPE": "edge"}


def _pipelines_root():
    path = _LAMBDA_DIR
    while os.path.basename(path) != "backendPipelines":
        parent = os.path.dirname(path)
        if parent == path:
            pytest.skip("backendPipelines root not found")
        path = parent
    return path


def _helper_digest(path):
    with open(path, "r", encoding="utf-8", newline=None) as fh:
        return hashlib.sha256(fh.read().encode("utf-8")).hexdigest()


@pytest.mark.unit
class TestVendoredHelperCopiesMatch:
    def test_every_pipeline_helper_is_identical(self):
        canonical = os.path.join(_LAMBDA_DIR, "manifestHelper.py")
        copies = sorted(glob.glob(os.path.join(_pipelines_root(), "**", "lambda", "manifestHelper.py"),
                                  recursive=True))
        assert len(copies) > 1, "expected the vendored helper in multiple pipelines"
        expected = _helper_digest(canonical)
        drifted = [c for c in copies if _helper_digest(c) != expected]
        assert drifted == [], f"vendored manifestHelper.py copies drifted from {canonical}: {drifted}"
