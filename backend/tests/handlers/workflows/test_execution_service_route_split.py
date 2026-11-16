# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The execution service module runs as four functions with four roles, and each role holds exactly the
DynamoDB calls its routes make.

The execution service serves the lists, abort, abort by group and re-run; the details function the
details view and its paged collections; the logs function the logs view; and the permanent-delete
function permanent delete, the only role granted to delete execution rows. EXECUTION_SERVICE_ROUTES
selects the set (unset serves every route). The inventories below are what the CDK grants in
infra/lib/lambdaBuilder/workflowFunctions.ts are written from, and
infra/test/security/executionServiceRouteSplitGrants.test.ts pins those grants to the same lists: a new
DynamoDB call on either path fails here first, naming the grant to add.

The calls are recorded off a real (moto) DynamoDB client, every route driven through lambda_handler,
so a call made by a shared helper three frames down is recorded the same as one in the route itself.

executionService resolves its table names at import (mirrors test_executionService_wb53.py)."""

import io
import json
import os
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

for _name, _value in [
    ("ASSET_STORAGE_TABLE_NAME", "t-assets"),
    ("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2"),
    ("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs"),
    ("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec"),
    ("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg"),
    ("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files"),
    ("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md"),
    ("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg"),
    ("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of"),
    ("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om"),
    ("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or"),
    ("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs"),
    ("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows"),
    ("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines"),
    ("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2"),
]:
    os.environ.setdefault(_name, _value)

from backend.backend.handlers.workflows import executionService as le  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"

DB, ASSET = "db01", "xasset01"
WF_DB, WF_ID = "wfdb01", "wf01"
COMPOSITE = f"{WF_DB}:{WF_ID}"
RUNNING_ID = "e1000000000000000000000000000001"
FINISHED_ID = "e2000000000000000000000000000002"
RUNNING_PEXEC = "p1000000000000000000000000000001"
FINISHED_PEXEC = "p2000000000000000000000000000002"
GROUP_ID = "g1"


# --------------------------------------------------------------------------------------------------
# What each role may call. Logical table names, resolved against the module's own names below.
# --------------------------------------------------------------------------------------------------

#: The authorization reads every route set but permanent delete makes: the main row, the run's input
#: and configuration rows, its assets (batched, then one by one) and the workflow definition.
AUTHORIZATION_READS = {
    ("main", "Query"),
    ("inputs", "Query"),
    ("configuration", "GetItem"),
    ("asset", "BatchGetItem"),
    ("asset", "Query"),
    ("workflow", "GetItem"),
}
#: The lock release (lock policy) of the abort and of a read that finds a run stopped outside VAMS.
LOCK_RELEASE = {("locks", "DeleteItem"), ("locks/index/WorkflowExecutionLocksByHolderGSI", "Query")}
#: The status reconcile of the main row and the pipeline rows.
RECONCILE_WRITES = {("main", "UpdateItem"), ("pipelineExecutions", "UpdateItem")}
PIPELINE_ROWS = ("pipelineExecutions/index/PipelineExecByWorkflowExecGSI", "Query")

#: Every call each route set's role is granted, by (logical table or index, IAM action).
ROUTE_SET_CALLS = {
    "service": AUTHORIZATION_READS | LOCK_RELEASE | RECONCILE_WRITES | {
        ("main/index/WorkflowExecutionsByWorkflowGSI", "Query"),
        ("main/index/WorkflowExecutionsByGroupGSI", "Query"),
        ("main/index/WorkflowExecutionsByDateGSI", "Query"),
        ("inputs/index/WorkflowExecInputsByAssetGSI", "Query"),
        ("configuration/index/WorkflowExecConfigByOutputAssetGSI", "Query"),  # pragma: allowlist secret
        PIPELINE_ROWS,
        ("pipeline", "GetItem"),
        ("pipelineInputConfiguration", "Query"),
    },
    "details": AUTHORIZATION_READS | LOCK_RELEASE | RECONCILE_WRITES | {
        PIPELINE_ROWS,
        ("pipeline", "BatchGetItem"),
        ("pipeline", "GetItem"),
        ("pipelineInputConfiguration", "Query"),
        ("pipelineInputMetadata", "Query"),
        ("pipelineOutputFiles", "Query"),
        ("pipelineOutputMetadata", "BatchGetItem"),
        ("pipelineOutputMetadata", "Query"),
        ("pipelineOutputResults", "Query"),
        ("fileVersionHistory/index/WorkflowExecutionIdIndex", "Query"),
    },
    "logs": AUTHORIZATION_READS | {
        PIPELINE_ROWS,
        ("pipelineLogs", "Query"),
    },
}

#: The routes each route set serves (keys of ROUTES below).
ROUTE_SET_ROUTES = {
    "service": ("assetList", "assetListByWorkflow", "globalList", "rerun", "abort"),
    "details": ("details", "detailsMetadata", "detailsFiles", "detailsResults"),
    "logs": ("logs",),
    "permanentDelete": ("permanentDelete",),
}

#: Every call the permanent-delete route makes, reads included: the main row and the configuration
#: row, the authorization reads (the workflow definition, the input rows and the assets), the pipeline
#: rows through their by-execution index, and the deletes of every row of the execution.
PERMANENT_DELETE_CALLS = {
    ("main", "Query"),
    ("main", "DeleteItem"),
    ("configuration", "GetItem"),
    ("configuration", "DeleteItem"),
    ("workflow", "GetItem"),
    ("asset", "Query"),
    ("inputs", "Query"),
    ("inputs", "BatchWriteItem"),
    ("pipelineExecutions/index/PipelineExecByWorkflowExecGSI", "Query"),
    ("pipelineExecutions", "DeleteItem"),
    *{(table, action)
      for table in ("pipelineInputConfiguration", "pipelineInputMetadata", "pipelineInputFiles",
                    "pipelineOutputFiles", "pipelineOutputMetadata", "pipelineOutputResults",
                    "pipelineLogs")
      for action in ("Query", "BatchWriteItem")},
}

#: The indexes each set's code names; a query on an index outside them fails AccessDenied.
ROUTE_SET_INDEXES = {
    route_set: {target.split("/index/")[1] for target, _action in calls if "/index/" in target}
    for route_set, calls in ROUTE_SET_CALLS.items()
}
READ_ACTIONS = {"GetItem", "BatchGetItem", "Query"}

WRITE_ACTIONS = {"PutItem", "UpdateItem", "DeleteItem", "BatchWriteItem", "TransactWriteItems"}


def _logical_names():
    return {
        le.workflow_execution_database_v2: "main",
        le.workflow_execution_inputs_table: "inputs",
        le.workflow_execution_configuration_table: "configuration",
        le.pipeline_executions_table: "pipelineExecutions",
        le.pipeline_execution_input_files_table: "pipelineInputFiles",
        le.pipeline_execution_input_metadata_table: "pipelineInputMetadata",
        le.pipeline_execution_input_configuration_table: "pipelineInputConfiguration",
        le.pipeline_execution_output_files_table: "pipelineOutputFiles",
        le.pipeline_execution_output_metadata_table: "pipelineOutputMetadata",
        le.pipeline_execution_output_results_table: "pipelineOutputResults",
        le.pipeline_execution_logs_table: "pipelineLogs",
        le.workflow_execution_locks_table: "locks",
        le.workflow_database: "workflow",
        le.pipeline_database: "pipeline",
        le.asset_storage_table_name: "asset",
        "t-file-version-history": "fileVersionHistory",
    }


# --------------------------------------------------------------------------------------------------
# Route gate
# --------------------------------------------------------------------------------------------------

ROUTES = {
    "assetList": ("GET", f"/database/{DB}/assets/{ASSET}/workflows/executions", "handle_get_request"),
    "assetListByWorkflow": ("GET", f"/database/{DB}/assets/{ASSET}/workflows/executions/{WF_ID}",
                            "handle_get_request"),
    "globalList": ("GET", "/workflows/executions", "handle_global_list_request"),
    "details": ("GET", f"/workflows/executions/{FINISHED_ID}/details", "handle_details_request"),
    "detailsMetadata": ("GET", f"/workflows/executions/{FINISHED_ID}/details/metadata",
                        "handle_details_metadata_request"),
    "detailsFiles": ("GET", f"/workflows/executions/{FINISHED_ID}/details/files",
                     "handle_details_files_request"),
    "detailsResults": ("GET", f"/workflows/executions/{FINISHED_ID}/details/results",
                       "handle_details_results_request"),
    "logs": ("GET", f"/workflows/executions/{FINISHED_ID}/logs", "handle_logs_request"),
    "rerun": ("POST", f"/workflows/executions/{FINISHED_ID}/rerun", "handle_rerun_request"),
    "abort": ("DELETE", f"/workflows/executions/{RUNNING_ID}", "handle_delete_request"),
    "permanentDelete": ("DELETE", f"/workflows/executions/{FINISHED_ID}/permanent",
                        "handle_permanent_delete_request"),
}


def _gate_event(method, path):
    return {"requestContext": {"http": {"method": method, "path": path}, "authorizer": {}},
            "pathParameters": {}, "queryStringParameters": {}}


def _dispatched(route, monkeypatch, route_set):
    """(status code, whether the route's handler ran) for one route under one EXECUTION_SERVICE_ROUTES."""
    method, path, handler = ROUTES[route]
    if route_set is None:
        monkeypatch.delenv(le.EXECUTION_SERVICE_ROUTES_ENV, raising=False)
    else:
        monkeypatch.setenv(le.EXECUTION_SERVICE_ROUTES_ENV, route_set)
    sentinel = {"statusCode": 299, "body": "{}"}
    with patch(f"{MOD}.{handler}", return_value=sentinel) as served, \
            patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"]}):
        response = le.lambda_handler(_gate_event(method, path), MagicMock())
    return response["statusCode"], served.called


@pytest.mark.unit
class TestTheRouteGate:
    def test_every_route_belongs_to_exactly_one_route_set(self):
        served = [route for routes in ROUTE_SET_ROUTES.values() for route in routes]
        assert sorted(served) == sorted(ROUTES)
        assert set(ROUTE_SET_ROUTES) == set(le.ROUTE_SETS)

    @pytest.mark.parametrize("route_set,route", [
        (route_set, route) for route_set in ROUTE_SET_ROUTES for route in ROUTES])
    def test_each_function_serves_its_routes_and_answers_every_other_with_404(
            self, route_set, route, monkeypatch):
        expected = (299, True) if route in ROUTE_SET_ROUTES[route_set] else (404, False)
        assert _dispatched(route, monkeypatch, route_set) == expected

    def test_abort_by_group_is_an_execution_service_route(self, monkeypatch):
        event = _gate_event("DELETE", f"/workflows/executions/{RUNNING_ID}")
        event["queryStringParameters"] = {"groupId": GROUP_ID}
        for route_set in le.ROUTE_SETS:
            expected = 299 if route_set == le.ROUTE_SET_SERVICE else 404
            monkeypatch.setenv(le.EXECUTION_SERVICE_ROUTES_ENV, route_set)
            with patch(f"{MOD}.abort_group", return_value={"statusCode": 299, "body": "{}"}), \
                    patch(f"{MOD}._enforce_api", return_value=True), \
                    patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"]}):
                assert le.lambda_handler(event, MagicMock())["statusCode"] == expected, route_set

    def test_a_method_other_than_get_on_a_details_or_logs_path_stays_on_the_service(self):
        # Only the GET of a details or logs path moves; anything else answers on the service, which
        # refuses it as before ("Method not allowed").
        for path in (f"/workflows/executions/{FINISHED_ID}/details", f"/workflows/executions/{FINISHED_ID}/logs"):
            assert le._route_set_of(_gate_event("POST", path)) == le.ROUTE_SET_SERVICE

    @pytest.mark.parametrize("route", list(ROUTES))
    def test_unset_serves_every_route(self, route, monkeypatch):
        assert _dispatched(route, monkeypatch, None) == (299, True)

    @pytest.mark.parametrize("route", list(ROUTES))
    def test_an_unknown_route_set_serves_nothing(self, route, monkeypatch):
        assert _dispatched(route, monkeypatch, "everything") == (404, False)

    @pytest.mark.parametrize("route_set,route", [
        (le.ROUTE_SET_SERVICE, "permanentDelete"), (le.ROUTE_SET_SERVICE, "details"),
        (le.ROUTE_SET_DETAILS, "logs"), (le.ROUTE_SET_LOGS, "abort")])
    def test_a_refused_route_reads_no_identity_and_no_table(self, route_set, route, monkeypatch):
        monkeypatch.setenv(le.EXECUTION_SERVICE_ROUTES_ENV, route_set)
        method, path, _ = ROUTES[route]
        with patch(f"{MOD}.request_to_claims") as claims, patch(f"{MOD}.dynamodb") as ddb:
            response = le.lambda_handler(_gate_event(method, path), MagicMock())
        assert response["statusCode"] == 404
        claims.assert_not_called()
        ddb.Table.assert_not_called()


# --------------------------------------------------------------------------------------------------
# Recorded DynamoDB calls, against moto
# --------------------------------------------------------------------------------------------------

def _iso(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _s(name):
    return {"AttributeName": name, "AttributeType": "S"}


def _gsi(name, pk, sk=None):
    schema = [{"AttributeName": pk, "KeyType": "HASH"}]
    if sk:
        schema.append({"AttributeName": sk, "KeyType": "RANGE"})
    return {"IndexName": name, "KeySchema": schema, "Projection": {"ProjectionType": "ALL"}}


def _create(ddb, name, pk, sk=None, gsis=()):
    key_schema = [{"AttributeName": pk, "KeyType": "HASH"}]
    attributes = {pk}
    if sk:
        key_schema.append({"AttributeName": sk, "KeyType": "RANGE"})
        attributes.add(sk)
    for gsi in gsis:
        attributes.update(k["AttributeName"] for k in gsi["KeySchema"])
    kwargs = {"TableName": name, "KeySchema": key_schema,
              "AttributeDefinitions": [_s(a) for a in sorted(attributes)],
              "BillingMode": "PAY_PER_REQUEST"}
    if gsis:
        kwargs["GlobalSecondaryIndexes"] = list(gsis)
    ddb.create_table(**kwargs)


def _create_tables(ddb):
    """The tables both functions call, with the key schemas and the indexes they query as the storage
    stack defines them."""
    _create(ddb, le.workflow_execution_database_v2, "workflowExecutionId",
            "workflowDatabaseId:workflowId", gsis=[
                _gsi("WorkflowExecutionsByWorkflowGSI", "workflowDatabaseId:workflowId",
                     "executionStartDate"),
                _gsi("WorkflowExecutionsByGroupGSI", "executionGroupId", "executionStartDate"),
                _gsi("WorkflowExecutionsByDateGSI", "allListPartition", "executionStartDate")])
    _create(ddb, le.workflow_execution_inputs_table, "workflowExecutionId",
            "databaseId:assetId:inputAssetFileKey", gsis=[
                _gsi("WorkflowExecInputsByAssetGSI", "databaseId:assetId", "executionStartDate")])
    _create(ddb, le.workflow_execution_configuration_table, "workflowExecutionId", "recordType", gsis=[
        _gsi("WorkflowExecConfigByOutputAssetGSI",  # pragma: allowlist secret
             "outputDatabaseId:outputAssetId", "executionStartDate")])
    _create(ddb, le.pipeline_executions_table, "pipelineExecutionId", "workflowExecutionId", gsis=[
        _gsi("PipelineExecByWorkflowExecGSI", "workflowExecutionId", "pipelineDatabaseId:pipelineId")])
    _create(ddb, le.pipeline_execution_input_files_table, "pipelineExecutionId",
            "databaseId:assetId:inputAssetFileKey")
    _create(ddb, le.pipeline_execution_input_metadata_table, "pipelineExecutionId",
            "databaseId:assetId:filePath")
    _create(ddb, le.pipeline_execution_input_configuration_table, "pipelineExecutionId", "recordType")
    _create(ddb, le.pipeline_execution_output_files_table, "pipelineExecutionId",
            "fileType:relativeFilePath")
    _create(ddb, le.pipeline_execution_output_metadata_table, "pipelineExecutionId",
            "targetFilePath:metadataKey")
    _create(ddb, le.pipeline_execution_output_results_table, "pipelineExecutionId", "relativeFilePath")
    _create(ddb, le.pipeline_execution_logs_table, "pipelineExecutionId", "logType")
    _create(ddb, le.workflow_execution_locks_table, "lockKey", gsis=[
        {"IndexName": "WorkflowExecutionLocksByHolderGSI",
         "KeySchema": [{"AttributeName": "workflowExecutionId", "KeyType": "HASH"}],
         "Projection": {"ProjectionType": "KEYS_ONLY"}}])
    _create(ddb, le.workflow_database, "databaseId", "workflowId")
    _create(ddb, le.pipeline_database, "databaseId", "pipelineId")
    _create(ddb, le.asset_storage_table_name, "databaseId", "assetId")
    _create(ddb, "t-file-version-history", "databaseId:assetId:filePath", "versionId", gsis=[
        _gsi("WorkflowExecutionIdIndex", "changeWorkflowExecutionId", "databaseId:assetId:filePath")])


def _main_row(execution_id, status, minutes_ago):
    finished = status != "RUNNING"
    return {"workflowExecutionId": execution_id, "workflowDatabaseId:workflowId": COMPOSITE,
            "workflowDatabaseId": WF_DB, "workflowId": WF_ID, "workflowName": "WF",
            "workflow_execution_arn": f"arn:aws:states:us-east-1:000000000000:execution:vams-wf:{execution_id}",
            "executionStatus": status, "executionStartDate": _iso(minutes_ago),
            "executionStopDate": _iso(minutes_ago - 1) if finished else "",
            "executionError": "", "executionLog": "", "executingUserName": "u1",
            "executionGroupId": GROUP_ID, "allListPartition": "ALL",
            "lastSfnSyncCheckDate": "2000-01-01T00:00:00Z"}


def _seed(ddb):
    ddb.Table(le.workflow_database).put_item(Item={
        "databaseId": WF_DB, "workflowId": WF_ID, "workflowName": "WF",
        "workflow_arn": "arn:aws:states:us-east-1:000000000000:stateMachine:vams-wf",
        "specifiedPipelines": [{"pipelineDatabaseId": DB, "pipelineId": "p", "jobName": "p"}],
        "systemConfig": {"concurrencyRestriction": "perAsset"}})
    ddb.Table(le.pipeline_database).put_item(Item={
        "databaseId": DB, "pipelineId": "p", "pipelineName": "P",
        "executionConfig": {"executionType": "Lambda"}})
    ddb.Table(le.asset_storage_table_name).put_item(Item={
        "databaseId": DB, "assetId": ASSET, "assetName": "A", "assetType": "folder", "tags": []})
    for execution_id, pexec_id, status, minutes_ago in (
            (RUNNING_ID, RUNNING_PEXEC, "RUNNING", 30), (FINISHED_ID, FINISHED_PEXEC, "SUCCEEDED", 60)):
        ddb.Table(le.workflow_execution_database_v2).put_item(
            Item=_main_row(execution_id, status, minutes_ago))
        ddb.Table(le.workflow_execution_inputs_table).put_item(Item={
            "workflowExecutionId": execution_id, "databaseId:assetId": f"{DB}:{ASSET}",
            "databaseId:assetId:inputAssetFileKey": f"{DB}:{ASSET}:/f.glb",
            "databaseId": DB, "assetId": ASSET, "inputAssetFileKey": "/f.glb",
            "workflowId": WF_ID, "workflowDatabaseId": WF_DB, "executionStartDate": _iso(minutes_ago)})
        ddb.Table(le.workflow_execution_configuration_table).put_item(Item={
            "workflowExecutionId": execution_id, "recordType": "configuration",
            "outputLocationType": "asset", "outputDatabaseId": DB, "outputAssetId": ASSET,
            "outputDatabaseId:outputAssetId": le.er.output_asset_partition_key(DB, ASSET),
            "executionStartDate": _iso(minutes_ago)})
        ddb.Table(le.pipeline_executions_table).put_item(Item={
            "pipelineExecutionId": pexec_id, "workflowExecutionId": execution_id,
            "pipelineDatabaseId:pipelineId": f"{DB}:p", "pipelineDatabaseId": DB, "pipelineId": "p",
            "pipelineName": "P", "executionStatus": status, "executionStartDate": _iso(minutes_ago),
            "pipelineOrder": 0, "endStatePipeline": "true"})
        for table, sort_key in ((le.pipeline_execution_input_files_table,
                                 "databaseId:assetId:inputAssetFileKey"),
                                (le.pipeline_execution_input_metadata_table, "databaseId:assetId:filePath"),
                                (le.pipeline_execution_input_configuration_table, "recordType"),
                                (le.pipeline_execution_output_files_table, "fileType:relativeFilePath"),
                                (le.pipeline_execution_output_metadata_table, "targetFilePath:metadataKey"),
                                (le.pipeline_execution_output_results_table, "relativeFilePath"),
                                (le.pipeline_execution_logs_table, "logType")):
            ddb.Table(table).put_item(Item={"pipelineExecutionId": pexec_id, sort_key: "k"})
    ddb.Table(le.workflow_execution_locks_table).put_item(Item={
        "lockKey": le.el.build_lock_key(WF_DB, WF_ID, DB, ASSET, "", "", scope=le.el.LOCK_SCOPE_ASSET),
        "workflowExecutionId": RUNNING_ID, "acquiredAt": _iso(30), "expiresAt": 4102444800})


class _Recorder:
    """Every DynamoDB call the client makes once seeding is done: (logical table, IAM action), with a
    query on an index recorded against '<table>/index/<name>'."""

    def __init__(self, client):
        self.calls = set()
        self.names = _logical_names()
        client.meta.events.register("before-parameter-build.dynamodb", self._record)

    def _record(self, model, params, **_kwargs):
        action = model.name
        tables = [params["TableName"]] if "TableName" in params else list(
            (params.get("RequestItems") or {}).keys())
        for table in tables:
            name = self.names.get(table, table)
            if params.get("IndexName"):
                name = f"{name}/index/{params['IndexName']}"
            self.calls.add((name, action))


def _event(route, **overrides):
    method, path, _ = ROUTES[route]
    event = {"requestContext": {"http": {"method": method, "path": path}, "authorizer": {}},
             "pathParameters": {}, "queryStringParameters": {}}
    if "/database/" in path:
        event["pathParameters"].update({"databaseId": DB, "assetId": ASSET})
        if path.endswith(WF_ID):
            event["pathParameters"]["workflowId"] = WF_ID
    else:
        parts = path.split("/")
        if len(parts) > 3:
            event["pathParameters"]["executionId"] = parts[3]
    for key, value in overrides.items():
        event[key] = value
    return event


class _Clients:
    """Stand-ins for every non-DynamoDB client, answering as a finished Step Functions execution does."""

    def __init__(self, sfn_status="SUCCEEDED"):
        stopped = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.sfn = MagicMock()
        self.sfn.describe_execution.return_value = {"status": sfn_status, "stopDate": stopped,
                                                    "startDate": stopped - timedelta(minutes=5)}
        self.sfn.get_execution_history.return_value = {"events": []}
        self.sfn.describe_state_machine.return_value = {"loggingConfiguration": {}}
        self.sfn.stop_execution.return_value = {"stopDate": stopped}
        self.logs = MagicMock()
        self.logs.filter_log_events.return_value = {"events": []}
        self.logs.get_log_events.return_value = {"events": []}
        self.logs.describe_log_streams.return_value = {"logStreams": []}
        self.lambda_ = MagicMock()
        self.lambda_.invoke.return_value = {"Payload": io.BytesIO(json.dumps({
            "statusCode": 200, "body": json.dumps({"message": {"executionId": "new"}})}).encode())}


def _allow_all():
    enforcer = MagicMock()
    enforcer.enforce.return_value = True
    enforcer.enforceAPI.return_value = True
    return enforcer


def _drive(ddb, events, monkeypatch, route_set, sfn_status="SUCCEEDED"):
    """Run each event through lambda_handler against `ddb`, every Step Functions execution reporting
    `sfn_status`; returns (responses, recorded calls)."""
    monkeypatch.setenv(le.EXECUTION_SERVICE_ROUTES_ENV, route_set)
    clients = _Clients(sfn_status)
    recorder = _Recorder(ddb.meta.client)
    responses = []
    with patch.object(le, "dynamodb", ddb), \
            patch.object(le, "asset_table", ddb.Table(le.asset_storage_table_name)), \
            patch.object(le, "asset_file_version_history_table", ddb.Table("t-file-version-history")), \
            patch.object(le, "sfn", clients.sfn), \
            patch.object(le, "logs_client", clients.logs), \
            patch.object(le, "batch_client", MagicMock()), \
            patch.object(le, "lambda_client", clients.lambda_), \
            patch.object(le, "sagemaker_client", MagicMock()), \
            patch.object(le, "deadline_client", MagicMock()), \
            patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
            patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"], "roles": ["admin"]}), \
            patch(f"{MOD}.log_actions"):
        for event in events:
            responses.append(le.lambda_handler(event, MagicMock()))
    return responses, set(recorder.calls)


def _route_set_events(route_set):
    """Every route of one route set, the abort also by group, and each read route on the running
    execution too, whose Step Functions execution has finished: that read reconciles the run."""
    def running(route, **overrides):
        event = _event(route, **overrides)
        event["requestContext"]["http"]["path"] = event["requestContext"]["http"]["path"].replace(
            FINISHED_ID, RUNNING_ID)
        event["pathParameters"]["executionId"] = RUNNING_ID
        return event

    return {
        le.ROUTE_SET_SERVICE: lambda: [
            _event("assetList"),
            _event("assetListByWorkflow"),
            _event("globalList"),
            _event("rerun", body=json.dumps({})),
            _event("abort"),
            _event("abort", queryStringParameters={"groupId": GROUP_ID}),
        ],
        le.ROUTE_SET_DETAILS: lambda: [
            _event("details"),
            _event("detailsMetadata", queryStringParameters={"collection": "input"}),
            _event("detailsMetadata", queryStringParameters={"collection": "output"}),
            _event("detailsFiles", queryStringParameters={"collection": "input"}),
            _event("detailsFiles", queryStringParameters={"collection": "output"}),
            _event("detailsResults"),
            running("details"),
        ],
        le.ROUTE_SET_LOGS: lambda: [
            _event("logs"),
            _event("logs", queryStringParameters={"mode": "full"}),
            running("logs"),
        ],
    }[route_set]()


def _recorded(route_set, monkeypatch, sfn_status="SUCCEEDED"):
    """(responses, recorded calls, row counts of the execution tables afterwards) for one route set."""
    import boto3
    from moto import mock_aws
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        _create_tables(ddb)
        _seed(ddb)
        responses, calls = _drive(ddb, _route_set_events(route_set), monkeypatch, route_set, sfn_status)
        counts = {table: ddb.Table(table).scan()["Count"] for table in (
            le.workflow_execution_database_v2, le.workflow_execution_inputs_table,
            le.workflow_execution_configuration_table, le.pipeline_executions_table)}
    return responses, calls, counts


#: Module-level table names (and the two table handles) the code refers to, by logical table.
_MODULE_TABLES = {
    "asset_storage_table_name": "asset", "asset_table": "asset",
    "workflow_execution_database_v2": "main", "workflow_execution_inputs_table": "inputs",
    "workflow_execution_configuration_table": "configuration",
    "pipeline_executions_table": "pipelineExecutions",
    "pipeline_execution_input_files_table": "pipelineInputFiles",
    "pipeline_execution_input_metadata_table": "pipelineInputMetadata",
    "pipeline_execution_input_configuration_table": "pipelineInputConfiguration",
    "pipeline_execution_output_files_table": "pipelineOutputFiles",
    "pipeline_execution_output_metadata_table": "pipelineOutputMetadata",
    "pipeline_execution_output_results_table": "pipelineOutputResults",
    "pipeline_execution_logs_table": "pipelineLogs",
    "workflow_execution_locks_table": "locks",
    "workflow_database": "workflow", "pipeline_database": "pipeline",
    "asset_file_version_history_table": "fileVersionHistory",
}

#: The handler functions each route set dispatches to.
_ROUTE_SET_HANDLERS = {
    "service": ("handle_get_request", "handle_global_list_request", "handle_rerun_request",
                "handle_delete_request", "abort_group"),
    "details": ("handle_details_request", "handle_details_metadata_request",
                "handle_details_files_request", "handle_details_results_request"),
    "logs": ("handle_logs_request",),
}


def _tables_reached(handlers):
    """Every logical table a module-level function reachable from `handlers` names: a static reach over
    the module's own call graph (a function passed as a value counts as called), so a branch the
    recorded routes do not take is covered too."""
    import ast
    with open(le.__file__, encoding="utf-8") as source:
        tree = ast.parse(source.read())
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    names = {name: {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
             for name, node in functions.items()}
    seen, stack = set(), list(handlers)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        stack.extend(n for n in names[name] if n in functions)
    return {_MODULE_TABLES[n] for name in seen for n in names[name] if n in _MODULE_TABLES}


@pytest.mark.unit
class TestEachRoleHoldsTheCallsItsRoutesMake:

    @pytest.mark.aws
    @pytest.mark.parametrize("sfn_status", ["SUCCEEDED", "FAILED"])
    @pytest.mark.parametrize("route_set", [le.ROUTE_SET_SERVICE, le.ROUTE_SET_DETAILS, le.ROUTE_SET_LOGS])
    def test_each_route_set_makes_only_the_calls_its_role_grants(self, route_set, sfn_status, monkeypatch):
        # A run found FAILED outside VAMS also marks its pipeline rows (a SUCCEEDED one leaves them).
        responses, calls, _counts = _recorded(route_set, monkeypatch, sfn_status)
        # Control: every route was served, so the recorded set is the routes' and not a 4xx/5xx short cut.
        assert [r["statusCode"] for r in responses] == [200] * len(responses), [
            (r["statusCode"], r.get("body", "")[:200]) for r in responses]
        assert calls, "no DynamoDB call was recorded"
        assert calls <= ROUTE_SET_CALLS[route_set], sorted(calls - ROUTE_SET_CALLS[route_set])

    @pytest.mark.aws
    @pytest.mark.parametrize("route_set", [le.ROUTE_SET_SERVICE, le.ROUTE_SET_DETAILS])
    def test_the_reconciling_route_sets_really_write(self, route_set, monkeypatch):
        # Control for the subset above: the reconcile (and on the service the abort) wrote the main and
        # pipeline rows and released the run's lock.
        _responses, calls, _counts = _recorded(route_set, monkeypatch, "FAILED")
        assert RECONCILE_WRITES | {("locks", "DeleteItem")} <= calls

    @pytest.mark.parametrize("route_set", list(_ROUTE_SET_HANDLERS))
    def test_each_route_set_reaches_only_tables_its_role_names(self, route_set):
        reached = _tables_reached(_ROUTE_SET_HANDLERS[route_set])
        granted = {target.split("/index/")[0] for target, _action in ROUTE_SET_CALLS[route_set]}
        assert reached <= granted, sorted(reached - granted)
        # Control: the reach is not empty and finds the per-set tables.
        assert {"main", "asset", "workflow"} <= reached

    def test_the_static_reach_separates_the_route_sets(self):
        # The three sets differ in the tables they reach, which is what lets each role drop the rest.
        assert "pipelineLogs" in _tables_reached(_ROUTE_SET_HANDLERS["logs"])
        assert "pipelineLogs" not in _tables_reached(_ROUTE_SET_HANDLERS["details"])
        assert "pipelineOutputResults" not in _tables_reached(_ROUTE_SET_HANDLERS["service"])

    def test_every_index_the_module_queries_is_granted(self):
        # The recorded routes do not reach every listing branch, so the index names the module spells
        # out are pinned too: a query on a new index fails here before it fails AccessDenied.
        with open(le.__file__, encoding="utf-8") as source:
            text = source.read()
        named = set(re.findall(r"""['"]IndexName['"]\s*:\s*['"](\w+)['"]""", text))
        named |= set(re.findall(r"""IndexName\s*=\s*['"](\w+)['"]""", text))
        # The lock table's holder index is queried by common.workflows.executionLocks, in the lock policy.
        granted = set().union(*ROUTE_SET_INDEXES.values()) - {"WorkflowExecutionLocksByHolderGSI"}
        assert named == granted

    @pytest.mark.aws
    @pytest.mark.parametrize("route_set", [le.ROUTE_SET_SERVICE, le.ROUTE_SET_DETAILS, le.ROUTE_SET_LOGS])
    def test_no_route_set_but_permanent_delete_deletes_or_puts_an_execution_row(self, route_set, monkeypatch):
        _responses, calls, counts = _recorded(route_set, monkeypatch)
        assert set(counts.values()) == {2}, counts
        # Control: the routes ran against the tables, so the two checks below are not vacuous.
        assert ("main", "Query") in calls, "the recorded routes read no execution row"
        assert not {c for c in calls if c[1] in {"PutItem", "BatchWriteItem", "TransactWriteItems"}}
        assert not {c for c in calls if c[1] == "DeleteItem" and c[0] != "locks"}

    @pytest.mark.aws
    def test_permanent_delete_makes_exactly_the_calls_its_role_grants(self, monkeypatch):
        import boto3
        from moto import mock_aws
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            _create_tables(ddb)
            _seed(ddb)
            responses, calls = _drive(
                ddb, [_event("permanentDelete", body=json.dumps({"confirmDelete": True}))],
                monkeypatch, le.ROUTE_SET_PERMANENT_DELETE)
            assert responses[0]["statusCode"] == 200, responses[0]
            # Control: the execution's rows are gone from every table, so every delete really ran.
            for table in (le.workflow_execution_database_v2, le.workflow_execution_inputs_table,
                          le.workflow_execution_configuration_table, le.pipeline_executions_table,
                          le.pipeline_execution_input_files_table, le.pipeline_execution_logs_table):
                ids = {item.get("workflowExecutionId") or item.get("pipelineExecutionId")
                       for item in ddb.Table(table).scan()["Items"]}
                assert FINISHED_ID not in ids and FINISHED_PEXEC not in ids, table
        assert calls == PERMANENT_DELETE_CALLS, (
            f"not granted: {sorted(calls - PERMANENT_DELETE_CALLS)}; "
            f"granted but unused: {sorted(PERMANENT_DELETE_CALLS - calls)}")

    @pytest.mark.aws
    def test_permanent_delete_of_a_row_left_non_terminal_reads_step_functions_only(self, monkeypatch):
        # The in-progress guard of a stale non-terminal row describes the Step Functions execution and
        # makes no DynamoDB call the role does not hold.
        import boto3
        from moto import mock_aws
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            _create_tables(ddb)
            _seed(ddb)
            event = _event("permanentDelete", body=json.dumps({"confirmDelete": True}))
            event["requestContext"]["http"]["path"] = f"/workflows/executions/{RUNNING_ID}/permanent"
            event["pathParameters"]["executionId"] = RUNNING_ID
            responses, calls = _drive(ddb, [event], monkeypatch, le.ROUTE_SET_PERMANENT_DELETE)
        assert responses[0]["statusCode"] == 200, responses[0]
        assert calls <= PERMANENT_DELETE_CALLS, sorted(calls - PERMANENT_DELETE_CALLS)
