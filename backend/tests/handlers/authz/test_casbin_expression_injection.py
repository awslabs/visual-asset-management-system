# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for Casbin authorization expression injection.

These tests exercise the REAL policy-string generation (_generate_criteria_object_rules)
and the REAL Casbin matcher (eval(p.obj_rule) over a simpleeval sandbox), with no mocking
of the enforcer. They lock in the fix for the constraint-value expression injection where a
crafted criterion value could break out of its string literal and force the matcher to
evaluate to True for every resource.

Context: constraint creation is an admin-gated configuration action (Tier-1 API auth only),
so this was never a non-privileged escalation path. The escaping here is defense-in-depth so
that "value is data, not code" is actually enforced, and so the eval()'d matcher cannot be
steered by constraint authors.
"""

import pytest
from casbin import FastEnforcer, model
from casbin.persist.adapters import string_adapter

from backend.backend.handlers.authz import CasbinEnforcerService
from backend.backend.common.constants import (
    PERMISSION_CONSTRAINT_POLICY,
    PERMISSION_CONSTRAINT_FIELDS,
)


# Injection payloads that, before the fix, broke out of the value's string literal.
# Keyed by the operator they target. Each must NOT grant access to an arbitrary resource.
INJECTION_PAYLOADS = {
    "is_one_of": "x' or True or 'x",
    "is_not_one_of": "x' or True or 'x",
    "equals": ".') or True or regexMatch(r.obj.databaseId, '.",
    "contains": ".') or True or regexMatch(r.obj.databaseId, '.",
    "starts_with": ".') or True or regexMatch(r.obj.databaseId, '.",
    "ends_with": ".') or True or regexMatch(r.obj.databaseId, '.",
}


def _build_enforcer_from_obj_rule(obj_rule):
    """Build a real Casbin enforcer for a single allow policy using the production model."""
    policy_text = (
        "g, user::attacker, 'role::r'\n"
        f"p, 'role::r', {obj_rule}, GET, allow"
    )
    new_model = model.Model()
    new_model.load_model_from_text(PERMISSION_CONSTRAINT_POLICY)
    adapter = string_adapter.StringAdapter(policy_text)
    return FastEnforcer(model=new_model, adapter=adapter, enable_log=False)


def _enforce(enforcer, database_id):
    obj = PERMISSION_CONSTRAINT_FIELDS.copy()
    obj["databaseId"] = database_id
    try:
        return enforcer.enforce("user::attacker", obj, "GET")
    except Exception:
        # A payload that produces an invalid expression / regex fails closed (deny).
        return False


def _generate_rules(criteria):
    """Call the real rule generator without running CasbinEnforcerService.__init__
    (which would require DynamoDB). The method is pure and only uses constants.
    """
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    return svc._generate_criteria_object_rules(criteria)


def _single_rule(field, operator, value):
    rules = _generate_rules([{"field": field, "operator": operator, "value": value}])
    assert len(rules) == 1, f"expected one rule, got {rules}"
    return rules[0]


@pytest.mark.unit
class TestCasbinExpressionInjection:
    """The injection vectors from the security report must not bypass authorization."""

    @pytest.mark.parametrize("operator,payload", list(INJECTION_PAYLOADS.items()))
    def test_injection_payload_does_not_grant_arbitrary_access(self, operator, payload):
        rule = _single_rule("databaseId", operator, payload)
        enforcer = _build_enforcer_from_obj_rule(rule)

        # The attacker crafted the value hoping the matcher evaluates True for everything.
        # With escaping, an unrelated resource must be denied.
        granted = _enforce(enforcer, "some-unrelated-database-the-attacker-should-not-see")

        # is_not_one_of is a negation: 'x' in field is False, so !False == True is EXPECTED
        # for a genuinely-unmatched resource. The security property we assert there is that
        # the payload is treated as a literal value (see the dedicated test below), not that
        # the rule denies. For all positive operators, arbitrary access must be denied.
        if operator != "is_not_one_of":
            assert granted is False, (
                f"Injection via '{operator}' granted access to an arbitrary resource "
                f"(payload={payload!r}, rule={rule!r})"
            )

    def test_is_one_of_injection_is_literal_not_boolean(self):
        """'x' or True or 'x' must be treated as a single literal membership test."""
        rule = _single_rule("databaseId", "is_one_of", "x' or True or 'x")
        enforcer = _build_enforcer_from_obj_rule(rule)
        # No databaseId value can contain the literal injected string as a member
        # of a (string) field unless it literally equals it, so arbitrary access is denied.
        assert _enforce(enforcer, "anything") is False
        assert _enforce(enforcer, "secret-db-999") is False

    def test_is_not_one_of_injection_treated_as_literal(self):
        """The injected payload must be inert. With escaping, the rule reduces to
        `!('<literal>' in r.obj.databaseId)`. The injected booleans must not appear as
        standalone expression terms (which previously forced a constant result)."""
        rule = _single_rule("databaseId", "is_not_one_of", "x' or True or 'x")
        # The generated rule must contain the escaped literal, not a bare `or True or`
        # operating at expression scope.
        assert "\\'" in rule, f"value was not escaped in rule: {rule!r}"

    def test_equals_regex_breakout_is_neutralized(self):
        rule = _single_rule(
            "databaseId", "equals", ".') or True or regexMatch(r.obj.databaseId, '."
        )
        enforcer = _build_enforcer_from_obj_rule(rule)
        assert _enforce(enforcer, "any-database") is False


@pytest.mark.unit
class TestLegitimateConstraintsStillWork:
    """The fix must not break legitimate constraint semantics, including admin wildcards."""

    def test_equals_exact_match(self):
        rule = _single_rule("databaseId", "equals", "secret-db")
        enforcer = _build_enforcer_from_obj_rule(rule)
        assert _enforce(enforcer, "secret-db") is True
        assert _enforce(enforcer, "other-db") is False

    def test_contains_wildcard_allows_all_admin_default(self):
        # This is exactly what the seeded admin "allow all databases" constraint uses:
        # operator=contains, value=".*"
        rule = _single_rule("databaseId", "contains", ".*")
        enforcer = _build_enforcer_from_obj_rule(rule)
        assert _enforce(enforcer, "any-database-at-all") is True
        assert _enforce(enforcer, "another-one") is True

    def test_starts_with(self):
        rule = _single_rule("databaseId", "starts_with", "proj-")
        enforcer = _build_enforcer_from_obj_rule(rule)
        assert _enforce(enforcer, "proj-alpha") is True
        assert _enforce(enforcer, "other-alpha") is False

    def test_ends_with(self):
        rule = _single_rule("databaseId", "ends_with", "-prod")
        enforcer = _build_enforcer_from_obj_rule(rule)
        assert _enforce(enforcer, "alpha-prod") is True
        assert _enforce(enforcer, "alpha-dev") is False

    def test_does_not_contain(self):
        rule = _single_rule("databaseId", "does_not_contain", "secret")
        enforcer = _build_enforcer_from_obj_rule(rule)
        assert _enforce(enforcer, "public-data") is True
        assert _enforce(enforcer, "secret-data") is False
