# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for WB6: the pure vamsSchema -> V2 cross-call request builder
(common/workflows/vamsSchemaImport.py) and the import CR handler upsert flow."""

import json
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.common.workflows import vamsSchemaImport as vsi
from backend.backend.common.workflows import executionValidation as ev


# ============================ pure request builder ============================

@pytest.mark.unit
class TestVamsSchemaImport:
    def _bundle(self, **over):
        b = {
            "pipeline": {
                "pipelineId": "conv", "pipelineName": "Converter", "category": "conversion",
                "executionConfig": {"executionType": "Lambda", "lambda": {}},
                "systemConfig": {"requireTemplate": False},
            },
            "workflow": {
                "workflowId": "conv", "workflowName": "Convert WF", "category": "conversion",
                "triggers": [{"triggerType": "fileUpload", "inputFileFilters": {"allow": [".glb"]},
                              "defaultTemplateIds": {"GLOBAL:conv": "to-obj"}}],
            },
            "templates": [{"templateId": "to-obj", "templateName": "To OBJ", "configFormat": "json",
                           "configBody": "{\"to\":\"obj\"}"}],
        }
        b.update(over)
        return b

    def test_pipeline_required(self):
        with pytest.raises(vsi.VamsSchemaError):
            vsi.build_import_requests({})
        with pytest.raises(vsi.VamsSchemaError):
            vsi.build_import_requests({"pipeline": {"pipelineId": "x"}})  # missing pipelineName

    def test_request_order_and_targets(self):
        reqs = vsi.build_import_requests(self._bundle())
        kinds = [(r["kind"], r["target"]) for r in reqs]
        # pipeline -> template -> workflow -> trigger, in that order.
        assert kinds == [
            ("pipeline", vsi.TARGET_PIPELINE_SERVICE),
            ("template", vsi.TARGET_TEMPLATE_SERVICE),
            ("workflow", vsi.TARGET_WORKFLOW_SERVICE),
            ("trigger", vsi.TARGET_TRIGGER_SERVICE),
        ]

    def test_lambda_resource_injected(self):
        reqs = vsi.build_import_requests(
            self._bundle(), resource_overrides={"lambdaName": "vams-conv-fn"})
        pipe = reqs[0]
        assert pipe["createBody"]["executionConfig"]["lambda"]["resourceId"] == "vams-conv-fn"

    def test_sqs_and_eventbridge_injection(self):
        b = self._bundle()
        b["pipeline"]["executionConfig"] = {"executionType": "SQS", "sqs": {}}
        reqs = vsi.build_import_requests(b, resource_overrides={"sqsQueueUrl": "https://q/1"})
        assert reqs[0]["createBody"]["executionConfig"]["sqs"]["queueUrl"] == "https://q/1"

        b["pipeline"]["executionConfig"] = {"executionType": "EventBridge", "eventBridge": {}}
        reqs = vsi.build_import_requests(b, resource_overrides={
            "eventBridgeBusArn": "arn:bus", "eventBridgeSource": "vams.x", "eventBridgeDetailType": "run"})
        eb = reqs[0]["createBody"]["executionConfig"]["eventBridge"]
        assert eb["busArn"] == "arn:bus" and eb["source"] == "vams.x" and eb["detailType"] == "run"

    def test_id_overrides(self):
        reqs = vsi.build_import_requests(
            self._bundle(), id_overrides={"pipelineId": "conv-override", "workflowId": "wf-override"})
        assert reqs[0]["createBody"]["pipelineId"] == "conv-override"
        wf = next(r for r in reqs if r["kind"] == "workflow")
        assert wf["createBody"]["workflowId"] == "wf-override"
        # The workflow's default specifiedPipelines references the overridden pipeline id.
        assert wf["createBody"]["specifiedPipelines"][0]["pipelineId"] == "conv-override"

    def test_minimal_pipeline_only(self):
        reqs = vsi.build_import_requests({"pipeline": {"pipelineId": "p", "pipelineName": "P"}})
        assert [r["kind"] for r in reqs] == ["pipeline"]  # no workflow/templates required

    def test_update_body_reenables(self):
        reqs = vsi.build_import_requests(self._bundle())
        assert reqs[0]["updateBody"]["enabled"] is True  # re-register unarchives/enables
        wf = next(r for r in reqs if r["kind"] == "workflow")
        assert wf["updateBody"]["enabled"] is True

    def test_trigger_enabled_from_schema_by_default(self):
        # No override -> the trigger's schema `enabled` (default True) is preserved.
        reqs = vsi.build_import_requests(self._bundle())
        trig = next(r for r in reqs if r["kind"] == "trigger")
        assert trig["setBody"]["enabled"] is True

    def test_trigger_enabled_override_disables(self):
        # Deploy-time override False forces the trigger disabled even though the schema says enabled.
        reqs = vsi.build_import_requests(self._bundle(), trigger_enabled_override=False)
        trig = next(r for r in reqs if r["kind"] == "trigger")
        assert trig["setBody"]["enabled"] is False
        # Filters + default templates still ship regardless of enable state.
        assert trig["setBody"]["inputFileFilters"] == {"allow": [".glb"]}
        assert trig["setBody"]["defaultTemplateIds"] == {"GLOBAL:conv": "to-obj"}

    def test_trigger_enabled_override_enables(self):
        b = self._bundle()
        b["workflow"]["triggers"][0]["enabled"] = False  # schema default disabled
        reqs = vsi.build_import_requests(b, trigger_enabled_override=True)
        trig = next(r for r in reqs if r["kind"] == "trigger")
        assert trig["setBody"]["enabled"] is True  # deploy-time opt-in wins

    def test_workflow_defaults_to_pipeline_ref(self):
        b = self._bundle()
        b["workflow"].pop("workflowId", None)  # workflow id defaults to pipeline id
        reqs = vsi.build_import_requests(b)
        wf = next(r for r in reqs if r["kind"] == "workflow")
        assert wf["id"] == "conv"

    def test_collect_ids(self):
        ids = vsi.collect_ids(self._bundle())
        assert ids == {"pipelineDatabaseId": "GLOBAL", "pipelineId": "conv",
                       "workflowDatabaseId": "GLOBAL", "workflowId": "conv"}

    def test_template_is_default_forwarded(self):
        """A schema template flagged isDefault must reach the create/update bodies: execute
        auto-selects the pipeline's default template, which is what lets a requireTemplate
        pipeline run without the caller naming one."""
        b = self._bundle()
        b["templates"] = [{"templateId": "t1", "templateName": "T1", "isDefault": True}]
        reqs = vsi.build_import_requests(b)
        tpl = next(r for r in reqs if r["kind"] == "template")
        assert tpl["createBody"]["isDefault"] is True
        assert tpl["updateBody"]["isDefault"] is True

    def test_template_is_default_absent_is_false(self):
        b = self._bundle()
        b["templates"] = [{"templateId": "t1", "templateName": "T1"}]
        reqs = vsi.build_import_requests(b)
        tpl = next(r for r in reqs if r["kind"] == "template")
        assert tpl["createBody"]["isDefault"] is False

    def test_sole_template_of_require_template_pipeline_becomes_default(self):
        """A requireTemplate pipeline shipping exactly one template must have it promoted to the
        default. Without a default, execute rejects the run unless the caller names the template,
        even though the single template is the only possible choice."""
        b = self._bundle()
        b["pipeline"]["systemConfig"] = {"requireTemplate": True}
        b["templates"] = [{"templateId": "t1", "templateName": "T1"}]
        reqs = vsi.build_import_requests(b)
        tpl = next(r for r in reqs if r["kind"] == "template")
        assert tpl["createBody"]["isDefault"] is True
        assert tpl["updateBody"]["isDefault"] is True

    def test_sole_template_not_promoted_when_template_not_required(self):
        b = self._bundle()
        b["pipeline"]["systemConfig"] = {"requireTemplate": False}
        b["templates"] = [{"templateId": "t1", "templateName": "T1"}]
        reqs = vsi.build_import_requests(b)
        tpl = next(r for r in reqs if r["kind"] == "template")
        assert tpl["createBody"]["isDefault"] is False

    def test_multiple_templates_are_not_auto_defaulted(self):
        """With more than one template the choice is ambiguous, so the bundle must declare it."""
        b = self._bundle()
        b["pipeline"]["systemConfig"] = {"requireTemplate": True}
        b["templates"] = [
            {"templateId": "t1", "templateName": "T1"},
            {"templateId": "t2", "templateName": "T2"},
        ]
        reqs = vsi.build_import_requests(b)
        tpls = [r for r in reqs if r["kind"] == "template"]
        assert [t["createBody"]["isDefault"] for t in tpls] == [False, False]

    def test_explicit_default_wins_over_promotion(self):
        b = self._bundle()
        b["pipeline"]["systemConfig"] = {"requireTemplate": True}
        b["templates"] = [{"templateId": "t1", "templateName": "T1", "isDefault": True}]
        reqs = vsi.build_import_requests(b)
        tpl = next(r for r in reqs if r["kind"] == "template")
        assert tpl["createBody"]["isDefault"] is True

    def test_template_requires_id(self):
        b = self._bundle()
        b["templates"] = [{"templateName": "No Id"}]
        with pytest.raises(vsi.VamsSchemaError):
            vsi.build_import_requests(b)

    def test_external_hardcoded_resource_preserved(self):
        # No override supplied -> a schema-hardcoded resourceId is left intact (external self-register).
        b = self._bundle()
        b["pipeline"]["executionConfig"] = {"executionType": "Lambda", "lambda": {"resourceId": "ext-fn"}}
        reqs = vsi.build_import_requests(b, resource_overrides={})
        assert reqs[0]["createBody"]["executionConfig"]["lambda"]["resourceId"] == "ext-fn"


# ============================ import CR handler ============================

os.environ.setdefault("PIPELINE_SERVICE_V2_FUNCTION_NAME", "t-pipe-v2")
os.environ.setdefault("PIPELINE_TEMPLATE_SERVICE_FUNCTION_NAME", "t-tpl")
os.environ.setdefault("WORKFLOW_SERVICE_V2_FUNCTION_NAME", "t-wf-v2")
os.environ.setdefault("WORKFLOW_TRIGGER_SERVICE_FUNCTION_NAME", "t-trig")
os.environ.setdefault("LAMBDA_PIPELINE_SAMPLE_FUNCTION_BUCKET", "t-artefacts")

# handlers.workflows __init__ imports get_task_builder at import; stub it.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import importGlobalPipelineWorkflow as imp

IMOD = "backend.backend.handlers.workflows.importGlobalPipelineWorkflow"


def _resp(status_code, body=None):
    class _P:
        def read(self):
            return json.dumps({"statusCode": status_code,
                               "body": json.dumps(body or {})}).encode()
    return {"Payload": _P()}


@pytest.mark.unit
class TestSystemConfigDefaultsFill:
    """A bundle's PARTIAL systemConfig is stored completed with the builder defaults.

    Both records store systemConfig wholesale (create/update replace, never merge), so a partial block
    would persist as-is — and every later systemConfig field addition would silently change the stored
    meaning of every bundle written before it. Filling at registration makes omissions explicitly the
    documented defaults, so a new field is inert for existing bundles.
    """

    def test_a_partial_workflow_block_is_completed_with_every_default(self):
        from backend.backend.common.workflows.workflowRecords import build_workflow_system_config
        defaults = build_workflow_system_config()
        body = vsi._workflow_create_body(
            {"workflowName": "W", "systemConfig": {"inputFileArity": "multi"}},
            "GLOBAL", "w1", "GLOBAL", "p1")
        stored = body["systemConfig"]
        assert set(stored.keys()) == set(defaults.keys())
        # The declaration wins; everything else is the default.
        assert stored["inputFileArity"] == "multi"
        for key, default_value in defaults.items():
            if key != "inputFileArity":
                assert stored[key] == default_value, key

    def test_a_partial_pipeline_block_is_completed_with_every_default(self):
        from backend.backend.common.workflows.pipelineRecords import build_pipeline_system_config
        defaults = build_pipeline_system_config()
        body = vsi._pipeline_create_body(
            {"pipelineName": "P", "systemConfig": {"requireTemplate": True}},
            "GLOBAL", "p1", {"executionType": "Lambda", "lambda": {}})
        stored = body["systemConfig"]
        assert set(stored.keys()) == set(defaults.keys())
        assert stored["requireTemplate"] is True
        assert stored["inputFileArity"] == defaults["inputFileArity"]

    def test_an_absent_block_becomes_the_full_defaults(self):
        from backend.backend.common.workflows.workflowRecords import build_workflow_system_config
        body = vsi._workflow_create_body(
            {"workflowName": "W"}, "GLOBAL", "w1", "GLOBAL", "p1")
        assert body["systemConfig"] == build_workflow_system_config()

    def test_a_partial_nested_map_keeps_its_sibling_defaults(self):
        """A declared assetScope that names one rule must not drop the other three."""
        body = vsi._workflow_create_body(
            {"workflowName": "W", "systemConfig": {"assetScope": {"folderAllowed": True}}},
            "GLOBAL", "w1", "GLOBAL", "p1")
        scope = body["systemConfig"]["assetScope"]
        assert scope["folderAllowed"] is True
        assert scope["singleAssetOnly"] is True
        assert scope["crossAssetAllowed"] is False
        assert scope["wholeAssetAllowed"] is False

    def test_the_wholeAsset_shorthand_is_not_contradicted_by_the_canonical_default(self):
        """The two spellings mean the same thing. Filling the canonical default alongside a shorthand
        declaration would let the default win at read time and silently disable whole-asset support."""
        body = vsi._pipeline_create_body(
            {"pipelineName": "P", "systemConfig": {"assetScope": {"wholeAsset": True}}},
            "GLOBAL", "p1", {"executionType": "Lambda", "lambda": {}})
        scope = body["systemConfig"]["assetScope"]
        assert scope["wholeAsset"] is True
        assert "wholeAssetAllowed" not in scope
        # The unrelated siblings are still filled.
        assert scope["singleAssetOnly"] is True

    def test_update_sends_the_full_defaults_when_the_bundle_declares_none(self):
        """Registration is self-healing: a bundle with no systemConfig still writes the complete
        defaults. Sending the raw {} once blanked stored blocks; merely OMITTING the field instead left
        rows already blanked that way stuck empty, because nothing rewrote them (observed live on
        conversion-3d-basic and metadata-extraction-cad-mesh)."""
        from backend.backend.common.workflows.workflowRecords import build_workflow_system_config
        body = vsi._workflow_update_body({"workflowName": "W"}, "GLOBAL", "p1")
        assert body["systemConfig"] == build_workflow_system_config()

    def test_update_sends_a_declared_block_filled(self):
        from backend.backend.common.workflows.workflowRecords import build_workflow_system_config
        body = vsi._workflow_update_body(
            {"workflowName": "W", "systemConfig": {"concurrencyRestriction": "perAsset"}},
            "GLOBAL", "p1")
        stored = body["systemConfig"]
        assert stored["concurrencyRestriction"] == "perAsset"
        assert set(stored.keys()) == set(build_workflow_system_config().keys())

    def test_the_declaration_is_not_mutated(self):
        declared = {"inputFileArity": "none", "outputTarget": {"allowOverride": True}}
        snapshot = json.loads(json.dumps(declared))
        vsi._workflow_create_body(
            {"workflowName": "W", "systemConfig": declared}, "GLOBAL", "w1", "GLOBAL", "p1")
        assert declared == snapshot

    def test_a_registered_bundle_with_no_inputs_and_no_output_override_is_REJECTED(self):
        """Registration runs the same model validation as the API, so a bundle that cannot execute is
        kicked back at deploy rather than landing as an unusable row. arity 'none' means no input asset
        to lock output to, so the destination must be selectable at execute time."""
        from aws_lambda_powertools.utilities.parser import ValidationError
        from backend.backend.models.workflows import CreateWorkflowRequestModel
        body = vsi._workflow_create_body(
            {"workflowName": "W", "systemConfig": {"inputFileArity": "none"}},
            "GLOBAL", "wf-none", "GLOBAL", "pipe-none")
        with pytest.raises(ValidationError) as raised:
            CreateWorkflowRequestModel(**body)
        assert "allow output override" in str(raised.value)

        # With the output asset selectable at execute time it validates.
        ok = vsi._workflow_create_body(
            {"workflowName": "W", "systemConfig": {
                "inputFileArity": "none",
                "outputTarget": {"locationType": "asset", "allowOverride": True}}},
            "GLOBAL", "wf-none", "GLOBAL", "pipe-none")
        CreateWorkflowRequestModel(**ok)

        # So does results-only, which writes no asset at all.
        results_only = vsi._workflow_create_body(
            {"workflowName": "W", "systemConfig": {
                "inputFileArity": "none", "outputTarget": {"locationType": "none"}}},
            "GLOBAL", "wf-none", "GLOBAL", "pipe-none")
        CreateWorkflowRequestModel(**results_only)


@pytest.mark.unit
class TestImportCrHandler:
    def _inline_props(self):
        return {"inlineBundle": {
            "pipeline": {"pipelineId": "conv", "pipelineName": "Converter",
                         "executionConfig": {"executionType": "Lambda", "lambda": {}}},
            "workflow": {"workflowId": "conv", "workflowName": "Convert WF"},
        }, "resourceOverrides": {"lambdaName": "vams-conv-fn"}}

    def test_register_creates_when_absent(self):
        # Every exists-probe returns 404 (absent) -> create (POST 200).
        calls = []

        def _invoke(FunctionName, InvocationType, Payload):
            ev = json.loads(Payload.decode("utf-8"))
            method = ev["requestContext"]["http"]["method"]
            calls.append((FunctionName, method, ev["requestContext"]["http"]["path"]))
            if method == "GET":
                return _resp(404)
            return _resp(200, {"message": "ok"})

        with patch.object(imp, "lambda_client") as m:
            m.invoke.side_effect = _invoke
            result = imp.register_bundle(self._inline_props())
        assert result["ids"]["pipelineId"] == "conv"
        # A POST create happened for both pipeline and workflow.
        posts = [c for c in calls if c[1] == "POST"]
        assert any("/pipelines" in c[2] for c in posts)
        assert any("/workflows" in c[2] for c in posts)

    def test_register_updates_when_present(self):
        def _invoke(FunctionName, InvocationType, Payload):
            ev = json.loads(Payload.decode("utf-8"))
            method = ev["requestContext"]["http"]["method"]
            if method == "GET":
                return _resp(200, {"message": "exists"})  # already present -> update
            return _resp(200, {"message": "ok"})

        applied = []
        with patch.object(imp, "lambda_client") as m:
            m.invoke.side_effect = _invoke
            result = imp.register_bundle(self._inline_props())
            applied = result["applied"]
        assert any("updated" in a for a in applied)

    def test_register_raises_on_service_error(self):
        def _invoke(FunctionName, InvocationType, Payload):
            ev = json.loads(Payload.decode("utf-8"))
            if ev["requestContext"]["http"]["method"] == "GET":
                return _resp(404)
            return _resp(400, {"message": "bad config"})  # create fails

        with patch.object(imp, "lambda_client") as m:
            m.invoke.side_effect = _invoke
            with pytest.raises(imp.ImportError_):
                imp.register_bundle(self._inline_props())

    def test_archive_bundle_best_effort(self):
        def _invoke(FunctionName, InvocationType, Payload):
            return _resp(200, {"message": "archived"})
        with patch.object(imp, "lambda_client") as m:
            m.invoke.side_effect = _invoke
            result = imp.archive_bundle(self._inline_props())
        assert result["ids"]["pipelineId"] == "conv"
        assert result["warnings"] == []

    def test_delete_never_fails_teardown(self):
        # A Delete whose archive fails still returns normally (the provider framework then signals
        # SUCCESS); the failure is surfaced as a warning attribute.
        event = {"RequestType": "Delete", "ResourceProperties": self._inline_props(),
                 "StackId": "s", "RequestId": "r", "LogicalResourceId": "l",
                 "ResponseURL": "https://cfn", "PhysicalResourceId": "conv"}
        ctx = MagicMock(); ctx.log_stream_name = "log"
        with patch.object(imp, "lambda_client") as m:
            m.invoke.side_effect = RuntimeError("boom")
            resp = imp.lambda_handler(event, ctx)
        assert "PhysicalResourceId" not in resp
        assert "pipeline archive" in resp["Data"]["warnings"]
        assert "workflow archive" in resp["Data"]["warnings"]

    def test_create_returns_provider_shape(self):
        # As a Provider onEventHandler, a successful Create returns {PhysicalResourceId, Data} and
        # writes no CloudFormation response itself.
        def _invoke(FunctionName, InvocationType, Payload):
            ev = json.loads(Payload.decode("utf-8"))
            if ev["requestContext"]["http"]["method"] == "GET":
                return _resp(404)
            return _resp(200, {"message": "ok"})

        event = {"RequestType": "Create", "ResourceProperties": self._inline_props(),
                 "StackId": "s", "RequestId": "r", "LogicalResourceId": "l",
                 "ResponseURL": "https://cfn"}
        ctx = MagicMock(); ctx.log_stream_name = "log"
        with patch.object(imp, "lambda_client") as m:
            m.invoke.side_effect = _invoke
            resp = imp.lambda_handler(event, ctx)
        assert resp["PhysicalResourceId"] == "conv"
        assert resp["Data"]["pipelineId"] == "conv"
        assert not hasattr(imp, "send_cfn_response")

    def _renamed_props(self):
        props = {"inlineBundle": {
            "pipeline": {"pipelineId": "conv-v2", "pipelineName": "Converter",
                         "executionConfig": {"executionType": "Lambda", "lambda": {}}},
            "workflow": {"workflowId": "conv-v2", "workflowName": "Convert WF"},
        }, "resourceOverrides": {"lambdaName": "vams-conv-fn"}}
        return props

    def test_update_archives_the_superseded_ids(self):
        # An id change (idOverrides/schema pipelineId rename) does not change the physical id, so
        # CloudFormation sends no Delete for the retired registration. The Update archives it itself
        # from OldResourceProperties — otherwise two built-ins stay active for one deployed lambda.
        calls = []

        def _invoke(FunctionName, InvocationType, Payload):
            ev = json.loads(Payload.decode("utf-8"))
            method = ev["requestContext"]["http"]["method"]
            calls.append((method, ev["requestContext"]["http"]["path"]))
            if method == "GET":
                return _resp(404)
            return _resp(200, {"message": "ok"})

        event = {"RequestType": "Update", "ResourceProperties": self._renamed_props(),
                 "OldResourceProperties": self._inline_props(),
                 "StackId": "s", "RequestId": "r", "LogicalResourceId": "l",
                 "ResponseURL": "https://cfn", "PhysicalResourceId": "conv"}
        ctx = MagicMock(); ctx.log_stream_name = "log"
        with patch.object(imp, "lambda_client") as m:
            m.invoke.side_effect = _invoke
            resp = imp.lambda_handler(event, ctx)
        assert resp["Data"]["pipelineId"] == "conv-v2"
        deletes = [path for method, path in calls if method == "DELETE"]
        assert any(path.endswith("/pipelines/conv") for path in deletes), deletes
        assert any(path.endswith("/workflows/conv") for path in deletes), deletes
        # The newly registered ids are never archived by the same invocation.
        assert not any("conv-v2" in path for path in deletes), deletes

    def test_update_without_id_change_archives_nothing(self):
        # A plain re-register (same ids) must not archive the rows it just wrote.
        calls = []

        def _invoke(FunctionName, InvocationType, Payload):
            ev = json.loads(Payload.decode("utf-8"))
            method = ev["requestContext"]["http"]["method"]
            calls.append((method, ev["requestContext"]["http"]["path"]))
            if method == "GET":
                return _resp(200, {"message": "exists"})
            return _resp(200, {"message": "ok"})

        event = {"RequestType": "Update", "ResourceProperties": self._inline_props(),
                 "OldResourceProperties": self._inline_props(),
                 "StackId": "s", "RequestId": "r", "LogicalResourceId": "l",
                 "ResponseURL": "https://cfn", "PhysicalResourceId": "conv"}
        ctx = MagicMock(); ctx.log_stream_name = "log"
        with patch.object(imp, "lambda_client") as m:
            m.invoke.side_effect = _invoke
            resp = imp.lambda_handler(event, ctx)
        assert "warnings" not in resp["Data"]
        assert not [c for c in calls if c[0] == "DELETE"], calls

    def test_create_failure_raises_for_the_provider(self):
        # A registration failure must raise so the provider framework signals FAILED — returning a
        # 500 payload would let the framework write SUCCESS and pass a broken deployment.
        def _invoke(FunctionName, InvocationType, Payload):
            ev = json.loads(Payload.decode("utf-8"))
            if ev["requestContext"]["http"]["method"] == "GET":
                return _resp(404)
            return _resp(400, {"message": "bad config"})

        event = {"RequestType": "Create", "ResourceProperties": self._inline_props(),
                 "StackId": "s", "RequestId": "r", "LogicalResourceId": "l",
                 "ResponseURL": "https://cfn"}
        ctx = MagicMock(); ctx.log_stream_name = "log"
        with patch.object(imp, "lambda_client") as m:
            m.invoke.side_effect = _invoke
            with pytest.raises(imp.ImportError_):
                imp.lambda_handler(event, ctx)

    def test_exists_probe_includes_archived(self):
        # The exists probe must see soft-archived rows: an archived built-in still occupies its id, so
        # the register must take the update (unarchive) branch, not a create the service rejects.
        probes = []

        def _invoke(FunctionName, InvocationType, Payload):
            ev = json.loads(Payload.decode("utf-8"))
            if ev["requestContext"]["http"]["method"] == "GET":
                probes.append(ev.get("queryStringParameters"))
                return _resp(200, {"message": "exists"})
            return _resp(200, {"message": "ok"})

        with patch.object(imp, "lambda_client") as m:
            m.invoke.side_effect = _invoke
            imp.register_bundle(self._inline_props())
        assert probes
        assert all(p.get("includeArchived") == "true" for p in probes), probes

    def test_archive_deletes_both_workflow_and_pipeline(self):
        # Turning off a built-in pipeline in CDK archives BOTH its workflow and its pipeline: the
        # archive path issues a DELETE cross-call to the workflow service and the pipeline service.
        calls = []

        def _invoke(FunctionName, InvocationType, Payload):
            ev = json.loads(Payload.decode("utf-8"))
            calls.append((ev["requestContext"]["http"]["method"],
                          ev["requestContext"]["http"]["path"]))
            return _resp(200, {"message": "archived"})

        with patch.object(imp, "lambda_client") as m:
            m.invoke.side_effect = _invoke
            result = imp.archive_bundle(self._inline_props())
        assert result["warnings"] == []
        deletes = [c for c in calls if c[0] == "DELETE"]
        assert any("/workflows/conv" in path for _, path in deletes), deletes
        assert any("/pipelines/conv" in path for _, path in deletes), deletes

    def test_reenable_reregister_reenables_both(self):
        # Re-enabling a previously-disabled built-in (re-register): the pipeline + workflow already
        # exist (archived), so the import updates them via PUT with enabled=True, which the services
        # treat as unarchive/re-enable — restoring both.
        put_bodies = []

        def _invoke(FunctionName, InvocationType, Payload):
            ev = json.loads(Payload.decode("utf-8"))
            method = ev["requestContext"]["http"]["method"]
            if method == "GET":
                return _resp(200, {"message": "exists"})  # already present (archived) -> update
            if method == "PUT":
                put_bodies.append((ev["requestContext"]["http"]["path"],
                                   json.loads(ev.get("body") or "{}")))
            return _resp(200, {"message": "ok"})

        with patch.object(imp, "lambda_client") as m:
            m.invoke.side_effect = _invoke
            imp.register_bundle(self._inline_props())
        pipeline_puts = [b for p, b in put_bodies if "/pipelines/conv" in p]
        workflow_puts = [b for p, b in put_bodies if "/workflows/conv" in p]
        assert pipeline_puts and pipeline_puts[0].get("enabled") is True
        assert workflow_puts and workflow_puts[0].get("enabled") is True
        # The update also clears archived so a soft-archived built-in is restored.
        assert pipeline_puts[0].get("archived") is False
        assert workflow_puts[0].get("archived") is False

    def test_bundle_from_s3_keys(self):
        props = {"bundleS3Keys": {"pipeline": "vamsSchema/conv/pipeline.json",
                                  "workflow": "vamsSchema/conv/workflow.json",
                                  "templates": ["vamsSchema/conv/templates/to-obj.json"]}}
        files = {
            "vamsSchema/conv/pipeline.json": {"pipelineId": "conv", "pipelineName": "C",
                                              "executionConfig": {"executionType": "Lambda", "lambda": {}}},
            "vamsSchema/conv/workflow.json": {"workflowId": "conv", "workflowName": "W"},
            "vamsSchema/conv/templates/to-obj.json": {"templateId": "to-obj", "templateName": "T"},
        }
        with patch(f"{IMOD}._read_s3_json", side_effect=lambda k: files[k]):
            bundle = imp.assemble_bundle(props)
        assert bundle["pipeline"]["pipelineId"] == "conv"
        assert bundle["templates"][0]["templateId"] == "to-obj"


# ============ built-in schema files validate against the request models ============

@pytest.mark.unit
class TestBuiltInSchemasValidate:
    """Every shipped backendPipelines/*/vamsSchema bundle must produce create bodies that pass the
    pipeline / workflow / template request-model validators — the same validation the import
    custom-resource hits at deploy time. Guards against a malformed built-in schema shipping."""

    def _schema_root(self):
        here = os.path.dirname(__file__)
        return os.path.normpath(os.path.join(here, "..", "..", "..", "..", "backendPipelines"))

    def _pipeline_files(self):
        import glob
        root = self._schema_root()
        found = set()
        for pat in ("/**/vamsSchema/**/pipeline.json", "/**/vamsSchema/pipeline.json"):
            for f in glob.glob(root + pat, recursive=True):
                found.add(os.path.normpath(f))
        return sorted(found)

    def _template_files(self, pipeline_file):
        """A bundle's template files. They live in a `templates/` subdirectory beside pipeline.json —
        NOT alongside it, which is why this is centralized rather than globbed per test."""
        import glob
        return sorted(glob.glob(os.path.join(os.path.dirname(pipeline_file), "templates", "*.json")))

    def test_all_built_in_pipeline_bundles_validate(self):
        from backend.backend.models.pipelines import (
            CreatePipelineRequestModel, CreateTemplateRequestModel)
        from backend.backend.models.workflows import CreateWorkflowRequestModel

        pipeline_files = self._pipeline_files()
        # Sanity: the built-in pipelines exist and are being checked.
        assert len(pipeline_files) >= 20
        templates_seen = 0

        for pf in pipeline_files:
            d = os.path.dirname(pf)
            pipeline = json.load(open(pf))
            pid = pipeline.get("pipelineId") or "placeholder-id"
            exec_cfg = vsi._inject_execution_resources(
                pipeline.get("executionConfig", {}), {"lambdaName": "dummy-fn"})
            body = vsi._pipeline_create_body(pipeline, "GLOBAL", pid, exec_cfg)
            CreatePipelineRequestModel(**body)  # raises on invalid

            wf = os.path.join(d, "workflow.json")
            if os.path.exists(wf):
                w = json.load(open(wf))
                wbody = vsi._workflow_create_body(
                    w, "GLOBAL", w.get("workflowId") or pid, "GLOBAL", pid)
                CreateWorkflowRequestModel(**wbody)

            template_files = self._template_files(pf)
            templates_seen += len(template_files)
            for tf in template_files:
                t = json.load(open(tf, encoding="utf-8"))
                CreateTemplateRequestModel(
                    **vsi._template_create_body(t, t.get("templateId") or "tid"))

        # The built-ins DO ship templates, so a run that validated none means the discovery glob broke
        # rather than that everything passed.
        assert templates_seen >= 20, f"only {templates_seen} built-in templates discovered"

    def test_no_bundle_excludes_everything(self):
        """No shipped bundle may carry a match-everything exclude at any level.

        Exclude is applied after allow, so '*' in an exclude removes every file — the pipeline or
        workflow becomes permanently unrunnable, and a trigger can never fire. The request models
        reject it, which means such a bundle would fail the deploy-time import; this catches it at
        commit time instead of at deploy."""
        offenders = []

        def check(filters, path, where):
            for pattern in (filters or {}).get("exclude") or []:
                if str(pattern).strip() in ev.MATCH_EVERYTHING_PATTERNS:
                    offenders.append(f"{os.path.basename(path)}:{where}:{pattern}")

        for pf in self._pipeline_files():
            pipeline = json.load(open(pf, encoding="utf-8"))
            check((pipeline.get("systemConfig") or {}).get("inputFileFilters"), pf, "systemConfig")

            wf = os.path.join(os.path.dirname(pf), "workflow.json")
            if os.path.exists(wf):
                workflow = json.load(open(wf, encoding="utf-8"))
                check((workflow.get("systemConfig") or {}).get("inputFileFilters"), wf,
                      "systemConfig")
                for trigger in workflow.get("triggers") or []:
                    check((trigger or {}).get("inputFileFilters"), wf, "trigger")

            for tf in self._template_files(pf):
                template = json.load(open(tf, encoding="utf-8"))
                check((template.get("overrides") or {}).get("inputFileFilters"), tf, "overrides")

        assert not offenders, (
            "these bundles exclude every file, so nothing could ever run: " f"{offenders}")

    def test_every_built_in_template_carries_input_instructions(self):
        """A built-in template must tell the operator what it takes in.

        The execute wizard renders inputInstructions as the only in-product description of a
        template's metadata keys, accepted inputs, and precedence rules. A template shipping with an
        empty string is not a validation error anywhere — it simply renders nothing, and the operator
        has to read the pipeline source to learn which metadata keys it reads. The 4096 cap is
        asserted too: the registration path invokes the real template service as a lambdaCrossCall,
        so the request models DO enforce it — but only at deploy time, where it surfaces as a failed
        import of an otherwise-successful stack. Checking it here fails at commit time instead."""
        missing, too_long = [], []
        for pf in self._pipeline_files():
            for tf in self._template_files(pf):
                template = json.load(open(tf, encoding="utf-8"))
                instructions = template.get("inputInstructions") or ""
                name = template.get("templateId") or os.path.basename(tf)
                if not instructions.strip():
                    missing.append(name)
                if len(instructions) > 4096:
                    too_long.append(f"{name} ({len(instructions)})")

        assert not missing, f"built-in templates with no input instructions: {missing}"
        assert not too_long, f"input instructions exceed the 4096 model cap: {too_long}"

    def test_declared_tags_are_referenced_by_the_config_body(self):
        """A declared tag whose {{key}} appears nowhere in the configBody is silently dropped.

        Substitution is textual: the resolver replaces {{key}} occurrences in the body and returns
        that text as the renderedConfig. So a template can declare a tag, the execute screen can
        collect a value for it, the value can be recorded on the execution — and the pipeline still
        never sees it, because nothing in the body referenced it. Nothing errors: an unmatched
        PROVIDED tag is ignored by design, and an unreferenced DECLARED tag is not checked at all.

        The failure is near-invisible for a pipeline that defaults the missing value (a prompt, say):
        the run succeeds, just not on the caller's input. This asserts the link exists."""
        unreferenced = []
        checked = 0
        for pf in self._pipeline_files():
            for tf in self._template_files(pf):
                t = json.load(open(tf, encoding="utf-8"))
                schema = t.get("tagSchema") or []
                if not schema:
                    continue
                checked += 1
                body = t.get("configBody") or ""
                for field in schema:
                    key = field.get("tagKey")
                    if key and "{{" + str(key) + "}}" not in body:
                        unreferenced.append(f"{os.path.basename(tf)}:{key}")

        assert checked > 0, "no built-in template declares a tagSchema; audit would be vacuous"
        assert not unreferenced, (
            "these templates declare tags their configBody never references, so the values are "
            f"collected and then dropped: {unreferenced}")
