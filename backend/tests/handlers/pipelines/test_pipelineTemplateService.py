# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the pipeline template + tag-schema handler (pipelineTemplateService).
Parent-pipeline Tier-2 auth, template hybrid inline storage, and tag-schema validation are
exercised; CasbinEnforcer/request_to_claims/tables/default-bucket are patched."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.pipelines.pipelineTemplateService import lambda_handler

MOD = "backend.backend.handlers.pipelines.pipelineTemplateService"


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


PIPELINE_ITEM = {"databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "P"}
BASE_PATH = "/database/db1/pipelines/pipe1/templates"
BASE_PARAMS = {"databaseId": "db1", "pipelineId": "pipe1"}


@pytest.mark.unit
class TestTemplateService:
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_api_denied(self, mock_enforcer, mock_claims, mock_parent):
        mock_claims.return_value = {"tokens": ["t"]}
        mock_enforcer.return_value = _enforcer(api=False)
        resp = lambda_handler(_event("GET", BASE_PATH, BASE_PARAMS), MagicMock())
        assert resp["statusCode"] == 403

    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_parent_pipeline_not_found(self, mock_enforcer, mock_claims, mock_parent):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (False, None)  # pipeline missing
        resp = lambda_handler(_event("GET", BASE_PATH, BASE_PARAMS), MagicMock())
        assert resp["statusCode"] == 404

    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_parent_object_denied(self, mock_enforcer, mock_claims, mock_parent):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (False, PIPELINE_ITEM)  # Tier-2 deny on parent
        resp = lambda_handler(_event("GET", BASE_PATH, BASE_PARAMS), MagicMock())
        assert resp["statusCode"] == 403

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_template_inline(self, mock_enforcer, mock_claims, mock_parent,
                                    mock_bucket, mock_get_row, mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_get_row.return_value = None  # no existing template
        mock_bucket.return_value = "default-bucket"
        table = MagicMock()
        mock_table.return_value = table
        body = {"templateName": "conv-glb-obj", "configFormat": "yaml",
                "configBody": "from: glb\nto: obj\nprompt: {{firstAssetFileKey}}"}
        resp = lambda_handler(_event("POST", BASE_PATH, BASE_PARAMS, body), MagicMock())
        assert resp["statusCode"] == 200
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["bodyStorage"] == "inline"
        assert saved["configBody"].startswith("from: glb")
        assert saved["configFormat"] == "yaml"
        assert len(saved["templateId"]) == 32

    @patch(f"{MOD}._tag_schema_table")
    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_template_with_tag_schema(self, mock_enforcer, mock_claims, mock_parent,
                                             mock_bucket, mock_get_row, mock_tmpl_table, mock_tag_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_get_row.return_value = None
        mock_bucket.return_value = "default-bucket"
        mock_tmpl_table.return_value = MagicMock()
        tag_table = MagicMock()
        tag_table.query.return_value = {"Items": []}  # no existing tag-schema row
        mock_tag_table.return_value = tag_table
        body = {"templateName": "t", "configFormat": "yaml", "configBody": "x: {{prompt}}",
                "tagSchema": [{"tagKey": "prompt", "type": "string", "required": True}]}
        resp = lambda_handler(_event("POST", BASE_PATH, BASE_PARAMS, body), MagicMock())
        assert resp["statusCode"] == 200
        # tag schema row persisted
        tag_table.put_item.assert_called_once()

    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_template_reserved_tag_rejected(self, mock_enforcer, mock_claims,
                                                   mock_parent, mock_bucket):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_bucket.return_value = "b"
        tmpl_table = MagicMock()
        with patch(f"{MOD}._get_template_row", return_value=None), \
                patch(f"{MOD}._templates_table", return_value=tmpl_table), \
                patch(f"{MOD}._tag_schema_table", return_value=MagicMock()):
            body = {"templateName": "t", "configFormat": "yaml", "configBody": "x",
                    "tagSchema": [{"tagKey": "executionId", "type": "string"}]}  # reserved
            resp = lambda_handler(_event("POST", BASE_PATH, BASE_PARAMS, body), MagicMock())
        assert resp["statusCode"] == 400
        assert "tagSchemaErrors" in json.loads(resp["body"])["message"]
        # Atomicity: an invalid tag schema is caught BEFORE the template row is persisted.
        tmpl_table.put_item.assert_not_called()

    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._tag_schema_table")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_set_tag_schema(self, mock_enforcer, mock_claims, mock_parent, mock_bucket,
                            mock_tag_table, mock_get_row):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_bucket.return_value = "b"
        mock_get_row.return_value = {"templateId": "tmpl1"}  # template exists
        tag_table = MagicMock()
        tag_table.query.return_value = {"Items": []}  # no existing tag-schema row
        mock_tag_table.return_value = tag_table
        path = BASE_PATH + "/tmpl1/tagSchema"
        params = {"databaseId": "db1", "pipelineId": "pipe1", "templateId": "tmpl1"}
        body = {"fields": [{"tagKey": "prompt", "type": "string", "required": True},
                           {"tagKey": "count", "type": "integer", "default": 3}]}
        resp = lambda_handler(_event("PUT", path, params, body), MagicMock())
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])["message"]
        assert data["fields"][0]["tagKey"] == "prompt"
        tag_table.put_item.assert_called_once()

    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._tag_schema_table")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_set_tag_schema_idempotent_reuses_id(self, mock_enforcer, mock_claims, mock_parent,
                                                 mock_bucket, mock_tag_table, mock_get_row):
        # Re-setting a schema must OVERWRITE the existing owner row (reuse its tagSchemaId), not
        # append a duplicate, and must delete any stray duplicates.
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_bucket.return_value = "b"
        mock_get_row.return_value = {"templateId": "tmpl1"}
        owner = "db1:pipe1:tmpl1"
        tag_table = MagicMock()
        tag_table.query.return_value = {"Items": [
            {"tagSchemaId": "existing-id", "pipelineDatabaseId:pipelineId:templateId": owner},
            {"tagSchemaId": "dup-id", "pipelineDatabaseId:pipelineId:templateId": owner},
        ]}
        mock_tag_table.return_value = tag_table
        path = BASE_PATH + "/tmpl1/tagSchema"
        params = {"databaseId": "db1", "pipelineId": "pipe1", "templateId": "tmpl1"}
        body = {"fields": [{"tagKey": "newtag", "type": "string"}]}
        resp = lambda_handler(_event("PUT", path, params, body), MagicMock())
        assert resp["statusCode"] == 200
        # put_item reuses the existing tagSchemaId (overwrite), and the duplicate is deleted.
        saved = tag_table.put_item.call_args.kwargs["Item"]
        assert saved["tagSchemaId"] == "existing-id"
        tag_table.delete_item.assert_called_once()

    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_tag_schema_missing_template_404(self, mock_enforcer, mock_claims, mock_parent, mock_get_row):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_get_row.return_value = None  # template does not exist
        path = BASE_PATH + "/tmpl1/tagSchema"
        params = {"databaseId": "db1", "pipelineId": "pipe1", "templateId": "tmpl1"}
        resp = lambda_handler(_event("GET", path, params), MagicMock())
        assert resp["statusCode"] == 404

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_delete_template(self, mock_enforcer, mock_claims, mock_parent, mock_bucket,
                             mock_get_row, mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_get_row.return_value = {"pipelineDatabaseId:pipelineId": "db1:pipe1", "templateId": "tmpl1"}
        mock_bucket.return_value = "b"
        table = MagicMock()
        table.query.return_value = {"Items": []}
        mock_table.return_value = table
        with patch(f"{MOD}._tag_schema_table", return_value=MagicMock(query=MagicMock(return_value={"Items": []}))):
            path = BASE_PATH + "/tmpl1"
            params = {"databaseId": "db1", "pipelineId": "pipe1", "templateId": "tmpl1"}
            resp = lambda_handler(_event("DELETE", path, params), MagicMock())
        assert resp["statusCode"] == 200
        table.delete_item.assert_called_once()


@pytest.mark.unit
class TestDefaultTemplate:
    """isDefault stored on create/update; setting a new default clears any prior default so at most
    one template per pipeline is the default."""

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_default_stores_and_clears_others(self, mock_enforcer, mock_claims, mock_parent,
                                                      mock_bucket, mock_get_row, mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_get_row.return_value = None
        mock_bucket.return_value = "b"
        table = MagicMock()
        # One OTHER template is currently the default; it must be unset.
        table.query.return_value = {
            "Items": [
                {"templateId": "new", "isDefault": True},
                {"templateId": "old", "isDefault": True},
            ]
        }
        mock_table.return_value = table
        body = {"templateName": "t", "configFormat": "yaml", "configBody": "x: 1",
                "isDefault": True}
        resp = lambda_handler(_event("POST", BASE_PATH, BASE_PARAMS, body), MagicMock())
        assert resp["statusCode"] == 200
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["isDefault"] is True
        # The prior default ("old") was cleared, and the just-created template was skipped.
        cleared_ids = [
            c.kwargs["Key"]["templateId"] for c in table.update_item.call_args_list
        ]
        assert "old" in cleared_ids
        assert saved["templateId"] not in cleared_ids

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_non_default_does_not_clear(self, mock_enforcer, mock_claims, mock_parent,
                                               mock_bucket, mock_get_row, mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_get_row.return_value = None
        mock_bucket.return_value = "b"
        table = MagicMock()
        mock_table.return_value = table
        body = {"templateName": "t", "configFormat": "yaml", "configBody": "x: 1"}
        resp = lambda_handler(_event("POST", BASE_PATH, BASE_PARAMS, body), MagicMock())
        assert resp["statusCode"] == 200
        assert table.put_item.call_args.kwargs["Item"]["isDefault"] is False
        table.update_item.assert_not_called()


@pytest.mark.unit
class TestUpdateConfigBodyJsonValidation:
    """A partial update that changes only configBody is JSON-validated against the template's stored
    configFormat (the request omits configFormat, so the handler resolves the effective format)."""

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_invalid_json_body_rejected_against_stored_format(self, mock_enforcer, mock_claims,
                                                              mock_parent, mock_bucket, mock_get_row,
                                                              mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        # Stored template is json-format; the update omits configFormat.
        mock_get_row.return_value = {"pipelineDatabaseId:pipelineId": "db1:pipe1", "templateId": "tmpl1",
                                     "configFormat": "json", "bodyStorage": "inline"}
        mock_bucket.return_value = "b"
        mock_table.return_value = MagicMock()
        path = BASE_PATH + "/tmpl1"
        params = {"databaseId": "db1", "pipelineId": "pipe1", "templateId": "tmpl1"}
        resp = lambda_handler(_event("PUT", path, params, {"configBody": "not json"}), MagicMock())
        assert resp["statusCode"] == 400
        assert "JSON" in json.loads(resp["body"])["message"]

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_valid_json_body_accepted(self, mock_enforcer, mock_claims, mock_parent, mock_bucket,
                                      mock_get_row, mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_get_row.return_value = {"pipelineDatabaseId:pipelineId": "db1:pipe1", "templateId": "tmpl1",
                                     "configFormat": "json", "bodyStorage": "inline"}
        mock_bucket.return_value = "b"
        mock_table.return_value = MagicMock()
        path = BASE_PATH + "/tmpl1"
        params = {"databaseId": "db1", "pipelineId": "pipe1", "templateId": "tmpl1"}
        resp = lambda_handler(_event("PUT", path, params, {"configBody": '{"a": 1}'}), MagicMock())
        assert resp["statusCode"] == 200
