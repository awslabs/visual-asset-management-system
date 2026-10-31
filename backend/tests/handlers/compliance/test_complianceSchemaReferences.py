# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Where a metadata rule's schema is looked up, and which databases a compliance schema may name.

`metadataSchemaRef.databaseId` is optional. Omitted, the metadata schema is looked up in the
evaluated asset's database, then GLOBAL, so a GLOBAL compliance schema bound to several databases
uses each database's own metadata schema of that name. Given, it pins the lookup, and the schema
service accepts only GLOBAL or the compliance schema's own database: a GLOBAL compliance schema may
reference GLOBAL only, a database-scoped one GLOBAL or its database. Pipeline rules' workflow and
pipeline databases follow the same rule."""

from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, SCHEMA, USER, body_of, claims_for, enforcer, put_items, rest_event,
)
from backend.tests.handlers.compliance.test_complianceEvaluationStore import (
    Tables, metadata_schema_row,
)
from common.compliance import evaluationEngine as engine
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceSchemaService as svc
from models.compliance import MetadataRule, MetadataSchemaRef

SVC = "handlers.compliance.complianceSchemaService"


def _metadata_rule(ref):
    return {"ruleType": "metadata", "enforcement": "warn", "metadataSchemaRef": ref,
            "checks": [{"name": "req", "validateRequired": True}]}


def _pipeline_rule(database_id="GLOBAL", pipeline_database_id="GLOBAL"):
    return {"ruleType": "pipeline", "enforcement": "warn",
            "pipelineRef": {"databaseId": database_id, "workflowId": "wf-1",
                            "pipelineDatabaseId": pipeline_database_id, "pipelineId": "pipe-1"},
            "checks": [{"name": "ok", "outputField": "execution_success",
                        "tolerance": {"operator": "eq", "value": 1}}]}


def _body(*rules):
    return {"schemaFormat": "vams-rules-v1",
            "rules": {f"r{index}": rule for index, rule in enumerate(rules)}}


@pytest.mark.unit
class TestTheReferenceModel:

    def test_the_database_is_optional(self):
        ref = MetadataSchemaRef(schemaName="defaultAsset")
        assert ref.databaseId is None
        assert MetadataRule(**_metadata_rule({"schemaName": "defaultAsset"})).metadataSchemaRef \
            .databaseId is None

    def test_a_given_database_is_still_validated(self):
        with pytest.raises(ValueError):
            MetadataSchemaRef(databaseId="bad id!", schemaName="defaultAsset")
        assert MetadataSchemaRef(databaseId="GLOBAL", schemaName="s").databaseId == "GLOBAL"


@pytest.mark.unit
class TestTheLookupDatabase:

    def test_an_omitted_database_is_the_evaluated_assets(self):
        ref = MetadataSchemaRef(schemaName="defaultAsset")
        assert engine.metadata_schema_lookup_database(ref, "db-7") == "db-7"
        assert engine.metadata_schema_ref_label(ref) == "defaultAsset"
        assert engine.metadata_schema_ref_key(ref).startswith(engine.ASSET_DATABASE_REF + "/")

    def test_a_pinned_database_is_kept(self):
        ref = MetadataSchemaRef(databaseId="GLOBAL", schemaName="defaultAsset")
        assert engine.metadata_schema_lookup_database(ref, "db-7") == "GLOBAL"
        assert engine.metadata_schema_ref_label(ref) == "GLOBAL/defaultAsset"

    @pytest.mark.parametrize("database_id", ["db-a", "db-b"])
    def test_the_evaluation_looks_an_omitted_reference_up_in_the_assets_database(self, database_id):
        """One GLOBAL compliance schema evaluated in two databases reads each one's own schema."""
        body = _body(_metadata_rule({"schemaName": "Asset Schema"}))
        tables = Tables(schema_body=body)
        partitions = []

        def query(**kwargs):
            partitions.append(kwargs["KeyConditionExpression"]._values[1])
            return {"Items": [metadata_schema_row(database_id=database_id)]
                    if partitions[-1] == f"{database_id}:assetMetadata" else []}

        tables.metadata_schema.query.side_effect = query
        result = tables.run(store.run_evaluation, database_id, ASSET, SCHEMA, USER)
        assert partitions[0] == f"{database_id}:assetMetadata"
        rule = result["ruleResults"][0]
        assert rule["status"] == "evaluated"
        assert rule["expected"]["schemaRef"] == "Asset Schema"


@pytest.mark.unit
class TestTheReferencesASchemaMayName:

    @pytest.mark.parametrize("rule", [
        _metadata_rule({"schemaName": "s"}),
        _metadata_rule({"databaseId": "GLOBAL", "schemaName": "s"}),
        _pipeline_rule(),
    ])
    def test_a_global_schema_may_reference_global_or_omit(self, rule):
        assert svc.schema_reference_error(_body(rule), "GLOBAL") is None

    @pytest.mark.parametrize("rule, field", [
        (_metadata_rule({"databaseId": "db-a", "schemaName": "s"}), "metadataSchemaRef.databaseId"),
        (_pipeline_rule(database_id="db-a"), "pipelineRef.databaseId"),
        (_pipeline_rule(pipeline_database_id="db-a"), "pipelineRef.pipelineDatabaseId"),
    ])
    def test_a_global_schema_may_not_reference_one_database(self, rule, field):
        error = svc.schema_reference_error(_body(_metadata_rule({"schemaName": "s"}), rule), "GLOBAL")
        assert error.startswith("rules[1]: a GLOBAL compliance schema may reference only GLOBAL")
        assert field in error and "db-a" not in error

    @pytest.mark.parametrize("database_id", [None, "GLOBAL", "db-a"])
    def test_a_database_schema_may_reference_global_or_its_own_database(self, database_id):
        ref = {"schemaName": "s"} if database_id is None else {"databaseId": database_id,
                                                                "schemaName": "s"}
        body = _body(_metadata_rule(ref), _pipeline_rule("db-a", "GLOBAL"))
        assert svc.schema_reference_error(body, "db-a") is None

    def test_a_database_schema_may_not_reference_another_database(self):
        body = _body(_metadata_rule({"databaseId": "db-b", "schemaName": "s"}))
        error = svc.schema_reference_error(body, "db-a")
        assert error.startswith("rules[0]: a database-scoped compliance schema may reference only")
        assert "db-b" not in error


def _run(event, latest=None, database_exists=True):
    schema_table = MagicMock(name="schema_table")
    schema_table.query.return_value = {"Items": [latest] if latest else []}
    database_table = MagicMock(name="database_table")
    database_table.get_item.return_value = {"Item": {"databaseId": "db-a"}} if database_exists else {}
    with patch(f"{SVC}.request_to_claims", claims_for(USER)), \
            patch(f"{SVC}.CasbinEnforcer", return_value=enforcer()), \
            patch(f"{SVC}.schema_table", schema_table), \
            patch(f"{SVC}.database_table", database_table):
        response = svc.lambda_handler(event, MagicMock())
    return response, schema_table


@pytest.mark.unit
class TestTheApiRefusesACrossDatabaseReference:

    def test_registering_a_global_schema_that_names_a_database_is_refused(self):
        body = _body(_metadata_rule({"databaseId": "db-a", "schemaName": "s"}))
        response, table = _run(rest_event("POST", "/compliance/schemas",
                                          body={"schemaName": SCHEMA, "schemaBody": body}))
        assert response["statusCode"] == 400
        assert "may reference only GLOBAL" in body_of(response)["message"]
        assert put_items(table) == []

    def test_registering_a_database_schema_that_names_its_database_is_accepted(self):
        body = _body(_metadata_rule({"databaseId": "db-a", "schemaName": "s"}))
        response, table = _run(rest_event("POST", "/compliance/schemas", body={
            "schemaName": SCHEMA, "schemaBody": body, "databaseId": "db-a"}))
        assert response["statusCode"] == 200, response
        assert put_items(table)[0]["databaseId"] == "db-a"

    def test_moving_a_schema_to_global_while_it_names_its_database_is_refused(self):
        body = _body(_metadata_rule({"databaseId": "db-a", "schemaName": "s"}))
        latest = {"schemaName": SCHEMA, "internalVersion": 1, "databaseId": "db-a",
                  "schemaBody": __import__("json").dumps(body)}
        response, table = _run(rest_event("PUT", f"/compliance/schemas/{SCHEMA}",
                                          {"schemaName": SCHEMA}, body={"databaseId": "GLOBAL"}),
                               latest=latest)
        assert response["statusCode"] == 400
        assert put_items(table) == []
