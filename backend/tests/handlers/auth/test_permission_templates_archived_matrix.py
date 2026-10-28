# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""What each database-scoped template grants on archived records and on the database itself.

An archived asset or a deleted database's record is stored under ``<databaseId>#deleted``, and the
enforcer allows such a record only when it passes on that stored id AND on its database id (see
``tests/handlers/authz/test_archived_database_id.py``). A template therefore reaches archived records
only when its constraint names both forms. The shipped templates implement this matrix:

==========================  ==================  ==============================  ==============================
                            database-readonly   database-user                   database-admin
==========================  ==================  ==============================  ==============================
Live assets                 view                view, create, edit, archive     view, create, edit, archive
Archived assets             none                none                            view, unarchive, permanent delete
The database itself         view                view + its database metadata    view, create, edit, archive
Archived database record    none                none                            view
==========================  ==================  ==============================  ==============================

database-user holds POST, PUT and DELETE on its ``database`` object because the database metadata
routes (``/database/{databaseId}/metadata``) authorize those actions on the database. Its Tier-1 route
constraints are what keep those actions off ``POST /database`` (create), ``PUT /database/{id}`` (edit
details) and ``DELETE /database/{id}`` (archive the database), and a route deny scoped to its own
database withholds ``unarchiveAsset``. Because a deny wins across every role a user holds, that deny
names the database, so it does not block an admin role the same user holds on another database.

Each template is run through the import's own substitution and conversion (``substitute_variables``,
``build_constraint_data``) and then through the real policy generation and Casbin evaluation, with only
the two table reads stubbed.
"""

import json
import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module  # noqa: E402
from backend.backend.common.apiRoutes import (  # noqa: E402
    ALL_API_ROUTES,
    API_ARCHIVE_FILE,
    API_DATABASE_METADATA,
    API_UNARCHIVE_ASSET,
    DELETE,
    POST,
    PUT,
)
from backend.backend.handlers.auth import authConstraintsTemplateService as svc  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "documentation" / "permissionsTemplates"

USER_ID = "matrix-user"
ROLE = "r"
DB = "dbX"
OTHER_DB = "dbY"
ARCHIVED = "#deleted"
ALL_METHODS = ("GET", "PUT", "POST", "DELETE")


def _stored_constraints(template_name, role_name=ROLE, database_id=DB):
    """The constraints an import of the template stores, in the shape the policy builder reads."""
    document = json.loads((TEMPLATES_DIR / template_name).read_text(encoding="utf-8"))
    constraints = [
        {
            "name": constraint["name"],
            "description": constraint["description"],
            "objectType": constraint["objectType"],
            "criteriaAnd": constraint.get("criteriaAnd", []),
            "criteriaOr": constraint.get("criteriaOr", []),
            "groupPermissions": constraint.get("groupPermissions", []),
        }
        for constraint in document["constraints"]
    ]
    substituted = svc.substitute_variables(
        constraints, {"ROLE_NAME": role_name, "DATABASE_ID": database_id})
    return [svc.build_constraint_data(constraint, role_name, {"tokens": [USER_ID]})
            for constraint in substituted]


def _asset(database_id):
    return {"object__type": "asset", "databaseId": database_id, "assetId": "a1",
            "assetName": "Part", "assetType": ".glb", "tags": []}


def _database(database_id):
    return {"object__type": "database", "databaseId": database_id}


def _api(path):
    """The Tier-1 object ``enforceAPI`` evaluates for a request path."""
    return {"object__type": "api", "route__path": path}


def _concrete_path(route_path, database_id=DB):
    """The route template with its database filled and every other ``{parameter}`` set to a probe id."""
    path = route_path.replace("{databaseId}", database_id)
    return re.sub(r"\{[^/{}]+\}", "probe-id", path)


def _allowed(enforcer, obj, methods=ALL_METHODS):
    return {method for method in methods if enforcer.enforce(obj, method)}


@pytest.fixture
def template_enforcer():
    """Build the real CasbinEnforcer for a user holding one role per (template, role, database) triple.

    The per-user policy and enforcer caches are cleared on every build, so each policy is generated
    from the constraints passed in. The two table reads are the only stubs, and the denial audit
    writer is replaced so a denied check writes nothing. Both caches are restored afterwards.
    """
    state = {"roles": [], "constraints": []}
    previous_policy_map = authz_module.casbin_user_policy_map
    previous_enforcer_map = authz_module.casbin_user_enforcer_map

    def build(*grants):
        state["roles"] = [role_name for _, role_name, _ in grants]
        state["constraints"] = [
            constraint
            for template_name, role_name, database_id in grants
            for constraint in _stored_constraints(template_name, role_name, database_id)
        ]
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}
        return authz_module.CasbinEnforcer(
            {"tokens": [USER_ID], "roles": state["roles"], "mfaEnabled": True})

    with patch.object(
        authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
        side_effect=lambda: [{"userId": USER_ID, "roleName": role} for role in state["roles"]],
    ), patch.object(
        authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
        side_effect=lambda role_names: state["constraints"],
    ), patch.object(authz_module, "log_authorization", MagicMock()):
        try:
            yield build
        finally:
            authz_module.casbin_user_policy_map = previous_policy_map
            authz_module.casbin_user_enforcer_map = previous_enforcer_map


@pytest.fixture
def admin(template_enforcer):
    return template_enforcer(("database-admin.json", ROLE, DB))


@pytest.fixture
def user(template_enforcer):
    return template_enforcer(("database-user.json", ROLE, DB))


@pytest.fixture
def readonly(template_enforcer):
    return template_enforcer(("database-readonly.json", ROLE, DB))


@pytest.mark.unit
class TestDatabaseAdminTemplate:

    def test_archived_assets_can_be_viewed_unarchived_and_permanently_deleted(self, admin):
        assert _allowed(admin, _asset(DB + ARCHIVED)) == set(ALL_METHODS)

    def test_live_assets_keep_full_access(self, admin):
        assert _allowed(admin, _asset(DB)) == set(ALL_METHODS)

    def test_the_database_can_be_created_edited_and_archived(self, admin):
        assert _allowed(admin, _database(DB)) == set(ALL_METHODS)
        assert admin.enforce(_api("/database"), POST) is True
        assert admin.enforce(_api(f"/database/{DB}"), PUT) is True
        assert admin.enforce(_api(f"/database/{DB}"), DELETE) is True

    def test_the_archived_database_record_can_be_viewed(self, admin):
        assert admin.enforce(_database(DB + ARCHIVED), "GET") is True

    def test_nothing_in_another_database_is_granted(self, admin):
        for database_id in (OTHER_DB, OTHER_DB + ARCHIVED):
            assert _allowed(admin, _asset(database_id)) == set(), database_id
            assert _allowed(admin, _database(database_id)) == set(), database_id


@pytest.mark.unit
class TestDatabaseUserTemplate:

    def test_archived_assets_are_not_reachable(self, user):
        assert _allowed(user, _asset(DB + ARCHIVED)) == set()

    def test_live_assets_keep_view_create_edit_and_archive(self, user):
        assert _allowed(user, _asset(DB)) == set(ALL_METHODS)

    def test_the_archived_database_record_is_not_reachable(self, user):
        assert _allowed(user, _database(DB + ARCHIVED)) == set()

    def test_the_database_metadata_actions_are_granted_on_the_database(self, user):
        """Tier 2 of database metadata create, update, delete and replace-all."""
        assert _allowed(user, _database(DB)) == set(ALL_METHODS)

    def test_the_database_metadata_routes_are_reachable(self, user):
        path = _concrete_path(API_DATABASE_METADATA.path)
        assert _allowed(user, _api(path), (POST, PUT, DELETE)) == {POST, PUT, DELETE}

    @pytest.mark.parametrize("method, path", [
        (POST, "/database"),
        (PUT, f"/database/{DB}"),
        (DELETE, f"/database/{DB}"),
    ], ids=["create", "edit-details", "archive-database"])
    def test_the_database_itself_cannot_be_created_edited_or_archived(self, user, method, path):
        assert user.enforce(_api(path), method) is False

    def test_an_asset_cannot_be_unarchived(self, user):
        path = _concrete_path(API_UNARCHIVE_ASSET.path)
        assert user.enforce(_api(path), PUT) is False

    @pytest.mark.parametrize("method, suffix", [
        (DELETE, "assets/a1/archiveAsset"),
        (DELETE, "assets/a1/archiveFile"),
        (POST, "assets/a1/unarchiveFile"),
        (POST, "assets/a1/assetversions/v1/archive"),
        (POST, "assets/a1/assetversions/v1/unarchive"),
    ])
    def test_asset_archive_and_file_and_version_archive_routes_stay_reachable(self, user, method, suffix):
        assert user.enforce(_api(f"/database/{DB}/{suffix}"), method) is True

    def test_every_post_and_put_route_under_its_database_is_reachable_except_unarchive(self, user):
        """The narrowed POST and PUT prefixes keep every write route the role had under its database."""
        refused = sorted(
            (method, route.path)
            for route in ALL_API_ROUTES
            if route.path.startswith("/database/{databaseId}/")
            for method in set(route.methods) & {POST, PUT}
            if not user.enforce(_api(_concrete_path(route.path)), method)
        )
        assert refused == [(PUT, API_UNARCHIVE_ASSET.path)]

    def test_deletes_under_its_database_reach_only_archive_paths_and_database_metadata(self, user):
        reached = sorted(
            route.path
            for route in ALL_API_ROUTES
            if route.path.startswith("/database/{databaseId}") and DELETE in route.methods
            and user.enforce(_api(_concrete_path(route.path)), DELETE)
        )
        assert reached == sorted([
            "/database/{databaseId}/assets/{assetId}/archiveAsset",
            API_ARCHIVE_FILE.path,
            API_DATABASE_METADATA.path,
        ])


@pytest.mark.unit
class TestDatabaseUserDenyIsScopedToItsDatabase:
    """A user role on dbX and an admin role on dbY: the user role's route deny leaves dbY alone."""

    @pytest.fixture
    def both(self, template_enforcer):
        return template_enforcer(
            ("database-user.json", "r-user", DB),
            ("database-admin.json", "r-admin", OTHER_DB),
        )

    def test_unarchive_in_the_admin_database_is_still_allowed(self, both):
        assert both.enforce(_api(_concrete_path(API_UNARCHIVE_ASSET.path, OTHER_DB)), PUT) is True
        assert both.enforce(_asset(OTHER_DB + ARCHIVED), PUT) is True

    def test_creating_the_admin_database_is_still_allowed(self, both):
        assert both.enforce(_api("/database"), POST) is True
        assert both.enforce(_database(OTHER_DB), POST) is True

    def test_the_user_database_keeps_its_user_limits(self, both):
        assert both.enforce(_api(_concrete_path(API_UNARCHIVE_ASSET.path, DB)), PUT) is False
        assert _allowed(both, _asset(DB + ARCHIVED)) == set()


@pytest.mark.unit
class TestDatabaseReadOnlyTemplate:

    def test_archived_records_are_not_reachable(self, readonly):
        assert readonly.enforce(_asset(DB + ARCHIVED), "GET") is False
        assert readonly.enforce(_database(DB + ARCHIVED), "GET") is False

    def test_live_records_can_be_viewed(self, readonly):
        assert _allowed(readonly, _asset(DB)) == {"GET"}
        assert _allowed(readonly, _database(DB)) == {"GET"}
