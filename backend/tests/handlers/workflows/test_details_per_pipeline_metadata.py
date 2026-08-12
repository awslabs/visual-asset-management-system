# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-pipeline attribution of the input-metadata collections in the execution-details view.

Input-metadata rows are keyed by pipelineExecutionId and describe the entities THAT step read: a
pipeline receives only the inputs passing its own effective inputFileFilters (none at arity 'none'), so
the rows legitimately differ per pipeline. The detail view must therefore report each pipeline's rows
distinctly, and neither the read budget nor the return trim may drop a whole pipeline's rows — for a
collection whose point is which metadata each step read, a step with no rows reads as a claim about the
step rather than a visible consequence of a cap."""

import json
import os

import pytest
from unittest.mock import patch

# executionService resolves these at import (mirrors test_executionService_wb53.py).
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

from backend.backend.handlers.workflows import executionService as le

MOD = "backend.backend.handlers.workflows.executionService"


def _assemble(md_by_pexec):
    """Assemble the details view where each pipeline execution's input-metadata read answers its own
    rows. Keyed 'pe-<pipelineId>' so an assertion can name the producing pipeline."""
    prows = [{"pipelineExecutionId": pexec, "pipelineId": pexec.replace("pe-", ""),
              "pipelineDatabaseId": "db"} for pexec in md_by_pexec]

    def _capped(table_name, key_condition, max_items):
        if table_name == le.pipeline_execution_input_metadata_table:
            rows = md_by_pexec.get(key_condition._values[1], [])
            return rows[:max_items], len(rows) > max_items
        return [], False

    with patch(f"{MOD}.get_workflow_definition", return_value={}), \
         patch(f"{MOD}.get_pipeline_definition", return_value={}), \
         patch(f"{MOD}.get_pipeline_definitions", return_value={}), \
         patch(f"{MOD}._query_all", return_value=[]), \
         patch(f"{MOD}.get_pipeline_execution_rows", return_value=prows), \
         patch(f"{MOD}._query_capped", side_effect=_capped), \
         patch(f"{MOD}.get_produced_file_versions", return_value={}):
        return le.assemble_execution_details(
            "E1", {"workflowId": "wf", "workflowDatabaseId": "db"}, config_row={})


def _row(file_path, scope="asset", database_id="db", asset_id="a1"):
    return {"databaseId": database_id, "assetId": asset_id, "filePath": file_path,
            "scope": scope, "metadata": {"k": "v"}}


def _many(count, prefix):
    return [{"databaseId": "db", "assetId": f"a{i}", "filePath": f"/{prefix}{i}.glb",
             "scope": "asset", "metadata": {"k": str(i)}} for i in range(count)]


def _pairs(details, collection="inputMetadata"):
    return sorted((md["pipelineId"], md["filePath"]) for md in details[collection])


def _counts(details, collection="inputMetadata"):
    out = {}
    for md in details[collection]:
        out[md["pipelineId"]] = out.get(md["pipelineId"], 0) + 1
    return out


@pytest.mark.unit
class TestPerPipelineAttribution:
    def test_each_pipelines_rows_are_reported_distinctly(self):
        details = _assemble({"pe-pA": [_row("/a.glb")], "pe-pB": [_row("/b.glb")],
                             "pe-pC": [_row("/c.glb")]})
        assert _pairs(details) == [("pA", "/a.glb"), ("pB", "/b.glb"), ("pC", "/c.glb")]

    def test_a_shared_entity_is_reported_once_per_pipeline_that_read_it(self):
        # The row two pipelines share is two distinct facts ("pA read it", "pB read it"), not one row
        # read twice. A key without the pipeline keeps whichever pipeline was collected last.
        details = _assemble({"pe-pA": [_row("/shared.glb")], "pe-pB": [_row("/shared.glb")]})
        assert _pairs(details) == [("pA", "/shared.glb"), ("pB", "/shared.glb")]

    def test_three_pipelines_over_three_shared_files_keep_all_nine_facts(self):
        rows = [_row("/a.glb"), _row("/b.glb"), _row("/c.glb")]
        details = _assemble({"pe-pA": list(rows), "pe-pB": list(rows), "pe-pC": list(rows)})
        assert len(details["inputMetadata"]) == 9
        assert len(_counts(details)) == 3

    def test_identical_rows_within_one_pipeline_still_collapse(self):
        # The dedupe keeps its original job: one pipeline cannot report the same entity twice.
        details = _assemble({"pe-pA": [_row("/a.glb"), _row("/a.glb")]})
        assert _pairs(details) == [("pA", "/a.glb")]

    def test_a_database_row_is_reported_for_each_pipeline_that_read_it(self):
        details = _assemble({
            "pe-pA": [_row("/", scope="database", database_id="src-db", asset_id="")],
            "pe-pB": [_row("/", scope="database", database_id="src-db", asset_id="")]})
        assert _pairs(details, "inputDatabaseMetadata") == [("pA", "/"), ("pB", "/")]

    def test_the_scope_split_still_separates_the_collections_per_pipeline(self):
        details = _assemble({
            "pe-pA": [_row("/a.glb"),
                      _row("/", scope="database", database_id="src-db", asset_id="")],
            "pe-pB": [_row("/b.glb")]})
        assert _pairs(details) == [("pA", "/a.glb"), ("pB", "/b.glb")]
        assert _pairs(details, "inputDatabaseMetadata") == [("pA", "/")]

    def test_a_database_row_is_not_collapsed_into_an_empty_id_asset_row(self):
        # The legacy flat asset row also carries empty ids and a '/' filePath, so neither the entity
        # keys nor the pipeline key may be dropped from the identity.
        details = _assemble({
            "pe-pA": [{"databaseId": "", "assetId": "", "filePath": "/", "scope": "asset",
                       "metadata": {"legacy": "1"}},
                      _row("/", scope="database", database_id="src-db", asset_id="")],
            "pe-pB": [{"databaseId": "", "assetId": "", "filePath": "/", "scope": "asset",
                       "metadata": {"legacy": "1"}}]})
        assert _pairs(details) == [("pA", "/"), ("pB", "/")]
        assert _pairs(details, "inputDatabaseMetadata") == [("pA", "/")]

    def test_a_pipeline_that_read_nothing_contributes_no_rows(self):
        # An arity-'none' pipeline with no metadata sources genuinely has none; the others are
        # unaffected by its absence.
        details = _assemble({"pe-pA": [_row("/a.glb")], "pe-pNone": []})
        assert _pairs(details) == [("pA", "/a.glb")]

    def test_a_single_pipeline_execution_is_unchanged(self):
        details = _assemble({"pe-pA": [_row("/a.glb"), _row("/b.glb")]})
        assert _pairs(details) == [("pA", "/a.glb"), ("pA", "/b.glb")]
        assert details["truncatedCollections"] == []
        json.dumps(details)


@pytest.mark.unit
class TestPerPipelineBounds:
    def test_the_return_trim_keeps_every_pipeline_represented(self):
        # Pipeline 1 alone holds more rows than the whole return budget; a prefix trim would return
        # only its rows and none of pipeline 2's.
        over = le.MAX_DETAIL_INPUT_ROWS_RETURNED + 50
        details = _assemble({"pe-pA": _many(over, "a"), "pe-pB": _many(over, "b")})
        counts = _counts(details)
        assert set(counts) == {"pA", "pB"}
        assert sum(counts.values()) == le.MAX_DETAIL_INPUT_ROWS_RETURNED
        assert min(counts.values()) >= le.MAX_DETAIL_INPUT_ROWS_RETURNED // 2 - 1
        assert "inputMetadata" in details["truncatedCollections"]

    def test_the_trim_is_flagged_whenever_it_drops_rows(self):
        details = _assemble({"pe-pA": _many(le.MAX_DETAIL_INPUT_ROWS_RETURNED + 10, "a")})
        assert len(details["inputMetadata"]) == le.MAX_DETAIL_INPUT_ROWS_RETURNED
        assert "inputMetadata" in details["truncatedCollections"]

    def test_a_pipeline_with_few_rows_keeps_all_of_them(self):
        # The round-robin share leaves a small pipeline's remainder to the larger one rather than
        # reserving budget it cannot use.
        details = _assemble({"pe-pA": _many(le.MAX_DETAIL_INPUT_ROWS_RETURNED + 50, "a"),
                             "pe-pB": _many(3, "b")})
        counts = _counts(details)
        assert counts["pB"] == 3
        assert counts["pA"] == le.MAX_DETAIL_INPUT_ROWS_RETURNED - 3

    def test_the_trim_preserves_each_pipelines_own_row_order(self):
        over = le.MAX_DETAIL_INPUT_ROWS_RETURNED
        details = _assemble({"pe-pA": _many(over, "a"), "pe-pB": _many(over, "b")})
        for pipeline_id in ("pA", "pB"):
            paths = [md["filePath"] for md in details["inputMetadata"]
                     if md["pipelineId"] == pipeline_id]
            assert paths == sorted(paths, key=lambda p: int(p.split(".")[0][2:]))

    def test_within_the_budget_nothing_is_trimmed_or_flagged(self):
        details = _assemble({"pe-pA": _many(5, "a"), "pe-pB": _many(5, "b")})
        assert len(details["inputMetadata"]) == 10
        assert details["truncatedCollections"] == []

    def test_the_read_budget_is_shared_so_a_late_pipeline_is_still_read(self):
        # A row-heavy first pipeline must not consume the whole read budget: without a per-pipeline
        # share, pipeline 2's read is issued with zero remaining and returns nothing.
        huge = le.MAX_DETAIL_ROWS_PER_COLLECTION + 100
        details = _assemble({"pe-pA": _many(huge, "a"), "pe-pB": _many(10, "b")})
        assert set(_counts(details)) == {"pA", "pB"}
        assert "inputMetadata" in details["truncatedCollections"]

    def test_a_many_step_workflow_still_reads_every_step(self):
        # 40 steps each holding more rows than their even share of the budget. Every step must be
        # represented: a share large enough to overrun the collection cap would cut the later steps off
        # exactly as a first-come budget does.
        details = _assemble({f"pe-p{i}": _many(200, f"p{i}") for i in range(40)})
        assert len(_counts(details)) == 40
        assert "inputMetadata" in details["truncatedCollections"]

    def test_many_steps_each_over_their_share_are_all_represented(self):
        # 900 steps: the even read share is small but non-zero, and the round-robin trim spends the
        # return budget one row per step before giving any step a second.
        details = _assemble({f"pe-p{i}": _many(5, f"p{i}") for i in range(900)})
        counts = _counts(details)
        assert len(counts) == 900
        assert min(counts.values()) >= 1
        assert sum(counts.values()) == le.MAX_DETAIL_INPUT_ROWS_RETURNED

    def test_more_steps_than_the_return_cap_is_bounded_and_flagged(self):
        # The honest limit: a response cannot carry a row for more steps than the return cap allows, so
        # the steps beyond it are dropped — and the collection is flagged rather than reading as though
        # those steps read nothing unremarkably.
        count = le.MAX_DETAIL_INPUT_ROWS_RETURNED + 10
        details = _assemble({f"pe-p{i}": _many(2, f"p{i}") for i in range(count)})
        counts = _counts(details)
        assert len(counts) == le.MAX_DETAIL_INPUT_ROWS_RETURNED
        assert set(counts.values()) == {1}
        assert "inputMetadata" in details["truncatedCollections"]

    def test_a_read_cap_hit_still_flags_both_metadata_collections(self):
        # A row dropped before the scope split has an unknown scope, so both collections are reported
        # partial rather than implying precision that is not available.
        details = _assemble({"pe-pA": _many(le.MAX_DETAIL_ROWS_PER_COLLECTION + 5, "a")})
        assert sorted(details["truncatedCollections"]) == [
            "inputDatabaseMetadata", "inputMetadata"]

    def test_light_steps_do_not_reserve_budget_away_from_a_heavy_one(self):
        """A step reads up to what is LEFT, not an even share the other steps cannot use.

        The regression this pins: with a fixed `budget // steps` share, four arity-none steps holding
        one database row each reserved four-fifths of the read budget, so a 5-step run whose first step
        read 900 files returned 399 of them and flagged the collection truncated — while the very same
        run on a single step returned all 900. The row count a caller sees must not depend on how many
        OTHER steps the workflow happens to have."""
        rows = {"pe-pA": _many(900, "a")}
        rows.update({f"pe-pB{i}": _many(1, f"b{i}") for i in range(4)})
        details = _assemble(rows)
        counts = _counts(details)
        assert counts["pA"] == 900, f"the heavy step lost rows to the light steps' reserve: {counts}"
        assert sum(counts.values()) == 904
        assert details["truncatedCollections"] == []

    def test_a_late_heavy_step_is_also_read_whole(self):
        # The same run with the heavy step last: order must not change what is returned.
        rows = {f"pe-pB{i}": _many(1, f"b{i}") for i in range(4)}
        rows["pe-pZ"] = _many(900, "z")
        details = _assemble(rows)
        assert _counts(details)["pZ"] == 900
        assert details["truncatedCollections"] == []

    def test_every_step_still_gets_a_floor_when_the_budget_is_oversubscribed(self):
        # Genuinely over budget: the earlier steps fill first, but the reserve guarantees each later
        # step rows rather than letting a first-come budget return none of them.
        details = _assemble({f"pe-p{i}": _many(600, f"p{i}") for i in range(5)})
        counts = _counts(details)
        assert len(counts) == 5, f"a step was starved entirely: {counts}"
        assert min(counts.values()) >= 1
        assert "inputMetadata" in details["truncatedCollections"]


@pytest.mark.unit
class TestTrimHelper:
    """The per-pipeline trim in isolation."""

    def _rows(self, spec):
        return [{"pipelineId": pid, "filePath": f"/{pid}{i}.glb"}
                for pid, count in spec for i in range(count)]

    def test_under_the_cap_returns_the_list_unchanged_and_unflagged(self):
        rows = self._rows([("pA", 2), ("pB", 2)])
        flags = set()
        assert le._trim_returned_rows_per_pipeline(rows, "c", flags, max_rows=10) == rows
        assert flags == set()

    def test_an_even_split_at_the_cap(self):
        rows = self._rows([("pA", 10), ("pB", 10)])
        flags = set()
        kept = le._trim_returned_rows_per_pipeline(rows, "c", flags, max_rows=6)
        assert len(kept) == 6
        assert [r["pipelineId"] for r in kept].count("pA") == 3
        assert [r["pipelineId"] for r in kept].count("pB") == 3
        assert flags == {"c"}

    def test_pipeline_order_and_row_order_are_preserved(self):
        rows = self._rows([("pA", 4), ("pB", 4)])
        kept = le._trim_returned_rows_per_pipeline(rows, "c", set(), max_rows=4)
        assert [r["filePath"] for r in kept] == ["/pA0.glb", "/pA1.glb", "/pB0.glb", "/pB1.glb"]

    def test_a_remainder_is_left_to_the_pipelines_that_can_use_it(self):
        rows = self._rows([("pA", 1), ("pB", 10)])
        kept = le._trim_returned_rows_per_pipeline(rows, "c", set(), max_rows=5)
        counts = {}
        for r in kept:
            counts[r["pipelineId"]] = counts.get(r["pipelineId"], 0) + 1
        assert counts == {"pA": 1, "pB": 4}

    def test_rows_carrying_no_pipeline_id_are_still_bounded(self):
        rows = [{"filePath": f"/f{i}.glb"} for i in range(10)]
        kept = le._trim_returned_rows_per_pipeline(rows, "c", set(), max_rows=4)
        assert len(kept) == 4


@pytest.mark.unit
class TestCollectionByteBudget:
    """A row count cannot bound the response on its own.

    A metadata row carries a whole entity's captured map, itself bounded only per entity, so a
    collection sitting at the row cap ranges from a few hundred KB to several times the 6 MB Lambda
    synchronous-response limit. Measured on real serialization, 1000 rows of 200 entries is 11.9 MB —
    the request fails outright and the caller loses even the truncation flags, which is strictly worse
    than returning fewer rows and saying so."""

    LAMBDA_LIMIT = 6 * 1024 * 1024

    def _rows(self, count, entries, pipeline_id="p1"):
        return [{"databaseId": "db", "assetId": f"a{i}", "filePath": f"/f{i}.glb", "scope": "asset",
                 "pipelineId": pipeline_id,
                 "metadata": {f"k_{j:04d}": "v" + "x" * 40 for j in range(entries)}}
                for i in range(count)]

    def _size(self, rows):
        return len(json.dumps(rows, default=str).encode("utf-8"))

    def test_a_row_capped_collection_still_fits_the_lambda_limit(self):
        rows = self._rows(le.MAX_DETAIL_INPUT_ROWS_RETURNED, 200)
        # The premise: at the row cap this collection is over the limit before trimming.
        assert self._size(rows) > self.LAMBDA_LIMIT
        flags = set()
        kept = le._trim_returned_rows(rows, "inputMetadata", flags)
        assert self._size(kept) <= le.MAX_DETAIL_COLLECTION_BYTES_RETURNED
        assert self._size(kept) < self.LAMBDA_LIMIT
        assert "inputMetadata" in flags, "a byte-trimmed collection must be flagged"

    def test_a_light_collection_is_returned_whole_and_unflagged(self):
        rows = self._rows(200, 20)
        assert self._size(rows) < le.MAX_DETAIL_COLLECTION_BYTES_RETURNED
        flags = set()
        assert le._trim_returned_rows(rows, "inputMetadata", flags) == rows
        assert flags == set()

    def test_the_byte_budget_keeps_every_pipeline_represented(self):
        # A trailing byte trim would cut whole pipelines off the end — the prefix-trim problem the
        # round-robin exists to avoid — so the budget is spent by the round-robin itself.
        rows = (self._rows(400, 200, "pA") + self._rows(400, 200, "pB")
                + self._rows(400, 200, "pC"))
        flags = set()
        kept = le._trim_returned_rows_per_pipeline(rows, "inputMetadata", flags)
        assert self._size(kept) <= le.MAX_DETAIL_COLLECTION_BYTES_RETURNED
        counts = {}
        for row in kept:
            counts[row["pipelineId"]] = counts.get(row["pipelineId"], 0) + 1
        assert set(counts) == {"pA", "pB", "pC"}, f"a pipeline was dropped entirely: {counts}"
        # Even shares, within one row of each other.
        assert max(counts.values()) - min(counts.values()) <= 1
        assert "inputMetadata" in flags

    def test_a_single_oversized_row_is_still_returned(self):
        # An empty collection would read as "this run captured nothing", which is a claim about the
        # run rather than a visible consequence of the cap.
        huge = [{"pipelineId": "p1", "filePath": "/big.glb",
                 "metadata": {"k": "x" * (le.MAX_DETAIL_COLLECTION_BYTES_RETURNED + 1024)}}]
        assert len(le._trim_returned_rows(list(huge), "inputMetadata", set())) == 1
        assert len(le._trim_returned_rows_per_pipeline(list(huge), "inputMetadata", set())) == 1

    def test_the_per_pipeline_trim_honors_a_caller_supplied_byte_budget(self):
        # The max_bytes parameter is what the assembly uses to hand each collection its share; a helper
        # that ignored it and used the module constant would let the collections overspend the ceiling.
        rows = self._rows(60, 200, "pA") + self._rows(60, 200, "pB")
        budget = 256 * 1024
        flags = set()
        kept = le._trim_returned_rows_per_pipeline(rows, "inputMetadata", flags, max_bytes=budget)
        assert self._size(kept) <= budget
        assert "inputMetadata" in flags


@pytest.mark.unit
class TestResponseByteBudgetAllocation:
    """The response budget is split files-first: the file collections are served before the metadata
    collections, and the metadata collections divide the remainder.

    Priority is not exemption — a run whose files alone would breach the ceiling has them trimmed and
    flagged too, because exceeding the Lambda synchronous-response limit returns a 502 with no body,
    which is strictly worse than a correctly flagged partial."""

    def test_light_files_leave_the_rest_to_metadata(self):
        file_budget, metadata_budget = le._allocate_detail_byte_budgets(1024)
        # Metadata gets everything the files did not use, well above its reserved floor.
        assert metadata_budget == le.DETAIL_RESPONSE_BYTE_CEILING - 1024
        assert metadata_budget > le.MIN_DETAIL_METADATA_BYTES_RETURNED
        assert file_budget + le.MIN_DETAIL_METADATA_BYTES_RETURNED <= le.DETAIL_RESPONSE_BYTE_CEILING

    def test_heavy_files_are_capped_and_metadata_keeps_its_floor(self):
        file_budget, metadata_budget = le._allocate_detail_byte_budgets(
            le.DETAIL_RESPONSE_BYTE_CEILING * 4)
        # Files are bounded rather than allowed to consume the whole ceiling...
        assert file_budget == (le.DETAIL_RESPONSE_BYTE_CEILING
                               - le.MIN_DETAIL_METADATA_BYTES_RETURNED)
        # ...and the metadata collections still get their reserved floor.
        assert metadata_budget == le.MIN_DETAIL_METADATA_BYTES_RETURNED

    def test_the_two_budgets_never_exceed_the_ceiling(self):
        # The floor is reserved OUT of the file allowance rather than added on top, so the response
        # cannot overflow by the floor's worth of bytes.
        for file_bytes in (0, 1024, 512 * 1024, 4 * 1024 * 1024,
                           le.DETAIL_RESPONSE_BYTE_CEILING,
                           le.DETAIL_RESPONSE_BYTE_CEILING * 10):
            file_budget, metadata_budget = le._allocate_detail_byte_budgets(file_bytes)
            granted = min(file_bytes, file_budget) + metadata_budget
            assert granted <= le.DETAIL_RESPONSE_BYTE_CEILING, (
                f"{file_bytes} bytes of files overflowed the ceiling")

    def test_budgets_are_never_negative_for_a_misconfigured_pair(self):
        # A floor larger than the ceiling degrades to "metadata only" rather than producing a negative
        # file budget, which would drop every row instead of trimming.
        file_budget, metadata_budget = le._allocate_detail_byte_budgets(
            10_000, ceiling=1000, metadata_floor=5000)
        assert file_budget >= 0 and metadata_budget >= 0
        assert metadata_budget <= 1000

    def test_a_file_heavy_execution_flags_its_file_collections(self):
        # The requester's ruling: cap the files too and use the truncation flag. inputFiles has no
        # paged route to escalate to, so the flag is the caller's only signal.
        rows = [{"databaseId": "db", "assetId": f"a{i}",
                 "inputAssetFileKey": "/" + "d" * 400 + f"/f{i}.glb", "versionId": "v" * 60}
                for i in range(4000)]
        flags = set()
        kept = le._trim_rows_to_byte_budget(rows, "inputFiles", flags, max_bytes=256 * 1024)
        assert len(kept) < len(rows)
        assert "inputFiles" in flags, "a trimmed file collection must be flagged, not silently cut"


@pytest.mark.unit
class TestAssembledResponseStaysWithinTheCeiling:
    """The whole assembled response — not just any one collection — must fit under the Lambda
    synchronous-response limit, with every trimmed collection named."""

    LAMBDA_LIMIT = 6 * 1024 * 1024

    def _assemble_heavy(self, input_file_count, md_rows_per_pipeline, md_entries):
        """Assemble with heavy input files AND heavy per-pipeline input metadata competing for the
        budget, so the files-first split is exercised end to end."""
        prows = [{"pipelineExecutionId": f"pe{i}", "pipelineId": f"p{i}",
                  "pipelineDatabaseId": "db", "S3AssetPipelineBucket": "bkt"} for i in range(3)]
        md = [{"databaseId": "db", "assetId": f"a{i}", "filePath": f"/f{i}.glb", "scope": "asset",
               "metadata": {f"k_{j:04d}": "v" + "x" * 40 for j in range(md_entries)}}
              for i in range(md_rows_per_pipeline)]
        files = [{"databaseId": "db", "assetId": f"a{i}",
                  "inputAssetFileKey": "/" + "d" * 300 + f"/f{i}.glb", "versionId": "v" * 60}
                 for i in range(input_file_count)]

        def _capped(table_name, key_condition, max_items):
            if table_name == le.pipeline_execution_input_metadata_table:
                return md[:max_items], len(md) > max_items
            if table_name == le.workflow_execution_inputs_table:
                return files[:max_items], len(files) > max_items
            return [], False

        with patch(f"{MOD}.get_workflow_definition", return_value={}), \
             patch(f"{MOD}.get_pipeline_definition", return_value={}), \
             patch(f"{MOD}.get_pipeline_definitions", return_value={}), \
             patch(f"{MOD}._query_all", return_value=[]), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=prows), \
             patch(f"{MOD}._query_capped", side_effect=_capped), \
             patch(f"{MOD}.get_produced_file_versions", return_value={}):
            return le.assemble_execution_details(
                "E1", {"workflowId": "wf", "workflowDatabaseId": "db"}, config_row={})

    def test_files_and_metadata_together_stay_under_the_lambda_limit(self):
        details = self._assemble_heavy(2000, 800, 200)
        size = len(json.dumps(details, default=str).encode("utf-8"))
        assert size < self.LAMBDA_LIMIT, f"assembled response was {size} bytes"
        # And the caller is told which sections are partial rather than being handed a silent subset.
        assert details["truncatedCollections"], "a bounded response must name what it cut"

    def test_a_file_heavy_run_still_returns_some_metadata(self):
        # The reserved floor exists so a file-heavy execution does not render empty metadata tables,
        # which would read as "this run captured no metadata".
        details = self._assemble_heavy(3000, 400, 100)
        assert details["inputMetadata"], "the metadata floor must survive a file-heavy run"

    def _assemble_with_configs(self, config_kb, steps=3, md_rows=400, md_entries=100):
        """Assemble with a rendered configuration body per step, which no bound can trim.

        The other harness patches _query_all to return nothing, so no configuration rows exist in it and
        the per-step envelope it measures is not the one that ships. A body is echoed twice per step (on
        the pipeline entry and in inputConfigurations), so a few large ones consume most of a megabyte
        before any collection is allocated a byte."""
        prows = [{"pipelineExecutionId": f"pe{i}", "pipelineId": f"p{i}",
                  "pipelineDatabaseId": "db", "S3AssetPipelineBucket": "bkt"} for i in range(steps)]
        body = "y" * (config_kb * 1024)
        md = [{"databaseId": "db", "assetId": f"a{i}", "filePath": f"/f{i}.glb", "scope": "asset",
               "metadata": {f"k_{j:04d}": "v" + "x" * 40 for j in range(md_entries)}}
              for i in range(md_rows)]

        def _capped(table_name, key_condition, max_items):
            if table_name == le.pipeline_execution_input_metadata_table:
                return md[:max_items], len(md) > max_items
            return [], False

        def _all(table_name, key_condition):
            if table_name == le.pipeline_execution_input_configuration_table:
                return [{"inputConfiguration": body, "inputConfigurationTruncated": False}]
            return []

        with patch(f"{MOD}.get_workflow_definition", return_value={}),              patch(f"{MOD}.get_pipeline_definition", return_value={}),              patch(f"{MOD}.get_pipeline_definitions", return_value={}),              patch(f"{MOD}._query_all", side_effect=_all),              patch(f"{MOD}.get_pipeline_execution_rows", return_value=prows),              patch(f"{MOD}._query_capped", side_effect=_capped),              patch(f"{MOD}.get_produced_file_versions", return_value={}):
            return le.assemble_execution_details(
                "E1", {"workflowId": "wf", "workflowDatabaseId": "db"}, config_row={})

    def test_large_rendered_configs_do_not_push_the_response_over_the_limit(self):
        # 3 steps x 380 KB, echoed twice each: ~2.2 MB of untrimmable envelope. The collections must
        # yield to it rather than the response overflowing.
        details = self._assemble_with_configs(380)
        size = len(json.dumps(details, default=str).encode("utf-8"))
        assert size < self.LAMBDA_LIMIT, f"assembled response was {size} bytes"
        assert details["truncatedCollections"], "a bounded response must name what it cut"

    def test_every_step_is_still_reported_when_its_config_is_large(self):
        # The envelope is charged, never trimmed: dropping a step to make room would misreport what ran.
        details = self._assemble_with_configs(380)
        assert len(details["pipelines"]) == 3

    def test_results_are_served_before_the_collections_that_can_be_paged(self):
        """outputs.results has no paged route, so a row trimmed from it is unreachable.

        inputMetadata does have one. Granting the shared metadata allowance in reporting order let a
        large inputMetadata starve outputs.results to a single row — the collection with a fallback
        crowding out the one without."""
        prows = [{"pipelineExecutionId": "pe0", "pipelineId": "p0",
                  "pipelineDatabaseId": "db", "S3AssetPipelineBucket": "bkt"}]
        heavy_md = [{"databaseId": "db", "assetId": f"a{i}", "filePath": f"/f{i}.glb",
                     "scope": "asset",
                     "metadata": {f"k_{j:04d}": "v" + "x" * 200 for j in range(300)}}
                    for i in range(400)]
        # The scrubber keeps relativeFilePath/resultsContent, so rows must carry THOSE fields or they
        # serialize to near-nothing and never compete for the allowance.
        results = [{"relativeFilePath": f"/out/r{i}.json",
                    "resultsContent": "z" * 4096} for i in range(50)]

        def _capped(table_name, key_condition, max_items):
            if table_name == le.pipeline_execution_input_metadata_table:
                return heavy_md[:max_items], len(heavy_md) > max_items
            if table_name == le.pipeline_execution_output_results_table:
                return results[:max_items], len(results) > max_items
            return [], False

        with patch(f"{MOD}.get_workflow_definition", return_value={}),              patch(f"{MOD}.get_pipeline_definition", return_value={}),              patch(f"{MOD}.get_pipeline_definitions", return_value={}),              patch(f"{MOD}._query_all", return_value=[]),              patch(f"{MOD}.get_pipeline_execution_rows", return_value=prows),              patch(f"{MOD}._query_capped", side_effect=_capped),              patch(f"{MOD}.get_produced_file_versions", return_value={}):
            details = le.assemble_execution_details(
                "E1", {"workflowId": "wf", "workflowDatabaseId": "db"}, config_row={})
        assert len(details["outputs"]["results"]) == len(results), (
            "results were starved by a collection that has a paged route to fall back on")

    def test_a_small_execution_is_returned_whole_and_unflagged(self):
        details = self._assemble_heavy(5, 4, 3)
        assert details["truncatedCollections"] == []
        assert len(details["inputFiles"]) == 5
        # 3 pipelines x 4 rows, each pipeline's rows reported distinctly.
        assert len(details["inputMetadata"]) == 12


@pytest.mark.unit
class TestByteBudgetEdgeCases:
    """Direct coverage of _trim_rows_to_byte_budget's own boundaries.

    The surrounding allocation tests exercise this function only through the assembled response, so
    they cannot distinguish "kept a partial prefix" from "returned nothing". Each case below is one
    where returning the wrong thing would be silent: an empty collection and a fully-returned one
    look identical to a caller that does not also read the truncation flags."""

    def test_a_single_row_larger_than_the_whole_budget_is_still_returned(self):
        """A legitimately huge metadata row must not vanish.

        Returning [] here would render the collection as 'this run captured nothing' — the flag alone
        does not distinguish that from an empty result, so the row is kept even though it overruns."""
        flags = set()
        oversized = {"databaseId": "db", "assetId": "a1", "metadata": {"k": "x" * 5000}}
        kept = le._trim_rows_to_byte_budget([oversized], "inputMetadata", flags, max_bytes=100)
        assert kept == [oversized]
        # Nothing was DROPPED, so nothing is flagged: the caller received every row there was.
        assert flags == set()

    def test_an_oversized_first_row_still_ends_the_collection_for_the_rest(self):
        """The oversized row is kept, but it has spent the budget: later rows are dropped and flagged."""
        flags = set()
        oversized = {"databaseId": "db", "assetId": "a1", "metadata": {"k": "x" * 5000}}
        follower = {"databaseId": "db", "assetId": "a2", "metadata": {"k": "y"}}
        kept = le._trim_rows_to_byte_budget(
            [oversized, follower], "inputMetadata", flags, max_bytes=100)
        assert kept == [oversized]
        assert flags == {"inputMetadata"}

    def test_an_empty_collection_is_not_flagged_as_truncated(self):
        """A flag on an empty collection reads as 'there was more', which is false."""
        flags = set()
        assert le._trim_rows_to_byte_budget([], "outputs.metadata", flags, max_bytes=100) == []
        assert flags == set()

    def test_a_collection_that_fits_is_returned_whole_and_unflagged(self):
        flags = set()
        rows = [{"databaseId": "db", "assetId": f"a{i}"} for i in range(3)]
        assert le._trim_rows_to_byte_budget(rows, "inputFiles", flags, max_bytes=1024) == rows
        assert flags == set()

    @pytest.mark.parametrize("unmeasurable", ["circular", "raising_str"])
    def test_a_row_json_cannot_measure_does_not_drop_the_collection(self, unmeasurable):
        """`json.dumps(default=str)` still raises on a circular reference or a raising `__str__`.

        `default=` only substitutes UNKNOWN TYPES; it does not rescue either of these. The measuring
        `except` treats such a row as zero bytes and keeps going, so one unmeasurable row cannot
        swallow the rows after it or fail the whole details request — the caller's own serializer
        raises later if the row is genuinely unserializable."""
        if unmeasurable == "circular":
            bad = {}
            bad["self"] = bad
        else:
            class _RaisesOnStr:
                def __str__(self):
                    raise RuntimeError("cannot stringify")

                def __repr__(self):
                    raise RuntimeError("cannot repr either")
            bad = {"value": _RaisesOnStr()}
        # Precondition: this row really is unmeasurable, or the test proves nothing.
        with pytest.raises(Exception):
            json.dumps(bad, default=str)

        flags = set()
        follower = {"databaseId": "db", "assetId": "a2"}
        kept = le._trim_rows_to_byte_budget([bad, follower], "inputMetadata", flags, max_bytes=1024)
        assert len(kept) == 2 and kept[1] == follower
        assert flags == set()
