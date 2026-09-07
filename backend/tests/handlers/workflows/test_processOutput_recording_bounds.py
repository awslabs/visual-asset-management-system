# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounds on what the end-state output lambda reads and writes for one execution.

Guards S2-BACKEND-130 (every pipeline results file read whole into memory, with no per-object size
budget and no per-execution count budget) and S2-BACKEND-131 (output-file / metadata / result
provenance rows written one sequential ``put_item`` per row, with a cap on metadata rows only).

Both are cost properties of a legitimate large output, so they are asserted on the values the
recording produces and on the WRITE MODE it uses -- never on a request count. A mutation that batches
more aggressively, reads less, or records fewer rows must leave every assertion here green.
"""

import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

# models.assetsV3 fails to import under Python 3.13 (pre-existing Pydantic v1 regex incompatibility);
# the handler only needs AssetUploadTableModel, which these tests do not reach.
if "models.assetsV3" not in sys.modules:
    _assetsv3_stub = types.ModuleType("models.assetsV3")
    _assetsv3_stub.AssetUploadTableModel = MagicMock()
    sys.modules["models.assetsV3"] = _assetsv3_stub

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md")
os.environ.setdefault("FILE_UPLOAD_LAMBDA_FUNCTION_NAME", "t-fu")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "t-upload")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "t-db")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")

# handlers.workflows.__init__ imports get_task_builder at import time; ASL generation is not
# exercised here.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po
from backend.backend.common.workflows import executionOutputs as eo
from backend.backend.common.workflows import executionRecords as er


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _RecordingTable:
    """A DynamoDB table stub that records the items written and WHICH mode wrote them.

    Keeping the two modes apart is what lets a test say "these rows went out batched" without
    pinning how many requests carried them.
    """

    def __init__(self):
        self.put_item_items = []
        self.batched_items = []
        self.batch_writer_kwargs = []

    def put_item(self, Item):
        self.put_item_items.append(Item)

    def update_item(self, **kwargs):
        pass

    def batch_writer(self, **kwargs):
        self.batch_writer_kwargs.append(kwargs)
        outer = self

        class _Ctx:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def put_item(self, Item):
                outer.batched_items.append(Item)

        return _Ctx()

    @property
    def all_items(self):
        return self.put_item_items + self.batched_items


def _dynamo_with(tables):
    """A dynamo resource stub handing out one _RecordingTable per table name."""
    dynamo = MagicMock()
    dynamo.Table.side_effect = lambda name: tables.setdefault(name, _RecordingTable())
    return dynamo


def _listing(sizes):
    """An S3 listing shaped like verify_get_path_objects' return, one entry per (key, size)."""
    return {"Contents": [{"Key": key, "Size": size} for key, size in sizes]}


def _body(payload: bytes):
    return {"Body": MagicMock(read=MagicMock(return_value=payload))}


# ---------------------------------------------------------------------------
# S2-BACKEND-130 -- bounded result reads
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestResultContentReadIsBounded:
    """A results file is read only up to the bytes its row can store, and a results folder cannot
    make the end-state lambda read an unbounded amount of content."""

    def test_a_file_within_the_row_budget_is_read_whole(self):
        """Positive control for the ranged-read assertion below: the small-file path must NOT range."""
        calls = []

        def _get(**kwargs):
            calls.append(kwargs)
            return _body(b"small")

        with patch.object(po.s3c, "get_object", side_effect=_get):
            content = po._read_result_content("bkt", "p/out/results/r.txt", listed_size=5)
        assert content == "small"
        assert "Range" not in calls[0]

    def test_a_file_larger_than_the_row_budget_is_read_with_a_ranged_get(self):
        calls = []
        oversized = er.MAX_TEXT_FIELD_BYTES * 4

        def _get(**kwargs):
            calls.append(kwargs)
            return _body(b"x" * po.RESULT_CONTENT_READ_BYTES)

        with patch.object(po.s3c, "get_object", side_effect=_get):
            content = po._read_result_content("bkt", "p/out/results/big.txt",
                                              listed_size=oversized)
        # A bounded read, not the whole object. The ceiling is asserted loosely (a larger safety
        # margin is still bounded) so only an unbounded read fails here.
        assert calls[0]["Range"] == f"bytes=0-{po.RESULT_CONTENT_READ_BYTES - 1}"
        assert po.RESULT_CONTENT_READ_BYTES <= er.MAX_TEXT_FIELD_BYTES * 2
        assert len(content.encode("utf-8")) <= po.RESULT_CONTENT_READ_BYTES

    def test_a_listing_without_a_size_is_still_read_under_the_cap(self):
        """An unannotated listing must not fall back to an unbounded read."""
        calls = []
        with patch.object(po.s3c, "get_object",
                          side_effect=lambda **kw: (calls.append(kw), _body(b"abc"))[1]):
            po._read_result_content("bkt", "p/out/results/r.txt", listed_size=None)
        assert calls[0]["Range"] == f"bytes=0-{po.RESULT_CONTENT_READ_BYTES - 1}"

    def test_the_ranged_read_still_reports_the_stored_row_as_truncated(self):
        """The read margin has to survive a multi-byte character split at the range boundary.

        MAX_TEXT_FIELD_BYTES is exactly divisible by 4, so a file of 4-byte characters aligns with
        the budget: a 1-byte margin is entirely consumed by the dropped partial character and the
        stored row would then claim to be complete. A 4-byte margin keeps it over the budget.
        """
        char = "\U0001d11e"  # 4 UTF-8 bytes
        assert len(char.encode("utf-8")) == 4
        assert er.MAX_TEXT_FIELD_BYTES % 4 == 0
        whole = char * (er.MAX_TEXT_FIELD_BYTES // 4 + 100)
        served = whole.encode("utf-8")[:po.RESULT_CONTENT_READ_BYTES]

        with patch.object(po.s3c, "get_object", return_value=_body(served)):
            content = po._read_result_content("bkt", "k", listed_size=len(whole.encode("utf-8")))
        row = er.build_output_result_record(
            pipeline_execution_id="P1", relative_file_path="/r.txt",
            results_content=content, s3_key="k")
        assert row["resultsContentTruncated"] is True


@pytest.mark.unit
class TestResultCollectionIsBounded:
    """Collection over the results folder stops at a row ceiling and at a content ceiling, and says
    so on the log, instead of accumulating the whole folder."""

    def _collect(self, listing, payload=b"body"):
        with patch.object(po.s3c, "get_object", return_value=_body(payload)):
            return po._collect_result_outputs("bkt", "p/out/results/", listing)

    def test_every_file_is_collected_below_the_caps(self):
        """Positive control: the caps do not narrow an ordinary results folder."""
        listing = _listing([(f"p/out/results/r{i}.txt", 4) for i in range(5)])
        descriptors, failures = self._collect(listing)
        assert [d["relativeFilePath"] for d in descriptors] == [f"/r{i}.txt" for i in range(5)]
        assert failures == []

    def test_collection_stops_at_the_row_cap(self):
        listing = _listing([(f"p/out/results/r{i}.txt", 4) for i in range(50)])
        with patch.object(po, "MAX_RECORDED_OUTPUT_RESULT_ROWS", 3):
            descriptors, _failures = self._collect(listing)
        assert len(descriptors) <= 3

    def test_collection_stops_at_the_content_budget(self):
        listing = _listing([(f"p/out/results/r{i}.txt", 8) for i in range(50)])
        with patch.object(po, "MAX_RECORDED_OUTPUT_RESULT_CONTENT_BYTES", 16):
            descriptors, _failures = self._collect(listing, payload=b"12345678")
        total = sum(len(d["resultsContent"].encode("utf-8")) for d in descriptors)
        # The budget is checked before each read, so at most one file may overshoot it.
        assert total <= 16 + 8
        assert len(descriptors) < 50

    def test_folder_placeholder_keys_are_skipped(self):
        listing = _listing([("p/out/results/", 0), ("p/out/results/r.txt", 4)])
        descriptors, _failures = self._collect(listing)
        assert [d["relativeFilePath"] for d in descriptors] == ["/r.txt"]

    def test_a_read_failure_is_reported_and_does_not_stop_the_rest(self):
        listing = _listing([("p/out/results/a.txt", 4), ("p/out/results/b.txt", 4)])
        seen = []

        def _get(**kwargs):
            seen.append(kwargs["Key"])
            if kwargs["Key"].endswith("a.txt"):
                raise RuntimeError("denied")
            return _body(b"ok")

        with patch.object(po.s3c, "get_object", side_effect=_get):
            descriptors, failures = po._collect_result_outputs("bkt", "p/out/results/", listing)
        assert [d["relativeFilePath"] for d in descriptors] == ["/b.txt"]
        assert failures and "results file" in failures[0]

    def test_an_empty_listing_collects_nothing(self):
        descriptors, failures = po._collect_result_outputs("bkt", "p/out/results/", {})
        assert descriptors == [] and failures == []


# ---------------------------------------------------------------------------
# S2-BACKEND-131 -- batched provenance writes + a bounded row count
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestProvenanceRowsAreBatched:
    """Provenance rows leave through a batch writer keyed on the table's own key attributes, and the
    row set is unchanged by that."""

    def _record(self, **overrides):
        po.workflow_execution_database_v2 = "t-exec-v2"
        tables = {}
        kwargs = dict(
            dynamo=_dynamo_with(tables), workflow_execution_id="E1",
            end_state_pipeline_execution_id="P9", workflow_database_id="wdb", workflow_id="wf",
            bucket_name="abkt", output_files=[], output_metadata=[], output_results=[],
            result_log="done", execution_log="", log_group_arn="", log_stream_name="",
            execution_status="SUCCEEDED", execution_error="",
        )
        kwargs.update(overrides)
        po.record_execution_outputs(**kwargs)
        return tables

    def test_output_file_rows_go_out_batched_rather_than_one_request_each(self):
        """`put_item_items == []` is a negative assertion, so the stub's put_item path is proved live
        first -- otherwise an empty list could mean the stub never records anything."""
        probe = _RecordingTable()
        probe.put_item(Item={"probe": 1})
        assert probe.put_item_items == [{"probe": 1}]   # positive control

        files = [{"fileType": "file", "relativeFilePath": f"/f{i}.glb", "s3Key": f"k{i}",
                  "fileSize": 1, "contentType": "", "s3VersionId": ""} for i in range(60)]
        tables = self._record(output_files=files)
        of = tables[po.pipeline_execution_output_files_table]
        assert len(of.batched_items) == len(files)
        assert of.put_item_items == []
        # Every row still arrives, whichever mode carried it.
        assert len(tables[po.pipeline_execution_logs_table].all_items) == 1

    def test_the_batch_writer_dedupes_on_the_tables_own_key_attributes(self):
        """A repeated key inside one batch is rejected by DynamoDB, so the pkey list is load-bearing
        and is asserted by name rather than by call count."""
        files = [{"fileType": "file", "relativeFilePath": "/f.glb", "s3Key": "k"}]
        metadata = [{"targetFilePath": "/", "metadataKey": "k", "metadataValue": "v",
                     "sourceMetadataFileRelativePath": "m.json"}]
        results = [{"relativeFilePath": "/r.txt", "resultsContent": "x", "s3Key": "rk"}]
        tables = self._record(output_files=files, output_metadata=metadata, output_results=results)
        assert tables[po.pipeline_execution_output_files_table].batch_writer_kwargs[0][
            "overwrite_by_pkeys"] == ["pipelineExecutionId", "fileType:relativeFilePath"]
        assert tables[po.pipeline_execution_output_metadata_table].batch_writer_kwargs[0][
            "overwrite_by_pkeys"] == ["pipelineExecutionId", "targetFilePath:metadataKey"]
        assert tables[po.pipeline_execution_output_results_table].batch_writer_kwargs[0][
            "overwrite_by_pkeys"] == ["pipelineExecutionId", "relativeFilePath"]

    def test_duplicate_metadata_keys_survive_as_one_row_not_a_rejected_batch(self):
        """Two metadata files applying the same key to the same target produced two identical-key
        rows; put_item overwrote, a raw batch would reject. Asserted through the REAL boto3 batch
        writer, so the dedupe is the shipped behaviour rather than the stub's."""
        import boto3.dynamodb.table as ddb_table
        sent = []

        class _Client:
            def batch_write_item(self, RequestItems):
                sent.extend(next(iter(RequestItems.values())))
                return {"UnprocessedItems": {}}

        class _RealBatchTable:
            name = "t-om"

            def __init__(self):
                self.meta = MagicMock(client=_Client())

            def batch_writer(self, **kwargs):
                return ddb_table.BatchWriter(self.name, self.meta.client, **kwargs)

            def put_item(self, Item):
                pass

            def update_item(self, **kwargs):
                pass

        om = _RealBatchTable()
        dynamo = MagicMock()
        dynamo.Table.side_effect = (
            lambda name: om if name == po.pipeline_execution_output_metadata_table
            else _RecordingTable())
        po.workflow_execution_database_v2 = "t-exec-v2"
        dup = [{"targetFilePath": "/", "metadataKey": "same", "metadataValue": v,
                "sourceMetadataFileRelativePath": "m.json"} for v in ("first", "second")]
        po.record_execution_outputs(
            dynamo=dynamo, workflow_execution_id="E1", end_state_pipeline_execution_id="P9",
            workflow_database_id="wdb", workflow_id="wf", bucket_name="abkt",
            output_files=[], output_metadata=dup, output_results=[],
            result_log="done", execution_log="", log_group_arn="", log_stream_name="",
            execution_status="SUCCEEDED", execution_error="")
        keys = [(r["PutRequest"]["Item"]["pipelineExecutionId"],
                 r["PutRequest"]["Item"]["targetFilePath:metadataKey"]) for r in sent]
        assert len(keys) == len(set(keys))
        assert [r["PutRequest"]["Item"]["metadataValue"] for r in sent] == ["second"]

    def test_output_file_rows_are_capped_and_the_log_row_says_the_set_is_partial(self):
        files = [{"fileType": "file", "relativeFilePath": f"/f{i}.glb", "s3Key": f"k{i}"}
                 for i in range(9)]
        with patch.object(po, "MAX_RECORDED_OUTPUT_FILE_ROWS", 4):
            tables = self._record(output_files=files)
        of = tables[po.pipeline_execution_output_files_table]
        logs = tables[po.pipeline_execution_logs_table]
        assert len(of.all_items) <= 4
        assert po.OUTPUT_ROWS_TRUNCATED_NOTE in logs.all_items[0]["resultLog"]

    def test_an_uncapped_run_does_not_claim_its_rows_were_dropped(self):
        """Negative control for the note: the same assertion must not pass unconditionally."""
        files = [{"fileType": "file", "relativeFilePath": f"/f{i}.glb", "s3Key": f"k{i}"}
                 for i in range(3)]
        with patch.object(po, "MAX_RECORDED_OUTPUT_FILE_ROWS", 4):
            tables = self._record(output_files=files)
        logs = tables[po.pipeline_execution_logs_table]
        assert len(tables[po.pipeline_execution_output_files_table].all_items) == 3
        assert po.OUTPUT_ROWS_TRUNCATED_NOTE not in logs.all_items[0]["resultLog"]

    def test_recorded_rows_are_the_same_values_the_per_item_writes_produced(self):
        files = [{"fileType": "preview", "relativeFilePath": "/p.png", "s3Bucket": "other",
                  "s3Key": "kp", "fileSize": 7, "contentType": "image/png", "s3VersionId": "v2"}]
        tables = self._record(output_files=files)
        row = tables[po.pipeline_execution_output_files_table].all_items[0]
        assert row["pipelineExecutionId"] == "P9"
        assert row["fileType:relativeFilePath"] == "preview:/p.png"
        assert row["s3Bucket"] == "other" and row["s3VersionId"] == "v2"


@pytest.mark.unit
class TestInterimOutputRowsAreBatched:
    """The interim step-transition writer shares the defect and the fix; its row count stays
    UNCAPPED because those rows are the end-state attribution baseline."""

    def test_produced_file_rows_go_out_batched_on_the_tables_key_attributes(self):
        tables = {}
        produced = [{"fileType": "file", "relativePath": f"/f{i}.glb", "key": f"k{i}",
                     "fileSize": 1, "contentType": "", "versionId": f"v{i}"} for i in range(40)]
        eo.record_pipeline_output_files(
            dynamo=_dynamo_with(tables), output_files_table="t-of",
            pipeline_execution_id="P1", bucket="abkt", produced_files=produced)
        table = tables["t-of"]
        assert len(table.batched_items) == len(produced)
        assert table.put_item_items == []
        assert table.batch_writer_kwargs[0]["overwrite_by_pkeys"] == [
            "pipelineExecutionId", "fileType:relativeFilePath"]

    def test_no_rows_are_dropped_because_the_baseline_must_be_complete(self):
        tables = {}
        produced = [{"fileType": "file", "relativePath": f"/f{i}.glb", "key": f"k{i}"}
                    for i in range(2500)]
        eo.record_pipeline_output_files(
            dynamo=_dynamo_with(tables), output_files_table="t-of",
            pipeline_execution_id="P1", bucket="abkt", produced_files=produced)
        assert len(tables["t-of"].all_items) == 2500

    def test_an_empty_produced_set_writes_nothing(self):
        tables = {}
        eo.record_pipeline_output_files(
            dynamo=_dynamo_with(tables), output_files_table="t-of",
            pipeline_execution_id="P1", bucket="abkt", produced_files=[])
        assert tables == {}


# ---------------------------------------------------------------------------
# S2-BACKEND-130 -- the results-only terminal path shares the bound
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestResultsOnlyTerminalPathIsBounded:
    """The results-only terminal path (outputLocationType 'none') is the SECOND place result files are
    read, and it must read through the same bounded collector as the normal path.

    The normal path's routing is already pinned by test_processOutput_write_order, which patches
    ``_collect_result_outputs`` and asserts a ``read_results`` step ran. This path had no coverage at
    all, so an inline whole-object read reinstated here would leave every other assertion green -- and
    it is the path where an unbounded read costs the most, because it is the only place a results-only
    run's outcome is recorded.

    Every assertion is about the reads issued and the rows accumulated, never about a request count.
    """

    RESULTS = "pipelines/p/j/output/E1/results/"

    def _event(self):
        return {
            "outputLocationType": "none",
            "workflowExecutionS3InputOutputBucket": "iobkt",
            "resultsPathKey": self.RESULTS,
            "workflowExecutionId": "E1",
            "endStatePipelineExecutionId": "P9",
            "workflowDatabaseId": "wdb",
            "workflowId": "wf",
        }

    def _run(self, sizes, payload=b"body", via_handler=False):
        """Drive the results-only path over a results listing.

        Returns (the output_results handed to the recorder, the get_object kwargs seen). Recording is
        stubbed because the write mode is covered above; what is under test here is what this path
        reads and accumulates before it gets there.
        """
        seen = []

        def _get(**kwargs):
            seen.append(kwargs)
            return _body(payload)

        event = self._event()
        with patch.object(po.s3c, "get_object", side_effect=_get), \
                patch.object(po, "verify_get_path_objects", return_value=_listing(sizes)), \
                patch.object(po, "_fetch_execution_logs", return_value=("", "")), \
                patch.object(po, "record_execution_outputs") as m_record:
            if via_handler:
                response = po.lambda_handler({"body": event}, MagicMock())
            else:
                response = po._process_results_only(event)
        assert response["statusCode"] == 200
        assert m_record.called, "the results-only path must record its outputs"
        return m_record.call_args.kwargs["output_results"], seen

    def test_an_ordinary_results_folder_is_recorded_whole(self):
        """Positive control: the bounds do not narrow a normal results-only run, and the small-file
        path does not range. Without this, an outage that read nothing would satisfy the bounds below.
        """
        sizes = [(f"{self.RESULTS}r{i}.txt", 4) for i in range(5)]
        recorded, seen = self._run(sizes)
        assert [r["relativeFilePath"] for r in recorded] == [f"/r{i}.txt" for i in range(5)]
        assert len(seen) == 5
        assert all("Range" not in call for call in seen)

    def test_an_oversized_result_file_is_range_read_not_read_whole(self):
        oversized = er.MAX_TEXT_FIELD_BYTES * 4
        recorded, seen = self._run([(f"{self.RESULTS}big.txt", oversized)],
                                   payload=b"x" * po.RESULT_CONTENT_READ_BYTES)
        assert seen[0]["Range"] == f"bytes=0-{po.RESULT_CONTENT_READ_BYTES - 1}"
        assert len(recorded) == 1
        assert len(recorded[0]["resultsContent"].encode("utf-8")) <= po.RESULT_CONTENT_READ_BYTES

    def test_the_row_cap_stops_this_path_reading_not_just_recording(self):
        """The cap has to short-circuit the READ loop. A cap applied only to the recorded rows would
        still have pulled every object into memory first, which is the defect itself.
        """
        sizes = [(f"{self.RESULTS}r{i}.txt", 4) for i in range(50)]
        with patch.object(po, "MAX_RECORDED_OUTPUT_RESULT_ROWS", 3):
            recorded, seen = self._run(sizes)
        assert len(recorded) <= 3
        assert len(seen) <= 3

    def test_the_content_budget_stops_this_path_reading(self):
        sizes = [(f"{self.RESULTS}r{i}.txt", 8) for i in range(50)]
        with patch.object(po, "MAX_RECORDED_OUTPUT_RESULT_CONTENT_BYTES", 16):
            recorded, seen = self._run(sizes, payload=b"12345678")
        total = sum(len(r["resultsContent"].encode("utf-8")) for r in recorded)
        # The budget is checked before each read, so at most one file may overshoot it.
        assert total <= 16 + 8
        assert len(seen) < 50

    def test_the_handler_routes_a_results_only_event_to_the_bounded_path(self):
        """Reachability: the bound must sit on the path lambda_handler actually dispatches an
        outputLocationType 'none' event to, not only on the private helper.
        """
        oversized = er.MAX_TEXT_FIELD_BYTES * 4
        recorded, seen = self._run([(f"{self.RESULTS}big.txt", oversized)],
                                   payload=b"x" * po.RESULT_CONTENT_READ_BYTES,
                                   via_handler=True)
        assert seen and seen[0]["Range"] == f"bytes=0-{po.RESULT_CONTENT_READ_BYTES - 1}"
        assert len(recorded) == 1


@pytest.mark.unit
class TestShippedCapsAreUsableBounds:
    """Every cap assertion above patches the constant it exercises, so none of them pins the SHIPPED
    value. A cap of ``None`` slices to the whole list and compares equal in length, so it bounds
    nothing and reports no truncation either -- a regression no patched test can see.
    """

    def test_each_recording_cap_is_a_finite_positive_bound(self):
        for name in ("MAX_RECORDED_OUTPUT_FILE_ROWS", "MAX_RECORDED_OUTPUT_RESULT_ROWS",
                     "MAX_RECORDED_OUTPUT_RESULT_CONTENT_BYTES",
                     "MAX_RECORDED_OUTPUT_METADATA_ROWS", "RESULT_CONTENT_READ_BYTES"):
            value = getattr(po, name)
            assert isinstance(value, int) and value > 0, name

    def test_the_per_file_read_ceiling_stays_close_to_what_a_row_stores(self):
        """The read ceiling exists because content past the row's budget is discarded. A ceiling that
        drifted far above the budget would restore the wasted read the bound was added to remove.
        """
        assert po.RESULT_CONTENT_READ_BYTES > er.MAX_TEXT_FIELD_BYTES
        assert po.RESULT_CONTENT_READ_BYTES <= er.MAX_TEXT_FIELD_BYTES * 2


# ---------------------------------------------------------------------------
# The bound must not change what a results file MEANS
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDecodingDoesNotDependOnTheFileSize:
    """Whether a results file is readable text decides the run's recorded status, so it cannot depend
    on which read fetched the file. The ranged read tolerates exactly the one character its own
    boundary can cut; anything else that does not decode fails the read at either size.
    """

    NOT_TEXT = bytes(range(256)) * 2000

    def test_a_multibyte_character_split_at_the_boundary_is_still_read(self):
        """Positive control for the two negatives below: the tolerated case must stay tolerated, and
        the read must still exceed the row's budget after the partial character is dropped.
        """
        # "€" is 3 UTF-8 bytes, and RESULT_CONTENT_READ_BYTES happens to be an exact multiple of 3, so
        # a payload of nothing but "€" lands the boundary BETWEEN characters and the split this test
        # exists to cover never happens. One leading ASCII byte shifts the whole run off that
        # alignment. Stated as a decode failure rather than as arithmetic on the ceiling: the property
        # that matters is "these bytes end mid-character", and it stays true if the ceiling changes.
        char = "€"
        whole = ("a" + char * (po.RESULT_CONTENT_READ_BYTES // 3 + 50)).encode("utf-8")
        served = whole[:po.RESULT_CONTENT_READ_BYTES]
        with pytest.raises(UnicodeDecodeError):
            served.decode("utf-8")

        # The longest valid UTF-8 prefix of what was served -- i.e. the partial trailing character
        # dropped. Derived by trimming rather than by a modulo, for the same reason.
        valid_prefix = served
        while True:
            try:
                expected_text = valid_prefix.decode("utf-8")
                break
            except UnicodeDecodeError:
                valid_prefix = valid_prefix[:-1]
        assert len(served) - len(valid_prefix) in (1, 2), (
            "exactly the bytes of one cut character should be dropped; "
            f"dropped {len(served) - len(valid_prefix)}")

        with patch.object(po.s3c, "get_object", return_value=_body(served)):
            content = po._read_result_content("bkt", "k", listed_size=len(whole))
        assert content == expected_text
        row = er.build_output_result_record(
            pipeline_execution_id="P1", relative_file_path="/r.txt",
            results_content=content, s3_key="k")
        assert row["resultsContentTruncated"] is True

    def test_a_large_non_text_file_fails_the_read_rather_than_yielding_mojibake(self):
        served = self.NOT_TEXT[:po.RESULT_CONTENT_READ_BYTES]
        with patch.object(po.s3c, "get_object", return_value=_body(served)):
            with pytest.raises(UnicodeDecodeError):
                po._read_result_content("bkt", "k", listed_size=er.MAX_TEXT_FIELD_BYTES * 4)

    def test_the_same_non_text_file_is_recorded_the_same_way_at_either_size(self):
        """The size-independence itself: one payload, read whole at one size and ranged at the other,
        must produce the same descriptors and the same terminal status.
        """
        small = self.NOT_TEXT[:1024]
        large = self.NOT_TEXT[:po.RESULT_CONTENT_READ_BYTES]
        outcomes = []
        for listed_size, served in ((len(small), small),
                                    (er.MAX_TEXT_FIELD_BYTES * 4, large)):
            listing = _listing([("p/out/results/r.bin", listed_size)])
            with patch.object(po.s3c, "get_object", return_value=_body(served)):
                descriptors, failures = po._collect_result_outputs(
                    "bkt", "p/out/results/", listing)
            outcomes.append((descriptors, po._terminal_status(failures)[0]))
        assert [status for _d, status in outcomes] == ["FAILED", "FAILED"]
        assert [descriptors for descriptors, _s in outcomes] == [[], []]
