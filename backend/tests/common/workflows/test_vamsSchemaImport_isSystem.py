# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A bundle's `isSystem` reaches the pipeline and workflow bodies the importer sends, on create AND on
update, and an absent key sends False — the bundle is authoritative, so a redeploy re-asserts the flag
either way. Templates and triggers carry no such key: on them it is an unknown key and is reported."""

import pytest

from backend.backend.common.workflows import vamsSchemaImport as vsi


def _bundle(pipeline_extra=None, workflow_extra=None):
    bundle = {
        "pipeline": {"pipelineId": "sys-p", "pipelineName": "System P", "category": "SYSTEM - Test",
                     "executionConfig": {"executionType": "Lambda", "lambda": {}}},
        "workflow": {"workflowId": "sys-w", "workflowName": "System W", "category": "SYSTEM - Test",
                     "triggers": [{"triggerType": "fileUpload", "inputFileFilters": {"allow": ["*.glb"]}}]},
        "templates": [{"templateId": "t", "templateName": "T"}],
    }
    bundle["pipeline"].update(pipeline_extra or {})
    bundle["workflow"].update(workflow_extra or {})
    return bundle


def _by_kind(requests, kind):
    return next(r for r in requests if r["kind"] == kind)


@pytest.mark.unit
class TestIsSystemIsAnAcceptedBundleKey:
    def test_pipeline_and_workflow_accept_it(self):
        assert "isSystem" in vsi._PIPELINE_KEYS
        assert "isSystem" in vsi._WORKFLOW_KEYS
        bundle = _bundle({"isSystem": True}, {"isSystem": True})
        assert vsi.unknown_bundle_keys(bundle) == []

    def test_templates_and_triggers_do_not(self):
        bundle = _bundle()
        bundle["templates"][0]["isSystem"] = True
        bundle["workflow"]["triggers"][0]["isSystem"] = True
        assert vsi.unknown_bundle_keys(bundle) == [
            "templates[0].isSystem", "workflow.triggers[0].isSystem"]


@pytest.mark.unit
class TestIsSystemReachesEveryBody:
    def test_true_is_emitted_on_create_and_update(self):
        requests = vsi.build_import_requests(_bundle({"isSystem": True}, {"isSystem": True}))
        pipeline, workflow = _by_kind(requests, "pipeline"), _by_kind(requests, "workflow")
        assert pipeline["createBody"]["isSystem"] is True
        assert pipeline["updateBody"]["isSystem"] is True
        assert workflow["createBody"]["isSystem"] is True
        assert workflow["updateBody"]["isSystem"] is True

    def test_an_absent_key_re_asserts_false(self):
        requests = vsi.build_import_requests(_bundle())
        for kind in ("pipeline", "workflow"):
            assert _by_kind(requests, kind)["createBody"]["isSystem"] is False
            assert _by_kind(requests, kind)["updateBody"]["isSystem"] is False

    def test_a_truthy_non_boolean_is_coerced(self):
        requests = vsi.build_import_requests(_bundle({"isSystem": "yes"}, {"isSystem": 1}))
        assert _by_kind(requests, "pipeline")["createBody"]["isSystem"] is True
        assert _by_kind(requests, "workflow")["updateBody"]["isSystem"] is True

    def test_the_template_body_does_not_gain_the_key(self):
        requests = vsi.build_import_requests(_bundle({"isSystem": True}, {"isSystem": True}))
        assert "isSystem" not in _by_kind(requests, "template")["createBody"]
        assert "isSystem" not in _by_kind(requests, "trigger")["setBody"]
