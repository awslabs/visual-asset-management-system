# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A pipeline save schedules the redeploy of every workflow built from it, and says so.

  - A change to a setting the workflow state machines encode (execution type, target, callback mode,
    timeouts, Deadline Cloud job settings) invokes the workflow service ASYNCHRONOUSLY, naming only
    the pipeline; any other edit invokes nothing.
  - The save warning reports how many referencing workflows are being redeployed, instead of asking
    for each to be saved again.
  - A systemConfig change that makes a referencing workflow fail its save checks returns a warning
    naming the workflow. It never blocks the save.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.pipelines import pipelineService as ps
from backend.backend.handlers.pipelines.pipelineService import lambda_handler

MOD = "backend.backend.handlers.pipelines.pipelineService"

STORED = {
    "databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "Converter", "enabled": True,
    "executionConfig": {"executionType": "Lambda", "waitForCallback": "Enabled",
                        "taskTimeout": "3600", "lambda": {"resourceId": "vams-fn"}},
    "systemConfig": {"inputFileArity": "one", "metadataInputs": {}},
}


def _event(body):
    return {
        "requestContext": {"http": {"method": "PUT", "path": "/database/db1/pipelines/pipe1"}},
        "pathParameters": {"databaseId": "db1", "pipelineId": "pipe1"},
        "queryStringParameters": None,
        "headers": {"authorization": "Bearer t"},
        "body": json.dumps(body),
    }


def _allow():
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = True
    return inst


def _referencing(count, system_config=None):
    return [{"databaseId": "db1", "workflowId": f"wf{i}", "systemConfig": system_config or {},
             "specifiedPipelines": [{"pipelineDatabaseId:pipelineId": "db1:pipe1"}]}
            for i in range(count)]


def _save(body, workflows=None, invoke_error=None, workflow_service="t-workflow-service"):
    """PUT the pipeline. Returns (response, lambda mock, saved pipeline row)."""
    pipeline_table = MagicMock()
    pipeline_table.get_item.return_value = {"Item": json.loads(json.dumps(STORED))}
    workflow_table = MagicMock()
    workflow_table.query.return_value = {"Items": workflows or []}
    with patch(f"{MOD}._pipeline_table", return_value=pipeline_table), \
            patch(f"{MOD}._workflow_table", return_value=workflow_table), \
            patch(f"{MOD}.request_to_claims", return_value={"tokens": ["user1"]}), \
            patch(f"{MOD}.CasbinEnforcer", return_value=_allow()), \
            patch(f"{MOD}.workflow_service_function", workflow_service), \
            patch(f"{MOD}.lambda_client") as m_lambda:
        if invoke_error:
            m_lambda.invoke.side_effect = invoke_error
        resp = lambda_handler(_event(body), MagicMock())
    saved = pipeline_table.put_item.call_args.kwargs["Item"] if pipeline_table.put_item.called else None
    return resp, m_lambda, saved


def _warnings(resp):
    return json.loads(resp["body"]).get("warnings") or []


def _execution_config(**changes):
    config = json.loads(json.dumps(STORED["executionConfig"]))
    config.update(changes)
    return config


@pytest.mark.unit
class TestRefreshIsScheduledOnlyForDefinitionChanges:
    @pytest.mark.parametrize("changes", [
        {"taskTimeout": "7200"},
        {"waitForCallback": "Disabled"},
        {"lambda": {"resourceId": "vams-other-fn"}},
    ])
    def test_a_definition_change_invokes_the_workflow_service_asynchronously(self, changes):
        resp, m_lambda, _saved = _save({"executionConfig": _execution_config(**changes)},
                                       workflows=_referencing(2))
        assert resp["statusCode"] == 200
        assert m_lambda.invoke.called
        call = m_lambda.invoke.call_args.kwargs
        assert call["FunctionName"] == "t-workflow-service"
        assert call["InvocationType"] == "Event"
        event = json.loads(call["Payload"])
        assert event["vamsInternalAction"] == "refreshPipelineReferences"
        assert event["lambdaCrossCall"] == {"userName": "SYSTEM_USER"}
        # Only the pipeline is named: the workflow service derives the targets itself.
        assert event["payload"] == {"pipelineDatabaseId": "db1", "pipelineId": "pipe1"}
        assert "requestContext" not in event

    def test_an_execution_config_edit_the_definition_does_not_encode_invokes_nothing(self):
        # isProvided is bookkeeping on the lambda block; the state machine never reads it.
        config = _execution_config()
        config["lambda"] = {"resourceId": "vams-fn", "isProvided": False}
        resp, m_lambda, saved = _save({"executionConfig": config}, workflows=_referencing(2))
        assert resp["statusCode"] == 200
        assert saved["executionConfig"]["lambda"]["isProvided"] is False
        m_lambda.invoke.assert_not_called()
        assert not any("redeployed" in w for w in _warnings(resp))

    @pytest.mark.parametrize("body", [
        {"description": "edited"},
        {"pipelineName": "Renamed"},
        {"enabled": False},
        {"systemConfig": {"inputFileArity": "multi", "metadataInputs": {}}},
    ])
    def test_edits_read_at_launch_invoke_nothing(self, body):
        resp, m_lambda, _saved = _save(body, workflows=_referencing(2))
        assert resp["statusCode"] == 200
        m_lambda.invoke.assert_not_called()

    def test_the_invoke_follows_the_write(self):
        # The workflow service must read the SAVED record, so the pipeline row is written first.
        order = []
        pipeline_table = MagicMock()
        pipeline_table.get_item.return_value = {"Item": json.loads(json.dumps(STORED))}
        pipeline_table.put_item.side_effect = lambda **kw: order.append("put")
        with patch(f"{MOD}._pipeline_table", return_value=pipeline_table), \
                patch(f"{MOD}._workflow_table",
                      return_value=MagicMock(query=MagicMock(return_value={"Items": []}))), \
                patch(f"{MOD}.request_to_claims", return_value={"tokens": ["user1"]}), \
                patch(f"{MOD}.CasbinEnforcer", return_value=_allow()), \
                patch(f"{MOD}.workflow_service_function", "t-workflow-service"), \
                patch(f"{MOD}.lambda_client") as m_lambda:
            m_lambda.invoke.side_effect = lambda **kw: order.append("invoke")
            lambda_handler(_event({"executionConfig": _execution_config(taskTimeout="7200")}),
                           MagicMock())
        assert order == ["put", "invoke"]


@pytest.mark.unit
class TestRefreshWarning:
    def test_the_warning_counts_the_workflows_being_redeployed(self):
        # More than the 25 workflows a warning used to name: the count is not cut off there.
        resp, _m, _saved = _save({"executionConfig": _execution_config(taskTimeout="7200")},
                                 workflows=_referencing(ps.MAX_REFERENCING_WORKFLOWS + 5))
        warnings = _warnings(resp)
        expected = f"{ps.MAX_REFERENCING_WORKFLOWS + 5} referencing workflow(s) are being redeployed"
        assert any(expected in w for w in warnings), warnings
        assert not any("save each workflow again" in w for w in warnings), warnings

    def test_no_referencing_workflow_means_no_warning(self):
        resp, m_lambda, _saved = _save({"executionConfig": _execution_config(taskTimeout="7200")})
        assert _warnings(resp) == []
        # The refresh is still scheduled: the service's own walk is the authority, not this count.
        assert m_lambda.invoke.called

    def test_an_unscheduled_refresh_says_when_the_workflows_catch_up(self):
        resp, _m, _saved = _save({"executionConfig": _execution_config(taskTimeout="7200")},
                                 workflows=_referencing(1), invoke_error=Exception("throttled"))
        assert resp["statusCode"] == 200
        assert any("next save or launch" in w for w in _warnings(resp)), _warnings(resp)

    def test_an_unset_function_name_still_saves(self):
        resp, m_lambda, saved = _save({"executionConfig": _execution_config(taskTimeout="7200")},
                                      workflows=_referencing(1), workflow_service="")
        assert resp["statusCode"] == 200 and saved is not None
        m_lambda.invoke.assert_not_called()
        assert any("next save or launch" in w for w in _warnings(resp)), _warnings(resp)


@pytest.mark.unit
class TestValidationDriftWarning:
    def test_a_system_config_change_that_breaks_a_workflow_check_warns(self):
        # The workflow allows multiple files; the pipeline now accepts one, which the workflow save
        # checks report as an arity mismatch.
        workflows = _referencing(1, system_config={"inputFileArity": "multi"})
        stored_multi = {"inputFileArity": "multi", "metadataInputs": {}}
        with patch.dict(STORED, {"systemConfig": stored_multi}):
            resp, m_lambda, _saved = _save(
                {"systemConfig": {"inputFileArity": "one", "metadataInputs": {}}}, workflows=workflows)
        assert resp["statusCode"] == 200
        warnings = _warnings(resp)
        assert any(w.startswith("workflow db1:wf0:") and "single input file" in w
                   for w in warnings), warnings
        m_lambda.invoke.assert_not_called()

    def test_a_finding_the_previous_config_already_produced_is_not_repeated(self):
        workflows = _referencing(1, system_config={"inputFileArity": "multi"})
        # Stored pipeline is already single-file; the edit changes something unrelated.
        resp, _m, _saved = _save(
            {"systemConfig": {"inputFileArity": "one", "metadataInputs": {},
                              "auxPreviewPipelineSuffix": "viewer"}}, workflows=workflows)
        assert not any(w.startswith("workflow ") for w in _warnings(resp)), _warnings(resp)

    def test_the_drift_list_is_bounded(self):
        workflows = _referencing(ps.MAX_REFERENCING_WORKFLOWS + 10,
                                 system_config={"inputFileArity": "multi"})
        with patch.dict(STORED, {"systemConfig": {"inputFileArity": "multi", "metadataInputs": {}}}):
            resp, _m, _saved = _save(
                {"systemConfig": {"inputFileArity": "one", "metadataInputs": {}}}, workflows=workflows)
        drift = [w for w in _warnings(resp) if w.startswith("workflow ")]
        assert len(drift) == ps.MAX_REFERENCING_WORKFLOWS
        assert any("further workflow save-check finding(s)" in w for w in _warnings(resp))
