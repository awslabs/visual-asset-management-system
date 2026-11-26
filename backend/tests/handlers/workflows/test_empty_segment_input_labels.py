# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A workflow input whose key has an empty segment after the asset's folder keeps its own label (#1977).

In an asset stored at ``A1/``, ``/cloud.las`` names ``A1/cloud.las`` and ``//cloud.las`` names ``A1//cloud.las``
(exactly one leading ``/`` marks a path in the asset). The run read the right object for each, but
``executionRecords.normalize_file_key`` dropped every leading ``/``, so both files carried the label
``/cloud.las``: one manifest ``relativePath``, one metadata record key, one later-step entry, and a later
step's manifest named ``A1//cloud.las`` by the other file's path. The normalizer now adds a ``/`` only when
one is missing, the interim step rebuilds a later step's paths the way the file list gives them, and the
wizard mirror and the pipelines' manifest helper read the labels by the same rule.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common.workflows import executionOutputs as eo
from backend.backend.common.workflows import executionRecords as er
from backend.backend.common.workflows import executionValidation as ev
from backend.tests.handlers.workflows.test_executeWorkflow import ewv2
from backend.tests.handlers.workflows.test_interimPipelineTracking import ipt

LOCATION = "A1/"
ROOT_FILE = "A1/cloud.las"
EMPTY_SEGMENT_FILE = "A1//cloud.las"


@pytest.mark.unit
class TestTheLabelRule:
    @pytest.mark.parametrize("key, expected", [
        ("//cloud.las", "//cloud.las"),
        ("/cloud.las", "/cloud.las"),
        ("cloud.las", "/cloud.las"),
        ("//sub/deep.las", "//sub/deep.las"),
        ("/sub//deep.las", "/sub//deep.las"),
        ("", "/"),
        ("/", "/"),
        ("//", "//"),
    ])
    def test_a_slash_is_added_only_when_missing(self, key, expected):
        assert er.normalize_file_key(key) == expected

    def test_the_empty_named_folder_is_not_the_whole_asset(self):
        # '//' selects the folder the web shows as '/', the keys under 'A1//'; '/' is the whole asset.
        assert ewv2._collapse_selections(["//", "/"]) == ["/"]
        assert ewv2._collapse_selections(["//"]) == ["//"]

    @pytest.mark.parametrize("selection, prefix", [
        ("//", "A1//"), ("//sub/", "A1//sub/"), ("/sub/", "A1/sub/"), ("/", "A1/")])
    def test_a_folder_selection_lists_its_own_folder(self, selection, prefix):
        # metadataInputs.assetFileMetadata expands a folder selection into the files under its prefix
        grouped = {("db1", "A1"): [{"databaseId": "db1", "assetId": "A1", "relativeFileKey": selection}]}
        asset = {"bucketId": "b1", "assetLocation": {"Key": LOCATION}}
        with patch.object(ewv2, "_asset_bucket_details", return_value={"bucketName": "bkt"}), \
                patch.object(ewv2, "_list_selection_files", return_value=([], 0, True)) as listing:
            ewv2._expand_asset_selections([("db1", "A1")], grouped, {("db1", "A1"): asset})
        assert listing.call_args.args[:3] == ("bkt", prefix, LOCATION)


@pytest.mark.unit
class TestTheFirstStep:
    def test_the_manifest_labels_each_file_by_its_own_path(self):
        asset = {"bucketId": "b1", "assetLocation": {"Key": LOCATION}}
        selected = [{"databaseId": "db1", "assetId": "A1", "relativeFileKey": "/cloud.las"},
                    {"databaseId": "db1", "assetId": "A1", "relativeFileKey": "//cloud.las"}]
        with patch.object(ewv2, "_asset_bucket_details", return_value={"bucketName": "bkt"}), \
                patch.object(ewv2, "_asset_table", return_value=MagicMock()), \
                patch.object(ewv2, "folder_input_exclude_locations", return_value=[]):
            entries = ewv2._build_input_manifest_entries(selected, {("db1", "A1"): asset})
        assert [(e["relativePath"], e["key"]) for e in entries] == [
            ("/cloud.las", ROOT_FILE), ("//cloud.las", EMPTY_SEGMENT_FILE)]

    def test_the_metadata_records_are_keyed_apart(self):
        envelope = {"schemaVersion": 2, "assets": [{
            "databaseId": "db1", "assetId": "A1", "assetData": {},
            "files": [er.build_metadata_file_record("/cloud.las", {"m": "root"}),
                      er.build_metadata_file_record("//cloud.las", {"m": "empty-segment"})]}]}
        assert [f["fileKey"] for f in envelope["assets"][0]["files"]] == ["/cloud.las", "//cloud.las"]
        assert er.get_asset_file_record(envelope, "db1", "A1", "//cloud.las")["metadata"] == {"m": "empty-segment"}
        assert er.get_asset_file_record(envelope, "db1", "A1", "/cloud.las")["metadata"] == {"m": "root"}

    def test_a_single_file_later_step_counts_the_two_files(self):
        # The run stores an input row for each, so a later step that takes one file would get both.
        wf = {"inputFileArity": "multi",
              "assetScope": {"crossAssetAllowed": False, "singleAssetOnly": True,
                             "wholeAssetAllowed": False, "folderAllowed": False},
              "inputFileFilters": {"allow": [], "exclude": []}}

        def step(pid, arity):
            return {"pipelineId": pid, "pipelineDatabaseId": "db", "enabled": True, "archived": False,
                    "systemConfig": {"inputFileArity": arity,
                                     "inputFileFilters": {"allow": ["*.las"], "exclude": []}}}

        errs, _ = ev.validate_execution(
            wf, [step("convert", "multi"), step("consume", "one")],
            [{"assetId": "A1", "relativeFileKey": "/cloud.las"},
             {"assetId": "A1", "relativeFileKey": "//cloud.las"}])
        assert len(errs) == 1 and "db:consume" in errs[0] and "2 selected input files" in errs[0]


@pytest.mark.unit
class TestALaterStep:
    @pytest.mark.parametrize("stored_key, root, expected", [
        ("/" + EMPTY_SEGMENT_FILE, LOCATION, "//cloud.las"),
        ("/" + ROOT_FILE, LOCATION, "/cloud.las"),
        ("/A1/", LOCATION, "/"),
        ("/A1//sub/deep.las", LOCATION, "//sub/deep.las"),
        # A single-file location keeps its earlier rule
        ("/A1/model.glb/x.bin", "A1/model.glb", "/x.bin"),
    ])
    def test_the_rebuilt_path_is_the_listed_path(self, stored_key, root, expected):
        assert ipt._relative_to_asset(stored_key, root) == expected

    def test_the_rebuilt_inputs_label_the_two_files_apart(self):
        rows = [{"inputAssetFileKey": "/" + key, "s3Bucket": "bkt", "assetRootS3Key": LOCATION,
                 "databaseId": "db1", "assetId": "A1", "versionId": "v1"}
                for key in (ROOT_FILE, EMPTY_SEGMENT_FILE)]
        table = MagicMock()
        table.query.return_value = {"Items": rows}
        with patch.object(ipt, "dynamodb") as dynamodb:
            dynamodb.Table.return_value = table
            entries = ipt._get_original_input_entries("exec-1")
        assert [(e["relativePath"], e["key"]) for e in entries] == [
            ("/cloud.las", ROOT_FILE), ("//cloud.las", EMPTY_SEGMENT_FILE)]

    def test_an_output_replaces_only_the_file_at_its_write_back_path(self):
        # Output processing writes 'out/files/cloud.las' back to A1/cloud.las, so it shadows that input
        # alone; the empty-segment file keeps its own object.
        listing = [eo._output_file_entry("out/files/cloud.las", "out/files/", "v9", 5)]
        originals = [
            {"relativePath": "/cloud.las", "databaseId": "db1", "assetId": "A1", "bucket": "bkt",
             "key": ROOT_FILE, "versionId": "v1"},
            {"relativePath": "//cloud.las", "databaseId": "db1", "assetId": "A1", "bucket": "bkt",
             "key": EMPTY_SEGMENT_FILE, "versionId": "v1"},
        ]
        resolved = eo.resolve_manifest_input_files(
            MagicMock(), originals, "run-bkt", "out/files/", current_output_files=listing)
        by_path = {e["relativePath"]: e["key"] for e in resolved}
        assert by_path == {"/cloud.las": "out/files/cloud.las", "//cloud.las": EMPTY_SEGMENT_FILE}

    def test_an_output_is_labelled_by_the_path_it_is_written_back_at(self):
        # Write-back drops every leading '/' of an output's path, so 'out/files//x.glb' lands at A1/x.glb.
        assert eo._output_file_entry("out/files//x.glb", "out/files/", "", 1)["relativePath"] == "/x.glb"
