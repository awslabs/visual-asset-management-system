# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The default read-only role must still see tags and tag types after per-database namespacing.

The CDK seeds that role (dynamodb-authdefaults-ro-construct.ts) with a constraint that matches on
``tagName``/``tagTypeName`` alone and says nothing about ``databaseId``. This exercises the real
policy generation and Casbin evaluation — not a mocked ``enforce`` — because the risk is in the
constraint-field matrix: ``_generate_criteria_object_rules`` DROPS a criterion whose field is not
valid for the object type, and ``_scrub_object_fields`` strips object attributes that are not valid
for it. A tag object now carries ``databaseId``, so this proves the extra attribute neither breaks
matching nor narrows what the read-only role can read.
"""

import pytest
from unittest.mock import patch

from backend.backend.handlers.authz import CasbinEnforcer
import backend.backend.handlers.authz as authz_module


def _ro_tag_constraints(role_name="readonly"):
    """The tag/tagType constraints the CDK read-only construct seeds, in parsed form."""
    return [
        {
            "constraintId": f"initial_{role_name}_allow_all_tags",
            "name": f"{role_name}-allow-all-tags",
            "objectType": "tag",
            "criteriaAnd": [{"field": "tagName", "operator": "contains", "value": ".*"}],
            "groupPermissions": [
                {"groupId": role_name, "permission": "GET", "permissionType": "allow"}
            ],
        },
        {
            "constraintId": f"initial_{role_name}_allow_all_tagtypes",
            "name": f"{role_name}-allow-all-tagtypes",
            "objectType": "tagType",
            "criteriaAnd": [{"field": "tagTypeName", "operator": "contains", "value": ".*"}],
            "groupPermissions": [
                {"groupId": role_name, "permission": "GET", "permissionType": "allow"}
            ],
        },
    ]


@pytest.fixture
def readonly_enforcer():
    """The public CasbinEnforcer proxy, carrying only the seeded read-only tag constraints.

    Both caches are cleared so the policy is generated from these constraints rather than reused
    from another test, and the two table reads on the inner service are stubbed so no AWS call is
    made. Everything downstream — policy text, rule generation, field scrubbing, Casbin evaluation —
    is the real implementation.
    """
    authz_module.casbin_user_policy_map = {}
    authz_module.casbin_user_enforcer_map = {}

    with patch.object(
        authz_module.CasbinEnforcerService,
        "_read_current_user_roles_from_table",
        return_value=[{"userId": "ro-user", "roleName": "readonly"}],
    ), patch.object(
        authz_module.CasbinEnforcerService,
        "_read_policies_batch_optimized",
        return_value=_ro_tag_constraints(),
    ):
        yield CasbinEnforcer({"tokens": ["ro-user"], "roles": ["readonly"], "mfaEnabled": True})


@pytest.mark.unit
class TestReadOnlyRoleTagScope:
    def test_readonly_can_read_a_global_tag(self, readonly_enforcer):
        tag = {
            "object__type": "tag",
            "tagName": "Status",
            "tagTypeName": "Lifecycle",
            "databaseId": "GLOBAL",
        }
        assert readonly_enforcer.enforce(tag, "GET") is True

    def test_readonly_can_read_a_database_scoped_tag(self, readonly_enforcer):
        # The new databaseId attribute must not cause the seeded constraint to stop matching.
        tag = {
            "object__type": "tag",
            "tagName": "Status",
            "tagTypeName": "Lifecycle",
            "databaseId": "factory-db",
        }
        assert readonly_enforcer.enforce(tag, "GET") is True

    def test_readonly_can_read_a_tag_with_no_databaseId(self, readonly_enforcer):
        # A row written before the upgrade and not yet migrated carries no databaseId at all.
        tag = {"object__type": "tag", "tagName": "Status", "tagTypeName": "Lifecycle"}
        assert readonly_enforcer.enforce(tag, "GET") is True

    def test_readonly_can_read_global_and_scoped_tag_types(self, readonly_enforcer):
        for database_id in ("GLOBAL", "factory-db"):
            tag_type = {
                "object__type": "tagType",
                "tagTypeName": "Lifecycle",
                "required": "False",
                "databaseId": database_id,
            }
            assert readonly_enforcer.enforce(tag_type, "GET") is True

    def test_readonly_still_cannot_write_tags(self, readonly_enforcer):
        # The seeded constraint grants GET only; namespacing must not widen it.
        tag = {"object__type": "tag", "tagName": "Status", "databaseId": "factory-db"}
        for action in ("POST", "PUT", "DELETE"):
            assert readonly_enforcer.enforce(tag, action) is False
