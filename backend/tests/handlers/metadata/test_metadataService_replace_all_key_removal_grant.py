# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A replace-all metadata update that removes stored keys also needs the route's DELETE grant.

`PUT` with `updateType: "replace_all"` removes every stored key the body leaves out. The route
check in the handler decides the PUT; removing keys is what the route's DELETE does, so a
replace-all that would remove a stored key is answered 403, before any write, unless the role also
holds Tier-1 DELETE on the same route. Which keys would go is decided from the keys the replace-all
reads from the table. A replace-all that removes nothing (it only adds or updates keys) is decided
as before.

Each role is imported from a shipped template in `documentation/permissionsTemplates/` the way the
import route does it, and every request runs through `lambda_handler` against the real policy
generation and Casbin evaluation for both tiers. Only the table reads and writes are stubbed.

The shipped `database-user` role holds PUT but not DELETE on the asset and file metadata routes, so
it is refused there. It holds DELETE on the asset-link and database metadata routes and still
removes keys there; on those two routes the refused role is `database-user` without that one
DELETE criterion.
"""

import contextlib
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module
from backend.backend.handlers.auth import authConstraintsTemplateService as template_import
from backend.backend.handlers.metadata import metadataService

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "documentation" / "permissionsTemplates"

ROLE_NAME = "template-role"
USER_ID = "template-user"
DATABASE_ID = "template-db"
ASSET_ID = "asset1"
LINK_ID = "11111111-2222-3333-4444-555555555555"
FILE_PATH = "/folder/file.txt"
VARIABLE_VALUES = {"ROLE_NAME": ROLE_NAME, "DATABASE_ID": DATABASE_ID, "TAG_VALUE": "locked"}
DELETE_CONSTRAINT_NAME = "{{ROLE_NAME}}-api-routes-delete"
CLAIMS = {"tokens": [USER_ID], "roles": [ROLE_NAME], "mfaEnabled": True}

KEPT_KEY = "keptKey"
STORED_KEY = "storedKey"
NEW_KEY = "newKey"


def _row(key, key_field="metadataKey", value_field="metadataValue", type_field="metadataValueType"):
    return {key_field: {"S": key}, value_field: {"S": "stored"}, type_field: {"S": "string"}}


def _attribute_row(key):
    return _row(key, "attributeKey", "attributeValue", "attributeValueType")


# name -> (request path, path parameters, extra body fields, stored row builder,
#          the database-user DELETE criterion that reaches the route, or None when it has none)
ROUTES = {
    "assetMetadata": (
        f"/database/{DATABASE_ID}/assets/{ASSET_ID}/metadata",
        {"databaseId": DATABASE_ID, "assetId": ASSET_ID}, {}, _row, None),
    "fileMetadata": (
        f"/database/{DATABASE_ID}/assets/{ASSET_ID}/metadata/file",
        {"databaseId": DATABASE_ID, "assetId": ASSET_ID},
        {"filePath": FILE_PATH, "type": "metadata"}, _row, None),
    "fileAttribute": (
        f"/database/{DATABASE_ID}/assets/{ASSET_ID}/metadata/file",
        {"databaseId": DATABASE_ID, "assetId": ASSET_ID},
        {"filePath": FILE_PATH, "type": "attribute"}, _attribute_row, None),
    "assetLinkMetadata": (
        f"/asset-links/{LINK_ID}/metadata", {"assetLinkId": LINK_ID}, {}, _row, "/asset-links"),
    "databaseMetadata": (
        f"/database/{DATABASE_ID}/metadata", {"databaseId": DATABASE_ID}, {}, _row,
        "/database/{{DATABASE_ID}}/metadata"),
}
ROUTES_WITHOUT_TEMPLATE_DELETE = [name for name, route in ROUTES.items() if route[4] is None]
ROUTES_WITH_TEMPLATE_DELETE = [name for name, route in ROUTES.items() if route[4] is not None]


def _template_role(template_name, without_delete_criterion=None):
    """The constraints an import of the template stores, in the shape the policy builder reads.

    ``without_delete_criterion`` first removes that one route criterion from the template's API
    DELETE constraint, which is the template as it reads without the route's DELETE grant.
    """
    document = json.loads((TEMPLATES_DIR / template_name).read_text(encoding="utf-8"))
    constraints = []
    for constraint in document["constraints"]:
        criteria_or = constraint.get("criteriaOr", [])
        if without_delete_criterion is not None and constraint["name"] == DELETE_CONSTRAINT_NAME:
            assert any(c["value"] == without_delete_criterion for c in criteria_or), (
                f"{template_name} has no DELETE criterion {without_delete_criterion}")
            criteria_or = [c for c in criteria_or if c["value"] != without_delete_criterion]
        constraints.append({
            "name": constraint["name"],
            "description": constraint["description"],
            "objectType": constraint["objectType"],
            "criteriaAnd": constraint.get("criteriaAnd", []),
            "criteriaOr": criteria_or,
            "groupPermissions": constraint.get("groupPermissions", []),
        })
    substituted = template_import.substitute_variables(constraints, VARIABLE_VALUES)
    return [template_import.build_constraint_data(constraint, ROLE_NAME, {"tokens": [USER_ID]})
            for constraint in substituted]


@pytest.fixture
def as_role():
    """Make the handler decide with the real CasbinEnforcer of a role built from ``constraints``.

    The per-user policy and enforcer caches are cleared on every build and restored afterwards.
    The user-roles and constraints reads are the only authorization stubs; the audit writers are
    replaced so a check writes nothing.
    """
    state = {"constraints": []}
    previous_policy_map = authz_module.casbin_user_policy_map
    previous_enforcer_map = authz_module.casbin_user_enforcer_map

    def build(constraints):
        state["constraints"] = constraints
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}

    with patch.object(
        authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
        return_value=[{"userId": USER_ID, "roleName": ROLE_NAME}],
    ), patch.object(
        authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
        side_effect=lambda role_names: state["constraints"],
    ), patch.object(authz_module, "log_authorization", MagicMock()), \
            patch.object(authz_module, "log_authorization_api", MagicMock()), \
            patch.object(authz_module, "request_to_claims", lambda event: dict(CLAIMS)), \
            patch.object(metadataService, "request_to_claims", lambda event: dict(CLAIMS)), \
            patch.object(metadataService, "CasbinEnforcer", authz_module.CasbinEnforcer):
        try:
            yield build
        finally:
            authz_module.casbin_user_policy_map = previous_policy_map
            authz_module.casbin_user_enforcer_map = previous_enforcer_map


class _Tables:
    """The tables one metadata write touches. The stored rows answer the metadata read; there are
    no metadata schemas; every batch write is accepted and recorded."""

    def __init__(self, stored_rows):
        self.client = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value.build_full_result.return_value = {"Items": list(stored_rows)}
        self.client.get_paginator.return_value = paginator
        self.client.query.return_value = {"Items": []}
        self.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        asset = {"databaseId": DATABASE_ID, "assetId": ASSET_ID, "assetName": "A",
                 "assetType": ".glb", "tags": []}
        self.asset_table = MagicMock()
        self.asset_table.get_item.return_value = {"Item": dict(asset)}
        self.database_table = MagicMock()
        self.database_table.get_item.return_value = {"Item": {"databaseId": DATABASE_ID}}
        self.links_table = MagicMock()
        self.links_table.get_item.return_value = {"Item": {
            "assetLinkId": LINK_ID,
            "fromAssetDatabaseId": DATABASE_ID, "fromAssetId": ASSET_ID,
            "toAssetDatabaseId": DATABASE_ID, "toAssetId": "asset2",
        }}
        self._stack = contextlib.ExitStack()

    def __enter__(self):
        for target, replacement in (
            ("dynamodb_client", self.client),
            ("asset_storage_table", self.asset_table),
            ("database_storage_table", self.database_table),
            ("asset_links_table", self.links_table),
            ("validate_file_exists", MagicMock(return_value=True)),
        ):
            self._stack.enter_context(patch.object(metadataService, target, replacement))
        return self

    def __exit__(self, *exc):
        self._stack.close()
        return False

    def _requests(self, kind):
        return [
            request[kind]
            for sent in self.client.batch_write_item.call_args_list
            for requests in sent.kwargs["RequestItems"].values()
            for request in requests
            if kind in request
        ]

    @property
    def deleted_keys(self):
        return {next(value["S"] for name, value in request["Key"].items() if name.endswith("Key"))
                for request in self._requests("DeleteRequest")}

    @property
    def written_keys(self):
        return {next(value["S"] for name, value in request["Item"].items() if name.endswith("Key"))
                for request in self._requests("PutRequest")}


def _replace_all(route_name, keys):
    """Run one replace-all PUT through the handler; returns the response and the tables."""
    path, path_parameters, body_fields, stored_row, _criterion = ROUTES[route_name]
    body = dict(body_fields, updateType="replace_all",
                metadata=[{"metadataKey": key, "metadataValue": "edited"} for key in keys])
    event = {"requestContext": {"http": {"method": "PUT", "path": path}},
             "pathParameters": dict(path_parameters), "queryStringParameters": None,
             "body": json.dumps(body)}
    tables = _Tables([stored_row(KEPT_KEY), stored_row(STORED_KEY)])
    with tables:
        response = metadataService.lambda_handler(event, MagicMock())
    return response, tables


REMOVING = [KEPT_KEY]                         # leaves STORED_KEY out, so it would be removed
NOT_REMOVING = [KEPT_KEY, STORED_KEY, NEW_KEY]  # updates both stored keys and adds one


@pytest.mark.unit
class TestARemovingReplaceAllNeedsTheRouteDelete:

    @pytest.mark.parametrize("route_name", ROUTES_WITHOUT_TEMPLATE_DELETE)
    def test_database_user_is_refused_and_nothing_is_written(self, as_role, route_name):
        as_role(_template_role("database-user.json"))
        response, tables = _replace_all(route_name, REMOVING)
        assert response["statusCode"] == 403, f"{route_name}: {response}"
        tables.client.batch_write_item.assert_not_called()

    @pytest.mark.parametrize("route_name", ROUTES_WITH_TEMPLATE_DELETE)
    def test_database_user_without_the_route_delete_is_refused_and_nothing_is_written(
            self, as_role, route_name):
        as_role(_template_role("database-user.json", without_delete_criterion=ROUTES[route_name][4]))
        response, tables = _replace_all(route_name, REMOVING)
        assert response["statusCode"] == 403, f"{route_name}: {response}"
        tables.client.batch_write_item.assert_not_called()


@pytest.mark.unit
class TestTheRestIsDecidedAsBefore:
    """Controls: these pass with and without the route DELETE check."""

    @pytest.mark.parametrize("route_name", list(ROUTES))
    def test_database_user_may_replace_all_without_removing_keys(self, as_role, route_name):
        as_role(_template_role("database-user.json"))
        response, tables = _replace_all(route_name, NOT_REMOVING)
        assert response["statusCode"] == 200, f"{route_name}: {response}"
        assert tables.deleted_keys == set()
        assert {KEPT_KEY, STORED_KEY, NEW_KEY} <= tables.written_keys

    @pytest.mark.parametrize("route_name", ROUTES_WITHOUT_TEMPLATE_DELETE)
    def test_database_admin_holds_the_route_delete_and_removes_keys(self, as_role, route_name):
        as_role(_template_role("database-admin.json"))
        response, tables = _replace_all(route_name, REMOVING)
        assert response["statusCode"] == 200, f"{route_name}: {response}"
        assert STORED_KEY in tables.deleted_keys
        assert KEPT_KEY in tables.written_keys

    @pytest.mark.parametrize("route_name", ROUTES_WITH_TEMPLATE_DELETE)
    def test_database_user_holds_the_route_delete_and_removes_keys(self, as_role, route_name):
        as_role(_template_role("database-user.json"))
        response, tables = _replace_all(route_name, REMOVING)
        assert response["statusCode"] == 200, f"{route_name}: {response}"
        assert STORED_KEY in tables.deleted_keys
        assert KEPT_KEY in tables.written_keys


@pytest.mark.unit
class TestTheCheckItself:

    def test_without_a_request_event_a_removal_is_refused(self):
        with pytest.raises(PermissionError):
            metadataService.require_route_delete_to_remove_keys(None, {STORED_KEY}, dict(CLAIMS))

    def test_without_tokens_a_removal_is_refused(self):
        event = {"requestContext": {"http": {"method": "PUT", "path": "/x"}}}
        with pytest.raises(PermissionError):
            metadataService.require_route_delete_to_remove_keys(event, {STORED_KEY}, {"tokens": []})

    def test_nothing_to_remove_is_not_checked(self):
        assert metadataService.require_route_delete_to_remove_keys(None, set(), {"tokens": []}) is None

    def test_a_workflow_write_back_keeps_removing_keys(self, as_role):
        """A pipeline's metadata write-back is a Lambda cross-call, which enforceAPI admits."""
        as_role(_template_role("database-user.json"))
        event = {"lambdaCrossCall": {"userName": "SYSTEM_USER"},
                 "requestContext": {"http": {"method": "PUT", "path": ROUTES["assetMetadata"][0]}}}
        assert metadataService.require_route_delete_to_remove_keys(
            event, {STORED_KEY}, dict(CLAIMS)) is None
