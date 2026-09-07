# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The workflow service's two reads of the triggers table read every page.

`get_workflow_triggers` fills the single-workflow details response and `_trigger_summary` produces
the triggerCount / triggersEnabledCount the listing reports. A workflow holds several triggers of one
base type (sort keys ``fileUpload#<triggerId>``), each carrying its own inputFileFilters lists and
defaultTemplateIds map, so one workflow's row set can outgrow a single 1 MB query page.

A short read is worse here than a short listing: `_trigger_summary` also feeds
`_matches_trigger_filter`, so an enabled trigger on a later page drops the whole workflow out of a
``hasTriggers=true`` listing rather than merely understating a count.

The loops end on the PRESENCE of ``LastEvaluatedKey`` -- the only end-of-set signal DynamoDB gives,
and the only form that stays finite against an under-stubbed reader.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.workflows import workflowService as wfs
from backend.backend.handlers.workflows.workflowService import lambda_handler
from backend.tests.pagingStub import BareMockReader, Pager

MOD = "backend.backend.handlers.workflows.workflowService"

WF_ITEM = {"databaseId": "db1", "workflowId": "wflow1", "workflowName": "W",
           "enabled": True, "archived": False}


def _row(trigger_key, enabled=True):
    """A stored trigger row, keyed as the base type or as ``type#triggerId``."""
    return {
        "workflowDatabaseId:workflowId": "db1:wflow1",
        "workflowDatabaseId": "db1",
        "workflowId": "wflow1",
        "triggerType": trigger_key,
        "triggerConfig": {"inputFileFilters": {"allow": ["*.glb"], "exclude": []},
                          "defaultTemplateIds": {}},
        "enabled": enabled,
    }


def _cursor(trigger_key):
    """The continuation key a triggers-table page carries: both halves of its primary key."""
    return {"workflowDatabaseId:workflowId": "db1:wflow1", "triggerType": trigger_key}


def _table(side_effect):
    table = MagicMock()
    table.query.side_effect = side_effect
    return table


def _event(path_params):
    return {
        "requestContext": {"http": {"method": "GET",
                                    "path": "/database/db1/workflows/wflow1"}},
        "pathParameters": path_params,
        "queryStringParameters": None,
        "headers": {"authorization": "Bearer test-token"},
        "body": None,
    }


def _enforcer():
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = True
    return inst


@pytest.mark.unit
class TestGetWorkflowTriggersPaging:
    def test_a_trigger_on_a_later_page_is_returned(self):
        pager = Pager(
            {"Items": [_row("fileUpload")], "LastEvaluatedKey": _cursor("fileUpload")},
            {"Items": [_row("fileUpload#second")]},
            name="get_workflow_triggers",
        )

        with patch(f"{MOD}._triggers_table", return_value=_table(pager)):
            triggers = wfs.get_workflow_triggers("db1", "wflow1")

        assert [t["triggerType"] for t in triggers] == ["fileUpload", "fileUpload#second"]
        # Over the SET of cursors, so an extra or repeated read is not a failure.
        pager.assert_paged_to_exhaustion()

    def test_a_single_page_read_is_unchanged(self):
        """Positive control: the ordinary one-page workflow reads once and needs no continuation."""
        pager = Pager({"Items": [_row("fileUpload", enabled=False)]}, name="get_workflow_triggers")

        with patch(f"{MOD}._triggers_table", return_value=_table(pager)):
            triggers = wfs.get_workflow_triggers("db1", "wflow1")

        assert triggers == [{"triggerType": "fileUpload",
                             "triggerConfig": _row("fileUpload")["triggerConfig"],
                             "enabled": False}]
        assert pager.resumed_from == [], f"a single page was continued from: {pager.resumed_from}"

    def test_the_continuation_keeps_the_partition_key_condition(self):
        """A continuation that dropped the key condition would read another workflow's triggers."""
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": _cursor("fileUpload")},
            {"Items": []},
            name="get_workflow_triggers",
        )

        with patch(f"{MOD}._triggers_table", return_value=_table(pager)):
            wfs.get_workflow_triggers("db1", "wflow1")

        # `all()` holds vacuously over no calls, so assert the continuation happened first.
        assert len(pager.calls) >= 2, f"the pager was not driven across a continuation: {pager.calls}"
        assert all("KeyConditionExpression" in call for call in pager.calls), pager.calls
        assert all("IndexName" not in call for call in pager.calls), (
            f"the details read must use the base table, not the by-type GSI: {pager.calls}")

    def test_terminates_against_an_under_stubbed_reader(self):
        """The loop ends on key PRESENCE, so a bare-mock page ends it after one read."""
        reader = BareMockReader(name="get_workflow_triggers")

        with patch(f"{MOD}._triggers_table", return_value=_table(reader)):
            assert wfs.get_workflow_triggers("db1", "wflow1") == []

        assert reader.calls, 'the listing never read the table'
        assert len(reader.calls) <= 1, reader.calls


@pytest.mark.unit
class TestTriggerSummaryPaging:
    def test_counts_cover_every_page(self):
        pager = Pager(
            {"Items": [_row("fileUpload"), _row("fileUpload#off", enabled=False)],
             "LastEvaluatedKey": _cursor("fileUpload#off")},
            {"Items": [_row("fileUpload#third")]},
            name="_trigger_summary",
        )

        with patch(f"{MOD}._triggers_table", return_value=_table(pager)):
            summary = wfs._trigger_summary("db1", "wflow1")

        assert summary == {"triggerCount": 3, "triggersEnabledCount": 2}
        pager.assert_paged_to_exhaustion()

    def test_an_enabled_trigger_on_a_later_page_keeps_the_workflow_in_a_hasTriggers_listing(self):
        """The count drives the hasTriggers filter, so a short read DROPS the workflow, not a number."""
        pager = Pager(
            {"Items": [_row("fileUpload", enabled=False)],
             "LastEvaluatedKey": _cursor("fileUpload")},
            {"Items": [_row("fileUpload#second", enabled=True)]},
            name="_trigger_summary",
        )

        with patch(f"{MOD}._triggers_table", return_value=_table(pager)):
            summary = wfs._trigger_summary("db1", "wflow1")

        assert wfs._matches_trigger_filter(summary, "true") is True
        assert wfs._matches_trigger_filter(summary, "false") is False

    def test_a_single_page_summary_is_unchanged(self):
        """Positive control: one trigger, one read, no continuation."""
        pager = Pager({"Items": [_row("fileUpload")]}, name="_trigger_summary")

        with patch(f"{MOD}._triggers_table", return_value=_table(pager)):
            summary = wfs._trigger_summary("db1", "wflow1")

        assert summary == {"triggerCount": 1, "triggersEnabledCount": 1}
        assert pager.resumed_from == [], f"a single page was continued from: {pager.resumed_from}"

    def test_an_empty_partition_still_counts_zero(self):
        """Positive control: a workflow with no triggers reads once and counts nothing."""
        pager = Pager({"Items": []}, name="_trigger_summary")

        with patch(f"{MOD}._triggers_table", return_value=_table(pager)):
            summary = wfs._trigger_summary("db1", "wflow1")

        assert summary == {"triggerCount": 0, "triggersEnabledCount": 0}
        assert pager.calls, 'the summary never read the table'
        assert len(pager.calls) <= 1, pager.calls

    def test_a_read_failure_is_still_best_effort(self):
        """Positive control: the paged loop did not turn a read failure into a raised exception."""
        table = MagicMock()
        table.query.side_effect = RuntimeError("boom")

        with patch(f"{MOD}._triggers_table", return_value=table):
            assert wfs._trigger_summary("db1", "wflow1") is None

    def test_terminates_against_an_under_stubbed_reader(self):
        """This helper catches Exception and degrades to None, which is why the stub's failure derives
        from BaseException -- otherwise a non-terminating loop would read as an ordinary None."""
        reader = BareMockReader(name="_trigger_summary")

        with patch(f"{MOD}._triggers_table", return_value=_table(reader)):
            summary = wfs._trigger_summary("db1", "wflow1")

        assert summary == {"triggerCount": 0, "triggersEnabledCount": 0}
        assert reader.calls, 'the summary never read the table'
        assert len(reader.calls) <= 1, reader.calls


@pytest.mark.unit
class TestWorkflowDetailsEndpointPaging:
    """The same completeness through GET /database/{databaseId}/workflows/{workflowId}."""

    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_the_details_response_carries_triggers_from_every_page(self, mock_enforcer, mock_claims,
                                                                   mock_workflow_table):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        workflow_table = MagicMock()
        workflow_table.get_item.return_value = {"Item": dict(WF_ITEM)}
        mock_workflow_table.return_value = workflow_table
        pager = Pager(
            {"Items": [_row("fileUpload")], "LastEvaluatedKey": _cursor("fileUpload")},
            {"Items": [_row("fileUpload#second")]},
            name="workflow details triggers",
        )

        with patch(f"{MOD}._triggers_table", return_value=_table(pager)):
            resp = lambda_handler(
                _event({"databaseId": "db1", "workflowId": "wflow1"}), MagicMock())

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])["message"]
        # The response carries no truncation signal, so the count is the only thing that can be
        # asserted -- a short list is indistinguishable from a workflow with one trigger.
        assert [t["triggerType"] for t in data["triggers"]] == ["fileUpload", "fileUpload#second"]
        pager.assert_paged_to_exhaustion()
