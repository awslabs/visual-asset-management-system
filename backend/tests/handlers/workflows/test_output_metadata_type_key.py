# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Output-metadata provenance rows keep file metadata and file attributes apart (#503).

A pipeline may write the same key onto the same file as both file metadata (`<file>.metadata.json`)
and a file attribute (`<file>.attribute.json`). Both are applied to the asset, so both must be
recorded: the row key carries the metadata type, and the public row says which kind it is.

Rows recorded before the type was part of the key carry `targetFilePath:metadataKey` as the sort-key
value and no `metadataType` attribute. They must still read everywhere: the type is then taken from
the source file the row was recorded from."""

import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

for _k, _v in {
    "S3_ASSET_BUCKETS_STORAGE_TABLE_NAME": "t-buckets",
    "METADATA_SERVICE_LAMBDA_FUNCTION_NAME": "t-md-svc",
    "FILE_UPLOAD_LAMBDA_FUNCTION_NAME": "t-upload",
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "ASSET_UPLOAD_TABLE_NAME": "t-asset-upload",
    "DATABASE_STORAGE_TABLE_NAME": "t-db",
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-cfg",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME": "t-pin-files",
    "PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME": "t-pin-md",
    "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME": "t-pin-cfg",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME": "t-om",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
    "WORKFLOW_STORAGE_TABLE_NAME": "t-workflows",
    "PIPELINE_STORAGE_TABLE_NAME": "t-pipelines",
    "EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME": "t-execv2",
}.items():
    os.environ.setdefault(_k, _v)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.common.workflows import executionRecords as er  # noqa: E402
from backend.backend.handlers.workflows import executionService as le  # noqa: E402
from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po  # noqa: E402
from backend.backend.models import executions as em  # noqa: E402

SORT_KEY = "targetFilePath:metadataKey"
EXEC_MOD = "backend.backend.handlers.workflows.executionService"

RUN_BUCKET = "run-io-bucket"
ASSET_BUCKET = "output-asset-bucket"
METADATA_PREFIX = "pipelines/p1/JOB/output/E1/metadata/"
TARGET = "scan.glb"
SHARED_KEY = "units"


def _real_batch_table(sent):
    """A table whose batch_writer is boto3's own BatchWriter over a client that records every put,
    so overwrite_by_pkeys dedupe is the shipped behaviour rather than a stub's."""
    import boto3.dynamodb.table as ddb_table

    class _Client:
        def batch_write_item(self, RequestItems):
            sent.extend(next(iter(RequestItems.values())))
            return {"UnprocessedItems": {}}

    class _Table:
        name = "t-om"

        def __init__(self):
            self.meta = MagicMock(client=_Client())

        def batch_writer(self, **kwargs):
            return ddb_table.BatchWriter(self.name, self.meta.client, **kwargs)

        def put_item(self, Item):
            pass

        def update_item(self, **kwargs):
            pass

    return _Table()


def _record_through_real_batch_writer(output_metadata):
    """Run record_execution_outputs with the output-metadata table backed by the real BatchWriter and
    return the items it sent."""
    sent = []
    om = _real_batch_table(sent)
    other = MagicMock()
    dynamo = MagicMock()
    dynamo.Table.side_effect = (
        lambda name: om if name == po.pipeline_execution_output_metadata_table else other)
    po.workflow_execution_database_v2 = "t-exec-v2"
    po.record_execution_outputs(
        dynamo=dynamo, workflow_execution_id="E1", end_state_pipeline_execution_id="P1",
        workflow_database_id="wdb", workflow_id="wf", bucket_name="abkt",
        output_files=[], output_metadata=output_metadata, output_results=[],
        result_log="done", execution_log="", log_group_arn="", log_stream_name="",
        execution_status="SUCCEEDED", execution_error="")
    return [r["PutRequest"]["Item"] for r in sent]


def _listing_for(prefix):
    if prefix == METADATA_PREFIX:
        return {"Contents": [
            {"Key": METADATA_PREFIX + TARGET + ".metadata.json", "Size": 9},
            {"Key": METADATA_PREFIX + TARGET + ".attribute.json", "Size": 9},
        ]}
    return {}


def _collected_output_metadata_from_handler():
    """Drive lambda_handler over one file's metadata and attribute files that both write SHARED_KEY,
    and return the output_metadata it hands to record_execution_outputs."""
    def _metadata(_bucket, _s3_key, _mdpath, _db, _asset, _file_path, metadata_type, _ctx):
        return [{"metadataKey": SHARED_KEY, "metadataValue": f"{metadata_type}-value"}]

    with patch.object(po, "lookup_existing_asset",
                      return_value={"databaseId": "db1", "assetId": "asset1", "bucketId": "b1"}), \
            patch.object(po, "get_default_bucket_details",
                         return_value={"bucketId": "b1", "bucketName": ASSET_BUCKET,
                                       "baseAssetsPrefix": "assets/"}), \
            patch.object(po, "_fetch_execution_logs", return_value=("", "")), \
            patch.object(po, "verify_get_path_objects", side_effect=lambda _b, p: _listing_for(p)), \
            patch.object(po, "process_metadata_file", side_effect=_metadata), \
            patch.object(po, "record_execution_outputs") as m_record:
        resp = po.lambda_handler({"body": {
            "outputAssetId": "asset1", "outputDatabaseId": "db1", "workflowExecutionId": "E1",
            "endStatePipelineExecutionId": "P1", "workflowDatabaseId": "wdb", "workflowId": "wf1",
            "workflowExecutionS3InputOutputBucket": RUN_BUCKET,
            "executingUserName": "SYSTEM_USER", "executingRequestContext": {"http": {}},
            "metadataPathKey": METADATA_PREFIX,
        }}, MagicMock())
    assert resp["statusCode"] == 200
    return m_record.call_args.kwargs["output_metadata"]


@pytest.mark.unit
class TestRecordBuilder:
    def test_same_key_as_metadata_and_attribute_builds_two_distinct_row_keys(self):
        meta = er.build_output_metadata_record(
            "P1", "/scan.glb", SHARED_KEY, "m", "scan.glb.metadata.json", metadata_type="metadata")
        attr = er.build_output_metadata_record(
            "P1", "/scan.glb", SHARED_KEY, "a", "scan.glb.attribute.json", metadata_type="attribute")
        assert meta[SORT_KEY] != attr[SORT_KEY]
        assert meta["metadataType"] == "metadata"
        assert attr["metadataType"] == "attribute"
        # The attribute name and the plain fields are unchanged: only the sort-key VALUE gains the type.
        for row in (meta, attr):
            assert row["targetFilePath"] == "/scan.glb" and row["metadataKey"] == SHARED_KEY

    def test_row_key_names_the_type_between_target_and_key(self):
        rec = er.build_output_metadata_record(
            "P1", "/scan.glb", SHARED_KEY, "a", "scan.glb.attribute.json", metadata_type="attribute")
        assert rec[SORT_KEY] == "/scan.glb:attribute:units"

    def test_an_unknown_type_is_refused_rather_than_written_into_the_key(self):
        with pytest.raises(ValueError):
            er.build_output_metadata_record("P1", "/", "k", "v", "", metadata_type="bogus")

    def test_the_record_model_describes_the_type(self):
        rec = er.build_output_metadata_record(
            "P1", "/scan.glb", SHARED_KEY, "a", "scan.glb.attribute.json", metadata_type="attribute")
        model = em.PipelineExecutionOutputMetadataRecord(**rec)
        assert model.metadataType == "attribute"
        assert model.dict(by_alias=True)[SORT_KEY] == rec[SORT_KEY]

    def test_a_legacy_row_still_validates_against_the_record_model(self):
        legacy = {"pipelineExecutionId": "P1", SORT_KEY: "/scan.glb:units",
                  "targetFilePath": "/scan.glb", "metadataKey": SHARED_KEY, "metadataValue": "m",
                  "sourceMetadataFileRelativePath": "scan.glb.metadata.json"}
        model = em.PipelineExecutionOutputMetadataRecord(**legacy)
        assert model.metadataType == ""


@pytest.mark.unit
class TestWriteBackRecordsBothRows:
    def test_the_handler_tags_each_collected_row_with_its_type(self):
        collected = _collected_output_metadata_from_handler()
        types_seen = sorted(row.get("metadataType") for row in collected
                            if row["metadataKey"] == SHARED_KEY)
        assert types_seen == ["attribute", "metadata"], collected

    def test_same_key_as_metadata_and_attribute_is_recorded_as_two_rows(self):
        """The reported defect end to end: handler loop -> real batch writer. Before the fix the two
        rows shared one sort-key value and overwrite_by_pkeys kept only the last."""
        items = _record_through_real_batch_writer(_collected_output_metadata_from_handler())
        rows = [i for i in items if i["metadataKey"] == SHARED_KEY]
        assert len(rows) == 2, items
        assert {r["metadataType"]: r["metadataValue"] for r in rows} == {
            "metadata": "metadata-value", "attribute": "attribute-value"}
        assert len({r[SORT_KEY] for r in rows}) == 2

    def test_a_repeated_key_of_the_same_type_still_collapses_to_one_row(self):
        """Negative control: the type separates kinds, it does not turn a true repeat into two rows."""
        dup = [{"targetFilePath": "/", "metadataKey": "same", "metadataValue": v,
                "metadataType": "metadata", "sourceMetadataFileRelativePath": "asset.metadata.json"}
               for v in ("first", "second")]
        items = _record_through_real_batch_writer(dup)
        assert [i["metadataValue"] for i in items] == ["second"]

    def test_a_descriptor_without_a_type_is_recorded_as_metadata(self):
        items = _record_through_real_batch_writer([
            {"targetFilePath": "/", "metadataKey": "k", "metadataValue": "v",
             "sourceMetadataFileRelativePath": "asset.metadata.json"}])
        assert items[0]["metadataType"] == "metadata"


@pytest.mark.unit
class TestReadersAcceptBothKeyShapes:
    @pytest.mark.parametrize("row,expected", [
        ({"metadataType": "attribute", "sourceMetadataFileRelativePath": "x.metadata.json"},
         "attribute"),
        ({"sourceMetadataFileRelativePath": "out/scan.glb.attribute.json"}, "attribute"),
        ({"sourceMetadataFileRelativePath": "out/scan.glb.metadata.json"}, "metadata"),
        ({"sourceMetadataFileRelativePath": "out/asset.metadata.json"}, "metadata"),
        ({}, "metadata"),
    ], ids=["stored-type-wins", "legacy-attribute-file", "legacy-metadata-file",
            "legacy-asset-file", "legacy-no-source"])
    def test_public_row_names_the_type(self, row, expected):
        base = {"targetFilePath": "/scan.glb", "metadataKey": SHARED_KEY, "metadataValue": "v"}
        assert le._scrub_output_metadata({**base, **row})["metadataType"] == expected

    def test_public_row_keeps_its_existing_fields_and_omits_internal_ones(self):
        out = le._scrub_output_metadata({
            "pipelineExecutionId": "P1", SORT_KEY: "/scan.glb:attribute:units",
            "targetFilePath": "/scan.glb", "metadataKey": SHARED_KEY, "metadataValue": "v",
            "metadataType": "attribute", "sourceMetadataFileRelativePath": "s3/key.attribute.json"})
        assert out == {"targetFilePath": "/scan.glb", "metadataKey": SHARED_KEY,
                       "metadataValue": "v", "metadataType": "attribute"}

    def test_paged_output_collection_walks_legacy_and_new_rows_across_pages(self):
        """One partition holding both key shapes (an execution recorded before the upgrade next to rows
        written after). Paging one row at a time resumes on each row's own sort-key value, so every row
        comes back exactly once whichever shape it has."""
        rows = [
            {"pipelineExecutionId": "pe-p1", SORT_KEY: "/a.glb:units", "targetFilePath": "/a.glb",
             "metadataKey": "units", "metadataValue": "legacy",
             "sourceMetadataFileRelativePath": "m/a.glb.attribute.json"},
            {"pipelineExecutionId": "pe-p1", SORT_KEY: "/scan.glb:attribute:units",
             "targetFilePath": "/scan.glb", "metadataKey": "units", "metadataValue": "a",
             "metadataType": "attribute"},
            {"pipelineExecutionId": "pe-p1", SORT_KEY: "/scan.glb:metadata:units",
             "targetFilePath": "/scan.glb", "metadataKey": "units", "metadataValue": "m",
             "metadataType": "metadata"},
        ]

        class _Table:
            def query(self, **kwargs):
                start = 0
                exclusive = kwargs.get("ExclusiveStartKey")
                if exclusive:
                    start = next(i for i, r in enumerate(rows)
                                 if r[SORT_KEY] == exclusive[SORT_KEY]) + 1
                page = rows[start:start + kwargs["Limit"]]
                resp = {"Items": page}
                if start + len(page) < len(rows):
                    resp["LastEvaluatedKey"] = {"pipelineExecutionId": "pe-p1",
                                                SORT_KEY: page[-1][SORT_KEY]}
                return resp

        steps = [{"pipelineExecutionId": "pe-p1", "pipelineId": "p1", "pipelineDatabaseId": "db"}]
        seen, token = [], None
        with patch(f"{EXEC_MOD}.dynamodb") as ddb, \
                patch(f"{EXEC_MOD}.get_pipeline_execution_rows", return_value=steps):
            ddb.Table.return_value = _Table()
            for _ in range(10):
                page = le.page_detail_metadata("E1", "output", 1, token)
                seen.extend(page["Items"])
                token = page.get("NextToken")
                if not token:
                    break
        assert [(r["metadataValue"], r["metadataType"]) for r in seen] == [
            ("legacy", "attribute"), ("a", "attribute"), ("m", "metadata")]


def _legacy(path, key, value, source):
    return {"pipelineExecutionId": "pe-p1", SORT_KEY: f"{path}:{key}", "targetFilePath": path,
            "metadataKey": key, "metadataValue": value, "sourceMetadataFileRelativePath": source}


def _typed(path, key, value, metadata_type):
    return {"pipelineExecutionId": "pe-p1", SORT_KEY: f"{path}:{metadata_type}:{key}",
            "targetFilePath": path, "metadataKey": key, "metadataValue": value,
            "metadataType": metadata_type}


@pytest.mark.unit
class TestRetryAcrossTheUpgradeListsAnEntryOnce:
    """A step retried across the upgrade leaves the old code's row and the new code's typed row for one
    entry. Reads keep the typed (newer) row and drop the legacy one, matching on (key, type)."""

    def test_a_legacy_row_with_a_typed_twin_is_dropped(self):
        rows = [_legacy("/scan.glb", "units", "old", "m/scan.glb.attribute.json"),
                _typed("/scan.glb", "units", "new", "attribute"),
                _typed("/scan.glb", "units", "mm", "metadata")]
        kept = er.drop_superseded_output_metadata_rows(rows)
        assert [(r["metadataValue"], r.get("metadataType")) for r in kept] == [
            ("new", "attribute"), ("mm", "metadata")]

    def test_the_twin_must_match_the_type_read_from_the_source_file(self):
        """A legacy attribute row is not superseded by a typed METADATA row of the same key."""
        rows = [_legacy("/scan.glb", "units", "m", "m/scan.glb.attribute.json"),
                _typed("/scan.glb", "units", "mm", "metadata")]
        assert er.drop_superseded_output_metadata_rows(rows) == rows

    def test_legacy_rows_of_a_pre_upgrade_execution_are_all_kept(self):
        rows = [_legacy("/a.glb", "units", "m", "m/a.glb.metadata.json"),
                _legacy("/b.glb", "units", "m", "m/b.glb.attribute.json")]
        assert er.drop_superseded_output_metadata_rows(rows) == rows

    def test_a_twin_outside_the_rows_is_found_through_the_callback(self):
        legacy = _legacy("/scan.glb", "units", "old", "m/scan.glb.metadata.json")
        asked = []

        def _exists(unmatched):
            asked.append(list(unmatched))
            return {("pe-p1", "/scan.glb:metadata:units")}

        assert er.drop_superseded_output_metadata_rows([legacy], typed_row_exists=_exists) == []
        assert asked == [[legacy]]

    def test_the_callback_is_not_asked_when_every_twin_is_in_the_rows(self):
        rows = [_legacy("/scan.glb", "units", "old", "m/scan.glb.metadata.json"),
                _typed("/scan.glb", "units", "new", "metadata")]

        def _never(_unmatched):
            raise AssertionError("no lookup needed")

        assert len(er.drop_superseded_output_metadata_rows(rows, typed_row_exists=_never)) == 1

    def test_details_view_lists_the_entry_once(self):
        rows = [_legacy("/scan.glb", "units", "old", "m/scan.glb.metadata.json"),
                _typed("/scan.glb", "units", "new", "metadata"),
                _typed("/scan.glb", "units", "m", "attribute")]
        prows = [{"pipelineExecutionId": "pe-p1", "pipelineId": "p1", "pipelineDatabaseId": "db"}]

        def _capped(table_name, _key_condition, max_items):
            if table_name == le.pipeline_execution_output_metadata_table:
                return rows[:max_items], len(rows) > max_items
            return [], False

        with patch(f"{EXEC_MOD}.get_workflow_definition", return_value={}), \
                patch(f"{EXEC_MOD}.get_pipeline_definition", return_value={}), \
                patch(f"{EXEC_MOD}.get_pipeline_definitions", return_value={}), \
                patch(f"{EXEC_MOD}._query_all", return_value=[]), \
                patch(f"{EXEC_MOD}.get_pipeline_execution_rows", return_value=prows), \
                patch(f"{EXEC_MOD}._query_capped", side_effect=_capped), \
                patch(f"{EXEC_MOD}.get_produced_file_versions", return_value={}):
            details = le.assemble_execution_details(
                "E1", {"workflowId": "wf", "workflowDatabaseId": "db"}, config_row={})
        listed = details["outputs"]["metadata"]
        assert sorted((r["metadataType"], r["metadataValue"]) for r in listed) == [
            ("attribute", "m"), ("metadata", "new")]

    def test_paged_walk_lists_the_entry_once_when_its_twin_is_on_another_page(self):
        """Page size 1: the legacy row comes first and its typed twin sorts later, on another page. The
        batch read finds the twin, so the walk returns the typed row only, and nothing else is lost."""
        rows = sorted([
            _legacy("/scan.glb", "units", "old", "m/scan.glb.metadata.json"),
            _typed("/scan.glb", "units", "new", "metadata"),
            _typed("/scan.glb", "units", "m", "attribute"),
            _legacy("/z.glb", "units", "keep", "m/z.glb.metadata.json"),
        ], key=lambda r: r[SORT_KEY])
        by_key = {r[SORT_KEY]: r for r in rows}

        class _Table:
            def query(self, **kwargs):
                start = 0
                exclusive = kwargs.get("ExclusiveStartKey")
                if exclusive:
                    start = next(i for i, r in enumerate(rows)
                                 if r[SORT_KEY] == exclusive[SORT_KEY]) + 1
                page = rows[start:start + kwargs["Limit"]]
                resp = {"Items": page}
                if start + len(page) < len(rows):
                    resp["LastEvaluatedKey"] = {"pipelineExecutionId": "pe-p1",
                                                SORT_KEY: page[-1][SORT_KEY]}
                return resp

        def _batch(_table, keys):
            return [by_key[k[SORT_KEY]] for k in keys if k[SORT_KEY] in by_key]

        steps = [{"pipelineExecutionId": "pe-p1", "pipelineId": "p1", "pipelineDatabaseId": "db"}]
        seen, token = [], None
        with patch(f"{EXEC_MOD}.dynamodb") as ddb, \
                patch(f"{EXEC_MOD}.get_pipeline_execution_rows", return_value=steps), \
                patch(f"{EXEC_MOD}._batch_get_rows", side_effect=_batch):
            ddb.Table.return_value = _Table()
            for _ in range(10):
                page = le.page_detail_metadata("E1", "output", 1, token)
                seen.extend(page["Items"])
                token = page.get("NextToken")
                if not token:
                    break
        assert sorted((r["targetFilePath"], r["metadataType"], r["metadataValue"]) for r in seen) == [
            ("/scan.glb", "attribute", "m"), ("/scan.glb", "metadata", "new"),
            ("/z.glb", "metadata", "keep")]

    def test_a_failed_twin_lookup_keeps_the_legacy_row(self):
        legacy = _legacy("/scan.glb", "units", "old", "m/scan.glb.metadata.json")
        with patch(f"{EXEC_MOD}.batch_get_items", side_effect=RuntimeError("throttled")):
            kept = er.drop_superseded_output_metadata_rows(
                [legacy], typed_row_exists=le._existing_typed_output_metadata_twins)
        assert kept == [legacy]
