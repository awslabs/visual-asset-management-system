# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A role built from a shipped viewing template can request a file download, and nothing else it may not.

The file viewers and the download action request a file through ``POST
/database/{databaseId}/assets/{assetId}/download`` (``API_DOWNLOAD_ASSET``): the request is a POST
because its body carries the file key and version, and it reads the file without changing anything.
The handler checks Tier 1 with ``enforceAPI`` for the POST and Tier 2 with a GET on the asset, so a
role whose ``api`` constraints allow POST only on non-download routes receives ``403`` in every viewer
that reads through it, whatever its asset grants are.

Each template is run through the import's own substitution and conversion (``substitute_variables``,
``build_constraint_data``) and then through the real policy generation and Casbin evaluation, with only
the two table reads stubbed. ``READ_ONLY_POST_ROUTES`` records every POST route a read-only template
reaches, so a criterion that widens the grant past the download route fails here as well as one that
misses it.
"""

import json
import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module  # noqa: E402
from backend.backend.common.apiRoutes import (  # noqa: E402
    ALL_API_ROUTES,
    API_AUTH_LOGIN_PROFILE,
    API_AUTH_ROUTES,
    API_CHECK_SUBSCRIPTION,
    API_DOWNLOAD_ASSET,
    API_SEARCH,
    API_SEARCH_SIMPLE,
    API_ZIP_DOWNLOADS_ASSET,
    API_ZIP_DOWNLOADS_ASSET_REQUEST_URL,
    POST,
)
from backend.backend.handlers.auth import authConstraintsTemplateService as svc  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "documentation" / "permissionsTemplates"

ROLE_NAME = "template-role"
USER_ID = "template-user"
DATABASE_ID = "template-db"
# What an operator supplies when importing the shipped templates.
VARIABLE_VALUES = {"ROLE_NAME": ROLE_NAME, "DATABASE_ID": DATABASE_ID, "TAG_VALUE": "locked"}

READ_ONLY_TEMPLATES = ("database-readonly.json", "global-readonly.json")
# Templates whose role views asset files; each reaches the download route.
VIEWING_TEMPLATES = ("database-admin.json", "database-user.json") + READ_ONLY_TEMPLATES
# Every POST route a read-only template reaches: non-mutating reads that carry their parameters in a body,
# plus Download as Zip (creating a job and requesting its URL change no asset data; the job is the caller's own).
READ_ONLY_POST_ROUTES = {
    API_AUTH_ROUTES,
    API_AUTH_LOGIN_PROFILE,
    API_SEARCH,
    API_SEARCH_SIMPLE,
    API_CHECK_SUBSCRIPTION,
    API_DOWNLOAD_ASSET,
    API_ZIP_DOWNLOADS_ASSET,
    API_ZIP_DOWNLOADS_ASSET_REQUEST_URL,
}
DOWNLOAD_CONSTRAINT_NAME = "{{ROLE_NAME}}-api-routes-download"


def _stored_constraints(template_name, drop_constraint=None):
    """The constraints an import of the template stores, in the shape the policy builder reads.

    ``drop_constraint`` first removes the constraint of that (unsubstituted) name, which is the
    template as it reads without that grant.
    """
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
        if constraint["name"] != drop_constraint
    ]
    substituted = svc.substitute_variables(constraints, VARIABLE_VALUES)
    return [svc.build_constraint_data(constraint, ROLE_NAME, {"tokens": [USER_ID]})
            for constraint in substituted]


def _api_request(path):
    """The Tier-1 object ``enforceAPI`` evaluates for a request path."""
    return {"object__type": "api", "route__path": path}


def _concrete_path(route_path):
    """The route template with each ``{parameter}`` segment filled, as a request carries it."""
    return re.sub(r"\{[^/{}]+\}", "probe-id", route_path)


def _download_path():
    return _concrete_path(API_DOWNLOAD_ASSET.path.replace("{databaseId}", DATABASE_ID))


@pytest.fixture
def template_enforcer():
    """Build the real CasbinEnforcer from a list of stored constraints.

    The per-user policy and enforcer caches are cleared on every build, so each policy is generated
    from the constraints passed in. The two table reads are the only stubs, and the denial audit
    writer is replaced so a denied check writes nothing. Both caches are restored afterwards.
    """
    state = {"constraints": []}
    previous_policy_map = authz_module.casbin_user_policy_map
    previous_enforcer_map = authz_module.casbin_user_enforcer_map

    def build(constraints):
        state["constraints"] = constraints
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}
        return authz_module.CasbinEnforcer(
            {"tokens": [USER_ID], "roles": [ROLE_NAME], "mfaEnabled": True})

    with patch.object(
        authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
        return_value=[{"userId": USER_ID, "roleName": ROLE_NAME}],
    ), patch.object(
        authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
        side_effect=lambda role_names: state["constraints"],
    ), patch.object(authz_module, "log_authorization", MagicMock()):
        try:
            yield build
        finally:
            authz_module.casbin_user_policy_map = previous_policy_map
            authz_module.casbin_user_enforcer_map = previous_enforcer_map


@pytest.mark.unit
class TestTemplatesGrantTheFileDownload:

    @pytest.mark.parametrize("template_name", VIEWING_TEMPLATES)
    def test_a_viewing_template_role_may_request_a_file_download(self, template_enforcer, template_name):
        enforcer = template_enforcer(_stored_constraints(template_name))
        assert enforcer.enforce(_api_request(_download_path()), POST) is True

    @pytest.mark.parametrize("template_name", READ_ONLY_TEMPLATES)
    def test_a_read_only_template_role_reaches_exactly_the_read_only_post_routes(
            self, template_enforcer, template_name):
        enforcer = template_enforcer(_stored_constraints(template_name))
        allowed = {
            route for route in ALL_API_ROUTES
            if POST in route.methods and enforcer.enforce(_api_request(_concrete_path(route.path)), POST)
        }
        assert allowed == READ_ONLY_POST_ROUTES, (
            f"{template_name}: unexpected {sorted(r.path for r in allowed - READ_ONLY_POST_ROUTES)}, "
            f"missing {sorted(r.path for r in READ_ONLY_POST_ROUTES - allowed)}")

    @pytest.mark.parametrize("template_name", READ_ONLY_TEMPLATES)
    def test_a_read_only_template_role_reads_the_asset_it_downloads(self, template_enforcer, template_name):
        """Tier 2 of the download is a GET on the asset, which the template's asset constraint allows."""
        enforcer = template_enforcer(_stored_constraints(template_name))
        asset = {"object__type": "asset", "databaseId": DATABASE_ID, "assetName": "probe",
                 "assetType": ".glb", "tags": []}
        assert enforcer.enforce(asset, "GET") is True


@pytest.mark.unit
class TestTheDownloadGrantCanFail:
    """Controls for the class above: the download constraint is the grant, and the evaluation denies."""

    @pytest.mark.parametrize("template_name", READ_ONLY_TEMPLATES)
    def test_without_the_download_constraint_the_route_is_denied(self, template_enforcer, template_name):
        enforcer = template_enforcer(
            _stored_constraints(template_name, drop_constraint=DOWNLOAD_CONSTRAINT_NAME))
        assert enforcer.enforce(_api_request(_download_path()), POST) is False

    @pytest.mark.parametrize("template_name", READ_ONLY_TEMPLATES)
    def test_the_download_constraint_does_not_reach_a_sibling_write_route(self, template_enforcer, template_name):
        enforcer = template_enforcer(_stored_constraints(template_name))
        for suffix in ("createFolder", "copyFile", "download/extra", "downloads"):
            path = f"/database/{DATABASE_ID}/assets/probe-id/{suffix}"
            assert enforcer.enforce(_api_request(path), POST) is False, path


ZIP_DOWNLOAD_ROUTES = tuple(route for route in ALL_API_ROUTES if route.category == "zipDownloads")


@pytest.mark.unit
class TestTemplatesGrantDownloadAsZip:
    """Every template whose role views files reaches every zip download route and method.

    The handler scopes create, get, cancel and delete to the requester, and checks the asset (and, for
    another user's archive, the file download route) on the shared list and the URL route, so the
    read-only templates get the whole set like the user and admin templates. The all-users list sits
    under `/download/zip-all-users`, which the templates' `/download/zip` criteria already reach.
    """

    def test_the_route_list_holds_the_seven_zip_operations(self):
        operations = sorted((route.path, method) for route in ZIP_DOWNLOAD_ROUTES for method in route.methods)
        assert len(operations) == 7, operations

    @pytest.mark.parametrize("template_name", VIEWING_TEMPLATES)
    def test_a_viewing_template_role_reaches_every_zip_route(self, template_enforcer, template_name):
        enforcer = template_enforcer(_stored_constraints(template_name))
        missing = [
            (route.path, method)
            for route in ZIP_DOWNLOAD_ROUTES
            for method in route.methods
            if not enforcer.enforce(
                _api_request(_concrete_path(route.path.replace("{databaseId}", DATABASE_ID))), method)
        ]
        assert missing == [], f"{template_name}: zip routes not granted {missing}"


def _seeded_as_role(role):
    """The seeded constraints of ``role`` re-pointed at the fixture's role name."""
    from backend.tests.handlers.authz import shipped_constraints

    constraints = shipped_constraints.seeded_constraints(role)
    for constraint in constraints:
        for permission in constraint.get("groupPermissions", []):
            permission["groupId"] = ROLE_NAME
    return constraints


SHARED_LIST_PATH = "/database/template-db/assets/probe-id/download/zip-all-users"
SHARED_URL_PATH = "/database/template-db/assets/probe-id/download/zip/probe-id/url"
FILE_DOWNLOAD_PATH = "/database/template-db/assets/probe-id/download"


@pytest.mark.unit
class TestDownloadQueueSharedView:
    """The Download Queue's shared view needs the all-users list route, and downloading another
    user's archive needs the URL route plus the asset's file download route. Every viewing template
    and both seeded roles grant all three; the handler then checks the asset itself."""

    @pytest.mark.parametrize("template_name", VIEWING_TEMPLATES)
    def test_a_viewing_template_role_reaches_the_shared_view(self, template_enforcer, template_name):
        enforcer = template_enforcer(_stored_constraints(template_name))
        assert enforcer.enforce(_api_request(SHARED_LIST_PATH), "GET")
        assert enforcer.enforce(_api_request(SHARED_URL_PATH), "POST")
        assert enforcer.enforce(_api_request(FILE_DOWNLOAD_PATH), "POST")

    @pytest.mark.parametrize("role", ("admin", "basicReadOnly"))
    def test_a_seeded_role_reaches_the_shared_list(self, template_enforcer, role):
        enforcer = template_enforcer(_seeded_as_role(role))
        assert enforcer.enforce(_api_request(SHARED_LIST_PATH), "GET")

    def test_a_role_without_api_grants_does_not(self, template_enforcer):
        enforcer = template_enforcer(_stored_constraints("database-tag-admin.json"))
        assert not enforcer.enforce(_api_request(SHARED_LIST_PATH), "GET")
