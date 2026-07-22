# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the pipeline CRUD handler (pipelineService). REST v1 event shape;
CasbinEnforcer + request_to_claims + the DynamoDB table are patched."""

import json
from unittest.mock import MagicMock, patch

import pytest

# Import at top-level so the real handler module loads before patch() resolves its targets
# (mirrors the gold-standard test_createPipeline import style).
from backend.backend.handlers.pipelines.pipelineService import lambda_handler

MOD = "backend.backend.handlers.pipelines.pipelineService"


def _event(method, path, path_params=None, body=None, query=None):
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": path_params,
        "queryStringParameters": query,
        "headers": {"authorization": "Bearer test-token"},
        "body": json.dumps(body) if body is not None else None,
    }


def _enforcer(api=True, obj=True):
    inst = MagicMock()
    inst.enforceAPI.return_value = api
    inst.enforce.return_value = obj
    return inst


@pytest.mark.unit
class TestPipelineServiceV2:
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_api_denied(self, mock_enforcer, mock_claims):
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["t"]}
        mock_enforcer.return_value = _enforcer(api=False)
        resp = lambda_handler(_event("GET", "/pipelines"), MagicMock())
        assert resp["statusCode"] == 403

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_deadline_cloud_rejected_when_type_disabled(
            self, mock_enforcer, mock_claims, mock_table):
        # A DeadlineCloud pipeline cannot be created when the deployment has the type disabled — its
        # workflow createJob task state + job-callback lambda are not deployed.
        import backend.backend.handlers.pipelines.pipelineService as ps
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {}
        mock_table.return_value = table
        with patch.object(ps, "DEADLINE_CLOUD_EXECUTION_TYPE_ENABLED", False):
            body = {"databaseId": "db1", "pipelineName": "P", "pipelineId": "dc-pipe",
                    "executionConfig": {"executionType": "DeadlineCloud"}}
            resp = lambda_handler(
                _event("POST", "/database/db1/pipelines", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 400
        assert "DeadlineCloud" in json.loads(resp["body"])["message"]
        table.put_item.assert_not_called()

    @patch(f"{MOD}._provision_lambda_for_pipeline", side_effect=lambda cfg, pid: cfg)
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_deadline_cloud_allowed_when_type_enabled(
            self, mock_enforcer, mock_claims, mock_table, mock_provision):
        import backend.backend.handlers.pipelines.pipelineService as ps
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {}
        mock_table.return_value = table
        with patch.object(ps, "DEADLINE_CLOUD_EXECUTION_TYPE_ENABLED", True):
            body = {"databaseId": "db1", "pipelineName": "P", "pipelineId": "dc-pipe",
                    "executionConfig": {"executionType": "DeadlineCloud"}}
            resp = lambda_handler(
                _event("POST", "/database/db1/pipelines", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 200
        table.put_item.assert_called_once()

    def test_casbin_object_surfaces_execution_type_constraint_field(self):
        # The Tier-2 Casbin object must expose the flat pipelineExecutionType ABAC field (derived from
        # executionConfig.executionType) so execution-type constraints (e.g. DENY on SQS) apply; the V2
        # record stores the type only structurally under executionConfig.
        from backend.backend.handlers.pipelines import pipelineService as ps
        item = {
            "databaseId": "db1", "pipelineId": "p1", "pipelineName": "P",
            "executionConfig": {"executionType": "SQS"},
        }
        obj = ps._casbin_object(item)
        assert obj["object__type"] == "pipeline"
        assert obj["name"] == "P"
        assert obj["pipelineExecutionType"] == "SQS"

    @patch(f"{MOD}._provision_lambda_for_pipeline", side_effect=lambda cfg, pid: cfg)
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_pipeline_success(self, mock_enforcer, mock_claims, mock_table, mock_provision):
        # A pipeline that already carries a Lambda resourceId does not auto-provision; the provisioner
        # is patched to a pass-through so the create-path assertions stay focused on record shape.
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {}  # no existing pipeline
        mock_table.return_value = table
        body = {"databaseId": "db1", "pipelineName": "My Pipe", "category": "conversion",
                "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn-existing"}}}
        resp = lambda_handler(
            _event("POST", "/database/db1/pipelines", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 200
        table.put_item.assert_called_once()
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["databaseId"] == "db1" and saved["pipelineName"] == "My Pipe"
        assert saved["enabled"] is True and saved["archived"] is False
        assert len(saved["pipelineId"]) == 32  # generated GUID

    @patch(f"{MOD}.lambda_client")
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_lambda_pipeline_auto_provisions_when_no_resource(
            self, mock_enforcer, mock_claims, mock_table, mock_lambda):
        # A Lambda-type pipeline with no referenced function auto-creates one (seeded from the sample
        # package) when the deploy-time provisioning env is present, and stores the generated name.
        import backend.backend.handlers.pipelines.pipelineService as ps
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {}
        mock_table.return_value = table
        with patch.multiple(
            ps,
            lambda_role_to_attach="arn:aws:iam::1:role/r",
            lambda_pipeline_sample_function_bucket="artefacts",
            lambda_pipeline_sample_function_key="sample.zip",
            lambda_python_version="python3.12",
            subnet_ids=[], security_group_ids=[],
        ):
            body = {"databaseId": "db1", "pipelineName": "P", "pipelineId": "auto-lam",
                    "executionConfig": {"executionType": "Lambda"}}
            resp = lambda_handler(
                _event("POST", "/database/db1/pipelines", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 200
        mock_lambda.create_function.assert_called_once()
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["executionConfig"]["lambda"]["resourceId"].startswith("vams-")
        assert saved["executionConfig"]["lambda"]["isProvided"] is False

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_lambda_pipeline_errors_when_provisioning_env_absent(
            self, mock_enforcer, mock_claims, mock_table):
        # Without the deploy-time provisioning env, a Lambda-type create that would need a new function
        # fails cleanly (400) rather than persisting a pipeline pointing at a non-existent function.
        import backend.backend.handlers.pipelines.pipelineService as ps
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {}
        mock_table.return_value = table
        with patch.multiple(
            ps,
            lambda_role_to_attach=None,
            lambda_pipeline_sample_function_bucket=None,
            lambda_pipeline_sample_function_key=None,
            lambda_python_version=None,
        ):
            body = {"databaseId": "db1", "pipelineName": "P", "pipelineId": "auto-lam",
                    "executionConfig": {"executionType": "Lambda"}}
            resp = lambda_handler(
                _event("POST", "/database/db1/pipelines", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 400
        table.put_item.assert_not_called()

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_pipeline_object_denied(self, mock_enforcer, mock_claims, mock_table):
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer(api=True, obj=False)  # Tier-2 deny
        table = MagicMock()
        table.get_item.return_value = {}
        mock_table.return_value = table
        body = {"databaseId": "db1", "pipelineName": "P", "executionConfig": {"executionType": "Lambda"}}
        resp = lambda_handler(
            _event("POST", "/database/db1/pipelines", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 403
        table.put_item.assert_not_called()

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_denied_does_not_probe_existence(self, mock_enforcer, mock_claims, mock_table):
        # Tier-2 denial must happen BEFORE the duplicate-existence probe, so a denied caller cannot
        # use the "already exists" response as an existence oracle.
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer(api=True, obj=False)  # Tier-2 deny
        table = MagicMock()
        mock_table.return_value = table
        body = {"databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "P",
                "executionConfig": {"executionType": "Lambda"}}
        resp = lambda_handler(
            _event("POST", "/database/db1/pipelines", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 403
        table.get_item.assert_not_called()  # no existence probe before auth

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_duplicate_rejected(self, mock_enforcer, mock_claims, mock_table):
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "db1", "pipelineId": "pipe1"}}
        mock_table.return_value = table
        body = {"databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "P",
                "executionConfig": {"executionType": "Lambda"}}
        resp = lambda_handler(
            _event("POST", "/database/db1/pipelines", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 400

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.get_pipeline_templates")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_get_single_pipeline_with_templates(self, mock_enforcer, mock_claims, mock_templates, mock_table):
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "db1", "pipelineId": "pipe1",
                                                 "pipelineName": "P", "enabled": True, "archived": False}}
        mock_table.return_value = table
        mock_templates.return_value = [{"templateId": "t1", "templateName": "tmpl"}]
        resp = lambda_handler(
            _event("GET", "/database/db1/pipelines/pipe1", {"databaseId": "db1", "pipelineId": "pipe1"}), MagicMock())
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])["message"]
        assert data["pipelineId"] == "pipe1"
        assert data["templates"][0]["templateId"] == "t1"

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_get_archived_hidden_by_default(self, mock_enforcer, mock_claims, mock_table):
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "db1", "pipelineId": "pipe1", "archived": True}}
        mock_table.return_value = table
        resp = lambda_handler(
            _event("GET", "/database/db1/pipelines/pipe1", {"databaseId": "db1", "pipelineId": "pipe1"}), MagicMock())
        assert resp["statusCode"] == 404

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_archive_pipeline(self, mock_enforcer, mock_claims, mock_table):
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "db1", "pipelineId": "pipe1", "enabled": True}}
        mock_table.return_value = table
        resp = lambda_handler(
            _event("DELETE", "/database/db1/pipelines/pipe1", {"databaseId": "db1", "pipelineId": "pipe1"}), MagicMock())
        assert resp["statusCode"] == 200
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["archived"] is True and saved["enabled"] is False

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_update_enable_disable(self, mock_enforcer, mock_claims, mock_table):
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "db1", "pipelineId": "pipe1", "enabled": True}}
        mock_table.return_value = table
        resp = lambda_handler(
            _event("PUT", "/database/db1/pipelines/pipe1", {"databaseId": "db1", "pipelineId": "pipe1"},
                   {"enabled": False}), MagicMock())
        assert resp["statusCode"] == 200
        assert table.put_item.call_args.kwargs["Item"]["enabled"] is False

    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_invalid_execution_type_rejected(self, mock_enforcer, mock_claims):
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        body = {"databaseId": "db1", "pipelineName": "P", "executionConfig": {"executionType": "Bogus"}}
        resp = lambda_handler(
            _event("POST", "/database/db1/pipelines", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 400
