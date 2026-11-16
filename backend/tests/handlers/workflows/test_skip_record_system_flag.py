# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A system workflow's skipped trigger launch is hidden with its runs (#1121 rule, #316 D115).

The execution lists hide a run whose main row carries isSystem True, which the launch copies from the
workflow row. A fileUpload trigger launch refused by the workflow's concurrency restriction is recorded as
a SKIPPED execution (#998) whose main row the skip writer builds itself; it now records the workflow's
flag the same way. The SYSTEM GenAI metadata workflow is locked per input file version, so a repeated
upload event of one version is the case: its runs are hidden, and its skipped launch was listed as a user
run."""

import pytest

from tests.handlers.workflows import test_execute_per_input_file_version_lock as lock_tests
from tests.handlers.workflows.test_skipped_trigger_launch_records import (
    _INPUT, ASSET_KEY, HOLDER, SKIP_ID, SKIP_ROW, _ConfigTable, _InputsTable, _launch_refused, _list, _MainTable,
    er, es,
)


def _stored(update, attribute):
    """The value an UpdateItem built by build_skip_record_update sets `attribute` to."""
    names = {name: alias for alias, name in update["ExpressionAttributeNames"].items()}
    return update["ExpressionAttributeValues"][":" + names[attribute][1:]]


def _builder_update(**kwargs):
    return er.build_skip_record_update(
        execution_id=er.skip_record_execution_id(ASSET_KEY, HOLDER), workflow_database_id="db1",
        workflow_id="wf1", workflow_arn="arn:sm", skipped_at="2026-11-16T10:00:00Z", skip_scope="asset",
        skip_reason="held", blocking_execution_id=HOLDER, **kwargs)


@pytest.mark.unit
class TestTheSkipRecordCarriesTheWorkflowFlag:
    def test_a_system_workflow_s_record_is_marked_system(self):
        update = _builder_update(is_system=True)
        assert _stored(update, "isSystem") is True
        # Set by the first skip only, like the record's identity.
        assert "#a" in update["UpdateExpression"] and "if_not_exists" in update["UpdateExpression"]

    @pytest.mark.parametrize("value", [False, "true", 1, None])
    def test_anything_but_true_is_a_user_record(self, value):
        assert _stored(_builder_update(is_system=value), "isSystem") is False

    def test_the_default_is_a_user_record(self):
        assert _stored(_builder_update(), "isSystem") is False


@pytest.mark.unit
class TestTheExecuteHandlerMarksASystemWorkflowsSkippedLaunch:
    @pytest.mark.parametrize("stored, expected", [(True, True), (False, False), ("absent", False), ("true", False)])
    def test_the_skip_record_takes_the_workflow_row_s_flag(self, monkeypatch, stored, expected):
        user_workflow = lock_tests._workflow

        def workflow(restriction):
            row = user_workflow(restriction)
            if stored != "absent":
                row["isSystem"] = stored
            return row

        monkeypatch.setattr(lock_tests, "_workflow", workflow)
        main = _MainTable()
        response, _sfn, _log, _stop = _launch_refused(main, _InputsTable(), config=_ConfigTable())
        assert response["statusCode"] == 400
        assert len(main.updates) == 1
        assert _stored(main.updates[0], "isSystem") is expected


@pytest.mark.unit
class TestTheListsReadTheFlagOffTheSkipRecord:
    def test_a_system_skip_record_lists_as_system(self):
        row = dict(SKIP_ROW, isSystem=True)
        assert _list({SKIP_ID: row}, [_INPUT])[0]["isSystem"] is True
        assert es._global_list_row(dict(row))["isSystem"] is True

    def test_a_skip_record_written_before_the_flag_lists_as_a_user_run(self):
        row = {key: value for key, value in SKIP_ROW.items() if key != "isSystem"}
        assert _list({SKIP_ID: row}, [_INPUT])[0]["isSystem"] is False
