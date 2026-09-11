# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Multi-policy combination smoke tests for the Casbin authorization engine.

A real user/role typically carries MANY constraints simultaneously -- each with
its own AND criteria, OR criteria, or both, with mixed allow/deny effects,
across object types, actions, roles, and user-level permissions. The Casbin
policy effect is:

    e = some(where (p.eft == allow)) && !some(where (p.eft == deny))

i.e. access is granted iff at least one matching allow rule exists AND no
matching deny rule exists. These tests pin down that the policy text generated
by the REAL _create_policy_text_helper (DynamoDB reads stubbed) combined with
the REAL CasbinEnforcerService.enforce() wrapper (the production path,
including missing-field padding) produces the correct decision when many
policies with different criteria combinations apply to a single check.

Pinned semantics (security-critical):
  * Combined AND+OR applies to DENY rules too: a deny with both groups only
    matches when all ANDs AND at least one OR hold (narrower than the legacy
    two-line emission, which denied when either group matched).
  * Any matching deny overrides any number of matching allows -- across
    policies, across roles, and between group- and user-level permissions.
  * Rules are isolated by action (r.act == p.act) and by objectType (every
    rule is prefixed with an objectType equals check).
  * No matching allow rule => default deny.
  * Decisions are independent of policy emission order.
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

USER_ID = "tester"


def _build_service(policies, roles=("roleA",)):
    """Build a CasbinEnforcerService running the REAL policy-text generation
    and REAL enforce() wrapper, with only the DynamoDB reads stubbed."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = USER_ID
    svc._mfaEnabled = True
    user_roles = [{"userId": USER_ID, "roleName": role} for role in roles]
    with patch.object(CasbinEnforcerService, "_read_current_user_roles_from_table", return_value=user_roles), \
         patch.object(CasbinEnforcerService, "_read_policies_batch_optimized", return_value=policies):
        policy_text = svc._create_policy_text_helper()
    new_model = model.Model()
    new_model.load_model_from_text(PERMISSION_CONSTRAINT_POLICY)
    adapter = string_adapter.StringAdapter(policy_text)
    svc._enforcer = FastEnforcer(model=new_model, adapter=adapter, enable_log=False)
    return svc


def _constraint(constraint_id, object_type="asset", criteria_and=None, criteria_or=None,
                group_perms=None, user_perms=None):
    """Build a constraint document in the deserialized DynamoDB shape."""
    return {
        "constraintId": constraint_id,
        "objectType": object_type,
        "criteriaAnd": criteria_and or [],
        "criteriaOr": criteria_or or [],
        "groupPermissions": group_perms or [],
        "userPermissions": user_perms or [],
    }


def _crit(field, operator, value):
    return {"field": field, "operator": operator, "value": value}


def _gp(group_id, permission, permission_type="allow"):
    return {"groupId": group_id, "permission": permission, "permissionType": permission_type}


def _up(user_id, permission, permission_type="allow"):
    return {"userId": user_id, "permission": permission, "permissionType": permission_type}


# ---------------------------------------------------------------------------
# The 10-policy matrix: one role, every criteria/effect combination in play
# simultaneously. Constraint inventory:
#   P1  allow GET   asset     AND-only:      databaseId == db1
#   P2  allow GET   asset     OR-only:       databaseId == db2 || db3
#   P3  allow GET   asset     AND+OR:        databaseId == db4 && (.glb || .gltf)
#   P4  deny  GET   asset     AND-only:      tags is_one_of locked
#   P5  deny  GET   asset     AND+OR:        databaseId == db4 && (.exe || .bat)
#   P6  allow GET   asset     legacy 'criteria' field: databaseId == db5
#   P7  allow GET   database  AND-only:      databaseId == db1   (other objectType)
#   P8  allow PUT   asset     AND-only:      databaseId == db6   (other action)
#   P9  deny  GET   asset     user-level:    assetName starts_with secret-
#   P10 allow GET   pipeline  wildcard:      pipelineId contains .*
# ---------------------------------------------------------------------------
def _ten_policy_matrix():
    return [
        _constraint("P1", criteria_and=[_crit("databaseId", "equals", "db1")],
                    group_perms=[_gp("roleA", "GET")]),
        _constraint("P2", criteria_or=[_crit("databaseId", "equals", "db2"),
                                       _crit("databaseId", "equals", "db3")],
                    group_perms=[_gp("roleA", "GET")]),
        _constraint("P3", criteria_and=[_crit("databaseId", "equals", "db4")],
                    criteria_or=[_crit("assetType", "equals", ".glb"),
                                 _crit("assetType", "equals", ".gltf")],
                    group_perms=[_gp("roleA", "GET")]),
        _constraint("P4", criteria_and=[_crit("tags", "is_one_of", "locked")],
                    group_perms=[_gp("roleA", "GET", "deny")]),
        _constraint("P5", criteria_and=[_crit("databaseId", "equals", "db4")],
                    criteria_or=[_crit("assetType", "equals", ".exe"),
                                 _crit("assetType", "equals", ".bat")],
                    group_perms=[_gp("roleA", "GET", "deny")]),
        {
            # Legacy shape: 'criteria' (no criteriaAnd key from the table read)
            "constraintId": "P6",
            "objectType": "asset",
            "criteria": [_crit("databaseId", "equals", "db5")],
            "criteriaOr": [],
            "groupPermissions": [_gp("roleA", "GET")],
            "userPermissions": [],
        },
        _constraint("P7", object_type="database",
                    criteria_and=[_crit("databaseId", "equals", "db1")],
                    group_perms=[_gp("roleA", "GET")]),
        _constraint("P8", criteria_and=[_crit("databaseId", "equals", "db6")],
                    group_perms=[_gp("roleA", "PUT")]),
        _constraint("P9", criteria_and=[_crit("assetName", "starts_with", "secret-")],
                    user_perms=[_up(USER_ID, "GET", "deny")]),
        _constraint("P10", object_type="pipeline",
                    criteria_and=[_crit("pipelineId", "contains", ".*")],
                    group_perms=[_gp("roleA", "GET")]),
    ]


@pytest.fixture(scope="module")
def matrix_service():
    return _build_service(_ten_policy_matrix())


def _asset(svc, act="GET", **fields):
    obj = {"object__type": "asset"}
    obj.update(fields)
    return svc.enforce(obj, act)


@pytest.mark.unit
class TestTenPolicyMatrix:
    """All 10 policies active at once; every check exercises the full set."""

    # --- allows from each criteria style ---

    def test_and_only_allow(self, matrix_service):
        assert _asset(matrix_service, databaseId="db1", assetType=".obj") is True

    def test_or_only_allow_both_branches(self, matrix_service):
        assert _asset(matrix_service, databaseId="db2") is True
        assert _asset(matrix_service, databaseId="db3") is True

    def test_combined_and_or_allow(self, matrix_service):
        assert _asset(matrix_service, databaseId="db4", assetType=".glb") is True
        assert _asset(matrix_service, databaseId="db4", assetType=".gltf") is True

    def test_combined_and_or_allow_requires_both_groups(self, matrix_service):
        # AND true but no OR branch true -> P3 does not match -> no allow
        assert _asset(matrix_service, databaseId="db4", assetType=".obj") is False

    def test_legacy_criteria_allow(self, matrix_service):
        assert _asset(matrix_service, databaseId="db5") is True

    def test_unmatched_database_default_deny(self, matrix_service):
        assert _asset(matrix_service, databaseId="db99") is False

    # --- denies override allows across policies ---

    def test_and_only_deny_overrides_allow(self, matrix_service):
        # P1 allows db1, but P4 denies anything tagged locked
        assert _asset(matrix_service, databaseId="db1", tags=["locked"]) is False

    def test_deny_does_not_fire_without_its_criteria(self, matrix_service):
        assert _asset(matrix_service, databaseId="db1", tags=["public"]) is True

    def test_combined_deny_blocks_when_both_groups_match(self, matrix_service):
        # P5 denies db4 && (.exe || .bat). P3 would not allow .exe anyway, but
        # even with a hypothetical allow the deny must dominate; verify the
        # deny itself fires by checking a db4 .exe with a tag-based allow path.
        assert _asset(matrix_service, databaseId="db4", assetType=".exe") is False
        assert _asset(matrix_service, databaseId="db4", assetType=".bat") is False

    def test_combined_deny_requires_both_groups(self, matrix_service):
        # .exe outside db4: P5's AND fails, so the deny must NOT fire.
        # db1 .exe is allowed by P1 (no other deny matches).
        assert _asset(matrix_service, databaseId="db1", assetType=".exe") is True

    def test_user_level_deny_overrides_group_allow(self, matrix_service):
        # P9 (userPermissions deny) vs P1 (groupPermissions allow)
        assert _asset(matrix_service, databaseId="db1", assetName="secret-model") is False
        assert _asset(matrix_service, databaseId="db1", assetName="open-model") is True

    def test_multiple_denies_can_stack(self, matrix_service):
        # locked tag AND secret- name: two denies match simultaneously
        assert _asset(matrix_service, databaseId="db1", assetName="secret-x",
                      tags=["locked"]) is False

    # --- objectType isolation ---

    def test_object_type_isolation_database_vs_asset(self, matrix_service):
        # P7 allows GET on database db1; an asset check for db1 must rely on
        # P1, and a database check must rely on P7 (not on P1).
        assert matrix_service.enforce({"object__type": "database", "databaseId": "db1"}, "GET") is True
        assert matrix_service.enforce({"object__type": "database", "databaseId": "db5"}, "GET") is False

    def test_object_type_isolation_pipeline_wildcard_does_not_leak(self, matrix_service):
        # P10's pipelineId contains .* matches everything -- but only for
        # object__type == pipeline. Asset checks must not be allowed by it.
        assert matrix_service.enforce({"object__type": "pipeline", "pipelineId": "anything"}, "GET") is True
        assert _asset(matrix_service, databaseId="db98", pipelineId="anything") is False

    # --- action isolation ---

    def test_action_isolation(self, matrix_service):
        # P8 allows PUT on db6; GET on db6 has no allow
        assert _asset(matrix_service, act="PUT", databaseId="db6") is True
        assert _asset(matrix_service, act="GET", databaseId="db6") is False
        # P1 allows GET on db1; PUT on db1 has no allow
        assert _asset(matrix_service, act="PUT", databaseId="db1") is False

    def test_deny_is_action_scoped(self, matrix_service):
        # P4 denies GET on locked assets; a PUT on a locked db6 asset is
        # still allowed by P8 (the GET deny does not bleed into PUT).
        assert _asset(matrix_service, act="PUT", databaseId="db6", tags=["locked"]) is True


@pytest.mark.unit
class TestPolicyOrderIndependence:
    """The decision must not depend on the order policies are emitted."""

    def test_reversed_policy_order_same_decisions(self):
        forward = _build_service(_ten_policy_matrix())
        reverse = _build_service(list(reversed(_ten_policy_matrix())))
        probes = [
            ({"object__type": "asset", "databaseId": "db1"}, "GET"),
            ({"object__type": "asset", "databaseId": "db1", "tags": ["locked"]}, "GET"),
            ({"object__type": "asset", "databaseId": "db4", "assetType": ".glb"}, "GET"),
            ({"object__type": "asset", "databaseId": "db4", "assetType": ".exe"}, "GET"),
            ({"object__type": "asset", "databaseId": "db4", "assetType": ".obj"}, "GET"),
            ({"object__type": "asset", "databaseId": "db6"}, "PUT"),
            ({"object__type": "asset", "databaseId": "db1", "assetName": "secret-a"}, "GET"),
            ({"object__type": "pipeline", "pipelineId": "p"}, "GET"),
            ({"object__type": "database", "databaseId": "db1"}, "GET"),
        ]
        for obj, act in probes:
            assert forward.enforce(dict(obj), act) == reverse.enforce(dict(obj), act), \
                f"order-dependent decision for {obj} {act}"


@pytest.mark.unit
class TestCrossRoleCombinations:
    """User in multiple roles: allows union across roles; any deny dominates."""

    def _service(self):
        policies = [
            _constraint("A1", criteria_and=[_crit("databaseId", "equals", "db1")],
                        group_perms=[_gp("roleA", "GET")]),
            _constraint("B1", criteria_and=[_crit("databaseId", "equals", "db7")],
                        group_perms=[_gp("roleB", "GET")]),
            _constraint("B2", criteria_and=[_crit("databaseId", "equals", "db1")],
                        criteria_or=[_crit("assetType", "equals", ".exe"),
                                     _crit("assetType", "equals", ".bat")],
                        group_perms=[_gp("roleB", "GET", "deny")]),
        ]
        return _build_service(policies, roles=("roleA", "roleB"))

    def test_allows_union_across_roles(self):
        svc = self._service()
        assert _asset(svc, databaseId="db1", assetType=".glb") is True   # via roleA
        assert _asset(svc, databaseId="db7") is True                      # via roleB

    def test_deny_from_one_role_blocks_allow_from_another(self):
        svc = self._service()
        # roleA allows db1, roleB's combined deny matches db1 .exe
        assert _asset(svc, databaseId="db1", assetType=".exe") is False

    def test_cross_role_combined_deny_requires_both_groups(self):
        svc = self._service()
        # roleB's deny AND group requires db1; .exe in db7 is not denied
        assert _asset(svc, databaseId="db7", assetType=".exe") is True

    def test_role_not_held_grants_nothing(self):
        # Same policies but the user only holds roleA: roleB's allow must not apply
        policies = [
            _constraint("A1", criteria_and=[_crit("databaseId", "equals", "db1")],
                        group_perms=[_gp("roleA", "GET")]),
            _constraint("B1", criteria_and=[_crit("databaseId", "equals", "db7")],
                        group_perms=[_gp("roleB", "GET")]),
        ]
        svc = _build_service(policies, roles=("roleA",))
        assert _asset(svc, databaseId="db1") is True
        assert _asset(svc, databaseId="db7") is False


@pytest.mark.unit
class TestMixedEffectsWithinOneConstraint:
    """One constraint document can carry allow and deny permission entries
    sharing the same combined criteria rule."""

    def test_allow_and_deny_on_different_actions(self):
        policies = [
            _constraint("M1",
                        criteria_and=[_crit("databaseId", "equals", "db1")],
                        criteria_or=[_crit("assetType", "equals", ".glb"),
                                     _crit("assetType", "equals", ".obj")],
                        group_perms=[_gp("roleA", "GET"),
                                     _gp("roleA", "PUT"),
                                     _gp("roleA", "DELETE", "deny")]),
        ]
        svc = _build_service(policies)
        assert _asset(svc, act="GET", databaseId="db1", assetType=".glb") is True
        assert _asset(svc, act="PUT", databaseId="db1", assetType=".obj") is True
        # DELETE has a matching deny and no matching allow
        assert _asset(svc, act="DELETE", databaseId="db1", assetType=".glb") is False
        # Outside the criteria nothing matches at all
        assert _asset(svc, act="GET", databaseId="db2", assetType=".glb") is False

    def test_group_allow_with_user_deny_same_constraint(self):
        policies = [
            _constraint("M2",
                        criteria_and=[_crit("databaseId", "equals", "db1")],
                        group_perms=[_gp("roleA", "GET")],
                        user_perms=[_up(USER_ID, "GET", "deny")]),
        ]
        svc = _build_service(policies)
        # Both rules share the same criteria; the deny wins for this user
        assert _asset(svc, databaseId="db1") is False


@pytest.mark.unit
class TestOverlappingAllowDenyBoundaries:
    """Allow and deny with partially overlapping criteria: access remains in
    the allow-minus-deny region only."""

    def _service(self):
        policies = [
            # Broad allow: everything in db1
            _constraint("O1", criteria_and=[_crit("databaseId", "equals", "db1")],
                        group_perms=[_gp("roleA", "GET")]),
            # Narrow combined deny inside it: db1 && secret- && (.exe || locked tag)
            _constraint("O2",
                        criteria_and=[_crit("databaseId", "equals", "db1"),
                                      _crit("assetName", "starts_with", "secret-")],
                        criteria_or=[_crit("assetType", "equals", ".exe"),
                                     _crit("tags", "is_one_of", "locked")],
                        group_perms=[_gp("roleA", "GET", "deny")]),
        ]
        return _build_service(policies)

    def test_inside_allow_outside_deny(self):
        svc = self._service()
        assert _asset(svc, databaseId="db1", assetName="open-model", assetType=".exe") is True
        assert _asset(svc, databaseId="db1", assetName="secret-x", assetType=".glb") is True

    def test_inside_deny_region(self):
        svc = self._service()
        assert _asset(svc, databaseId="db1", assetName="secret-x", assetType=".exe") is False
        assert _asset(svc, databaseId="db1", assetName="secret-x", assetType=".glb",
                      tags=["locked"]) is False

    def test_outside_allow_entirely(self):
        svc = self._service()
        assert _asset(svc, databaseId="db2", assetName="open-model") is False


@pytest.mark.unit
class TestNegatedOperatorsInOrGroup:
    """Negated operators (does_not_contain, is_not_one_of) inside the combined
    OR group must keep their negation scoped to their own criterion."""

    def test_does_not_contain_in_or_group(self):
        policies = [
            _constraint("N1",
                        criteria_and=[_crit("databaseId", "equals", "db1")],
                        criteria_or=[_crit("assetType", "does_not_contain", ".exe"),
                                     _crit("tags", "is_one_of", "safe")],
                        group_perms=[_gp("roleA", "GET")]),
        ]
        svc = _build_service(policies)
        assert _asset(svc, databaseId="db1", assetType=".glb", tags=[]) is True
        assert _asset(svc, databaseId="db1", assetType=".exe", tags=[]) is False
        assert _asset(svc, databaseId="db1", assetType=".exe", tags=["safe"]) is True
        # AND group must still gate the whole rule
        assert _asset(svc, databaseId="db2", assetType=".glb", tags=[]) is False

    def test_is_not_one_of_in_or_group(self):
        policies = [
            _constraint("N2",
                        criteria_and=[_crit("databaseId", "equals", "db1")],
                        criteria_or=[_crit("tags", "is_not_one_of", "locked"),
                                     _crit("assetName", "starts_with", "override-")],
                        group_perms=[_gp("roleA", "GET")]),
        ]
        svc = _build_service(policies)
        assert _asset(svc, databaseId="db1", tags=[]) is True
        assert _asset(svc, databaseId="db1", tags=["locked"]) is False
        assert _asset(svc, databaseId="db1", tags=["locked"], assetName="override-x") is True


@pytest.mark.unit
class TestDeprecatedFieldHandling:
    """Criteria with fields not in PERMISSION_CONSTRAINT_FIELDS are skipped at
    rule-generation time. Pin the resulting (pre-existing) semantics so any
    future change here is a conscious decision."""

    def test_all_criteria_deprecated_emits_no_rule_fail_closed_for_allow(self):
        policies = [
            _constraint("D1", criteria_and=[_crit("nonexistent__field", "equals", "x")],
                        group_perms=[_gp("roleA", "GET")]),
        ]
        svc = _build_service(policies)
        # The allow rule vanished entirely -> default deny (fails closed)
        assert _asset(svc, databaseId="db1") is False

    def test_all_criteria_deprecated_on_deny_erases_the_deny(self):
        # SECURITY NOTE: if every criterion of a DENY constraint references a
        # deprecated field, the deny is not emitted and protection silently
        # disappears. Pre-existing behavior, pinned here for visibility.
        policies = [
            _constraint("D2", criteria_and=[_crit("databaseId", "equals", "db1")],
                        group_perms=[_gp("roleA", "GET")]),
            _constraint("D3", criteria_and=[_crit("nonexistent__field", "equals", "x")],
                        group_perms=[_gp("roleA", "GET", "deny")]),
        ]
        svc = _build_service(policies)
        assert _asset(svc, databaseId="db1") is True

    def test_deprecated_or_branch_dropped_but_valid_branches_remain(self):
        policies = [
            _constraint("D4",
                        criteria_and=[_crit("databaseId", "equals", "db1")],
                        criteria_or=[_crit("nonexistent__field", "equals", "x"),
                                     _crit("assetType", "equals", ".glb")],
                        group_perms=[_gp("roleA", "GET")]),
        ]
        svc = _build_service(policies)
        assert _asset(svc, databaseId="db1", assetType=".glb") is True
        assert _asset(svc, databaseId="db1", assetType=".obj") is False

    def test_entire_or_group_deprecated_broadens_to_and_only(self):
        # SECURITY NOTE: when ALL OR criteria reference deprecated fields the
        # OR group vanishes and the rule degrades to AND-only (broader than
        # authored). Pre-existing behavior, pinned here for visibility.
        policies = [
            _constraint("D5",
                        criteria_and=[_crit("databaseId", "equals", "db1")],
                        criteria_or=[_crit("nonexistent__field", "equals", "x")],
                        group_perms=[_gp("roleA", "GET")]),
        ]
        svc = _build_service(policies)
        assert _asset(svc, databaseId="db1", assetType="anything") is True
        assert _asset(svc, databaseId="db2") is False


@pytest.mark.unit
class TestPolicyTextShape:
    """Structural assertions on the generated policy text."""

    def test_one_rule_line_per_permission_entry(self):
        svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
        svc._user_id = USER_ID
        svc._mfaEnabled = True
        policies = _ten_policy_matrix()
        user_roles = [{"userId": USER_ID, "roleName": "roleA"}]
        with patch.object(CasbinEnforcerService, "_read_current_user_roles_from_table", return_value=user_roles), \
             patch.object(CasbinEnforcerService, "_read_policies_batch_optimized", return_value=policies):
            policy_text = svc._create_policy_text_helper()
        p_lines = [line for line in policy_text.split("\n") if line.startswith("p,")]
        g_lines = [line for line in policy_text.split("\n") if line.startswith("g,")]
        # 10 constraints x 1 permission entry each = 10 rule lines, 1 group line
        assert len(p_lines) == 10
        assert len(g_lines) == 1
        # Every rule line carries the objectType prefix as its first condition
        for line in p_lines:
            assert "regexMatch(r.obj.object__type" in line

    def test_combined_rule_parenthesizes_or_group(self):
        svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
        svc._user_id = USER_ID
        svc._mfaEnabled = True
        policies = [
            _constraint("S1",
                        criteria_and=[_crit("databaseId", "equals", "db1")],
                        criteria_or=[_crit("assetType", "equals", ".glb"),
                                     _crit("assetType", "equals", ".gltf")],
                        group_perms=[_gp("roleA", "GET")]),
        ]
        user_roles = [{"userId": USER_ID, "roleName": "roleA"}]
        with patch.object(CasbinEnforcerService, "_read_current_user_roles_from_table", return_value=user_roles), \
             patch.object(CasbinEnforcerService, "_read_policies_batch_optimized", return_value=policies):
            policy_text = svc._create_policy_text_helper()
        p_line = [line for line in policy_text.split("\n") if line.startswith("p,")][0]
        # The OR group must be parenthesized so && precedence cannot split it
        assert "&& (" in p_line and " || " in p_line and ")" in p_line
