# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceEvaluationStore: one evaluation end to end against mock tables -- synchronous rules,
the pipeline-rule launch through the execute-workflow Lambda (input files resolved from the asset's
S3 listing against the workflow / pipeline / template config, files a workflow execution wrote left
out, request body per ExecuteWorkflowRequestV2Model, `manual` trigger, SYSTEM_USER cross-call), the
records it writes, the binding it leaves alone, rule errors (tooling failures that bear no verdict
unless the engine switch says so), the quarantine-exception semantics, the ordering rule that keeps an
older evaluation from overwriting a newer one's state, and the completion path the workflow callback
drives."""

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, DB, REAL_TO_UPDATE_EXPR, RULES_SCHEMA_BODY, SCHEMA, SYNC_RULES_SCHEMA_BODY, USER,
    FakeAssetStateTable, put_items, schema_row, update_values,
)
from common.compliance import listing
from handlers.compliance import complianceEvaluationStore as store
from models.compliance import PipelineRule
from models.executions import ExecuteWorkflowRequestV2Model

STORE = "handlers.compliance.complianceEvaluationStore"
# The attributes the evaluation writes when it records the asset's name on its compliance record.
NAME_ATTRIBUTES = {"assetName", "assetNameSort", "assetSearchText"}
# A compliance record that already carries the (unnamed) asset's name attributes, for the tests
# that pin what an evaluation reads and writes: the evaluation then writes no name.
NAMED_RECORD = listing.asset_name_attributes(ASSET, None)
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
    keys under ASSET_PREFIX (a key ending in '/' is a folder marker), and whose list_object_versions
    reports the same keys as current content versions with version id `v:<key>`."""
    client = MagicMock(name="s3_client")
    client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": ASSET_PREFIX + key.lstrip("/"), "Size": 1} for key in page]}
        for page in pages
    ]
    client.list_object_versions.return_value = {
        "Versions": [{"Key": ASSET_PREFIX + key.lstrip("/"), "VersionId": f"v:{key}",
                      "IsLatest": True} for page in pages for key in page],
        "DeleteMarkers": [],
    }
    return client


def conditional_check_failure():
    """The exception DynamoDB raises for a write whose ConditionExpression does not hold."""
    return store.dynamodb.meta.client.exceptions.ConditionalCheckFailedException(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "UpdateItem")


def metadata_schema_row(schema_name="Asset Schema", entity_type="assetMetadata", fields=(),
                        database_id=DB, restriction=None):
    """A metadata-schema-table row as the metadata schema service writes it: the entity-type
    composite key, the entity type, and the field list as a JSON string."""
    row = {
        "metadataSchemaId": f"id-{schema_name}",
        "databaseId": database_id,
        "databaseId:metadataEntityType": f"{database_id}:{entity_type}",
        "metadataSchemaEntityType": entity_type,
        "schemaName": schema_name,
        "fields": json.dumps({"fields": [
            {"metadataFieldKeyName": name, "metadataFieldValueType": type_, "required": required}
            for name, type_, required in fields]}),
    }
    if restriction is not None:
        row["fileKeyTypeRestriction"] = restriction
    return row


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
    pipeline (arity one, no filters), the rule's template when it names one, no asset-version file
    records, and no prior compliance state. `previous_row` is the whole prior asset-state row (it wins over `previous_state`). The
    evaluation table is a FakeEvaluationTable, so the completion path's conditional writes and its
    consistent re-reads of the tracking rows run against real rows."""

    def __init__(self, schema_body=RULES_SCHEMA_BODY, previous_state=None, previous_row=None,
                 metadata_keys=("owner",), parents=1, workflow_exists=True, pipeline_exists=True,
                 template_exists=True, workflow_config=None, pipeline_config=None,
                 template_overrides=None, asset_files=DEFAULT_ASSET_FILES, schema_version=1,
                 metadata_schema_found=True):
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
        self.metadata_schema.query.return_value = {"Items": [
            metadata_schema_row()]} if metadata_schema_found else {"Items": []}
        self.file_attributes = MagicMock(name="file_attribute_table")
        self.file_attributes.query.return_value = {"Items": []}
        self.link_metadata = MagicMock(name="asset_links_metadata_table")
        self.link_metadata.query.return_value = {"Items": []}
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
        self.file_versions = MagicMock(name="asset_file_versions_table")
        self.file_versions.query.return_value = {"Items": []}
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
            patch(f"{STORE}.asset_file_versions_table", self.file_versions),
            patch(f"{STORE}.metadata_schema_table", self.metadata_schema),
            patch(f"{STORE}.file_attribute_table", self.file_attributes),
            patch(f"{STORE}.asset_links_metadata_table", self.link_metadata),
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
        """The evaluation table's rows as they stand (every put with the updates that followed it),
        split into parent evaluations and pipeline-execution tracking rows."""
        rows = list(self.evaluation_rows.rows.values())
        parents = [r for r in rows if r.get("recordType") is None]
        tracking = [r for r in rows if r.get("recordType") == store.PIPELINE_EXECUTION_RECORD_TYPE]
        return parents, tracking

    def audit_events(self):
        return [r["eventType"] for r in put_items(self.audit)]

    def claim_and_outcome(self):
        """`(claim, outcome)`: the two asset-state writes of a `run_evaluation` that ran — the claim
        it made as it started (the pointers, `pending_pipeline`) and the write that applied its
        outcome for the same evaluation. The write of the asset's name onto a record without one
        (`_keep_record_name`) is not one of them."""
        writes = [write for write in update_values(self.state) if set(write) != NAME_ATTRIBUTES]
        assert len(writes) == 2, writes
        claim, outcome = writes
        assert claim["lastEvaluationStatus"] == store.engine.EVALUATION_STATUS_PENDING_PIPELINE
        assert claim["lastEvaluationId"] == outcome["lastEvaluationId"]
        return claim, outcome

    def outcome_state(self):
        """The asset-state write that applied a `run_evaluation`'s outcome, after its claim."""
        return self.claim_and_outcome()[1]


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

        state = tables.outcome_state()
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
        state = tables.outcome_state()
        assert "schemaName" not in state
        assert "schemaSource" not in state
        assert set(state) == {"complianceState", "lastEvaluationId", "lastEvaluatedAt",
                              "lastEvaluationStatus", "updatedAt", "quarantineReason",
                              "quarantineExecutionFailures"}
        assert state["lastEvaluationStatus"] == "completed"
        assert put_items(tables.audit)[0]["schemaName"] == "adhoc-schema"

    def test_a_failed_audit_write_does_not_fail_the_evaluation(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY)
        tables.audit.put_item.side_effect = RuntimeError("AccessDeniedException")
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "compliant"
        assert tables.outcome_state()["complianceState"] == "compliant"
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
        assert tables.outcome_state()["complianceState"] == "quarantined"
        assert tables.outcome_state()["quarantineReason"] == "parent: found 0 links, minimum is 1"
        published = notifications_aws.sns_client.publish.call_args.kwargs
        assert published["TopicArn"] == "arn:aws:sns:us-east-1:1:topic"
        assert "QUARANTINED" in published["Subject"]
        assert "has-parent" in published["Message"]

    def test_a_failed_warn_rule_is_non_compliant_without_notification(self, notifications_aws):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, metadata_keys=())
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["verdict"] == "non_compliant"
        assert tables.outcome_state()["complianceState"] == "non_compliant"
        notifications_aws.sns_client.publish.assert_not_called()

    def test_passing_after_quarantine_audits_the_release(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, previous_state="quarantined")
        tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert tables.audit_events() == ["compliance_check", "quarantine_released"]
        assert tables.outcome_state()["quarantineReason"] is None
        audit = put_items(tables.audit)[0]
        assert audit["previousState"] == "quarantined"
        assert audit["newState"] == "compliant"

    def test_the_metadata_schema_is_read_from_the_database_then_global(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY)
        tables.metadata_schema.query.side_effect = [
            {"Items": []},
            {"Items": [metadata_schema_row(database_id="GLOBAL",
                                           fields=[("units", "STRING", True)])]},
        ]
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        calls = [c.kwargs for c in tables.metadata_schema.query.call_args_list]
        assert calls, "the metadata schema was not looked up"
        assert {c["IndexName"] for c in calls} == {"DatabaseIdMetadataEntityTypeIndex"}
        partitions = [c["KeyConditionExpression"]._values[1] for c in calls]
        assert partitions == [f"{DB}:assetMetadata", "GLOBAL:assetMetadata"]
        metadata_result = next(r for r in result["ruleResults"] if r["ruleName"] == "owner-present")
        assert metadata_result["passed"] is False
        assert "units" in metadata_result["message"]

    def test_a_metadata_schema_that_is_not_found_is_a_tooling_error_not_a_pass(self):
        """A misspelt schemaName used to skip validateRequired / validateTypes and pass."""
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, metadata_schema_found=False)
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        metadata_result = next(r for r in result["ruleResults"] if r["ruleName"] == "owner-present")
        assert metadata_result["status"] == "error"
        assert metadata_result["message"] == "Metadata schema not found"
        assert result["errorRules"] == ["owner-present"]
        assert "evaluation_error" in tables.audit_events()

    def test_a_missing_schema_records_an_error_evaluation(self):
        """The evaluation row is `error`; the asset-state row gains the evaluation pointers and
        `lastEvaluationStatus: error` but keeps its state; an `evaluation_error` entry is audited."""
        tables = Tables(previous_row={"complianceState": "compliant", "schemaName": SCHEMA})
        tables.schema.query.return_value = {"Items": []}
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["verdict"] == "error"
        assert result["error"] == "Schema not found"
        assert result["complianceState"] == "compliant"
        assert result["hasRuleErrors"] is False
        parents, _ = tables.evaluation_records()
        assert parents[0]["status"] == "error"
        assert parents[0]["errorMessage"] == "Schema not found"
        state = tables.outcome_state()
        assert state["lastEvaluationId"] == parents[0]["evaluationId"]
        assert state["lastEvaluatedAt"] == parents[0]["evaluatedAt"]
        assert state["lastEvaluationStatus"] == "error"
        assert set(state) == {"lastEvaluationId", "lastEvaluatedAt", "lastEvaluationStatus",
                              "updatedAt"}
        assert tables.audit_events() == ["evaluation_error"]
        audit = put_items(tables.audit)[0]
        assert audit["evaluationId"] == parents[0]["evaluationId"]
        assert audit["schemaName"] == SCHEMA
        assert json.loads(audit["details"]) == {"ruleNames": [], "errorMessage": "Schema not found"}

    def test_an_untracked_asset_whose_schema_is_missing_reads_as_unknown(self):
        """No state row yet: the pointers are written on a row whose state is `unknown` — what a
        missing row reads as — so the record an error evaluation creates is a well-formed one."""
        tables = Tables()
        tables.schema.query.return_value = {"Items": []}
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["complianceState"] == "unknown"
        state = tables.outcome_state()
        assert state["lastEvaluationStatus"] == "error"
        assert state["complianceState"] == "unknown"
        assert "schemaName" not in state

    def test_a_legacy_json_schema_records_an_error_evaluation(self):
        tables = Tables(schema_body={"type": "object"})
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["error"] == "Schema is not vams-rules-v1 format"
        assert tables.audit_events() == ["evaluation_error"]
        assert tables.outcome_state()["lastEvaluationStatus"] == "error"

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
        assert tables.outcome_state()["complianceState"] == "pending_evaluation"

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
    def test_a_launch_that_fails_records_the_rule_as_errored_and_completes_synchronously(
            self, scenario):
        """A launch that could not start is a `status: error` result the verdict leaves out: the
        other rules pass, so the evaluation is compliant with `hasRuleErrors`, and an
        `evaluation_error` entry names the rule."""
        body = RULES_SCHEMA_BODY
        if scenario == "template-missing":
            rule = _rule(pipelineRef=dict(PIPELINE_RULE.pipelineRef.dict(), templateId="tmpl-1"))
            body = dict(RULES_SCHEMA_BODY, rules=dict(RULES_SCHEMA_BODY["rules"],
                                                      **{"residual-bound": rule.dict()}))
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
        assert result["verdict"] == "compliant"
        assert result["complianceState"] == "compliant"
        assert result["pipelineRulesPending"] == 0
        assert result["hasRuleErrors"] is True
        assert result["errorRules"] == ["residual-bound"]
        pipeline_result = next(r for r in result["ruleResults"] if r["ruleName"] == "residual-bound")
        assert pipeline_result["passed"] is False
        assert pipeline_result["status"] == "error"
        assert pipeline_result["message"] == "Pipeline execution could not be started"
        parents, tracking = tables.evaluation_records()
        assert tracking == []
        record = parents[0]
        assert record["status"] == "completed"
        assert record["verdict"] == "compliant"
        assert record["violations"] == []
        assert record["hasRuleErrors"] is True
        assert record["errorRules"] == ["residual-bound"]
        assert "pipelineRulesPending" not in record
        state = tables.outcome_state()
        assert state["complianceState"] == "compliant"
        assert state["lastEvaluationStatus"] == "completed"
        assert tables.audit_events() == ["evaluation_error", "compliance_check"]
        error_audit = put_items(tables.audit)[0]
        assert json.loads(error_audit["details"]) == {"ruleNames": ["residual-bound"],
                                                      "verdict": "compliant"}
        assert error_audit["evaluationId"] == record["evaluationId"]

    def test_with_the_enforcement_switch_on_a_launch_failure_applies_the_rules_enforcement(self):
        """`TOOLING_FAILURES_APPLY_ENFORCEMENT = True` restores the enforcement path: the errored
        quarantine rule quarantines the asset and its message is a violation."""
        tables = Tables(workflow_exists=False)
        with patch.object(store.engine, "TOOLING_FAILURES_APPLY_ENFORCEMENT", True):
            result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["verdict"] == "quarantined"
        assert result["complianceState"] == "quarantined"
        assert result["hasRuleErrors"] is True
        pipeline_result = next(r for r in result["ruleResults"] if r["ruleName"] == "residual-bound")
        assert pipeline_result["status"] == "error"
        parents, _ = tables.evaluation_records()
        assert parents[0]["violations"] == ["Pipeline execution could not be started"]
        assert parents[0]["hasRuleErrors"] is True
        state = tables.outcome_state()
        assert state["complianceState"] == "quarantined"
        assert state["quarantineReason"] == "Pipeline execution could not be started"
        assert tables.audit_events() == ["evaluation_error", "compliance_check"]

    @pytest.mark.parametrize("asset_files,message", [
        ((), store.INPUT_SELECTION_NOT_ONE_FILE),
        (("/a.stl", "/b.stl"), store.INPUT_SELECTION_NOT_ONE_FILE),
    ], ids=["no-match", "several-matches"])
    def test_a_selection_failure_is_the_rules_error_result_with_the_generic_message(
            self, asset_files, message):
        tables = Tables(asset_files=asset_files)
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        tables.lambda_client.invoke.assert_not_called()
        assert result["verdict"] == "compliant"
        assert result["hasRuleErrors"] is True
        pipeline_result = next(r for r in result["ruleResults"] if r["ruleName"] == "residual-bound")
        assert pipeline_result["passed"] is False
        assert pipeline_result["status"] == "error"
        assert pipeline_result["message"] == message
        for key in asset_files:
            assert key not in pipeline_result["message"]
        parents, tracking = tables.evaluation_records()
        assert tracking == []
        assert parents[0]["status"] == "completed"
        assert parents[0]["violations"] == []
        assert parents[0]["errorRules"] == ["residual-bound"]

    def test_an_evaluation_whose_every_rule_errored_has_no_verdict_and_leaves_the_state_alone(
            self, notifications_aws):
        """Only the pipeline rule, and its selection matches nothing: verdict `error`, evaluation
        `status: error`, the asset keeps its state (and its quarantine reason), the row's evaluation
        pointers carry `lastEvaluationStatus: error`, and the audit trail has the `evaluation_error`
        entry alone — no `compliance_check`, no notification."""
        body = dict(RULES_SCHEMA_BODY, rules={"residual-bound": PIPELINE_RULE.dict()})
        tables = Tables(schema_body=body, asset_files=(), previous_row={
            "complianceState": "quarantined", "quarantineReason": "earlier",
            "lastEvaluationId": "eval-0", "lastEvaluatedAt": "2025-01-01T00:00:00+00:00"})
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "error"
        assert result["complianceState"] == "quarantined"
        assert result["hasRuleErrors"] is True
        assert result["errorRules"] == ["residual-bound"]
        assert result["exceptionApplied"] is False
        assert "error" not in result
        parents, _ = tables.evaluation_records()
        record = parents[0]
        assert record["status"] == "error"
        assert record["verdict"] == "error"
        assert record["errorMessage"] == store.ALL_RULES_ERRORED_MESSAGE
        assert record["violations"] == []
        assert record["hasRuleErrors"] is True
        assert record["completedAt"] == record["evaluatedAt"]
        assert json.loads(record["ruleResults"])[0]["status"] == "error"
        state = tables.outcome_state()
        assert set(state) == {"lastEvaluationId", "lastEvaluatedAt", "lastEvaluationStatus",
                              "updatedAt"}
        assert state["lastEvaluationId"] == record["evaluationId"]
        assert state["lastEvaluationStatus"] == "error"
        assert tables.audit_events() == ["evaluation_error"]
        assert json.loads(put_items(tables.audit)[0]["details"]) == {
            "ruleNames": ["residual-bound"], "verdict": "error"}
        notifications_aws.sns_client.publish.assert_not_called()

    def test_an_all_errored_evaluation_neither_applies_nor_supersedes_an_exception(self):
        body = dict(RULES_SCHEMA_BODY, rules={"residual-bound": PIPELINE_RULE.dict()})
        tables = Tables(schema_body=body, asset_files=(),
                        previous_row=_exception_row(schema_name="other-schema"))
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "error"
        assert result["complianceState"] == "exception"
        state = tables.outcome_state()
        assert not set(state) & set(store.engine.EXCEPTION_FIELDS)
        assert "complianceState" not in state
        assert tables.audit_events() == ["evaluation_error"]

    def test_a_pending_evaluation_records_its_launch_errors_and_the_pending_status(self):
        """Two pipeline rules, one launched, one refused: the evaluation is pending with the errored
        rule already on the row, and the asset-state row says `pending_pipeline`."""
        body = dict(RULES_SCHEMA_BODY, rules={
            "residual-bound": PIPELINE_RULE.dict(), "other": OTHER_RULE.dict()})
        tables = Tables(schema_body=body)
        tables.pipeline.get_item.side_effect = lambda **kw: (
            {"Item": {"pipelineId": "pipe-1"}} if kw["Key"]["pipelineId"] == "pipe-1" else {})
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["verdict"] == "pending_pipeline"
        assert result["pipelineRulesPending"] == 1
        assert result["hasRuleErrors"] is True
        assert result["errorRules"] == ["other"]
        parents, _ = tables.evaluation_records()
        assert parents[0]["status"] == "pending_pipeline"
        assert parents[0]["errorRules"] == ["other"]
        state = tables.outcome_state()
        assert state["complianceState"] == "pending_evaluation"
        assert state["lastEvaluationStatus"] == "pending_pipeline"
        assert tables.audit_events() == ["evaluation_error", "compliance_check"]

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
        tables = Tables(schema_body=body, previous_row=NAMED_RECORD)
        tables.pipeline.get_item.side_effect = lambda **kw: {"Item": {"pipelineId": kw["Key"]["pipelineId"]}}
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["pipelineRulesPending"] == 2
        assert tables.s3_client.get_paginator.return_value.paginate.call_count == 1
        assert tables.asset.get_item.call_count == 1
        # One HEAD per surviving file for the whole evaluation, not one per rule.
        assert tables.s3_client.head_object.call_count == 1

    def test_a_file_a_workflow_execution_wrote_is_left_out_of_a_matching_selection(self):
        """The pipeline's own `.glb` output sits beside the `.stl` source; both pass the filters, so
        only the provenance read (a HEAD on each survivor) keeps the selection to the source."""
        tables = Tables(asset_files=("/model.stl", "/model.glb"),
                        pipeline_config={"inputFileArity": "one",
                                         "inputFileFilters": {"allow": MODEL_EXTENSIONS}})
        _script_provenance(tables, {"/model.glb": "workflowExecution", "/model.stl": "upload"})
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["verdict"] == "pending_pipeline"
        launch_body = json.loads(json.loads(tables.lambda_client.invoke.call_args.kwargs["Payload"])["body"])
        assert launch_body["inputFiles"] == _inputs("/model.stl")
        head_calls = tables.s3_client.head_object.call_args_list
        assert head_calls
        heads = sorted(c.kwargs["Key"] for c in head_calls)
        assert heads == [ASSET_PREFIX + "model.glb", ASSET_PREFIX + "model.stl"]
        assert all(c.kwargs["Bucket"] == BUCKET_NAME for c in head_calls)

    def test_provenance_is_read_only_for_the_files_that_survive_the_filter_chain(self):
        tables = Tables(asset_files=("/model.stl", "/notes.txt", "/model.glb"),
                        pipeline_config={"inputFileArity": "one",
                                         "inputFileFilters": {"allow": MODEL_EXTENSIONS}})
        _script_provenance(tables, {"/model.glb": "workflowExecution"})
        tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        heads = sorted(c.kwargs["Key"] for c in tables.s3_client.head_object.call_args_list)
        assert heads == [ASSET_PREFIX + "model.glb", ASSET_PREFIX + "model.stl"]

    def test_a_file_whose_provenance_cannot_be_read_stays_selected(self):
        tables = Tables(asset_files=("/model.stl", "/model.glb"),
                        pipeline_config={"inputFileArity": "one",
                                         "inputFileFilters": {"allow": MODEL_EXTENSIONS}})
        tables.s3_client.head_object.side_effect = RuntimeError("AccessDenied")
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        tables.lambda_client.invoke.assert_not_called()
        pipeline_result = next(r for r in result["ruleResults"] if r["ruleName"] == "residual-bound")
        assert pipeline_result["message"] == store.INPUT_SELECTION_NOT_ONE_FILE
        assert tables.s3_client.head_object.call_count == 2

    def test_an_explicit_selection_sends_a_workflow_written_file_as_given_without_a_head(self):
        rule = _rule(inputFiles={"mode": "explicit", "keys": ["/model.glb"]})
        body = dict(RULES_SCHEMA_BODY, rules={"residual-bound": rule.dict()})
        tables = Tables(schema_body=body, asset_files=("/model.stl", "/model.glb"),
                        pipeline_config={"inputFileArity": "one",
                                         "inputFileFilters": {"allow": MODEL_EXTENSIONS}})
        _script_provenance(tables, {"/model.glb": "workflowExecution"})
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["verdict"] == "pending_pipeline"
        launch_body = json.loads(json.loads(tables.lambda_client.invoke.call_args.kwargs["Payload"])["body"])
        assert launch_body["inputFiles"] == _inputs("/model.glb")
        tables.s3_client.head_object.assert_not_called()

    def test_the_object_change_source_read_is_a_head_on_the_current_version(self):
        tables = Tables()
        _script_provenance(tables, {"/model.stl": "workflowExecution"})
        assert tables.run(store.object_change_source, BUCKET_NAME, ASSET_PREFIX + "model.stl") == \
            "workflowExecution"
        assert tables.s3_client.head_object.call_args.kwargs == {
            "Bucket": BUCKET_NAME, "Key": ASSET_PREFIX + "model.stl"}
        tables.s3_client.head_object.side_effect = None
        tables.s3_client.head_object.return_value = {"Metadata": {}}
        assert tables.run(store.object_change_source, BUCKET_NAME, "k") == ""
        tables.s3_client.head_object.return_value = {}
        assert tables.run(store.object_change_source, BUCKET_NAME, "k") == ""


def _script_provenance(tables, sources):
    """Script `head_object` on the tables' S3 client to answer `vams-changesource` per asset-relative
    key (`sources`); a key not named carries no provenance."""
    def head_object(Bucket, Key, **_):
        relative = "/" + Key[len(ASSET_PREFIX):] if Key.startswith(ASSET_PREFIX) else Key
        source = sources.get(relative)
        return {"Metadata": {"vams-changesource": source} if source else {}}
    tables.s3_client.head_object.side_effect = head_object


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

    # --- workflow-written files ---

    def _resolve_with_provenance(self, rule, files, sources, **kwargs):
        provenance = MagicMock(name="file_change_source",
                               side_effect=lambda key: sources.get(key, ""))
        listing = MagicMock(name="asset_file_keys", return_value=files)
        inputs, error = store.resolve_pipeline_rule_inputs(
            rule, DB, ASSET, kwargs.get("workflow", BARE_WORKFLOW),
            kwargs.get("pipeline", CONVERSION_PIPELINE), kwargs.get("template"), listing,
            provenance)
        return inputs, error, provenance

    def test_matching_leaves_out_a_file_a_workflow_execution_wrote(self):
        inputs, error, provenance = self._resolve_with_provenance(
            PIPELINE_RULE, ["/model.stl", "/model.glb"], {"/model.glb": "workflowExecution"})
        assert error is None
        assert inputs == _inputs("/model.stl")
        assert {c.args[0] for c in provenance.call_args_list} == {"/model.stl", "/model.glb"}

    @pytest.mark.parametrize("source", ["upload", "direct", ""], ids=["upload", "direct", "unknown"])
    def test_matching_keeps_a_file_of_any_other_or_unreadable_provenance(self, source):
        inputs, error, _ = self._resolve_with_provenance(
            PIPELINE_RULE, ["/model.stl"], {"/model.stl": source})
        assert error is None
        assert inputs == _inputs("/model.stl")

    def test_provenance_is_read_for_the_survivors_of_the_filter_chain_only(self):
        rule = _rule(inputFiles={"mode": "matching", "filter": ["*.stl"]})
        inputs, error, provenance = self._resolve_with_provenance(
            rule, ["/model.stl", "/notes.txt", "/model.glb", "/x.obj"], {})
        assert error is None
        assert inputs == _inputs("/model.stl")
        assert [c.args[0] for c in provenance.call_args_list] == ["/model.stl"]

    def test_leaving_out_every_match_is_the_not_one_file_refusal(self):
        inputs, error, _ = self._resolve_with_provenance(
            PIPELINE_RULE, ["/model.glb"], {"/model.glb": "workflowExecution"})
        assert inputs is None
        assert error == store.INPUT_SELECTION_NOT_ONE_FILE

    def test_explicit_keys_are_sent_as_given_whatever_their_provenance(self):
        rule = _rule(inputFiles={"mode": "explicit", "keys": ["/model.glb"]})
        inputs, error, provenance = self._resolve_with_provenance(
            rule, ["/model.stl", "/model.glb"], {"/model.glb": "workflowExecution"})
        assert error is None
        assert inputs == _inputs("/model.glb")
        provenance.assert_not_called()

    def test_whole_asset_reads_no_provenance(self):
        rule = _rule(inputFiles={"mode": "wholeAsset"})
        inputs, error, provenance = self._resolve_with_provenance(
            rule, ["/model.glb"], {"/model.glb": "workflowExecution"},
            workflow=_workflow(arity="one", whole_asset=True),
            pipeline=_pipeline(arity="one", whole_asset=True))
        assert error is None
        assert inputs == _inputs("/")
        provenance.assert_not_called()

    def test_without_a_provenance_reader_the_filter_chain_alone_decides(self):
        inputs, error, _ = self._resolve(PIPELINE_RULE, ["/model.stl", "/model.glb"])
        assert inputs is None
        assert error == store.INPUT_SELECTION_NOT_ONE_FILE

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

    def test_the_bucket_rows_region_is_recorded_for_the_bucket_name(self):
        """The Region-routing client signs a call by the bucket name; resolving the asset location
        records the bucket row's Region so a bucket in another Region is reached there."""
        # The `common.s3` the store bound at import (another suite may have re-registered the name).
        bucket_region_for_name = store.bucket_region_fields.__globals__["bucket_region_for_name"]
        tables = Tables()
        tables.buckets.query.return_value = {"Items": [
            {"bucketId": BUCKET_ID, "bucketName": "remote-asset-bucket", "baseAssetsPrefix": "/",
             "bucketRegion": "eu-west-1"}]}
        self._list(tables)
        assert bucket_region_for_name("remote-asset-bucket") == "eu-west-1"


# A vams-rules-v1 body carrying one rule of each of the file-structure, version-coverage and
# asset-attribute types.
FILE_RULES_SCHEMA_BODY = {
    "schemaFormat": "vams-rules-v1",
    "rules": {
        "model-present": {
            "ruleType": "fileStructure", "enforcement": "quarantine",
            "checks": [{"name": "glb", "requiredPaths": [r"\.glb$"], "forbiddenPaths": [r"\.tmp$"]}],
        },
        "files-versioned": {
            "ruleType": "versionCoverage", "enforcement": "warn",
            "checks": [{"name": "all", "excludePaths": [r"\.previewFile\."]}],
        },
        "naming": {
            "ruleType": "assetAttributes", "enforcement": "inform",
            "checks": [{"name": "name", "field": "assetName", "matches": "^[A-Z]"},
                       {"name": "tagged", "field": "tags", "requiredTags": ["engineering"]}],
        },
    },
}


def _versions_listing(*entries, delete_markers=()):
    """A list_object_versions response under ASSET_PREFIX: `entries` are (key, versionId, isLatest)."""
    return {
        "Versions": [{"Key": ASSET_PREFIX + key.lstrip("/"), "VersionId": version_id,
                      "IsLatest": latest} for key, version_id, latest in entries],
        "DeleteMarkers": [{"Key": ASSET_PREFIX + key.lstrip("/"), "VersionId": "dm",
                           "IsLatest": True} for key in delete_markers],
    }


def _file_version_rows(*pairs):
    """Asset file-versions rows: (fileKey as stored, versionId)."""
    return [{"databaseId:assetId:assetVersionId": f"{DB}:{ASSET}:1", "fileKey": key,
             "databaseId:assetId": f"{DB}:{ASSET}", "versionId": version_id}
            for key, version_id in pairs]


@pytest.mark.unit
class TestCurrentFiles:

    def test_the_latest_content_version_of_every_key_is_listed(self):
        tables = Tables()
        tables.s3_client.list_object_versions.return_value = _versions_listing(
            ("models/part.glb", "v2", True), ("models/part.glb", "v1", False),
            ("models/", "vf", True), ("legacy.obj", "null", True), ("old.stl", "v0", False),
            delete_markers=("old.stl",))
        files = tables.run(store.get_current_files, DB, ASSET)
        assert files == [store.engine.CurrentFile("/legacy.obj", None),
                         store.engine.CurrentFile("/models/part.glb", "v2")]
        assert tables.s3_client.list_object_versions.call_args.kwargs["Bucket"] == BUCKET_NAME
        assert tables.s3_client.list_object_versions.call_args.kwargs["Prefix"] == ASSET_PREFIX

    def test_a_row_the_caller_already_read_is_not_read_again(self):
        tables = Tables()
        asset = tables.asset.get_item.return_value["Item"]
        tables.run(store.get_current_files, DB, ASSET, asset=asset)
        tables.asset.get_item.assert_not_called()

    def test_an_unresolvable_location_or_a_failed_listing_is_none(self):
        tables = Tables()
        tables.buckets.query.return_value = {"Items": []}
        assert tables.run(store.get_current_files, DB, ASSET) is None
        tables = Tables()
        tables.s3_client.list_object_versions.side_effect = RuntimeError("AccessDenied")
        assert tables.run(store.get_current_files, DB, ASSET) is None


@pytest.mark.unit
class TestVersionedFileKeys:

    def test_every_asset_version_row_is_read_through_the_gsi_to_exhaustion(self):
        tables = Tables()
        tables.file_versions.query.side_effect = [
            {"Items": _file_version_rows(("models/part.glb", "v1")),
             "LastEvaluatedKey": {"k": 1}},
            {"Items": _file_version_rows(("/models/part.glb", "v2"), ("legacy.obj", "null"),
                                         ("readme.txt", None))},
        ]
        keys = tables.run(store.get_versioned_file_keys, DB, ASSET)
        assert keys == {("/models/part.glb", "v1"), ("/models/part.glb", "v2")}
        first, second = tables.file_versions.query.call_args_list
        assert first.kwargs["IndexName"] == "databaseIdAssetIdIndex"
        assert first.kwargs["KeyConditionExpression"]._values[1] == f"{DB}:{ASSET}"
        assert second.kwargs["ExclusiveStartKey"] == {"k": 1}


@pytest.mark.unit
class TestFileAndAttributeRules:

    def _tables(self, files=("/models/part.glb",), version_rows=(("models/part.glb", "v:/models/part.glb"),),
                asset=None, **kwargs):
        tables = Tables(schema_body=FILE_RULES_SCHEMA_BODY, asset_files=files, **kwargs)
        tables.asset.get_item.return_value = {"Item": {
            "databaseId": DB, "assetId": ASSET, "bucketId": BUCKET_ID,
            "assetLocation": {"Key": ASSET_PREFIX}, "assetName": "Turbine",
            "tags": ["engineering"], **(asset or {})}}
        tables.file_versions.query.return_value = {"Items": _file_version_rows(*version_rows)}
        return tables

    def test_an_evaluation_without_these_rule_types_lists_no_files_and_reads_no_versions(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, previous_row=NAMED_RECORD)
        tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        tables.s3_client.list_object_versions.assert_not_called()
        tables.s3_client.get_paginator.assert_not_called()
        tables.file_versions.query.assert_not_called()
        tables.asset.get_item.assert_not_called()

    def test_an_attribute_rule_alone_reads_the_asset_row_but_lists_no_files(self):
        body = {"schemaFormat": "vams-rules-v1",
                "rules": {"naming": FILE_RULES_SCHEMA_BODY["rules"]["naming"]}}
        tables = self._tables()
        tables.schema.query.return_value = {"Items": [schema_row(body=body, version=1)]}
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "compliant"
        assert tables.asset.get_item.call_count == 1
        tables.s3_client.list_object_versions.assert_not_called()
        tables.file_versions.query.assert_not_called()

    def test_all_three_pass_with_one_asset_read_and_one_listing(self):
        tables = self._tables()
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "compliant"
        assert {r["ruleName"]: r["passed"] for r in result["ruleResults"]} == {
            "model-present": True, "files-versioned": True, "naming": True}
        assert tables.asset.get_item.call_count == 1
        assert tables.s3_client.list_object_versions.call_count == 1
        assert tables.file_versions.query.call_count == 1
        parents, _ = tables.evaluation_records()
        assert parents[0]["status"] == "completed"

    def test_a_fresh_upload_not_yet_versioned_fails_the_coverage_rule(self):
        tables = self._tables(files=("/models/part.glb", "/models/new.glb"))
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "non_compliant"
        coverage = next(r for r in result["ruleResults"] if r["ruleName"] == "files-versioned")
        assert coverage["passed"] is False
        assert coverage["measured"] == {"fileCount": 2, "uncoveredCount": 1,
                                        "uncovered": ["/models/new.glb"]}

    def test_a_missing_model_file_quarantines(self, notifications_aws):
        tables = self._tables(files=("/models/part.stl",),
                              version_rows=(("models/part.stl", "v:/models/part.stl"),))
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "quarantined"
        assert tables.outcome_state()["quarantineReason"] == (
            r"glb: no file matches required path pattern(s) \.glb$")

    def test_a_listing_that_fails_errors_the_file_rules_and_leaves_the_attribute_verdict(self):
        tables = self._tables()
        tables.s3_client.list_object_versions.side_effect = RuntimeError("AccessDenied")
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        statuses = {r["ruleName"]: r["status"] for r in result["ruleResults"]}
        assert statuses == {"model-present": "error", "files-versioned": "error",
                            "naming": "evaluated"}
        assert result["verdict"] == "compliant"
        assert result["errorRules"] == ["model-present", "files-versioned"]

    def test_a_missing_asset_row_errors_every_rule_of_these_types(self):
        tables = self._tables()
        tables.asset.get_item.return_value = {}
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert {r["status"] for r in result["ruleResults"]} == {"error"}
        assert result["verdict"] == "error"
        tables.s3_client.list_object_versions.assert_not_called()


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
        state = tables.outcome_state()
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
        state = tables.outcome_state()
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
        state = tables.outcome_state()
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
        assert not set(tables.outcome_state()) & set(store.engine.EXCEPTION_FIELDS)
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

    def test_a_tracking_row_whose_parent_is_absent_raises(self):
        """The tracking row proves the execution is a compliance execution, so a missing parent is
        a failure to retry, not an execution to ignore."""
        tables = Tables()
        tables.evaluation.query.return_value = {"Items": [
            {"recordType": "pipelineExecution", "parentEvaluationId": "eval-x",
             "pipelineRuleName": "r"}]}
        with pytest.raises(store.ComplianceExecutionUnresolved):
            tables.run(store.resolve_pipeline_execution, "exec-1")


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
        status_alias = next(alias for alias, name in names.items()
                            if name == "status" and alias != "#cond_status")
        status_value = tracking_write["UpdateExpression"].split(f"{status_alias} = ", 1)[1]
        status_value = status_value.split(",", 1)[0].strip()
        assert values[status_value] == "completed"

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
        # The parent is re-read after this rule's write, then every sibling's tracking row.
        assert [r["Key"]["evaluationId"] for r in reads] == [
            "eval-1", "eval-1#residual-bound", "eval-1#other"]
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

    def test_the_finalized_state_row_points_at_the_evaluation_and_its_start(self):
        """`lastEvaluatedAt` is the evaluation's `evaluatedAt` — the instant its inputs were read —
        not the callback's time; `lastEvaluationStatus` is `completed`."""
        tables = self._tables(previous_row={
            "complianceState": "pending_evaluation", "lastEvaluationId": "eval-1",
            "lastEvaluatedAt": "2026-01-01T00:00:00+00:00", "lastEvaluationStatus": "pending_pipeline"})
        tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                   "SUCCEEDED", self.OUTPUT, None, None)
        state = update_values(tables.state)[0]
        assert state["lastEvaluationId"] == "eval-1"
        assert state["lastEvaluatedAt"] == "2026-01-01T00:00:00+00:00"
        assert state["lastEvaluationStatus"] == "completed"
        assert state["complianceState"] == "compliant"
        assert state["updatedAt"] != state["lastEvaluatedAt"]
        finalized = tables.evaluation_rows.rows["eval-1"]
        assert finalized["hasRuleErrors"] is False
        assert "errorRules" not in finalized

    def test_a_callback_landing_after_a_newer_evaluation_finalizes_its_row_only(self):
        """A synchronous evaluation begun after eval-1 already owns the asset-state row: the
        callback completes eval-1's evaluation row and leaves the row, the audit trail and the
        notification alone."""
        tables = self._tables(previous_row={
            "complianceState": "quarantined", "quarantineReason": "newer verdict",
            "lastEvaluationId": "eval-2", "lastEvaluatedAt": "2026-01-01T00:00:30+00:00",
            "lastEvaluationStatus": "completed"})
        outcome = tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                             "SUCCEEDED", self.OUTPUT, None, None)
        assert outcome["finalized"] is True
        assert outcome["verdict"] == "compliant"
        finalized = tables.evaluation_rows.rows["eval-1"]
        assert finalized["status"] == "completed"
        assert finalized["verdict"] == "compliant"
        tables.state.update_item.assert_not_called()
        tables.audit.put_item.assert_not_called()

    def test_a_callback_newer_than_the_rows_evaluation_writes_the_state(self):
        tables = self._tables(previous_row={
            "complianceState": "compliant", "lastEvaluationId": "eval-0",
            "lastEvaluatedAt": "2025-12-31T23:59:59+00:00", "lastEvaluationStatus": "completed"})
        outcome = tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                             "SUCCEEDED", dict(self.OUTPUT, measurements={"residual": 5}), None, None)
        assert outcome["complianceState"] == "quarantined"
        state = update_values(tables.state)[0]
        assert state["lastEvaluationId"] == "eval-1"
        assert state["complianceState"] == "quarantined"
        assert tables.audit_events() == ["compliance_check"]

    def test_the_rows_own_evaluation_always_writes_the_state(self):
        """The row already points at eval-1 (written when its pipeline rule launched); the row's
        `lastEvaluatedAt` being later than eval-1's start does not stop eval-1 from finalizing it."""
        tables = self._tables(previous_row={
            "complianceState": "pending_evaluation", "lastEvaluationId": "eval-1",
            "lastEvaluatedAt": "2026-01-01T00:05:00+00:00"})
        outcome = tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                             "SUCCEEDED", self.OUTPUT, None, None)
        assert outcome["complianceState"] == "compliant"
        assert update_values(tables.state)[0]["complianceState"] == "compliant"

    def test_a_row_without_an_evaluation_yet_is_written(self):
        tables = self._tables(previous_row={"complianceState": "pending_evaluation",
                                            "schemaName": SCHEMA})
        tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                   "SUCCEEDED", self.OUTPUT, None, None)
        assert update_values(tables.state)[0]["complianceState"] == "compliant"


@pytest.mark.unit
class TestParentRowBeforeLaunch:
    """The evaluation row exists before any pipeline execution is launched, so a completion event
    that lands during the launches finds its parent; the rules not yet launched keep the evaluation
    open until the launch outcome is recorded, and `run_evaluation` finalizes an evaluation whose
    every launched rule reported while it was still launching."""

    TWO_RULES = dict(RULES_SCHEMA_BODY, rules={
        "residual-bound": PIPELINE_RULE.dict(), "other": OTHER_RULE.dict()})

    @staticmethod
    def _record_invocations(tables):
        """Interleave the evaluation table's writes and the execute-workflow invokes in one list."""
        events = []
        real_put, real_update = tables.evaluation.put_item.side_effect, tables.evaluation.update_item.side_effect
        real_invoke = tables.lambda_client.invoke.side_effect or tables.lambda_client.invoke.return_value

        def put(Item, **kw):
            events.append(("put", Item["evaluationId"], Item.get("status"),
                           Item.get("pipelineExecutions")))
            return real_put(Item, **kw)

        def update(Key, **kw):
            events.append(("update", Key["evaluationId"]))
            return real_update(Key=Key, **kw)

        def invoke(**kw):
            events.append(("invoke",))
            return real_invoke(**kw) if callable(real_invoke) else real_invoke

        tables.evaluation.put_item.side_effect = put
        tables.evaluation.update_item.side_effect = update
        tables.lambda_client.invoke.side_effect = invoke
        return events

    def test_the_parent_row_is_written_before_the_first_launch(self):
        tables = Tables(schema_body=self.TWO_RULES)
        tables.pipeline.get_item.side_effect = lambda **kw: {"Item": {"pipelineId": kw["Key"]["pipelineId"]}}
        events = self._record_invocations(tables)
        tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)

        assert events
        first_invoke = events.index(("invoke",))
        parent_puts = [e for e in events[:first_invoke] if e[0] == "put" and "#" not in e[1]]
        assert len(parent_puts) == 1
        _, _, status, executions = parent_puts[0]
        assert status == "pending_pipeline"
        assert executions == [{"ruleName": "residual-bound", "status": "starting"},
                              {"ruleName": "other", "status": "starting"}]
        # The launch outcome is an update of that row after the last launch, never a second put.
        parent_writes_after = [e for e in events[first_invoke:] if len(e) > 1 and "#" not in e[1]]
        assert parent_writes_after
        assert all(e[0] == "update" for e in parent_writes_after)

        parents, tracking = tables.evaluation_records()
        parent = parents[0]
        assert parent["executionId"] == "exec-1"
        assert parent["pipelineExecutions"] == [
            {"ruleName": "residual-bound", "executionId": "exec-1", "status": "pending"},
            {"ruleName": "other", "executionId": "exec-1", "status": "pending"}]
        assert [r["ruleName"] for r in json.loads(parent["pipelineRulesPending"])] == [
            "residual-bound", "other"]
        assert len(tracking) == 2

    def test_a_launch_failure_is_recorded_as_not_started_beside_the_launched_rule(self):
        tables = Tables(schema_body=self.TWO_RULES)
        tables.pipeline.get_item.side_effect = lambda **kw: (
            {"Item": {"pipelineId": kw["Key"]["pipelineId"]}}
            if kw["Key"]["pipelineId"] == "pipe-1" else {})
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "pending_pipeline"
        assert result["pipelineRulesPending"] == 1
        assert result["errorRules"] == ["other"]
        parent = tables.evaluation_records()[0][0]
        assert parent["pipelineExecutions"] == [
            {"ruleName": "residual-bound", "executionId": "exec-1", "status": "pending"},
            {"ruleName": "other", "status": "not_started"}]
        assert [r["ruleName"] for r in json.loads(parent["pipelineRulesPending"])] == ["residual-bound"]
        assert parent["hasRuleErrors"] is True and parent["errorRules"] == ["other"]

    def test_a_completion_that_lands_during_the_launches_is_held_until_the_launch_outcome(self):
        """The first rule's completion arrives while the second is still `starting`: the callback
        records the rule but cannot finalize (a rule is unlaunched) and writes no progress over the
        starting list; `run_evaluation` then finalizes once the second launch fails, so the
        evaluation is never left pending with every launched rule reported."""
        tables = Tables(schema_body=self.TWO_RULES)
        tables.pipeline.get_item.side_effect = lambda **kw: (
            {"Item": {"pipelineId": kw["Key"]["pipelineId"]}}
            if kw["Key"]["pipelineId"] == "pipe-1" else {})
        callback_outcomes = []

        def put_then_complete(Item, **kw):
            # The completion lands the moment the first rule's tracking row exists, before the
            # launch loop reaches the second rule.
            tables.evaluation_rows._put_item(Item, **kw)
            if Item.get("recordType") == "pipelineExecution" \
                    and Item["pipelineRuleName"] == "residual-bound":
                parent = tables.evaluation_rows.rows[Item["parentEvaluationId"]]
                callback_outcomes.append(store.complete_pipeline_rule(
                    parent, "residual-bound", "SUCCEEDED", TestCompletePipelineRule.OUTPUT,
                    None, None))
            return {}

        tables.evaluation.put_item.side_effect = put_then_complete
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)

        assert callback_outcomes and callback_outcomes[0]["finalized"] is False
        assert result["verdict"] == "compliant"
        assert result["complianceState"] == "compliant"
        assert result["pipelineRulesPending"] == 0
        parent = tables.evaluation_records()[0][0]
        assert parent["status"] == "completed"
        assert parent["verdict"] == "compliant"
        assert parent["pipelineExecutions"] == [
            {"ruleName": "residual-bound", "executionId": "exec-1", "status": "completed"},
            {"ruleName": "other", "status": "not_started"}]
        results = {r["ruleName"]: r for r in json.loads(parent["ruleResults"])}
        assert results["residual-bound"]["passed"] is True
        assert results["other"]["status"] == "error"
        states = [u["complianceState"] for u in update_values(tables.state) if "complianceState" in u]
        # The asset had no state row: the claim creates it as `unknown`, then the pending outcome
        # and the finalized one follow.
        assert states == ["unknown", "pending_evaluation", "compliant"]


def _record_calls(tables, notify=None):
    """Wrap the store's tables, the execute-workflow invoke and (when given) the quarantine notifier
    so every call lands in one ordered list of `(label, detail)`; each call still does what it did.
    The detail is the read's ConsistentRead, the state write's `lastEvaluationStatus` or the audit
    entry's event type."""
    calls = []

    def wrap(mock, label, detail=lambda args, kwargs: None):
        original_effect, original_return = mock.side_effect, mock.return_value

        def effect(*args, **kwargs):
            calls.append((label, detail(args, kwargs)))
            return original_effect(*args, **kwargs) if original_effect else original_return
        mock.side_effect = effect

    def written_status(args, kwargs):
        names = kwargs["ExpressionAttributeNames"]
        values = kwargs["ExpressionAttributeValues"]
        for clause in kwargs["UpdateExpression"].replace("SET ", "", 1).split(", "):
            name_token, value_token = clause.split(" = ", 1)
            if names[name_token] == "lastEvaluationStatus":
                return values[value_token]
        return None

    wrap(tables.state.get_item, "state.get", lambda a, kw: kw.get("ConsistentRead", False))
    wrap(tables.state.update_item, "state.update", written_status)
    wrap(tables.schema.query, "schema.query")
    wrap(tables.metadata.query, "metadata.query")
    wrap(tables.evaluation.put_item, "evaluation.put")
    wrap(tables.lambda_client.invoke, "invoke")
    wrap(tables.audit.put_item, "audit", lambda a, kw: kw["Item"]["eventType"])
    if notify is not None:
        wrap(notify, "notify")
    return calls


@pytest.mark.unit
class TestStartClaim:
    """`run_evaluation` claims the asset-state row before it reads its schema, evaluates a rule or
    launches an execution: the pointers move to the evaluation with `pending_pipeline`, nothing else
    on the row changes, and nothing is audited or notified until the outcome is applied."""

    PREVIOUS = {"complianceState": "compliant", "schemaName": SCHEMA, "schemaSource": "database",
                "lastEvaluationId": "eval-older", "lastEvaluatedAt": "2026-01-01T00:00:00+00:00",
                "lastEvaluationStatus": "completed", **NAMED_RECORD}

    def test_the_claim_precedes_every_rule_read_and_the_launch(self):
        tables = Tables(previous_row=self.PREVIOUS)
        calls = _record_calls(tables)
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "pending_pipeline"

        labels = [label for label, _ in calls]
        claim = labels.index("state.update")
        assert calls[:claim + 1] == [("state.get", True), ("state.update", "pending_pipeline")]
        assert claim < labels.index("schema.query") < labels.index("metadata.query") \
            < labels.index("evaluation.put") < labels.index("invoke")
        outcome = len(labels) - 1 - labels[::-1].index("state.update")
        assert outcome > labels.index("invoke")
        assert calls[outcome] == ("state.update", "pending_pipeline")
        # The outcome is judged against the row re-read after the launches, consistently.
        assert calls[outcome - 1] == ("state.get", True)
        assert [c for c in calls[:outcome] if c[0] == "audit"] == []
        assert [c for c in calls if c[0] == "state.update"] == [
            ("state.update", "pending_pipeline"), ("state.update", "pending_pipeline")]

    def test_the_claim_moves_the_pointers_only_under_the_ownership_condition(self):
        previous = dict(self.PREVIOUS, complianceState="exception", exceptionGranted=True,
                        exceptionReason="waived", exceptionSchemaName=SCHEMA,
                        exceptionSchemaVersion=1, quarantineReason="old")
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, previous_row=previous)
        tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        claim, _ = tables.claim_and_outcome()
        record = tables.evaluation_records()[0][0]
        assert set(claim) == {"lastEvaluationId", "lastEvaluatedAt", "lastEvaluationStatus",
                              "updatedAt"}
        assert claim["lastEvaluationId"] == record["evaluationId"]
        assert claim["lastEvaluatedAt"] == record["evaluatedAt"]
        assert claim["lastEvaluationStatus"] == "pending_pipeline"
        write = tables.state.update_item.call_args_list[0].kwargs
        assert write["Key"] == {"databaseId": DB, "assetId": ASSET}
        assert write["ConditionExpression"] == store.evaluation_ownership_condition(
            record["evaluationId"], record["evaluatedAt"])[0]
        assert write["ExpressionAttributeValues"][":ownedEvaluationId"] == record["evaluationId"]
        assert write["ExpressionAttributeValues"][":ownedEvaluatedAt"] == record["evaluatedAt"]

    def test_an_asset_without_a_state_row_is_claimed_as_unknown(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY)
        tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        claim, outcome = tables.claim_and_outcome()
        assert claim["complianceState"] == "unknown"
        assert outcome["complianceState"] == "compliant"

    def test_the_claim_writes_no_audit_entry_and_sends_no_notification(self):
        """A quarantining evaluation: its one `compliance_check` entry and its one quarantine
        notification both follow the outcome write, none the claim."""
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, parents=0, previous_row=self.PREVIOUS)
        with patch(f"{NOTIFICATIONS}.notify_quarantine") as notify:
            calls = _record_calls(tables, notify=notify)
            result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "quarantined"
        writes = [c for c in calls if c[0] in ("state.update", "audit", "notify")]
        assert writes == [("state.update", "pending_pipeline"), ("state.update", "completed"),
                          ("audit", "compliance_check"), ("notify", None)]

    def test_a_concurrent_reader_sees_the_claim_while_the_launch_is_under_way(self):
        """What the trigger's coverage read of the row returns while the first launch is still in
        flight: this evaluation's id and start, status pending."""
        tables = Tables(previous_row=self.PREVIOUS)
        state = FakeAssetStateTable({"databaseId": DB, "assetId": ASSET, **self.PREVIOUS})
        tables.state = state.mock
        seen_during_launch = []
        original_invoke = tables.lambda_client.invoke.return_value

        def invoke(**_):
            seen_during_launch.append(store.get_compliance_record(DB, ASSET, consistent_read=True))
            return original_invoke
        tables.lambda_client.invoke.side_effect = invoke
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)

        record = tables.evaluation_records()[0][0]
        assert len(seen_during_launch) == 1
        row = seen_during_launch[0]
        assert row["lastEvaluationId"] == result["evaluationId"] == record["evaluationId"]
        assert row["lastEvaluatedAt"] == record["evaluatedAt"]
        assert row["lastEvaluationStatus"] == "pending_pipeline"
        assert row["complianceState"] == "compliant"
        assert row["schemaName"] == SCHEMA and row["schemaSource"] == "database"


@pytest.mark.unit
class TestOwnedStateWrite:
    """The asset-state write carries the ownership rule as a condition, so an evaluation that read
    the row as its own cannot overwrite a newer evaluation that landed in between."""

    def test_the_state_write_is_conditioned_on_the_evaluations_ownership(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, parents=0)
        tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        write = tables.state.update_item.call_args.kwargs
        condition = write["ConditionExpression"]
        names, values = write["ExpressionAttributeNames"], write["ExpressionAttributeValues"]
        assert "attribute_not_exists(" in condition and " < " in condition and " = " in condition
        assert {names[n] for n in names if n.startswith("#owned")} == {
            "lastEvaluatedAt", "lastEvaluationId"}
        evaluation_id = tables.evaluation_records()[0][0]["evaluationId"]
        assert values[":ownedEvaluationId"] == evaluation_id
        assert values[":ownedEvaluatedAt"] == tables.evaluation_records()[0][0]["evaluatedAt"]

    def test_a_write_that_loses_the_condition_leaves_the_row_and_the_audit_alone(self):
        """The claim lands; a newer evaluation claims the row while this one runs, so the outcome
        write loses its condition."""
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, parents=0, previous_row=NAMED_RECORD)
        tables.state.update_item.side_effect = [{}, conditional_check_failure()]
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "quarantined"
        assert tables.state.update_item.call_count == 2
        tables.audit.put_item.assert_not_called()
        assert tables.evaluation_records()[0][0]["status"] == "completed"

    def test_a_real_write_failure_is_raised(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, parents=0, previous_row=NAMED_RECORD)
        tables.state.update_item.side_effect = [{}, RuntimeError("table unavailable")]
        with pytest.raises(RuntimeError):
            tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)

    def test_the_outcome_write_of_the_evaluation_that_holds_the_claim_lands(self):
        """Same-id ownership: after the claim the row names this evaluation, with this evaluation's
        own `lastEvaluatedAt`, so neither the `<` clause nor an absent row lets the outcome through
        -- only the `lastEvaluationId = this evaluation` clause does, and it must."""
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, parents=0)
        state = FakeAssetStateTable({"databaseId": DB, "assetId": ASSET,
                                     "complianceState": "compliant", "schemaName": SCHEMA,
                                     "lastEvaluationId": "eval-older",
                                     "lastEvaluatedAt": "2026-01-01T00:00:00+00:00"})
        tables.state = state.mock
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "quarantined"
        claim, outcome = tables.claim_and_outcome()
        row = state.row(DB, ASSET)
        assert row["lastEvaluationId"] == result["evaluationId"] == claim["lastEvaluationId"]
        assert row["lastEvaluatedAt"] == claim["lastEvaluatedAt"] == outcome["lastEvaluatedAt"]
        assert row["complianceState"] == "quarantined"
        assert row["lastEvaluationStatus"] == "completed"
        assert row["schemaName"] == SCHEMA
        assert tables.audit_events() == ["compliance_check"]


@pytest.mark.unit
class TestLatestVerdictEvaluation:

    def _pages(self, *pages):
        responses = []
        for index, items in enumerate(pages):
            response = {"Items": list(items)}
            if index < len(pages) - 1:
                response["LastEvaluatedKey"] = {"evaluationId": f"page-{index}"}
            responses.append(response)
        return responses

    def test_the_newest_verdict_bearing_row_is_returned_from_the_first_page(self):
        tables = Tables()
        tables.evaluation.query.side_effect = self._pages([
            {"evaluationId": "e3", "verdict": "pending_pipeline"},
            {"evaluationId": "e2", "verdict": "error"},
            {"evaluationId": "e1", "verdict": "non_compliant"},
            {"evaluationId": "e0", "verdict": "compliant"}])
        row = tables.run(store.latest_verdict_evaluation, DB, ASSET)
        assert row["evaluationId"] == "e1"
        query = tables.evaluation.query.call_args.kwargs
        assert query["IndexName"] == "AssetIndex" and query["ScanIndexForward"] is False
        assert query["Limit"] <= store.LATEST_VERDICT_LOOKBACK

    def test_the_history_is_paged_until_a_verdict_is_found(self):
        tables = Tables()
        tables.evaluation.query.side_effect = self._pages(
            [{"evaluationId": "e5", "verdict": "error"}, {"evaluationId": "e4", "verdict": "error"}],
            [{"evaluationId": "e3", "verdict": "pending_pipeline"}],
            [{"evaluationId": "e2", "verdict": "quarantined"}, {"evaluationId": "e1", "verdict": "compliant"}])
        row = tables.run(store.latest_verdict_evaluation, DB, ASSET)
        assert row["evaluationId"] == "e2"
        calls = tables.evaluation.query.call_args_list
        assert len(calls) == 3
        assert "ExclusiveStartKey" not in calls[0].kwargs
        assert calls[1].kwargs["ExclusiveStartKey"] == {"evaluationId": "page-0"}
        assert calls[2].kwargs["ExclusiveStartKey"] == {"evaluationId": "page-1"}

    def test_an_asset_with_no_verdict_bearing_evaluation_is_none(self):
        tables = Tables()
        tables.evaluation.query.side_effect = self._pages(
            [{"evaluationId": "e2", "verdict": "error"}], [{"evaluationId": "e1", "verdict": "error"}])
        assert tables.run(store.latest_verdict_evaluation, DB, ASSET) is None
        assert len(tables.evaluation.query.call_args_list) == 2


@pytest.mark.unit
class TestEvaluationOrdering:
    """`run_evaluation` follows the same rule as the callback, applied when it starts: an evaluation
    that begins before the row's last evaluation cannot claim the row, so it does not run at all --
    no rule is evaluated, no execution launched, nothing written or audited."""

    OLD = "2026-01-01T00:00:00+00:00"
    NEWER = "2026-01-01T00:00:10+00:00"

    @staticmethod
    def _assert_superseded(tables, result):
        assert result["superseded"] is True
        assert result["error"] == store.SUPERSEDED_MESSAGE
        assert result["verdict"] == "superseded"
        assert result["evaluationId"] != "eval-newer"
        assert result["complianceState"] == "compliant"
        assert result["pipelineRulesPending"] == 0
        assert tables.evaluation_records() == ([], [])
        tables.evaluation.put_item.assert_not_called()
        tables.evaluation.update_item.assert_not_called()
        tables.state.update_item.assert_not_called()
        tables.audit.put_item.assert_not_called()
        tables.lambda_client.invoke.assert_not_called()
        tables.metadata.query.assert_not_called()

    def test_an_evaluation_older_than_the_rows_last_one_does_not_run(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, parents=0, previous_row={
            "complianceState": "compliant", "lastEvaluationId": "eval-newer",
            "lastEvaluatedAt": self.NEWER, "lastEvaluationStatus": "completed"})
        with patch(f"{STORE}.now_iso", return_value=self.OLD):
            result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        self._assert_superseded(tables, result)
        tables.schema.query.assert_not_called()

    def test_an_older_pipeline_evaluation_launches_nothing(self):
        """The row already carries a newer `lastEvaluatedAt` (a newer evaluation claimed it): the
        claim is refused and no workflow execution is started."""
        tables = Tables(previous_row={
            "complianceState": "compliant", "lastEvaluationId": "eval-newer",
            "lastEvaluatedAt": self.NEWER, "lastEvaluationStatus": "pending_pipeline"})
        with patch(f"{STORE}.now_iso", return_value=self.OLD):
            result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        self._assert_superseded(tables, result)

    def test_an_older_missing_schema_evaluation_records_nothing(self):
        tables = Tables(previous_row={"complianceState": "compliant",
                                      "lastEvaluationId": "eval-newer", "lastEvaluatedAt": self.NEWER})
        tables.schema.query.return_value = {"Items": []}
        with patch(f"{STORE}.now_iso", return_value=self.OLD):
            result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        self._assert_superseded(tables, result)

    def test_a_claim_that_loses_the_write_condition_launches_nothing(self):
        """The row read before the claim is older, but a newer evaluation claims it between that read
        and this claim's write: the conditional write fails and the evaluation stops there."""
        tables = Tables(previous_row={
            "complianceState": "compliant", "lastEvaluationId": "eval-older",
            "lastEvaluatedAt": self.OLD, "lastEvaluationStatus": "completed"})
        tables.state.update_item.side_effect = conditional_check_failure()
        with patch(f"{STORE}.now_iso", return_value=self.NEWER):
            result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["superseded"] is True and result["error"] == store.SUPERSEDED_MESSAGE
        assert result["verdict"] == "superseded"
        assert tables.state.update_item.call_count == 1
        assert tables.evaluation_records() == ([], [])
        tables.evaluation.put_item.assert_not_called()
        tables.lambda_client.invoke.assert_not_called()
        tables.audit.put_item.assert_not_called()

    def test_the_superseded_result_names_the_unused_id_and_the_state_as_read(self):
        """The result a superseded run returns: the id it was given (written nowhere), the
        `superseded` verdict and flag, the message the evaluate route reports, and the state the
        row held when read -- the newer evaluation's to settle."""
        assert store._superseded_result("eval-lost", {
            "complianceState": "quarantined", "lastEvaluationId": "eval-newer"}) == {
            "evaluationId": "eval-lost",
            "verdict": "superseded",
            "complianceState": "quarantined",
            "ruleResults": [],
            "pipelineRulesPending": 0,
            "schemaVersion": None,
            "exceptionApplied": False,
            "hasRuleErrors": False,
            "errorRules": [],
            "superseded": True,
            "error": "A newer evaluation of the asset has started",
        }
        assert store._superseded_result("eval-lost", {})["complianceState"] == "unknown"

    def test_the_superseded_verdict_is_not_an_evaluation_verdict(self):
        """`superseded` lives on the run result only. Evaluation rows and the compliance record
        carry `EvaluationVerdict` values, so a reader parsing a stored verdict never meets it."""
        from models.compliance import EvaluationVerdict

        assert store.SUPERSEDED_VERDICT == "superseded"
        assert store.SUPERSEDED_VERDICT not in {verdict.value for verdict in EvaluationVerdict}

    def test_an_evaluation_newer_than_the_rows_last_one_writes_the_state(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, parents=0, previous_row={
            "complianceState": "compliant", "lastEvaluationId": "eval-older",
            "lastEvaluatedAt": self.OLD, "lastEvaluationStatus": "completed"})
        with patch(f"{STORE}.now_iso", return_value=self.NEWER):
            result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["complianceState"] == "quarantined"
        state = tables.outcome_state()
        assert state["complianceState"] == "quarantined"
        assert state["lastEvaluatedAt"] == self.NEWER
        assert state["lastEvaluationStatus"] == "completed"

    @pytest.mark.parametrize("row,evaluation_id,evaluated_at,owns", [
        ({}, "e", "2026-01-01T00:00:00+00:00", True),
        ({"lastEvaluationId": "e"}, "e", "2026-01-01T00:00:00+00:00", True),
        ({"lastEvaluationId": "e", "lastEvaluatedAt": "2026-01-01T00:00:05+00:00"}, "e",
         "2026-01-01T00:00:00+00:00", True),
        ({"lastEvaluationId": "e", "lastEvaluatedAt": "2026-01-01T00:00:00+00:00"}, "e",
         "2026-01-01T00:00:00+00:00", True),
        ({"lastEvaluationId": "other"}, "e", "2026-01-01T00:00:00+00:00", True),
        ({"lastEvaluationId": "other", "lastEvaluatedAt": "2026-01-01T00:00:00+00:00"}, "e",
         "2026-01-01T00:00:00.500000+00:00", True),
        ({"lastEvaluationId": "other", "lastEvaluatedAt": "2026-01-01T00:00:00.500000+00:00"}, "e",
         "2026-01-01T00:00:00+00:00", False),
        ({"lastEvaluationId": "other", "lastEvaluatedAt": "2026-01-01T00:00:00+00:00"}, "e",
         "2026-01-01T00:00:00+00:00", False),
        ({"lastEvaluationId": "other", "lastEvaluatedAt": "2026-01-01T00:00:00Z"}, "e",
         "2026-01-01T01:00:00+01:00", False),
        ({"lastEvaluationId": "other", "lastEvaluatedAt": "garbage"}, "e", "z", True),
        ({"lastEvaluationId": "other", "lastEvaluatedAt": "garbage"}, "e", "a", False),
    ], ids=["no-row", "own-id", "own-id-later-row", "own-claim-same-instant", "other-id-no-time",
            "newer", "older",
            "same-instant", "same-instant-other-offset", "unparseable-text-newer",
            "unparseable-text-older"])
    def test_the_ownership_predicate(self, row, evaluation_id, evaluated_at, owns):
        assert store.engine.evaluation_owns_state_row(row, evaluation_id, evaluated_at) is owns


@pytest.mark.unit
class TestStatusCondition:

    def test_the_condition_placeholders_never_collide_with_the_update_aliases(self):
        """The status condition's placeholders share no name with the aliases `to_update_expr`
        generates, so merging them never replaces a SET value with the condition's value."""
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


# --- Metadata rule scopes -------------------------------------------------------------------------

def _scoped_body(**rules):
    """A vams-rules-v1 body of metadata rules, each `name=(scope, checks, extra)`, all naming the
    `Model Schema` metadata schema in DB."""
    return {"schemaFormat": "vams-rules-v1", "rules": {
        name: {"ruleType": "metadata", "enforcement": "warn", "scope": scope,
               "metadataSchemaRef": {"databaseId": DB, "schemaName": "Model Schema"},
               "checks": checks, **extra}
        for name, (scope, checks, extra) in rules.items()}}


REQUIRE_UNITS = [{"name": "units", "validateRequired": True}]


def _schema_lookup(*rows):
    """A metadata-schema-table `query` side effect: the rows whose index partition and `schemaName`
    match the query's key condition and filter, as the GSI and filter would select —
    `databaseId:metadataEntityType` on DatabaseIdMetadataEntityTypeIndex, `databaseId` on
    DatabaseIdIndex."""
    partition_attribute = {"DatabaseIdMetadataEntityTypeIndex": "databaseId:metadataEntityType",
                           "DatabaseIdIndex": "databaseId"}

    def query(**kwargs):
        attribute = partition_attribute[kwargs["IndexName"]]
        partition = kwargs["KeyConditionExpression"]._values[1]
        name = kwargs["FilterExpression"]._values[1]
        return {"Items": [row for row in rows if row[attribute] == partition
                          and row["schemaName"] == name]}
    return query


def _file_metadata_rows(path, **values):
    return [{"metadataKey": key, "metadataValue": value, "metadataValueType": "STRING",
             "databaseId:assetId:filePath": f"{DB}:{ASSET}:{path}",
             "databaseId:assetId": f"{DB}:{ASSET}"} for key, value in values.items()]


def _link_row(link_id, relationship_type="parentChild"):
    return {"assetLinkId": link_id, "relationshipType": relationship_type}


@pytest.mark.unit
class TestScopedMetadataRules:
    """A metadata rule's scope selects which metadata it reads: the store reads that source once per
    evaluation, only when a rule of the scope exists, and keys file rows by the same leading-'/' path
    the S3 listing yields."""

    def _tables(self, body, schema_rows, files=("/models/part.glb", "/models/plain.glb"),
                file_rows=(), attribute_rows=(), parents=(), children=(), link_rows=None):
        tables = Tables(schema_body=body, asset_files=files, previous_row=NAMED_RECORD)
        tables.metadata_schema.query.side_effect = _schema_lookup(*schema_rows)
        asset_rows = [{"metadataKey": "owner", "metadataValue": "v", "metadataValueType": "STRING",
                       "databaseId:assetId:filePath": f"{DB}:{ASSET}:/"}]
        tables.metadata.query.side_effect = lambda **kw: {"Items": list(
            file_rows) + asset_rows if kw.get("IndexName") == "DatabaseIdAssetIdIndex" else asset_rows}
        tables.file_attributes.query.return_value = {"Items": list(attribute_rows)}
        tables.links.query.side_effect = lambda **kw: {"Items": list(
            parents if kw.get("IndexName") == "toAssetGSI" else children)}
        link_rows = link_rows or {}
        tables.link_metadata.query.side_effect = lambda **kw: {"Items": list(
            link_rows.get(kw["KeyConditionExpression"]._values[1], []))}
        return tables

    @staticmethod
    def _result(result, name):
        return next(r for r in result["ruleResults"] if r["ruleName"] == name)

    def test_a_files_rule_reads_the_asset_s_file_rows_once_and_fails_a_file_without_rows(self):
        body = _scoped_body(per_file=("files", REQUIRE_UNITS, {}))
        tables = self._tables(body, [metadata_schema_row(
            "Model Schema", "fileMetadata", fields=[("units", "STRING", True)])],
            file_rows=_file_metadata_rows("/models/part.glb", units="mm"))
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        rule = self._result(result, "per_file")
        assert rule["passed"] is False
        assert rule["measured"]["evaluatedCount"] == 2
        assert rule["measured"]["failing"] == [{
            "path": "/models/plain.glb",
            "message": "units: missing required schema fields: ['units']"}]
        assert rule["message"].startswith("1 of 2 files fail: /models/plain.glb: units:")
        indexes = [c.kwargs.get("IndexName") for c in tables.metadata.query.call_args_list]
        assert indexes.count("DatabaseIdAssetIdIndex") == 1
        assert tables.s3_client.list_object_versions.call_count == 1
        tables.file_attributes.query.assert_not_called()
        tables.link_metadata.query.assert_not_called()

    def test_a_file_attributes_rule_reads_the_attribute_table_and_the_schema_restriction(self):
        body = _scoped_body(attrs=("fileAttributes", REQUIRE_UNITS, {}))
        attribute_rows = [{"attributeKey": "units", "attributeValue": "mm",
                           "attributeValueType": "STRING",
                           "databaseId:assetId:filePath": f"{DB}:{ASSET}:/models/part.glb"}]
        tables = self._tables(body, [metadata_schema_row(
            "Model Schema", "fileAttribute", fields=[("units", "STRING", True)],
            restriction=".glb")], files=("/models/part.glb", "/readme.txt"),
            attribute_rows=attribute_rows)
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        rule = self._result(result, "attrs")
        assert rule["passed"] is True
        assert rule["measured"]["evaluatedCount"] == 1
        query = tables.file_attributes.query.call_args.kwargs
        assert query["IndexName"] == "DatabaseIdAssetIdIndex"
        assert query["KeyConditionExpression"]._values[1] == f"{DB}:{ASSET}"

    def test_a_links_rule_reads_each_link_s_metadata_in_its_scope_only(self):
        body = _scoped_body(placement=("links", [{"name": "placed",
                                                  "additionalRequiredFields": ["Matrix"]}],
                                       {"linkDirection": "parents",
                                        "relationshipType": "parentChild"}))
        tables = self._tables(
            body, [metadata_schema_row("Model Schema", "assetLinkMetadata")],
            parents=[_link_row("link-a"), _link_row("link-r", "related")],
            children=[_link_row("link-c")],
            link_rows={"link-a": [{"assetLinkId": "link-a", "metadataKey": "Matrix",
                                   "metadataValue": "1", "metadataValueType": "STRING"}]})
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        rule = self._result(result, "placement")
        assert rule["passed"] is True
        assert rule["measured"]["evaluatedCount"] == 1
        read = [c.kwargs["KeyConditionExpression"]._values[1]
                for c in tables.link_metadata.query.call_args_list]
        assert read == ["link-a"]

    def test_more_links_than_the_bound_errors_and_reads_no_link_metadata(self):
        body = _scoped_body(placement=("links", REQUIRE_UNITS, {}))
        tables = self._tables(
            body, [metadata_schema_row("Model Schema", "assetLinkMetadata")],
            children=[_link_row(f"link-{i:04d}")
                      for i in range(store.engine.MAX_LINKS_PER_METADATA_RULE + 1)])
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        rule = self._result(result, "placement")
        assert rule["status"] == "error"
        assert "too many links" in rule["message"]
        tables.link_metadata.query.assert_not_called()

    def test_an_asset_scope_rule_reads_no_file_attribute_or_link_metadata_and_lists_no_files(self):
        body = _scoped_body(own=("asset", [{"name": "o", "additionalRequiredFields": ["owner"]}], {}))
        tables = self._tables(body, [metadata_schema_row("Model Schema", "assetMetadata")])
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert self._result(result, "own")["passed"] is True
        assert [c.kwargs.get("IndexName") for c in tables.metadata.query.call_args_list] == [
            "DatabaseIdAssetIdFilePathIndex"]
        tables.file_attributes.query.assert_not_called()
        tables.link_metadata.query.assert_not_called()
        tables.s3_client.list_object_versions.assert_not_called()
        tables.asset.get_item.assert_not_called()

    def test_two_rules_on_one_schema_name_with_different_scopes_look_it_up_per_entity_type(self):
        body = _scoped_body(own=("asset", REQUIRE_UNITS, {}), per_file=("files", REQUIRE_UNITS, {}))
        tables = self._tables(body, [
            metadata_schema_row("Model Schema", "assetMetadata", fields=[("units", "STRING", True)]),
            metadata_schema_row("Model Schema", "fileMetadata")])
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        partitions = sorted(c.kwargs["KeyConditionExpression"]._values[1]
                            for c in tables.metadata_schema.query.call_args_list)
        assert partitions == [f"{DB}:assetMetadata", f"{DB}:fileMetadata"]
        # The asset scope required `units` (the asset lacks it); the file schema requires nothing.
        assert self._result(result, "own")["passed"] is False
        assert self._result(result, "per_file")["passed"] is True


@pytest.mark.unit
class TestMetadataRowGrouping:

    def test_rows_group_by_path_with_the_asset_root_left_out(self):
        rows = (_file_metadata_rows("/a.glb", units="mm", owner="x")
                + _file_metadata_rows("/", owner="asset-level")
                + _file_metadata_rows("b/c.glb", units="m"))
        grouped = store._metadata_by_file_path(rows, DB, ASSET, ("metadataKey",),
                                               ("metadataValue",), ("metadataValueType",))
        assert grouped == {"/a.glb": {"units": "mm", "owner": "x"}, "/b/c.glb": {"units": "m"}}

    def test_a_path_containing_a_colon_keeps_it(self):
        rows = _file_metadata_rows("/scans/12:30.las", units="mm")
        grouped = store._metadata_by_file_path(rows, DB, ASSET, ("metadataKey",),
                                               ("metadataValue",), ("metadataValueType",))
        assert grouped == {"/scans/12:30.las": {"units": "mm"}}

    def test_another_asset_s_rows_are_ignored(self):
        rows = [{"metadataKey": "units", "metadataValue": "mm",
                 "databaseId:assetId:filePath": f"{DB}:other-asset:/a.glb"}]
        assert store._metadata_by_file_path(rows, DB, ASSET, ("metadataKey",),
                                            ("metadataValue",), ("metadataValueType",)) == {}

    def test_values_are_typed(self):
        rows = [{"metadataKey": "count", "metadataValue": "3", "metadataValueType": "NUMBER",
                 "databaseId:assetId:filePath": f"{DB}:{ASSET}:/a.glb"}]
        assert store._metadata_by_file_path(rows, DB, ASSET, ("metadataKey",), ("metadataValue",),
                                            ("metadataValueType",)) == {"/a.glb": {"count": 3.0}}
