# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the workflow trigger handler (workflowTriggerService). Parent-workflow Tier-2 auth
+ trigger CRUD; CasbinEnforcer/request_to_claims/tables patched. IDs >=3 chars (isolation-safe)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.workflows.workflowTriggerService import lambda_handler

MOD = "backend.backend.handlers.workflows.workflowTriggerService"


def _event(method, path, path_params, body=None):
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": path_params,
        "queryStringParameters": None,
        "headers": {"authorization": "Bearer test-token"},
        "body": json.dumps(body) if body is not None else None,
    }


def _enforcer(api=True, obj=True):
    inst = MagicMock()
    inst.enforceAPI.return_value = api
    inst.enforce.return_value = obj
    return inst


WF_ITEM = {"databaseId": "db1", "workflowId": "wflow1", "workflowName": "W"}
BASE = "/database/db1/workflows/wflow1/triggers"
PARAMS = {"databaseId": "db1", "workflowId": "wflow1"}
TPARAMS = {"databaseId": "db1", "workflowId": "wflow1", "triggerType": "fileUpload"}


@pytest.mark.unit
class TestWorkflowTriggerService:
    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_api_denied(self, mock_enforcer, mock_claims, mock_parent):
        mock_claims.return_value = {"tokens": ["t"]}
        mock_enforcer.return_value = _enforcer(api=False)
        resp = lambda_handler(_event("GET", BASE, PARAMS), MagicMock())
        assert resp["statusCode"] == 403

    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_parent_workflow_not_found(self, mock_enforcer, mock_claims, mock_parent):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (False, None)
        resp = lambda_handler(_event("GET", BASE, PARAMS), MagicMock())
        assert resp["statusCode"] == 404

    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_parent_object_denied(self, mock_enforcer, mock_claims, mock_parent):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (False, WF_ITEM)
        resp = lambda_handler(_event("GET", BASE, PARAMS), MagicMock())
        assert resp["statusCode"] == 403

    @patch(f"{MOD}._load_template_tag_schema_fields")
    @patch(f"{MOD}._triggers_table")
    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_set_trigger(self, mock_enforcer, mock_claims, mock_parent, mock_table, mock_load_schema):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, WF_ITEM)
        table = MagicMock()
        table.get_item.return_value = {}  # no existing trigger
        mock_table.return_value = table
        # The chosen default template has no tag schema (nothing to validate as headless-unsafe).
        mock_load_schema.return_value = None
        body = {"inputFileFilters": {"allow": ["*.glb"], "exclude": []},
                "defaultTemplateIds": {"db1:pipe1": "tmpl1"}}
        resp = lambda_handler(_event("PUT", BASE + "/fileUpload", TPARAMS, body), MagicMock())
        assert resp["statusCode"] == 200
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["triggerType"] == "fileUpload"
        assert saved["triggerConfig"]["defaultTemplateIds"] == {"db1:pipe1": "tmpl1"}
        assert saved["triggerConfig"]["inputFileFilters"]["allow"] == ["*.glb"]

    @patch(f"{MOD}._load_template_tag_schema_fields")
    @patch(f"{MOD}._triggers_table")
    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_set_trigger_rejects_default_template_with_required_no_default(
        self, mock_enforcer, mock_claims, mock_parent, mock_table, mock_load_schema):
        # A trigger runs headless; a chosen default template with a required tag lacking a default
        # can never render, so the save is rejected (400) rather than silently failing every run.
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, WF_ITEM)
        table = MagicMock()
        table.get_item.return_value = {}
        mock_table.return_value = table
        mock_load_schema.return_value = [
            {"tagKey": "quality", "type": "string", "required": True},  # required, no default
        ]
        body = {"inputFileFilters": {"allow": ["*.glb"], "exclude": []},
                "defaultTemplateIds": {"db1:pipe1": "tmpl1"}}
        resp = lambda_handler(_event("PUT", BASE + "/fileUpload", TPARAMS, body), MagicMock())
        assert resp["statusCode"] == 400
        errors = json.loads(resp["body"])["message"]["triggerTemplateErrors"]
        assert any("quality" in e for e in errors)
        table.put_item.assert_not_called()

    @patch(f"{MOD}._load_template_tag_schema_fields")
    @patch(f"{MOD}._triggers_table")
    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_set_trigger_allows_required_tag_with_default(
        self, mock_enforcer, mock_claims, mock_parent, mock_table, mock_load_schema):
        # A required tag WITH a default is fine headlessly — the default fills it.
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, WF_ITEM)
        table = MagicMock()
        table.get_item.return_value = {}
        mock_table.return_value = table
        mock_load_schema.return_value = [
            {"tagKey": "quality", "type": "string", "required": True, "default": "high"},
        ]
        body = {"inputFileFilters": {"allow": ["*.glb"], "exclude": []},
                "defaultTemplateIds": {"db1:pipe1": "tmpl1"}}
        resp = lambda_handler(_event("PUT", BASE + "/fileUpload", TPARAMS, body), MagicMock())
        assert resp["statusCode"] == 200

    @patch(f"{MOD}._triggers_table")
    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_set_trigger_preserves_datecreated_on_replace(self, mock_enforcer, mock_claims,
                                                          mock_parent, mock_table):
        # Re-setting an existing trigger keeps its original dateCreated (only dateModified advances).
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, WF_ITEM)
        table = MagicMock()
        table.get_item.return_value = {"Item": {"triggerType": "fileUpload",
                                               "dateCreated": "2020-01-01T00:00:00Z"}}
        mock_table.return_value = table
        body = {"inputFileFilters": {"allow": ["*.obj"], "exclude": []}, "defaultTemplateIds": {}}
        resp = lambda_handler(_event("PUT", BASE + "/fileUpload", TPARAMS, body), MagicMock())
        assert resp["statusCode"] == 200
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["dateCreated"] == "2020-01-01T00:00:00Z"  # original preserved

    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_unsupported_trigger_type_rejected(self, mock_enforcer, mock_claims, mock_parent):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, WF_ITEM)
        params = {"databaseId": "db1", "workflowId": "wflow1", "triggerType": "bogusTrigger"}
        resp = lambda_handler(_event("PUT", BASE + "/bogusTrigger", params, {}), MagicMock())
        assert resp["statusCode"] == 400
        assert "Unsupported trigger type" in json.loads(resp["body"])["message"]

    @patch(f"{MOD}._triggers_table")
    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_get_trigger_404(self, mock_enforcer, mock_claims, mock_parent, mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, WF_ITEM)
        table = MagicMock()
        table.get_item.return_value = {}  # no trigger
        mock_table.return_value = table
        resp = lambda_handler(_event("GET", BASE + "/fileUpload", TPARAMS), MagicMock())
        assert resp["statusCode"] == 404

    @patch(f"{MOD}._triggers_table")
    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_list_triggers(self, mock_enforcer, mock_claims, mock_parent, mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, WF_ITEM)
        table = MagicMock()
        table.query.return_value = {"Items": [
            {"workflowDatabaseId": "db1", "workflowId": "wflow1", "triggerType": "fileUpload",
             "triggerConfig": {}, "enabled": True}]}
        mock_table.return_value = table
        resp = lambda_handler(_event("GET", BASE, PARAMS), MagicMock())
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])["message"]
        assert data["Items"][0]["triggerType"] == "fileUpload"

    @patch(f"{MOD}._triggers_table")
    @patch(f"{MOD}._enforce_parent_workflow")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_delete_trigger(self, mock_enforcer, mock_claims, mock_parent, mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, WF_ITEM)
        table = MagicMock()
        table.get_item.return_value = {"Item": {"triggerType": "fileUpload"}}
        mock_table.return_value = table
        resp = lambda_handler(_event("DELETE", BASE + "/fileUpload", TPARAMS), MagicMock())
        assert resp["statusCode"] == 200
        table.delete_item.assert_called_once()


@pytest.mark.unit
class TestTriggerConfigBuilderDispatch:
    """triggerConfig is built by the builder registered for the trigger type, so a supported type
    with no builder fails the save rather than storing a fileUpload-shaped config under it."""

    def test_file_upload_type_has_a_builder(self):
        from backend.backend.handlers.workflows import workflowTriggerService as wts
        assert wts.TRIGGER_TYPE_FILE_UPLOAD in wts._TRIGGER_CONFIG_BUILDERS

    def test_type_without_a_builder_is_rejected(self):
        from backend.backend.handlers.workflows import workflowTriggerService as wts
        request = type("R", (), {"inputFileFilters": {}, "defaultTemplateIds": {},
                                 "enabled": True})()
        with patch(f"{MOD}.validate_trigger_default_templates", return_value=[]), \
             patch(f"{MOD}._triggers_table") as mock_table:
            resp = wts.set_trigger("db1", "wflow1", "schedule", request)
        assert resp["statusCode"] == 400
        mock_table.return_value.put_item.assert_not_called()


@pytest.mark.unit
class TestOffloadedTagSchemaRead:
    """An offloaded tag schema is read from the default bucket under the prefix that bucket is
    registered with. A bucket-root-relative read lands outside the area a customer scoped to VAMS, so
    it 403s under the normal cross-account bucket policy and the headless-template check silently
    stops running."""

    def _row(self):
        from backend.backend.common.workflows import templateBodyStorage as tbs
        return {"bodyStorage": tbs.BODY_STORAGE_S3,
                "fieldsS3Key": "pipelines/templates/pdb/pipe1/tpl/tagSchema.json"}

    def _load(self, base_prefix):
        from backend.backend.handlers.workflows import workflowTriggerService as wts
        schema_table = MagicMock()
        schema_table.query.return_value = {"Items": [self._row()]}
        with patch(f"{MOD}._tag_schema_table", return_value=schema_table), \
             patch(f"{MOD}.resolve_default_bucket",
                   return_value={"bucketId": "b1", "bucketName": "customer-bucket",
                                 "baseAssetsPrefix": base_prefix}), \
             patch(f"{MOD}.tbs.read_body_from_s3", return_value="[]") as m_read:
            assert wts._load_template_tag_schema_fields("pdb", "pipe1", "tpl") == []
        return m_read.call_args.args

    def test_prefixed_default_bucket_read_stays_inside_the_prefix(self):
        _client, bucket, key = self._load("vams/")
        assert bucket == "customer-bucket"
        assert key == "vams/pipelines/templates/pdb/pipe1/tpl/tagSchema.json"

    def test_root_prefix_default_bucket_read_is_unchanged(self):
        for root in ("/", ""):
            _client, _bucket, key = self._load(root)
            assert key == "pipelines/templates/pdb/pipe1/tpl/tagSchema.json"
