# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceSchemaBindingService: dispatch of the five method+path pairs, both authorization
tiers (every path is enforced against the schema AND the database or asset it targets), input
validation, the binding semantics (a database binding marks every asset without an override pending;
an asset override wins; removing an override falls back to the database binding), and the paged
asset-override listing."""

from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, DB, SCHEMA, USER, body_of, claims_for, decode_token, encode_token, enforcer,
    enforcer_for_object_types, rest_event, schema_row,
)
from handlers.compliance import complianceSchemaBindingService as svc

MOD = "handlers.compliance.complianceSchemaBindingService"
STORE = "handlers.compliance.complianceEvaluationStore"

# The stored asset row, as the asset table holds it: the attributes ABAC constraints match on.
ASSET_ROW = {"databaseId": DB, "assetId": ASSET, "assetName": "Turbine", "assetType": "glb",
             "tags": ["rotor"]}
DATABASE_ROW = {"databaseId": DB, "description": "Turbines", "complianceSchemaName": SCHEMA,
                "complianceAutoEval": True}


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
         casbin=None, **table_kwargs):
    schema_table, database_table, state_table, asset_table = _tables(**table_kwargs)
    casbin = casbin if casbin is not None else enforcer(api=api, obj=obj)
    with patch(f"{MOD}.request_to_claims", claims_for(*tokens)), \
            patch(f"{MOD}.CasbinEnforcer", return_value=casbin), \
            patch(f"{MOD}.schema_table", schema_table), \
            patch(f"{MOD}.database_table", database_table), \
            patch(f"{MOD}.asset_state_table", state_table), \
            patch(f"{MOD}.asset_table", asset_table), \
            patch(f"{STORE}.get_asset_item",
                  return_value=dict(ASSET_ROW) if asset_exists else None), \
            patch(f"{STORE}.get_compliance_record", return_value=compliance_record), \
            patch(f"{STORE}.update_asset_state") as update_state, \
            patch(f"{STORE}.write_audit") as write_audit:
        response = svc.lambda_handler(event, MagicMock())
    return response, {"schema": schema_table, "database": database_table, "state": state_table,
                      "asset": asset_table, "update_state": update_state, "audit": write_audit,
                      "casbin": casbin}


def _assert_nothing_written(tables):
    tables["database"].update_item.assert_not_called()
    tables["update_state"].assert_not_called()
    tables["state"].delete_item.assert_not_called()
    tables["state"].batch_writer.assert_not_called()
    tables["audit"].assert_not_called()


DB_PATH = f"/compliance/bind/{DB}"
ASSET_PATH = f"/compliance/bind/{DB}/{ASSET}"
DB_PARAMS = {"databaseId": DB}
ASSET_PARAMS = {"databaseId": DB, "assetId": ASSET}
BIND_BODY = {"schemaName": SCHEMA}

DB_PATHS = [
    ("GET", DB_PATH, DB_PARAMS, None),
    ("PUT", DB_PATH, DB_PARAMS, BIND_BODY),
    ("DELETE", DB_PATH, DB_PARAMS, None),
]
ASSET_PATHS = [
    ("PUT", ASSET_PATH, ASSET_PARAMS, BIND_BODY),
    ("DELETE", ASSET_PATH, ASSET_PARAMS, None),
]


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
        assert "NextToken" not in body

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

    @pytest.mark.parametrize("method,path,params,body", DB_PATHS + ASSET_PATHS)
    def test_tier_one_with_empty_tokens_denies_even_when_the_api_check_would_pass(
            self, method, path, params, body):
        response, tables = _run(rest_event(method, path, params, body=body), tokens=(), api=True,
                                database_item=dict(DATABASE_ROW),
                                compliance_record={"schemaName": SCHEMA, "schemaSource": "asset"})
        assert response["statusCode"] == 403
        tables["casbin"].enforce.assert_not_called()
        tables["database"].get_item.assert_not_called()
        _assert_nothing_written(tables)

    @pytest.mark.parametrize("method,path,params,body", DB_PATHS + ASSET_PATHS)
    def test_tier_two_denial_writes_nothing(self, method, path, params, body):
        response, tables = _run(
            rest_event(method, path, params, body=body), obj=False,
            database_item=dict(DATABASE_ROW),
            compliance_record={"schemaName": SCHEMA, "schemaSource": "asset"})
        assert response["statusCode"] == 403
        _assert_nothing_written(tables)

    @pytest.mark.parametrize("method,path,params,body", DB_PATHS)
    def test_schema_permission_alone_does_not_reach_the_database(self, method, path, params, body):
        """The schema object grants; the database object denies: refused before any read of the
        database's asset rows or any write."""
        response, tables = _run(
            rest_event(method, path, params, body=body),
            casbin=enforcer_for_object_types("complianceSchema"),
            database_item=dict(DATABASE_ROW), asset_rows=[{"assetId": "a"}, {"assetId": "b"}],
            state_rows=[{"assetId": "a", "schemaSource": "database"}])
        assert response["statusCode"] == 403
        tables["state"].query.assert_not_called()
        tables["asset"].query.assert_not_called()
        _assert_nothing_written(tables)

    @pytest.mark.parametrize("method,path,params,body", ASSET_PATHS)
    def test_schema_permission_alone_does_not_reach_the_asset(self, method, path, params, body):
        response, tables = _run(
            rest_event(method, path, params, body=body),
            casbin=enforcer_for_object_types("complianceSchema"),
            database_item=dict(DATABASE_ROW),
            compliance_record={"schemaName": SCHEMA, "schemaSource": "asset"})
        assert response["statusCode"] == 403
        _assert_nothing_written(tables)

    @pytest.mark.parametrize("method,path,params,body", DB_PATHS)
    def test_database_permission_alone_does_not_reach_the_schema(self, method, path, params, body):
        response, tables = _run(
            rest_event(method, path, params, body=body),
            casbin=enforcer_for_object_types("database"), database_item=dict(DATABASE_ROW))
        assert response["statusCode"] == 403
        _assert_nothing_written(tables)

    @pytest.mark.parametrize("method,path,params,body", ASSET_PATHS)
    def test_asset_permission_alone_does_not_reach_the_schema(self, method, path, params, body):
        response, tables = _run(
            rest_event(method, path, params, body=body),
            casbin=enforcer_for_object_types("asset"), database_item=dict(DATABASE_ROW),
            compliance_record={"schemaName": SCHEMA, "schemaSource": "asset"})
        assert response["statusCode"] == 403
        _assert_nothing_written(tables)

    @pytest.mark.parametrize("method,path,params,body", DB_PATHS)
    def test_both_grants_reach_the_operation(self, method, path, params, body):
        response, _ = _run(
            rest_event(method, path, params, body=body),
            casbin=enforcer_for_object_types("complianceSchema", "database"),
            database_item=dict(DATABASE_ROW))
        assert response["statusCode"] == 200, response

    def test_binding_a_database_checks_the_schema_and_the_stored_database_row(self):
        _, tables = _run(rest_event("PUT", DB_PATH, DB_PARAMS, body=BIND_BODY),
                         database_item=dict(DATABASE_ROW))
        calls = [c.args for c in tables["casbin"].enforce.call_args_list]
        assert calls == [
            ({"object__type": "complianceSchema", "complianceSchemaName": SCHEMA}, "PUT"),
            (dict(DATABASE_ROW, object__type="database"), "PUT"),
        ]

    def test_unbinding_a_database_checks_the_stored_row_and_the_currently_bound_schema(self):
        row = dict(DATABASE_ROW, complianceSchemaName="old-schema")
        _, tables = _run(rest_event("DELETE", DB_PATH, DB_PARAMS), database_item=row)
        calls = [c.args for c in tables["casbin"].enforce.call_args_list]
        assert calls == [
            (dict(row, object__type="database"), "DELETE"),
            ({"object__type": "complianceSchema", "complianceSchemaName": "old-schema"}, "DELETE"),
        ]

    def test_reading_the_bindings_checks_the_stored_database_row(self):
        _, tables = _run(rest_event("GET", DB_PATH, DB_PARAMS), database_item=dict(DATABASE_ROW))
        calls = [c.args for c in tables["casbin"].enforce.call_args_list]
        assert calls == [
            (dict(DATABASE_ROW, object__type="database"), "GET"),
            ({"object__type": "complianceSchema", "complianceSchemaName": SCHEMA}, "GET"),
        ]

    @pytest.mark.parametrize("method,path,params,body", ASSET_PATHS)
    def test_asset_paths_check_the_stored_asset_row(self, method, path, params, body):
        """The asset object carries the row as stored (tags, assetType, assetName), so ABAC
        constraints on those attributes apply, plus the `asset` annotation."""
        _, tables = _run(rest_event(method, path, params, body=body),
                         compliance_record={"schemaName": SCHEMA, "schemaSource": "asset"},
                         database_item={"databaseId": DB})
        asset_calls = [c.args for c in tables["casbin"].enforce.call_args_list
                       if c.args[0].get("object__type") == "asset"]
        assert asset_calls == [(dict(ASSET_ROW, object__type="asset"), method)]

    def test_a_missing_database_is_refused_before_its_absence_is_reported(self):
        response, _ = _run(rest_event("PUT", DB_PATH, DB_PARAMS, body=BIND_BODY),
                           casbin=enforcer_for_object_types("complianceSchema"))
        assert response["statusCode"] == 403

    def test_a_missing_database_is_checked_by_its_requested_id(self):
        _, tables = _run(rest_event("GET", DB_PATH, DB_PARAMS))
        assert tables["casbin"].enforce.call_args_list[0].args == (
            {"databaseId": DB, "object__type": "database"}, "GET")

    def test_a_missing_asset_is_refused_before_its_absence_is_reported(self):
        response, _ = _run(rest_event("PUT", ASSET_PATH, ASSET_PARAMS, body=BIND_BODY),
                           casbin=enforcer_for_object_types("complianceSchema"), asset_exists=False)
        assert response["statusCode"] == 403

    def test_a_missing_asset_is_checked_by_its_requested_ids(self):
        _, tables = _run(rest_event("PUT", ASSET_PATH, ASSET_PARAMS, body=BIND_BODY),
                         asset_exists=False)
        assert tables["casbin"].enforce.call_args_list[1].args == (
            {"databaseId": DB, "assetId": ASSET, "object__type": "asset"}, "PUT")


@pytest.mark.unit
class TestValidation:

    @pytest.mark.parametrize("method,body", [("GET", None), ("PUT", BIND_BODY), ("DELETE", None)])
    def test_a_bad_database_id_is_rejected(self, method, body):
        bad = "bad<database-id>"
        response, tables = _run(
            rest_event(method, f"/compliance/bind/{bad}", {"databaseId": bad}, body=body))
        assert response["statusCode"] == 400
        assert bad not in response["body"]
        tables["database"].get_item.assert_not_called()

    @pytest.mark.parametrize("method,body", [("PUT", BIND_BODY), ("DELETE", None)])
    def test_a_bad_asset_id_is_rejected(self, method, body):
        bad = "bad<asset-id>"
        response, tables = _run(
            rest_event(method, f"/compliance/bind/{DB}/{bad}", {"databaseId": DB, "assetId": bad},
                       body=body))
        assert response["statusCode"] == 400
        assert bad not in response["body"]
        tables["update_state"].assert_not_called()

    @pytest.mark.parametrize("body,bad", [
        ({}, None), ({"schemaName": "zq"}, "zq"), ({"schemaName": "bad name!"}, "bad name!"),
    ], ids=["missing", "short", "invalid-chars"])
    def test_a_bad_bind_body_is_rejected(self, body, bad):
        response, tables = _run(rest_event("PUT", DB_PATH, DB_PARAMS, body=body),
                                database_item={"databaseId": DB})
        assert response["statusCode"] == 400
        if bad is not None:
            assert bad not in response["body"]
        tables["database"].update_item.assert_not_called()

    def test_a_body_that_is_not_json_is_rejected(self):
        response, _ = _run(rest_event("PUT", DB_PATH, DB_PARAMS, body="{oops"))
        assert response["statusCode"] == 400
        assert "Invalid JSON" in body_of(response)["message"]

    def test_a_bad_page_size_is_rejected(self):
        response, tables = _run(rest_event("GET", DB_PATH, DB_PARAMS, query_params={"maxItems": "ten"}),
                                database_item=dict(DATABASE_ROW))
        assert response["statusCode"] == 400
        assert "ten" not in response["body"]
        tables["state"].query.assert_not_called()

    @pytest.mark.parametrize("token", [
        "!!not-base64!!", encode_token([1]), encode_token("offset"), encode_token({}),
        encode_token({"offset": -1}), encode_token({"offset": "3"}), encode_token({"offset": True}),
        encode_token({"offset": 1.5}),
    ], ids=["not-base64", "list", "string", "no-offset", "negative", "string-offset", "bool",
            "float"])
    def test_a_malformed_pagination_token_is_rejected(self, token):
        response, tables = _run(
            rest_event("GET", DB_PATH, DB_PARAMS, query_params={"startingToken": token}),
            database_item=dict(DATABASE_ROW))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Invalid pagination token"
        tables["state"].query.assert_not_called()


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

    @pytest.mark.parametrize("body", [
        {"type": "object", "properties": {"secret<p>": {}}},
        {"rules": {"r": {"ruleType": "metadata"}}},
        {"schemaFormat": "other", "rules": {}},
    ], ids=["json-schema", "rules-without-format", "other-format"])
    def test_binding_a_legacy_schema_to_a_database_is_refused(self, body):
        """A stored body that is not a vams-rules-v1 document cannot be evaluated, so it cannot be
        bound; the message is the one the schema service gives such a body, and echoes nothing."""
        response, tables = _run(rest_event("PUT", DB_PATH, DB_PARAMS, body=BIND_BODY),
                                schema_rows=[schema_row(body=body)],
                                database_item={"databaseId": DB})
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Schema body must be a vams-rules-v1 document"
        assert "secret<p>" not in response["body"]
        _assert_nothing_written(tables)

    def test_binding_a_schema_whose_stored_body_is_not_json_is_refused(self):
        row = dict(schema_row(), schemaBody="not json")
        response, tables = _run(rest_event("PUT", DB_PATH, DB_PARAMS, body=BIND_BODY),
                                schema_rows=[row], database_item={"databaseId": DB})
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == svc.LEGACY_SCHEMA_MESSAGE
        _assert_nothing_written(tables)

    def test_the_legacy_message_names_no_schema_and_no_request_field(self):
        """Rule 11: a fixed sentence about the stored body; the bind request has no `schemaBody`."""
        assert svc.LEGACY_SCHEMA_MESSAGE == "Schema body must be a vams-rules-v1 document"
        assert SCHEMA not in svc.LEGACY_SCHEMA_MESSAGE

    def test_the_format_check_reads_the_latest_version_only(self):
        """The one query the visibility check already makes answers the format too."""
        response, tables = _run(rest_event("PUT", DB_PATH, DB_PARAMS, body=BIND_BODY),
                                database_item={"databaseId": DB})
        assert response["statusCode"] == 200
        assert tables["schema"].query.call_count == 1
        query = tables["schema"].query.call_args.kwargs
        assert query["ScanIndexForward"] is False and query["Limit"] == 1

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

    def test_binding_a_legacy_schema_to_an_asset_is_refused(self):
        response, tables = _run(
            rest_event("PUT", ASSET_PATH, ASSET_PARAMS, body=BIND_BODY),
            schema_rows=[schema_row(body={"type": "object", "properties": {"secret<p>": {}}})],
            compliance_record={"schemaName": "previous", "schemaSource": "database"})
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Schema body must be a vams-rules-v1 document"
        assert "secret<p>" not in response["body"]
        _assert_nothing_written(tables)

    def test_a_missing_legacy_schema_is_reported_as_missing_before_its_format(self):
        response, _ = _run(rest_event("PUT", ASSET_PATH, ASSET_PARAMS, body=BIND_BODY),
                           schema_rows=[])
        assert body_of(response)["message"] == "Schema not found"

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


@pytest.mark.unit
class TestOverrideListingPaging:
    """The asset-override listing pages with maxItems / startingToken / NextToken; the count
    covers the full set on every page."""

    OVERRIDES = [{"assetId": f"asset-{i}", "schemaSource": "asset", "schemaName": f"s{i}",
                  "complianceState": "compliant"} for i in (4, 1, 3, 0, 2)]
    ROWS = OVERRIDES + [{"assetId": "inherited", "schemaSource": "database"}]

    def _page(self, query_params):
        response, _ = _run(rest_event("GET", DB_PATH, DB_PARAMS, query_params=query_params),
                           database_item=dict(DATABASE_ROW), state_rows=list(self.ROWS))
        assert response["statusCode"] == 200, response
        return body_of(response)

    def test_the_token_round_trips_and_the_pages_partition_the_full_set(self):
        page_one = self._page({"maxItems": "2"})
        assert [o["assetId"] for o in page_one["assetOverrides"]] == ["asset-0", "asset-1"]
        assert page_one["assetOverrideCount"] == 5
        assert decode_token(page_one["NextToken"]) == {"offset": 2}

        page_two = self._page({"maxItems": "2", "startingToken": page_one["NextToken"]})
        assert [o["assetId"] for o in page_two["assetOverrides"]] == ["asset-2", "asset-3"]
        assert page_two["assetOverrideCount"] == 5

        page_three = self._page({"maxItems": "2", "startingToken": page_two["NextToken"]})
        assert [o["assetId"] for o in page_three["assetOverrides"]] == ["asset-4"]
        assert "NextToken" not in page_three

        union = page_one["assetOverrides"] + page_two["assetOverrides"] + page_three["assetOverrides"]
        assert sorted(o["assetId"] for o in union) == sorted(o["assetId"] for o in self.OVERRIDES)

    def test_a_page_that_ends_exactly_on_the_set_has_no_token(self):
        page = self._page({"maxItems": "5"})
        assert len(page["assetOverrides"]) == 5
        assert "NextToken" not in page

    def test_an_offset_past_the_end_yields_an_empty_page(self):
        page = self._page({"startingToken": encode_token({"offset": 50})})
        assert page["assetOverrides"] == []
        assert page["assetOverrideCount"] == 5
        assert "NextToken" not in page

    def test_the_page_size_is_clamped_to_the_named_bounds(self):
        page = self._page({"maxItems": "0"})
        assert len(page["assetOverrides"]) == 1
        assert decode_token(page["NextToken"]) == {"offset": 1}
        assert svc.DEFAULT_LIST_PAGE_SIZE == 100
        assert svc.MAX_LIST_PAGE_SIZE == 500
        page = self._page({"maxItems": "100000"})
        assert len(page["assetOverrides"]) == 5
