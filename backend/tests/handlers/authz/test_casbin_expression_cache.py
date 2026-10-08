# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compiled-expression cache on the per-user Casbin enforcer (issue #390).

``ExpressionCachingEnforcer`` overrides ``CoreEnforcer._get_expression`` so each distinct inlined
matcher expression is parsed once per enforcer INSTANCE instead of once per policy line per
``enforce()``. These tests pin the properties that make that safe for authorization code:

* Decision parity -- for every cell of (effect x object type x operator x MFA x role count) the
  cached enforcer decides exactly what a plain ``casbin.Enforcer`` decides on the same model and
  policy text, cold and warm, and the matrix realises both outcomes so the comparison is not
  vacuous.
* No cross-user leakage -- two users' services own disjoint cache objects, a shared rule text
  compiles to a distinct object in each, and user B never receives an allow only A's roles grant.
* Freshness -- the cache dies with the service: a constraint edit after the 60 s TTL and an MFA
  flip both rebuild the service with a fresh, empty cache, and the new rule text misses it.
* Cost -- on a 130-line policy the second ``enforce()`` makes zero ``ast.parse`` calls.
* Shape -- keys are the FULL inlined rule strings, the cache is an instance attribute (never
  class- or module-level), and the production factory wires the caching class in.

Everything goes through the REAL policy-text generator and the REAL ``enforce()`` wrapper; only the
two DynamoDB reads are stubbed. Casbin 1.36.0 hook: ``core_enforcer.py`` ``enforce_ex`` L453-L456 ->
``_get_expression`` L538-L544 -> ``util/expression.py`` ``SimpleEval.__init__`` L34 ``ast.parse``.
"""

import threading
from datetime import datetime, timedelta
from unittest.mock import patch

import casbin
import casbin.util.expression as casbin_expression
import pytest
from casbin import FastEnforcer, model
from casbin.persist.adapters import string_adapter

from backend.backend.common.constants import PERMISSION_CONSTRAINT_POLICY
from backend.backend.handlers import authz
from backend.backend.handlers.authz import (
    CasbinEnforcer,
    CasbinEnforcerService,
    ExpressionCachingEnforcer,
)

OBJECT_TYPES = ("asset", "database", "pipeline", "workflow", "api", "web", "tag", "metadataSchema")

# The string-valued field each object type is constrained on in the matrix. ``tags`` (the only
# list-valued field) is valid on ``asset`` alone, so the membership operators run there only.
STRING_FIELD = {
    "asset": "databaseId",
    "database": "databaseId",
    "pipeline": "pipelineId",
    "workflow": "workflowId",
    "api": "route__path",
    "web": "route__path",
    "tag": "tagName",
    "metadataSchema": "metadataSchemaName",
}

# operator -> (constraint value, request value that satisfies the rule, request value that does
# not). For the negating operators "satisfies the rule" means the rule is TRUE, i.e. the value is
# absent; the request value that falsifies it carries the value.
STRING_OPERATORS = {
    "equals": ("target-a", "target-a", "target-b"),
    "contains": ("mid", "x-mid-y", "x-y"),
    "does_not_contain": ("forbid", "clean", "has-forbid-here"),
    "starts_with": ("pre", "pre-x", "x-pre"),
    "ends_with": ("suf", "x-suf", "suf-x"),
}
TAG_OPERATORS = {
    "is_one_of": (["red", "blue"], ["blue", "other"], ["green"]),
    "is_not_one_of": (["red", "blue"], ["green"], ["red"]),
}
ALL_OPERATORS = tuple(STRING_OPERATORS) + tuple(TAG_OPERATORS)

ROLES_5 = ("role0", "role1", "role2", "role3", "role4")
# In a non-MFA session only the roles the roles table marks mfaRequired=False survive the policy
# build. Two of the five require MFA so the MFA-off text genuinely differs from the MFA-on text.
MFA_FREE_ROLES = ("role0", "role1", "role2")

PARSE = casbin_expression.ast.parse


@pytest.fixture(autouse=True)
def _clear_enforcer_caches():
    """The module-level enforcer/policy maps have no per-test reset; keep other modules' entries
    out of these tests and these tests' entries out of the next module."""
    authz.casbin_user_enforcer_map.clear()
    authz.casbin_user_policy_map.clear()
    yield
    authz.casbin_user_enforcer_map.clear()
    authz.casbin_user_policy_map.clear()


# ---------------------------------------------------------------------------------------------
# Builders -- production policy text, production (caching) service, plain-casbin control service
# ---------------------------------------------------------------------------------------------
def _crit(field, operator, value):
    return {"field": field, "operator": operator, "value": value}


def _gp(role, permission="GET", permission_type="allow"):
    return {"groupId": role, "permission": permission, "permissionType": permission_type}


def _constraint(constraint_id, object_type, criteria_and, group_perms, criteria_or=None, user_perms=None):
    return {
        "constraintId": constraint_id,
        "objectType": object_type,
        "criteriaAnd": criteria_and,
        "criteriaOr": criteria_or or [],
        "groupPermissions": group_perms,
        "userPermissions": user_perms or [],
    }


def _patched_reads(user_id, roles, policies, mfa_free_roles=None):
    """Context managers stubbing the two DynamoDB reads the policy builder performs.

    The constraints read is keyed on the role names the builder asks for, as the real GSI query
    is, so a role the MFA filter dropped takes its policy lines with it."""
    user_roles = [{"userId": user_id, "roleName": role} for role in roles]
    mfa_free = [{"roleName": role} for role in (mfa_free_roles if mfa_free_roles is not None else roles)]

    def _policies_for(role_names):
        wanted = set(role_names)
        return [p for p in policies if any(gp["groupId"] in wanted for gp in p.get("groupPermissions", []))]

    return (
        patch.object(CasbinEnforcerService, "_read_current_user_roles_from_table", return_value=user_roles),
        patch.object(CasbinEnforcerService, "_read_policies_batch_optimized", side_effect=_policies_for),
        patch.object(CasbinEnforcerService, "_read_mfaNotRequired_roles_from_table", return_value=mfa_free),
    )


def _policy_text(user_id, roles, policies, mfa_enabled, mfa_free_roles=None):
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = user_id
    svc._mfaEnabled = mfa_enabled
    p1, p2, p3 = _patched_reads(user_id, roles, policies, mfa_free_roles)
    with p1, p2, p3:
        return svc._create_policy_text_helper()


def _bare_service(user_id, mfa_enabled):
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = user_id
    svc._mfaEnabled = mfa_enabled
    svc._model_text = PERMISSION_CONSTRAINT_POLICY
    svc._dateTime_Cached = datetime.now()
    svc._enforcer = None
    return svc


def _production_service(user_id, policy_text, mfa_enabled=True):
    """Enforcer built by the PRODUCTION factory (``_create_casbin_enforcer``) -- the caching one."""
    svc = _bare_service(user_id, mfa_enabled)
    svc._create_casbin_enforcer(policy_text)
    return svc


def _plain_service(user_id, policy_text, mfa_enabled=True):
    """Same wrapper, same model and policy text, but a plain ``casbin.Enforcer`` underneath -- the
    uncached reference every parity cell is compared against."""
    svc = _bare_service(user_id, mfa_enabled)
    new_model = model.Model()
    new_model.load_model_from_text(PERMISSION_CONSTRAINT_POLICY)
    svc._enforcer = casbin.Enforcer(new_model, string_adapter.StringAdapter(policy_text), enable_log=False)
    return svc


def _request(object_type, value):
    """A request object of ``object_type`` carrying ``value`` on the field the matrix constrains.
    For the tag operators ``value`` is the tag list."""
    if isinstance(value, list):
        return {"object__type": object_type, "tags": value}
    return {"object__type": object_type, STRING_FIELD[object_type]: value}


def _public_enforcer(user_id, roles, policies, mfa_enabled, mfa_free_roles=None):
    """``CasbinEnforcer`` through its PUBLIC constructor -- the TTL / MFA invalidation path."""
    p1, p2, p3 = _patched_reads(user_id, roles, policies, mfa_free_roles)
    with p1, p2, p3:
        return CasbinEnforcer({"tokens": [user_id], "roles": [], "mfaEnabled": mfa_enabled})


class _Clock:
    """Stand-in for ``authz.datetime``: ``now()`` returns a controllable real ``datetime``."""

    def __init__(self, start):
        self.value = start

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value = self.value + timedelta(seconds=seconds)


# ---------------------------------------------------------------------------------------------
# Decision-parity matrix
# ---------------------------------------------------------------------------------------------
def _matrix_policies(object_type, operator, effect, roles):
    """The constraints for one matrix cell.

    ``effect == "allow"``: one ALLOW line for the operator under test, so a request that satisfies
    the rule is allowed and one that does not is denied by default.
    ``effect == "deny"``: a baseline ALLOW on the object type (``starts_with ""`` is always true)
    plus one DENY line for the operator under test, so a request that satisfies the rule is denied
    by the explicit deny and one that does not falls through to the baseline allow.

    The line under test is joined by one allow line on EVERY OTHER object type (company lines), so
    the cell is decided in the company of lines that are INDETERMINATE for its request -- the
    object-type gate on each generated line is what keeps them out -- as in a real policy. Lines
    are spread over the user's roles round-robin: the baseline sits on the first role, the line
    under test on the role at the operator's index, company lines on the rest.
    """
    operators = list(STRING_OPERATORS) + list(TAG_OPERATORS)
    policies = []
    if effect == "deny":
        policies.append(
            _constraint(
                f"{object_type}-baseline",
                object_type,
                [_crit(STRING_FIELD[object_type], "starts_with", "")],
                [_gp(roles[0], "GET", "allow")],
            )
        )
    if operator in TAG_OPERATORS:
        criterion = _crit("tags", operator, TAG_OPERATORS[operator][0])
    else:
        criterion = _crit(STRING_FIELD[object_type], operator, STRING_OPERATORS[operator][0])
    policies.append(
        _constraint(
            f"{object_type}-{operator}",
            object_type,
            [criterion],
            [_gp(_line_role(operators, operator, roles), "GET", effect)],
        )
    )
    for index, other in enumerate(t for t in OBJECT_TYPES if t != object_type):
        policies.append(
            _constraint(
                f"company-{other}",
                other,
                [_crit(STRING_FIELD[other], "starts_with", "")],
                [_gp(roles[(index + 1) % len(roles)], "GET", "allow")],
            )
        )
    return policies, operators


def _line_role(operators, operator, roles):
    return roles[operators.index(operator) % len(roles)]


def _matrix_cells():
    """Every (effect, operator, object type, role count, MFA) cell whose operator is valid on the
    object type: the five string operators on all eight types, the two membership operators on
    ``asset`` (``tags`` is a field of asset only -- CONSTRAINT_OBJECT_TYPE_FIELDS)."""
    cells = []
    for effect in ("allow", "deny"):
        for operator in ALL_OPERATORS:
            for object_type in OBJECT_TYPES:
                if operator in TAG_OPERATORS and object_type != "asset":
                    continue
                for role_count in (1, 5):
                    for mfa_enabled in (True, False):
                        mfa = "mfaOn" if mfa_enabled else "mfaOff"
                        cells.append(
                            pytest.param(
                                effect,
                                operator,
                                object_type,
                                role_count,
                                mfa_enabled,
                                id=f"{effect}-{operator}-{object_type}-{role_count}role-{mfa}",
                            )
                        )
    return cells


@pytest.mark.unit
@pytest.mark.parametrize("effect,operator,object_type,role_count,mfa_enabled", _matrix_cells())
def test_decision_parity_matrix(effect, operator, object_type, role_count, mfa_enabled):
    """Cached decision == plain ``casbin.Enforcer`` decision for the satisfying request, the
    non-satisfying request and a PUT (no line grants it), cold and warm -- and the decisions are
    the ones the policy says, so the cell is not vacuously equal."""
    user_id = f"parity-{object_type}-{operator}-{effect}-{role_count}-{mfa_enabled}"
    roles = ROLES_5[:role_count]
    mfa_free = MFA_FREE_ROLES if role_count == 5 else roles
    policies, operators = _matrix_policies(object_type, operator, effect, roles)
    policy_text = _policy_text(user_id, roles, policies, mfa_enabled, mfa_free_roles=mfa_free)

    cached = _production_service(user_id, policy_text, mfa_enabled)
    plain = _plain_service(user_id, policy_text, mfa_enabled)
    assert isinstance(cached._enforcer, ExpressionCachingEnforcer)
    assert type(plain._enforcer) is casbin.Enforcer

    _, satisfying, non_satisfying = (TAG_OPERATORS if operator in TAG_OPERATORS else STRING_OPERATORS)[operator]
    requests = [
        (_request(object_type, satisfying), "GET"),
        (_request(object_type, non_satisfying), "GET"),
        (_request(object_type, satisfying), "PUT"),
    ]
    assert len(requests) == 3

    # Parity, every request, cold then warm.
    for _ in range(2):
        for obj, act in requests:
            assert cached.enforce(dict(obj), act) is plain.enforce(dict(obj), act), (obj, act)
    assert len(cached._enforcer._expression_cache) > 0

    # The decisions the policy prescribes. A line is live only when its role survived the MFA
    # filter; the deny variant's baseline sits on roles[0], which is always MFA-free here.
    active_roles = set(roles) if mfa_enabled else set(mfa_free)
    line_live = _line_role(operators, operator, roles) in active_roles
    satisfied, unsatisfied, put = (plain.enforce(dict(obj), act) for obj, act in requests)
    if effect == "allow":
        assert satisfied is line_live
        assert unsatisfied is False
    else:
        assert satisfied is (not line_live)
        assert unsatisfied is True
    assert put is False


@pytest.mark.unit
def test_tag_membership_truth_table_matches_plain_enforcer():
    """``is_one_of`` / ``is_not_one_of`` on the list-valued ``tags`` field, with an allow and a
    deny line in play at once: the cached enforcer reproduces the plain enforcer's full truth
    table, including the OR over a multi-value criterion and the single negation over the group."""
    user_id = "tags-truth"
    policies = [
        _constraint("allow-any-asset", "asset", [_crit("databaseId", "starts_with", "")], [_gp("roleA")]),
        _constraint(
            "deny-locked",
            "asset",
            [_crit("tags", "is_one_of", ["locked", "quarantine"])],
            [_gp("roleA", "GET", "deny")],
        ),
        _constraint(
            "deny-unless-reviewed",
            "asset",
            [_crit("tags", "is_not_one_of", ["reviewed"])],
            [_gp("roleA", "DELETE", "deny")],
        ),
        _constraint("allow-delete", "asset", [_crit("databaseId", "starts_with", "")], [_gp("roleA", "DELETE")]),
    ]
    text = _policy_text(user_id, ("roleA",), policies, True)
    cached, plain = _production_service(user_id, text), _plain_service(user_id, text)

    table = {
        (("public",), "GET"): True,
        (("locked",), "GET"): False,
        (("x", "quarantine"), "GET"): False,
        ((), "GET"): True,
        (("reviewed",), "DELETE"): True,
        (("public",), "DELETE"): False,
        ((), "DELETE"): False,
    }
    for _ in range(2):
        for (tags, act), expected in table.items():
            obj = {"object__type": "asset", "databaseId": "db1", "tags": list(tags)}
            assert plain.enforce(dict(obj), act) is expected, (tags, act)
            assert cached.enforce(dict(obj), act) is expected, (tags, act)


# ---------------------------------------------------------------------------------------------
# No cross-user leakage
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
def test_two_users_have_disjoint_caches_and_b_never_inherits_a_allow():
    """Users A and B hold different roles. Their policies contain a line with IDENTICAL rule
    text (same field, operator and value -- only the action differs), so a cache shared across
    users would key both onto one compiled object. Through the public constructor, each user's
    service owns its own cache object; the shared text compiles to a distinct object in each;
    and after A warms its cache, B's identical request is still denied."""
    db1_rule = [_crit("databaseId", "equals", "db1")]
    policies_a = [_constraint("a-get-db1", "asset", db1_rule, [_gp("roleA", "GET")])]
    policies_b = [_constraint("b-delete-db1", "asset", db1_rule, [_gp("roleB", "DELETE")])]

    enforcer_a = _public_enforcer("user-a", ("roleA",), policies_a, True)
    enforcer_b = _public_enforcer("user-b", ("roleB",), policies_b, True)
    svc_a, svc_b = enforcer_a.service_object, enforcer_b.service_object
    obj = {"object__type": "asset", "databaseId": "db1"}

    # Warm A, then B asks for the thing only A's role grants.
    assert enforcer_a.enforce(dict(obj), "GET") is True
    assert enforcer_b.enforce(dict(obj), "GET") is False
    # B's own grant works, and does not leak back to A.
    assert enforcer_b.enforce(dict(obj), "DELETE") is True
    assert enforcer_a.enforce(dict(obj), "DELETE") is False
    # A is still itself after B evaluated the same rule text.
    assert enforcer_a.enforce(dict(obj), "GET") is True

    assert svc_a is not svc_b
    assert svc_a._enforcer is not svc_b._enforcer
    cache_a, cache_b = svc_a._enforcer._expression_cache, svc_b._enforcer._expression_cache
    assert cache_a is not cache_b
    shared_keys = set(cache_a) & set(cache_b)
    assert shared_keys, "the two policies were meant to share a rule text"
    for key in shared_keys:
        assert cache_a[key] is not cache_b[key]


@pytest.mark.unit
def test_cache_is_an_instance_attribute_not_class_or_module_state():
    """The compiled expression keeps the enforcer's ``functions`` dict, whose ``g`` is bound to
    THAT enforcer's role manager; the cache must therefore never outlive or outreach its enforcer.
    Pin that it is created in ``__init__`` and that no class or module attribute carries one."""
    assert "_expression_cache" not in vars(ExpressionCachingEnforcer)
    assert "_expression_cache" not in vars(FastEnforcer)
    for name, value in vars(authz).items():
        if isinstance(value, dict) and name not in ("casbin_user_enforcer_map", "casbin_user_policy_map"):
            assert not any(isinstance(v, casbin_expression.SimpleEval) for v in value.values()), name

    policies = [_constraint("c", "asset", [_crit("databaseId", "equals", "db1")], [_gp("roleA")])]
    text = _policy_text("inst", ("roleA",), policies, True)
    first = _production_service("inst", text)._enforcer
    second = _production_service("inst", text)._enforcer
    assert first._expression_cache == {} and second._expression_cache == {}
    assert first._expression_cache is not second._expression_cache


# ---------------------------------------------------------------------------------------------
# Freshness: the cache dies with the service
# ---------------------------------------------------------------------------------------------
def _single_db_policy(database_id):
    return [
        _constraint(f"get-{database_id}", "asset", [_crit("databaseId", "equals", database_id)], [_gp("roleA", "GET")])
    ]


@pytest.mark.unit
def test_constraint_edit_after_ttl_rebuilds_service_and_new_rule_misses_cache():
    """Within the TTL the service (and its cache) is reused and still decides on the OLD
    constraint, exactly as before; past the TTL the service is rebuilt with an EMPTY cache, the
    new rule string is compiled into it, and neither cache holds the other's key."""
    clock = _Clock(datetime(2026, 10, 8, 12, 0, 0))
    db1, db2 = ({"object__type": "asset", "databaseId": d} for d in ("db1", "db2"))

    with patch.object(authz, "datetime", clock):
        first = _public_enforcer("ttl-user", ("roleA",), _single_db_policy("db1"), True)
        assert first.enforce(dict(db1), "GET") is True
        assert first.enforce(dict(db2), "GET") is False
        cache_v1 = first.service_object._enforcer._expression_cache
        keys_v1 = set(cache_v1)
        assert len(keys_v1) == 1

        # Constraint changes in the table, but the TTL has not elapsed: cached service reused.
        clock.advance(authz.CASBIN_REFRESH_POLICY_SECONDS - 1)
        within = _public_enforcer("ttl-user", ("roleA",), _single_db_policy("db2"), True)
        assert within.service_object is first.service_object
        assert within.enforce(dict(db1), "GET") is True
        assert within.enforce(dict(db2), "GET") is False
        assert set(cache_v1) == keys_v1

        # Past the TTL: rebuilt from the table, fresh cache, new rule text.
        clock.advance(2)
        rebuilt = _public_enforcer("ttl-user", ("roleA",), _single_db_policy("db2"), True)
        assert rebuilt.service_object is not first.service_object
        cache_v2 = rebuilt.service_object._enforcer._expression_cache
        assert cache_v2 is not cache_v1
        assert cache_v2 == {}, "a rebuilt service starts with an empty expression cache"
        assert rebuilt.enforce(dict(db2), "GET") is True
        assert rebuilt.enforce(dict(db1), "GET") is False
        keys_v2 = set(cache_v2)
        assert len(keys_v2) == 1
        assert keys_v2.isdisjoint(keys_v1), "the edited rule string must miss the old cache"
        assert set(cache_v1) == keys_v1, "the retired cache was not written to"


@pytest.mark.unit
def test_mfa_flip_rebuilds_service_with_fresh_cache():
    """An MFA state change invalidates the cached service inside the TTL. The user holds roleA
    (requires MFA, grants db1) and roleB (MFA-free, grants db2). The rebuilt service starts with an
    empty cache and its policy reflects the MFA filter: roleA's rule text is compiled only in the
    MFA session's cache, and the non-MFA session never gets db1."""
    clock = _Clock(datetime(2026, 10, 8, 12, 0, 0))
    db1, db2 = ({"object__type": "asset", "databaseId": d} for d in ("db1", "db2"))
    policies = [
        _constraint("a-db1", "asset", [_crit("databaseId", "equals", "db1")], [_gp("roleA", "GET")]),
        _constraint("b-db2", "asset", [_crit("databaseId", "equals", "db2")], [_gp("roleB", "GET")]),
    ]
    roles = ("roleA", "roleB")

    with patch.object(authz, "datetime", clock):
        with_mfa = _public_enforcer("mfa-user", roles, policies, True, mfa_free_roles=("roleB",))
        assert with_mfa.enforce(dict(db1), "GET") is True
        assert with_mfa.enforce(dict(db2), "GET") is True
        cache_mfa = with_mfa.service_object._enforcer._expression_cache
        assert len(cache_mfa) == 2
        (db1_key,) = [k for k in cache_mfa if "'^db1" in k]

        clock.advance(5)  # well inside the TTL: only the MFA flip can invalidate
        without_mfa = _public_enforcer("mfa-user", roles, policies, False, mfa_free_roles=("roleB",))
        assert without_mfa.service_object is not with_mfa.service_object
        cache_no_mfa = without_mfa.service_object._enforcer._expression_cache
        assert cache_no_mfa is not cache_mfa
        assert cache_no_mfa == {}, "a rebuilt service starts with an empty expression cache"
        assert without_mfa.enforce(dict(db1), "GET") is False
        assert without_mfa.enforce(dict(db2), "GET") is True
        assert len(cache_no_mfa) == 1
        assert db1_key not in cache_no_mfa, "the MFA-required role's rule never reached this cache"
        assert len(cache_mfa) == 2, "the retired cache was not written to"

        # And back: flipping MFA on again rebuilds once more, with a fresh cache, and db1 returns.
        clock.advance(5)
        again = _public_enforcer("mfa-user", roles, policies, True, mfa_free_roles=("roleB",))
        assert again.service_object is not without_mfa.service_object
        assert again.service_object._enforcer._expression_cache == {}
        assert again.enforce(dict(db1), "GET") is True
        assert again.enforce(dict(db2), "GET") is True


# ---------------------------------------------------------------------------------------------
# Cost: ast.parse call count on a 130-line policy
# ---------------------------------------------------------------------------------------------
def _hundred_thirty_line_policies():
    """130 constraints spread over roles, object types, operators and both effects; every rule
    text is distinct (the value carries the index) so the first full walk parses 130 times."""
    operators = list(STRING_OPERATORS)
    policies = []
    for n in range(130):
        object_type = OBJECT_TYPES[n % len(OBJECT_TYPES)]
        op = operators[n % len(operators)]
        criteria = [_crit(STRING_FIELD[object_type], op, f"v{n}")]
        if object_type == "asset" and n % 3 == 0:
            criteria.append(_crit("tags", "is_one_of" if n % 2 else "is_not_one_of", [f"t{n}", f"t{n + 1}"]))
        effect = "deny" if n % 7 == 6 else "allow"
        policies.append(
            _constraint(
                f"C{n}", object_type, criteria, [_gp(ROLES_5[n % 5], ["GET", "PUT", "POST", "DELETE"][n % 4], effect)]
            )
        )
    return policies


@pytest.mark.unit
def test_second_enforce_on_130_line_policy_makes_zero_ast_parse_calls():
    """First ``enforce()`` that walks every line parses each distinct rule text once; the second
    ``enforce()`` -- same request or a different one -- parses nothing. Patches the exact symbol
    casbin 1.36.0 calls: ``casbin.util.expression.ast.parse`` (``util/expression.py`` L34)."""
    user_id = "perf-user"
    text = _policy_text(user_id, ROLES_5, _hundred_thirty_line_policies(), True)
    p_lines = [line for line in text.splitlines() if line.startswith("p,")]
    assert len(p_lines) == 130
    svc = _production_service(user_id, text)

    # A request no line can match walks all 130 lines (no deny short-circuit).
    walker = {"object__type": "nonesuch"}
    with patch.object(casbin_expression.ast, "parse", side_effect=PARSE) as spy:
        assert svc.enforce(dict(walker), "GET") is False
    assert spy.call_count == 130
    assert len(svc._enforcer._expression_cache) == 130

    with patch.object(casbin_expression.ast, "parse", side_effect=PARSE) as spy:
        assert svc.enforce(dict(walker), "GET") is False
    assert spy.call_count == 0

    with patch.object(casbin_expression.ast, "parse", side_effect=PARSE) as spy:
        svc.enforce({"object__type": "asset", "databaseId": "v0", "tags": ["t1"]}, "GET")
        svc.enforce({"object__type": "pipeline", "pipelineId": "v2"}, "POST")
        svc.enforce({"object__type": "api", "route__path": "/anything"}, "DELETE")
    assert spy.call_count == 0

    # Parity with the plain enforcer on the same 130 lines, for a spread of requests.
    plain = _plain_service(user_id, text)
    for n in range(0, 130, 7):
        object_type = OBJECT_TYPES[n % len(OBJECT_TYPES)]
        for act in ("GET", "PUT", "POST", "DELETE"):
            obj = _request(object_type, f"v{n}")
            assert svc.enforce(dict(obj), act) is plain.enforce(dict(obj), act), (n, act)


@pytest.mark.unit
def test_uncached_enforcer_parses_every_line_on_every_enforce():
    """Control for the test above: the plain enforcer on the same text parses all 130 lines on
    the SECOND request too. If this ever passes with zero, the spy is not watching the hook."""
    text = _policy_text("control", ROLES_5, _hundred_thirty_line_policies(), True)
    plain = _plain_service("control", text)
    plain.enforce({"object__type": "nonesuch"}, "GET")
    with patch.object(casbin_expression.ast, "parse", side_effect=PARSE) as spy:
        plain.enforce({"object__type": "nonesuch"}, "GET")
    assert spy.call_count == 130


# ---------------------------------------------------------------------------------------------
# Shape: keys, factory wiring, serialization
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
def test_cache_keys_are_full_inlined_rule_strings():
    """Each key is the whole matcher with the line's rule inlined, exactly as ``enforce_ex`` hands
    it to ``_get_expression`` (casbin has already rewritten ``r.obj`` to ``r_obj``; the ``&&`` ->
    ``and`` rewrite happens inside ``_get_expression`` and is not part of the key), never an index.
    Two lines that differ only in their rule VALUE are two keys; two lines with the same rule text
    are one key."""
    policies = [
        _constraint("c1", "asset", [_crit("databaseId", "equals", "db1")], [_gp("roleA", "GET")]),
        _constraint("c2", "asset", [_crit("databaseId", "equals", "db2")], [_gp("roleA", "GET")]),
        _constraint("c3", "asset", [_crit("databaseId", "equals", "db1")], [_gp("roleA", "PUT")]),
    ]
    text = _policy_text("keys", ("roleA",), policies, True)
    svc = _production_service("keys", text)
    svc.enforce({"object__type": "nonesuch"}, "GET")  # walks all three lines

    keys = sorted(svc._enforcer._expression_cache)
    assert len(keys) == 2
    for key in keys:
        assert isinstance(key, str)
        assert key.startswith("g(r_sub, p_sub) && (")
        assert key.endswith(") && r_act == p_act")
        assert "regexMatch(r_obj.object__type, '^asset" in key
        assert "regexMatch(r_obj.databaseId, '^db" in key
    assert any("'^db1" in key for key in keys) and any("'^db2" in key for key in keys)
    for value in svc._enforcer._expression_cache.values():
        assert isinstance(value, casbin_expression.SimpleEval)


@pytest.mark.unit
def test_production_factory_and_deny_all_fallback_use_the_caching_enforcer():
    svc = _bare_service("factory", True)
    svc._create_casbin_enforcer("p, 'role::r', (regexMatch(r.obj.object__type, '^asset\\\\Z')), GET, allow")
    assert type(svc._enforcer) is ExpressionCachingEnforcer

    broken = _bare_service("broken", True)
    broken._create_casbin_enforcer("p, this, line, has, far, too, many, fields")
    assert type(broken._enforcer) is ExpressionCachingEnforcer
    assert broken.enforce({"object__type": "asset", "databaseId": "db1"}, "GET") is False


@pytest.mark.unit
def test_cache_hit_rebinds_functions_when_handed_a_different_dict():
    """A compiled expression evaluates ``g`` from its ``functions`` dict. pycasbin 1.36.0 passes
    the enforcer's own dict each call, so a hit normally sees the current ``g`` through identity;
    if a caller hands a DIFFERENT dict the hit must adopt it rather than keep the stale one."""
    text = _policy_text("rebind", ("roleA",), _single_db_policy("db1"), True)
    enforcer = _production_service("rebind", text)._enforcer
    original_functions = enforcer.fm.get_functions()
    expression = enforcer._get_expression("1 == 1", original_functions)
    assert expression.functions is original_functions

    replacement = dict(original_functions)
    assert enforcer._get_expression("1 == 1", replacement) is expression
    assert expression.functions is replacement
    # Same dict again: no-op, still bound.
    assert enforcer._get_expression("1 == 1", replacement).functions is replacement


@pytest.mark.unit
def test_concurrent_enforce_on_one_service_stays_correct():
    """A cached ``SimpleEval`` holds the request's parameters on itself during evaluation, so the
    enforcer serializes ``enforce_ex``. Eight threads hammer one service with requests whose
    answers are known; every answer must be right."""
    policies = [
        _constraint("allow-db1", "asset", [_crit("databaseId", "equals", "db1")], [_gp("roleA", "GET")]),
        _constraint("deny-locked", "asset", [_crit("tags", "is_one_of", ["locked"])], [_gp("roleA", "GET", "deny")]),
    ]
    text = _policy_text("threads", ("roleA",), policies, True)
    svc = _production_service("threads", text)
    cases = [
        ({"object__type": "asset", "databaseId": "db1", "tags": []}, "GET", True),
        ({"object__type": "asset", "databaseId": "db1", "tags": ["locked"]}, "GET", False),
        ({"object__type": "asset", "databaseId": "db2", "tags": []}, "GET", False),
        ({"object__type": "asset", "databaseId": "db1", "tags": []}, "PUT", False),
    ]
    failures = []

    def worker(offset):
        for i in range(150):
            obj, act, expected = cases[(i + offset) % len(cases)]
            if svc.enforce(dict(obj), act) is not expected:
                failures.append((offset, i, obj, act))

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert failures == []
    assert len(svc._enforcer._expression_cache) == 2
