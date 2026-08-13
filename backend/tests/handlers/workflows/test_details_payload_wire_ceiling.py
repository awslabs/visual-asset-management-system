# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Byte budgets of the detail and paged-metadata views measured in the units the response is SENT in.

The response body is carried as a JSON *string* (models.common.success serializes it and the integration
returns the object verbatim), so every quote and backslash in a row is escaped a second time on the way
out. A budget measured on the plain serialization therefore under-counts — 1.4x on escape-heavy values,
approaching 2x on quote-dense ones — and a view assembled inside a 5 MiB budget can emit a payload past
the 6 MB Lambda synchronous-response limit. That failure returns a 502 with no body at all, losing the
rows AND the truncatedCollections flags that exist to report a bounded view.

These tests build metadata whose values are quote- and backslash-heavy (JSON documents captured as
metadata values, which is what a pipeline writing structured results produces) and assert on the size of
the payload as Lambda returns it, not on the size of the rows."""

import json
import os

import pytest

# executionService resolves these at import (mirrors test_details_per_pipeline_metadata.py).
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

from backend.backend.handlers.workflows import executionService as le  # noqa: E402
from backend.backend.models import common as mc  # noqa: E402

# The Lambda synchronous-response limit. A payload over it fails the whole request.
LAMBDA_RESPONSE_LIMIT = 6 * 1024 * 1024


def _escape_heavy_value(approx_bytes):
    """A metadata value that is quote- and backslash-dense: a JSON document captured as a string, which
    is what a pipeline emitting structured results writes into a metadata value."""
    unit = json.dumps({"path": "C:\\models\\part.glb", "note": 'he said "ok"', "n": 1})
    return (unit * (approx_bytes // len(unit) + 1))[:approx_bytes]


def _md_row(pexec, index, value_bytes, entries=8):
    return {
        "pipelineExecutionId": pexec,
        "pipelineId": pexec.replace("pe-", ""),
        "databaseId": "db1",
        "assetId": "asset1",
        "filePath": f"/f{index:05d}.glb",
        "metadata": {f"k{i:03d}": _escape_heavy_value(value_bytes) for i in range(entries)},
    }


def _sent_payload_bytes(details):
    """Size of the response as Lambda returns it, built through the REAL response helper."""
    return len(json.dumps(mc.success(body={'message': details})).encode("utf-8"))


@pytest.mark.unit
class TestWireMeasurement:
    """The measurement itself: escaping is counted, and it is what the budgets are expressed in."""

    def test_escape_heavy_rows_measure_larger_on_the_wire_than_serialized(self):
        row = _md_row("pe-p1", 0, 4096)
        plain = len(json.dumps(row, default=str).encode("utf-8"))
        wire = le._wire_bytes(row)
        assert wire > plain * 1.3, (
            f"escaping is not being counted: plain {plain} vs wire {wire}")

    def test_a_rows_wire_measure_matches_what_the_response_carries(self):
        # The measure must be the size the row costs in the emitted body, not an arbitrary inflation.
        rows = [_md_row("pe-p1", i, 2048) for i in range(4)]
        empty = _sent_payload_bytes({"inputMetadata": []})
        full = _sent_payload_bytes({"inputMetadata": rows})
        measured = le._rows_serialized_bytes(rows)
        # Within the separators/brackets the row measure does not include.
        assert measured <= full - empty + 8 * len(rows)
        assert measured >= (full - empty) * 0.9, (
            f"the row measure {measured} under-counts the {full - empty} bytes it costs the response")

    def test_the_paged_route_row_measure_uses_the_same_units(self):
        row = _md_row("pe-p1", 0, 4096)
        assert le._detail_metadata_row_bytes(row) == le._wire_bytes(row)


@pytest.mark.unit
class TestPayloadCeiling:
    """The finished payload is measured and trimmed against, so the emitted response fits the limit."""

    @staticmethod
    def _details(md_rows=(), file_rows=(), result_rows=()):
        return {
            "workflowExecutionId": "e1",
            "pipelines": [{"pipelineId": "p1", "renderedConfig": ""}],
            "inputFiles": list(file_rows),
            "inputMetadata": list(md_rows),
            "inputDatabaseMetadata": [],
            "inputConfigurations": [],
            "outputs": {"files": [], "metadata": [], "results": list(result_rows)},
            "truncatedCollections": [],
        }

    def test_a_collection_filling_the_budget_plainly_would_emit_over_the_lambda_limit(self):
        # The failure mode itself: rows whose PLAIN serialization fits DETAIL_RESPONSE_BYTE_CEILING emit
        # a payload past the 6 MB limit, which returns a 502 with no body — and so no truncation flags.
        rows = []
        plain = 0
        while True:
            row = _md_row("pe-p1", len(rows), 8192)
            row_plain = len(json.dumps(row, default=str).encode("utf-8"))
            if plain + row_plain > le.DETAIL_RESPONSE_BYTE_CEILING:
                break
            rows.append(row)
            plain += row_plain
        assert _sent_payload_bytes(self._details(md_rows=rows)) > LAMBDA_RESPONSE_LIMIT, (
            "these rows do not reproduce the overflow; the escaping is not heavy enough")

        # The same rows measured in the units the response is sent in do not fit the budget, so the trim
        # drops the excess and flags the collection.
        flags = set()
        kept = le._trim_rows_to_byte_budget(
            rows, "inputMetadata", flags, max_bytes=le.DETAIL_RESPONSE_BYTE_CEILING)
        assert flags == {"inputMetadata"}, "a byte-trimmed collection must be flagged"
        assert len(kept) < len(rows)
        details = self._details(md_rows=kept)
        details["truncatedCollections"] = sorted(flags)
        le._enforce_detail_payload_ceiling(details)
        sent = _sent_payload_bytes(details)
        assert sent < LAMBDA_RESPONSE_LIMIT, f"emitted payload is {sent} bytes"

    def test_an_over_ceiling_payload_is_trimmed_under_the_limit_and_flagged(self):
        rows = [_md_row("pe-p1", i, 16384) for i in range(64)]
        details = self._details(md_rows=rows)
        assert _sent_payload_bytes(details) > le.DETAIL_PAYLOAD_WIRE_CEILING, (
            "the payload already fits; the ceiling never fires and the test proves nothing")
        le._enforce_detail_payload_ceiling(details)
        sent = _sent_payload_bytes(details)
        assert sent <= le.DETAIL_PAYLOAD_WIRE_CEILING, f"payload still {sent} bytes"
        assert sent < LAMBDA_RESPONSE_LIMIT
        assert "inputMetadata" in details["truncatedCollections"], (
            "rows were dropped without naming the collection")
        assert details["inputMetadata"], "the collection was emptied rather than trimmed"

    def test_a_payload_within_the_ceiling_is_untouched_and_unflagged(self):
        rows = [_md_row("pe-p1", i, 512) for i in range(4)]
        details = self._details(md_rows=rows)
        assert le._enforce_detail_payload_ceiling(details) == _sent_payload_bytes(details)
        assert details["inputMetadata"] == rows
        assert details["truncatedCollections"] == []

    def test_metadata_is_surrendered_before_files(self):
        # Round-9 locked order: files get the budget first, so they are the last thing given up.
        md = [_md_row("pe-p1", i, 16384) for i in range(48)]
        files = [{"pipelineExecutionId": "pe-p1", "pipelineId": "p1", "databaseId": "db1",
                  "assetId": "asset1", "filePath": f"/in{i:05d}.glb"} for i in range(200)]
        details = self._details(md_rows=md, file_rows=files)
        le._enforce_detail_payload_ceiling(details)
        assert len(details["inputMetadata"]) < len(md), "metadata was not trimmed first"
        assert details["inputFiles"] == files, "files were trimmed while metadata rows remained"
        assert "inputFiles" not in details["truncatedCollections"]
        assert "inputMetadata" in details["truncatedCollections"]

    def test_every_collection_the_ceiling_takes_rows_from_is_named(self):
        # A payload so far over the ceiling that the metadata collections alone cannot clear it: whatever
        # else it reaches into must be flagged too, so no collection is ever returned partial unflagged.
        md = [_md_row("pe-p1", i, 16384) for i in range(48)]
        results = [{"pipelineExecutionId": "pe-p1", "pipelineId": "p1", "resultKey": f"r{i}",
                    "resultValue": _escape_heavy_value(16384)} for i in range(200)]
        details = self._details(md_rows=md, result_rows=results)
        before = {"inputMetadata": list(md), "outputs.results": list(results)}
        le._enforce_detail_payload_ceiling(details)
        assert _sent_payload_bytes(details) <= le.DETAIL_PAYLOAD_WIRE_CEILING
        flags = set(details["truncatedCollections"])
        for name, original in before.items():
            current = (details["outputs"]["results"] if name == "outputs.results"
                       else details["inputMetadata"])
            if len(current) < len(original):
                assert name in flags, f"{name} lost rows without being flagged"

    def test_an_existing_truncation_flag_survives_the_ceiling_pass(self):
        rows = [_md_row("pe-p1", i, 512) for i in range(4)]
        details = self._details(md_rows=rows)
        details["truncatedCollections"] = ["outputs.files"]
        le._enforce_detail_payload_ceiling(details)
        assert "outputs.files" in details["truncatedCollections"]


@pytest.mark.unit
class TestPagedRouteBudget:
    """The paged metadata route's page budget is in the same units, so a page fits the limit too."""

    def test_a_full_page_of_escape_heavy_rows_emits_under_the_lambda_limit(self):
        rows = []
        used = 0
        while True:
            row = _md_row("pe-p1", len(rows), 8192)
            row_bytes = le._detail_metadata_row_bytes(row)
            if rows and used + row_bytes > le.MAX_DETAIL_METADATA_PAGE_BYTES:
                break
            rows.append(row)
            used += row_bytes
        plain = sum(len(json.dumps(r, default=str).encode("utf-8")) for r in rows)
        assert plain < le.MAX_DETAIL_METADATA_PAGE_BYTES, (
            "the page already exceeds the budget measured plainly; the test proves nothing")
        sent = _sent_payload_bytes({"Items": rows})
        assert sent < LAMBDA_RESPONSE_LIMIT, f"emitted page is {sent} bytes"
