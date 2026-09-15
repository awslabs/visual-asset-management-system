# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceSchemaService: route dispatch, both authorization tiers, input validation, and the
schema registry's write semantics (new version per write; DELETE refuses a bound schema and removes
every version of an unbound one)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    RULES_SCHEMA_BODY, SCHEMA, USER, body_of, claims_for, enforcer, put_items, rest_event,
    schema_row,
)
from handlers.compliance import complianceSchemaService as svc

MOD = "handlers.compliance.complianceSchemaService"


def _tables(schema_rows=None, bound=False, database_exists=True):
    """Mock tables: the schema table serves `schema_rows` to both the version query and the GSI /
    scan listings; the asset-state GSI and the database scan answer the DELETE binding check."""
    schema_table = MagicMock(name="schema_table")
    schema_table.query.return_value = {"Items": list(schema_rows or [])}
    schema_table.scan.return_value = {"Items": list(schema_rows or [])}
    state_table = MagicMock(name="asset_state_table")
    state_table.query.return_value = {"Items": [{"assetId": "a"}] if bound else []}
    database_table = MagicMock(name="database_table")
    database_table.scan.return_value = {"Items": []}
    database_table.get_item.return_value = {"Item": {"databaseId": "db1"}} if database_exists else {}
    audit_table = MagicMock(name="audit_table")
    return schema_table, state_table, database_table, audit_table


def _run(event, tokens=(USER,), api=True, obj=True, **table_kwargs):
    schema_table, state_table, database_table, audit_table = _tables(**table_kwargs)
    with patch(f"{MOD}.request_to_claims", claims_for(*tokens)), \
            patch(f"{MOD}.CasbinEnforcer", return_value=enforcer(api=api, obj=obj)), \
            patch(f"{MOD}.schema_table", schema_table), \
            patch(f"{MOD}.asset_state_table", state_table), \
            patch(f"{MOD}.database_table", database_table), \
            patch(f"{MOD}.audit_table", audit_table):
        response = svc.lambda_handler(event, MagicMock())
    return response, {"schema": schema_table, "state": state_table,
                      "database": database_table, "audit": audit_table}


@pytest.mark.unit
class TestRouteDispatch:
    """Each of the five method+path pairs reaches its operation; anything else is refused."""

    def test_get_collection_lists_schemas_with_null_rest_params(self):
        response, tables = _run(rest_event("GET", "/compliance/schemas"),
                                schema_rows=[schema_row(version=1), schema_row(version=2)])
        assert response["statusCode"] == 200
        schemas = body_of(response)["schemas"]
        assert [s["schemaName"] for s in schemas] == [SCHEMA]
        assert schemas[0]["version"] == 2
        tables["schema"].scan.assert_called()

    def test_get_collection_with_database_filter_reads_the_gsi(self):
        response, tables = _run(
            rest_event("GET", "/compliance/schemas", query_params={"databaseId": "db1"}),
            schema_rows=[schema_row()])
        assert response["statusCode"] == 200
        partitions = [c.kwargs["KeyConditionExpression"]._values[1]
                      for c in tables["schema"].query.call_args_list]
        assert partitions == ["GLOBAL", "db1"]
        assert {c.kwargs["IndexName"] for c in tables["schema"].query.call_args_list} == {"DatabaseIdIndex"}

    def test_post_collection_registers_a_schema(self):
        response, tables = _run(
            rest_event("POST", "/compliance/schemas",
                       body={"schemaName": SCHEMA, "schemaBody": RULES_SCHEMA_BODY}))
        assert response["statusCode"] == 200, response
        assert body_of(response)["internalVersion"] == 1
        written = put_items(tables["schema"])[0]
        assert written["schemaName"] == SCHEMA
        assert written["databaseId"] == "GLOBAL"
        assert written["registeredBy"] == USER
        assert json.loads(written["schemaBody"]) == RULES_SCHEMA_BODY

    def test_get_by_name_returns_the_latest_version(self):
        response, _ = _run(
            rest_event("GET", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA}),
            schema_rows=[schema_row(version=3)])
        assert response["statusCode"] == 200
        assert body_of(response)["version"] == 3
        assert body_of(response)["schemaBody"] == RULES_SCHEMA_BODY

    def test_put_by_name_writes_the_next_version(self):
        response, tables = _run(
            rest_event("PUT", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA},
                       body={"description": "updated"}),
            schema_rows=[schema_row(version=2)])
        assert response["statusCode"] == 200, response
        assert body_of(response)["internalVersion"] == 3
        assert put_items(tables["schema"])[0]["description"] == "updated"

    def test_delete_by_name_reaches_delete(self):
        response, _ = _run(
            rest_event("DELETE", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA}),
            schema_rows=[schema_row()])
        assert response["statusCode"] == 200
        assert body_of(response)["versionsDeleted"] == 1

    @pytest.mark.parametrize("method,path", [
        ("PATCH", "/compliance/schemas"),
        ("GET", "/compliance/schemas/x/versions"),
        ("POST", f"/compliance/schemas/{SCHEMA}"),
        ("DELETE", "/compliance/schemas"),
        ("PUT", "/compliance/schemas"),
    ])
    def test_an_unknown_method_or_path_is_refused(self, method, path):
        response, _ = _run(rest_event(method, path))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Method not allowed"


@pytest.mark.unit
class TestAuthorization:

    @pytest.mark.parametrize("tokens", [(), (USER,)], ids=["empty-tokens", "api-denied"])
    def test_tier_one_denies_before_any_read(self, tokens):
        response, tables = _run(rest_event("GET", "/compliance/schemas"), tokens=tokens,
                                api=False, schema_rows=[schema_row()])
        assert response["statusCode"] == 403
        tables["schema"].scan.assert_not_called()
        tables["schema"].query.assert_not_called()

    def test_tier_one_with_empty_tokens_denies_even_when_the_api_check_would_pass(self):
        response, tables = _run(rest_event("GET", "/compliance/schemas"), tokens=(), api=True)
        assert response["statusCode"] == 403
        tables["schema"].scan.assert_not_called()

    @pytest.mark.parametrize("method,body", [
        ("GET", None), ("PUT", {"description": "x"}), ("DELETE", None),
    ])
    def test_tier_two_denial_on_a_single_schema(self, method, body):
        response, tables = _run(
            rest_event(method, f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA}, body=body),
            obj=False, schema_rows=[schema_row()])
        assert response["statusCode"] == 403
        tables["schema"].put_item.assert_not_called()
        tables["schema"].batch_writer.assert_not_called()

    def test_tier_two_object_carries_the_schema_name_and_action(self):
        instance = enforcer()
        schema_table, state_table, database_table, audit_table = _tables([schema_row()])
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=instance), \
                patch(f"{MOD}.schema_table", schema_table), \
                patch(f"{MOD}.asset_state_table", state_table), \
                patch(f"{MOD}.database_table", database_table), \
                patch(f"{MOD}.audit_table", audit_table):
            svc.lambda_handler(
                rest_event("DELETE", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA}),
                MagicMock())
        instance.enforce.assert_called_once_with(
            {"object__type": "complianceSchema", "complianceSchemaName": SCHEMA}, "DELETE")

    def test_post_denied_at_tier_two_writes_nothing(self):
        response, tables = _run(
            rest_event("POST", "/compliance/schemas",
                       body={"schemaName": SCHEMA, "schemaBody": RULES_SCHEMA_BODY}),
            obj=False)
        assert response["statusCode"] == 403
        tables["schema"].put_item.assert_not_called()

    def test_listing_filters_to_the_schemas_the_caller_may_get(self):
        response, _ = _run(rest_event("GET", "/compliance/schemas"), obj=False,
                           schema_rows=[schema_row()])
        assert response["statusCode"] == 200
        assert body_of(response)["schemas"] == []


@pytest.mark.unit
class TestValidation:

    @pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
    def test_a_bad_schema_name_is_rejected(self, method):
        bad = "bad<schema-name>"
        response, tables = _run(
            rest_event(method, f"/compliance/schemas/{bad}", {"schemaName": bad}, body={}),
            schema_rows=[schema_row()])
        assert response["statusCode"] == 400
        assert bad not in response["body"]
        tables["schema"].query.assert_not_called()

    def test_a_bad_database_filter_is_rejected(self):
        bad = "bad<database-id>!"
        response, _ = _run(rest_event("GET", "/compliance/schemas",
                                      query_params={"databaseId": bad}))
        assert response["statusCode"] == 400
        assert bad not in response["body"]

    def test_a_body_that_is_not_json_is_rejected(self):
        response, _ = _run(rest_event("POST", "/compliance/schemas", body="{not json"))
        assert response["statusCode"] == 400
        assert "Invalid JSON" in body_of(response)["message"]

    @pytest.mark.parametrize("body,bad", [
        ({"schemaBody": RULES_SCHEMA_BODY}, None),
        ({"schemaName": "zq", "schemaBody": RULES_SCHEMA_BODY}, "zq"),
        ({"schemaName": SCHEMA, "schemaBody": "not-an-object"}, "not-an-object"),
        ({"schemaName": SCHEMA, "schemaBody": {}}, None),
        ({"schemaName": SCHEMA, "schemaBody": RULES_SCHEMA_BODY, "databaseId": "zq"}, "zq"),
    ], ids=["missing-name", "short-name", "string-body", "empty-body", "short-database"])
    def test_a_bad_register_body_is_rejected_by_the_model(self, body, bad):
        response, tables = _run(rest_event("POST", "/compliance/schemas", body=body))
        assert response["statusCode"] == 400
        if bad is not None:
            assert bad not in response["body"]
        tables["schema"].put_item.assert_not_called()

    def test_a_rules_body_with_an_unknown_rule_type_is_rejected(self):
        body = {"schemaFormat": "vams-rules-v1",
                "rules": {"secret-rule-name": {"ruleType": "not<a-rule-type>", "enforcement": "warn"}}}
        response, tables = _run(rest_event("POST", "/compliance/schemas",
                                           body={"schemaName": SCHEMA, "schemaBody": body}))
        assert response["statusCode"] == 400
        assert "Invalid schema" in body_of(response)["message"]
        assert "secret-rule-name" not in response["body"]
        assert "not<a-rule-type>" not in response["body"]
        tables["schema"].put_item.assert_not_called()

    def test_a_rules_body_whose_rule_is_not_an_object_is_rejected(self):
        body = {"schemaFormat": "vams-rules-v1", "rules": {"secret-rule-name": "secret<value>"}}
        response, tables = _run(rest_event("POST", "/compliance/schemas",
                                           body={"schemaName": SCHEMA, "schemaBody": body}))
        assert response["statusCode"] == 400
        assert "secret-rule-name" not in response["body"]
        assert "secret<value>" not in response["body"]
        tables["schema"].put_item.assert_not_called()

    def test_a_rules_body_whose_rule_fails_its_model_is_rejected(self):
        body = {"schemaFormat": "vams-rules-v1", "rules": {
            "fine-rule": RULES_SCHEMA_BODY["rules"]["has-parent"],
            "secret-rule-name": {"ruleType": "pipeline", "enforcement": "warn",
                                 "pipelineRef": {"databaseId": "GLOBAL", "workflowId": "wf-1"},
                                 "checks": [{"name": "c", "outputField": "x",
                                             "tolerance": {"operator": "lte", "value": 1}}]}}}
        response, tables = _run(rest_event("POST", "/compliance/schemas",
                                           body={"schemaName": SCHEMA, "schemaBody": body}))
        assert response["statusCode"] == 400
        message = body_of(response)["message"]
        assert "secret-rule-name" not in response["body"]
        assert "PipelineRule" not in message
        assert "validation error" not in message
        assert "rules[1]" in message
        assert "pipelineDatabaseId" in message
        tables["schema"].put_item.assert_not_called()

    def test_a_rules_body_error_does_not_name_the_model_class(self):
        body = {"schemaFormat": "vams-rules-v1", "rules": "not<an-object>"}
        response, _ = _run(rest_event("POST", "/compliance/schemas",
                                      body={"schemaName": SCHEMA, "schemaBody": body}))
        assert response["statusCode"] == 400
        assert "VamsRulesV1Schema" not in response["body"]
        assert "validation error" not in response["body"]
        assert "not<an-object>" not in response["body"]

    @pytest.mark.parametrize("body,bad", [
        ({"type": "not<a-type>"}, "not<a-type>"),
        ({"type": ["object", "not<a-type>"]}, "not<a-type>"),
        ({"type": "object", "properties": {"secret<property>": {"type": "not<a-type>"}}},
         "secret<property>"),
        ({"type": "object", "properties": {"secret<property>": {"type": "not<a-type>"}}},
         "not<a-type>"),
        ({"type": "object", "properties": {"secret<property>": {"type": ["not<a-type>"]}}},
         "secret<property>"),
        ({"type": "object", "properties": {"secret<property>": "not<an-object>"}}, "secret<property>"),
        ({"type": "object", "properties": {"secret<property>": "not<an-object>"}}, "not<an-object>"),
        ({"type": "object", "properties": {"secret<property>": {"enum": "a"}}}, "secret<property>"),
        ({"type": "object", "properties": {"secret<property>": {"minimum": "1"}}}, "secret<property>"),
        ({"type": "object", "properties": {"secret<property>": {"maximum": "1"}}}, "secret<property>"),
        ({"type": "object", "properties": {"a": {}}, "required": ["secret<field>"]}, "secret<field>"),
        ({"type": "array", "items": {"type": "not<a-type>"}}, "not<a-type>"),
    ], ids=["type", "type-array", "property-type/name", "property-type/type", "property-type-array",
            "property-not-object/name", "property-not-object/value", "enum", "minimum", "maximum",
            "required", "items"])
    def test_a_legacy_json_schema_body_with_a_bad_type_is_rejected(self, body, bad):
        response, tables = _run(rest_event("POST", "/compliance/schemas",
                                           body={"schemaName": SCHEMA, "schemaBody": body}))
        assert response["statusCode"] == 400
        assert "Invalid schema" in body_of(response)["message"]
        assert bad not in response["body"]
        tables["schema"].put_item.assert_not_called()

    def test_registering_under_a_missing_database_is_refused(self):
        response, tables = _run(
            rest_event("POST", "/compliance/schemas",
                       body={"schemaName": SCHEMA, "schemaBody": RULES_SCHEMA_BODY,
                             "databaseId": "db1"}),
            database_exists=False)
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Database not found"
        tables["schema"].put_item.assert_not_called()


@pytest.mark.unit
class TestRegisterAndUpdate:

    def test_registering_an_existing_name_writes_the_next_version(self):
        response, tables = _run(
            rest_event("POST", "/compliance/schemas",
                       body={"schemaName": SCHEMA, "schemaBody": RULES_SCHEMA_BODY}),
            schema_rows=[schema_row(version=2)])
        assert body_of(response)["internalVersion"] == 3
        assert put_items(tables["schema"])[0]["internalVersion"] == 3

    def test_only_the_system_user_may_register_a_system_schema(self):
        request = {"schemaName": SCHEMA, "schemaBody": RULES_SCHEMA_BODY, "isSystem": True}
        _, tables = _run(rest_event("POST", "/compliance/schemas", body=request))
        assert put_items(tables["schema"])[0]["isSystem"] is False
        _, tables = _run(rest_event("POST", "/compliance/schemas", body=request),
                         tokens=("SYSTEM_USER",))
        assert put_items(tables["schema"])[0]["isSystem"] is True

    def test_updating_a_system_schema_as_a_user_is_refused(self):
        response, tables = _run(
            rest_event("PUT", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA},
                       body={"description": "x"}),
            schema_rows=[schema_row(is_system=True)])
        assert response["statusCode"] == 400
        assert "System schemas" in body_of(response)["message"]
        tables["schema"].put_item.assert_not_called()

    def test_updating_a_missing_schema_is_not_found(self):
        response, _ = _run(
            rest_event("PUT", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA},
                       body={"description": "x"}))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Schema not found"

    def test_updating_keeps_the_stored_body_when_none_is_sent(self):
        _, tables = _run(
            rest_event("PUT", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA},
                       body={"description": "x"}),
            schema_rows=[schema_row(version=1)])
        assert json.loads(put_items(tables["schema"])[0]["schemaBody"]) == RULES_SCHEMA_BODY

    def test_updating_with_an_invalid_body_is_rejected(self):
        response, tables = _run(
            rest_event("PUT", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA},
                       body={"schemaBody": {"schemaFormat": "vams-rules-v1", "rules": {}}}),
            schema_rows=[schema_row()])
        assert response["statusCode"] == 400
        tables["schema"].put_item.assert_not_called()

    def test_getting_a_missing_schema_is_not_found(self):
        response, _ = _run(
            rest_event("GET", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA}))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Schema not found"


@pytest.mark.unit
class TestDeleteSchema:

    def test_deleting_a_system_schema_as_a_user_is_refused(self):
        response, tables = _run(
            rest_event("DELETE", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA}),
            schema_rows=[schema_row(is_system=True)])
        assert response["statusCode"] == 400
        assert "System schemas" in body_of(response)["message"]
        tables["schema"].batch_writer.assert_not_called()
        tables["audit"].put_item.assert_not_called()

    def test_a_bound_schema_is_refused_and_nothing_is_deleted(self):
        response, tables = _run(
            rest_event("DELETE", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA}),
            schema_rows=[schema_row()], bound=True)
        assert response["statusCode"] == 400
        assert "bound" in body_of(response)["message"]
        tables["schema"].batch_writer.assert_not_called()
        tables["audit"].put_item.assert_not_called()

    def test_a_database_binding_found_on_a_later_scan_page_refuses_the_delete(self):
        """The database scan is filtered, so an empty first page with a LastEvaluatedKey means
        "keep paging", not "unbound"."""
        schema_table, state_table, database_table, audit_table = _tables([schema_row()])
        database_table.scan.side_effect = [
            {"Items": [], "LastEvaluatedKey": {"databaseId": "k"}},
            {"Items": [{"databaseId": "db2"}]},
        ]
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer()), \
                patch(f"{MOD}.schema_table", schema_table), \
                patch(f"{MOD}.asset_state_table", state_table), \
                patch(f"{MOD}.database_table", database_table), \
                patch(f"{MOD}.audit_table", audit_table):
            response = svc.lambda_handler(
                rest_event("DELETE", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA}),
                MagicMock())
        assert response["statusCode"] == 400
        assert database_table.scan.call_args_list[1].kwargs["ExclusiveStartKey"] == {"databaseId": "k"}
        schema_table.batch_writer.assert_not_called()

    def test_an_unbound_schema_loses_every_version_and_is_audited(self):
        rows = [schema_row(version=1), schema_row(version=2), schema_row(version=3)]
        response, tables = _run(
            rest_event("DELETE", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA}),
            schema_rows=rows)
        assert response["statusCode"] == 200
        assert body_of(response)["versionsDeleted"] == 3
        batch = tables["schema"].batch_writer.return_value.__enter__.return_value
        deleted = [c.kwargs["Key"] for c in batch.delete_item.call_args_list]
        assert deleted == [{"schemaName": SCHEMA, "internalVersion": v} for v in (1, 2, 3)]
        audit = put_items(tables["audit"])[0]
        assert audit["eventType"] == "schema_deleted"
        assert audit["schemaName"] == SCHEMA
        assert audit["actor"] == USER
        assert json.loads(audit["details"]) == {"versionsDeleted": 3}

    def test_a_missing_schema_is_not_found(self):
        response, tables = _run(
            rest_event("DELETE", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA}))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Schema not found"
        tables["state"].query.assert_not_called()
