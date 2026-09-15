# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceSchemaBindingService: dispatch of the five method+path pairs, both authorization
tiers, input validation, and the binding semantics (a database binding marks every asset without an
override pending; an asset override wins; removing an override falls back to the database
binding)."""

from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, DB, SCHEMA, USER, body_of, claims_for, enforcer, rest_event, schema_row,
)
from handlers.compliance import complianceSchemaBindingService as svc

MOD = "handlers.compliance.complianceSchemaBindingService"
STORE = "handlers.compliance.complianceEvaluationStore"


def _tables(schema_rows=None, database_item=None, state_rows=None, asset_rows=None):
    schema_table = MagicMock(name="schema_table")
    schema_table.query.return_value = {"Items": list(schema_rows if schema_rows is not None
                                                     else [schema_row()])}
    database_table = MagicMock(name="database_table")
    database_table.get_item.return_value = (
        {"Item": database_item} if database_item is not None else {})
    state_table = MagicMock(name="asset_state_table")
    state_table.query.return_value = {"Items": list(state_rows or [])}
    asset_table = MagicMock(name="asset_table")
    asset_table.query.return_value = {"Items": list(asset_rows or [])}
    return schema_table, database_table, state_table, asset_table


def _run(event, tokens=(USER,), api=True, obj=True, asset_exists=True, compliance_record=None,
         **table_kwargs):
    schema_table, database_table, state_table, asset_table = _tables(**table_kwargs)
    with patch(f"{MOD}.request_to_claims", claims_for(*tokens)), \
            patch(f"{MOD}.CasbinEnforcer", return_value=enforcer(api=api, obj=obj)), \
            patch(f"{MOD}.schema_table", schema_table), \
            patch(f"{MOD}.database_table", database_table), \
            patch(f"{MOD}.asset_state_table", state_table), \
            patch(f"{MOD}.asset_table", asset_table), \
            patch(f"{STORE}.get_asset_item",
                  return_value={"assetId": ASSET} if asset_exists else None), \
            patch(f"{STORE}.get_compliance_record", return_value=compliance_record), \
            patch(f"{STORE}.update_asset_state") as update_state, \
            patch(f"{STORE}.write_audit") as write_audit:
        response = svc.lambda_handler(event, MagicMock())
    return response, {"schema": schema_table, "database": database_table, "state": state_table,
                      "asset": asset_table, "update_state": update_state, "audit": write_audit}


DB_PATH = f"/compliance/bind/{DB}"
ASSET_PATH = f"/compliance/bind/{DB}/{ASSET}"
DB_PARAMS = {"databaseId": DB}
ASSET_PARAMS = {"databaseId": DB, "assetId": ASSET}
BIND_BODY = {"schemaName": SCHEMA}


@pytest.mark.unit
class TestRouteDispatch:

    def test_get_database_bindings(self):
        response, _ = _run(
            rest_event("GET", DB_PATH, DB_PARAMS),
            database_item={"databaseId": DB, "complianceSchemaName": SCHEMA,
                           "complianceAutoEval": True},
            state_rows=[{"assetId": "a", "schemaSource": "asset", "schemaName": "s2",
                         "complianceState": "compliant"},
                        {"assetId": "b", "schemaSource": "database"}])
        assert response["statusCode"] == 200
        body = body_of(response)
        assert body["databaseSchema"] == SCHEMA
        assert body["complianceAutoEval"] is True
        assert body["assetOverrides"] == [
            {"assetId": "a", "schemaName": "s2", "complianceState": "compliant"}]
        assert body["assetOverrideCount"] == 1

    def test_put_database_binds(self):
        response, _ = _run(rest_event("PUT", DB_PATH, DB_PARAMS, body=BIND_BODY),
                           database_item={"databaseId": DB})
        assert response["statusCode"] == 200, response
        assert body_of(response)["schemaName"] == SCHEMA

    def test_delete_database_unbinds(self):
        response, _ = _run(rest_event("DELETE", DB_PATH, DB_PARAMS),
                           database_item={"databaseId": DB, "complianceSchemaName": SCHEMA})
        assert response["statusCode"] == 200
        assert body_of(response)["previousSchema"] == SCHEMA

    def test_put_asset_binds_an_override(self):
        response, _ = _run(rest_event("PUT", ASSET_PATH, ASSET_PARAMS, body=BIND_BODY))
        assert response["statusCode"] == 200, response
        assert body_of(response)["schemaSource"] == "asset"

    def test_delete_asset_removes_the_override(self):
        response, _ = _run(rest_event("DELETE", ASSET_PATH, ASSET_PARAMS),
                           compliance_record={"schemaName": SCHEMA, "schemaSource": "asset"},
                           database_item={"databaseId": DB})
        assert response["statusCode"] == 200
        assert body_of(response)["message"] == "Asset schema override removed"

    @pytest.mark.parametrize("method,path", [
        ("POST", DB_PATH), ("GET", ASSET_PATH), ("PATCH", DB_PATH),
        ("GET", "/compliance/bind"), ("PUT", f"{ASSET_PATH}/extra"),
    ])
    def test_an_unknown_method_or_path_is_refused(self, method, path):
        response, _ = _run(rest_event(method, path, body={}))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Method not allowed"


@pytest.mark.unit
class TestAuthorization:

    @pytest.mark.parametrize("tokens", [(), (USER,)], ids=["empty-tokens", "api-denied"])
    def test_tier_one_denies(self, tokens):
        response, tables = _run(rest_event("GET", DB_PATH, DB_PARAMS), tokens=tokens, api=False,
                                database_item={"databaseId": DB})
        assert response["statusCode"] == 403
        tables["database"].get_item.assert_not_called()

    @pytest.mark.parametrize("method,path,params,body", [
        ("GET", DB_PATH, DB_PARAMS, None),
        ("PUT", DB_PATH, DB_PARAMS, BIND_BODY),
        ("DELETE", DB_PATH, DB_PARAMS, None),
        ("PUT", ASSET_PATH, ASSET_PARAMS, BIND_BODY),
        ("DELETE", ASSET_PATH, ASSET_PARAMS, None),
    ])
    def test_tier_two_denial_writes_nothing(self, method, path, params, body):
        response, tables = _run(
            rest_event(method, path, params, body=body), obj=False,
            database_item={"databaseId": DB, "complianceSchemaName": SCHEMA},
            compliance_record={"schemaName": SCHEMA, "schemaSource": "asset"})
        assert response["statusCode"] == 403
        tables["database"].update_item.assert_not_called()
        tables["update_state"].assert_not_called()
        tables["state"].delete_item.assert_not_called()
        tables["audit"].assert_not_called()

    def test_tier_two_object_names_the_schema_being_bound(self):
        instance = enforcer()
        schema_table, database_table, state_table, asset_table = _tables(
            database_item={"databaseId": DB})
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=instance), \
                patch(f"{MOD}.schema_table", schema_table), \
                patch(f"{MOD}.database_table", database_table), \
                patch(f"{MOD}.asset_state_table", state_table), \
                patch(f"{MOD}.asset_table", asset_table), \
                patch(f"{STORE}.write_audit"):
            svc.lambda_handler(rest_event("PUT", DB_PATH, DB_PARAMS, body=BIND_BODY), MagicMock())
        instance.enforce.assert_called_once_with(
            {"object__type": "complianceSchema", "complianceSchemaName": SCHEMA}, "PUT")

    def test_unbinding_a_database_checks_the_currently_bound_schema(self):
        instance = enforcer()
        schema_table, database_table, state_table, asset_table = _tables(
            database_item={"databaseId": DB, "complianceSchemaName": "old-schema"})
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=instance), \
                patch(f"{MOD}.schema_table", schema_table), \
                patch(f"{MOD}.database_table", database_table), \
                patch(f"{MOD}.asset_state_table", state_table), \
                patch(f"{MOD}.asset_table", asset_table), \
                patch(f"{STORE}.write_audit"):
            svc.lambda_handler(rest_event("DELETE", DB_PATH, DB_PARAMS), MagicMock())
        instance.enforce.assert_called_once_with(
            {"object__type": "complianceSchema", "complianceSchemaName": "old-schema"}, "DELETE")


@pytest.mark.unit
class TestValidation:

    @pytest.mark.parametrize("method,body", [("GET", None), ("PUT", BIND_BODY), ("DELETE", None)])
    def test_a_bad_database_id_is_rejected(self, method, body):
        response, tables = _run(
            rest_event(method, "/compliance/bind/x", {"databaseId": "x"}, body=body))
        assert response["statusCode"] == 400
        tables["database"].get_item.assert_not_called()

    @pytest.mark.parametrize("method,body", [("PUT", BIND_BODY), ("DELETE", None)])
    def test_a_bad_asset_id_is_rejected(self, method, body):
        bad = "bad<asset>"
        response, tables = _run(
            rest_event(method, f"/compliance/bind/{DB}/{bad}", {"databaseId": DB, "assetId": bad},
                       body=body))
        assert response["statusCode"] == 400
        tables["update_state"].assert_not_called()

    @pytest.mark.parametrize("body", [{}, {"schemaName": "x"}, {"schemaName": "bad name!"}],
                             ids=["missing", "short", "invalid-chars"])
    def test_a_bad_bind_body_is_rejected(self, body):
        response, tables = _run(rest_event("PUT", DB_PATH, DB_PARAMS, body=body),
                                database_item={"databaseId": DB})
        assert response["statusCode"] == 400
        tables["database"].update_item.assert_not_called()

    def test_a_body_that_is_not_json_is_rejected(self):
        response, _ = _run(rest_event("PUT", DB_PATH, DB_PARAMS, body="{oops"))
        assert response["statusCode"] == 400
        assert "Invalid JSON" in body_of(response)["message"]


@pytest.mark.unit
class TestDatabaseBinding:

    def test_binding_marks_assets_without_an_override_pending(self):
        response, tables = _run(
            rest_event("PUT", DB_PATH, DB_PARAMS,
                       body={"schemaName": SCHEMA, "complianceAutoEval": False}),
            database_item={"databaseId": DB, "complianceSchemaName": "previous"},
            state_rows=[{"assetId": "a", "schemaSource": "asset"}],
            asset_rows=[{"assetId": "a"}, {"assetId": "b"}, {"assetId": "c"}])
        assert response["statusCode"] == 200
        assert body_of(response)["assetsPendingEvaluation"] == 2
        update = tables["database"].update_item.call_args.kwargs
        assert update["ExpressionAttributeValues"][":schema"] == SCHEMA
        assert update["ExpressionAttributeValues"][":autoEval"] is False
        batch = tables["state"].batch_writer.return_value.__enter__.return_value
        marked = [c.kwargs["Item"] for c in batch.put_item.call_args_list]
        assert [m["assetId"] for m in marked] == ["b", "c"]
        assert {(m["complianceState"], m["schemaSource"], m["schemaName"]) for m in marked} == {
            ("pending_evaluation", "database", SCHEMA)}
        audit = tables["audit"].call_args
        assert audit.kwargs["event_type"] == "schema_bound_to_database"
        assert audit.kwargs["details"] == {"previousSchema": "previous", "affectedAssets": 2}

    def test_binding_pages_the_asset_listing_to_exhaustion(self):
        schema_table, database_table, state_table, asset_table = _tables(
            database_item={"databaseId": DB})
        asset_table.query.side_effect = [
            {"Items": [{"assetId": "a"}], "LastEvaluatedKey": {"assetId": "a"}},
            {"Items": [{"assetId": "b"}]},
        ]
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer()), \
                patch(f"{MOD}.schema_table", schema_table), \
                patch(f"{MOD}.database_table", database_table), \
                patch(f"{MOD}.asset_state_table", state_table), \
                patch(f"{MOD}.asset_table", asset_table), \
                patch(f"{STORE}.write_audit"):
            response = svc.lambda_handler(
                rest_event("PUT", DB_PATH, DB_PARAMS, body=BIND_BODY), MagicMock())
        assert body_of(response)["assetsPendingEvaluation"] == 2
        assert asset_table.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {"assetId": "a"}

    def test_binding_a_missing_schema_is_refused(self):
        response, tables = _run(rest_event("PUT", DB_PATH, DB_PARAMS, body=BIND_BODY),
                                schema_rows=[], database_item={"databaseId": DB})
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Schema not found"
        tables["database"].update_item.assert_not_called()

    def test_binding_another_databases_schema_is_refused(self):
        response, tables = _run(
            rest_event("PUT", DB_PATH, DB_PARAMS, body=BIND_BODY),
            schema_rows=[schema_row(database_id="other-db")], database_item={"databaseId": DB})
        assert response["statusCode"] == 400
        assert "not available" in body_of(response)["message"]
        tables["database"].update_item.assert_not_called()

    def test_binding_to_a_missing_database_is_refused(self):
        response, tables = _run(rest_event("PUT", DB_PATH, DB_PARAMS, body=BIND_BODY))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Database not found"
        tables["database"].update_item.assert_not_called()

    def test_unbinding_removes_inherited_rows_and_keeps_overrides(self):
        response, tables = _run(
            rest_event("DELETE", DB_PATH, DB_PARAMS),
            database_item={"databaseId": DB, "complianceSchemaName": SCHEMA},
            state_rows=[{"assetId": "a", "schemaSource": "asset"},
                        {"assetId": "b", "schemaSource": "database"},
                        {"assetId": "c"}])
        assert response["statusCode"] == 200
        assert body_of(response)["removedComplianceRecords"] == 2
        assert "REMOVE complianceSchemaName" in tables["database"].update_item.call_args.kwargs[
            "UpdateExpression"]
        batch = tables["state"].batch_writer.return_value.__enter__.return_value
        deleted = [c.kwargs["Key"]["assetId"] for c in batch.delete_item.call_args_list]
        assert deleted == ["b", "c"]
        assert tables["audit"].call_args.kwargs["event_type"] == "schema_unbound_from_database"

    def test_unbinding_a_database_with_no_binding_changes_nothing(self):
        response, tables = _run(rest_event("DELETE", DB_PATH, DB_PARAMS),
                                database_item={"databaseId": DB})
        assert response["statusCode"] == 200
        assert "no schema binding" in body_of(response)["message"]
        tables["database"].update_item.assert_not_called()
        tables["audit"].assert_not_called()

    @pytest.mark.parametrize("method", ["GET", "DELETE"])
    def test_a_missing_database_is_not_found(self, method):
        response, _ = _run(rest_event(method, DB_PATH, DB_PARAMS))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Database not found"


@pytest.mark.unit
class TestAssetBinding:

    def test_binding_an_asset_writes_an_override_row(self):
        response, tables = _run(
            rest_event("PUT", ASSET_PATH, ASSET_PARAMS, body=BIND_BODY),
            compliance_record={"schemaName": "previous", "schemaSource": "database"})
        assert response["statusCode"] == 200
        updates = tables["update_state"].call_args.args[2]
        assert updates["schemaName"] == SCHEMA
        assert updates["schemaSource"] == "asset"
        assert updates["complianceState"] == "pending_evaluation"
        audit = tables["audit"].call_args.kwargs
        assert audit["event_type"] == "schema_bound_to_asset"
        assert audit["details"] == {"previousSchema": "previous", "previousSource": "database"}

    def test_binding_a_missing_asset_is_refused(self):
        response, tables = _run(rest_event("PUT", ASSET_PATH, ASSET_PARAMS, body=BIND_BODY),
                                asset_exists=False)
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Asset not found"
        tables["update_state"].assert_not_called()

    def test_removing_an_override_falls_back_to_the_database_schema(self):
        response, tables = _run(
            rest_event("DELETE", ASSET_PATH, ASSET_PARAMS),
            compliance_record={"schemaName": SCHEMA, "schemaSource": "asset"},
            database_item={"databaseId": DB, "complianceSchemaName": "db-schema"})
        assert response["statusCode"] == 200
        assert body_of(response)["fallbackSchema"] == "db-schema"
        assert body_of(response)["schemaSource"] == "database"
        updates = tables["update_state"].call_args.args[2]
        assert updates["schemaName"] == "db-schema"
        assert updates["schemaSource"] == "database"
        tables["state"].delete_item.assert_not_called()

    def test_removing_an_override_with_no_database_binding_deletes_the_row(self):
        response, tables = _run(
            rest_event("DELETE", ASSET_PATH, ASSET_PARAMS),
            compliance_record={"schemaName": SCHEMA, "schemaSource": "asset"},
            database_item={"databaseId": DB})
        assert response["statusCode"] == 200
        assert body_of(response)["fallbackSchema"] is None
        tables["state"].delete_item.assert_called_once_with(
            Key={"databaseId": DB, "assetId": ASSET})
        tables["update_state"].assert_not_called()

    @pytest.mark.parametrize("record", [None, {"schemaName": SCHEMA, "schemaSource": "database"}],
                             ids=["no-row", "inherited-row"])
    def test_removing_a_non_override_changes_nothing(self, record):
        response, tables = _run(rest_event("DELETE", ASSET_PATH, ASSET_PARAMS),
                                compliance_record=record)
        assert response["statusCode"] == 200
        assert "no explicit schema override" in body_of(response)["message"]
        tables["state"].delete_item.assert_not_called()
        tables["update_state"].assert_not_called()
        tables["audit"].assert_not_called()
