# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A listing narrowed to the vector-search reindexer's runs shows them by default (#316, with #1121).

The reindexer launches the SYSTEM GenAI metadata workflow, a system workflow, with the stored trigger type
System-Reindex and an execution group vec-<runId>-<chunk>. Its runs are system runs, hidden from the
execution lists by default; a listing narrowed to that trigger (the Executions page's "System reindex"
trigger filter) or to such a group (`vamscli execution list --group-id vec-<runId>-0`, the command the
vectorBackfill step prints) would then always be empty. Such a listing shows system runs by default, as a
listing narrowed to one system workflow does, and an explicit includeSystem=false still hides them. Per-row
visibility is unchanged."""

import pytest

from tests.handlers.workflows.test_execution_list_system_runs import (
    _ScriptedIndex, _ids, _row, _run_asset, _run_global, er, le,
)


def _reindex_row(i, group="vec-r1-0"):
    return dict(_row("r", i, True, workflow="sgm", db="GLOBAL"), triggerType="System-Reindex",
                executionGroupId=group)


def _user_row(i):
    return dict(_row("u", i, False), triggerType="Manual")


@pytest.mark.unit
class TestTheReindexTriggerFilter:
    @pytest.mark.parametrize("trigger", ["System-Reindex", "systemReindex"])
    def test_the_global_list_shows_the_reindex_runs(self, trigger):
        rows = [_reindex_row(0), _user_row(1), _reindex_row(2)]
        status, message = _run_global(_ScriptedIndex(rows), {"pageSize": "50", "triggerType": trigger})
        assert status == 200
        assert _ids(message) == ["r000", "r002"]
        assert message["includeSystem"] is True

    def test_an_explicit_false_still_hides_them(self):
        rows = [_reindex_row(0)]
        status, message = _run_global(
            _ScriptedIndex(rows), {"pageSize": "50", "triggerType": "System-Reindex", "includeSystem": "false"})
        assert status == 200
        assert message["Items"] == [] and message["includeSystem"] is False

    def test_another_trigger_keeps_the_default(self):
        rows = [dict(_reindex_row(0), triggerType="File-Upload"), dict(_user_row(1), triggerType="File-Upload")]
        _status, message = _run_global(_ScriptedIndex(rows), {"pageSize": "50", "triggerType": "fileUpload"})
        assert _ids(message) == ["u001"] and message["includeSystem"] is False

    def test_the_asset_list_shows_the_reindex_runs(self):
        rows = [_reindex_row(0), _user_row(1)]
        status, message = _run_asset(rows, {"triggerType": "System-Reindex"})
        assert status == 200
        assert _ids(message) == ["r000"]
        assert message["includeSystem"] is True


@pytest.mark.unit
class TestTheReindexGroupFilter:
    @pytest.mark.parametrize("group", ["vec-r1-0", "vec-unarchive-a1b2-0"])
    def test_the_global_list_shows_the_group_s_runs(self, group):
        rows = [_reindex_row(0, group), _reindex_row(1, group)]
        status, message = _run_global(_ScriptedIndex(rows), {"pageSize": "50", "groupId": group})
        assert status == 200
        assert _ids(message) == ["r000", "r001"]
        assert message["includeSystem"] is True

    def test_another_group_keeps_the_default(self):
        rows = [_reindex_row(0, "grp-1"), dict(_user_row(1), executionGroupId="grp-1")]
        _status, message = _run_global(_ScriptedIndex(rows), {"pageSize": "50", "groupId": "grp-1"})
        assert _ids(message) == ["u001"] and message["includeSystem"] is False

    def test_an_explicit_false_still_hides_them(self):
        _status, message = _run_global(_ScriptedIndex([_reindex_row(0)]),
                                       {"pageSize": "50", "groupId": "vec-r1-0", "includeSystem": "false"})
        assert message["Items"] == []

    def test_the_prefix_is_the_launcher_s(self):
        assert er.REINDEX_EXECUTION_GROUP_PREFIX == "vec-"


@pytest.mark.unit
class TestTheDefaultIsPresentationOnly:
    def test_a_caller_who_may_not_get_the_workflow_still_sees_no_reindex_run(self):
        from tests.handlers.workflows.test_execution_list_system_runs import _deny_workflow

        rows = [_reindex_row(0), _user_row(1)]
        status, message = _run_global(_ScriptedIndex(rows), {"pageSize": "50", "triggerType": "System-Reindex"},
                                      enforcer=_deny_workflow("sgm"))
        assert status == 200
        assert message["Items"] == []

    def test_resolve_include_system_reads_the_two_narrowings(self):
        assert le._resolve_include_system(None, None, "", "", trigger_type="System-Reindex") is True
        assert le._resolve_include_system(None, None, "", "", group_id="vec-r1-3") is True
        assert le._resolve_include_system(None, None, "", "", trigger_type="Manual", group_id="grp") is False
        assert le._resolve_include_system(False, None, "", "", trigger_type="System-Reindex") is False
