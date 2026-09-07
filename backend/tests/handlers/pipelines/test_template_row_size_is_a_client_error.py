# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A template row too large to store answers 400, not 500.

The template row carries the inline configBody alongside `overrides`, `inputInstructions`,
`description` and the provenance fields, and the update path sets each of those independently — so a
row can be grown past DynamoDB's 400 KB per-item limit one request at a time even though every
individual field is within its own declared bound. Two guards make that a client error:

  - `templateBodyStorage.assert_row_within_item_limit` measures the assembled row before the write,
    so the request never reaches put_item.
  - the handler's `ClientError` arm maps a DynamoDB item-size ValidationException to a 400, so a row
    the estimate above under-counts is still not reported as a server fault. Every OTHER
    ValidationException (a malformed key, a bad expression) stays a 500 — that is the negative
    control here, because an arm that answered 400 for all of them would hide real defects.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.common.workflows import templateBodyStorage as tbs

MOD = "backend.backend.handlers.pipelines.pipelineTemplateService"

from backend.backend.handlers.pipelines.pipelineTemplateService import lambda_handler  # noqa: E402

PIPELINE_ITEM = {"databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "P"}
DEFAULT_BUCKET = {"bucketId": "b-id", "bucketName": "b", "baseAssetsPrefix": ""}
BASE_PATH = "/database/db1/pipelines/pipe1/templates"
TEMPLATE_PATH = f"{BASE_PATH}/tmpl1"
TEMPLATE_PARAMS = {"databaseId": "db1", "pipelineId": "pipe1", "templateId": "tmpl1"}


def _event(method, path, path_params, body=None):
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": path_params,
        "queryStringParameters": None,
        "headers": {"authorization": "Bearer test-token"},
        "body": json.dumps(body) if body is not None else None,
    }


def _enforcer():
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = True
    return inst


def _client_error(message):
    return ClientError(
        {"Error": {"Code": "ValidationException", "Message": message}}, "PutItem")


# A stored row from before the inline threshold was lowered: the body is on the item.
def _legacy_row(body_bytes):
    return {
        "pipelineDatabaseId:pipelineId": "db1:pipe1",
        "templateId": "tmpl1",
        "pipelineDatabaseId": "db1", "pipelineId": "pipe1",
        "templateName": "T", "description": "", "configFormat": "yaml",
        "allowCustomEdit": False, "inputInstructions": "",
        "bodyStorage": "inline", "configBody": "x" * body_bytes, "webFormJson": "",
        "configBodyS3Key": "", "configBodyHash": "0" * 64,
        "webFormS3Key": "", "webFormHash": "0" * 64,
        "overrides": {}, "isDefault": False,
        "dateCreated": "2026-01-01T00:00:00Z", "dateModified": "2026-01-01T00:00:00Z",
        "createdBy": "u", "modifiedBy": "u", "schemaVersion": 1,
    }


# An overrides block just under the model's own serialized-size bound.
_BIG_OVERRIDES = {"inputFileFilters": {"allow": ["a" * 512] * 120}}


@pytest.mark.unit
class TestAnOversizedTemplateRowIsAClientError:
    @patch(f"{MOD}._triggers_table")
    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_an_update_that_grows_the_row_past_the_item_limit_is_rejected_before_the_write(
            self, mock_enforcer, mock_claims, mock_parent, mock_bucket, mock_get_row,
            mock_table, mock_triggers):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_bucket.return_value = DEFAULT_BUCKET
        mock_get_row.return_value = _legacy_row(320 * 1024)
        table = MagicMock()
        mock_table.return_value = table

        # Each field is inside its own declared bound; together with the stored body they are not.
        body = {"overrides": _BIG_OVERRIDES,
                "inputInstructions": "\U0001f600" * 4096,
                "description": "\U0001f600" * 1024}
        resp = lambda_handler(_event("PUT", TEMPLATE_PATH, TEMPLATE_PARAMS, body), MagicMock())

        assert resp["statusCode"] == 400, resp
        assert table.put_item.call_count == 0, "the oversized row must not reach put_item"

    @patch(f"{MOD}._triggers_table")
    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_a_modest_update_of_the_same_row_still_succeeds(
            self, mock_enforcer, mock_claims, mock_parent, mock_bucket, mock_get_row,
            mock_table, mock_triggers):
        """POSITIVE CONTROL: the guard fires on the size, not on the update path itself."""
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_bucket.return_value = DEFAULT_BUCKET
        mock_get_row.return_value = _legacy_row(320 * 1024)
        table = MagicMock()
        mock_table.return_value = table

        resp = lambda_handler(
            _event("PUT", TEMPLATE_PATH, TEMPLATE_PARAMS, {"templateName": "renamed"}), MagicMock())

        assert resp["statusCode"] == 200, resp
        assert table.put_item.call_count == 1

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_a_dynamodb_item_size_rejection_answers_400(
            self, mock_enforcer, mock_claims, mock_parent, mock_bucket, mock_get_row, mock_table):
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_bucket.return_value = DEFAULT_BUCKET
        mock_get_row.return_value = None
        table = MagicMock()
        table.put_item.side_effect = _client_error(
            "Item size has exceeded the maximum allowed size")
        mock_table.return_value = table

        resp = lambda_handler(
            _event("POST", BASE_PATH, {"databaseId": "db1", "pipelineId": "pipe1"},
                   {"templateName": "T", "configFormat": "yaml", "configBody": "a: 1"}),
            MagicMock())

        assert resp["statusCode"] == 400, resp
        assert "too large" in json.loads(resp["body"])["message"].lower()

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._get_template_row")
    @patch(f"{MOD}._default_bucket")
    @patch(f"{MOD}._enforce_parent_pipeline")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_any_other_validation_exception_is_still_a_server_fault(
            self, mock_enforcer, mock_claims, mock_parent, mock_bucket, mock_get_row, mock_table):
        """NEGATIVE CONTROL: ValidationException covers many conditions; only the size one is the
        caller's fault, so the arm must not swallow the rest into a 400."""
        mock_claims.return_value = {"tokens": ["u"]}
        mock_enforcer.return_value = _enforcer()
        mock_parent.return_value = (True, PIPELINE_ITEM)
        mock_bucket.return_value = DEFAULT_BUCKET
        mock_get_row.return_value = None
        table = MagicMock()
        table.put_item.side_effect = _client_error(
            "One or more parameter values were invalid: An AttributeValue may not contain an empty "
            "string")
        mock_table.return_value = table

        resp = lambda_handler(
            _event("POST", BASE_PATH, {"databaseId": "db1", "pipelineId": "pipe1"},
                   {"templateName": "T", "configFormat": "yaml", "configBody": "a: 1"}),
            MagicMock())

        assert resp["statusCode"] == 500, resp

    def test_the_measured_limit_leaves_the_service_ceiling_intact(self):
        """The estimate is deliberately conservative — it must sit under DynamoDB's own limit, or the
        pre-write guard would pass rows the service then refuses."""
        assert tbs.MAX_ITEM_BYTES - tbs.ITEM_SIZE_RESERVE_BYTES < tbs.MAX_ITEM_BYTES
        assert tbs.item_bytes({"a": "x" * 100}) >= 101
