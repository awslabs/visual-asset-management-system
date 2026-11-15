# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A constraint criterion value is compared exactly as written, whatever characters it contains.

Before pycasbin compiles a policy line it rewrites the TEXT of the line's rule, string literals
included:

* ``casbin.util.escape_assertion`` rewrites every ``p.`` / ``p<digits>.`` and ``r.`` that starts at a
  word boundary to ``p_`` / ``r_`` (it exists to turn ``r.obj`` into the ``r_obj`` name the evaluator
  binds), and
* ``CoreEnforcer._get_expression`` replaces every ``&&``, ``||`` and ``!`` with ``and``, ``or`` and
  ``not``.

So a value has to reach the compiled expression in a form those rewrites cannot touch. The tests
here drive the production policy builder (``_create_policy_text_helper``) and enforcer
(``_create_casbin_enforcer`` / ``CasbinEnforcerService.enforce``, and ``CasbinEnforcer.enforceAPI``
for ``api`` routes) with values such rewrites would alter -- ``p.`` / ``r.`` leading, after a space,
after ``-`` or ``.``, inside a regex -- and with ``!``, ``&&``, ``||``, for every operator, as an allow
line and as a deny line, for an entity check and for ``api`` and ``web`` route checks. Every decision
is compared with what the RAW value means, computed directly in Python (``criteria_oracle``).
"""

from datetime import datetime
from unittest.mock import patch

import pytest

from backend.backend.handlers import authz
from backend.backend.handlers.authz import CasbinEnforcer, CasbinEnforcerService
from backend.backend.common.constants import PERMISSION_CONSTRAINT_POLICY
from tests.handlers.authz.criteria_oracle import (
    MEMBERSHIP_OPERATORS,
    OPERATORS,
    criterion_holds,
    rewritten_form,
)

USER_ID = "as-written-user"
ROLE = "roleA"

# Values whose text the two rewrites above would alter.
REWRITTEN_VALUES = (
    "p.glb",            # leading p.
    "r.glb",            # leading r.
    "scan p.v2",        # after a space
    "x-r.1",            # after '-'
    "p2.x",             # p<digits>.
    "model.p.glb",      # after '.'
    "r.*",              # inside a regex
    "(x|r.)y",          # inside a regex alternative
    "[a-z]+-p.[0-9]",   # inside a regex
    "a!b",
    "(?!secret).*",     # look-ahead
    "a&&b",
    "a||b",
    "x != y",
)

# Look-alikes: a word character before the 'p' / 'r', no dot after it, a single '&' or '|'. ('p',
# 'x-p' and 'r' meet the '.*' of starts_with, which the rewrite turns into '_*' -- a pattern that
# matches the same strings, so their decisions never changed.)
UNREWRITTEN_VALUES = ("model.glb", "snap.glb", "pr.x", "p_glb", "p", "x-p", "r", "a|b", "a&b", ".*")

# Extra entities for the regex-shaped values, so each pattern is also seen matching.
REGEX_SAMPLES = {
    "r.*": ["rx", "r", "ar"],
    "(x|r.)y": ["xy", "rzy", "r_y"],
    "[a-z]+-p.[0-9]": ["ab-pq1", "ab-p_1"],
    "(?!secret).*": ["public", "secret-x"],
    "a||b": ["zzz"],
    ".*": ["anything"],
}

ROUTE_PREFIX = "/x/"
ROUTE_VALUES = tuple(ROUTE_PREFIX + v for v in (
    "p.glb", "x-r.1", "scan p.v2", "r.*", "a!b", "(?!private).*", "a||b", "model.glb", "pr.x",
)) + ("/assets", "/database/db1/assets")


def scalar_entities(value):
    candidates = [value, rewritten_form(value), "pre " + value, value + "-tail", "", "unrelated"]
    candidates.extend(REGEX_SAMPLES.get(value, []))
    return list(dict.fromkeys(candidates))


def route_entities(value):
    tail = value[len(ROUTE_PREFIX):] if value.startswith(ROUTE_PREFIX) else value
    candidates = [value, rewritten_form(value), value + "/tail", "/other" + value, "/", "/unrelated"]
    candidates.extend(ROUTE_PREFIX + sample for sample in REGEX_SAMPLES.get(tail, []))
    return list(dict.fromkeys(candidates))


def membership_entities(value):
    return [[value], [rewritten_form(value)], [], ["unrelated", value], ["unrelated"]]


def value_shapes(value):
    """Scalar, one-element list and two-element list: the generator's three clause branches."""
    return (value, [value], [value, "zz-other"])


@pytest.fixture(autouse=True)
def _clean_caches():
    authz.casbin_user_enforcer_map.clear()
    authz.casbin_user_policy_map.clear()
    yield
    authz.casbin_user_enforcer_map.clear()
    authz.casbin_user_policy_map.clear()


def constraint(constraint_id, object_type, criteria_and, permission_type="allow", permission="GET"):
    return {
        "constraintId": constraint_id,
        "objectType": object_type,
        "criteriaAnd": criteria_and,
        "criteriaOr": [],
        "groupPermissions": [{"groupId": ROLE, "permission": permission,
                              "permissionType": permission_type}],
        "userPermissions": [],
    }


def production_service(policies, user_id=USER_ID, roles=(ROLE,), mfa_enabled=True):
    """A service whose policy text and enforcer come from the production builder and factory."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = user_id
    svc._mfaEnabled = mfa_enabled
    svc._model_text = PERMISSION_CONSTRAINT_POLICY
    svc._dateTime_Cached = datetime.now()
    svc._enforcer = None
    user_roles = [{"userId": user_id, "roleName": role} for role in roles]
    with patch.object(CasbinEnforcerService, "_read_current_user_roles_from_table",
                      return_value=user_roles), \
            patch.object(CasbinEnforcerService, "_read_policies_batch_optimized",
                         return_value=policies):
        policy_text = svc._create_policy_text_helper()
    svc._create_casbin_enforcer(policy_text)
    return svc


def api_route_allowed(svc, path, method="GET"):
    """Tier-1 route check exactly as a handler runs it: ``CasbinEnforcer.enforceAPI(event)``."""
    enforcer = CasbinEnforcer.__new__(CasbinEnforcer)
    enforcer.service_object = svc
    enforcer.claims_and_roles = {"tokens": [USER_ID], "roles": [ROLE], "mfaEnabled": True}
    event = {"requestContext": {"http": {"method": method, "path": path}}}
    return enforcer.enforceAPI(event)


def web_route_allowed(svc, path, method="GET"):
    """The ``/auth/routes`` check: one ``web`` object per route."""
    return svc.enforce({"method": method, "route__path": path, "object__type": "web"}, method)


# One check per kind: (object type, field, entity generator, decide(svc, entity), allow-all criterion).
CHECKS = {
    "asset": dict(
        object_type="asset",
        field=lambda operator: "tags" if operator in MEMBERSHIP_OPERATORS else "assetName",
        entities=lambda operator, value: (
            membership_entities(value) if operator in MEMBERSHIP_OPERATORS else scalar_entities(value)),
        decide=lambda svc, field, entity: svc.enforce(
            {"object__type": "asset", "databaseId": "db1", field: entity}, "GET"),
        allow_all={"field": "databaseId", "operator": "contains", "value": ".*"},
    ),
    "api": dict(
        object_type="api",
        field=lambda operator: "route__path",
        entities=lambda operator, value: route_entities(value),
        decide=lambda svc, field, entity: api_route_allowed(svc, entity),
        allow_all={"field": "route__path", "operator": "contains", "value": ".*"},
    ),
    "web": dict(
        object_type="web",
        field=lambda operator: "route__path",
        entities=lambda operator, value: route_entities(value),
        decide=lambda svc, field, entity: web_route_allowed(svc, entity),
        allow_all={"field": "route__path", "operator": "contains", "value": ".*"},
    ),
}


def decision_mismatches(check_name, direction, operator, value):
    """Every (shape, entity) whose production decision differs from the raw-value meaning."""
    check = CHECKS[check_name]
    field = check["field"](operator)
    mismatches = []
    for shape in value_shapes(value):
        criterion = {"field": field, "operator": operator, "value": shape}
        if direction == "allow":
            policies = [constraint("c-allow", check["object_type"], [criterion])]
        else:
            policies = [constraint("c-all", check["object_type"], [check["allow_all"]]),
                        constraint("c-deny", check["object_type"], [criterion], permission_type="deny")]
        svc = production_service(policies)
        values = shape if isinstance(shape, list) else [shape]
        for entity in check["entities"](operator, value):
            holds = criterion_holds(operator, values, entity)
            expected = (holds is True) if direction == "allow" else (holds is False)
            got = check["decide"](svc, field, entity)
            if got is not expected:
                mismatches.append(f"value={shape!r} entity={entity!r}: decided {got}, expected {expected}")
    return mismatches


@pytest.mark.unit
class TestRewrittenValuesMatchAsWritten:
    """Values the rewrites would alter: allow and deny lines decide by the value as written."""

    @pytest.mark.parametrize("direction", ("allow", "deny"))
    @pytest.mark.parametrize("operator", OPERATORS)
    @pytest.mark.parametrize("value", REWRITTEN_VALUES)
    def test_entity_check(self, direction, operator, value):
        mismatches = decision_mismatches("asset", direction, operator, value)
        assert not mismatches, "\n".join(mismatches)

    @pytest.mark.parametrize("direction", ("allow", "deny"))
    @pytest.mark.parametrize("operator", OPERATORS)
    @pytest.mark.parametrize("value", ROUTE_VALUES)
    def test_api_route_check(self, direction, operator, value):
        mismatches = decision_mismatches("api", direction, operator, value)
        assert not mismatches, "\n".join(mismatches)

    @pytest.mark.parametrize("direction", ("allow", "deny"))
    @pytest.mark.parametrize("operator", OPERATORS)
    @pytest.mark.parametrize("value", ROUTE_VALUES)
    def test_web_route_check(self, direction, operator, value):
        mismatches = decision_mismatches("web", direction, operator, value)
        assert not mismatches, "\n".join(mismatches)


@pytest.mark.unit
class TestLookAlikeValuesMatchAsWritten:
    """CONTROL: values the rewrites never altered decide the same way, as written."""

    @pytest.mark.parametrize("direction", ("allow", "deny"))
    @pytest.mark.parametrize("operator", OPERATORS)
    @pytest.mark.parametrize("value", UNREWRITTEN_VALUES)
    def test_entity_check(self, direction, operator, value):
        mismatches = decision_mismatches("asset", direction, operator, value)
        assert not mismatches, "\n".join(mismatches)


@pytest.mark.unit
class TestTheChecksAreNotVacuous:
    """The comparisons above must see both outcomes, or agreeing with the oracle proves nothing."""

    def test_the_oracle_sees_matching_and_non_matching_entities(self):
        """Per value across the operators, and per operator across the values (a value such as
        ``(?!secret).*`` matches every entity under ``contains``, so per-pair is too strict)."""
        by_operator = {operator: set() for operator in OPERATORS}
        for value in REWRITTEN_VALUES + UNREWRITTEN_VALUES:
            outcomes = set()
            for operator in OPERATORS:
                entities = (membership_entities(value) if operator in MEMBERSHIP_OPERATORS
                            else scalar_entities(value))
                seen = {criterion_holds(operator, [value], entity) for entity in entities}
                outcomes |= seen
                by_operator[operator] |= seen
            assert {True, False} <= outcomes, (value, outcomes)
        for operator, outcomes in by_operator.items():
            assert {True, False} <= outcomes, (operator, outcomes)

    def test_a_value_as_written_is_granted_by_its_equals_allow_line(self):
        svc = production_service([constraint(
            "c", "asset", [{"field": "assetName", "operator": "equals", "value": "p.glb"}])])
        assert svc.enforce({"object__type": "asset", "assetName": "p.glb"}, "GET") is True
        assert svc.enforce({"object__type": "asset", "assetName": "model.glb"}, "GET") is False

    def test_a_value_as_written_is_refused_by_its_equals_deny_line(self):
        svc = production_service([
            constraint("all", "asset", [{"field": "databaseId", "operator": "contains", "value": ".*"}]),
            constraint("d", "asset", [{"field": "assetName", "operator": "equals", "value": "r.glb"}],
                       permission_type="deny"),
        ])
        assert svc.enforce({"object__type": "asset", "databaseId": "db1", "assetName": "r.glb"},
                           "GET") is False
        assert svc.enforce({"object__type": "asset", "databaseId": "db1", "assetName": "other"},
                           "GET") is True

    def test_an_api_route_deny_line_applies_to_its_route(self):
        svc = production_service([
            constraint("all", "api", [{"field": "route__path", "operator": "contains", "value": ".*"}]),
            constraint("d", "api", [{"field": "route__path", "operator": "starts_with",
                                     "value": "/database/db1/assets/x-r.1"}], permission_type="deny"),
        ])
        assert api_route_allowed(svc, "/database/db1/assets/x-r.1/download") is False
        assert api_route_allowed(svc, "/database/db1/assets/other/download") is True
