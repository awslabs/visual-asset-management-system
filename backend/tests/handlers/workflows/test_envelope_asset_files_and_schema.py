# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The two opt-in metadataInputs keys of the workflow-execution input envelope:

  - assetFileMetadata: a whole-asset ('/') or folder ('/f/') selection is expanded into one record per
    current file under it, capped, with archived and preview files left out and a truncation warning;
  - metadataSchema: the involved databases' aggregated metadata schemas, read after the schema
    endpoint's permission checks, with a denied or unreadable schema left out behind one generic
    warning.

Both default off, and with both off the envelope is byte-identical to the one the execute path built
before the keys existed (the golden file was generated from that code). Each step receives a section
only when its own gate asks for it.

The handler loads env vars and resource names at import; the seeds below mirror test_executeWorkflow.py
so this module imports standalone as well as inside a full-suite run."""
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2")
os.environ.setdefault("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME", "t-templates")
os.environ.setdefault("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME", "t-tagschema")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux")
os.environ.setdefault("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import executeWorkflow as ew  # noqa: E402
from backend.backend.common.workflows import executionRecords as er  # noqa: E402
from backend.backend.common.workflows import executionValidation as ev  # noqa: E402
from backend.backend.common import metadataSchemaValidation as msv  # noqa: E402
from backend.backend.models import pipelines as pipeline_models  # noqa: E402
from backend.backend.models import workflows as workflow_models  # noqa: E402

MOD = "backend.backend.handlers.workflows.executeWorkflow"
GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden",
                      "grouped_metadata_envelope_without_opt_in_keys.json")


#######################
# Fixtures
#######################

# Three assets, each selected a different way, plus a metadata-source asset and two source databases:
# every record kind the envelope carries. a2 is selected whole and a3 by folder, so an expansion would
# add records to both if it ran.
GOLDEN_INPUTS = [
    {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/x.glb", "versionId": ""},
    {"databaseId": "db1", "assetId": "a2", "relativeFileKey": "/", "versionId": ""},
    {"databaseId": "db2", "assetId": "a3", "relativeFileKey": "/folder/", "versionId": ""},
]
GOLDEN_SOURCES = [{"databaseId": "db2", "assetId": "a9"}]
GOLDEN_DATABASES = ["db1", "db2"]

# Objects under each asset's location: a2 at 'assets/a2/', a3 at 'assets/a3/'.
LISTINGS = {
    "assets/a2/": [
        "assets/a2/", "assets/a2/b.glb", "assets/a2/b.glb.previewFile.png", "assets/a2/sub/",
        "assets/a2/sub/c.usd", "assets/a2/z.txt",
    ],
    "assets/a3/folder/": [
        "assets/a3/folder/", "assets/a3/folder/m.obj", "assets/a3/folder/m.mtl",
    ],
    "assets/a1/": ["assets/a1/x.glb", "assets/a1/y.glb"],
}


def _entries(**pairs):
    return [{"metadataKey": k, "metadataValue": v} for k, v in pairs.items()]


def _reads():
    """The metadata service's answers, keyed the way the builder keys its reads."""
    return {
        ("asset", "db1", "a1"): _entries(title="A1"),
        ("asset", "db1", "a2"): _entries(title="A2"),
        ("asset", "db2", "a3"): _entries(title="A3"),
        ("asset", "db2", "a9"): _entries(title="A9"),
        ("metadata", "db1", "a1", "/x.glb"): _entries(material="steel"),
        ("attribute", "db1", "a1", "/x.glb"): _entries(units="mm"),
        ("metadata", "db1", "a2", "/b.glb"): _entries(material="wood"),
        ("attribute", "db1", "a2", "/b.glb"): _entries(units="cm"),
        ("metadata", "db1", "a2", "/sub/c.usd"): _entries(material="glass"),
        ("metadata", "db2", "a3", "/folder/m.obj"): _entries(material="clay"),
        ("database", "db1"): _entries(region="north"),
        ("database", "db2"): _entries(region="south"),
    }


def _asset_records(inputs, sources=()):
    records = {}
    for item in list(inputs) + list(sources):
        key = (item["databaseId"], item["assetId"])
        records[key] = {"assetName": item["assetId"], "description": "", "tags": [],
                        "bucketId": "bkt-id",
                        "assetLocation": {"Key": f"assets/{item['assetId']}/"}}
    return records


def _list_objects(listings):
    """A ListObjectsV2 stub over {prefix-root: [keys]}: answers the keys under the requested Prefix in
    key order, one page."""
    def _call(**kwargs):
        prefix = kwargs["Prefix"]
        keys = sorted({k for keys in listings.values() for k in keys if k.startswith(prefix)})
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}
    return _call


def _build(gate, inputs=GOLDEN_INPUTS, sources=GOLDEN_SOURCES, databases=GOLDEN_DATABASES,
           reads=None, listings=None, notices=None, s3=None, extra_patches=()):
    """Build an envelope through the real assembly path with stubbed metadata reads and S3 listing."""
    reads = _reads() if reads is None else reads
    s3_mock = s3 or MagicMock()
    if s3 is None:
        s3_mock.list_objects_v2.side_effect = _list_objects(LISTINGS if listings is None else listings)
    patches = [
        patch(f"{MOD}._fetch_metadata", side_effect=lambda d, a, _q, _e: reads.get(("asset", d, a), [])),
        patch(f"{MOD}._fetch_file_metadata",
              side_effect=lambda d, a, r, kind, _e: reads.get((kind, d, a, r), [])),
        patch(f"{MOD}._fetch_database_metadata", side_effect=lambda d, _e: reads.get(("database", d), [])),
        patch(f"{MOD}._asset_bucket_details", return_value={"bucketName": "bkt"}),
        patch(f"{MOD}.s3c", s3_mock),
    ] + list(extra_patches)
    for p in patches:
        p.start()
    try:
        return ew._build_grouped_metadata(
            list(inputs), _asset_records(inputs, sources), gate, {},
            metadata_source_assets=list(sources), metadata_source_databases=list(databases),
            notices=notices)
    finally:
        for p in reversed(patches):
            p.stop()


def _group(envelope, database_id, asset_id):
    return next(g for g in envelope["assets"]
                if g["databaseId"] == database_id and g["assetId"] == asset_id)


def _keys(group):
    return [r["fileKey"] for r in group["files"]]


EXPAND = {"assetFileMetadata": True}


#######################
# Byte identity with both keys off
#######################

@pytest.mark.unit
class TestEnvelopeWithoutOptInKeysIsUnchanged:
    """Requirement 3: with both keys absent or false the envelope is byte-identical to today's. The
    golden file was written by the execute path of the release before the keys existed, from the same
    fixture; this test therefore passes on that code too, by design: it pins identity, not a change."""

    @pytest.mark.parametrize("gate", [
        {},
        {"assetFileMetadata": False, "metadataSchema": False},
        {"assetMetadata": True, "fileMetadata": True, "fileAttributes": True, "databaseMetadata": True},
    ], ids=["absent", "false", "four-types-only"])
    def test_the_envelope_matches_the_golden_bytes(self, gate):
        s3 = MagicMock()
        envelope = _build(gate, s3=s3)
        # The golden file is stored formatted (the repo's prettier check reflows JSON); loading it
        # keeps key order, so re-serializing it gives the exact bytes the execute path wrote to S3
        # (json.dumps(envelope)) and the comparison is byte for byte on that form.
        with open(GOLDEN, encoding="utf-8") as handle:
            golden_bytes = json.dumps(json.load(handle)).encode("utf-8")
        assert json.dumps(envelope).encode("utf-8") == golden_bytes
        # Nothing was listed: the expansion is the only S3 call this path could add.
        s3.list_objects_v2.assert_not_called()

    def test_no_step_narrowing_is_triggered_by_the_new_keys(self):
        envelope = _build({})
        # A step gate that names neither key leaves an envelope carrying neither section as the same
        # object, so the common case still writes one shared S3 file.
        assert er.narrow_metadata_envelope(envelope, {}) is envelope
        assert er.narrow_metadata_envelope(envelope, {"assetFileMetadata": False}) is envelope


#######################
# assetFileMetadata
#######################

@pytest.mark.unit
class TestWholeAssetAndFolderExpansion:

    def test_a_whole_asset_selection_carries_every_current_file(self):
        envelope = _build(EXPAND)
        group = _group(envelope, "db1", "a2")
        # The asset-level record first, then one record per file in key order; the folder markers and
        # the generated preview file are not files of the asset.
        assert _keys(group) == ["/", "/b.glb", "/sub/c.usd", "/z.txt"]
        records = {r["fileKey"]: r for r in group["files"]}
        assert records["/b.glb"] == {"fileKey": "/b.glb", "metadata": {"material": "wood"},
                                     "attributes": {"units": "cm"}, "expanded": True}
        assert records["/z.txt"] == {"fileKey": "/z.txt", "metadata": {}, "attributes": {},
                                     "expanded": True}
        assert "expanded" not in records["/"]
        assert group["fileExpansion"] == {"selections": ["/"], "expandedFileCount": 3,
                                          "omittedFileCount": 0, "omittedFileCountExact": True}

    def test_a_folder_selection_carries_only_the_files_under_that_folder(self):
        s3 = MagicMock()
        s3.list_objects_v2.side_effect = _list_objects(LISTINGS)
        envelope = _build(EXPAND, s3=s3)
        group = _group(envelope, "db2", "a3")
        assert _keys(group) == ["/", "/folder/", "/folder/m.mtl", "/folder/m.obj"]
        assert group["fileExpansion"]["selections"] == ["/folder/"]
        prefixes = [c.kwargs["Prefix"] for c in s3.list_objects_v2.call_args_list]
        assert "assets/a3/folder/" in prefixes
        assert "assets/a3/" not in prefixes

    def test_a_file_selection_is_not_expanded(self):
        envelope = _build(EXPAND)
        group = _group(envelope, "db1", "a1")
        # a1's sibling y.glb is not added: only '/' and folder selections expand, while the whole-asset
        # selection of a2 in the same run is.
        assert _keys(group) == ["/", "/x.glb"]
        assert "fileExpansion" not in group
        assert "expanded" not in group["files"][1]
        assert "fileExpansion" in _group(envelope, "db1", "a2")

    def test_the_listing_reads_current_objects_only(self):
        # Archived files are delete markers, which ListObjectsV2 never returns; listing versions would
        # bring them back.
        s3 = MagicMock()
        s3.list_objects_v2.side_effect = _list_objects(LISTINGS)
        _build(EXPAND, s3=s3)
        assert s3.list_objects_v2.called
        s3.list_object_versions.assert_not_called()

    def test_preview_files_are_skipped(self):
        envelope = _build(EXPAND)
        keys = _keys(_group(envelope, "db1", "a2"))
        assert "/b.glb" in keys
        assert not any(".previewFile." in key for key in keys)

    def test_an_explicitly_selected_file_keeps_its_own_record_once(self):
        inputs = [{"databaseId": "db1", "assetId": "a2", "relativeFileKey": "/", "versionId": ""},
                  {"databaseId": "db1", "assetId": "a2", "relativeFileKey": "/b.glb", "versionId": ""}]
        envelope = _build(EXPAND, inputs=inputs, sources=(), databases=())
        group = _group(envelope, "db1", "a2")
        assert _keys(group).count("/b.glb") == 1
        record = next(r for r in group["files"] if r["fileKey"] == "/b.glb")
        assert "expanded" not in record
        assert group["fileExpansion"]["expandedFileCount"] == 2
        assert _keys(group) == ["/", "/b.glb", "/sub/c.usd", "/z.txt"]

    def test_a_whole_asset_selection_covers_a_folder_selection_of_the_same_asset(self):
        s3 = MagicMock()
        s3.list_objects_v2.side_effect = _list_objects(LISTINGS)
        inputs = [{"databaseId": "db1", "assetId": "a2", "relativeFileKey": "/sub/", "versionId": ""},
                  {"databaseId": "db1", "assetId": "a2", "relativeFileKey": "/", "versionId": ""}]
        envelope = _build(EXPAND, inputs=inputs, sources=(), databases=(), s3=s3)
        group = _group(envelope, "db1", "a2")
        assert _keys(group).count("/sub/c.usd") == 1
        assert group["fileExpansion"]["selections"] == ["/"]
        assert s3.list_objects_v2.call_count == 1

    def test_with_file_metadata_and_attributes_off_nothing_is_listed(self):
        s3 = MagicMock()
        envelope = _build({"assetFileMetadata": True, "fileMetadata": False, "fileAttributes": False},
                          s3=s3)
        s3.list_objects_v2.assert_not_called()
        assert "fileExpansion" not in _group(envelope, "db1", "a2")
        # One of the two is enough for the expansion to run.
        envelope = _build({"assetFileMetadata": True, "fileMetadata": False})
        assert "fileExpansion" in _group(envelope, "db1", "a2")

    def test_attributes_only_records_follow_the_file_gates(self):
        envelope = _build({"assetFileMetadata": True, "fileMetadata": False})
        record = next(r for r in _group(envelope, "db1", "a2")["files"] if r["fileKey"] == "/b.glb")
        # fileMetadata off at intake reads no metadata for it; attributes are still read.
        assert record["metadata"] == {}
        assert record["attributes"] == {"units": "cm"}

    def test_the_metadata_reads_go_through_the_metadata_service_with_the_callers_event(self):
        event = {"requestContext": {"authorizer": {"jwt": {"claims": {"vams:tokens": '["u1"]'}}}}}
        calls = []
        with patch(f"{MOD}._fetch_metadata", return_value=[]), \
             patch(f"{MOD}._fetch_database_metadata", return_value=[]), \
             patch(f"{MOD}._fetch_file_metadata",
                   side_effect=lambda d, a, r, kind, e: calls.append((r, kind, e)) or []), \
             patch(f"{MOD}._asset_bucket_details", return_value={"bucketName": "bkt"}), \
             patch(f"{MOD}.s3c") as s3:
            s3.list_objects_v2.side_effect = _list_objects(LISTINGS)
            inputs = [GOLDEN_INPUTS[1]]
            ew._build_grouped_metadata(inputs, _asset_records(inputs), EXPAND, event)
        assert sorted({(r, kind) for r, kind, _e in calls}) == [
            ("/b.glb", "attribute"), ("/b.glb", "metadata"), ("/sub/c.usd", "attribute"),
            ("/sub/c.usd", "metadata"), ("/z.txt", "attribute"), ("/z.txt", "metadata")]
        assert len(calls) == 6
        assert all(e is event for _r, _k, e in calls)


@pytest.mark.unit
class TestExpansionBounds:

    @staticmethod
    def _many(count, root="assets/a2/"):
        return {root: [f"{root}f{i:05d}.glb" for i in range(count)]}

    def test_an_asset_over_the_cap_launches_with_the_capped_files_and_a_warning(self):
        over = ew.MAX_ASSET_FILE_EXPANSION_PER_SELECTION + 37
        notices = []
        inputs = [GOLDEN_INPUTS[1]]
        envelope = _build(EXPAND, inputs=inputs, sources=(), databases=(),
                          listings=self._many(over), notices=notices)
        group = _group(envelope, "db1", "a2")
        assert len(group["files"]) == 1 + ew.MAX_ASSET_FILE_EXPANSION_PER_SELECTION
        # The first files in key order, so a re-run expands the same subset.
        assert group["files"][1]["fileKey"] == "/f00000.glb"
        assert group["fileExpansion"]["omittedFileCount"] == 37
        assert group["fileExpansion"]["omittedFileCountExact"] is True
        assert len(notices) == 1
        assert "db1:a2:/ (37 files left out)" in notices[0]
        assert str(ew.MAX_ASSET_FILE_EXPANSION_PER_SELECTION) in notices[0]

    def test_the_warning_names_each_selection_as_database_asset_and_selection(self):
        # A folder of a3 over the cap and the whole of a2 with no budget left: both labels keep the
        # asset id and the selection apart with the same separator as the database and the asset.
        listings = {**self._many(ew.MAX_ASSET_FILE_EXPANSION_PER_SELECTION + 1, "assets/a3/folder/"),
                    **self._many(3)}
        inputs = [{"databaseId": "db2", "assetId": "a3", "relativeFileKey": "/folder/", "versionId": ""},
                  {"databaseId": "db1", "assetId": "a2", "relativeFileKey": "/", "versionId": ""}]
        notices = []
        with patch(f"{MOD}.MAX_INPUT_FILES_PER_EXECUTION", ew.MAX_ASSET_FILE_EXPANSION_PER_SELECTION):
            _build(EXPAND, inputs=inputs, sources=(), databases=(), listings=listings, notices=notices)
        assert len(notices) == 1
        assert "db2:a3:/folder/ (1 file left out)" in notices[0]
        assert "db1:a2:/ (not expanded)" in notices[0]
        assert "a3/folder/" not in notices[0] and "a2/ " not in notices[0]

    def test_a_listing_that_stops_at_the_scan_bound_reports_a_lower_bound(self):
        page = {"Contents": [{"Key": f"assets/a2/p{i:04d}.glb"} for i in range(600)],
                "IsTruncated": True, "NextContinuationToken": "t"}
        s3 = MagicMock()
        s3.list_objects_v2.return_value = page
        notices = []
        with patch(f"{MOD}.MAX_ASSET_FILE_EXPANSION_SCAN_KEYS", 1200):
            envelope = _build(EXPAND, inputs=[GOLDEN_INPUTS[1]], sources=(), databases=(), s3=s3,
                              notices=notices)
        assert s3.list_objects_v2.call_count == 2
        assert _group(envelope, "db1", "a2")["fileExpansion"]["omittedFileCountExact"] is False
        assert "at least 700 files left out" in notices[0]

    def test_the_execution_budget_counts_explicit_files_and_spans_selections(self):
        inputs = [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/x.glb", "versionId": ""},
            {"databaseId": "db1", "assetId": "a2", "relativeFileKey": "/", "versionId": ""},
            {"databaseId": "db2", "assetId": "a3", "relativeFileKey": "/folder/", "versionId": ""},
        ]
        notices = []
        with patch(f"{MOD}.MAX_INPUT_FILES_PER_EXECUTION", 3):
            envelope = _build(EXPAND, inputs=inputs, sources=(), databases=(), notices=notices)
        # One explicit file leaves two expanded records for the whole run; the folder gets none.
        assert len([r for g in envelope["assets"] for r in g["files"] if r.get("expanded")]) == 2
        assert "db2:a3:/folder/ (not expanded)" in notices[0]
        assert "db1:a2:/ (1 file left out)" in notices[0]

    def test_an_asset_without_a_location_is_not_listed(self):
        # Listing an empty prefix would read the whole bucket, so only the asset with a location is.
        s3 = MagicMock()
        s3.list_objects_v2.side_effect = _list_objects(LISTINGS)
        inputs = [GOLDEN_INPUTS[1],
                  {"databaseId": "db2", "assetId": "a3", "relativeFileKey": "/folder/", "versionId": ""}]
        records = _asset_records(inputs)
        records[("db1", "a2")]["assetLocation"] = {}
        with patch(f"{MOD}._fetch_metadata", return_value=[]), \
             patch(f"{MOD}._fetch_file_metadata", return_value=[]), \
             patch(f"{MOD}._asset_bucket_details", return_value={"bucketName": "bkt"}), \
             patch(f"{MOD}.s3c", s3):
            envelope = ew._build_grouped_metadata(inputs, records, EXPAND, {})
        assert [c.kwargs["Prefix"] for c in s3.list_objects_v2.call_args_list] == ["assets/a3/folder/"]
        assert _keys(_group(envelope, "db1", "a2")) == ["/"]
        assert "fileExpansion" in _group(envelope, "db2", "a3")


@pytest.mark.unit
class TestExpandedRowsInTheBudgetAndTheRecords:

    def test_expanded_rows_give_way_before_the_selected_files(self):
        envelope = _build(EXPAND)
        rows = ew._metadata_budget_rows(envelope["assets"], envelope.get("databases") or [])
        labels = [label for label, _c, _k in rows]
        selected = labels.index("file db1:a1/x.glb metadata")
        expanded = labels.index("file db1:a2/b.glb metadata")
        assert selected < expanded
        assert labels.index("file db1:a1/x.glb attributes") < expanded

    def test_an_expanded_row_belongs_to_the_pipeline_that_received_its_selection(self):
        envelope = _build(EXPAND)
        whole = [GOLDEN_INPUTS[1]]
        rows = list(er.pipeline_metadata_envelope_rows(envelope, whole))
        paths = {(r["assetId"], r["filePath"]) for r in rows}
        assert ("a2", "/b.glb") in paths and ("a2", "/sub/c.usd") in paths
        assert all(r.get("expanded") for r in rows if r["filePath"] not in ("/",) and r["assetId"] == "a2")

    def test_a_pipeline_that_received_only_a_file_gets_no_expanded_rows(self):
        envelope = _build(EXPAND)
        rows = list(er.pipeline_metadata_envelope_rows(envelope, [GOLDEN_INPUTS[0]]))
        assert not any(r.get("expanded") for r in rows)
        # while the one that received the whole-asset selection does.
        rows = list(er.pipeline_metadata_envelope_rows(envelope, [GOLDEN_INPUTS[1]]))
        assert any(r.get("expanded") for r in rows)

    def test_a_folder_selection_owns_only_the_rows_under_it(self):
        envelope = _build(EXPAND, inputs=[GOLDEN_INPUTS[1]], sources=(), databases=())
        # A pipeline whose filters admitted only a folder selection of the same asset.
        received = [{"databaseId": "db1", "assetId": "a2", "relativeFileKey": "/sub/"}]
        rows = list(er.pipeline_metadata_envelope_rows(envelope, received))
        assert {r["filePath"] for r in rows if r.get("expanded")} == {"/sub/c.usd"}


#######################
# Step narrowing (the two-level gate)
#######################

@pytest.mark.unit
class TestStepNarrowingOfTheOptInSections:

    def _envelope(self):
        envelope = _build(EXPAND)
        envelope["metadataSchemas"] = [er.build_metadata_schema_entry("db1")]
        return envelope

    def test_a_step_that_does_not_ask_for_the_expansion_receives_the_selection_without_it(self):
        envelope = self._envelope()
        narrowed = er.narrow_metadata_envelope(envelope, {"metadataSchema": True})
        assert narrowed is not envelope
        group = _group(narrowed, "db1", "a2")
        assert _keys(group) == ["/"]
        assert "fileExpansion" not in group
        # The caller's own file selection is untouched.
        assert _keys(_group(narrowed, "db1", "a1")) == ["/", "/x.glb"]
        assert "metadataSchemas" in narrowed

    def test_a_step_that_asks_for_the_expansion_receives_it(self):
        envelope = self._envelope()
        narrowed = er.narrow_metadata_envelope(envelope, {"assetFileMetadata": True,
                                                          "metadataSchema": True})
        assert narrowed is envelope

    def test_a_step_that_does_not_ask_for_the_schema_receives_no_schema_section(self):
        envelope = self._envelope()
        narrowed = er.narrow_metadata_envelope(envelope, {"assetFileMetadata": True})
        assert "metadataSchemas" not in narrowed
        assert "fileExpansion" in _group(narrowed, "db1", "a2")

    def test_narrowing_does_not_mutate_the_shared_envelope(self):
        envelope = self._envelope()
        before = json.dumps(envelope)
        er.narrow_metadata_envelope(envelope, {})
        assert json.dumps(envelope) == before

    def test_a_type_gate_still_applies_to_expanded_records(self):
        narrowed = er.narrow_metadata_envelope(self._envelope(), {"assetFileMetadata": True,
                                                                  "metadataSchema": True,
                                                                  "fileAttributes": False})
        record = next(r for r in _group(narrowed, "db1", "a2")["files"] if r["fileKey"] == "/b.glb")
        assert "attributes" not in record and record["expanded"] is True


@pytest.mark.unit
class TestIntakeGate:
    """The shared envelope gathers an opt-in section only when the workflow enables it AND some step
    asks for it, since a section no step can receive costs reads for nothing."""

    @staticmethod
    def _records(*gates):
        return [{"databaseId": "db1", "pipelineId": f"p{i}",
                 "systemConfig": {"metadataInputs": gate}} for i, gate in enumerate(gates)]

    def test_no_step_asking_turns_the_key_off_at_intake(self):
        gate = ew._intake_metadata_inputs(
            {"assetFileMetadata": True, "metadataSchema": True}, self._records({}, {}), {})
        assert gate["assetFileMetadata"] is False and gate["metadataSchema"] is False

    def test_one_step_asking_keeps_it_on(self):
        gate = ew._intake_metadata_inputs(
            {"assetFileMetadata": True}, self._records({}, {"assetFileMetadata": True}), {})
        assert gate["assetFileMetadata"] is True

    def test_a_template_override_counts_as_the_step_asking(self):
        records = self._records({})
        resolved = {"db1:p0": {"templateOverrides": {"metadataInputs": {"metadataSchema": True}}}}
        gate = ew._intake_metadata_inputs({"metadataSchema": True}, records, resolved)
        assert gate["metadataSchema"] is True

    def test_the_workflow_gate_off_wins_over_every_step(self):
        gate = ew._intake_metadata_inputs({}, self._records({"assetFileMetadata": True}), {})
        assert er.metadata_input_enabled(gate, "assetFileMetadata") is False

    def test_the_four_metadata_types_keep_the_workflow_gate(self):
        workflow_gate = {"fileMetadata": False}
        gate = ew._intake_metadata_inputs(workflow_gate, self._records({}), {})
        assert gate == workflow_gate


#######################
# metadataSchema
#######################

def _schema(database_id, name, entity_type, fields, restriction=""):
    """A schema record as load_enabled_schemas returns it: fields keyed by name."""
    return {"databaseId": database_id, "schemaName": name, "metadataSchemaEntityType": entity_type,
            "fileKeyTypeRestriction": restriction,
            "fields": {f["metadataFieldKeyName"]: f for f in fields},
            "metadataSchemaFields": {f["metadataFieldKeyName"]: f for f in fields}}


def _field(name, value_type="string", required=False, controlled=None, default=None, sequence=None):
    return {"metadataFieldKeyName": name, "metadataFieldValueType": value_type, "required": required,
            "controlledListKeys": controlled, "defaultMetadataFieldValue": default,
            "sequence": sequence, "dependsOnFieldKeyName": None}


SCHEMAS = {
    ("db1", "assetMetadata"): [_schema("db1", "AssetInfo", "assetMetadata", [
        _field("category", "inline_controlled_list", True, ["part", "scene"], "part", 0)])],
    ("GLOBAL", "assetMetadata"): [_schema("GLOBAL", "Org", "assetMetadata", [_field("owner", sequence=1)])],
    ("db1", "fileMetadata"): [
        _schema("db1", "Mesh", "fileMetadata", [_field("material")], ".glb,.usd"),
        _schema("db1", "Docs", "fileMetadata", [_field("language")], ""),
    ],
    ("GLOBAL", "fileMetadata"): [],
    ("db1", "fileAttribute"): [_schema("db1", "Units", "fileAttribute", [_field("units", required=True)],
                                       ".all")],
    ("GLOBAL", "fileAttribute"): [],
}


class _Casbin:
    """A CasbinEnforcer stand-in: `api` answers enforceAPI; `deny` lists the (databaseId, schemaName)
    pairs the per-object check refuses ('' name = the provisional object of a database)."""

    def __init__(self, api=True, deny=()):
        self.api = api
        self.deny = set(deny)
        self.api_probes = []

    def __call__(self, _claims):
        return self

    def enforceAPI(self, probe, *_a):
        self.api_probes.append(probe)
        return self.api

    def enforce(self, obj, _action):
        if obj.get("object__type") != "metadataSchema":
            return True
        return (obj.get("databaseId"), obj.get("metadataSchemaName")) not in self.deny


def _build_schema(casbin, schemas=SCHEMAS, restrict=True, inputs=None, notices=None,
                  load_error=None):
    loader_calls = []

    def _load(database_id, entity_type, _client, _table):
        loader_calls.append((database_id, entity_type))
        if load_error is not None:
            raise load_error
        return [dict(s) for s in schemas.get((database_id, entity_type), [])]

    table = MagicMock()
    table.get_item.return_value = {"Item": {"restrictMetadataOutsideSchemas": restrict}}
    dynamodb = MagicMock()
    dynamodb.Table.return_value = table
    envelope = _build(
        {"metadataSchema": True}, inputs=inputs or [GOLDEN_INPUTS[0]], sources=(), databases=(),
        notices=notices,
        extra_patches=[patch(f"{MOD}.CasbinEnforcer", casbin),
                       patch(f"{MOD}.claims_and_roles", {"tokens": ["user1"]}),
                       patch(f"{MOD}.load_enabled_schemas", side_effect=_load),
                       patch(f"{MOD}.dynamodb", dynamodb)])
    return envelope, loader_calls


@pytest.mark.unit
class TestMetadataSchemaSection:

    def test_the_section_lists_the_database_and_global_fields_and_the_restrict_flag(self):
        envelope, _calls = _build_schema(_Casbin())
        assert envelope["metadataSchemas"] == [{
            "databaseId": "db1",
            "restrictMetadataOutsideSchemas": True,
            "assetMetadata": [
                {"metadataFieldKeyName": "category", "metadataFieldValueType": "inline_controlled_list",
                 "required": True, "controlledListKeys": ["part", "scene"],
                 "defaultMetadataFieldValue": "part", "metadataSchemaMultiFieldConflict": False},
                {"metadataFieldKeyName": "owner", "metadataFieldValueType": "string",
                 "required": False, "controlledListKeys": [], "defaultMetadataFieldValue": None,
                 "metadataSchemaMultiFieldConflict": False},
            ],
            "fileMetadata": [
                {"metadataFieldKeyName": "language", "metadataFieldValueType": "string",
                 "required": False, "controlledListKeys": [], "defaultMetadataFieldValue": None,
                 "metadataSchemaMultiFieldConflict": False, "fileKeyTypeRestriction": []},
                {"metadataFieldKeyName": "material", "metadataFieldValueType": "string",
                 "required": False, "controlledListKeys": [], "defaultMetadataFieldValue": None,
                 "metadataSchemaMultiFieldConflict": False, "fileKeyTypeRestriction": [".glb", ".usd"]},
            ],
            "fileAttributes": [
                {"metadataFieldKeyName": "units", "metadataFieldValueType": "string",
                 "required": True, "controlledListKeys": [], "defaultMetadataFieldValue": None,
                 "metadataSchemaMultiFieldConflict": False, "fileKeyTypeRestriction": []},
            ],
        }]

    def test_a_field_lists_one_entry_per_reach_with_the_every_file_entry_first(self):
        schemas = {
            ("db1", "fileMetadata"): [_schema("db1", "ObjOnly", "fileMetadata",
                                              [_field("weight", required=True)], ".obj")],
            ("GLOBAL", "fileMetadata"): [_schema("GLOBAL", "Org", "fileMetadata",
                                                 [_field("weight", "number")], "")],
        }
        envelope, _calls = _build_schema(_Casbin(), schemas=schemas)
        weight = [f for f in envelope["metadataSchemas"][0]["fileMetadata"]
                  if f["metadataFieldKeyName"] == "weight"]
        assert [(f["metadataFieldValueType"], f["required"], f["metadataSchemaMultiFieldConflict"],
                 f["fileKeyTypeRestriction"]) for f in weight] == [
            ("number", False, False, []),
            ("string", False, True, [".obj"]),
        ]

    def test_with_the_key_absent_there_is_no_schema_section_and_no_schema_read(self):
        loader = MagicMock()
        with patch(f"{MOD}.load_enabled_schemas", loader):
            envelope = _build({})
        assert "metadataSchemas" not in envelope
        loader.assert_not_called()

    def test_a_database_without_schemas_still_has_an_entry(self):
        envelope, _calls = _build_schema(_Casbin(), schemas={}, restrict=False)
        assert envelope["metadataSchemas"] == [er.build_metadata_schema_entry("db1")]

    def test_each_scope_and_entity_type_is_read_once(self):
        inputs = [GOLDEN_INPUTS[0],
                  {"databaseId": "db1", "assetId": "a2", "relativeFileKey": "/", "versionId": ""}]
        _envelope, calls = _build_schema(_Casbin(), inputs=inputs)
        assert sorted(calls) == sorted({(d, t) for d in ("db1", "GLOBAL")
                                        for t in ("assetMetadata", "fileMetadata", "fileAttribute")})

    def test_the_api_check_carries_the_identity_the_request_arrived_with(self):
        casbin = _Casbin()
        _build_schema(casbin)
        assert casbin.api_probes[0]["requestContext"]["http"] == {"method": "GET",
                                                                  "path": "/metadataschema"}


@pytest.mark.unit
class TestMetadataSchemaAuthorization:
    """A caller who may run the workflow but may not read the schema gets a run with no schema section
    and a generic warning, never a failed launch, and nothing that says whether a schema exists."""

    def test_a_caller_without_the_schema_api_gets_no_section_and_a_generic_warning(self):
        notices = []
        envelope, calls = _build_schema(_Casbin(api=False), notices=notices)
        assert "metadataSchemas" not in envelope
        assert calls == []
        assert notices == ["This execution's input metadata carries no metadata schema: metadata "
                           "schemas could not be read for this caller."]

    @pytest.mark.parametrize("denied_scope", ["db1", "GLOBAL"])
    def test_a_denied_scope_leaves_the_database_out_with_a_generic_warning(self, denied_scope):
        notices = []
        envelope, calls = _build_schema(_Casbin(deny={(denied_scope, "")}), notices=notices)
        assert "metadataSchemas" not in envelope
        assert calls == []
        assert notices == ["This execution's input metadata carries no metadata schema for database "
                           "db1: the schema could not be read."]

    def test_the_answer_does_not_depend_on_whether_a_schema_exists(self):
        outcomes = []
        for schemas in (SCHEMAS, {}):
            notices = []
            envelope, _calls = _build_schema(_Casbin(deny={("db1", "")}), schemas=schemas,
                                             notices=notices)
            outcomes.append((json.dumps(envelope), notices))
        assert outcomes[0] == outcomes[1]

    def test_a_schema_the_callers_rules_exclude_by_name_is_dropped_silently(self):
        notices = []
        envelope, _calls = _build_schema(_Casbin(deny={("db1", "Mesh")}), notices=notices)
        file_fields = [f["metadataFieldKeyName"] for f in envelope["metadataSchemas"][0]["fileMetadata"]]
        assert file_fields == ["language"]
        assert notices == []

    def test_an_unreadable_schema_gives_the_same_generic_warning_as_a_denial(self):
        denied, failed = [], []
        _build_schema(_Casbin(deny={("db1", "")}), notices=denied)
        # The handler's own SchemaLookupError: a test session may load the module twice (as
        # `common.*` and as `backend.backend.common.*`), and only the handler's class is caught.
        envelope, _calls = _build_schema(_Casbin(), notices=failed,
                                         load_error=ew.SchemaLookupError("query did not complete"))
        assert "metadataSchemas" not in envelope
        assert failed == denied

    def test_a_cross_call_launch_probes_with_its_cross_call_identity(self):
        casbin = _Casbin()
        event = {"lambdaCrossCall": {"userName": "SYSTEM_USER"}}
        with patch(f"{MOD}.CasbinEnforcer", casbin), \
             patch(f"{MOD}.claims_and_roles", {"tokens": ["SYSTEM_USER"]}):
            assert ew._caller_may_list_metadata_schemas(event) is True
        assert casbin.api_probes == [{"lambdaCrossCall": {"userName": "SYSTEM_USER"}}]

    def test_empty_tokens_fail_closed(self):
        casbin = _Casbin()
        with patch(f"{MOD}.CasbinEnforcer", casbin), patch(f"{MOD}.claims_and_roles", {"tokens": []}):
            assert ew._caller_may_list_metadata_schemas({}) is False
        assert casbin.api_probes == []


@pytest.mark.unit
class TestMetadataSchemaBounds:

    def test_databases_past_the_cap_are_named_in_a_warning(self):
        inputs = [{"databaseId": f"d{i:02d}", "assetId": f"a{i}", "relativeFileKey": "/x.glb",
                   "versionId": ""} for i in range(ew.MAX_METADATA_SCHEMA_DATABASES_PER_EXECUTION + 2)]
        notices = []
        envelope, _calls = _build_schema(_Casbin(), schemas={}, inputs=inputs, notices=notices)
        assert len(envelope["metadataSchemas"]) == ew.MAX_METADATA_SCHEMA_DATABASES_PER_EXECUTION
        assert len(notices) == 1 and "2 databases" in notices[0]

    def test_an_entry_past_the_byte_cap_is_left_out_whole(self):
        notices = []
        with patch(f"{MOD}.MAX_METADATA_SCHEMA_SECTION_BYTES", 10):
            envelope, _calls = _build_schema(_Casbin(), notices=notices)
        assert "metadataSchemas" not in envelope
        assert "database db1" in notices[0]


@pytest.mark.unit
class TestSchemaFieldCatalog:

    def test_extension_restriction_reads_the_schema_the_way_the_filter_does(self):
        assert msv.schema_file_extension_restriction({"fileKeyTypeRestriction": ".GLB, .usd"}) == [
            ".glb", ".usd"]
        assert msv.schema_file_extension_restriction({"fileKeyTypeRestriction": ".glb,.all"}) == []
        assert msv.schema_file_extension_restriction({"fileKeyTypeRestriction": "  "}) == []
        assert msv.schema_file_extension_restriction({"fileExtensionRestrictions": ".obj"}) == [".obj"]

    @staticmethod
    def _reaches(schemas, name="material"):
        return [(definition["metadataFieldValueType"], definition["required"],
                 definition["metadataSchemaMultiFieldConflict"], reach)
                for definition, reach in msv.aggregate_schema_field_catalog(schemas)[name]]

    def test_agreeing_schemas_share_one_entry_whatever_their_reach(self):
        restricted = _schema("db1", "A", "fileMetadata", [_field("material")], ".glb")
        other = _schema("db1", "B", "fileMetadata", [_field("material")], ".usd")
        unrestricted = _schema("db1", "C", "fileMetadata", [_field("material")], "")
        assert self._reaches([restricted, other]) == [("string", False, False, [".glb", ".usd"])]
        assert self._reaches([restricted, unrestricted]) == [("string", False, False, [])]
        assert self._reaches([unrestricted, restricted]) == [("string", False, False, [])]

    def test_a_restricted_definition_does_not_conflict_with_files_it_never_reaches(self):
        # A GLOBAL field for every file and a database field restricted to .obj: a .glb file is
        # validated against the GLOBAL definition only, an .obj file against both.
        global_schema = _schema("GLOBAL", "G", "fileMetadata", [_field("material", "number")], "")
        obj_schema = _schema("db1", "O", "fileMetadata", [_field("material", required=True)], ".obj")
        assert self._reaches([obj_schema, global_schema]) == [
            ("number", False, False, []),
            ("string", False, True, [".obj"]),
        ]

    def test_restricted_definitions_that_never_meet_are_reported_apart(self):
        glb = _schema("db1", "A", "fileMetadata", [_field("material")], ".glb")
        usd = _schema("db1", "B", "fileMetadata", [_field("material", "number")], ".usd")
        assert self._reaches([glb, usd]) == [("string", False, False, [".glb"]),
                                             ("number", False, False, [".usd"])]

    def test_restricted_definitions_that_meet_on_an_extension_conflict_there_only(self):
        both = _schema("db1", "A", "fileMetadata", [_field("material")], ".glb,.usd")
        glb = _schema("db1", "B", "fileMetadata", [_field("material", "number")], ".glb")
        assert self._reaches([both, glb]) == [("string", False, True, [".glb"]),
                                              ("string", False, False, [".usd"])]

    def test_asset_metadata_is_resolved_over_every_schema(self):
        # The service never filters asset metadata by file, so a stray restriction is ignored.
        a = _schema("db1", "A", "assetMetadata", [_field("owner")], ".obj")
        b = _schema("db1", "B", "assetMetadata", [_field("owner", "number")], "")
        catalog = msv.aggregate_schema_field_catalog([a, b], by_file_extension=False)
        [(definition, reach)] = catalog["owner"]
        assert definition["metadataSchemaMultiFieldConflict"] is True and reach == []

    def test_a_conflict_is_reported_with_the_vams_resolution(self):
        a = _schema("db1", "A", "fileMetadata", [_field("material", required=True)])
        b = _schema("db1", "B", "fileMetadata", [_field("material", "number")])
        [(definition, _reach)] = msv.aggregate_schema_field_catalog([a, b])["material"]
        field = er.build_metadata_schema_field("material", definition, [])
        assert field["metadataSchemaMultiFieldConflict"] is True
        assert field["metadataFieldValueType"] == "string" and field["required"] is False

    def test_a_file_with_no_extension_takes_the_every_file_entry_as_vams_validates_it(self):
        # A GLOBAL every-file number field, the database's .obj schema defining it as a required
        # string, and a .glb schema with a required field of its own: a file with no extension is
        # validated against the GLOBAL schema only, so it gets the number and no .glb field.
        global_schema = _schema("GLOBAL", "G", "fileMetadata", [_field("weight", "number")], "")
        obj_schema = _schema("db1", "O", "fileMetadata", [_field("weight", required=True)], ".obj")
        glb_schema = _schema("db1", "B", "fileMetadata", [_field("note", required=True)], ".glb")
        schemas = [obj_schema, glb_schema, global_schema]
        catalog = msv.aggregate_schema_field_catalog(schemas)
        with patch.object(msv, "load_enabled_schemas", side_effect=lambda database_id, *_a: [
                s for s in schemas if s["databaseId"] == database_id]), \
             patch.object(msv, "_get_from_cache", return_value=None), \
             patch.object(msv, "_set_in_cache"):
            for file_path in ("README", "/a.b/LICENSE"):
                assert msv.extract_file_extension(file_path) is None
                applied = msv.get_aggregated_schemas(
                    ["db1", "GLOBAL"], "fileMetadata", file_path, None, "t")
                picked = {name: definition for name, reaches in catalog.items()
                          for definition, reach in reaches if not reach}
                assert sorted(applied) == sorted(picked) == ["weight"], file_path
                assert applied["weight"]["metadataFieldValueType"] == "number"
                assert picked["weight"]["metadataFieldValueType"] == "number"
                assert picked["weight"]["metadataSchemaMultiFieldConflict"] is False

    # Every ordered pair and triple of these schemas, judged for files of each extension and for files
    # with none (at the root and under a folder whose name has a dot).
    _RESTRICTIONS = ["", ".glb", ".obj", ".glb,.obj", ".all"]
    _DEFINITIONS = [("string", False), ("number", False), ("string", True)]
    _FILES = [("models/part.glb", ".glb"), ("models/part.obj", ".obj"), ("models/part.usd", ".usd"),
              ("README", None), ("a.b/LICENSE", None)]

    @classmethod
    def _schema_sets(cls):
        import itertools
        variants = [(r, d) for r in cls._RESTRICTIONS for d in cls._DEFINITIONS]
        for size in (1, 2, 3):
            for combo in itertools.product(variants, repeat=size):
                yield [_schema("db1" if i % 2 == 0 else "GLOBAL", f"S{i}", "fileMetadata",
                               [_field("material", value_type, required)], restriction)
                       for i, (restriction, (value_type, required)) in enumerate(combo)]

    def test_the_entry_a_file_picks_is_what_the_metadata_service_applies(self):
        """For every combination above and each file, the entry the custom pipeline guide's rule picks
        (the one listing the file's extension, else the one with an empty list, else none) equals the
        field the metadata service validates that file against. A file with no extension lists in no
        entry, so it takes the empty-list one."""
        for file_path, extension in self._FILES:
            assert msv.extract_file_extension(file_path) == extension
        cases = 0
        for schemas in self._schema_sets():
            # The execute path loads the database's schemas, then GLOBAL's, as the service does.
            ordered = ([s for s in schemas if s["databaseId"] == "db1"]
                       + [s for s in schemas if s["databaseId"] == "GLOBAL"])
            catalog = msv.aggregate_schema_field_catalog(ordered)
            reaches = catalog.get("material", [])
            with patch.object(msv, "load_enabled_schemas",
                              side_effect=lambda database_id, *_a: [
                                  s for s in schemas if s["databaseId"] == database_id]), \
                 patch.object(msv, "_get_from_cache", return_value=None), \
                 patch.object(msv, "_set_in_cache"):
                for file_path, extension in self._FILES:
                    applied = msv.get_aggregated_schemas(
                        ["db1", "GLOBAL"], "fileMetadata", file_path, None, "t")
                    listed = [d for d, reach in reaches if extension in reach]
                    every = [d for d, reach in reaches if not reach]
                    assert len(listed) <= 1 and len(every) <= 1
                    picked = (listed or every or [None])[0]
                    expected = applied.get("material")
                    cases += 1
                    if expected is None:
                        assert picked is None, (schemas, file_path)
                        continue
                    assert picked is not None, (schemas, file_path)
                    for key in ("metadataFieldValueType", "required",
                                "metadataSchemaMultiFieldConflict"):
                        assert picked[key] == expected[key], (key, schemas, file_path)
        assert cases == len(self._FILES) * sum(15 ** n for n in (1, 2, 3))


#######################
# Model validation
#######################

@pytest.mark.unit
class TestOptInKeyValidation:
    """Both keys are accepted on pipelines, template overrides and workflows, only as booleans; an
    unknown key still fails."""

    VALIDATORS = {
        "pipeline": lambda mi: pipeline_models._validate_pipeline_system_config({"metadataInputs": mi}),
        "template overrides": lambda mi: pipeline_models._validate_template_overrides(
            {"metadataInputs": mi}),
        "workflow": lambda mi: workflow_models._validate_system_config_shapes({"metadataInputs": mi}),
    }

    @pytest.mark.parametrize("level", sorted(VALIDATORS))
    @pytest.mark.parametrize("key", ["assetFileMetadata", "metadataSchema"])
    @pytest.mark.parametrize("value", [True, False])
    def test_a_boolean_is_accepted(self, level, key, value):
        self.VALIDATORS[level]({key: value})

    @pytest.mark.parametrize("level", sorted(VALIDATORS))
    @pytest.mark.parametrize("key", ["assetFileMetadata", "metadataSchema"])
    @pytest.mark.parametrize("value", ["true", 1, None, {}])
    def test_a_non_boolean_is_rejected(self, level, key, value):
        with pytest.raises(ValueError, match="must be a boolean"):
            self.VALIDATORS[level]({key: value})

    @pytest.mark.parametrize("level", sorted(VALIDATORS))
    def test_an_unknown_key_still_fails(self, level):
        with pytest.raises(ValueError, match="unknown key"):
            self.VALIDATORS[level]({"assetFilesMetadata": True})

    def test_the_save_check_warns_about_an_expansion_that_would_carry_nothing(self):
        # The workflow's own file types are the run's intake gate, so with both off the run lists
        # nothing and expands nothing, which is what the workflow-level text says.
        workflow_gate = {"assetFileMetadata": True, "fileMetadata": False, "fileAttributes": False}
        _errors, warnings = ev.validate_workflow_save({"metadataInputs": workflow_gate}, [])
        assert warnings == [
            "the workflow uses assetFileMetadata with fileMetadata and fileAttributes both off, so "
            "the per-file records it adds would carry nothing and no files are expanded."]
        step = {"databaseId": "db1", "pipelineId": "p",
                "systemConfig": {"metadataInputs": {"assetFileMetadata": True}}}
        s3 = MagicMock()
        envelope = _build(ew._intake_metadata_inputs(workflow_gate, [step], {}), s3=s3)
        s3.list_objects_v2.assert_not_called()
        assert not any(r.get("expanded") for g in envelope["assets"] for r in g["files"])

    def test_a_pipeline_with_both_file_types_off_is_told_what_its_step_receives(self):
        # A pipeline's gate only narrows what its step receives; the run expands under the workflow's
        # file types. So the pipeline-level text names the empty records, not a skipped expansion.
        step_gate = {"assetFileMetadata": True, "fileMetadata": False, "fileAttributes": False}
        workflow_gate = {"assetFileMetadata": True}
        _errors, warnings = ev.validate_workflow_save(
            {"metadataInputs": workflow_gate},
            [{"pipelineId": "p", "pipelineDatabaseId": "db1",
              "systemConfig": {"metadataInputs": step_gate}}])
        assert warnings == [
            "pipeline 'db1:p' uses assetFileMetadata with fileMetadata and fileAttributes both off, "
            "so the per-file records it receives carry no metadata or attributes."]
        # The same configuration at run time: the selection is expanded, and the step gets each
        # expanded record with neither metadata nor attributes.
        step = {"databaseId": "db1", "pipelineId": "p",
                "systemConfig": {"metadataInputs": step_gate}}
        envelope = _build(ew._intake_metadata_inputs(workflow_gate, [step], {}))
        received = er.narrow_metadata_envelope(envelope, step_gate)
        expanded = [r for r in _group(received, "db1", "a2")["files"] if r.get("expanded")]
        assert [r["fileKey"] for r in expanded] == ["/b.glb", "/sub/c.usd", "/z.txt"]
        assert all(r["metadata"] is None and "attributes" not in r for r in expanded)

    def test_the_save_check_warns_when_the_workflow_gates_off_a_key_a_pipeline_asks_for(self):
        _errors, warnings = ev.validate_workflow_save(
            {"metadataInputs": {}},
            [{"pipelineId": "p", "pipelineDatabaseId": "db1",
              "systemConfig": {"metadataInputs": {"metadataSchema": True}}}])
        assert any("uses metadataSchema but the workflow" in w for w in warnings)

    def test_the_workflow_aggregate_reports_both_keys(self):
        aggregate = ev.aggregate_metadata_inputs(
            {"metadataInputs": {"assetFileMetadata": True}},
            [{"metadataInputs": {"assetFileMetadata": True, "metadataSchema": True}}])
        assert aggregate["assetFileMetadata"] is True
        assert aggregate["metadataSchema"] is False
        assert aggregate["gatedOffByWorkflow"] == ["metadataSchema"]
