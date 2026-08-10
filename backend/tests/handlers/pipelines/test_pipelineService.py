# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the pipeline CRUD handler (pipelineService). REST v1 event shape;
CasbinEnforcer + request_to_claims + the DynamoDB table are patched."""

import json
import os
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


def _deadline_execution_config():
    """A complete DeadlineCloud executionConfig: the callback is mandatory and the farm + queue the
    job is submitted to must be named."""
    return {"executionType": "DeadlineCloud", "waitForCallback": "Enabled",
            "deadlineCloud": {"farmId": "farm-1", "queueId": "queue-1"}}


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
                    "executionConfig": _deadline_execution_config()}
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
                    "executionConfig": _deadline_execution_config()}
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

    @patch(f"{MOD}.find_pipeline_id_owner")
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_rejected_when_id_used_by_another_database(
            self, mock_enforcer, mock_claims, mock_table, mock_owner):
        """Pipeline ids are unique across every database: the execute request keys per-pipeline
        parameters by bare pipelineId, so a duplicate id in a second database is ambiguous."""
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {}          # free within this database
        mock_table.return_value = table
        mock_owner.return_value = "db-other"      # but taken by another database
        body = {"databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "P",
                "executionConfig": {"executionType": "Lambda"}}
        resp = lambda_handler(
            _event("POST", "/database/db1/pipelines", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 400
        table.put_item.assert_not_called()
        # The owning database is never disclosed to the caller.
        assert "db-other" not in resp["body"]

    @patch(f"{MOD}.find_pipeline_id_owner")
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_allowed_when_id_globally_free(
            self, mock_enforcer, mock_claims, mock_table, mock_owner):
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {}
        table.query.return_value = {"Items": []}
        mock_table.return_value = table
        mock_owner.return_value = None            # free everywhere
        # Reference an existing function so the create does not attempt Lambda provisioning (which
        # this environment cannot do) — the uniqueness check is what is under test.
        body = {"databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "P",
                "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"}}}
        resp = lambda_handler(
            _event("POST", "/database/db1/pipelines", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 200
        table.put_item.assert_called_once()
        mock_owner.assert_called_once()

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.get_pipeline_templates")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_get_single_pipeline_with_templates(self, mock_enforcer, mock_claims, mock_templates,
                                                mock_table, mock_templates_table):
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "db1", "pipelineId": "pipe1",
                                                 "pipelineName": "P", "enabled": True, "archived": False}}
        mock_table.return_value = table
        mock_templates.return_value = [{"templateId": "t1", "templateName": "tmpl"}]
        # templateCount comes from the COUNT query, not from the inline list.
        templates_table = MagicMock()
        templates_table.query.return_value = {"Count": 1}
        mock_templates_table.return_value = templates_table
        resp = lambda_handler(
            _event("GET", "/database/db1/pipelines/pipe1", {"databaseId": "db1", "pipelineId": "pipe1"}), MagicMock())
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])["message"]
        assert data["pipelineId"] == "pipe1"
        assert data["templates"][0]["templateId"] == "t1"
        assert data["templateCount"] == 1

    @patch(f"{MOD}._templates_table")
    @patch(f"{MOD}.dynamodb")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_list_pipelines_includes_template_count(
        self, mock_enforcer, mock_claims, mock_dynamodb, mock_templates_table
    ):
        from backend.backend.handlers.pipelines.pipelineService import lambda_handler
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()

        # Paginator returns one pipeline row.
        paginator = MagicMock()
        paginator.paginate.return_value.build_full_result.return_value = {
            "Items": [{"databaseId": "db1", "pipelineId": "pipe1", "enabled": True, "archived": False}]
        }
        mock_dynamodb.meta.client.get_paginator.return_value = paginator

        # Templates COUNT query returns 3.
        templates_table = MagicMock()
        templates_table.query.return_value = {"Count": 3}
        mock_templates_table.return_value = templates_table

        event = _event("GET", "/pipelines")
        event["queryStringParameters"] = {"maxItems": "100", "pageSize": "100", "startingToken": None}
        resp = lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        items = json.loads(resp["body"])["message"]["Items"]
        assert items[0]["pipelineId"] == "pipe1"
        assert items[0]["templateCount"] == 3
        # Global list queries the by-date GSI newest-first, not a table scan.
        mock_dynamodb.meta.client.get_paginator.assert_called_with("query")
        paginate_kwargs = paginator.paginate.call_args.kwargs
        assert paginate_kwargs["IndexName"] == "PipelinesByDateGSI"
        assert paginate_kwargs["ScanIndexForward"] is False

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


def _real_validate_pagination_info():
    """The genuine common.dynamodb.validate_pagination_info. The suite replaces common.dynamodb with
    a mock module, so the real helper is loaded from source to exercise the handler's actual
    default overrides."""
    import importlib.util
    source = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "common",
                          "dynamodb.py")
    spec = importlib.util.spec_from_file_location("_real_common_dynamodb", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The suite's mock safeLogger has no warn(); the helper logs a warning on a reset value.
    module.logger = MagicMock()
    return module.validate_pagination_info


@pytest.mark.unit
class TestListPageBound:
    """The list page stays bounded at 100 whatever the caller sends, so a single response never
    accumulates the whole table (and the per-row template COUNT fan-out stays bounded)."""

    def _list(self, mock_dynamodb, query):
        paginator = MagicMock()
        paginator.paginate.return_value.build_full_result.return_value = {"Items": []}
        mock_dynamodb.meta.client.get_paginator.return_value = paginator
        event = _event("GET", "/pipelines")
        event["queryStringParameters"] = query
        with patch(f"{MOD}.validate_pagination_info", _real_validate_pagination_info()):
            resp = lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        return paginator.paginate.call_args.kwargs["PaginationConfig"]

    @patch(f"{MOD}.dynamodb")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_default_page_bound(self, mock_enforcer, mock_claims, mock_dynamodb):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        config = self._list(mock_dynamodb, None)
        assert config["MaxItems"] == 100 and config["PageSize"] == 100

    @patch(f"{MOD}.dynamodb")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_non_numeric_page_size_keeps_the_bound(self, mock_enforcer, mock_claims, mock_dynamodb):
        # An unparseable pageSize must fall back to the handler's bound, not the utility's 3000.
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        config = self._list(mock_dynamodb, {"pageSize": "abc"})
        assert config["MaxItems"] == 100 and config["PageSize"] == 100


def _action_enforcer(allowed_actions):
    """Enforcer whose Tier-2 verdict depends on the action, so a role holding the shared execute/create
    POST action but no management PUT can be expressed."""
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.side_effect = lambda obj, action: action in allowed_actions
    return inst


@pytest.mark.unit
class TestGlobalScopeManagement:
    """A GLOBAL pipeline is shared by every database, so creating one requires pipeline management
    (PUT) permission on the GLOBAL scope in addition to the shared execute/create POST action."""

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_global_denied_without_management(self, mock_enforcer, mock_claims, mock_table):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _action_enforcer({"GET", "POST"})  # run-only role
        table = MagicMock()
        mock_table.return_value = table
        body = {"databaseId": "GLOBAL", "pipelineId": "glob1", "pipelineName": "P",
                "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"}}}
        resp = lambda_handler(
            _event("POST", "/database/GLOBAL/pipelines", {"databaseId": "GLOBAL"}, body), MagicMock())
        assert resp["statusCode"] == 403
        table.put_item.assert_not_called()

    @patch(f"{MOD}._provision_lambda_for_pipeline", side_effect=lambda cfg, pid: cfg)
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_global_allowed_with_management(self, mock_enforcer, mock_claims, mock_table,
                                                   mock_provision):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _action_enforcer({"GET", "POST", "PUT", "DELETE"})
        table = MagicMock()
        table.get_item.return_value = {}
        mock_table.return_value = table
        body = {"databaseId": "GLOBAL", "pipelineId": "glob1", "pipelineName": "P",
                "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"}}}
        resp = lambda_handler(
            _event("POST", "/database/GLOBAL/pipelines", {"databaseId": "GLOBAL"}, body), MagicMock())
        assert resp["statusCode"] == 200
        table.put_item.assert_called_once()

    @patch(f"{MOD}._provision_lambda_for_pipeline", side_effect=lambda cfg, pid: cfg)
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_database_scoped_needs_no_management(self, mock_enforcer, mock_claims, mock_table,
                                                        mock_provision):
        # The GLOBAL gate applies only to the GLOBAL database; a database-scoped create is unchanged.
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _action_enforcer({"GET", "POST"})
        table = MagicMock()
        table.get_item.return_value = {}
        mock_table.return_value = table
        body = {"databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "P",
                "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"}}}
        resp = lambda_handler(
            _event("POST", "/database/db1/pipelines", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 200


@pytest.mark.unit
class TestUnarchiveRegistration:
    """An archived pipeline can be restored: an update can clear `archived`, and a create over an
    archived row restores it in place (the path a re-registration of an archived built-in takes)."""

    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_update_can_clear_archived(self, mock_enforcer, mock_claims, mock_table, mock_wf_table):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "GLOBAL", "pipelineId": "pipe1",
                                                "enabled": False, "archived": True}}
        mock_table.return_value = table
        mock_wf_table.return_value = MagicMock(scan=MagicMock(return_value={"Items": []}))
        resp = lambda_handler(
            _event("PUT", "/database/GLOBAL/pipelines/pipe1",
                   {"databaseId": "GLOBAL", "pipelineId": "pipe1"},
                   {"enabled": True, "archived": False}), MagicMock())
        assert resp["statusCode"] == 200
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["archived"] is False and saved["enabled"] is True
        assert json.loads(resp["body"])["message"]["archived"] is False

    @patch(f"{MOD}._provision_lambda_for_pipeline", side_effect=lambda cfg, pid: cfg)
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_over_archived_row_restores_it(self, mock_enforcer, mock_claims, mock_table,
                                                  mock_provision):
        mock_claims.return_value = {"tokens": ["SYSTEM_USER"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": {
            "databaseId": "GLOBAL", "pipelineId": "pipe1", "archived": True, "enabled": False,
            "dateCreated": "2024-01-01T00:00:00Z", "createdBy": "SYSTEM_USER",
            "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn-existing"}},
        }}
        mock_table.return_value = table
        body = {"databaseId": "GLOBAL", "pipelineId": "pipe1", "pipelineName": "P",
                "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn-existing"}}}
        resp = lambda_handler(
            _event("POST", "/database/GLOBAL/pipelines", {"databaseId": "GLOBAL"}, body), MagicMock())
        assert resp["statusCode"] == 200
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["archived"] is False and saved["enabled"] is True
        assert saved["dateCreated"] == "2024-01-01T00:00:00Z"  # create provenance preserved

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_over_live_row_still_rejected(self, mock_enforcer, mock_claims, mock_table):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "db1", "pipelineId": "pipe1",
                                                "archived": False}}
        mock_table.return_value = table
        body = {"databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "P",
                "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"}}}
        resp = lambda_handler(
            _event("POST", "/database/db1/pipelines", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 400
        table.put_item.assert_not_called()

    @patch(f"{MOD}.lambda_client")
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_restore_reuses_prior_provisioned_lambda(self, mock_enforcer, mock_claims, mock_table,
                                                     mock_lambda):
        # Restoring an archived Lambda-type pipeline whose request carries no resourceId reuses the
        # function the prior row already had instead of provisioning a second one.
        import backend.backend.handlers.pipelines.pipelineService as ps
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": {
            "databaseId": "db1", "pipelineId": "pipe1", "archived": True,
            "executionConfig": {"executionType": "Lambda",
                                "lambda": {"resourceId": "vams-priorfn", "isProvided": False}},
        }}
        mock_table.return_value = table
        with patch.multiple(
            ps,
            lambda_role_to_attach="arn:aws:iam::1:role/r",
            lambda_pipeline_sample_function_bucket="artefacts",
            lambda_pipeline_sample_function_key="sample.zip",
            lambda_python_version="python3.12",
            subnet_ids=[], security_group_ids=[],
        ):
            body = {"databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "P",
                    "executionConfig": {"executionType": "Lambda"}}
            resp = lambda_handler(
                _event("POST", "/database/db1/pipelines", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 200
        mock_lambda.create_function.assert_not_called()
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["executionConfig"]["lambda"]["resourceId"] == "vams-priorfn"


@pytest.mark.unit
class TestExecutionConfigChangeWarning:
    """The pipeline execution target is baked into each referencing workflow's deployed state machine
    at workflow-save time, so an executionConfig change surfaces a non-blocking warning naming the
    workflows that must be saved again."""

    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_execution_config_change_warns_about_referencing_workflows(
            self, mock_enforcer, mock_claims, mock_table, mock_wf_table):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": {
            "databaseId": "db1", "pipelineId": "pipe1", "enabled": True,
            "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "vams-old"}},
        }}
        mock_table.return_value = table
        wf_table = MagicMock()
        wf_table.query.return_value = {"Items": [{
            "databaseId": "db1", "workflowId": "wf1",
            "specifiedPipelines": [{"pipelineDatabaseId:pipelineId": "db1:pipe1"}],
        }]}
        mock_wf_table.return_value = wf_table
        resp = lambda_handler(
            _event("PUT", "/database/db1/pipelines/pipe1",
                   {"databaseId": "db1", "pipelineId": "pipe1"},
                   {"executionConfig": {"executionType": "Lambda",
                                        "lambda": {"resourceId": "vams-new"}}}), MagicMock())
        assert resp["statusCode"] == 200
        warnings = json.loads(resp["body"])["warnings"]
        assert any("db1:wf1" in w for w in warnings)
        # The referencing-workflow lookup queries the by-date GSI; it never scans the table.
        wf_table.scan.assert_not_called()
        assert wf_table.query.call_args.kwargs["IndexName"] == "WorkflowsByDateGSI"

    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_unchanged_execution_config_does_not_warn(self, mock_enforcer, mock_claims, mock_table,
                                                      mock_wf_table):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        config = {"executionType": "Lambda", "lambda": {"resourceId": "vams-old"}}
        table = MagicMock()
        table.get_item.return_value = {"Item": {
            "databaseId": "db1", "pipelineId": "pipe1", "enabled": True, "executionConfig": config,
        }}
        mock_table.return_value = table
        wf_table = MagicMock()
        wf_table.query.return_value = {"Items": [{
            "databaseId": "db1", "workflowId": "wf1",
            "specifiedPipelines": [{"pipelineDatabaseId:pipelineId": "db1:pipe1"}],
        }]}
        mock_wf_table.return_value = wf_table
        resp = lambda_handler(
            _event("PUT", "/database/db1/pipelines/pipe1",
                   {"databaseId": "db1", "pipelineId": "pipe1"},
                   {"executionConfig": dict(config)}), MagicMock())
        assert resp["statusCode"] == 200
        assert "warnings" not in json.loads(resp["body"])


@pytest.mark.unit
@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
class TestExistenceOracle:
    """A missing pipeline is authorized before the 404 is returned, so an unauthorized caller cannot
    use the 404-vs-403 difference as an existence oracle."""

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_missing_pipeline_denied_returns_403(self, mock_enforcer, mock_claims, mock_table, method):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer(api=True, obj=False)  # Tier-2 deny
        table = MagicMock()
        table.get_item.return_value = {}  # pipeline does not exist
        mock_table.return_value = table
        body = {"enabled": False} if method == "PUT" else None
        resp = lambda_handler(
            _event(method, "/database/db1/pipelines/pipe1",
                   {"databaseId": "db1", "pipelineId": "pipe1"}, body), MagicMock())
        assert resp["statusCode"] == 403

    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_missing_pipeline_authorized_returns_404(self, mock_enforcer, mock_claims, mock_table,
                                                     mock_wf_table, method):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {}
        mock_table.return_value = table
        mock_wf_table.return_value = MagicMock(scan=MagicMock(return_value={"Items": []}))
        body = {"enabled": False} if method == "PUT" else None
        resp = lambda_handler(
            _event(method, "/database/db1/pipelines/pipe1",
                   {"databaseId": "db1", "pipelineId": "pipe1"}, body), MagicMock())
        assert resp["statusCode"] == 404


@pytest.mark.unit
class TestRequestBodyShape:
    """A syntactically valid but non-object JSON body is a client error, not an internal one."""

    @pytest.mark.parametrize("raw_body", ["[1, 2]", '"x"', "null", "5"])
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_non_object_body_rejected(self, mock_enforcer, mock_claims, mock_table, raw_body):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        mock_table.return_value = MagicMock()
        event = _event("POST", "/database/db1/pipelines", {"databaseId": "db1"})
        event["body"] = raw_body
        resp = lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 400

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_method_not_allowed_is_400(self, mock_enforcer, mock_claims, mock_table):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        mock_table.return_value = MagicMock()
        resp = lambda_handler(_event("PATCH", "/pipelines"), MagicMock())
        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["message"] == "Method not allowed"


@pytest.mark.unit
class TestCreateBodyDatabaseIdMatchesPath:
    """The pipeline is created under the path-scoped database, so a body databaseId naming a
    different one is rejected instead of silently ignored."""

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_mismatched_body_database_rejected(self, mock_enforcer, mock_claims, mock_table):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        mock_table.return_value = table
        body = {"databaseId": "db2", "pipelineName": "P",
                "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"}}}
        resp = lambda_handler(
            _event("POST", "/database/db1/pipelines", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 400
        # The rejected id is not echoed back to the caller.
        assert "db2" not in json.loads(resp["body"])["message"]
        table.put_item.assert_not_called()
