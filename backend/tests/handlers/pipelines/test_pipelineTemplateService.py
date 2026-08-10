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


@pytest.mark.unit
class TestShrinkToInlineCleanupOrdering:
    """A shrink-to-inline update deletes the prior offloaded S3 objects only AFTER the row is
    rewritten, so a failed write leaves a stored row whose S3 keys still resolve."""

    S3_ROW = {"pipelineDatabaseId:pipelineId": "db1:pipe1", "templateId": "tmpl1",
              "configFormat": "json", "bodyStorage": "s3",
              "configBodyS3Key": "pipelines/db1/pipe1/tmpl1/configBody",
              "webFormS3Key": "pipelines/db1/pipe1/tmpl1/webForm"}
    PATH = BASE_PATH + "/tmpl1"
    PARAMS = {"databaseId": "db1", "pipelineId": "pipe1", "templateId": "tmpl1"}

    def _patches(self):
        return (patch(f"{MOD}._get_template_row", return_value=dict(self.S3_ROW)),
                patch(f"{MOD}._default_bucket_name", return_value="b"),
                patch(f"{MOD}._rehydrate_template",
                      return_value={"configBody": '{"big": 1}', "webFormJson": ""}))

    @patch(f"{MOD}.s3_client")
    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_objects_deleted_after_successful_write(self, mock_enforcer, mock_claims, mock_parent,
                                                    mock_table, mock_s3):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        table = MagicMock()
        mock_table.return_value = table
        row_patch, bucket_patch, rehydrate_patch = self._patches()
        with row_patch, bucket_patch, rehydrate_patch:
            resp = lambda_handler(
                _event("PUT", self.PATH, self.PARAMS, {"configBody": '{"a": 1}'}), MagicMock())
        assert resp["statusCode"] == 200
        table.put_item.assert_called_once()
        deleted = {c.kwargs["Key"] for c in mock_s3.delete_object.call_args_list}
        assert deleted == {self.S3_ROW["configBodyS3Key"], self.S3_ROW["webFormS3Key"]}

    @patch(f"{MOD}.s3_client")
    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_objects_preserved_when_write_fails(self, mock_enforcer, mock_claims, mock_parent,
                                                mock_table, mock_s3):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        table = MagicMock()
        table.put_item.side_effect = RuntimeError("throttled")
        mock_table.return_value = table
        row_patch, bucket_patch, rehydrate_patch = self._patches()
        with row_patch, bucket_patch, rehydrate_patch:
            resp = lambda_handler(
                _event("PUT", self.PATH, self.PARAMS, {"configBody": '{"a": 1}'}), MagicMock())
        assert resp["statusCode"] == 500
        # The stored row still points at these keys, so they must survive the failed write.
        mock_s3.delete_object.assert_not_called()


@pytest.mark.unit
class TestListTemplatesPagination:
    """The templates list returns one bounded page plus a NextToken rather than accumulating every
    template (an inline body can be up to 320KB, so an unbounded list can exceed the 6MB limit)."""

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_single_page_with_next_token(self, mock_enforcer, mock_claims, mock_parent, mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        table = MagicMock()
        table.query.return_value = {
            "Items": [{"templateId": "t1", "pipelineDatabaseId": "db1", "pipelineId": "pipe1"}],
            "LastEvaluatedKey": {"pipelineDatabaseId:pipelineId": "db1:pipe1", "templateId": "t1"},
        }
        mock_table.return_value = table
        resp = lambda_handler(_event("GET", BASE_PATH, BASE_PARAMS), MagicMock())
        assert resp["statusCode"] == 200
        # One DynamoDB query only: the handler does NOT drain every page into one response.
        table.query.assert_called_once()
        assert table.query.call_args.kwargs["Limit"] == 10
        data = json.loads(resp["body"])["message"]
        assert [i["templateId"] for i in data["Items"]] == ["t1"]
        assert data["NextToken"]

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_starting_token_resumes_the_query(self, mock_enforcer, mock_claims, mock_parent,
                                              mock_table):
        import base64
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        table = MagicMock()
        table.query.return_value = {"Items": []}
        mock_table.return_value = table
        last_key = {"pipelineDatabaseId:pipelineId": "db1:pipe1", "templateId": "t1"}
        token = base64.b64encode(json.dumps(last_key).encode("utf-8")).decode("utf-8")
        event = _event("GET", BASE_PATH, BASE_PARAMS)
        event["queryStringParameters"] = {"startingToken": token, "pageSize": "5"}
        resp = lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        kwargs = table.query.call_args.kwargs
        assert kwargs["ExclusiveStartKey"] == last_key
        assert kwargs["Limit"] == 5
        # The final page carries no NextToken, so a draining client stops.
        assert json.loads(resp["body"])["message"]["NextToken"] is None

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_page_size_clamped_to_maximum(self, mock_enforcer, mock_claims, mock_parent, mock_table):
        # A caller cannot widen the page past the ceiling that keeps a worst-case page under 6MB.
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        table = MagicMock()
        table.query.return_value = {"Items": []}
        mock_table.return_value = table
        event = _event("GET", BASE_PATH, BASE_PARAMS)
        event["queryStringParameters"] = {"pageSize": "5000"}
        resp = lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        assert table.query.call_args.kwargs["Limit"] == 10

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_non_numeric_page_size_falls_back_to_default(self, mock_enforcer, mock_claims,
                                                         mock_parent, mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        table = MagicMock()
        table.query.return_value = {"Items": []}
        mock_table.return_value = table
        event = _event("GET", BASE_PATH, BASE_PARAMS)
        event["queryStringParameters"] = {"pageSize": "abc"}
        resp = lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        assert table.query.call_args.kwargs["Limit"] == 10


def _action_enforcer(allowed_actions):
    """Enforcer whose Tier-2 verdict depends on the action, so a role holding the shared execute/create
    POST action but no management PUT can be expressed."""
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.side_effect = lambda obj, action: action in allowed_actions
    return inst


@pytest.mark.unit
class TestParentPipelineConstraintFields:
    """The parent-pipeline Tier-2 object carries the flat pipelineExecutionType ABAC field (derived
    from executionConfig) so execution-type constraints apply to template operations too."""

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_execution_type_surfaced_to_enforce(self, mock_enforcer, mock_claims, mock_table):
        from backend.backend.handlers.pipelines import pipelineTemplateService as pts
        claims = {"tokens": ["u"]}
        mock_claims.return_value = claims
        enforcer = _enforcer()
        mock_enforcer.return_value = enforcer
        table = MagicMock()
        table.get_item.return_value = {"Item": {
            "databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "P",
            "executionConfig": {"executionType": "DeadlineCloud"},
        }}
        mock_table.return_value = table
        pts._enforce_parent_pipeline("db1", "pipe1", "GET", claims)
        obj = enforcer.enforce.call_args.args[0]
        assert obj["object__type"] == "pipeline"
        assert obj["name"] == "P"
        assert obj["pipelineExecutionType"] == "DeadlineCloud"

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_execution_type_deny_blocks_template_read(self, mock_enforcer, mock_claims, mock_table):
        # A deny keyed on pipelineExecutionType must fire for the template routes, not just the
        # pipeline routes.
        mock_claims.return_value = {"tokens": ["u"]}
        inst = MagicMock()
        inst.enforceAPI.return_value = True
        inst.enforce.side_effect = lambda obj, action: obj.get(
            "pipelineExecutionType") != "DeadlineCloud"
        mock_enforcer.return_value = inst
        table = MagicMock()
        table.get_item.return_value = {"Item": {
            "databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "P",
            "executionConfig": {"executionType": "DeadlineCloud"},
        }}
        mock_table.return_value = table
        resp = lambda_handler(_event("GET", BASE_PATH, BASE_PARAMS), MagicMock())
        assert resp["statusCode"] == 403


@pytest.mark.unit
class TestGlobalScopeManagement:
    """A GLOBAL pipeline's templates drive its behavior in every database, so reconfiguring them
    requires pipeline management (PUT) permission on the GLOBAL scope."""

    GLOBAL_PATH = "/database/GLOBAL/pipelines/pipe1/templates"
    GLOBAL_PARAMS = {"databaseId": "GLOBAL", "pipelineId": "pipe1"}
    GLOBAL_PIPELINE = {"databaseId": "GLOBAL", "pipelineId": "pipe1", "pipelineName": "P"}

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_global_template_denied_without_management(
            self, mock_enforcer, mock_claims, mock_pipeline_table, mock_bucket, mock_get_row,
            mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _action_enforcer({"GET", "POST"})  # run-only role
        mock_pipeline_table.return_value = MagicMock(
            get_item=MagicMock(return_value={"Item": self.GLOBAL_PIPELINE}))
        mock_bucket.return_value = "b"
        mock_get_row.return_value = None
        table = MagicMock()
        mock_table.return_value = table
        body = {"templateName": "t", "configFormat": "json", "configBody": "{}", "isDefault": True}
        resp = lambda_handler(_event("POST", self.GLOBAL_PATH, self.GLOBAL_PARAMS, body), MagicMock())
        assert resp["statusCode"] == 403
        table.put_item.assert_not_called()

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_global_template_allowed_with_management(
            self, mock_enforcer, mock_claims, mock_pipeline_table, mock_bucket, mock_get_row,
            mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _action_enforcer({"GET", "POST", "PUT", "DELETE"})
        mock_pipeline_table.return_value = MagicMock(
            get_item=MagicMock(return_value={"Item": self.GLOBAL_PIPELINE}))
        mock_bucket.return_value = "b"
        mock_get_row.return_value = None
        table = MagicMock()
        table.query.return_value = {"Items": []}
        mock_table.return_value = table
        body = {"templateName": "t", "configFormat": "json", "configBody": "{}"}
        resp = lambda_handler(_event("POST", self.GLOBAL_PATH, self.GLOBAL_PARAMS, body), MagicMock())
        assert resp["statusCode"] == 200
        table.put_item.assert_called_once()

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_read_global_template_allowed_without_management(
            self, mock_enforcer, mock_claims, mock_pipeline_table, mock_table):
        # Reading a GLOBAL pipeline's templates stays available to a run-only role.
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _action_enforcer({"GET", "POST"})
        mock_pipeline_table.return_value = MagicMock(
            get_item=MagicMock(return_value={"Item": self.GLOBAL_PIPELINE}))
        table = MagicMock()
        table.query.return_value = {"Items": []}
        mock_table.return_value = table
        resp = lambda_handler(_event("GET", self.GLOBAL_PATH, self.GLOBAL_PARAMS), MagicMock())
        assert resp["statusCode"] == 200


@pytest.mark.unit
class TestExistenceOracle:
    """A missing parent pipeline is authorized before the 404 is returned, so an unauthorized caller
    cannot use the 404-vs-403 difference as an existence oracle."""

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_missing_parent_denied_returns_403(self, mock_enforcer, mock_claims, mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer(api=True, obj=False)  # Tier-2 deny
        mock_table.return_value = MagicMock(get_item=MagicMock(return_value={}))
        resp = lambda_handler(_event("GET", BASE_PATH, BASE_PARAMS), MagicMock())
        assert resp["statusCode"] == 403

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_missing_parent_authorized_returns_404(self, mock_enforcer, mock_claims, mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_table.return_value = MagicMock(get_item=MagicMock(return_value={}))
        resp = lambda_handler(_event("GET", BASE_PATH, BASE_PARAMS), MagicMock())
        assert resp["statusCode"] == 404


@pytest.mark.unit
class TestTagSchemaRouteMatching:
    """The tag-schema sub-route is dispatched via the master ApiRoute constant, so a template whose
    templateId is literally 'tagSchema' reaches the template routes."""

    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._tag_schema_table")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_template_named_tag_schema_is_a_template(self, mock_enforcer, mock_claims, mock_parent,
                                                     mock_tag_table, mock_bucket, mock_get_row):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_bucket.return_value = "b"
        mock_tag_table.return_value = MagicMock(query=MagicMock(return_value={"Items": []}))
        mock_get_row.return_value = {"pipelineDatabaseId:pipelineId": "db1:pipe1",
                                     "templateId": "tagSchema", "configFormat": "json",
                                     "bodyStorage": "inline", "configBody": "{}"}
        path = BASE_PATH + "/tagSchema"
        params = {"databaseId": "db1", "pipelineId": "pipe1", "templateId": "tagSchema"}
        resp = lambda_handler(_event("GET", path, params), MagicMock())
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["message"]["templateId"] == "tagSchema"


@pytest.mark.unit
class TestTagSchemaShrinkToInlineCleanup:
    """A tag schema that shrinks back below the inline threshold deletes its now-unreferenced S3
    object, and only after the row is rewritten."""

    @patch(f"{MOD}.s3_client")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._tag_schema_table")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_prior_offloaded_object_deleted(self, mock_enforcer, mock_claims, mock_parent,
                                            mock_bucket, mock_tag_table, mock_get_row, mock_s3):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_bucket.return_value = "b"
        mock_get_row.return_value = {"templateId": "tmpl1"}
        owner = "db1:pipe1:tmpl1"
        prior_key = "pipelines/templates/db1/pipe1/tmpl1/tagSchema.json"
        tag_table = MagicMock()
        tag_table.query.return_value = {"Items": [{
            "tagSchemaId": "existing-id", "pipelineDatabaseId:pipelineId:templateId": owner,
            "bodyStorage": "s3", "fieldsS3Key": prior_key,
        }]}
        mock_tag_table.return_value = tag_table
        path = BASE_PATH + "/tmpl1/tagSchema"
        params = {"databaseId": "db1", "pipelineId": "pipe1", "templateId": "tmpl1"}
        body = {"fields": [{"tagKey": "prompt", "type": "string"}]}
        resp = lambda_handler(_event("PUT", path, params, body), MagicMock())
        assert resp["statusCode"] == 200
        saved = tag_table.put_item.call_args.kwargs["Item"]
        assert saved["bodyStorage"] == "inline" and saved["fieldsS3Key"] == ""
        mock_s3.delete_object.assert_called_once_with(Bucket="b", Key=prior_key)


@pytest.mark.unit
class TestFormatOnlyUpdateRevalidation:
    """Changing only configFormat revalidates the stored body against the new format, so a
    non-JSON body cannot end up declared as a json-format template."""

    ROW = {"pipelineDatabaseId:pipelineId": "db1:pipe1", "templateId": "tmpl1",
           "configFormat": "yaml", "bodyStorage": "inline", "configBody": "x: 1"}
    PATH = BASE_PATH + "/tmpl1"
    PARAMS = {"databaseId": "db1", "pipelineId": "pipe1", "templateId": "tmpl1"}

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_format_switch_rejects_non_json_stored_body(self, mock_enforcer, mock_claims, mock_parent,
                                                        mock_bucket, mock_get_row, mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_bucket.return_value = "b"
        mock_get_row.return_value = dict(self.ROW)
        table = MagicMock()
        mock_table.return_value = table
        resp = lambda_handler(_event("PUT", self.PATH, self.PARAMS, {"configFormat": "json"}),
                              MagicMock())
        assert resp["statusCode"] == 400
        assert "JSON" in json.loads(resp["body"])["message"]
        table.put_item.assert_not_called()

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_format_switch_accepts_json_stored_body(self, mock_enforcer, mock_claims, mock_parent,
                                                    mock_bucket, mock_get_row, mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_bucket.return_value = "b"
        row = dict(self.ROW)
        row["configBody"] = '{"a": 1}'
        mock_get_row.return_value = row
        mock_table.return_value = MagicMock()
        resp = lambda_handler(_event("PUT", self.PATH, self.PARAMS, {"configFormat": "json"}),
                              MagicMock())
        assert resp["statusCode"] == 200

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket_name")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_unchanged_format_does_not_revalidate(self, mock_enforcer, mock_claims, mock_parent,
                                                  mock_bucket, mock_get_row, mock_table):
        # Restating the stored (non-json) format alongside an unrelated field change is not a format
        # change, so the stored body is left alone.
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_bucket.return_value = "b"
        mock_get_row.return_value = dict(self.ROW)
        mock_table.return_value = MagicMock()
        resp = lambda_handler(
            _event("PUT", self.PATH, self.PARAMS, {"configFormat": "yaml", "description": "d"}),
            MagicMock())
        assert resp["statusCode"] == 200


@pytest.mark.unit
class TestTemplateMethodNotAllowed:
    """An unsupported verb is a request problem, not a permissions failure."""

    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_unsupported_verb_is_400(self, mock_enforcer, mock_claims, mock_parent):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        resp = lambda_handler(_event("PATCH", BASE_PATH, BASE_PARAMS), MagicMock())
        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["message"] == "Method not allowed"

    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_unsupported_verb_on_tag_schema_is_400(self, mock_enforcer, mock_claims, mock_parent,
                                                   mock_get_row):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_get_row.return_value = {"templateId": "tmpl1"}
        path = BASE_PATH + "/tmpl1/tagSchema"
        params = {"databaseId": "db1", "pipelineId": "pipe1", "templateId": "tmpl1"}
        resp = lambda_handler(_event("DELETE", path, params), MagicMock())
        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["message"] == "Method not allowed"


@pytest.mark.unit
class TestTagSchemaRetypeParityAcrossBothRoutes:
    """Two routes change a template's tag schema; both must apply the same body cross-check.

    The schema and the stored configBody are one contract: a tag's declared type decides whether its
    placeholder renders into valid JSON. `{"steps": {{PARAM}}}` is valid with an integer-typed PARAM
    and invalid with a string-typed one, because the substituted value lands in an unquoted slot.

    The tag-schema PUT validated this (`_set_tag_schema_on_template`), but the template PUT gated its
    check on `configBody is not None or format_changed` — so a tagSchema-ONLY update skipped it and
    accepted a change the other route rejected. Live, the template PUT returned 0 while the
    tag-schema PUT returned 1 for the identical payload.
    """

    UNQUOTED_BODY = '{"steps": {{PARAM}}}'
    SCHEMA_STRING = [{"tagKey": "PARAM", "type": "string", "required": False, "default": "abc"}]
    SCHEMA_INTEGER = [{"tagKey": "PARAM", "type": "integer", "required": False, "default": 10}]

    def _stored_row(self):
        return {
            "pipelineDatabaseId": "db1", "pipelineId": "pipe1", "templateId": "tmpl-1",
            "templateName": "T", "configFormat": "json", "configBody": self.UNQUOTED_BODY,
        }

    def _run(self, path, method, body, mocks):
        mock_enforcer, mock_claims, mock_parent, mock_get_row, mock_rehydrate = mocks
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_get_row.return_value = self._stored_row()
        mock_rehydrate.return_value = {"configBody": self.UNQUOTED_BODY}
        params = {**BASE_PARAMS, "templateId": "tmpl-1"}
        return lambda_handler(_event(method, path, params, body), MagicMock())

    @patch(f"{MOD}._rehydrate_template")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_the_template_put_rejects_a_retype_that_breaks_the_stored_body(
            self, mock_enforcer, mock_claims, mock_parent, mock_get_row, mock_rehydrate):
        resp = self._run(f"{BASE_PATH}/tmpl-1", "PUT", {"tagSchema": self.SCHEMA_STRING},
                         (mock_enforcer, mock_claims, mock_parent, mock_get_row, mock_rehydrate))
        assert resp["statusCode"] == 400, (
            f"a tagSchema-only retype that invalidates the stored body must be refused: {resp}")

    @patch(f"{MOD}._rehydrate_template")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_a_retype_that_keeps_the_body_valid_is_still_accepted(
            self, mock_enforcer, mock_claims, mock_parent, mock_get_row, mock_rehydrate):
        # The positive control: the check above must not be rejecting every tagSchema-only update.
        resp = self._run(f"{BASE_PATH}/tmpl-1", "PUT", {"tagSchema": self.SCHEMA_INTEGER},
                         (mock_enforcer, mock_claims, mock_parent, mock_get_row, mock_rehydrate))
        assert resp["statusCode"] != 400, (
            f"an integer-typed PARAM keeps '{self.UNQUOTED_BODY}' valid, so it must be allowed: {resp}")
