# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The first pipeline step records a start date at launch.

Steps 2+ are stamped by the interim tracking lambda as it advances INTO them
(`_stamp_pipeline_start_date`), but nothing ever advances into step 1 — it is set RUNNING directly at
launch. So step 1 is the one step whose start date has to be written by the launch path, and without it
the row keeps the builder's empty-string default: a finished multi-step execution reports a duration for
every step except the first, and the details view shows an empty executionStartDate on step 1.
"""

import pathlib
import re

import pytest

from backend.backend.common.workflows import executionRecords as er

# Read from disk rather than importing: executeWorkflow resolves resource names and builds AWS clients
# at import time, so importing it needs the full mocked-table bootstrap. The assertions here are about
# a statement pair in the launch path, which the source answers directly.
EXECUTE_WORKFLOW_SOURCE = (
    pathlib.Path(__file__).resolve().parents[3]
    / "backend" / "handlers" / "workflows" / "executeWorkflow.py"
).read_text(encoding="utf-8")


@pytest.mark.unit
class TestPipelineRecordDefault:
    """The builder itself leaves the start date empty — which is why the launch path must set it.

    executionStartDate is a GSI sort key and DynamoDB rejects an empty string for an indexed key
    attribute, so the builder cannot default it to a placeholder; it is written only once real.
    """

    def test_a_freshly_built_pipeline_row_has_no_start_date(self):
        row = er.build_pipeline_execution_record(
            pipeline_execution_id="pe1", workflow_execution_id="we1",
            pipeline_database_id="db", pipeline_id="pipe",
            end_state_pipeline=True, s3_asset_bucket="bucket", s3_aux_bucket="aux",
            output_prefixes={}, input_metadata_file_prefix="", input_config_file_prefix="",
            aux_temp_prefix="", aux_preview_prefix="",
            pipeline_execution_type="Lambda", wait_for_callback=False,
            pipeline_resource_arn="arn")
        assert row["executionStartDate"] == ""
        assert row["executionStatus"] == "NEW"


@pytest.mark.unit
class TestLaunchStampsTheFirstStep:
    """The launch path stamps step 1's start date alongside its NEW -> RUNNING flip.

    Asserted against the source of executeWorkflow because the surrounding write is a DynamoDB
    batch_writer loop over resolved pipeline records; reading the statement pair directly keeps the
    check on the behavior that broke (status set without a start date) rather than reconstructing the
    whole handler.
    """

    @staticmethod
    def _first_step_block():
        """The `idx == 0` branch that mutates the built pipeline-execution record.

        There is more than one `if idx == 0:` in this handler (config rendering has its own), so the
        branch is located by the record variable it writes rather than by the condition alone — an
        unanchored match found the config-rendering block and reported a passing fix as broken.
        """
        match = re.search(
            r"if idx == 0:\n(?P<body>(?:[ \t]+pexec_record\[.*\n)+)", EXECUTE_WORKFLOW_SOURCE)
        assert match, (
            "no `if idx == 0:` branch writing pexec_record[...] found in the launch path")
        return match.group("body")

    def test_the_first_step_is_set_running(self):
        assert 'executionStatus"] = "RUNNING"' in self._first_step_block()

    def test_the_first_step_also_gets_a_start_date(self):
        """The regression: RUNNING was set without a start date, so step 1 reported no duration."""
        body = self._first_step_block()
        assert 'executionStartDate"] = start_date' in body, (
            "step 1 is set RUNNING without stamping executionStartDate; nothing else stamps it, so "
            "the row keeps the builder's empty default")

    def test_the_stamp_uses_the_same_timestamp_as_the_execution_row(self):
        """A start date later than the workflow's own would make step 1 look like it began late."""
        # The one timestamp the main execution row and the per-input rows are built from.
        assert re.search(r"^\s*start_date = er\.iso_now\(\)", EXECUTE_WORKFLOW_SOURCE, re.MULTILINE), (
            "start_date is no longer the single launch timestamp; the first-step stamp would drift "
            "from the execution row")
        assert "execution_start_date=start_date" in EXECUTE_WORKFLOW_SOURCE
