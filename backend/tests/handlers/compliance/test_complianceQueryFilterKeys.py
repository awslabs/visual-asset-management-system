# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""No compliance read asks DynamoDB to filter on a key attribute of the table or index it queries.

DynamoDB refuses a Query whose FilterExpression names a key attribute of the table or index read
("Filter Expression can only contain non-primary key attributes"); moto accepts it, so a handler
unit test cannot see the refusal, and the database overview's text filter answered 500 in a
deployment while every test passed (D64). This guard drives every compliance listing, each with
every combination of its filters, plus the other filtered reads of the compliance services, against
DynamoDB doubles built from the CDK table definitions (`_cdkTables`), records each Query and Scan
request as botocore sends it, and checks every Query's FilterExpression against the keys of the
table or index it names. Scans are recorded too (each listing's path is visible in the record), but
the rule is DynamoDB's for Query only: a Scan may filter on a key attribute.

Also here: the overview's text filter on its default path (the table's own key, assetId order)
still matches a row written before `assetSearchText` existed, by its id, in code.
"""

import itertools
import json
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import boto3
from boto3.dynamodb.conditions import ConditionExpressionBuilder
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    SYNC_RULES_SCHEMA_BODY, USER, body_of, claims_for, enforcer, rest_event, schema_row,
)
from common.compliance import listing
from common.compliance.auditRecord import build_audit_item
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceCascadeService as cascade_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceQuarantineService as quarantine_svc
from handlers.compliance import complianceSchemaBindingService as binding_svc
from handlers.compliance import complianceSchemaService as schema_svc
from handlers.compliance import complianceTrigger as trigger

DB = "db-guard"
OTHER_DB = "db-other"
SCHEMA = "schema-guard"
NAMED = "a-named"
LEGACY = "Legacy-Upper"  # a row written before the name attributes existed
CASCADE_ID = "5b1e2c3d-0000-4000-8000-0000000000aa"
SERVICES = (audit_svc, cascade_svc, evaluate_svc, quarantine_svc, binding_svc, schema_svc)
HANDLER_MODULES = SERVICES + (store, trigger)

_FUNCTIONS = {"contains", "begins_with", "attribute_exists", "attribute_not_exists",
              "attribute_type", "size", "and", "or", "not", "between", "in"}


def filter_attribute_names(request):
    """The attribute names a request's FilterExpression reads: its `#name` placeholders resolved
    through ExpressionAttributeNames, and any bare names."""
    expression = request.get("FilterExpression") or ""
    names = request.get("ExpressionAttributeNames") or {}
    used = {names.get(token, token) for token in re.findall(r"#[A-Za-z0-9_]+", expression)}
    for bare in re.findall(r"(?<![#:\w])([A-Za-z_][A-Za-z0-9_]*)", expression):
        if bare.lower() not in _FUNCTIONS:
            used.add(bare)
    return used


def key_attribute_violations(calls, tables_by_name):
    """`(operation, table, index, keys named in the filter)` for each recorded Query whose
    FilterExpression names a key attribute of the table or index it reads."""
    violations = []
    for operation, request in calls:
        if operation != "Query" or not request.get("FilterExpression"):
            continue
        table = tables_by_name[request["TableName"]]
        keys = set(table.key_attributes(request.get("IndexName")))
        named = filter_attribute_names(request) & keys
        if named:
            violations.append((operation, table.logical_id, request.get("IndexName"),
                               tuple(sorted(named))))
    return violations



def _now(offset_hours=0):
    return (datetime.now(timezone.utc) + timedelta(hours=offset_hours)).isoformat()


def _seed(tables):
    state = tables["complianceAssetStateStorage"]
    for database_id in (DB, OTHER_DB):
        tables["databaseStorage"].put_item(Item={"databaseId": database_id,
                                                 "complianceSchemaName": SCHEMA})
    tables["databaseStorage"].put_item(Item={"databaseId": "gone#deleted"})
    rows = [
        dict(databaseId=DB, assetId=NAMED, complianceState="quarantined", schemaName=SCHEMA,
             schemaSource="database", lastEvaluationStatus="error",
             **listing.asset_name_attributes(NAMED, "Turbine Blade")),
        dict(databaseId=DB, assetId="b-named", complianceState="compliant", schemaName=SCHEMA,
             schemaSource="asset", lastEvaluationStatus="completed",
             **listing.asset_name_attributes("b-named", "Bracket")),
        dict(databaseId=DB, assetId=LEGACY, complianceState="pending_evaluation",
             schemaName=SCHEMA, schemaSource="database"),
        dict(databaseId=OTHER_DB, assetId="c-other", complianceState="quarantined",
             schemaName=SCHEMA, schemaSource="database",
             **listing.asset_name_attributes("c-other", "Another")),
    ]
    for row in rows:
        state.put_item(Item=row)
    audit = tables["complianceAuditStorage"]
    for index, (database_id, asset_id, event_type) in enumerate((
            (DB, NAMED, "compliance_check"), (DB, LEGACY, "exception_granted"),
            (OTHER_DB, "c-other", "compliance_check"))):
        item = build_audit_item(database_id, asset_id, event_type, USER, {"i": index},
                                timestamp=f"2026-10-0{index + 1}T00:00:00+00:00",
                                asset_name="Turbine Blade")
        item["entryId"] = f"entry-{index}"
        audit.put_item(Item=item)
    tables["complianceCascadeStorage"].put_item(Item={
        "cascadeId": CASCADE_ID, "state": "pending_approval", "createdAt": _now(),
        "approvalTimeoutAt": _now(24), "triggeredByDatabaseId": DB, "triggeredByAssetId": NAMED,
        "triggeredByAssetName": "Turbine Blade", "searchText": "turbine blade a-named",
        "schemaName": SCHEMA, "totalNodes": 0,
    })
    schemas = tables["complianceSchemaStorage"]
    schemas.put_item(Item=schema_row(SCHEMA, body=SYNC_RULES_SCHEMA_BODY, database_id=DB))
    schemas.put_item(Item=schema_row("schema-global", body=SYNC_RULES_SCHEMA_BODY))
    tables["metadataSchemaStorageV2"].put_item(Item={
        "metadataSchemaId": "ms-1", "databaseId:metadataEntityType": f"{DB}:assetMetadata",
        "databaseId": DB, "metadataEntityType": "assetMetadata", "schemaName": "Asset Schema",
        "fields": {"fields": []}})


def _requests():
    """Every listing request, with every combination of its filters (all must answer 200)."""
    requests = []
    for state, sort, text, schema_name, source, error in itertools.product(
            (None, "quarantined"), (None, "name"), (None, "legacy"), (None, SCHEMA),
            (None, "database"), (None, "true", "false")):
        params = {name: value for name, value in (
            ("state", state), ("sort", sort), ("text", text), ("schemaName", schema_name),
            ("schemaSource", source), ("evaluationError", error)) if value}
        requests.append((evaluate_svc, rest_event("GET", f"/compliance/state/{DB}",
                                                  {"databaseId": DB}, query_params=params or None)))
    for database_id, text in itertools.product((None, DB), (None, "turbine")):
        params = {name: value for name, value in (("databaseId", database_id), ("text", text))
                  if value}
        requests.append((quarantine_svc, rest_event("GET", "/compliance/quarantine",
                                                    query_params=params or None)))
    for state, database_id, text in itertools.product(
            (None, "pending_approval", "executing", "completed", "aborted"), (None, DB),
            (None, "turbine")):
        params = {name: value for name, value in (
            ("state", state), ("databaseId", database_id), ("text", text)) if value}
        requests.append((cascade_svc, rest_event("GET", "/compliance/cascades",
                                                 query_params=params or None)))
    for event_type, database_id, asset_id, text, window in itertools.product(
            (None, "compliance_check"), (None, DB), (None, NAMED), (None, "turbine"),
            (None, ("2026-10-01T00:00:00+00:00", "2026-10-31T00:00:00+00:00"))):
        if asset_id and not database_id:
            continue
        params = {name: value for name, value in (
            ("eventType", event_type), ("databaseId", database_id), ("assetId", asset_id),
            ("text", text)) if value}
        if window:
            params.update(startDate=window[0], endDate=window[1])
        requests.append((audit_svc, rest_event("GET", "/compliance/audit",
                                               query_params=params or None)))
    asset_params = {"databaseId": DB, "assetId": NAMED}
    requests += [
        (audit_svc, rest_event("GET", f"/compliance/audit/{DB}/{NAMED}", asset_params)),
        (evaluate_svc, rest_event("GET", f"/compliance/evaluations/{DB}/{NAMED}", asset_params)),
        (evaluate_svc, rest_event("GET", f"/compliance/state/{DB}/{NAMED}", asset_params)),
        (cascade_svc, rest_event("GET", f"/compliance/cascades/{CASCADE_ID}",
                                 {"cascadeId": CASCADE_ID})),
        (schema_svc, rest_event("GET", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA})),
        (binding_svc, rest_event("GET", f"/compliance/bind/{DB}", {"databaseId": DB})),
    ]
    for database_id in (None, DB, "GLOBAL"):
        requests.append((schema_svc, rest_event(
            "GET", "/compliance/schemas",
            query_params={"databaseId": database_id} if database_id else None)))
    return requests


@pytest.fixture(scope="module")
def recorded():
    """Every request of `_requests()` and the other filtered compliance reads, run against
    CDK-shaped doubles: `{"calls": [(operation, request)], "responses": [...], "tables": ...}`."""
    tables_by_test_name = _cdkTables.tables_by_test_name()
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        calls = []

        def record(params, model, **kwargs):
            calls.append((model.name, json.loads(params["body"])))

        resource.meta.client.meta.events.register("before-call.dynamodb.Query", record)
        resource.meta.client.meta.events.register("before-call.dynamodb.Scan", record)
        patches, by_key = _cdkTables.table_doubles(resource, HANDLER_MODULES)
        for active in patches:
            active.start()
        try:
            _seed(by_key)
            calls.clear()
            responses = []
            for module in SERVICES:
                patches_for_module = [
                    patch.object(module, "request_to_claims", claims_for(USER)),
                    patch.object(module, "CasbinEnforcer", return_value=enforcer()),
                ]
                for active in patches_for_module:
                    active.start()
            try:
                for module, event in _requests():
                    response = module.lambda_handler(event, MagicMock())
                    responses.append((event["path"], event["queryStringParameters"],
                                      response["statusCode"], response.get("body")))
                # The other filtered reads of the compliance services.
                store.get_metadata_schema(DB, "Asset Schema", "assetMetadata")
                store.get_metadata_schema(DB, "No Such Schema", "assetMetadata")
                store.databases_bound_to_schema(SCHEMA)
                trigger.pending_cascade_exists(DB, NAMED)
                schema_svc._schema_is_bound(SCHEMA)
                schema_svc._schema_is_bound("schema-unbound")
            finally:
                patch.stopall()
        finally:
            patch.stopall()
        tables = {name: table for name, (_, table) in tables_by_test_name.items()}
        yield {"calls": list(calls), "responses": responses, "tables": tables}


@pytest.mark.unit
class TestNoFilterOnAKeyAttribute:

    def test_every_listing_request_answers_200(self, recorded):
        failed = [(path, params, status, body) for path, params, status, body
                  in recorded["responses"] if status != 200]
        assert not failed, failed

    def test_no_query_filters_on_a_key_attribute_of_what_it_reads(self, recorded):
        assert key_attribute_violations(recorded["calls"], recorded["tables"]) == []

    def test_the_guard_saw_each_listing_read_with_a_filter(self, recorded):
        filtered = {(recorded["tables"][request["TableName"]].logical_id, request.get("IndexName"))
                    for operation, request in recorded["calls"]
                    if operation == "Query" and request.get("FilterExpression")}
        for expected in (
                ("ComplianceAssetStateStorageTable", None),  # overview default path
                ("ComplianceAssetStateStorageTable", "ComplianceStateIndex"),  # state, quarantine
                ("ComplianceAssetStateStorageTable", "DatabaseAssetNameIndex"),  # sort=name
                ("ComplianceCascadeStorageTable", "StateIndex"),
                ("ComplianceAuditStorageTable", "AuditByDateGSI"),
                ("ComplianceAuditStorageTable", "EventTypeIndex"),
                ("ComplianceAuditStorageTable", "AssetIndex"),
                ("MetadataSchemaStorageTableV2", "DatabaseIdMetadataEntityTypeIndex"),
                ("MetadataSchemaStorageTableV2", "DatabaseIdIndex"),
        ):
            assert expected in filtered, expected
        scanned = {recorded["tables"][request["TableName"]].logical_id
                   for operation, request in recorded["calls"] if operation == "Scan"}
        assert {"DatabaseStorageTable", "ComplianceSchemaStorageTable"} <= scanned

    def test_the_guard_reports_a_filter_on_a_table_or_index_key(self, recorded):
        tables = recorded["tables"]
        state_table = next(name for name, table in tables.items()
                           if table.logical_id == "ComplianceAssetStateStorageTable")
        base = {"TableName": state_table, "FilterExpression": "contains(#n0, :v0) OR #n1 = :v1",
                "ExpressionAttributeNames": {"#n0": "assetSearchText", "#n1": "assetId"}}
        on_index = dict(base, IndexName="ComplianceStateIndex",
                        ExpressionAttributeNames={"#n0": "assetSearchText", "#n1": "databaseId"})
        legal = dict(base, IndexName="DatabaseAssetNameIndex")
        scan = dict(base)
        assert key_attribute_violations(
            [("Query", base), ("Query", on_index), ("Query", legal), ("Scan", scan)], tables) == [
            ("Query", "ComplianceAssetStateStorageTable", None, ("assetId",)),
            ("Query", "ComplianceAssetStateStorageTable", "ComplianceStateIndex", ("databaseId",)),
        ]


@pytest.mark.unit
class TestTheOverviewTextFilterOnTheTableKeyPath:

    def _overview_assets(self, recorded, text):
        for path, params, status, body in recorded["responses"]:
            if path == f"/compliance/state/{DB}" and params == {"text": text}:
                assert status == 200, body
                return sorted(row["assetId"] for row in json.loads(body)["assets"])
        raise AssertionError(f"no overview request with text={text}")

    def test_a_row_without_search_text_matches_on_its_id_whatever_the_case(self, recorded):
        assert self._overview_assets(recorded, "legacy") == [LEGACY]

    def test_the_text_filter_needs_no_key_attribute_in_its_expression(self):
        condition, accept = listing.asset_text_filter("legacy")
        expression = ConditionExpressionBuilder().build_expression(
            condition)
        assert set(expression.attribute_name_placeholders.values()) == {"assetSearchText"}
        assert accept({"assetId": "Legacy-Upper"}) and not accept({"assetId": "other"})
        assert accept({"assetId": "x", "assetSearchText": "named x"})
