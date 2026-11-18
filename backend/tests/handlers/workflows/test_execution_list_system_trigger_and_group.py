# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A listing narrowed to a system trigger value or to an execution group shows system runs by default
(#316 with #1121 and #1135).

System runs are hidden from the execution lists by default. A run a system feature launched is recorded
under a system trigger value (common.workflows.systemPipelines.is_system_trigger: System-<Identifier>) and
usually in an execution group of its batch; a listing narrowed to that trigger value or to one group would
otherwise always be empty for such runs. Core decides it by format alone: any well-formed system trigger
value, and any execution group, never a named value or group prefix. An explicit includeSystem=false still
hides them, another trigger keeps the default, and per-row visibility is unchanged.

The vector-search reindex runs (System-Reindex, group vec-<runId>-<chunk>) are one such case; a neutral
value shows the rule names none."""

import pytest

from tests.handlers.workflows.test_execution_list_system_runs import (
    _ScriptedIndex, _deny_workflow, _ids, _row, _run_asset, _run_global, le,
)


def _system_row(i, trigger="System-Reindex", group="vec-r1-0"):
    row = dict(_row("s", i, True, workflow="sgm", db="GLOBAL"), triggerType=trigger)
    if group:
        row["executionGroupId"] = group
    return row


def _user_row(i, **over):
    return {**_row("u", i, False), "triggerType": "Manual", **over}


@pytest.mark.unit
class TestASystemTriggerFilter:
    @pytest.mark.parametrize("trigger", ["System-Reindex", "System-TestFeature"])
    def test_the_global_list_shows_that_triggers_runs(self, trigger):
        rows = [_system_row(0, trigger), _user_row(1), _system_row(2, trigger)]
        status, message = _run_global(_ScriptedIndex(rows), {"pageSize": "50", "triggerType": trigger})
        assert status == 200
        assert _ids(message) == ["s000", "s002"]
        assert message["includeSystem"] is True

    def test_the_earlier_request_value_is_no_system_trigger(self):
        # systemReindex is not a system trigger value (no System- prefix) and maps to nothing, so it
        # matches no stored row and keeps the default.
        rows = [_system_row(0), _user_row(1)]
        _status, message = _run_global(_ScriptedIndex(rows), {"pageSize": "50", "triggerType": "systemReindex"})
        assert message["Items"] == [] and message["includeSystem"] is False

    def test_an_explicit_false_still_hides_them(self):
        status, message = _run_global(
            _ScriptedIndex([_system_row(0)]),
            {"pageSize": "50", "triggerType": "System-Reindex", "includeSystem": "false"})
        assert status == 200
        assert message["Items"] == [] and message["includeSystem"] is False

    def test_another_trigger_keeps_the_default(self):
        rows = [dict(_system_row(0), triggerType="File-Upload"), _user_row(1, triggerType="File-Upload")]
        _status, message = _run_global(_ScriptedIndex(rows), {"pageSize": "50", "triggerType": "fileUpload"})
        assert _ids(message) == ["u001"] and message["includeSystem"] is False

    def test_the_asset_list_shows_that_triggers_runs(self):
        rows = [_system_row(0), _user_row(1)]
        status, message = _run_asset(rows, {"triggerType": "System-Reindex"})
        assert status == 200
        assert _ids(message) == ["s000"]
        assert message["includeSystem"] is True


@pytest.mark.unit
class TestAGroupFilter:
    @pytest.mark.parametrize("group", ["vec-r1-0", "vec-unarchive-a1b2-0", "nightly-batch-1"])
    def test_the_global_list_shows_the_groups_system_runs(self, group):
        rows = [_system_row(0, group=group), _system_row(1, group=group), _user_row(2, executionGroupId=group)]
        status, message = _run_global(_ScriptedIndex(rows), {"pageSize": "50", "groupId": group})
        assert status == 200
        assert _ids(message) == ["s000", "s001", "u002"]
        assert message["includeSystem"] is True

    def test_an_explicit_false_still_hides_them(self):
        _status, message = _run_global(_ScriptedIndex([_system_row(0)]),
                                       {"pageSize": "50", "groupId": "vec-r1-0", "includeSystem": "false"})
        assert message["Items"] == []

    def test_a_blank_group_keeps_the_default(self):
        _status, message = _run_global(_ScriptedIndex([_system_row(0), _user_row(1)]),
                                       {"pageSize": "50", "groupId": "  "})
        assert _ids(message) == ["u001"] and message["includeSystem"] is False


@pytest.mark.unit
class TestTheDefaultIsPresentationOnly:
    def test_a_caller_who_may_not_get_the_workflow_still_sees_no_system_run(self):
        rows = [_system_row(0), _user_row(1)]
        status, message = _run_global(_ScriptedIndex(rows), {"pageSize": "50", "triggerType": "System-Reindex"},
                                      enforcer=_deny_workflow("sgm"))
        assert status == 200
        assert message["Items"] == []

    def test_resolve_include_system_keys_on_the_format_and_the_group(self):
        assert le._resolve_include_system(None, None, "", "", trigger_type="System-Reindex") is True
        assert le._resolve_include_system(None, None, "", "", trigger_type="System-AnyFeature") is True
        assert le._resolve_include_system(None, None, "", "", group_id="any-group") is True
        assert le._resolve_include_system(None, None, "", "", trigger_type="Manual", group_id="") is False
        assert le._resolve_include_system(None, None, "", "", trigger_type="System-") is False
        assert le._resolve_include_system(False, None, "", "", trigger_type="System-Reindex") is False
