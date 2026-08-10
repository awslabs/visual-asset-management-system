# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Integrity of the pipeline UPDATE path, and the bound on its save-path workflow lookup.

Three properties the update path has to hold, each of which a passing 200 would otherwise hide:

  - EXECUTION TARGET. executionConfig is stored wholesale, so a partial edit that names no
    `lambda.resourceId` would drop the function the pipeline runs and leave the deployed state
    machines invoking an empty FunctionName. The stored function carries over, and a switch INTO
    Lambda that no deployment can provision is refused rather than saved pointing at nothing.
  - AUTHORIZATION SCOPE. `category` and `pipelineName` are ABAC constraint fields, so the
    pre-mutation check authorizes only the scope the pipeline is LEAVING. The mutated object is
    re-checked, so a scoped role cannot move a pipeline beyond its own policy.
  - LOOKUP BOUND. The referencing-workflow lookup behind the advisory save warnings queries the
    constant-partition by-date GSI with the reference fields projected, capped in pages and labels,
    rather than paging the whole workflow table on every save.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.pipelines import pipelineService as ps
from backend.backend.handlers.pipelines.pipelineService import lambda_handler

MOD = "backend.backend.handlers.pipelines.pipelineService"

PATH = "/database/db1/pipelines/pipe1"
PARAMS = {"databaseId": "db1", "pipelineId": "pipe1"}


def _event(method, body=None, path=PATH, path_params=None):
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": PARAMS if path_params is None else path_params,
        "queryStringParameters": None,
        "headers": {"authorization": "Bearer test-token"},
        "body": json.dumps(body) if body is not None else None,
    }


def _enforcer():
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = True
    return inst


def _field_enforcer(field, allowed_values):
    """Enforcer whose Tier-2 verdict depends on a CONSTRAINT FIELD of the object under test, which is
    how an ABAC rule such as `category equals conversion` behaves."""
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.side_effect = lambda obj, action: obj.get(field) in allowed_values
    return inst


STORED = {
    "databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "Converter",
    "category": "conversion", "enabled": True,
    "executionConfig": {"executionType": "Lambda", "waitForCallback": "Disabled",
                        "lambda": {"resourceId": "vams-existing-fn", "isProvided": True}},
}


def _pipeline_table_mock(stored=None):
    table = MagicMock()
    table.get_item.return_value = {"Item": dict(stored if stored is not None else STORED)}
    return table


def _empty_workflow_table():
    return MagicMock(query=MagicMock(return_value={"Items": []}))


@pytest.mark.unit
class TestUpdateKeepsExecutionTarget:
    """A Lambda-type executionConfig that names no function keeps the one the pipeline already runs."""

    @patch(f"{MOD}._workflow_table", side_effect=lambda: _empty_workflow_table())
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_partial_lambda_config_carries_over_the_stored_function(
            self, mock_enforcer, mock_claims, mock_table, mock_wf_table):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = _pipeline_table_mock()
        mock_table.return_value = table
        # A realistic partial edit: the callback flag is changed and the lambda block is omitted.
        resp = lambda_handler(_event("PUT", {"executionConfig": {
            "executionType": "Lambda", "waitForCallback": "Enabled"}}), MagicMock())
        assert resp["statusCode"] == 200
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["executionConfig"]["lambda"]["resourceId"] == "vams-existing-fn"
        assert saved["executionConfig"]["waitForCallback"] == "Enabled"
        # The response reports what was stored, so a caller reading it back sees the live target.
        assert (json.loads(resp["body"])["message"]["executionConfig"]["lambda"]["resourceId"]
                == "vams-existing-fn")

    @patch(f"{MOD}._workflow_table", side_effect=lambda: _empty_workflow_table())
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_explicit_resource_id_still_repoints_the_pipeline(
            self, mock_enforcer, mock_claims, mock_table, mock_wf_table):
        # The carry-over must not defeat a deliberate change of target.
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = _pipeline_table_mock()
        mock_table.return_value = table
        resp = lambda_handler(_event("PUT", {"executionConfig": {
            "executionType": "Lambda", "lambda": {"resourceId": "vams-new-fn"}}}), MagicMock())
        assert resp["statusCode"] == 200
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["executionConfig"]["lambda"]["resourceId"] == "vams-new-fn"
        # Repointing the compute is exactly what the stale-deployment warning exists to report.
        assert json.loads(resp["body"])["message"]["executionConfig"]["executionType"] == "Lambda"

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_switch_into_lambda_with_no_target_is_refused(self, mock_enforcer, mock_claims,
                                                          mock_table):
        # There is no prior Lambda to carry over from an SQS pipeline, and this deployment cannot
        # auto-create one, so the row must not be saved pointing at an empty FunctionName.
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        stored = dict(STORED)
        stored["executionConfig"] = {"executionType": "SQS",
                                     "sqs": {"queueUrl": "https://sqs.us-east-1.amazonaws.com/123456789012/my-queue"}}
        table = _pipeline_table_mock(stored)
        mock_table.return_value = table
        with patch.object(ps, "lambda_role_to_attach", None):
            resp = lambda_handler(
                _event("PUT", {"executionConfig": {"executionType": "Lambda"}}), MagicMock())
        assert resp["statusCode"] == 400
        assert "resourceId" in json.loads(resp["body"])["message"]
        table.put_item.assert_not_called()

    @patch(f"{MOD}._workflow_table", side_effect=lambda: _empty_workflow_table())
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_switch_into_lambda_provisions_a_function(self, mock_enforcer, mock_claims, mock_table,
                                                      mock_wf_table):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        stored = dict(STORED)
        stored["executionConfig"] = {"executionType": "SQS",
                                     "sqs": {"queueUrl": "https://sqs.us-east-1.amazonaws.com/123456789012/my-queue"}}
        table = _pipeline_table_mock(stored)
        mock_table.return_value = table
        mock_lambda = MagicMock()
        with patch.object(ps, "lambda_role_to_attach", "arn:aws:iam::1:role/r"), \
                patch.object(ps, "lambda_pipeline_sample_function_bucket", "artefacts"), \
                patch.object(ps, "lambda_pipeline_sample_function_key", "sample.zip"), \
                patch.object(ps, "lambda_python_version", "python3.12"), \
                patch.object(ps, "lambda_client", mock_lambda):
            resp = lambda_handler(
                _event("PUT", {"executionConfig": {"executionType": "Lambda"}}), MagicMock())
        assert resp["statusCode"] == 200
        mock_lambda.create_function.assert_called_once()
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["executionConfig"]["lambda"]["resourceId"].startswith("vams-")
        assert saved["executionConfig"]["lambda"]["isProvided"] is False


@pytest.mark.unit
class TestUpdateReEnforcesOnTheMutatedObject:
    """category / pipelineName are ABAC constraint fields, so the MUTATED object is authorized too."""

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_moving_a_pipeline_out_of_the_caller_category_scope_is_denied(
            self, mock_enforcer, mock_claims, mock_table):
        mock_claims.return_value = {"tokens": ["user1"]}
        # A role scoped to `category equals conversion`: the stored row passes, the mutated one does not.
        mock_enforcer.return_value = _field_enforcer("category", {"conversion"})
        table = _pipeline_table_mock()
        mock_table.return_value = table
        resp = lambda_handler(_event("PUT", {"category": "admin-only",
                                             "pipelineName": "Privileged Converter"}), MagicMock())
        assert resp["statusCode"] == 403
        table.put_item.assert_not_called()

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_renaming_out_of_a_name_scope_is_denied(self, mock_enforcer, mock_claims, mock_table):
        # `name` (from pipelineName) is the other live pipeline constraint field.
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _field_enforcer("name", {"Converter"})
        table = _pipeline_table_mock()
        mock_table.return_value = table
        resp = lambda_handler(_event("PUT", {"pipelineName": "Privileged Converter"}), MagicMock())
        assert resp["statusCode"] == 403
        table.put_item.assert_not_called()

    @patch(f"{MOD}._workflow_table", side_effect=lambda: _empty_workflow_table())
    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_a_move_within_the_caller_scope_still_succeeds(self, mock_enforcer, mock_claims,
                                                           mock_table, mock_wf_table):
        # The re-check must not block an edit that keeps the pipeline inside the permitted scope.
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _field_enforcer("category", {"conversion", "conversion-2d"})
        table = _pipeline_table_mock()
        mock_table.return_value = table
        resp = lambda_handler(_event("PUT", {"category": "conversion-2d"}), MagicMock())
        assert resp["statusCode"] == 200
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["category"] == "conversion-2d"
        assert saved["databaseId:category"] == "db1:conversion-2d"

    @patch(f"{MOD}._pipeline_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_moving_a_pipeline_out_of_an_execution_type_scope_is_denied(
            self, mock_enforcer, mock_claims, mock_table):
        # pipelineExecutionType is derived from executionConfig, so an executionConfig edit moves the
        # pipeline through that scope as well.
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _field_enforcer("pipelineExecutionType", {"Lambda"})
        table = _pipeline_table_mock()
        mock_table.return_value = table
        resp = lambda_handler(_event("PUT", {"executionConfig": {
            "executionType": "SQS",
            "sqs": {"queueUrl": "https://sqs.us-east-1.amazonaws.com/123456789012/my-queue"}}}), MagicMock())
        assert resp["statusCode"] == 403
        table.put_item.assert_not_called()


@pytest.mark.unit
class TestReferencingWorkflowLookupIsBounded:
    """The save-path lookup behind the advisory warnings is a bounded GSI query, not a table scan."""

    def test_lookup_queries_the_gsi_with_only_the_reference_fields(self):
        wf_table = MagicMock()
        wf_table.query.return_value = {"Items": [{
            "databaseId": "db1", "workflowId": "wf1",
            "specifiedPipelines": [{"pipelineDatabaseId:pipelineId": "db1:pipe1"}],
        }]}
        with patch(f"{MOD}._workflow_table", return_value=wf_table):
            labels = ps._referencing_workflow_labels("db1", "pipe1")
        assert labels == ["db1:wf1"]
        wf_table.scan.assert_not_called()
        kwargs = wf_table.query.call_args.kwargs
        assert kwargs["IndexName"] == "WorkflowsByDateGSI"
        assert kwargs["Limit"] == ps.REFERENCING_WORKFLOW_PAGE_SIZE
        # Only the three attributes the match needs are read off the index.
        assert kwargs["ProjectionExpression"] == "databaseId, workflowId, specifiedPipelines"

    def test_page_count_is_capped(self):
        # A table that always reports more pages must not be paged forever on a save.
        wf_table = MagicMock()
        wf_table.query.return_value = {
            "Items": [{"databaseId": "db1", "workflowId": "other", "specifiedPipelines": []}],
            "LastEvaluatedKey": {"allListPartition": "workflow", "dateModified": "x"},
        }
        with patch(f"{MOD}._workflow_table", return_value=wf_table):
            labels = ps._referencing_workflow_labels("db1", "pipe1")
        assert labels == []
        assert wf_table.query.call_count == ps.MAX_REFERENCING_WORKFLOW_PAGES

    def test_label_count_is_capped_and_stops_paging(self):
        # Every row matches, so the label cap is what has to stop the walk.
        matching = [{"databaseId": "db1", "workflowId": f"wf{i}",
                     "specifiedPipelines": [{"pipelineDatabaseId:pipelineId": "db1:pipe1"}]}
                    for i in range(ps.MAX_REFERENCING_WORKFLOWS + 50)]
        wf_table = MagicMock()
        wf_table.query.return_value = {
            "Items": matching,
            "LastEvaluatedKey": {"allListPartition": "workflow", "dateModified": "x"},
        }
        with patch(f"{MOD}._workflow_table", return_value=wf_table):
            labels = ps._referencing_workflow_labels("db1", "pipe1")
        assert len(labels) == ps.MAX_REFERENCING_WORKFLOWS
        wf_table.query.assert_called_once()

    def test_a_read_error_degrades_to_no_warning(self):
        # The warning is advisory, so a lookup failure must not fail the save.
        wf_table = MagicMock()
        wf_table.query.side_effect = Exception("throttled")
        with patch(f"{MOD}._workflow_table", return_value=wf_table):
            assert ps._referencing_workflow_labels("db1", "pipe1") == []
