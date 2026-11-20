# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Registration-time executionConfig overrides of the vamsSchema import.

A deployment can register an existing bundle with a different execution config (callback mode,
timeouts, Lambda name) through the optional ``executionConfigOverrides`` property of the registration
custom resource. These tests cover:

  - the request builder applies the override on top of the bundle and the injected resource values;
  - an absent (or empty) override sends exactly the request the registration always sent;
  - the custom-resource handler reads the property and rejects a malformed one before any cross-call;
  - a callback-mode change made through the import's own update path schedules the redeploy of every
    workflow that references the pipeline, in both directions, and the refresh redeploys a bundle
    workflow and a custom workflow alike, so neither keeps the old ASL;
  - an in-flight execution is untouched: the refresh only publishes a new state-machine version.
"""

import copy
import io
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("PIPELINE_SERVICE_V2_FUNCTION_NAME", "t-pipe-v2")
os.environ.setdefault("PIPELINE_TEMPLATE_SERVICE_FUNCTION_NAME", "t-tpl")
os.environ.setdefault("WORKFLOW_SERVICE_V2_FUNCTION_NAME", "t-wf-v2")
os.environ.setdefault("WORKFLOW_TRIGGER_SERVICE_FUNCTION_NAME", "t-trig")
os.environ.setdefault("LAMBDA_PIPELINE_SAMPLE_FUNCTION_BUCKET", "t-artefacts")

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.common.workflows import systemRecords as sr  # noqa: E402
from backend.backend.common.workflows import vamsSchemaImport as vsi  # noqa: E402
from backend.backend.common.workflows import workflowAsl as wa  # noqa: E402
from backend.backend.handlers.pipelines import pipelineService as ps  # noqa: E402
from backend.backend.handlers.workflows import importGlobalPipelineWorkflow as imp  # noqa: E402
from backend.backend.handlers.workflows import workflowService as ws  # noqa: E402
from backend.tests.handlers.workflows import test_workflow_pipeline_refresh as refresh  # noqa: E402
from backend.tests.pagingStub import Pager  # noqa: E402

PS = "backend.backend.handlers.pipelines.pipelineService"
WS = "backend.backend.handlers.workflows.workflowService"


def _bundle(**execution):
    config = {"executionType": "Lambda", "waitForCallback": "Disabled", "taskTimeout": "900",
              "taskHeartbeatTimeout": "", "lambda": {}}
    config.update(execution)
    return {
        "pipeline": {"pipelineId": "conv", "pipelineName": "Converter", "category": "Conversion",
                     "executionConfig": config, "systemConfig": {"requireTemplate": False}},
        "workflow": {"workflowId": "conv", "workflowName": "Convert WF", "category": "Conversion"},
    }


def _pipeline_request(bundle, **kwargs):
    requests = vsi.build_import_requests(bundle, {"lambdaName": "vams-conv-fn"}, **kwargs)
    return requests[0]


def _create_config(request):
    return request["createBody"]["executionConfig"]


def _update_config(request):
    return request["updateBody"]["executionConfig"]


# ============================ request builder ============================

@pytest.mark.unit
class TestBuilderAppliesTheOverride:
    def test_wait_for_callback_and_timeouts_replace_the_bundle_values(self):
        request = _pipeline_request(_bundle(), execution_config_overrides={
            "waitForCallback": "Enabled", "taskTimeout": "14400", "taskHeartbeatTimeout": "600"})
        for config in (_create_config(request), _update_config(request)):
            assert config["waitForCallback"] == "Enabled"
            assert config["taskTimeout"] == "14400"
            assert config["taskHeartbeatTimeout"] == "600"
            assert config["executionType"] == "Lambda"

    def test_numbers_are_stored_as_strings(self):
        request = _pipeline_request(_bundle(), execution_config_overrides={"taskTimeout": 7200})
        assert _update_config(request)["taskTimeout"] == "7200"

    def test_an_empty_heartbeat_clears_the_bundle_value(self):
        request = _pipeline_request(
            _bundle(taskHeartbeatTimeout="300"),
            execution_config_overrides={"taskHeartbeatTimeout": ""})
        assert _update_config(request)["taskHeartbeatTimeout"] == ""

    def test_the_override_lambda_name_wins_over_the_injected_resource(self):
        request = _pipeline_request(
            _bundle(), execution_config_overrides={"lambdaName": "vams-other-fn"})
        assert _update_config(request)["lambda"]["resourceId"] == "vams-other-fn"
        assert _create_config(request)["lambda"]["resourceId"] == "vams-other-fn"

    def test_a_field_not_overridden_keeps_the_bundle_value(self):
        request = _pipeline_request(
            _bundle(taskTimeout="900"), execution_config_overrides={"waitForCallback": "Enabled"})
        config = _update_config(request)
        assert config["taskTimeout"] == "900"
        assert config["lambda"]["resourceId"] == "vams-conv-fn"

    def test_a_none_value_is_treated_as_absent(self):
        baseline = _pipeline_request(_bundle())
        request = _pipeline_request(
            _bundle(), execution_config_overrides={"waitForCallback": None, "taskTimeout": None})
        assert request == baseline

    def test_the_override_does_not_change_anything_but_the_pipeline_execution_config(self):
        baseline = vsi.build_import_requests(_bundle(), {"lambdaName": "vams-conv-fn"})
        overridden = vsi.build_import_requests(
            _bundle(), {"lambdaName": "vams-conv-fn"},
            execution_config_overrides={"waitForCallback": "Enabled"})
        assert baseline[1:] == overridden[1:]
        expected = copy.deepcopy(baseline[0])
        expected["createBody"]["executionConfig"]["waitForCallback"] = "Enabled"
        expected["updateBody"]["executionConfig"]["waitForCallback"] = "Enabled"
        assert overridden[0] == expected

    def test_the_inputs_are_not_mutated(self):
        bundle = _bundle()
        before = copy.deepcopy(bundle)
        overrides = {"waitForCallback": "Enabled"}
        vsi.build_import_requests(bundle, execution_config_overrides=overrides)
        assert bundle == before
        assert overrides == {"waitForCallback": "Enabled"}


@pytest.mark.unit
class TestAbsentOverrideIsTheRequestItAlwaysWas:
    @pytest.mark.parametrize("absent", [None, {}])
    def test_the_whole_request_list_is_identical(self, absent):
        baseline = vsi.build_import_requests(_bundle(), {"lambdaName": "vams-conv-fn"})
        again = vsi.build_import_requests(
            _bundle(), {"lambdaName": "vams-conv-fn"}, execution_config_overrides=absent)
        assert again == baseline

    def test_the_default_argument_is_absent(self):
        baseline = vsi.build_import_requests(_bundle(), {"lambdaName": "vams-conv-fn"})
        assert vsi.build_import_requests(
            _bundle(), {"lambdaName": "vams-conv-fn"}, execution_config_overrides=None) == baseline


@pytest.mark.unit
class TestBuilderRejectsAMalformedOverride:
    @pytest.mark.parametrize("overrides, message", [
        ({"waitForCallBack": "Enabled"}, "unknown key"),
        ({"resourceId": "x"}, "unknown key"),
        ({"waitForCallback": "maybe"}, "waitForCallback must be one of"),
        ({"waitForCallback": True}, "must be a string or a number"),
        ({"taskTimeout": ["1"]}, "must be a string or a number"),
        ({"taskTimeout": {"a": 1}}, "must be a string or a number"),
        ({"lambdaName": ""}, "must not be empty"),
        (["waitForCallback"], "must be a JSON object"),
    ])
    def test_the_registration_fails_before_any_cross_call(self, overrides, message):
        with pytest.raises(vsi.VamsSchemaError, match=message):
            vsi.build_import_requests(_bundle(), execution_config_overrides=overrides)

    def test_a_lambda_name_on_another_execution_type_is_refused(self):
        bundle = _bundle(executionType="SQS")
        with pytest.raises(vsi.VamsSchemaError, match="Lambda execution type only"):
            vsi.build_import_requests(bundle, execution_config_overrides={"lambdaName": "fn"})


# ============================ custom-resource handler ============================

def _lambda_client():
    client = MagicMock()

    def _invoke(FunctionName, InvocationType, Payload):
        return {"Payload": io.BytesIO(json.dumps({"statusCode": 200, "body": "{}"}).encode("utf-8"))}

    client.invoke.side_effect = _invoke
    return client


def _register(properties):
    """Run register_bundle against a service that answers 404 to the exists probes (so everything is
    created). Returns the pipeline POST body the importer sent."""
    client = _lambda_client()

    def _status(function_name, event):
        return 404 if event.get("requestContext", {}).get("http", {}).get("method") == "GET" else 200

    def _invoke(FunctionName, InvocationType, Payload):
        event = json.loads(Payload.decode("utf-8"))
        status = _status(FunctionName, event)
        return {"Payload": io.BytesIO(json.dumps({"statusCode": status, "body": "{}"}).encode())}

    client.invoke.side_effect = _invoke
    with patch.object(imp, "lambda_client", client):
        imp.register_bundle(properties)
    bodies = []
    for call in client.invoke.call_args_list:
        event = json.loads(call.kwargs["Payload"].decode("utf-8"))
        if event["requestContext"]["http"]["method"] == "POST" and \
                event["requestContext"]["http"]["path"].endswith("/pipelines"):
            bodies.append(json.loads(event["body"]))
    assert len(bodies) == 1
    return bodies[0], client


@pytest.mark.unit
class TestCustomResourceHandler:
    def _properties(self, **extra):
        props = {"inlineBundle": _bundle(), "resourceOverrides": json.dumps(
            {"lambdaName": "vams-conv-fn"})}
        props.update(extra)
        return props

    def test_a_json_string_property_is_applied(self):
        body, _client = _register(self._properties(
            executionConfigOverrides=json.dumps({"waitForCallback": "Enabled"})))
        assert body["executionConfig"]["waitForCallback"] == "Enabled"
        assert body["executionConfig"]["lambda"]["resourceId"] == "vams-conv-fn"

    def test_an_object_property_is_applied(self):
        body, _client = _register(self._properties(
            executionConfigOverrides={"taskTimeout": "14400"}))
        assert body["executionConfig"]["taskTimeout"] == "14400"

    @pytest.mark.parametrize("absent", [None, "", "{}", {}])
    def test_an_absent_property_sends_exactly_todays_requests(self, absent):
        _body, baseline = _register(self._properties())
        extra = {} if absent is None else {"executionConfigOverrides": absent}
        _body2, again = _register(self._properties(**extra))
        baseline_calls = [c.kwargs for c in baseline.invoke.call_args_list]
        again_calls = [c.kwargs for c in again.invoke.call_args_list]
        assert len(baseline_calls) >= 4  # probe + write for the pipeline, the workflow, and more
        assert again_calls == baseline_calls

    @pytest.mark.parametrize("bad", ["{not json", "[1, 2]", "\"text\"", 5])
    def test_an_unparsable_property_fails_the_registration_before_any_cross_call(self, bad):
        client = _lambda_client()
        with patch.object(imp, "lambda_client", client):
            with pytest.raises(imp.ImportError_):
                imp.register_bundle(self._properties(executionConfigOverrides=bad))
        client.invoke.assert_not_called()

    def test_an_unknown_override_key_fails_the_registration_before_any_cross_call(self):
        client = _lambda_client()
        with patch.object(imp, "lambda_client", client):
            # The handler imports the builder as `common.workflows...`, a module object distinct
            # from the `backend.backend...` one this file imports.
            with pytest.raises(imp.vsi.VamsSchemaError):
                imp.register_bundle(self._properties(
                    executionConfigOverrides=json.dumps({"waitForCallbak": "Enabled"})))
        client.invoke.assert_not_called()


# ============================ redeploy of referencing workflows ============================

ARN = refresh.ARN


def _stored_pipeline(wait_for_callback):
    return {
        "databaseId": "GLOBAL", "pipelineId": "conv", "pipelineName": "Converter", "enabled": True,
        "isSystem": True, "executionConfig": {
            "executionType": "Lambda", "waitForCallback": wait_for_callback, "taskTimeout": "900",
            "taskHeartbeatTimeout": "", "lambda": {"resourceId": "vams-conv-fn"}},
        "systemConfig": {"inputFileArity": "one", "metadataInputs": {}},
    }


def _import_put_event(update_body):
    return {
        "requestContext": {"http": {"method": "PUT", "path": "/database/GLOBAL/pipelines/conv"}},
        "pathParameters": {"databaseId": "GLOBAL", "pipelineId": "conv"},
        "queryStringParameters": None,
        "lambdaCrossCall": {"userName": "SYSTEM_USER", "source": sr.IMPORT_SOURCE_MARKER},
        "body": json.dumps(update_body),
    }


def _allow():
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = True
    return inst


def _import_update(stored_wait, bundle_wait, override_wait):
    """Push the import's own update body for a registration (bundle declares `bundle_wait`, the
    deployment overrides to `override_wait`; None = no override) through the pipeline service against a
    stored row. Returns (saved row, lambda mock)."""
    bundle = _bundle(waitForCallback=bundle_wait)
    overrides = {"waitForCallback": override_wait} if override_wait else None
    update_body = _pipeline_request(
        bundle, execution_config_overrides=overrides)["updateBody"]
    pipeline_table = MagicMock()
    pipeline_table.get_item.return_value = {"Item": _stored_pipeline(stored_wait)}
    workflow_table = MagicMock()
    workflow_table.query.return_value = {"Items": []}
    with patch(f"{PS}._pipeline_table", return_value=pipeline_table), \
            patch(f"{PS}._workflow_table", return_value=workflow_table), \
            patch(f"{PS}.request_to_claims", return_value={"tokens": ["SYSTEM_USER"]}), \
            patch(f"{PS}.CasbinEnforcer", return_value=_allow()), \
            patch(f"{PS}.workflow_service_function", "t-workflow-service"), \
            patch(f"{PS}.lambda_client") as m_lambda:
        response = ps.lambda_handler(_import_put_event(update_body), MagicMock())
    assert response["statusCode"] == 200, response
    saved = pipeline_table.put_item.call_args.kwargs["Item"]
    return saved, m_lambda


@pytest.mark.unit
class TestCallbackChangeThroughTheImportPathRefreshesReferencingWorkflows:
    @pytest.mark.parametrize("stored, bundle, override, expected", [
        ("Disabled", "Disabled", "Enabled", "Enabled"),   # off -> on, by override
        ("Enabled", "Disabled", "Disabled", "Disabled"),  # on -> off, by override
        ("Enabled", "Disabled", None, "Disabled"),        # override removed: bundle value returns
    ])
    def test_a_changed_callback_mode_schedules_the_refresh(self, stored, bundle, override, expected):
        saved, m_lambda = _import_update(stored, bundle, override)
        assert saved["executionConfig"]["waitForCallback"] == expected
        assert m_lambda.invoke.called
        call = m_lambda.invoke.call_args.kwargs
        assert call["FunctionName"] == "t-workflow-service"
        assert call["InvocationType"] == "Event"
        event = json.loads(call["Payload"])
        assert event["vamsInternalAction"] == "refreshPipelineReferences"
        assert event["payload"] == {"pipelineDatabaseId": "GLOBAL", "pipelineId": "conv"}

    @pytest.mark.parametrize("stored, bundle, override", [
        ("Enabled", "Disabled", "Enabled"),   # same value again
        ("Disabled", "Disabled", None),       # no override, no change
    ])
    def test_an_unchanged_callback_mode_schedules_nothing(self, stored, bundle, override):
        _saved, m_lambda = _import_update(stored, bundle, override)
        m_lambda.invoke.assert_not_called()

    def test_a_changed_timeout_schedules_the_refresh_too(self):
        bundle = _bundle(waitForCallback="Enabled")
        update_body = _pipeline_request(
            bundle, execution_config_overrides={"taskTimeout": "14400"})["updateBody"]
        pipeline_table = MagicMock()
        pipeline_table.get_item.return_value = {"Item": _stored_pipeline("Enabled")}
        workflow_table = MagicMock()
        workflow_table.query.return_value = {"Items": []}
        with patch(f"{PS}._pipeline_table", return_value=pipeline_table), \
                patch(f"{PS}._workflow_table", return_value=workflow_table), \
                patch(f"{PS}.request_to_claims", return_value={"tokens": ["SYSTEM_USER"]}), \
                patch(f"{PS}.CasbinEnforcer", return_value=_allow()), \
                patch(f"{PS}.workflow_service_function", "t-workflow-service"), \
                patch(f"{PS}.lambda_client") as m_lambda:
            ps.lambda_handler(_import_put_event(update_body), MagicMock())
        assert m_lambda.invoke.called


def _ref():
    return {"pipelineDatabaseId": "GLOBAL", "pipelineId": "conv",
            "pipelineDatabaseId:pipelineId": "GLOBAL:conv", "jobName": "", "defaultTemplateId": ""}


def _workflow(workflow_id, deployed_from, **extra):
    row = refresh._stored_workflow(
        workflow_id, database_id="db-custom" if workflow_id.startswith("custom") else "GLOBAL",
        refs=[_ref()], deployed_from=deployed_from, **extra)
    return row


@pytest.mark.unit
class TestTheRefreshRedeploysBundleAndCustomWorkflowsInBothDirections:
    @pytest.fixture(autouse=True)
    def bind_real_to_update_expr(self):
        # conftest replaces common.dynamodb with a mock; bind the real update-expression builder.
        with patch(f"{WS}.to_update_expr", refresh._real_to_update_expr):
            yield

    @pytest.mark.parametrize("old, new", [("Disabled", "Enabled"), ("Enabled", "Disabled")])
    def test_every_referencing_workflow_is_rebuilt_from_the_new_callback_mode(self, old, new):
        old_pipeline = _stored_pipeline(old)
        new_pipeline = _stored_pipeline(new)
        bundle_wf = _workflow("conv", [old_pipeline])
        custom_wf = _workflow("custom-wf", [old_pipeline])
        unrelated = refresh._stored_workflow(
            "other", refs=[refresh._ref("pipe9")], deployed_from=[refresh._pipeline("pipe9")])
        rows = [bundle_wf, custom_wf, unrelated]
        pager = Pager({"Items": rows}, name="refresh walk", max_reads=10)
        table = refresh.FakeWorkflowTable(rows, pager=pager)
        with patch(f"{WS}._workflow_table", return_value=table), \
                patch(f"{WS}._get_pipeline_record",
                      side_effect=lambda db, pid, **_kw: new_pipeline
                      if pid == "conv" else refresh._pipeline(pid)), \
                patch(f"{WS}.workflowAsl.deploy_state_machine",
                      side_effect=refresh._deployed()) as deploy, \
                patch(f"{WS}.log_actions"):
            result = ws.refresh_pipeline_references("GLOBAL", "conv")
        assert result == {"workflowCount": 2, "outcomes": {"redeployed": 2}}
        assert {c.args[1] for c in deploy.call_args_list} == {"conv", "custom-wf"}
        written = dict(table.writes)
        for key in (("GLOBAL", "conv"), ("db-custom", "custom-wf")):
            assert written[key]["pipelineFingerprints"] == \
                wa.workflow_pipeline_fingerprints([new_pipeline])
        # The fingerprint really moved with the callback mode, so the walk has something to detect.
        assert wa.pipeline_asl_fingerprint(old_pipeline) != wa.pipeline_asl_fingerprint(new_pipeline)

    def test_a_redeploy_publishes_a_new_version_and_does_not_touch_the_running_one(self):
        # In-flight executions run on the version they started with. The refresh only moves the
        # workflow's recorded version forward; it never stops, edits or deletes a running version.
        old_pipeline = _stored_pipeline("Disabled")
        row = _workflow("conv", [old_pipeline])
        table = refresh.FakeWorkflowTable(
            [row], pager=Pager({"Items": [row]}, name="refresh walk", max_reads=10))
        with patch(f"{WS}._workflow_table", return_value=table), \
                patch(f"{WS}._get_pipeline_record", return_value=_stored_pipeline("Enabled")), \
                patch(f"{WS}.workflowAsl.deploy_state_machine",
                      side_effect=refresh._deployed()), \
                patch(f"{WS}.log_actions"), \
                patch(f"{WS}.sfn_client", create=True) as m_sfn:
            ws.refresh_pipeline_references("GLOBAL", "conv")
        written = dict(table.writes)[("GLOBAL", "conv")]
        assert written["workflowVersionArn"] == ARN + ":4"
        assert not m_sfn.stop_execution.called
        assert not m_sfn.delete_state_machine_version.called
        assert not m_sfn.delete_state_machine.called
