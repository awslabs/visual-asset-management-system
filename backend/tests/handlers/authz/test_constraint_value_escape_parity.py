# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Writing every character of a criterion value as a source escape changes no other decision.

``CasbinEnforcerService._escape_rule_value`` writes each character of a criterion value as its
``\\xNN`` / ``\\uNNNN`` / ``\\UNNNNNNNN`` source escape. The reference is the escaper it replaced
(``legacy_escape_rule_value``, verbatim: backslash and quote escaped, control characters and ``,()[]``
as ``\\xNN``, every other character as it is), patched into the SAME production policy builder
together with the object-type clause it produced. The constraint sets:

* every shipped template in ``documentation/permissionsTemplates/``, through the import's own
  variable substitution and constraint conversion;
* the seeded ``admin`` and ``basicReadOnly`` constraints, read from the ``dynamodb-authdefaults-*``
  constructs;
* synthetic sets: allow and deny lines on several object types, every operator, scalar and list
  values, OR groups, user-level grants, two roles with and without MFA, and all of the above held by
  one user at once.

Three checks per set:

1. Compiled expression. For every policy line, the expression ``enforce_ex`` compiles from it
   (``escape_assertion`` + ``replace_eval``, then ``_get_expression``'s ``&&`` / ``||`` / ``!``
   rewrite and parse) has the same syntax tree under both builders, and its string constants are
   exactly the patterns the line's raw values mean. One tree evaluates one way for every request, so
   this covers requests the sweep below never sends.
2. Decisions. A request sweep -- every named type plus one no line names, four actions, field values
   drawn from the set's own criteria values and variations of them -- gets the same decision from both
   services, and the decision ``criteria_oracle.policy_decision`` computes from the raw constraints.
3. Not vacuous. Each set's sweep is granted and refused, and in the sets with deny lines some request
   is refused only because of one.

For a set whose values pycasbin's rule-text rewrites alter, the trees differ on exactly the lines
carrying such a value, the new constants are still the raw-value patterns, and the new decisions are
the oracle's while the reference's are not.
"""

import ast
import copy
import itertools
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import patch

import pytest
from casbin import FastEnforcer, model, util
from casbin.persist.adapters import string_adapter

from backend.backend.handlers import authz
from backend.backend.handlers.authz import CasbinEnforcerService
from backend.backend.common.constants import (
    PERMISSION_CONSTRAINT_POLICY,
    get_constraint_fields_for_object_type,
)
from tests.handlers.authz import shipped_constraints
from tests.handlers.authz.criteria_oracle import (
    MEMBERSHIP_OPERATORS,
    criterion_values,
    intended_pattern,
    policy_decision,
    rewritten_form,
    usable_criteria,
)

USER_ID = "parity-user"
ACTIONS = ("GET", "PUT", "POST", "DELETE")
TEMPLATE_VARIABLES = {"DATABASE_ID": "tpl-db", "TAG_VALUE": "locked"}


def legacy_escape_rule_value(value):
    """The escaper ``_escape_rule_value`` replaced, verbatim."""
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    contained = {code: "\\x%02x" % code for code in range(32)}
    contained.update({ord(character): "\\x%02x" % ord(character) for character in ",()[]"})
    return escaped.translate(contained)


def legacy_object_type_rule(self, object_type):
    """The object-type clause the builder produced with that escaper."""
    return self._generate_criteria_object_rules(
        [{"field": "object__type", "operator": "equals", "value": object_type}])[0]


@pytest.fixture(autouse=True)
def _clean_caches():
    authz.casbin_user_enforcer_map.clear()
    authz.casbin_user_policy_map.clear()
    with patch.object(authz, "log_authorization"):
        yield
    authz.casbin_user_enforcer_map.clear()
    authz.casbin_user_policy_map.clear()


# ---------------------------------------------------------------------------------------------
# Constraint sets
# ---------------------------------------------------------------------------------------------
def crit(field, operator, value):
    return {"field": field, "operator": operator, "value": value}


def gp(role, permission, permission_type="allow"):
    return {"groupId": role, "permission": permission, "permissionType": permission_type}


def up(user, permission, permission_type="allow"):
    return {"userId": user, "permission": permission, "permissionType": permission_type}


def constraint(constraint_id, object_type, criteria_and=(), criteria_or=(), group=(), users=()):
    return {"constraintId": constraint_id, "objectType": object_type, "criteriaAnd": list(criteria_and),
            "criteriaOr": list(criteria_or), "groupPermissions": list(group), "userPermissions": list(users)}


def synthetic_constraints():
    """Allow and deny lines over several types, every operator, two roles and user grants. No value
    contains text pycasbin's rule-text rewrites act on."""
    return [
        constraint("a-allow", "asset", [crit("databaseId", "equals", "db1")],
                   group=[gp("roleA", a) for a in ACTIONS]),
        constraint("a-deny-tag", "asset", [crit("tags", "is_one_of", ["locked", "quarantine"])],
                   group=[gp("roleA", "GET", "deny"), gp("roleB", "DELETE", "deny")]),
        constraint("a-deny-name", "asset", [crit("assetName", "starts_with", "secret")],
                   group=[gp("roleB", a, "deny") for a in ("PUT", "POST")]),
        constraint("a-allow-type", "asset", [crit("assetType", "ends_with", ".glb")],
                   [crit("databaseId", "contains", "db"), crit("tags", "is_not_one_of", ["draft"])],
                   group=[gp("roleB", "GET"), gp("roleB", "PUT")]),
        constraint("a-deny-public", "asset", [crit("assetName", "does_not_contain", "public")],
                   group=[gp("roleA", "PUT", "deny")]),
        constraint("a-allow-alt", "asset", [crit("assetName", "contains", "model|scan")],
                   group=[gp("roleB", "POST")]),
        constraint("d-allow", "database", [crit("databaseId", "contains", "db.*")],
                   group=[gp("roleA", "GET"), gp("roleB", "POST")]),
        constraint("d-deny", "database", [crit("databaseId", "equals", "db2")],
                   group=[gp("roleB", "POST", "deny"), gp("roleA", "GET", "deny")]),
        constraint("d-deny-not", "database", [crit("databaseId", "does_not_contain", "1")],
                   group=[gp("roleA", "DELETE", "deny")]),
        constraint("d-allow-delete", "database", [crit("databaseId", "starts_with", "")],
                   group=[gp("roleA", "DELETE")]),
        constraint("api-allow", "api", [crit("route__path", "starts_with", "/database")],
                   group=[gp("roleA", a) for a in ACTIONS]),
        constraint("api-deny", "api", [crit("route__path", "contains", "/assets/.*/delete")],
                   group=[gp("roleA", "DELETE", "deny")]),
        constraint("api-allow-download", "api", [], [crit("route__path", "ends_with", "/download"),
                                                     crit("route__path", "equals", "/assets")],
                   group=[gp("roleB", "GET")]),
        constraint("web-allow", "web", [crit("route__path", "equals", "/assets")],
                   group=[gp("roleB", "GET")]),
        constraint("web-deny", "web", [crit("route__path", "is_one_of", ["/admin", "/settings"])],
                   group=[gp("roleB", "GET", "deny")]),
        constraint("t-allow", "tag", [crit("tagName", "starts_with", "t")],
                   users=[up(USER_ID, "PUT"), up(USER_ID, "GET")]),
        constraint("t-deny", "tag", [crit("tagName", "equals", "t-locked")], users=[up(USER_ID, "PUT", "deny")]),
        constraint("p-allow", "pipeline", [crit("pipelineId", "ends_with", "-pipe")],
                   [crit("databaseId", "equals", "db1"), crit("databaseId", "equals", "GLOBAL")],
                   group=[gp("roleA", "GET"), gp("roleA", "POST")]),
        constraint("w-deny", "workflow", [crit("workflowId", "contains", "x")],
                   group=[gp("roleB", "POST", "deny")]),
        constraint("w-allow", "workflow", [crit("workflowId", "is_not_one_of", ["wf-hidden"])],
                   group=[gp("roleB", "POST"), gp("roleB", "GET")]),
        constraint("m-allow", "metadataSchema", [crit("metadataSchemaName", "is_one_of", ["schema-a", "b"])],
                   group=[gp("roleA", "GET")]),
        constraint("tt-allow", "tagType", [crit("tagTypeName", "contains", ".*")],
                   group=[gp("roleB", "GET")]),
        constraint("r-allow", "role", [crit("roleName", "equals", "viewer")], group=[gp("roleA", "GET")]),
        constraint("ur-deny", "userRole", [crit("userId", "starts_with", "svc-")],
                   [crit("roleName", "equals", "admin")], group=[gp("roleA", "PUT", "deny")]),
    ]


def rewritten_constraints():
    """The synthetic shapes with values pycasbin's rule-text rewrites alter, for each operator, on
    allow and deny lines, entity and route checks."""
    return [
        constraint("a-allow", "asset", [crit("assetName", "equals", "p.glb")],
                   group=[gp("roleA", a) for a in ACTIONS]),
        constraint("a-allow-2", "asset", [crit("assetName", "starts_with", "scan p.v2")],
                   group=[gp("roleA", a) for a in ACTIONS]),
        constraint("a-allow-db", "asset", [crit("databaseId", "contains", "db")],
                   group=[gp("roleB", a) for a in ACTIONS]),
        constraint("a-deny-name", "asset", [crit("assetName", "contains", "r.glb")],
                   group=[gp("roleB", "GET", "deny")]),
        constraint("a-deny-tag", "asset", [crit("tags", "is_one_of", ["x-r.1", "locked"])],
                   group=[gp("roleB", "PUT", "deny")]),
        constraint("a-deny-not", "asset", [crit("tags", "is_not_one_of", ["a!b"])],
                   group=[gp("roleB", "DELETE", "deny")]),
        constraint("a-deny-type", "asset", [crit("assetType", "ends_with", "a&&b")],
                   group=[gp("roleB", "POST", "deny")]),
        constraint("a-deny-dnc", "asset", [crit("assetName", "does_not_contain", "model.p.glb")],
                   group=[gp("roleA", "DELETE", "deny")]),
        constraint("api-allow", "api", [crit("route__path", "starts_with", "/x/p.glb")],
                   group=[gp("roleA", a) for a in ACTIONS]),
        constraint("api-allow-all", "api", [crit("route__path", "contains", ".*")],
                   group=[gp("roleB", a) for a in ACTIONS]),
        constraint("api-deny", "api", [crit("route__path", "contains", "/x/r.*")],
                   group=[gp("roleB", "DELETE", "deny")]),
        constraint("web-allow", "web", [crit("route__path", "equals", "/x/(?!private).*")],
                   group=[gp("roleA", "GET")]),
        constraint("web-deny", "web", [crit("route__path", "ends_with", "/a||b")],
                   group=[gp("roleA", "GET", "deny")]),
        constraint("d-allow", "database", [crit("databaseId", "equals", ["p2.x", "x != y"])],
                   group=[gp("roleA", "GET")]),
    ]


def template_set(name):
    return shipped_constraints.template_constraints(name, "tpl", TEMPLATE_VARIABLES)


SETS = {f"template {name}": (lambda name=name: (template_set(name), ("tpl",), True, ()))
        for name in shipped_constraints.template_names()}
SETS.update({
    "seeded admin": lambda: (shipped_constraints.seeded_constraints("admin"), ("admin",), True, ()),
    "seeded basicReadOnly": lambda: (shipped_constraints.seeded_constraints("basicReadOnly"),
                                     ("basicReadOnly",), True, ()),
    "template database-admin + deny-tagged-assets": lambda: (
        template_set("database-admin.json") + template_set("deny-tagged-assets.json"), ("tpl",), True, ()),
    "synthetic two roles, MFA": lambda: (synthetic_constraints(), ("roleA", "roleB"), True, ()),
    "synthetic two roles, no MFA": lambda: (synthetic_constraints(), ("roleA", "roleB"), False, ("roleB",)),
    "everything at once": lambda: (
        [c for name in shipped_constraints.template_names() for c in template_set(name)]
        + shipped_constraints.seeded_constraints("admin")
        + shipped_constraints.seeded_constraints("basicReadOnly")
        + synthetic_constraints(),
        ("tpl", "admin", "basicReadOnly", "roleA", "roleB"), True, ()),
})
# Sets in which some request is decided by a deny line (an allow line also matches it). In the
# read-only sets a deny line only ever repeats what no allow line grants.
DENY_DECIDES = {"template database-admin + deny-tagged-assets", "synthetic two roles, MFA",
                "synthetic two roles, no MFA", "everything at once", "rewritten values"}
# The combined set walks ~250 lines per check; every Nth request of its sweep keeps it affordable.
SWEEP_STRIDE = {"everything at once": 5}


def set_definition(set_name):
    if set_name == "rewritten values":
        return rewritten_constraints(), ("roleA", "roleB"), True, ()
    return SETS[set_name]()


# ---------------------------------------------------------------------------------------------
# Building and compiling
# ---------------------------------------------------------------------------------------------
def build_policy_text(constraints, held_roles, mfa_enabled, mfa_free_roles, legacy):
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = USER_ID
    svc._mfaEnabled = mfa_enabled
    user_roles = [{"userId": USER_ID, "roleName": role} for role in held_roles]
    with ExitStack() as stack:
        stack.enter_context(patch.object(CasbinEnforcerService, "_read_current_user_roles_from_table",
                                         return_value=user_roles))
        stack.enter_context(patch.object(CasbinEnforcerService, "_read_policies_batch_optimized",
                                         return_value=copy.deepcopy(constraints)))
        stack.enter_context(patch.object(CasbinEnforcerService, "_read_mfaNotRequired_roles_from_table",
                                         return_value=[{"roleName": role} for role in mfa_free_roles]))
        if legacy:
            stack.enter_context(patch.object(CasbinEnforcerService, "_escape_rule_value",
                                             staticmethod(legacy_escape_rule_value)))
            stack.enter_context(patch.object(CasbinEnforcerService, "_object_type_rule",
                                             legacy_object_type_rule))
        return svc._create_policy_text_helper()


def active_roles(held_roles, mfa_enabled, mfa_free_roles):
    return set(held_roles) if mfa_enabled else set(held_roles) & set(mfa_free_roles)


def compiled_lines(policy_text):
    """(sub, act, eft, syntax-tree dump, string constants) per ``p`` line, compiled the way
    ``CoreEnforcer.enforce_ex`` compiles it."""
    policy_model = model.Model()
    policy_model.load_model_from_text(PERMISSION_CONSTRAINT_POLICY)
    string_adapter.StringAdapter(policy_text).load_policy(policy_model)
    matcher = policy_model["m"]["m"].value
    tokens = policy_model["p"]["p"].tokens
    lines = []
    for values in policy_model["p"]["p"].policy:
        parameters = dict(zip(tokens, values))
        rules = [util.escape_assertion(parameters[name]) for name in util.get_eval_value(matcher)]
        expression = FastEnforcer._get_expression(util.replace_eval(matcher, rules), {})
        tree = expression.ast_parsed_value
        constants = sorted(node.value for node in ast.walk(tree)
                           if isinstance(node, ast.Constant) and isinstance(node.value, str))
        lines.append((parameters["p_sub"], parameters["p_act"], parameters["p_eft"], ast.dump(tree),
                      constants))
    return lines


def expected_constants(constraints):
    """Per emitted line, in emission order: the patterns the line's raw values mean. The builder
    emits one line per permission of every constraint with a usable criterion."""
    lines = []
    for item in constraints:
        criteria = usable_criteria(item, "criteriaAnd") + usable_criteria(item, "criteriaOr")
        if not criteria:
            continue
        patterns = ["^" + item["objectType"] + "\\Z"]
        for criterion in criteria:
            patterns.extend(intended_pattern(criterion["operator"], value)
                            for value in criterion_values(criterion["value"]))
        count = len(item.get("groupPermissions") or []) + len(item.get("userPermissions") or [])
        lines.extend([sorted(patterns)] * count)
    return lines


def production_service(policy_text, mfa_enabled):
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = USER_ID
    svc._mfaEnabled = mfa_enabled
    svc._model_text = PERMISSION_CONSTRAINT_POLICY
    svc._dateTime_Cached = datetime.now()
    svc._enforcer = None
    svc._create_casbin_enforcer(policy_text)
    return svc


def allow_lines_only(policy_text):
    return "\n".join(line for line in policy_text.splitlines()
                     if not (line.startswith("p,") and line.rsplit(",", 1)[1].strip() == "deny"))


# ---------------------------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------------------------
def string_samples(values):
    samples = {"", "unmatched"}
    for value in values:
        samples.update({value, "pre-" + value, value + "-tail", value + "/tail", rewritten_form(value)})
    return sorted(samples)


def requests_for(constraints):
    by_field = {}
    for item in constraints:
        for criterion in item.get("criteriaAnd", []) + item.get("criteriaOr", []):
            by_field.setdefault(criterion["field"], set()).update(criterion_values(criterion["value"]))
    types = sorted({item["objectType"] for item in constraints}) + ["nonesuch"]
    requests = []
    for object_type in types:
        fields = get_constraint_fields_for_object_type(object_type) or ["databaseId"]
        objects = [{"object__type": object_type}]
        for field in fields:
            values = sorted(by_field.get(field, set()))
            if field == "tags":
                samples = [[], ["unmatched"]] + [shape for value in values
                                                  for shape in ([value], [value, "other"])]
                pair_samples = [["unmatched"]] + [[value] for value in values]
            else:
                samples = string_samples(values)
                pair_samples = values + ["unmatched"]
            objects.extend({"object__type": object_type, field: sample} for sample in samples)
            if field != "databaseId" and "databaseId" in fields:
                # Two-field objects: the set's own database ids beside each of the field's own values.
                database_ids = sorted(by_field.get("databaseId", set()))[:6] + ["unmatched"]
                objects.extend({"object__type": object_type, "databaseId": database_id, field: sample}
                               for database_id, sample in itertools.product(database_ids, pair_samples[:8]))
        requests.extend((obj, act) for obj in objects for act in ACTIONS)
    return requests


_SWEEPS = {}


def sweep(set_name):
    """Per request: (obj, act, new decision, reference decision, oracle decision, allow-lines-only
    decision). Computed once per set and shared by the tests that read it."""
    if set_name in _SWEEPS:
        return _SWEEPS[set_name]
    constraints, held_roles, mfa_enabled, mfa_free_roles = set_definition(set_name)
    new_text = build_policy_text(constraints, held_roles, mfa_enabled, mfa_free_roles, legacy=False)
    old_text = build_policy_text(constraints, held_roles, mfa_enabled, mfa_free_roles, legacy=True)
    new = production_service(new_text, mfa_enabled)
    old = production_service(old_text, mfa_enabled)
    allow_only = production_service(allow_lines_only(new_text), mfa_enabled)
    roles = active_roles(held_roles, mfa_enabled, mfa_free_roles)
    rows = []
    for obj, act in requests_for(constraints)[::SWEEP_STRIDE.get(set_name, 1)]:
        rows.append((obj, act, new.enforce(dict(obj), act), old.enforce(dict(obj), act),
                     policy_decision(constraints, roles, USER_ID, obj, act),
                     allow_only.enforce(dict(obj), act)))
    _SWEEPS[set_name] = rows
    return rows


# ---------------------------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------------------------
ALL_SETS = sorted(SETS) + ["rewritten values"]


@pytest.mark.unit
class TestEveryValueReachesTheCompiledExpressionAsWritten:
    """Whatever the values contain, each line compiles to the patterns its raw values mean and the
    service decides what the raw constraints prescribe."""

    @pytest.mark.parametrize("set_name", ALL_SETS)
    def test_constants_are_the_raw_value_patterns(self, set_name):
        constraints, held, mfa, mfa_free = set_definition(set_name)
        lines = compiled_lines(build_policy_text(constraints, held, mfa, mfa_free, legacy=False))
        expected = expected_constants(constraints)
        assert len(lines) == len(expected) > 0
        for line, patterns in zip(lines, expected):
            assert line[4] == patterns, (line[:3], line[4], patterns)

    @pytest.mark.parametrize("set_name", ALL_SETS)
    def test_decisions_are_the_raw_value_decisions(self, set_name):
        rows = sweep(set_name)
        wrong = [(obj, act, new, oracle) for obj, act, new, _old, oracle, _ in rows if new is not oracle]
        assert not wrong, wrong[:10]
        assert len(rows) > 50

    @pytest.mark.parametrize("set_name", ALL_SETS)
    def test_the_sweep_is_not_vacuous(self, set_name):
        """Granted and refused requests in every set with an allow line, and in the sets listed in
        DENY_DECIDES a request refused only because of a deny line."""
        constraints, held, mfa, mfa_free = set_definition(set_name)
        effects = {line[2] for line in compiled_lines(
            build_policy_text(constraints, held, mfa, mfa_free, legacy=False))}
        rows = sweep(set_name)
        decisions = {new for _obj, _act, new, *_ in rows}
        if "allow" in effects:
            assert decisions == {True, False}, f"{set_name}: the sweep saw only {decisions}"
        refused_by_deny = [row for row in rows if row[2] is False and row[5] is True]
        assert bool(refused_by_deny) is (set_name in DENY_DECIDES), (set_name, len(refused_by_deny))


@pytest.mark.unit
@pytest.mark.temporary  # pins decision parity of the every-character value escaping against the escaper it replaced
class TestParityWithTheReplacedEscaper:

    @pytest.mark.parametrize("set_name", sorted(SETS))
    def test_every_line_compiles_to_the_same_expression(self, set_name):
        constraints, held, mfa, mfa_free = set_definition(set_name)
        new_text = build_policy_text(constraints, held, mfa, mfa_free, legacy=False)
        old_text = build_policy_text(constraints, held, mfa, mfa_free, legacy=True)
        assert [l for l in new_text.splitlines() if l.startswith("g,")] == [
            l for l in old_text.splitlines() if l.startswith("g,")]
        new_lines, old_lines = compiled_lines(new_text), compiled_lines(old_text)
        assert len(new_lines) == len(old_lines) > 0
        assert new_lines == old_lines

    @pytest.mark.parametrize("set_name", sorted(SETS))
    def test_every_request_gets_the_same_decision(self, set_name):
        rows = sweep(set_name)
        differing = [(obj, act, new, old) for obj, act, new, old, *_ in rows if new is not old]
        assert not differing, differing[:10]
        assert len(rows) > 50

    def test_the_object_type_clause_is_the_text_it_was(self):
        svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
        for object_type in ("asset", "database", "api", "web", "tag", "tagType", "role", "userRole",
                            "pipeline", "workflow", "metadataSchema", "extWidget"):
            with patch.object(CasbinEnforcerService, "_escape_rule_value",
                              staticmethod(legacy_escape_rule_value)):
                legacy = legacy_object_type_rule(svc, object_type)
            assert svc._object_type_rule(object_type) == legacy, object_type

    def test_rewritten_values_differ_on_exactly_their_lines(self):
        """CONTROL: the comparison above can see a difference, and the rewritten values are where."""
        constraints, held, mfa, mfa_free = set_definition("rewritten values")
        new_lines = compiled_lines(build_policy_text(constraints, held, mfa, mfa_free, legacy=False))
        old_lines = compiled_lines(build_policy_text(constraints, held, mfa, mfa_free, legacy=True))
        assert len(new_lines) == len(old_lines)
        carries_rewritten = []
        for item in constraints:
            criteria = usable_criteria(item, "criteriaAnd") + usable_criteria(item, "criteriaOr")
            rewritten = any(rewritten_form(value) != value
                            for criterion in criteria for value in criterion_values(criterion["value"]))
            carries_rewritten.extend([rewritten] * (len(item["groupPermissions"]) + len(item["userPermissions"])))
        differs = [new != old for new, old in zip(new_lines, old_lines)]
        assert differs == carries_rewritten
        assert any(differs) and not all(differs)

    def test_rewritten_values_change_decisions_in_both_directions(self):
        """CONTROL: the reference decides some requests against the raw-value meaning -- granted
        where the value as written refuses, and refused where it grants."""
        rows = sweep("rewritten values")
        wrongly_granted = [row for row in rows if row[3] is True and row[4] is False]
        wrongly_refused = [row for row in rows if row[3] is False and row[4] is True]
        assert wrongly_granted and wrongly_refused
        assert all(row[2] is row[4] for row in rows)
