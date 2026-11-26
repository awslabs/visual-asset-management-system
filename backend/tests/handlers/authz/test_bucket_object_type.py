# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The `bucket` constraint object type: an asset bucket registration (issue #1978).

Pinned here: the type and its four fields are core constants served to the constraint editor; the
seeded admin role holds every action on every registration and the seeded read-only role holds GET;
every shipped permission template holds GET on every registration and nothing else on the type; a
custom role's constraint on `bucketName` allows only the matching registrations; a role with no
`bucket` constraint is denied (deny by default). Roles are evaluated through the real policy builder
and Casbin enforcer; only the two table reads are stubbed.
"""

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest
from aws_lambda_powertools.utilities.parser import parse

import backend.backend.handlers.authz as authz_module
from backend.backend.common import constants
from backend.backend.models.roleConstraints import ImportConstraintsTemplateRequestModel
from tests.handlers.authz import shipped_constraints

USER_ID = "bucket-user"
ACTIONS = ("GET", "PUT", "POST", "DELETE")
FIELDS = ["bucketId", "bucketName", "bucketRegion", "source"]
REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "documentation" / "permissionsTemplates"
TEMPLATE_VALUES = {"DATABASE_ID": "db-a", "TAG_VALUE": "locked", "ALLOWED_GROUPS": ".*"}


def _bucket(bucket_id="b-1", name="team-a-assets", region="us-east-1", source="static"):
    return {"object__type": "bucket", "bucketId": bucket_id, "bucketName": name, "bucketRegion": region,
            "source": source}


def _constraint(name, criteria_and, permissions, object_type="bucket"):
    return {
        "constraintId": name, "name": name, "description": name, "objectType": object_type,
        "criteriaAnd": criteria_and, "criteriaOr": [], "userPermissions": [],
        "groupPermissions": [{"groupId": "custom", "permission": p, "permissionType": "allow"}
                             for p in permissions],
    }


@pytest.fixture
def build_enforcer():
    """A real CasbinEnforcer for a user holding the roles in `{role name: constraints}`."""
    state = {"user_roles": [], "constraints": []}
    previous_policy_map = authz_module.casbin_user_policy_map
    previous_enforcer_map = authz_module.casbin_user_enforcer_map

    def build(roles):
        state["user_roles"] = [{"userId": USER_ID, "roleName": name} for name in roles]
        state["constraints"] = [c for constraints in roles.values() for c in constraints]
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}
        return authz_module.CasbinEnforcer({"tokens": [USER_ID], "roles": list(roles), "mfaEnabled": True})

    audit = MagicMock()
    with patch.object(
        authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
        side_effect=lambda *_args, **_kwargs: state["user_roles"],
    ), patch.object(
        authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
        side_effect=lambda role_names: state["constraints"],
    ), patch.object(authz_module, "log_authorization", audit):
        try:
            build.audit = audit
            yield build
        finally:
            authz_module.casbin_user_policy_map = previous_policy_map
            authz_module.casbin_user_enforcer_map = previous_enforcer_map


@pytest.mark.unit
class TestTheObjectTypeIsDeclared:
    def test_the_type_and_its_fields_are_core_constants(self):
        assert "bucket" in constants.CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES
        for field in FIELDS:
            assert constants.CORE_PERMISSION_CONSTRAINT_FIELDS[field] == ""
        assert constants.CORE_CONSTRAINT_OBJECT_TYPE_FIELDS["bucket"] == {
            "label": "Bucket",
            "fields": [{"label": "Bucket ID", "value": "bucketId"},
                       {"label": "Bucket Name", "value": "bucketName"},
                       {"label": "Bucket Region", "value": "bucketRegion"},
                       {"label": "Source", "value": "source"}],
        }

    def test_the_merged_names_consumers_import_carry_it(self):
        assert "bucket" in constants.ALLOWED_CONSTRAINT_OBJECT_TYPES
        assert constants.get_constraint_fields_for_object_type("bucket") == FIELDS

    def test_the_fields_can_be_evaluated(self):
        for field in FIELDS:
            assert field not in constants.EVALUATOR_RESERVED_ATTRIBUTES
            assert field not in constants.RESERVED_CONSTRAINT_KEYS

    def test_no_other_object_type_takes_the_new_fields(self):
        for object_type, entry in constants.CONSTRAINT_OBJECT_TYPE_FIELDS.items():
            if object_type != "bucket":
                assert not set(FIELDS) & {f["value"] for f in entry["fields"]}, object_type


@pytest.mark.unit
class TestCustomRoles:
    def test_a_constraint_on_the_bucket_name_allows_only_the_matching_registrations(self, build_enforcer):
        enforcer = build_enforcer({"custom": [_constraint(
            "team-a", [{"field": "bucketName", "operator": "starts_with", "value": "team-a-"}], ["GET"])]})
        assert enforcer.enforce(_bucket(name="team-a-assets"), "GET") is True
        assert enforcer.enforce(_bucket(name="team-b-assets"), "GET") is False
        assert enforcer.enforce(_bucket(name="xteam-a-assets"), "GET") is False
        # Only the granted action.
        for action in ("PUT", "POST", "DELETE"):
            assert enforcer.enforce(_bucket(name="team-a-assets"), action) is False

    def test_a_constraint_on_the_source_and_region(self, build_enforcer):
        enforcer = build_enforcer({"custom": [_constraint("static-west", [
            {"field": "source", "operator": "equals", "value": "static"},
            {"field": "bucketRegion", "operator": "equals", "value": "us-west-2"}], ["GET"])]})
        assert enforcer.enforce(_bucket(region="us-west-2"), "GET") is True
        assert enforcer.enforce(_bucket(region="us-east-1"), "GET") is False
        assert enforcer.enforce(_bucket(region="us-west-2", source="runtime"), "GET") is False

    def test_a_role_without_a_bucket_constraint_is_denied_and_the_denial_is_audited(self, build_enforcer):
        enforcer = build_enforcer({"custom": [_constraint(
            "db", [{"field": "databaseId", "operator": "contains", "value": ".*"}], ACTIONS,
            object_type="database")]})
        for action in ACTIONS:
            assert enforcer.enforce(_bucket(), action) is False
        audited = [c.args[2]["obj"] for c in build_enforcer.audit.call_args_list]
        assert audited and all(obj["object__type"] == "bucket" for obj in audited)

    def test_a_database_constraint_does_not_reach_a_bucket(self, build_enforcer):
        # A bucket object carries no databaseId, and a database line is never walked for it.
        enforcer = build_enforcer({"custom": [_constraint(
            "db", [{"field": "databaseId", "operator": "contains", "value": ".*"}], ["GET"],
            object_type="database")]})
        assert enforcer.enforce({"object__type": "database", "databaseId": "db-a"}, "GET") is True
        assert enforcer.enforce(_bucket(), "GET") is False


@pytest.mark.unit
class TestSeededRoles:
    def test_the_admin_role_holds_every_action_on_every_registration(self, build_enforcer):
        enforcer = build_enforcer({"admin": shipped_constraints.seeded_constraints("admin")})
        for obj in (_bucket(), _bucket(name="other", region="", source="runtime")):
            for action in ACTIONS:
                assert enforcer.enforce(obj, action) is True, action

    def test_the_read_only_role_reads_every_registration_and_nothing_more(self, build_enforcer):
        enforcer = build_enforcer({"basicReadOnly": shipped_constraints.seeded_constraints("basicReadOnly")})
        assert enforcer.enforce(_bucket(), "GET") is True
        assert enforcer.enforce(_bucket(name="other", source="runtime"), "GET") is True
        for action in ("PUT", "POST", "DELETE"):
            assert enforcer.enforce(_bucket(), action) is False

    def test_the_read_only_role_still_holds_no_route_that_lists_buckets(self, build_enforcer):
        enforcer = build_enforcer({"basicReadOnly": shipped_constraints.seeded_constraints("basicReadOnly")})
        assert enforcer.enforce({"object__type": "api", "route__path": "/buckets"}, "GET") is False


@pytest.mark.unit
class TestShippedTemplates:
    @pytest.mark.parametrize("template_name", shipped_constraints.template_names())
    def test_every_template_reads_every_registration_and_nothing_more(self, template_name):
        document = json.loads((TEMPLATES_DIR / template_name).read_text(encoding="utf-8"))
        bucket_constraints = [c for c in document["constraints"] if c["objectType"] == "bucket"]
        assert len(bucket_constraints) == 1
        constraint = bucket_constraints[0]
        assert constraint["criteriaAnd"] == [{"field": "bucketId", "operator": "contains", "value": ".*"}]
        assert constraint["criteriaOr"] == []
        assert constraint["groupPermissions"] == [{"action": "GET", "type": "allow"}]

    @pytest.mark.parametrize("template_name", shipped_constraints.template_names())
    def test_a_role_from_the_template_reads_any_registration(self, template_name, build_enforcer):
        role = shipped_constraints.template_constraints(template_name, "tpl", TEMPLATE_VALUES)
        bucket_only = [c for c in role if c["objectType"] == "bucket"]
        enforcer = build_enforcer({"tpl": bucket_only})
        assert enforcer.enforce(_bucket(), "GET") is True
        assert enforcer.enforce(_bucket(name="anything", region="", source="runtime"), "GET") is True
        assert enforcer.enforce(_bucket(), "POST") is False

    def test_twelve_templates_are_covered(self):
        assert len(shipped_constraints.template_names()) == 12

    @pytest.mark.parametrize("template_name", shipped_constraints.template_names())
    def test_the_template_passes_the_import_validation(self, template_name):
        document = json.loads((TEMPLATES_DIR / template_name).read_text(encoding="utf-8"))
        values = {v["name"]: TEMPLATE_VALUES.get(v["name"], "x") for v in document["variables"]}
        values["ROLE_NAME"] = "tpl"
        model = parse(dict(document, variableValues=values), model=ImportConstraintsTemplateRequestModel)
        assert [c.objectType for c in model.constraints].count("bucket") == 1
