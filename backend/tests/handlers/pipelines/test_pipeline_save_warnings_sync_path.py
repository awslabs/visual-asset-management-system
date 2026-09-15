# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-062 (owner constraint): the trigger-template warning check stays on the SYNCHRONOUS save path.

`pipeline_trigger_template_warnings` is expensive — it pages the workflows table on every pipeline
create/update whose systemConfig sets `requireTemplate` — and the obvious way to make a save fast is
to defer it to an async invoke or drop it from the save path. The repository owner ruled that out:
the warning has to come back with the save response, so the fix is to bound and project the read, not
to move it. This test encodes that constraint so a later "optimisation" fails the build rather than
quietly removing the warning.

The bounding/projection behaviour itself is covered in
tests/common/test_triggerTemplateValidation_scan_bounds.py."""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.pipelines import pipelineService as ps


@pytest.mark.unit
class TestPipelineSaveWarningsStaySynchronous:
    """_pipeline_save_warnings is what the create/update handlers call before they return, so the
    check has to be reached from it directly."""

    ITEM = {"databaseId": "db1", "pipelineId": "pipe1",
            "systemConfig": {"requireTemplate": True}}

    def test_the_check_runs_inline_and_its_warnings_reach_the_response(self):
        """FIX-062: the check is called from the save path and its output is returned to the caller."""
        sentinel = "SENTINEL-TRIGGER-TEMPLATE-WARNING"
        with patch.object(ps, "pipeline_trigger_template_warnings",
                          return_value=[sentinel]) as checked, \
             patch.object(ps, "_workflow_table", MagicMock()), \
             patch.object(ps, "arity_none_metadata_warnings", return_value=[]):
            warnings = ps._pipeline_save_warnings(dict(self.ITEM))
        assert checked.call_count == 1, "the check did not run on the save path"
        assert sentinel in warnings, "the check's warnings did not reach the save response"
        # requireTemplate is what gates the check; the ids identify the pipeline being saved.
        args = checked.call_args.args
        assert args[2] == "db1" and args[3] == "pipe1" and args[4] is True

    def test_a_pipeline_that_requires_no_template_still_skips_the_read(self):
        """FIX-062 control: the gate is the requireTemplate flag, not the presence of the call.

        Without this, the test above passes for an implementation that runs the workflows read on
        every save regardless — which is the cost this fix exists to bound."""
        item = {"databaseId": "db1", "pipelineId": "pipe1",
                "systemConfig": {"requireTemplate": False}}
        with patch.object(ps, "pipeline_trigger_template_warnings",
                          side_effect=ps.pipeline_trigger_template_warnings), \
             patch.object(ps, "_workflow_table") as workflow_table, \
             patch.object(ps, "arity_none_metadata_warnings", return_value=[]):
            assert ps._pipeline_save_warnings(item) == []
        workflow_table.return_value.scan.assert_not_called()
        workflow_table.return_value.query.assert_not_called()
