# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The execute handler never starts a workflow on a definition built from different pipeline settings.

A workflow's state machine encodes its pipelines' execution settings (target, callback mode,
timeouts). The workflow record stores the published state machine version together with a fingerprint
of each pipeline's settings, and a launch compares those with the pipeline records it just read:

  - match: the run starts on the recorded VERSION ARN (immutable, so it is exactly that definition);
  - mismatch or no fingerprints (a workflow deployed before they were recorded): the workflow service
    redeploys it synchronously and the run starts on the new version;
  - the redeploy fails, or comes back built from different settings: the launch is refused with a
    retryable 409 and nothing is started.
"""

import io
import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common.workflows import workflowAsl
from backend.tests.handlers.workflows import test_executeWorkflow as tew
from backend.tests.workflowDeploymentStub import with_current_deployment

ewv2 = tew.ewv2
MOD = tew.MOD

BODY = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
STATE_MACHINE_ARN = tew._WORKFLOW["workflow_arn"]
OLD_VERSION_ARN = STATE_MACHINE_ARN + ":3"
NEW_VERSION_ARN = STATE_MACHINE_ARN + ":4"


def _changed_pipeline():
    """The test pipeline after its task timeout was raised: a definition-relevant change."""
    pipeline = dict(tew._PIPELINE)
    pipeline["executionConfig"] = {**tew._PIPELINE["executionConfig"], "waitForCallback": "Enabled",
                                   "taskTimeout": "7200"}
    return pipeline


def _redeploy_response(outcome, pipelines=None, version_arn=NEW_VERSION_ARN, reason=""):
    """The workflow service's internal-action response, as Lambda returns it."""
    message = {"outcome": outcome}
    if outcome in ("redeployed", "current"):
        message.update({
            "workflow_arn": STATE_MACHINE_ARN,
            "workflowVersionArn": version_arn,
            "jobNames": ["job-p1"],
            "pipelineFingerprints": workflowAsl.workflow_pipeline_fingerprints(pipelines or []),
        })
    if reason:
        message["reason"] = reason
    payload = {"statusCode": 200, "body": json.dumps({"message": message})}
    return {"Payload": io.BytesIO(json.dumps(payload).encode("utf-8"))}


def _launch(workflow, pipeline, redeploy=None, workflow_service="t-workflow-service", tables=None):
    """Run the execute handler with `workflow` as stored and `pipeline` as the live record. Returns
    (response, sfn mock, the redeploy invoke calls, every item put to any table).

    With `tables` = (workflow table, pipeline table), the record fetches run unpatched against those
    tables instead of returning `workflow` and `pipeline`, so a test sees the read arguments the
    handler sends.

    The handler's Lambda client also reaches the metadata service, so invokes are routed by function
    name: only the workflow service gets the scripted redeploy response."""
    p = tew.TestExecuteOrchestration()._patches(pipeline=pipeline)
    table = MagicMock()
    redeploy_calls = []

    def _invoke(**kwargs):
        if kwargs.get("FunctionName") == "t-workflow-service":
            redeploy_calls.append(kwargs)
            return redeploy
        return {"Payload": io.BytesIO(json.dumps(
            {"statusCode": 200, "body": json.dumps({"metadata": []})}).encode("utf-8"))}

    with ExitStack() as stack:
        if tables is None:
            stack.enter_context(patch(f"{MOD}._get_workflow", return_value=dict(workflow)))
            stack.enter_context(p["get_pipeline"])
        else:
            stack.enter_context(patch(f"{MOD}._workflow_table", return_value=tables[0]))
            stack.enter_context(patch(f"{MOD}._pipeline_table", return_value=tables[1]))
        for name in ("get_asset", "default_bucket", "asset_bucket", "exists", "enforcer", "claims"):
            stack.enter_context(p[name])
        stack.enter_context(patch(f"{MOD}._running_execution_exists", return_value=False))
        stack.enter_context(patch(f"{MOD}.workflow_service_function", workflow_service))
        m_lambda = stack.enter_context(patch(f"{MOD}.lambda_client"))
        stack.enter_context(patch(f"{MOD}.s3c"))
        m_sfn = stack.enter_context(patch(f"{MOD}.sfn_client"))
        m_dynamo = stack.enter_context(patch(f"{MOD}.dynamodb"))
        m_lambda.invoke.side_effect = _invoke
        m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
        m_dynamo.Table.return_value = table
        resp = ewv2.lambda_handler(tew._event(body=BODY), MagicMock())
    items = [c.kwargs.get("Item") for c in table.put_item.call_args_list]
    return resp, m_sfn, redeploy_calls, items


def _main_row(items):
    rows = [item for item in items if item and "workflow_execution_arn" in item]
    assert len(rows) == 1, rows
    return rows[0]


@pytest.mark.unit
class TestCurrentDeploymentStartsOnItsVersion:
    def test_matching_fingerprints_start_on_the_recorded_version_without_a_redeploy(self):
        workflow = with_current_deployment(tew._WORKFLOW, [tew._PIPELINE], version_arn=OLD_VERSION_ARN)
        resp, m_sfn, redeploys, items = _launch(workflow, tew._PIPELINE)
        assert resp["statusCode"] == 200, resp
        assert redeploys == []
        assert m_sfn.start_execution.call_args.kwargs["stateMachineArn"] == OLD_VERSION_ARN
        # The execution row names the definition that ran, next to the state machine it belongs to.
        row = _main_row(items)
        assert row["workflowVersionArn"] == OLD_VERSION_ARN
        assert row["workflow_arn"] == STATE_MACHINE_ARN


@pytest.mark.unit
class TestStaleDeploymentIsRedeployedFirst:
    def test_a_changed_pipeline_is_redeployed_before_the_run_starts(self):
        # Deployed from the pipeline BEFORE its timeout was raised.
        workflow = with_current_deployment(tew._WORKFLOW, [tew._PIPELINE], version_arn=OLD_VERSION_ARN)
        live = _changed_pipeline()
        resp, m_sfn, redeploys, items = _launch(
            workflow, live, redeploy=_redeploy_response("redeployed", [live]))
        assert resp["statusCode"] == 200, resp
        assert redeploys, "the launch did not ask for a redeploy"
        call = redeploys[-1]
        assert call["FunctionName"] == "t-workflow-service"
        # Synchronous: the run must not start before the redeploy has landed.
        assert call["InvocationType"] == "RequestResponse"
        event = json.loads(call["Payload"])
        assert event["vamsInternalAction"] == "redeployWorkflow"
        assert event["lambdaCrossCall"] == {"userName": "SYSTEM_USER"}
        assert event["payload"] == {"workflowDatabaseId": "db1", "workflowId": "wf1"}
        assert "requestContext" not in event
        # Started on the version the redeploy published, never on the stale one or the bare ARN.
        assert m_sfn.start_execution.call_args.kwargs["stateMachineArn"] == NEW_VERSION_ARN
        assert _main_row(items)["workflowVersionArn"] == NEW_VERSION_ARN

    def test_a_workflow_recorded_without_fingerprints_is_redeployed(self):
        # Every workflow deployed before this check existed: no version ARN, no fingerprints.
        legacy = dict(tew._WORKFLOW)
        resp, m_sfn, redeploys, _items = _launch(
            legacy, tew._PIPELINE, redeploy=_redeploy_response("redeployed", [tew._PIPELINE]))
        assert resp["statusCode"] == 200, resp
        assert redeploys, "the launch did not ask for a redeploy"
        assert m_sfn.start_execution.call_args.kwargs["stateMachineArn"] == NEW_VERSION_ARN

    def test_a_redeploy_another_writer_already_made_is_used(self):
        # The background refresh got there first: the service reports "current" with its version.
        workflow = with_current_deployment(tew._WORKFLOW, [tew._PIPELINE], version_arn=OLD_VERSION_ARN)
        live = _changed_pipeline()
        resp, m_sfn, _redeploys, _items = _launch(
            workflow, live, redeploy=_redeploy_response("current", [live]))
        assert resp["statusCode"] == 200, resp
        assert m_sfn.start_execution.call_args.kwargs["stateMachineArn"] == NEW_VERSION_ARN


class _ReplicaLagTable:
    """One row keyed on (databaseId, `sort_key`). An eventually consistent get_item returns
    `previous`, the row a replica that has not caught up still holds; a ConsistentRead returns
    `latest`. `reads` records the ConsistentRead flag of every get_item."""

    def __init__(self, sort_key, previous, latest):
        self.sort_key = sort_key
        self.previous = previous
        self.latest = latest
        self.reads = []

    def get_item(self, Key, ConsistentRead=False):
        self.reads.append(ConsistentRead)
        row = self.latest if ConsistentRead else self.previous
        if Key == {"databaseId": row["databaseId"], self.sort_key: row[self.sort_key]}:
            return {"Item": dict(row)}
        return {}


@pytest.mark.unit
class TestLaunchReadsTheCurrentRecords:
    """A launch right after a pipeline save or a background redeploy compares the records as they
    now stand. An eventually consistent read can return the rows from before that write, which match
    each other and would start the run on the version built from the previous settings."""

    def test_a_launch_after_a_pipeline_save_redeploys_instead_of_running_the_previous_settings(self):
        deployed = with_current_deployment(tew._WORKFLOW, [tew._PIPELINE], version_arn=OLD_VERSION_ARN)
        live = _changed_pipeline()
        workflows = _ReplicaLagTable("workflowId", deployed, deployed)
        pipelines = _ReplicaLagTable("pipelineId", tew._PIPELINE, live)
        resp, m_sfn, redeploys, _items = _launch(
            deployed, live, redeploy=_redeploy_response("redeployed", [live]),
            tables=(workflows, pipelines))
        assert resp["statusCode"] == 200, resp
        assert redeploys, "the launch compared a replica's previous pipeline row and did not redeploy"
        assert m_sfn.start_execution.call_args.kwargs["stateMachineArn"] == NEW_VERSION_ARN
        assert pipelines.reads and all(pipelines.reads), pipelines.reads

    def test_a_launch_after_a_background_redeploy_starts_on_the_new_version(self):
        live = _changed_pipeline()
        previous = with_current_deployment(tew._WORKFLOW, [tew._PIPELINE], version_arn=OLD_VERSION_ARN)
        redeployed = with_current_deployment(tew._WORKFLOW, [live], version_arn=NEW_VERSION_ARN)
        workflows = _ReplicaLagTable("workflowId", previous, redeployed)
        pipelines = _ReplicaLagTable("pipelineId", tew._PIPELINE, live)
        resp, m_sfn, redeploys, _items = _launch(redeployed, live, tables=(workflows, pipelines))
        assert resp["statusCode"] == 200, resp
        assert redeploys == []
        assert m_sfn.start_execution.call_args.kwargs["stateMachineArn"] == NEW_VERSION_ARN
        assert workflows.reads and all(workflows.reads), workflows.reads
        assert pipelines.reads and all(pipelines.reads), pipelines.reads


@pytest.mark.unit
class TestStaleDeploymentFailsClosed:
    def _stale(self):
        return with_current_deployment(tew._WORKFLOW, [tew._PIPELINE], version_arn=OLD_VERSION_ARN)

    def test_a_failed_redeploy_refuses_the_launch(self):
        resp, m_sfn, _redeploys, items = _launch(
            self._stale(), _changed_pipeline(),
            redeploy=_redeploy_response("failed", reason="The state machine could not be "
                                                         "redeployed (AccessDeniedException)."))
        assert resp["statusCode"] == 409, resp
        assert "Try again" in json.loads(resp["body"])["message"]
        m_sfn.start_execution.assert_not_called()
        assert not [item for item in items if item and "workflow_execution_arn" in item]

    def test_a_redeploy_built_from_other_settings_refuses_the_launch(self):
        # The pipeline changed again between this launch's read and the redeploy's read.
        live = _changed_pipeline()
        newer = dict(live)
        newer["executionConfig"] = {**live["executionConfig"], "taskTimeout": "9000"}
        resp, m_sfn, _redeploys, _items = _launch(
            self._stale(), live, redeploy=_redeploy_response("redeployed", [newer]))
        assert resp["statusCode"] == 409, resp
        m_sfn.start_execution.assert_not_called()

    def test_an_unreachable_workflow_service_refuses_the_launch(self):
        resp, m_sfn, redeploys, _items = _launch(
            self._stale(), _changed_pipeline(), workflow_service="")
        assert resp["statusCode"] == 409, resp
        assert redeploys == []
        m_sfn.start_execution.assert_not_called()

    def test_a_redeploy_invoke_error_refuses_the_launch(self):
        workflow = self._stale()
        with patch(f"{MOD}.workflow_service_function", "t-workflow-service"), \
                patch(f"{MOD}.lambda_client") as m_lambda:
            m_lambda.invoke.side_effect = Exception("throttled")
            err = ewv2._ensure_current_deployment(workflow, [_changed_pipeline()], {})
        assert m_lambda.invoke.called
        assert err is not None and err["statusCode"] == 409
        # The workflow passed in is left as it was, so nothing downstream reads a half-updated view.
        assert workflow["workflowVersionArn"] == OLD_VERSION_ARN
