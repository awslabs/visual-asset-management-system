# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for combined AND + OR criteria within a single constraint policy.

When a constraint defines both criteriaAnd and criteriaOr, the generated Casbin
policy must require ALL of the AND criteria to be true AND at least one of the
OR criteria to be true. (Previously two separate policy lines were emitted,
which Casbin's some(where allow) effect treated as alternatives: AND-rule OR
OR-rule.)

These tests run the REAL policy text generation (_create_policy_text_helper,
with the DynamoDB reads stubbed) and the REAL Casbin enforcer.
"""

import pytest
from unittest.mock import patch
from casbin import FastEnforcer, model
from casbin.persist.adapters import string_adapter

from backend.backend.handlers.authz import CasbinEnforcerService
from backend.backend.common.constants import (
    PERMISSION_CONSTRAINT_POLICY,
    PERMISSION_CONSTRAINT_FIELDS,
)


def _build_policy_text(policies, user_id="tester", roles=("r1",)):
    """Run the real _create_policy_text_helper with the table reads stubbed."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = user_id
    svc._mfaEnabled = True
    user_roles = [{"userId": user_id, "roleName": role} for role in roles]
    with patch.object(CasbinEnforcerService, "_read_current_user_roles_from_table", return_value=user_roles), \
         patch.object(CasbinEnforcerService, "_read_policies_batch_optimized", return_value=policies):
        return svc._create_policy_text_helper()


def _build_enforcer(policy_text):
    new_model = model.Model()
    new_model.load_model_from_text(PERMISSION_CONSTRAINT_POLICY)
    adapter = string_adapter.StringAdapter(policy_text)
    return FastEnforcer(model=new_model, adapter=adapter, enable_log=False)


def _enforce(enforcer, user_id="tester", **obj_fields):
    obj = PERMISSION_CONSTRAINT_FIELDS.copy()
    obj.update(obj_fields)
    return enforcer.enforce(f"user::{user_id}", obj, "GET")


def _policy(criteria_and=None, criteria_or=None, object_type="asset"):
    return {
        "constraintId": "c1",
        "objectType": object_type,
        "criteriaAnd": criteria_and or [],
        "criteriaOr": criteria_or or [],
        "groupPermissions": [
            {"groupId": "r1", "permission": "GET", "permissionType": "allow"}
        ],
        "userPermissions": [],
    }


@pytest.mark.unit
class TestCombinedAndOrCriteria:
    """Both AND and OR present: all ANDs AND at least one OR must hold."""

    def _enforcer(self):
        policies = [_policy(
            criteria_and=[{"field": "databaseId", "operator": "equals", "value": "db1"}],
            criteria_or=[
                {"field": "assetType", "operator": "equals", "value": ".glb"},
                {"field": "assetType", "operator": "equals", "value": ".gltf"},
            ],
        )]
        return _build_enforcer(_build_policy_text(policies))

    def test_and_true_one_or_true_allows(self):
        e = self._enforcer()
        assert _enforce(e, object__type="asset", databaseId="db1", assetType=".glb") is True
        assert _enforce(e, object__type="asset", databaseId="db1", assetType=".gltf") is True

    def test_and_true_all_or_false_denies(self):
        e = self._enforcer()
        assert _enforce(e, object__type="asset", databaseId="db1", assetType=".obj") is False

    def test_and_false_or_true_denies(self):
        e = self._enforcer()
        assert _enforce(e, object__type="asset", databaseId="db2", assetType=".glb") is False

    def test_wrong_object_type_denies(self):
        e = self._enforcer()
        assert _enforce(e, object__type="database", databaseId="db1", assetType=".glb") is False

    def test_single_policy_line_emitted(self):
        """Combined AND+OR constraints produce exactly one policy line per permission."""
        policies = [_policy(
            criteria_and=[{"field": "databaseId", "operator": "equals", "value": "db1"}],
            criteria_or=[{"field": "assetType", "operator": "equals", "value": ".glb"}],
        )]
        policy_text = _build_policy_text(policies)
        p_lines = [line for line in policy_text.split("\n") if line.startswith("p,")]
        assert len(p_lines) == 1


@pytest.mark.unit
class TestAndOnlyCriteria:
    """Only AND criteria: all must hold (unchanged behavior)."""

    def _enforcer(self):
        policies = [_policy(
            criteria_and=[
                {"field": "databaseId", "operator": "equals", "value": "db1"},
                {"field": "assetType", "operator": "equals", "value": ".glb"},
            ],
        )]
        return _build_enforcer(_build_policy_text(policies))

    def test_all_true_allows(self):
        assert _enforce(self._enforcer(), object__type="asset", databaseId="db1", assetType=".glb") is True

    def test_one_false_denies(self):
        assert _enforce(self._enforcer(), object__type="asset", databaseId="db1", assetType=".obj") is False


@pytest.mark.unit
class TestOrOnlyCriteria:
    """Only OR criteria: at least one must hold (unchanged behavior)."""

    def _enforcer(self):
        policies = [_policy(
            criteria_or=[
                {"field": "databaseId", "operator": "equals", "value": "db1"},
                {"field": "databaseId", "operator": "equals", "value": "db2"},
            ],
        )]
        return _build_enforcer(_build_policy_text(policies))

    def test_one_true_allows(self):
        assert _enforce(self._enforcer(), object__type="asset", databaseId="db2") is True

    def test_all_false_denies(self):
        assert _enforce(self._enforcer(), object__type="asset", databaseId="db3") is False


@pytest.mark.unit
class TestLegacyCriteriaField:
    """Legacy 'criteria' field is folded into criteriaAnd (backwards compatibility)."""

    def test_legacy_criteria_treated_as_and(self):
        policies = [{
            "constraintId": "c-legacy",
            "objectType": "asset",
            "criteria": [{"field": "databaseId", "operator": "equals", "value": "db1"}],
            "groupPermissions": [
                {"groupId": "r1", "permission": "GET", "permissionType": "allow"}
            ],
            "userPermissions": [],
        }]
        # The helper folds 'criteria' into criteriaAnd before rule generation.
        # Note: production deserialization always supplies criteriaAnd; mirror that.
        policies[0]["criteriaAnd"] = []
        enforcer = _build_enforcer(_build_policy_text(policies))
        assert _enforce(enforcer, object__type="asset", databaseId="db1") is True
        assert _enforce(enforcer, object__type="asset", databaseId="db2") is False


@pytest.mark.unit
class TestNoCriteriaPolicy:
    """A policy without any criteria emits no rule (deny by default)."""

    def test_no_rule_emitted(self):
        policies = [_policy()]
        policy_text = _build_policy_text(policies)
        p_lines = [line for line in policy_text.split("\n") if line.startswith("p,")]
        assert len(p_lines) == 0


@pytest.mark.unit
class TestDenyPolicyInteraction:
    """Deny policies still override allow with combined criteria."""

    def test_deny_overrides_allow(self):
        allow_policy = _policy(
            criteria_and=[{"field": "databaseId", "operator": "equals", "value": "db1"}],
        )
        deny_policy = {
            "constraintId": "c-deny",
            "objectType": "asset",
            "criteriaAnd": [{"field": "assetType", "operator": "equals", "value": ".exe"}],
            "criteriaOr": [],
            "groupPermissions": [
                {"groupId": "r1", "permission": "GET", "permissionType": "deny"}
            ],
            "userPermissions": [],
        }
        enforcer = _build_enforcer(_build_policy_text([allow_policy, deny_policy]))
        assert _enforce(enforcer, object__type="asset", databaseId="db1", assetType=".glb") is True
        assert _enforce(enforcer, object__type="asset", databaseId="db1", assetType=".exe") is False
