# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceEvaluationStore: one evaluation end to end against mock tables -- synchronous rules,
the pipeline-rule launch through the execute-workflow Lambda (input files resolved from the asset's
S3 listing against the workflow / pipeline / template config, request body per
ExecuteWorkflowRequestV2Model, `manual` trigger, SYSTEM_USER cross-call), the records it writes,
the binding it leaves alone, the quarantine-exception semantics, and the completion path the
workflow callback drives."""

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, DB, REAL_TO_UPDATE_EXPR, RULES_SCHEMA_BODY, SCHEMA, SYNC_RULES_SCHEMA_BODY, USER,
    put_items, schema_row, update_values,
)
from handlers.compliance import complianceEvaluationStore as store
from models.compliance import PipelineRule
from models.executions import ExecuteWorkflowRequestV2Model

STORE = "handlers.compliance.complianceEvaluationStore"
NOTIFICATIONS = "handlers.compliance.complianceNotifications"

PIPELINE_RULE = PipelineRule(**RULES_SCHEMA_BODY["rules"]["residual-bound"])

# The asset's S3 location as the store resolves it: the bucket row named by the asset's bucketId
# and the asset's assetLocation key.
BUCKET_ID = "11111111-2222-3333-4444-555555555555"
BUCKET_NAME = "asset-bucket"
ASSET_PREFIX = f"{ASSET}/"
DEFAULT_ASSET_FILES = ("/model.stl",)


def _rule(**overrides):
    """PIPELINE_RULE with fields replaced."""
    return PipelineRule(**dict(RULES_SCHEMA_BODY["rules"]["residual-bound"], **overrides))


def _inputs(*keys):
    return [{"databaseId": DB, "assetId": ASSET, "relativeFileKey": key} for key in keys]


def _lambda_client(status_code=200, execution_id="exec-1", body=None):
    client = MagicMock(name="lambda_client")
    payload = MagicMock()
    response_body = body if body is not None else {"message": {"executionId": execution_id}}
    payload.read.return_value = json.dumps(
        {"statusCode": status_code, "body": json.dumps(response_body)}).encode()
    client.invoke.return_value = {"Payload": payload}
    return client


def _s3_client(*pages):
    """An S3 client whose list_objects_v2 paginator serves `pages`, each a list of asset-relative
    keys under ASSET_PREFIX (a key ending in '/' is a folder marker)."""
    client = MagicMock(name="s3_client")
    client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": ASSET_PREFIX + key.lstrip("/"), "Size": 1} for key in page]}
        for page in pages
    ]
    return client


def conditional_check_failure():
    """The exception DynamoDB raises for a write whose ConditionExpression does not hold."""
    return store.dynamodb.meta.client.exceptions.ConditionalCheckFailedException(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "UpdateItem")


class FakeEvaluationTable:
    """An in-memory stand-in for the evaluation table that HONORS the store's status conditions.

    `put_item`, `get_item` and `update_item` read and write `rows` (keyed by evaluationId); an
    `update_item` whose ConditionExpression does not hold raises ConditionalCheckFailedException, the
    way DynamoDB does. `query` stays scripted. The table is a MagicMock, so the harness helpers
    (`put_items`, `update_values`) still read its call history."""

    def __init__(self, *rows):
        self.rows = {row["evaluationId"]: dict(row) for row in rows}
        self.mock = MagicMock(name="evaluation_table")
        self.mock.put_item.side_effect = self._put_item
        self.mock.get_item.side_effect = self._get_item
        self.mock.update_item.side_effect = self._update_item
        self.mock.query.return_value = {"Items": []}

    def seed(self, *rows):
        for row in rows:
            self.rows[row["evaluationId"]] = dict(row)
        return self

    def _put_item(self, Item, **_):
        self.rows[Item["evaluationId"]] = dict(Item)
        return {}

    def _get_item(self, Key, **_):
        row = self.rows.get(Key["evaluationId"])
        return {"Item": dict(row)} if row is not None else {}

    def _update_item(self, Key, UpdateExpression, ExpressionAttributeNames,
                     ExpressionAttributeValues, ConditionExpression=None, **_):
        row = dict(self.rows.get(Key["evaluationId"]) or {})
        if ConditionExpression is not None and not self._holds(
                row, ConditionExpression, ExpressionAttributeNames, ExpressionAttributeValues):
            raise conditional_check_failure()
        for clause in UpdateExpression.replace("SET ", "", 1).split(", "):
            name_token, value_token = clause.split(" = ", 1)
            row[ExpressionAttributeNames[name_token]] = ExpressionAttributeValues[value_token]
        row["evaluationId"] = Key["evaluationId"]
        self.rows[Key["evaluationId"]] = row
        return {}

    @staticmethod
    def _holds(row, expression, names, values):
        for clause in expression.split(" OR "):
            clause = clause.strip()
            if clause.startswith("attribute_not_exists("):
                if names[clause[len("attribute_not_exists("):-1]] not in row:
                    return True
                continue
            name_token, value_token = clause.split(" = ", 1)
            if row.get(names[name_token]) == values[value_token]:
                return True
        return False


class Tables:
    """Every table the store touches, each a MagicMock scripted with the row shapes the store reads.
    Defaults describe an asset with the `owner` metadata key, one parentChild parent, one file
    (`/model.stl`) under its S3 prefix, an existing workflow (no systemConfig of its own) and
    pipeline (arity one, no filters), the rule's template when it names one, and no prior compliance
    state. `previous_row` is the whole prior asset-state row (it wins over `previous_state`). The
    evaluation table is a FakeEvaluationTable, so the completion path's conditional writes and its
    consistent re-reads of the tracking rows run against real rows."""

    def __init__(self, schema_body=RULES_SCHEMA_BODY, previous_state=None, previous_row=None,
                 metadata_keys=("owner",), parents=1, workflow_exists=True, pipeline_exists=True,
                 template_exists=True, workflow_config=None, pipeline_config=None,
                 template_overrides=None, asset_files=DEFAULT_ASSET_FILES, schema_version=1):
        self.schema = MagicMock(name="schema_table")
        self.schema.query.return_value = {"Items": [
            schema_row(body=schema_body, version=schema_version)]}
        self.state = MagicMock(name="asset_state_table")
        if previous_row is None and previous_state:
            previous_row = {"complianceState": previous_state}
        self.state.get_item.return_value = {"Item": dict(previous_row)} if previous_row else {}
        self.evaluation_rows = FakeEvaluationTable()
        self.evaluation = self.evaluation_rows.mock
        self.audit = MagicMock(name="audit_table")
        self.metadata = MagicMock(name="asset_file_metadata_table")
        self.metadata.query.return_value = {"Items": [
            {"metadataKey": key, "metadataValue": "v", "metadataValueType": "STRING"}
            for key in metadata_keys]}
        self.metadata_schema = MagicMock(name="metadata_schema_table")
        self.metadata_schema.query.return_value = {"Items": []}
        self.links = MagicMock(name="asset_links_table")
        self.links.query.side_effect = lambda **kw: (
            {"Items": [{"relationshipType": "parentChild"}] * parents}
            if kw.get("IndexName") == "toAssetGSI" else {"Items": []})
        self.workflow = MagicMock(name="workflow_table")
        workflow_row = {"workflowId": "wf-1"}
        if workflow_config is not None:
            workflow_row["systemConfig"] = workflow_config
        self.workflow.get_item.return_value = {"Item": workflow_row} if workflow_exists else {}
        self.pipeline = MagicMock(name="pipeline_table")
        pipeline_row = {"pipelineId": "pipe-1"}
        if pipeline_config is not None:
            pipeline_row["systemConfig"] = pipeline_config
        self.pipeline.get_item.return_value = {"Item": pipeline_row} if pipeline_exists else {}
        self.templates = MagicMock(name="pipeline_templates_table")
        template_row = {"templateId": "tmpl-1"}
        if template_overrides is not None:
            template_row["overrides"] = template_overrides
        self.templates.get_item.return_value = {"Item": template_row} if template_exists else {}
        self.asset = MagicMock(name="asset_table")
        self.asset.get_item.return_value = {"Item": {
            "databaseId": DB, "assetId": ASSET, "bucketId": BUCKET_ID,
            "assetLocation": {"Key": ASSET_PREFIX}}}
        self.buckets = MagicMock(name="s3_asset_buckets_table")
        self.buckets.query.return_value = {"Items": [
            {"bucketId": BUCKET_ID, "bucketName": BUCKET_NAME, "baseAssetsPrefix": "/"}]}
        self.s3_client = _s3_client(list(asset_files))
        self.lambda_client = _lambda_client()

    def patches(self, function_name="execute-workflow-fn"):
        return [
            patch(f"{STORE}.to_update_expr", REAL_TO_UPDATE_EXPR),
            patch(f"{STORE}.schema_table", self.schema),
            patch(f"{STORE}.asset_state_table", self.state),
            patch(f"{STORE}.evaluation_table", self.evaluation),
            patch(f"{STORE}.audit_table", self.audit),
            patch(f"{STORE}.asset_file_metadata_table", self.metadata),
            patch(f"{STORE}.metadata_schema_table", self.metadata_schema),
            patch(f"{STORE}.asset_links_table", self.links),
            patch(f"{STORE}.workflow_table", self.workflow),
            patch(f"{STORE}.pipeline_table", self.pipeline),
            patch(f"{STORE}.pipeline_templates_table", self.templates),
            patch(f"{STORE}.asset_table", self.asset),
            patch(f"{STORE}.s3_asset_buckets_table", self.buckets),
            patch(f"{STORE}.s3_client", self.s3_client),
            patch(f"{STORE}.lambda_client", self.lambda_client),
            patch(f"{STORE}.execute_workflow_function_name", function_name),
        ]

    def run(self, callable_, *args, function_name="execute-workflow-fn", **kwargs):
        patches = self.patches(function_name)
        for p in patches:
            p.start()
        try:
            return callable_(*args, **kwargs)
        finally:
            for p in reversed(patches):
                p.stop()

    def evaluation_records(self):
        rows = put_items(self.evaluation)
        parents = [r for r in rows if r.get("recordType") is None]
        tracking = [r for r in rows if r.get("recordType") == store.PIPELINE_EXECUTION_RECORD_TYPE]
        return parents, tracking

    def audit_events(self):
        return [r["eventType"] for r in put_items(self.audit)]


@pytest.mark.unit
class TestSynchronousEvaluation:

    def test_all_rules_pass_writes_a_completed_compliant_evaluation(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY)
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "compliant"
        assert result["complianceState"] == "compliant"
        assert result["pipelineRulesPending"] == 0
        assert sorted(r["ruleName"] for r in result["ruleResults"]) == ["has-parent", "owner-present"]

        parents, tracking = tables.evaluation_records()
        assert tracking == []
        record = parents[0]
        assert record["status"] == "completed"
        assert record["verdict"] == "compliant"
        assert record["databaseId:assetId"] == f"{DB}:{ASSET}"
        assert record["completedAt"] == record["evaluatedAt"]
        assert record["actor"] == USER
        assert "executionId" not in record

        state = update_values(tables.state)[0]
        assert state["complianceState"] == "compliant"
        assert "schemaName" not in state
        assert "schemaSource" not in state
        assert state["lastEvaluationId"] == record["evaluationId"]
        assert record["schemaVersion"] == 1
        assert "exceptionApplied" not in record
        assert tables.audit_events() == ["compliance_check"]

    def test_the_evaluation_never_rewrites_the_binding(self):
        """An ad-hoc evaluation against another schema records that schema on the evaluation row
        only; the asset-state row keeps the schema it is bound to."""
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, previous_row={
            "complianceState": "compliant", "schemaName": "bound-schema", "schemaSource": "asset"})
        tables.run(store.run_evaluation, DB, ASSET, "adhoc-schema", USER)
        parents, _ = tables.evaluation_records()
        assert parents[0]["schemaName"] == "adhoc-schema"
        state = update_values(tables.state)[0]
        assert "schemaName" not in state
        assert "schemaSource" not in state
        assert set(state) == {"complianceState", "lastEvaluationId", "lastEvaluatedAt",
                              "updatedAt", "quarantineReason"}
        assert put_items(tables.audit)[0]["schemaName"] == "adhoc-schema"

    def test_a_failed_audit_write_does_not_fail_the_evaluation(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY)
        tables.audit.put_item.side_effect = RuntimeError("AccessDeniedException")
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "compliant"
        assert update_values(tables.state)[0]["complianceState"] == "compliant"
        assert tables.audit.put_item.call_count == 1

    def test_a_failed_quarantine_rule_quarantines_and_notifies(self, notifications_aws):
        notifications_aws.dynamodb_client.query.return_value = {"Items": [
            {"assetName": {"S": "Turbine"}, "snsTopic": {"S": "arn:aws:sns:us-east-1:1:topic"}}]}
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, parents=0)
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["verdict"] == "quarantined"
        assert result["complianceState"] == "quarantined"
        parents, _ = tables.evaluation_records()
        assert parents[0]["violations"] == ["parent: found 0 links, minimum is 1"]
        assert parents[0]["actor"] == store.SYSTEM_ACTOR
        assert update_values(tables.state)[0]["complianceState"] == "quarantined"
        assert update_values(tables.state)[0]["quarantineReason"] == "parent: found 0 links, minimum is 1"
        published = notifications_aws.sns_client.publish.call_args.kwargs
        assert published["TopicArn"] == "arn:aws:sns:us-east-1:1:topic"
        assert "QUARANTINED" in published["Subject"]
        assert "has-parent" in published["Message"]

    def test_a_failed_warn_rule_is_non_compliant_without_notification(self, notifications_aws):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, metadata_keys=())
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["verdict"] == "non_compliant"
        assert update_values(tables.state)[0]["complianceState"] == "non_compliant"
        notifications_aws.sns_client.publish.assert_not_called()

    def test_passing_after_quarantine_audits_the_release(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, previous_state="quarantined")
        tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert tables.audit_events() == ["compliance_check", "quarantine_released"]
        assert update_values(tables.state)[0]["quarantineReason"] is None
        audit = put_items(tables.audit)[0]
        assert audit["previousState"] == "quarantined"
        assert audit["newState"] == "compliant"

    def test_the_metadata_schema_is_read_from_the_database_then_global(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY)
        tables.metadata_schema.query.side_effect = [
            {"Items": []},
            {"Items": [{"schemaName": "Asset Schema", "fields": json.dumps(
                [{"metadataFieldKeyName": "units", "required": True}])}]},
        ]
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        partitions = [c.kwargs["KeyConditionExpression"]._values[1]
                      for c in tables.metadata_schema.query.call_args_list]
        assert partitions == [DB, "GLOBAL"]
        metadata_result = next(r for r in result["ruleResults"] if r["ruleName"] == "owner-present")
        assert metadata_result["passed"] is False
        assert "units" in metadata_result["message"]

    def test_a_missing_schema_records_an_error_evaluation(self):
        tables = Tables()
        tables.schema.query.return_value = {"Items": []}
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["verdict"] == "error"
        assert result["error"] == "Schema not found"
        assert result["complianceState"] == "unknown"
        parents, _ = tables.evaluation_records()
        assert parents[0]["status"] == "error"
        assert parents[0]["errorMessage"] == "Schema not found"
        tables.state.update_item.assert_not_called()
        tables.audit.put_item.assert_not_called()

    def test_a_legacy_json_schema_records_an_error_evaluation(self):
        tables = Tables(schema_body={"type": "object"})
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["error"] == "Schema is not vams-rules-v1 format"

    def test_asset_metadata_is_read_from_the_asset_root_composite_key_to_exhaustion(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY)
        tables.metadata.query.side_effect = [
            {"Items": [{"metadataKey": "a", "metadataValue": "1", "metadataValueType": "NUMBER"}],
             "LastEvaluatedKey": {"k": 1}},
            {"Items": [{"metadataKey": "owner", "metadataValue": "x", "metadataValueType": "STRING"}]},
        ]
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        first = tables.metadata.query.call_args_list[0].kwargs
        assert first["IndexName"] == "DatabaseIdAssetIdFilePathIndex"
        assert first["KeyConditionExpression"]._values[1] == f"{DB}:{ASSET}:/"
        assert tables.metadata.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {"k": 1}
        metadata_result = next(r for r in result["ruleResults"] if r["ruleName"] == "owner-present")
        assert metadata_result["measured"]["metadataKeys"] == ["a", "owner"]


@pytest.mark.unit
class TestPipelineRuleLaunch:

    def test_the_execute_request_follows_the_v2_model(self):
        rule = _rule(inputParameters={"expected_crs": "EPSG:27700"},
                     pipelineRef=dict(PIPELINE_RULE.pipelineRef.dict(), templateId="tmpl-1"))
        body = store.build_execute_workflow_request(rule, _inputs("/models/a.stl"), "grp-1")
        parsed = ExecuteWorkflowRequestV2Model(**body)
        assert parsed.triggerType == "manual"
        assert body["inputFiles"] == [{"databaseId": DB, "assetId": ASSET,
                                       "relativeFileKey": "/models/a.stl"}]
        assert body["pipelineExecutionParameters"] == {"pipe-1": {
            "templateId": "tmpl-1",
            "templateTags": [{"key": "expected_crs", "value": "EPSG:27700"}]}}
        assert body["executionGroupId"] == "grp-1"

    def test_a_rule_without_template_or_parameters_sends_an_empty_parameter_map(self):
        body = store.build_execute_workflow_request(PIPELINE_RULE, _inputs("/"))
        assert body["pipelineExecutionParameters"] == {"pipe-1": {}}
        assert body["inputFiles"] == _inputs("/")
        assert "executionGroupId" not in body
        ExecuteWorkflowRequestV2Model(**body)

    def test_an_arity_none_selection_sends_no_input_files(self):
        body = store.build_execute_workflow_request(PIPELINE_RULE, [])
        assert body["inputFiles"] == []
        assert ExecuteWorkflowRequestV2Model(**body).inputFiles == []

    def test_the_cross_call_event_targets_the_execute_route_as_system_user(self):
        event = store.build_execute_workflow_event("GLOBAL", "wf-1", {"x": 1})
        assert event["lambdaCrossCall"] == {"userName": "SYSTEM_USER"}
        assert event["requestContext"]["http"] == {"method": "POST",
                                                   "path": "/workflows/GLOBAL/wf-1/execute"}
        assert event["pathParameters"] == {"workflowDatabaseId": "GLOBAL", "workflowId": "wf-1"}
        assert json.loads(event["body"]) == {"x": 1}

    def test_a_pipeline_rule_launches_with_the_matching_file_and_leaves_the_evaluation_pending(self):
        tables = Tables()
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "pending_pipeline"
        assert result["complianceState"] == "pending_evaluation"
        assert result["pipelineRulesPending"] == 1

        invoke = tables.lambda_client.invoke.call_args.kwargs
        assert invoke["FunctionName"] == "execute-workflow-fn"
        assert invoke["InvocationType"] == "RequestResponse"
        payload = json.loads(invoke["Payload"])
        assert payload["requestContext"]["http"]["path"] == "/workflows/GLOBAL/wf-1/execute"
        launch_body = json.loads(payload["body"])
        assert launch_body["triggerType"] == "manual"
        # The default `matching` selection resolves to the asset's one file, never to the root.
        assert launch_body["inputFiles"] == _inputs("/model.stl")
        listing = tables.s3_client.get_paginator.return_value.paginate.call_args.kwargs
        assert listing == {"Bucket": BUCKET_NAME, "Prefix": ASSET_PREFIX}

        parents, tracking = tables.evaluation_records()
        parent, track = parents[0], tracking[0]
        # The launch is grouped under the evaluation, so the execution's audit entry and completion
        # event name the evaluation they belong to; the execute model accepts the id as a group id.
        assert launch_body["executionGroupId"] == parent["evaluationId"]
        ExecuteWorkflowRequestV2Model(**launch_body)
        assert parent["status"] == "pending_pipeline"
        assert parent["executionId"] == "exec-1"
        assert parent["pipelineRuleName"] == "residual-bound"
        assert parent["pipelineExecutions"] == [
            {"ruleName": "residual-bound", "executionId": "exec-1", "status": "pending"}]
        assert "completedAt" not in parent
        assert [r["ruleName"] for r in json.loads(parent["pipelineRulesPending"])] == ["residual-bound"]
        assert track["evaluationId"] == parent["evaluationId"] + "#residual-bound"
        assert track["parentEvaluationId"] == parent["evaluationId"]
        assert track["executionId"] == "exec-1"
        assert track["status"] == "pending"
        assert "databaseId:assetId" not in track
        assert update_values(tables.state)[0]["complianceState"] == "pending_evaluation"

    def test_a_template_rule_reads_its_template_row_and_applies_its_overrides(self):
        rule = _rule(pipelineRef=dict(PIPELINE_RULE.pipelineRef.dict(), templateId="tmpl-1"))
        body = dict(RULES_SCHEMA_BODY, rules={"residual-bound": rule.dict()})
        tables = Tables(schema_body=body, asset_files=("/a.stl", "/b.obj"),
                        pipeline_config={"inputFileArity": "one"},
                        template_overrides={"inputFileFilters": {"allow": ["*.obj"]}})
        tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert tables.templates.get_item.call_args.kwargs["Key"] == {
            "pipelineDatabaseId:pipelineId": "GLOBAL:pipe-1", "templateId": "tmpl-1"}
        launch_body = json.loads(json.loads(tables.lambda_client.invoke.call_args.kwargs["Payload"])["body"])
        assert launch_body["inputFiles"] == _inputs("/b.obj")

    @pytest.mark.parametrize("scenario", ["workflow-missing", "pipeline-missing", "template-missing",
                                          "refused", "no-execution-id", "unconfigured",
                                          "invoke-raises"])
    def test_a_launch_that_fails_marks_the_rule_failed_and_completes_synchronously(self, scenario):
        body = RULES_SCHEMA_BODY
        if scenario == "template-missing":
            rule = _rule(pipelineRef=dict(PIPELINE_RULE.pipelineRef.dict(), templateId="tmpl-1"))
            body = dict(RULES_SCHEMA_BODY, rules={"residual-bound": rule.dict()})
        tables = Tables(schema_body=body, workflow_exists=scenario != "workflow-missing",
                        pipeline_exists=scenario != "pipeline-missing",
                        template_exists=scenario != "template-missing")
        if scenario == "refused":
            tables.lambda_client = _lambda_client(status_code=403)
        if scenario == "no-execution-id":
            tables.lambda_client = _lambda_client(body={"message": "ok"})
        if scenario == "invoke-raises":
            tables.lambda_client.invoke.side_effect = RuntimeError("boom")
        function_name = "" if scenario == "unconfigured" else "execute-workflow-fn"

        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, function_name=function_name)
        assert result["verdict"] == "quarantined"
        assert result["pipelineRulesPending"] == 0
        pipeline_result = next(r for r in result["ruleResults"] if r["ruleName"] == "residual-bound")
        assert pipeline_result["passed"] is False
        assert pipeline_result["message"] == "Pipeline execution could not be started"
        parents, tracking = tables.evaluation_records()
        assert tracking == []
        assert parents[0]["status"] == "completed"
        assert "pipelineRulesPending" not in parents[0]

    @pytest.mark.parametrize("asset_files,message", [
        ((), store.INPUT_SELECTION_NOT_ONE_FILE),
        (("/a.stl", "/b.stl"), store.INPUT_SELECTION_NOT_ONE_FILE),
    ], ids=["no-match", "several-matches"])
    def test_a_selection_failure_is_the_rules_error_result_with_the_generic_message(
            self, asset_files, message):
        tables = Tables(asset_files=asset_files)
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        tables.lambda_client.invoke.assert_not_called()
        assert result["verdict"] == "quarantined"
        pipeline_result = next(r for r in result["ruleResults"] if r["ruleName"] == "residual-bound")
        assert pipeline_result["passed"] is False
        assert pipeline_result["message"] == message
        for key in asset_files:
            assert key not in pipeline_result["message"]
        parents, tracking = tables.evaluation_records()
        assert tracking == []
        assert parents[0]["status"] == "completed"
        assert parents[0]["violations"] == [message]

    def test_a_whole_asset_selection_the_workflow_refuses_is_the_rules_error(self):
        rule = _rule(inputFiles={"mode": "wholeAsset"})
        body = dict(RULES_SCHEMA_BODY, rules={"residual-bound": rule.dict()})
        tables = Tables(schema_body=body, workflow_config={
            "inputFileArity": "one", "assetScope": {"wholeAssetAllowed": False}})
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        tables.lambda_client.invoke.assert_not_called()
        tables.s3_client.get_paginator.assert_not_called()
        pipeline_result = next(r for r in result["ruleResults"] if r["ruleName"] == "residual-bound")
        assert pipeline_result["message"] == store.INPUT_SELECTION_REFUSED

    def test_the_asset_files_are_listed_once_for_several_pipeline_rules(self):
        body = dict(RULES_SCHEMA_BODY, rules={
            "residual-bound": PIPELINE_RULE.dict(), "other": OTHER_RULE.dict()})
        tables = Tables(schema_body=body)
        tables.pipeline.get_item.side_effect = lambda **kw: {"Item": {"pipelineId": kw["Key"]["pipelineId"]}}
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["pipelineRulesPending"] == 2
        assert tables.s3_client.get_paginator.return_value.paginate.call_count == 1
        assert tables.asset.get_item.call_count == 1


def _workflow(arity=None, whole_asset=None, allow=None, exclude=None):
    """A workflow row with the systemConfig keys a test names."""
    config = {}
    if arity is not None:
        config["inputFileArity"] = arity
    if whole_asset is not None:
        config["assetScope"] = {"wholeAssetAllowed": whole_asset}
    if allow is not None or exclude is not None:
        config["inputFileFilters"] = {"allow": allow or [], "exclude": exclude or []}
    return {"workflowId": "wf-1", "systemConfig": config}


def _pipeline(arity=None, whole_asset=None, allow=None, exclude=None, enabled=True, archived=False):
    """A pipeline row with the systemConfig keys a test names (assetScope in the registration
    shorthand the built-in pipelines use)."""
    config = {}
    if arity is not None:
        config["inputFileArity"] = arity
    if whole_asset is not None:
        config["assetScope"] = {"wholeAsset": whole_asset}
    if allow is not None or exclude is not None:
        config["inputFileFilters"] = {"allow": allow or [], "exclude": exclude or []}
    return {"pipelineId": "pipe-1", "systemConfig": config, "enabled": enabled, "archived": archived}


def _template(**overrides):
    return {"templateId": "tmpl-1", "overrides": overrides}


# The built-in 3D conversion pipeline as seeded: arity one, no whole-asset selection, six model
# extensions allowed.
MODEL_EXTENSIONS = ["*.stl", "*.obj", "*.ply", "*.gltf", "*.glb", "*.xyz"]
CONVERSION_PIPELINE = _pipeline(arity="one", whole_asset=False, allow=MODEL_EXTENSIONS)
# A workflow row whose systemConfig declares no arity: the pipeline's arity applies.
BARE_WORKFLOW = {"workflowId": "wf-1"}


@pytest.mark.unit
class TestPipelineInputSelection:
    """`resolve_pipeline_rule_inputs` over an in-memory file listing: every mode against every arity,
    the filter chain, the asset-scope gate and the three generic messages."""

    def _resolve(self, rule, files, workflow=BARE_WORKFLOW, pipeline=CONVERSION_PIPELINE,
                 template=None):
        listing = MagicMock(name="asset_file_keys", return_value=files)
        inputs, error = store.resolve_pipeline_rule_inputs(
            rule, DB, ASSET, workflow, pipeline, template, listing)
        return inputs, error, listing

    # --- matching ---

    def test_matching_with_arity_one_and_exactly_one_match_sends_that_file(self):
        inputs, error, _ = self._resolve(PIPELINE_RULE, ["/readme.txt", "/model.stl"])
        assert error is None
        assert inputs == _inputs("/model.stl")

    @pytest.mark.parametrize("files", [[], ["/readme.txt"], ["/a.stl", "/b.obj"]],
                             ids=["no-files", "no-match", "several-matches"])
    def test_matching_with_arity_one_needs_exactly_one_match(self, files):
        inputs, error, _ = self._resolve(PIPELINE_RULE, files)
        assert inputs is None
        assert error == store.INPUT_SELECTION_NOT_ONE_FILE

    def test_matching_with_arity_multi_sends_every_match_sorted_by_key(self):
        inputs, error, _ = self._resolve(
            PIPELINE_RULE, ["/z.stl", "/notes.txt", "/a.obj", "/m/b.glb"],
            workflow=_workflow(arity="multi"), pipeline=_pipeline(arity="multi", allow=MODEL_EXTENSIONS))
        assert error is None
        assert inputs == _inputs("/a.obj", "/m/b.glb", "/z.stl")

    def test_matching_with_arity_multi_and_no_match_is_refused(self):
        inputs, error, _ = self._resolve(PIPELINE_RULE, ["/notes.txt"],
                                         workflow=_workflow(arity="multi"),
                                         pipeline=_pipeline(arity="multi", allow=MODEL_EXTENSIONS))
        assert inputs is None
        assert error == store.INPUT_SELECTION_REFUSED

    def test_matching_with_arity_none_sends_no_input_files(self):
        inputs, error, _ = self._resolve(PIPELINE_RULE, ["/a.stl", "/b.stl"],
                                         workflow=_workflow(arity="none"), pipeline=_pipeline(arity="none"))
        assert error is None
        assert inputs == []

    def test_the_workflow_arity_wins_over_the_pipelines(self):
        inputs, error, _ = self._resolve(PIPELINE_RULE, ["/a.stl", "/b.stl"],
                                         workflow=_workflow(arity="multi"),
                                         pipeline=_pipeline(arity="one", allow=MODEL_EXTENSIONS))
        # The workflow admits both; the pipeline's own arity is then judged by the shared validator.
        assert inputs is None
        assert error == store.INPUT_SELECTION_REFUSED

    def test_the_rules_own_filter_narrows_the_matches(self):
        rule = _rule(inputFiles={"mode": "matching", "filter": ["*.obj"]})
        inputs, error, _ = self._resolve(rule, ["/a.stl", "/b.obj"])
        assert error is None
        assert inputs == _inputs("/b.obj")

    def test_the_rules_filter_cannot_widen_the_pipelines(self):
        rule = _rule(inputFiles={"mode": "matching", "filter": ["*.txt"]})
        inputs, error, _ = self._resolve(rule, ["/a.stl", "/notes.txt"])
        assert inputs is None
        assert error == store.INPUT_SELECTION_NOT_ONE_FILE

    def test_the_workflow_filters_apply_before_the_pipelines(self):
        inputs, error, _ = self._resolve(
            PIPELINE_RULE, ["/a.stl", "/b.obj"], workflow=_workflow(exclude=["*.stl"]))
        assert error is None
        assert inputs == _inputs("/b.obj")

    def test_a_template_override_replaces_the_pipelines_filters(self):
        inputs, error, _ = self._resolve(
            PIPELINE_RULE, ["/a.stl", "/b.obj"],
            template=_template(inputFileFilters={"allow": ["*.obj"]}))
        assert error is None
        assert inputs == _inputs("/b.obj")

    def test_a_template_override_of_the_arity_applies(self):
        pipeline = _pipeline(arity="one", allow=MODEL_EXTENSIONS)
        refused, error, _ = self._resolve(PIPELINE_RULE, ["/a.stl", "/b.obj"],
                                          workflow=_workflow(arity="multi"), pipeline=pipeline)
        assert refused is None and error == store.INPUT_SELECTION_REFUSED
        inputs, error, _ = self._resolve(
            PIPELINE_RULE, ["/a.stl", "/b.obj"], workflow=_workflow(arity="multi"),
            pipeline=pipeline, template=_template(inputFileArity="multi"))
        assert error is None
        assert inputs == _inputs("/a.stl", "/b.obj")

    def test_matching_is_refused_when_the_asset_files_cannot_be_listed(self):
        inputs, error, listing = self._resolve(PIPELINE_RULE, None)
        assert inputs is None
        assert error == store.INPUT_SELECTION_REFUSED
        listing.assert_called_once_with()

    # --- wholeAsset ---

    def test_whole_asset_is_sent_when_the_workflow_allows_it(self):
        rule = _rule(inputFiles={"mode": "wholeAsset"})
        inputs, error, listing = self._resolve(
            rule, ["/a.stl"], workflow=_workflow(arity="one", whole_asset=True),
            pipeline=_pipeline(arity="one", whole_asset=True))
        assert error is None
        assert inputs == _inputs("/")
        listing.assert_not_called()

    @pytest.mark.parametrize("workflow,pipeline", [
        (_workflow(arity="one", whole_asset=False), _pipeline(arity="one")),
        (BARE_WORKFLOW, CONVERSION_PIPELINE),
        (_workflow(arity="one", whole_asset=True), _pipeline(arity="one", whole_asset=False)),
    ], ids=["workflow-refuses", "workflow-silent-pipeline-refuses", "pipeline-refuses"])
    def test_whole_asset_is_refused_when_the_scope_forbids_it(self, workflow, pipeline):
        rule = _rule(inputFiles={"mode": "wholeAsset"})
        inputs, error, _ = self._resolve(rule, ["/a.stl"], workflow=workflow, pipeline=pipeline)
        assert inputs is None
        assert error == store.INPUT_SELECTION_REFUSED

    # --- explicit ---

    def test_explicit_keys_that_exist_are_sent_sorted(self):
        rule = _rule(inputFiles={"mode": "explicit", "keys": ["/b.obj", "a.stl"]})
        inputs, error, _ = self._resolve(
            rule, ["/a.stl", "/b.obj", "/c.stl"], workflow=_workflow(arity="multi"),
            pipeline=_pipeline(arity="multi", allow=MODEL_EXTENSIONS))
        assert error is None
        assert inputs == _inputs("/a.stl", "/b.obj")

    def test_an_explicit_key_the_asset_lacks_is_refused(self):
        rule = _rule(inputFiles={"mode": "explicit", "keys": ["/a.stl", "/missing.stl"]})
        inputs, error, _ = self._resolve(rule, ["/a.stl"], workflow=_workflow(arity="multi"))
        assert inputs is None
        assert error == store.INPUT_SELECTION_MISSING_FILE
        assert "missing.stl" not in error

    def test_explicit_keys_are_held_to_the_arity(self):
        rule = _rule(inputFiles={"mode": "explicit", "keys": ["/a.stl", "/b.stl"]})
        inputs, error, _ = self._resolve(rule, ["/a.stl", "/b.stl"])
        assert inputs is None
        assert error == store.INPUT_SELECTION_NOT_ONE_FILE

    def test_an_explicit_key_the_workflow_filters_out_is_refused(self):
        rule = _rule(inputFiles={"mode": "explicit", "keys": ["/notes.txt"]})
        inputs, error, _ = self._resolve(rule, ["/notes.txt"])
        assert inputs is None
        assert error == store.INPUT_SELECTION_REFUSED

    # --- the pipeline row ---

    @pytest.mark.parametrize("pipeline", [
        _pipeline(arity="one", enabled=False), _pipeline(arity="one", archived=True),
    ], ids=["disabled", "archived"])
    def test_a_pipeline_that_cannot_run_is_refused(self, pipeline):
        inputs, error, _ = self._resolve(PIPELINE_RULE, ["/a.stl"], pipeline=pipeline)
        assert inputs is None
        assert error == store.INPUT_SELECTION_REFUSED

    def test_the_messages_carry_no_selection_details(self):
        """Rule 11: the three texts are fixed strings with no file key, glob or pipeline id."""
        for message in (store.INPUT_SELECTION_REFUSED, store.INPUT_SELECTION_NOT_ONE_FILE,
                        store.INPUT_SELECTION_MISSING_FILE):
            assert "/" not in message and "*" not in message and "pipe-1" not in message


@pytest.mark.unit
class TestAssetFileListing:

    def _list(self, tables):
        return tables.run(store.list_asset_file_keys, DB, ASSET)

    def test_the_listing_pages_to_exhaustion_and_yields_sorted_relative_keys(self):
        tables = Tables()
        tables.s3_client = _s3_client(["/z.stl", "models/", "/models/a.obj"], ["/a.txt", "/z.stl"])
        keys = self._list(tables)
        assert keys == ["/a.txt", "/models/a.obj", "/z.stl"]
        assert tables.s3_client.get_paginator.call_args.args == ("list_objects_v2",)
        assert tables.s3_client.get_paginator.return_value.paginate.call_args.kwargs == {
            "Bucket": BUCKET_NAME, "Prefix": ASSET_PREFIX}

    def test_the_prefix_gains_a_trailing_slash(self):
        tables = Tables()
        tables.asset.get_item.return_value = {"Item": {
            "bucketId": BUCKET_ID, "assetLocation": {"Key": ASSET}}}
        self._list(tables)
        assert tables.s3_client.get_paginator.return_value.paginate.call_args.kwargs["Prefix"] == ASSET_PREFIX

    @pytest.mark.parametrize("asset", [
        None, {"assetLocation": {"Key": ASSET_PREFIX}}, {"bucketId": BUCKET_ID},
        {"bucketId": BUCKET_ID, "assetLocation": "not-a-map"},
    ], ids=["no-asset", "no-bucket-id", "no-location", "malformed-location"])
    def test_an_unresolvable_asset_yields_none(self, asset):
        tables = Tables()
        tables.asset.get_item.return_value = {"Item": asset} if asset is not None else {}
        assert self._list(tables) is None
        tables.s3_client.get_paginator.assert_not_called()

    def test_a_missing_bucket_row_yields_none(self):
        tables = Tables()
        tables.buckets.query.return_value = {"Items": []}
        assert self._list(tables) is None
        assert tables.buckets.query.call_args.kwargs["KeyConditionExpression"]._values[1] == BUCKET_ID


def _exception_row(state="exception", schema_name=SCHEMA, version=1, **extra):
    """An asset-state row carrying an active exception granted against `schema_name` v`version`
    (the version stored as DynamoDB returns a number)."""
    row = {
        "complianceState": state,
        "schemaName": SCHEMA,
        "exceptionGranted": True,
        "exceptionReason": "vendor waiver",
        "exceptionGrantedBy": USER,
        "exceptionGrantedAt": "2026-01-01T00:00:00+00:00",
        "exceptionSchemaName": schema_name,
        "exceptionSchemaVersion": Decimal(version),
    }
    row.update(extra)
    return row


@pytest.mark.unit
class TestExceptions:
    """A quarantine exception scoped to a schema name + version keeps the asset released while it
    applies; an evaluation against another schema or version supersedes it."""

    def test_failing_rules_under_an_active_exception_leave_the_asset_in_the_exception_state(
            self, notifications_aws):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, parents=0, previous_row=_exception_row())
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "quarantined"
        assert result["complianceState"] == "exception"
        parents, _ = tables.evaluation_records()
        assert parents[0]["verdict"] == "quarantined"
        assert parents[0]["violations"] == ["parent: found 0 links, minimum is 1"]
        assert parents[0]["exceptionApplied"] is True
        state = update_values(tables.state)[0]
        assert state["complianceState"] == "exception"
        assert state["quarantineReason"] is None
        assert not set(state) & set(store.engine.EXCEPTION_FIELDS)
        notifications_aws.sns_client.publish.assert_not_called()
        assert tables.audit_events() == ["compliance_check"]
        assert json.loads(put_items(tables.audit)[0]["details"])["exceptionApplied"] is True

    def test_passing_rules_under_an_active_exception_are_compliant_and_keep_the_exception(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, previous_row=_exception_row())
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["complianceState"] == "compliant"
        parents, _ = tables.evaluation_records()
        assert parents[0]["exceptionApplied"] is True
        state = update_values(tables.state)[0]
        assert state["complianceState"] == "compliant"
        assert not set(state) & set(store.engine.EXCEPTION_FIELDS)

    def test_a_warn_failure_under_an_active_exception_is_the_exception_state_too(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, metadata_keys=(),
                        previous_row=_exception_row())
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "non_compliant"
        assert result["complianceState"] == "exception"

    @pytest.mark.parametrize("previous_row,schema_version", [
        (_exception_row(version=1), 2),
        (_exception_row(schema_name="other-schema"), 1),
    ], ids=["newer-version", "other-schema"])
    def test_an_evaluation_against_another_schema_or_version_supersedes_the_exception(
            self, notifications_aws, previous_row, schema_version):
        notifications_aws.dynamodb_client.query.return_value = {"Items": [
            {"assetName": {"S": "Turbine"}, "snsTopic": {"S": "arn:aws:sns:us-east-1:1:topic"}}]}
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, parents=0, previous_row=previous_row,
                        schema_version=schema_version)
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["complianceState"] == "quarantined"
        parents, _ = tables.evaluation_records()
        assert "exceptionApplied" not in parents[0]
        state = update_values(tables.state)[0]
        assert state["complianceState"] == "quarantined"
        assert state["exceptionGranted"] is False
        for field in store.engine.EXCEPTION_FIELDS:
            if field != "exceptionGranted":
                assert state[field] is None
        assert tables.audit_events() == ["exception_superseded", "compliance_check"]
        superseded = put_items(tables.audit)[0]
        assert superseded["previousState"] == "exception"
        assert superseded["newState"] == "quarantined"
        assert superseded["schemaName"] == SCHEMA
        details = json.loads(superseded["details"])
        assert details["exceptionSchemaName"] == previous_row["exceptionSchemaName"]
        assert details["exceptionSchemaVersion"] == 1
        assert details["schemaVersion"] == schema_version
        notifications_aws.sns_client.publish.assert_called_once()

    def test_a_row_without_an_exception_is_neither_applied_nor_superseded(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, parents=0,
                        previous_row={"complianceState": "compliant", "exceptionGranted": False})
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["complianceState"] == "quarantined"
        assert not set(update_values(tables.state)[0]) & set(store.engine.EXCEPTION_FIELDS)
        assert tables.audit_events() == ["compliance_check"]

    def test_a_pending_pipeline_evaluation_under_an_exception_records_it_and_stays_pending(self):
        tables = Tables(previous_row=_exception_row())
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["complianceState"] == "pending_evaluation"
        parents, _ = tables.evaluation_records()
        assert parents[0]["exceptionApplied"] is True
        assert parents[0]["schemaVersion"] == 1

    def _finalize(self, tables, output):
        evaluation = dict(_pending_evaluation(), schemaVersion=1)
        tables.evaluation_rows.seed(evaluation, _tracking_row())
        return tables.run(store.complete_pipeline_rule, evaluation, "residual-bound",
                          "SUCCEEDED", output, None, None)

    def test_the_finalize_path_honours_an_active_exception(self, notifications_aws):
        tables = Tables(previous_row=_exception_row())
        outcome = self._finalize(tables, {"complianceOutput": True, "status": "success",
                                          "measurements": {"residual": 5}})
        assert outcome["verdict"] == "quarantined"
        assert outcome["complianceState"] == "exception"
        assert tables.evaluation_rows.rows["eval-1"]["exceptionApplied"] is True
        state = update_values(tables.state)[0]
        assert state["complianceState"] == "exception"
        assert not set(state) & set(store.engine.EXCEPTION_FIELDS)
        notifications_aws.sns_client.publish.assert_not_called()

    def test_the_finalize_path_supersedes_an_exception_granted_against_an_older_version(self):
        tables = Tables(previous_row=_exception_row(version=1))
        evaluation = dict(_pending_evaluation(), schemaVersion=2)
        tables.evaluation_rows.seed(evaluation, _tracking_row())
        outcome = tables.run(store.complete_pipeline_rule, evaluation, "residual-bound",
                             "SUCCEEDED", {"complianceOutput": True, "status": "success",
                                           "measurements": {"residual": 5}}, None, None)
        assert outcome["complianceState"] == "quarantined"
        assert "exceptionApplied" not in tables.evaluation_rows.rows["eval-1"]
        assert update_values(tables.state)[0]["exceptionGranted"] is False
        assert tables.audit_events() == ["exception_superseded", "compliance_check"]

    def test_an_evaluation_row_without_a_version_is_judged_against_the_schemas_current_version(self):
        tables = Tables(previous_row=_exception_row(version=1), schema_version=1)
        tables.evaluation_rows.seed(_pending_evaluation(), _tracking_row())
        outcome = tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                             "SUCCEEDED", {"complianceOutput": True, "status": "success",
                                           "measurements": {"residual": 5}}, None, None)
        assert outcome["complianceState"] == "exception"
        assert tables.schema.query.call_args.kwargs["KeyConditionExpression"]._values[1] == SCHEMA

    def test_the_cleared_fields_helper_names_every_exception_attribute(self):
        cleared = store.engine.cleared_exception_fields()
        assert set(cleared) == set(store.engine.EXCEPTION_FIELDS) == {
            "exceptionGranted", "exceptionReason", "exceptionGrantedBy", "exceptionGrantedAt",
            "exceptionSchemaName", "exceptionSchemaVersion"}
        assert cleared["exceptionGranted"] is False
        assert all(value is None for field, value in cleared.items() if field != "exceptionGranted")
        assert store.engine.STATE_EXCEPTION == "exception"
        assert store.AUDIT_EXCEPTION_SUPERSEDED == "exception_superseded"


def _pending_evaluation(rule_results=None, executions=None, pending_rules=None):
    """A stored `pending_pipeline` evaluation record for the one pipeline rule of RULES_SCHEMA_BODY,
    with the synchronous rule results already recorded."""
    return {
        "evaluationId": "eval-1",
        "databaseId": DB,
        "assetId": ASSET,
        "schemaName": SCHEMA,
        "status": "pending_pipeline",
        "evaluatedAt": "2026-01-01T00:00:00+00:00",
        "ruleResults": json.dumps(rule_results if rule_results is not None else [
            {"ruleName": "has-parent", "ruleType": "relationship", "enforcement": "quarantine",
             "passed": True}]),
        "pipelineRulesPending": json.dumps(pending_rules if pending_rules is not None else [
            {"ruleName": "residual-bound", "rule": PIPELINE_RULE.dict()}]),
        "pipelineExecutions": executions or [
            {"ruleName": "residual-bound", "executionId": "exec-1", "status": "pending"}],
        "executionId": "exec-1",
        "pipelineRuleName": "residual-bound",
    }


def _tracking_row(rule_name="residual-bound", execution_id="exec-1", status="pending", **extra):
    row = {"evaluationId": f"eval-1#{rule_name}", "recordType": "pipelineExecution",
           "parentEvaluationId": "eval-1", "pipelineRuleName": rule_name,
           "executionId": execution_id, "status": status}
    row.update(extra)
    return row


# A second pipeline rule, so an evaluation can have two executions in flight.
OTHER_RULE = PipelineRule(**dict(RULES_SCHEMA_BODY["rules"]["residual-bound"],
                                 pipelineRef=dict(PIPELINE_RULE.pipelineRef.dict(), pipelineId="pipe-2"),
                                 checks=[{"name": "drift", "outputField": "drift",
                                          "tolerance": {"operator": "lte", "value": 1}}]))


def _two_rule_evaluation():
    return _pending_evaluation(
        executions=[{"ruleName": "residual-bound", "executionId": "exec-1", "status": "pending"},
                    {"ruleName": "other", "executionId": "exec-2", "status": "pending"}],
        pending_rules=[{"ruleName": "residual-bound", "rule": PIPELINE_RULE.dict()},
                       {"ruleName": "other", "rule": OTHER_RULE.dict()}])


@pytest.mark.unit
class TestResolvePipelineExecution:

    def test_a_tracking_row_resolves_to_its_parent_and_rule(self):
        tables = Tables()
        tables.evaluation.query.return_value = {"Items": [_tracking_row()]}
        tables.evaluation_rows.seed(_pending_evaluation())
        parent, rule_name = tables.run(store.resolve_pipeline_execution, "exec-1")
        assert parent["evaluationId"] == "eval-1"
        assert rule_name == "residual-bound"
        query = tables.evaluation.query.call_args.kwargs
        assert query["IndexName"] == "ExecutionIdIndex"
        assert query["KeyConditionExpression"]._values[1] == "exec-1"
        # The parent may have been written by a sibling's callback moments ago.
        assert tables.evaluation.get_item.call_args.kwargs["ConsistentRead"] is True

    def test_a_parent_row_alone_resolves_through_its_own_rule_name(self):
        tables = Tables()
        tables.evaluation.query.return_value = {"Items": [_pending_evaluation()]}
        parent, rule_name = tables.run(store.resolve_pipeline_execution, "exec-1")
        assert parent["evaluationId"] == "eval-1" and rule_name == "residual-bound"
        tables.evaluation.get_item.assert_not_called()

    def test_an_unknown_execution_is_none(self):
        tables = Tables()
        assert tables.run(store.resolve_pipeline_execution, "other") is None

    def test_a_tracking_row_whose_parent_is_gone_is_none(self):
        tables = Tables()
        tables.evaluation.query.return_value = {"Items": [
            {"recordType": "pipelineExecution", "parentEvaluationId": "eval-x",
             "pipelineRuleName": "r"}]}
        assert tables.run(store.resolve_pipeline_execution, "exec-1") is None


@pytest.mark.unit
class TestCompletePipelineRule:
    OUTPUT = {"complianceOutput": True, "status": "success", "measurements": {"residual": 0.2}}
    OTHER_OUTPUT = {"complianceOutput": True, "status": "success", "measurements": {"drift": 0.5}}

    def _tables(self, evaluation=None, tracking=(_tracking_row(),), **kwargs):
        tables = Tables(**kwargs)
        tables.evaluation_rows.seed(evaluation or _pending_evaluation(), *tracking)
        return tables

    def test_the_last_rule_finalizes_the_evaluation_and_the_asset_state(self):
        tables = self._tables(previous_state="pending_evaluation")
        outcome = tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                             "SUCCEEDED", self.OUTPUT, "2026-01-01T00:00:00+00:00",
                             "2026-01-01T00:01:00+00:00")
        assert outcome == {"evaluationId": "eval-1", "ruleName": "residual-bound",
                           "finalized": True, "verdict": "compliant", "complianceState": "compliant"}
        updates = update_values(tables.evaluation)
        tracking, parent = updates[0], updates[1]
        assert tracking["status"] == "completed"
        assert tracking["executionStatus"] == "SUCCEEDED"
        assert json.loads(tracking["ruleResults"])[0]["passed"] is True
        assert parent["status"] == "completed"
        assert parent["verdict"] == "compliant"
        assert parent["violations"] == []
        assert [r["ruleName"] for r in json.loads(parent["ruleResults"])] == [
            "has-parent", "residual-bound"]
        assert parent["pipelineExecutions"][0]["status"] == "completed"
        finalize = tables.evaluation.update_item.call_args_list[1].kwargs
        assert "ConditionExpression" in finalize
        stored = tables.evaluation_rows.rows["eval-1"]
        assert stored["status"] == "completed"
        assert stored["pipelineExecutions"] == [
            {"ruleName": "residual-bound", "executionId": "exec-1", "status": "completed"}]
        assert update_values(tables.state)[0]["complianceState"] == "compliant"
        assert tables.audit_events() == ["compliance_check"]
        audit = put_items(tables.audit)[0]
        assert json.loads(audit["details"])["phase"] == "pipeline_callback"
        assert audit["actor"] == store.SYSTEM_ACTOR

    def test_the_tracking_write_is_conditioned_on_the_row_still_pending(self):
        tables = self._tables()
        tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                   "SUCCEEDED", self.OUTPUT, None, None)
        tracking_write = tables.evaluation.update_item.call_args_list[0].kwargs
        assert tracking_write["Key"] == {"evaluationId": "eval-1#residual-bound"}
        condition = tracking_write["ConditionExpression"]
        names = tracking_write["ExpressionAttributeNames"]
        values = tracking_write["ExpressionAttributeValues"]
        assert "attribute_not_exists(#cond_status)" in condition
        assert "#cond_status = :cond_status0" in condition
        assert names["#cond_status"] == "status"
        assert values[":cond_status0"] == "pending"
        # The condition placeholders sit beside the update's own aliases rather than replacing them.
        assert values[":v0"] == "completed"

    def test_a_redelivered_completion_records_nothing_twice(self):
        """At-least-once delivery: the second delivery finds the tracking row completed, so it neither
        appends the rule's result again nor touches the evaluation, the asset state or the audit."""
        tables = self._tables(previous_state="pending_evaluation")
        first = tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                           "SUCCEEDED", self.OUTPUT, None, None)
        assert first["finalized"] is True
        writes_after_first = len(tables.evaluation.update_item.call_args_list)
        stored_after_first = json.loads(tables.evaluation_rows.rows["eval-1"]["ruleResults"])

        second = tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                            "SUCCEEDED", self.OUTPUT, None, None)
        assert second == {"evaluationId": "eval-1", "ruleName": "residual-bound", "finalized": False}
        # One rejected tracking write, and nothing else.
        assert len(tables.evaluation.update_item.call_args_list) == writes_after_first + 1
        assert json.loads(tables.evaluation_rows.rows["eval-1"]["ruleResults"]) == stored_after_first
        assert [r["ruleName"] for r in stored_after_first] == ["has-parent", "residual-bound"]
        assert tables.state.update_item.call_count == 1
        assert tables.audit.put_item.call_count == 1

    def test_a_redelivery_while_a_sibling_is_outstanding_appends_nothing(self):
        tables = self._tables(_two_rule_evaluation(),
                              tracking=(_tracking_row(), _tracking_row("other", "exec-2")))
        tables.run(store.complete_pipeline_rule, _two_rule_evaluation(), "residual-bound",
                   "SUCCEEDED", self.OUTPUT, None, None)
        results_after_first = json.loads(tables.evaluation_rows.rows["eval-1"]["ruleResults"])
        outcome = tables.run(store.complete_pipeline_rule, _two_rule_evaluation(), "residual-bound",
                             "SUCCEEDED", self.OUTPUT, None, None)
        assert outcome["finalized"] is False
        assert json.loads(tables.evaluation_rows.rows["eval-1"]["ruleResults"]) == results_after_first
        assert [r["ruleName"] for r in results_after_first] == ["has-parent", "residual-bound"]

    def test_a_measurement_out_of_tolerance_quarantines(self):
        tables = self._tables()
        output = dict(self.OUTPUT, measurements={"residual": 5})
        outcome = tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                             "SUCCEEDED", output, None, None)
        assert outcome["verdict"] == "quarantined"
        assert update_values(tables.state)[0]["complianceState"] == "quarantined"

    @pytest.mark.parametrize("status", ["FAILED", "ABORTED", "TIMED_OUT"])
    def test_a_non_succeeded_execution_fails_the_rule(self, status):
        tables = self._tables()
        outcome = tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                             status, None, None, None)
        assert outcome["finalized"] is True
        assert outcome["verdict"] == "quarantined"
        parent = update_values(tables.evaluation)[1]
        assert parent["violations"] == [f"Pipeline execution {status}"]

    def test_a_rule_that_was_not_pending_is_a_no_op(self):
        tables = self._tables()
        outcome = tables.run(store.complete_pipeline_rule, _pending_evaluation(), "other-rule",
                             "SUCCEEDED", self.OUTPUT, None, None)
        assert outcome == {"evaluationId": "eval-1", "ruleName": "other-rule", "finalized": False}
        tables.evaluation.update_item.assert_not_called()
        tables.state.update_item.assert_not_called()

    def test_an_outstanding_sibling_rule_defers_finalization(self):
        tables = self._tables(_two_rule_evaluation(),
                              tracking=(_tracking_row(), _tracking_row("other", "exec-2")))
        outcome = tables.run(store.complete_pipeline_rule, _two_rule_evaluation(), "residual-bound",
                             "SUCCEEDED", self.OUTPUT, None, None)
        assert outcome["finalized"] is False
        parent = update_values(tables.evaluation)[1]
        assert "status" not in parent
        assert parent["pipelineExecutions"][0]["status"] == "completed"
        assert parent["pipelineExecutions"][1]["status"] == "pending"
        assert [r["ruleName"] for r in json.loads(parent["ruleResults"])] == [
            "has-parent", "residual-bound"]
        # The progress write is guarded too, so it cannot land over a finalized evaluation.
        progress = tables.evaluation.update_item.call_args_list[1].kwargs
        assert progress["ExpressionAttributeValues"][":cond_status0"] == "pending_pipeline"
        tables.state.update_item.assert_not_called()
        tables.audit.put_item.assert_not_called()

    def test_the_sibling_rows_are_read_consistently_after_this_rules_write(self):
        tables = self._tables(_two_rule_evaluation(),
                              tracking=(_tracking_row(), _tracking_row("other", "exec-2")))
        tables.run(store.complete_pipeline_rule, _two_rule_evaluation(), "residual-bound",
                   "SUCCEEDED", self.OUTPUT, None, None)
        reads = [c.kwargs for c in tables.evaluation.get_item.call_args_list]
        assert reads
        assert [r["Key"]["evaluationId"] for r in reads] == ["eval-1#residual-bound", "eval-1#other"]
        assert all(r["ConsistentRead"] is True for r in reads)
        assert tables.evaluation.update_item.call_args_list[0].kwargs["Key"] == {
            "evaluationId": "eval-1#residual-bound"}

    def test_a_sibling_whose_tracking_row_already_completed_lets_this_one_finalize(self):
        other_result = [{"ruleName": "other", "ruleType": "pipeline", "enforcement": "quarantine",
                         "passed": True, "measured": {"drift": 0.5}}]
        tables = self._tables(_two_rule_evaluation(), tracking=(
            _tracking_row(),
            _tracking_row("other", "exec-2", status="completed", ruleResults=json.dumps(other_result))))
        outcome = tables.run(store.complete_pipeline_rule, _two_rule_evaluation(), "residual-bound",
                             "SUCCEEDED", self.OUTPUT, None, None)
        assert outcome["finalized"] is True
        assert outcome["verdict"] == "compliant"
        stored = tables.evaluation_rows.rows["eval-1"]
        assert [r["ruleName"] for r in json.loads(stored["ruleResults"])] == [
            "has-parent", "residual-bound", "other"]
        assert [e["status"] for e in stored["pipelineExecutions"]] == ["completed", "completed"]

    def test_two_rule_completions_keep_both_results_and_finalize_exactly_once(self):
        """The two callbacks each see the parent as it stood at launch. Neither snapshot carries the
        other's result, so a finalize built from a snapshot would drop one; the aggregate is read from
        the tracking rows instead, and the guarded finalize lands once."""
        tables = self._tables(_two_rule_evaluation(), previous_state="pending_evaluation",
                              tracking=(_tracking_row(), _tracking_row("other", "exec-2")))
        first = tables.run(store.complete_pipeline_rule, _two_rule_evaluation(), "residual-bound",
                           "SUCCEEDED", self.OUTPUT, None, None)
        second = tables.run(store.complete_pipeline_rule, _two_rule_evaluation(), "other",
                            "SUCCEEDED", self.OTHER_OUTPUT, None, None)
        assert first["finalized"] is False
        assert second["finalized"] is True
        assert second["verdict"] == "compliant"
        stored = tables.evaluation_rows.rows["eval-1"]
        assert stored["status"] == "completed"
        assert stored["verdict"] == "compliant"
        results = {r["ruleName"]: r for r in json.loads(stored["ruleResults"])}
        assert set(results) == {"has-parent", "residual-bound", "other"}
        assert results["residual-bound"]["measured"] == {"residual": 0.2}
        assert results["other"]["measured"] == {"drift": 0.5}
        finalizes = [u for u in update_values(tables.evaluation) if "verdict" in u]
        assert len(finalizes) == 1
        assert tables.state.update_item.call_count == 1
        assert tables.audit_events() == ["compliance_check"]

    def test_a_late_progress_write_cannot_overwrite_a_finalized_evaluation(self):
        """Callback A read sibling B as pending and goes to write progress; B finalized in between.
        A's progress write is rejected rather than replacing B's complete result set."""
        tables = self._tables(_two_rule_evaluation(), previous_state="pending_evaluation",
                              tracking=(_tracking_row(), _tracking_row("other", "exec-2")))
        real_update = tables.evaluation.update_item.side_effect

        def finalize_other_first(**kwargs):
            if kwargs["Key"] == {"evaluationId": "eval-1"} and \
                    ":cond_status0" in kwargs["ExpressionAttributeValues"] and \
                    tables.evaluation_rows.rows["eval-1"]["status"] == "pending_pipeline" and \
                    tables.evaluation_rows.rows["eval-1#other"]["status"] == "pending":
                # B's completion lands between A's read and A's write.
                tables.evaluation.update_item.side_effect = real_update
                tables.run(store.complete_pipeline_rule, _two_rule_evaluation(), "other",
                           "SUCCEEDED", self.OTHER_OUTPUT, None, None)
                tables.evaluation.update_item.side_effect = finalize_other_first
            return real_update(**kwargs)

        tables.evaluation.update_item.side_effect = finalize_other_first
        outcome = tables.run(store.complete_pipeline_rule, _two_rule_evaluation(), "residual-bound",
                             "SUCCEEDED", self.OUTPUT, None, None)
        assert outcome["finalized"] is False
        stored = tables.evaluation_rows.rows["eval-1"]
        assert stored["status"] == "completed"
        assert {r["ruleName"] for r in json.loads(stored["ruleResults"])} == {
            "has-parent", "residual-bound", "other"}
        assert [e["status"] for e in stored["pipelineExecutions"]] == ["completed", "completed"]
        assert tables.state.update_item.call_count == 1

    def test_losing_the_finalize_race_leaves_the_asset_state_alone(self):
        tables = self._tables()
        real_update = tables.evaluation.update_item.side_effect

        def finalized_elsewhere(**kwargs):
            if kwargs["Key"] == {"evaluationId": "eval-1"}:
                tables.evaluation_rows.rows["eval-1"]["status"] = "completed"
            return real_update(**kwargs)

        tables.evaluation.update_item.side_effect = finalized_elsewhere
        outcome = tables.run(store.complete_pipeline_rule, _pending_evaluation(),
                             "residual-bound", "SUCCEEDED", self.OUTPUT, None, None)
        assert outcome["finalized"] is False
        tables.state.update_item.assert_not_called()
        tables.audit.put_item.assert_not_called()

    def test_a_real_write_failure_still_surfaces(self):
        tables = self._tables()
        tables.evaluation.update_item.side_effect = RuntimeError("table unavailable")
        with pytest.raises(RuntimeError):
            tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                       "SUCCEEDED", self.OUTPUT, None, None)

    def test_finalizing_compliant_after_quarantine_audits_the_release(self):
        tables = self._tables(previous_state="quarantined")
        tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                   "SUCCEEDED", self.OUTPUT, None, None)
        assert tables.audit_events() == ["compliance_check", "quarantine_released"]


@pytest.mark.unit
class TestStatusCondition:

    def test_the_condition_placeholders_never_collide_with_the_update_aliases(self):
        """`to_update_expr` aliases values as `:v<n>`; a condition rendered by boto3's `Attr` would
        also use `:v0` and overwrite the first SET value with the condition's value."""
        expression, names, values = store.status_condition("pending", allow_absent=True)
        assert expression == "attribute_not_exists(#cond_status) OR #cond_status = :cond_status0"
        assert names == {"#cond_status": "status"}
        assert values == {":cond_status0": "pending"}
        update_names, update_values_, _ = REAL_TO_UPDATE_EXPR({"status": "completed", "x": 1})
        assert not set(update_names) & set(names)
        assert not set(update_values_) & set(values)

    def test_several_allowed_statuses(self):
        expression, _, values = store.status_condition("a", "b")
        assert expression == "#cond_status = :cond_status0 OR #cond_status = :cond_status1"
        assert values == {":cond_status0": "a", ":cond_status1": "b"}
