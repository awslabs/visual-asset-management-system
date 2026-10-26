# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Object-type routing decides exactly what the all-lines evaluation decides (issue #827).

``ObjectTypeRoutingEnforcer`` (the enforcer the production factory builds) walks, per request, only
the policy lines that can match the request's object type. ``ExpressionCachingEnforcer`` -- the
enforcer the factory built before, which walks every line -- is the reference: both are built from
the same model and policy text and driven through the same ``CasbinEnforcerService.enforce()``
wrapper, and every request must get the same decision from both, and the same outcome (decision or
exception class) from the two enforcers underneath.

Policy sets: every shipped template in ``documentation/permissionsTemplates/``, the seeded admin and
read-only constraints (read from the ``dynamodb-authdefaults-*`` constructs), synthetic sets with deny
lines, every criteria operator, user-level grants, multi-role users with and without MFA and lines for
object types no request asks for, and hand-written policy text with lines routing must SHARE (regex
type names, a gate that is not first, a top-level ``||``, no gate, a rule that does not parse, a wrong
field count, the deny-all fallback).

Requests: every object type a policy names, every registered type, and types no line names, times
every action, times resource values drawn from the policy's own criteria values (and variations of
them). Each request is classified by what the plain policy matches -- only an allow line, only a deny
line, both, or neither -- and the synthetic sets must realise all four, so the comparison is not
vacuous.

The mutation tests break the routing three ways -- drop the deny lines, misroute a type, drop the
shared lines -- and require the comparison to report a difference.
"""

import itertools
import sys
import threading
from unittest.mock import patch

import pytest
import simpleeval
from casbin import model
from casbin.persist.adapters import string_adapter

import backend.backend.handlers.authz as authz
from backend.backend.handlers.authz import (
    CasbinEnforcerService,
    ExpressionCachingEnforcer,
    ObjectTypeRoutingEnforcer,
)
from backend.backend.common.constants import ALLOWED_CONSTRAINT_OBJECT_TYPES
from tests.handlers.authz import shipped_constraints

USER_ID = "routing-user"
ACTIONS = ("GET", "PUT", "POST", "DELETE")
DATABASE_ID = "routing-db"
VARIABLE_VALUES = {"DATABASE_ID": DATABASE_ID, "TAG_VALUE": "locked"}

# Object types the synthetic sets add, the way an add-on or custom constants lane registers one.
# ``extOrphan`` carries lines and is never requested.
EXTENSION_TYPES = {"extWidget": ["databaseId", "assetName"], "extGadget": ["databaseId"],
                   "extOrphan": ["databaseId"]}
REQUESTED_EXTENSION_TYPES = ("extWidget", "extGadget")
# Types no line names, including near-misses of a gate's literal name.
UNNAMED_TYPES = ("nonesuch", "", "Asset", "asset\n", "asset|database", ".*")

CATEGORIES = ("only-allow", "only-deny", "both", "neither")


@pytest.fixture(autouse=True)
def _extension_types_and_clean_caches():
    """Register the synthetic extension types on the authz module (policy builder and scrubber both
    read ``get_constraint_fields_for_object_type`` from it) and keep the per-user maps empty."""
    core_fields = authz.get_constraint_fields_for_object_type

    def fields(object_type):
        return list(EXTENSION_TYPES[object_type]) if object_type in EXTENSION_TYPES else core_fields(object_type)

    authz.casbin_user_enforcer_map.clear()
    authz.casbin_user_policy_map.clear()
    with patch.object(authz, "get_constraint_fields_for_object_type", fields), \
         patch.object(authz, "log_authorization"):
        yield
    authz.casbin_user_enforcer_map.clear()
    authz.casbin_user_policy_map.clear()


# ---------------------------------------------------------------------------------------------
# Policy sets
# ---------------------------------------------------------------------------------------------
def _crit(field, operator, value):
    return {"field": field, "operator": operator, "value": value}


def _gp(role, permission, permission_type="allow"):
    return {"groupId": role, "permission": permission, "permissionType": permission_type}


def _up(user, permission, permission_type="allow"):
    return {"userId": user, "permission": permission, "permissionType": permission_type}


def _constraint(constraint_id, object_type, criteria_and=(), criteria_or=(), group=(), users=()):
    return {"constraintId": constraint_id, "objectType": object_type, "criteriaAnd": list(criteria_and),
            "criteriaOr": list(criteria_or), "groupPermissions": list(group), "userPermissions": list(users)}


def _synthetic_deny_constraints():
    """Allow and deny lines on several types and every operator, over two roles and one user grant."""
    return [
        # asset: allow a database, deny a tag inside it, deny a name pattern on writes
        _constraint("a-allow", "asset", [_crit("databaseId", "equals", "db1")],
                    group=[_gp("roleA", a) for a in ACTIONS]),
        _constraint("a-deny-tag", "asset", [_crit("tags", "is_one_of", ["locked", "quarantine"])],
                    group=[_gp("roleA", "GET", "deny"), _gp("roleB", "DELETE", "deny")]),
        _constraint("a-deny-name", "asset", [_crit("assetName", "starts_with", "secret")],
                    group=[_gp("roleB", a, "deny") for a in ("PUT", "POST")]),
        _constraint("a-allow-type", "asset", [_crit("assetType", "ends_with", ".glb")],
                    [_crit("databaseId", "contains", "db"), _crit("tags", "is_not_one_of", ["draft"])],
                    group=[_gp("roleB", "GET"), _gp("roleB", "PUT")]),
        # database: allow by regex, deny one database outright
        _constraint("d-allow", "database", [_crit("databaseId", "contains", "db.*")],
                    group=[_gp("roleA", "GET"), _gp("roleB", "POST")]),
        _constraint("d-deny", "database", [_crit("databaseId", "equals", "db2")],
                    group=[_gp("roleB", "POST", "deny"), _gp("roleA", "GET", "deny")]),
        _constraint("d-deny-not", "database", [_crit("databaseId", "does_not_contain", "1")],
                    group=[_gp("roleA", "DELETE", "deny")]),
        _constraint("d-allow-delete", "database", [_crit("databaseId", "starts_with", "")],
                    group=[_gp("roleA", "DELETE")]),
        # api / web routes
        _constraint("api-allow", "api", [_crit("route__path", "starts_with", "/database")],
                    group=[_gp("roleA", a) for a in ACTIONS]),
        _constraint("api-deny", "api", [_crit("route__path", "contains", "/assets/.*/delete")],
                    group=[_gp("roleA", "DELETE", "deny")]),
        _constraint("web-allow", "web", [_crit("route__path", "equals", "/assets")],
                    group=[_gp("roleB", "GET")]),
        # tag / pipeline / workflow / metadata schema, with a user-level grant and deny
        _constraint("t-allow", "tag", [_crit("tagName", "starts_with", "t")],
                    users=[_up(USER_ID, "PUT"), _up(USER_ID, "GET")]),
        _constraint("t-deny", "tag", [_crit("tagName", "equals", "t-locked")], users=[_up(USER_ID, "PUT", "deny")]),
        _constraint("p-allow", "pipeline", [_crit("pipelineId", "ends_with", "-p")],
                    [_crit("databaseId", "equals", "db1"), _crit("databaseId", "equals", "GLOBAL")],
                    group=[_gp("roleA", "GET"), _gp("roleA", "POST")]),
        _constraint("w-deny", "workflow", [_crit("workflowId", "contains", "x")],
                    group=[_gp("roleB", "POST", "deny")]),
        _constraint("w-allow", "workflow", [_crit("databaseId", "starts_with", "db")],
                    group=[_gp("roleB", "POST")]),
        _constraint("m-allow", "metadataSchema", [_crit("metadataSchemaName", "contains", ".*")],
                    group=[_gp("roleA", "GET")]),
        # extension types: two requested, one never requested
        _constraint("x-allow", "extWidget", [_crit("assetName", "equals", "w1")],
                    group=[_gp("roleA", "GET"), _gp("roleA", "DELETE")]),
        _constraint("x-deny", "extWidget", [_crit("databaseId", "equals", "db2")],
                    group=[_gp("roleB", "GET", "deny")]),
        _constraint("g-allow", "extGadget", [_crit("databaseId", "contains", ".*")], group=[_gp("roleB", "GET")]),
        _constraint("o-allow", "extOrphan", [_crit("databaseId", "contains", ".*")],
                    group=[_gp("roleA", a) for a in ACTIONS]),
        _constraint("o-deny", "extOrphan", [_crit("databaseId", "equals", "db1")],
                    group=[_gp("roleA", "GET", "deny")]),
    ]


def _all_shipped_constraints():
    """Every template on its own role, plus both seeded roles: one user holding all of them."""
    constraints = []
    for index, name in enumerate(shipped_constraints.template_names()):
        constraints.extend(shipped_constraints.template_constraints(name, f"tpl{index}", VARIABLE_VALUES))
    for role in shipped_constraints.SEEDED_ROLE_CONSTRUCTS:
        constraints.extend(shipped_constraints.seeded_constraints(role))
    return constraints


def _roles_of(constraints):
    return sorted({gp["groupId"] for c in constraints for gp in c.get("groupPermissions", [])})


def _policy_text(constraints, roles, mfa_enabled=True, mfa_free_roles=None):
    """Policy text from the REAL builder; only the table reads are stubbed. The constraints read is
    keyed on the role names asked for, as the GSI query is, so an MFA-filtered role takes its lines
    with it."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = USER_ID
    svc._mfaEnabled = mfa_enabled
    mfa_free = [{"roleName": r} for r in (mfa_free_roles if mfa_free_roles is not None else roles)]

    def constraints_for(role_names):
        wanted = set(role_names)
        return [c for c in constraints
                if any(gp["groupId"] in wanted for gp in c.get("groupPermissions", []))
                or c.get("userPermissions")]

    with patch.object(CasbinEnforcerService, "_read_current_user_roles_from_table",
                      return_value=[{"userId": USER_ID, "roleName": r} for r in roles]), \
         patch.object(CasbinEnforcerService, "_read_policies_batch_optimized", side_effect=constraints_for), \
         patch.object(CasbinEnforcerService, "_read_mfaNotRequired_roles_from_table", return_value=mfa_free):
        return svc._create_policy_text_helper()


# Lines routing must share, written as policy text. Each grants or denies something a generated
# line elsewhere in the set also decides, so a routing error changes a decision.
_SHARED_LINE_TEXT = "\n".join([
    "g, user::routing-user, 'role::r1'",
    "g, user::routing-user, 'role::r2'",
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^asset\\Z')) && (regexMatch(r.obj.databaseId, '^db1\\Z')), GET, allow",
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^database\\Z')) && (regexMatch(r.obj.databaseId, '(?s:.*)')), GET, allow",
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^pipeline\\Z')) && (regexMatch(r.obj.databaseId, '(?s:.*)')), GET, allow",
    # regex type name: matches every type -> shared deny
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^.*\\Z')) && (regexMatch(r.obj.databaseId, '(?s:.*)locked(?s:.*)')), GET, deny",
    # alternation: '^asset' OR 'database\Z' -> shared allow
    r"p, 'role::r2', (regexMatch(r.obj.object__type, '^asset|database\\Z')) && (regexMatch(r.obj.databaseId, '^db2\\Z')), GET, allow",
    # no gate at all -> shared
    r"p, 'role::r2', (regexMatch(r.obj.databaseId, '^db3\\Z')), GET, allow",
    # gate is not the first criterion -> shared
    r"p, 'role::r2', (regexMatch(r.obj.databaseId, '^db4\\Z')) && (regexMatch(r.obj.object__type, '^database\\Z')), GET, allow",
    # top-level '||': the gate is not required -> shared
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^pipeline\\Z')) || (regexMatch(r.obj.databaseId, '^db5\\Z')), GET, allow",
    # a shared deny that is the only thing standing between a type and the generic allow above
    r"p, 'role::r2', !(regexMatch(r.obj.object__type, '^asset\\Z')) && (regexMatch(r.obj.databaseId, '^db3\\Z')), GET, deny",
    # an exact line on a field no request carries: AttributeDoesNotExist -> the service's retry path
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^asset\\Z')) && (regexMatch(r.obj.unknownField, '^x\\Z')), PUT, allow",
    r"p, user::routing-user, (regexMatch(r.obj.object__type, '^tag\\Z')) && (regexMatch(r.obj.tagName, '^t1\\Z')), PUT, allow",
])

# Lines that raise when evaluated: the walk must reach them, or not, exactly as before. Exact lines
# raise only for their own type; a shared line raises for every type whose walk reaches it.
_RAISING_LINE_TEXT = "\n".join([
    "g, user::routing-user, 'role::r1'",
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^asset\\Z')) && (regexMatch(r.obj.databaseId, '^db9\\Z')), GET, deny",
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^asset\\Z')) && (regexMatch(r.obj.databaseId, '(?s:.*)')), GET, allow",
    # exact asset line calling an undefined function: raises for asset requests only
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^asset\\Z')) && (noSuchFunction(r.obj.databaseId)), GET, allow",
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^database\\Z')) && (regexMatch(r.obj.databaseId, '^db1\\Z')), GET, deny",
    # group in the type name -> shared; raises for database and tag requests only
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^(database|tag)\\Z')) && (noSuchFunction(r.obj.databaseId)), GET, allow",
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^pipeline\\Z')) && (regexMatch(r.obj.databaseId, '(?s:.*)')), GET, allow",
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^pipeline\\Z')) && (regexMatch(r.obj.databaseId, '^db7\\Z')), GET, deny",
    # does not parse -> shared; raises for every request whose walk reaches it
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^web\\Z')) && (1 +), GET, allow",
])

# A line with five fields: ``enforce_ex`` raises on it for every walk that reaches it.
_FIELD_COUNT_LINE_TEXT = "\n".join([
    "g, user::routing-user, 'role::r1'",
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^tag\\Z')) && (regexMatch(r.obj.tagName, '(?s:.*)')), GET, allow",
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^asset\\Z')) && (regexMatch(r.obj.databaseId, '^db9\\Z')), GET, deny",
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^tag\\Z')), GET, allow, extra",
    r"p, 'role::r1', (regexMatch(r.obj.object__type, '^asset\\Z')) && (regexMatch(r.obj.databaseId, '(?s:.*)')), GET, allow",
])

_ROLE_ONLY_TEXT = "g, user::routing-user, 'role::r1'"


def _generated_sets():
    """name -> (policy text, the constraints it was built from)."""
    synthetic = _synthetic_deny_constraints()
    shipped = _all_shipped_constraints()
    everything = shipped + synthetic
    sets = {}
    for index, name in enumerate(shipped_constraints.template_names()):
        role = f"tpl{index}"
        constraints = shipped_constraints.template_constraints(name, role, VARIABLE_VALUES)
        sets[f"template:{name}"] = (_policy_text(constraints, [role]), constraints)
    for role in shipped_constraints.SEEDED_ROLE_CONSTRUCTS:
        constraints = shipped_constraints.seeded_constraints(role)
        sets[f"seeded:{role}"] = (_policy_text(constraints, [role]), constraints)
    one_role = [c for c in synthetic if not any(gp["groupId"] == "roleB" for gp in c["groupPermissions"])]
    sets["synthetic:one-role"] = (_policy_text(one_role, ["roleA"]), one_role)
    sets["synthetic:two-roles"] = (_policy_text(synthetic, ["roleA", "roleB"]), synthetic)
    sets["all:mfa"] = (_policy_text(everything, _roles_of(everything), mfa_enabled=True), everything)
    sets["all:no-mfa"] = (_policy_text(everything, _roles_of(everything), mfa_enabled=False,
                                       mfa_free_roles=["admin", "roleB", "tpl1", "tpl3"]), everything)
    return sets


_GENERATED_SETS = {}


def _generated(name):
    if not _GENERATED_SETS:
        _GENERATED_SETS.update(_generated_sets())
    return _GENERATED_SETS[name]


RAW_SETS = {"raw:shared-lines": _SHARED_LINE_TEXT, "raw:raising-lines": _RAISING_LINE_TEXT,
            "raw:field-count": _FIELD_COUNT_LINE_TEXT, "raw:roles-only": _ROLE_ONLY_TEXT,
            "raw:deny-all": authz.POLICY_TEXT_DENY_ALL}
RAW_TYPES = ["asset", "database", "pipeline", "tag", "web"] + list(UNNAMED_TYPES)


# ---------------------------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------------------------
def _criteria_values(constraints):
    """field -> the values the constraints' criteria carry (list values flattened)."""
    values = {}
    for constraint in constraints:
        for criterion in constraint.get("criteriaAnd", []) + constraint.get("criteriaOr", []):
            raw = criterion["value"]
            for value in raw if isinstance(raw, list) else [raw]:
                values.setdefault(criterion["field"], [])
                if value not in values[criterion["field"]]:
                    values[criterion["field"]].append(value)
    return values


_RAW_VALUES = {"databaseId": ["db1", "db2", "db3", "db4", "db5", "db7", "db9", "x-locked-y"],
               "tagName": ["t1", "t2"], "route__path": ["/assets"]}


def _variants(field, values, limit):
    """Each criteria value and its near-misses on both ends, plus values no criterion names."""
    if field == "tags":
        tags = [v for v in values if v][:limit] or ["locked"]
        return [[], ["unrelated"]] + [[t] for t in tags] + [[tags[0], "unrelated"]]
    out = ["", "zz-unmatched"]
    for value in values[:limit]:
        out.extend([value, f"{value}-tail", f"pre-{value}"])
    return list(dict.fromkeys(out))


def _requests(object_types, values_by_field, limit=6, pair_limit=3):
    """(object, action) pairs. Per object type: the bare object, one object per variant of each of
    the type's fields, and two-field objects over the first variants of each field pair, which is
    what reaches an allow line and a deny line at once. A type no line names gets the bare object
    and the single-field objects only: it walks no line, so more objects show nothing more."""
    requests = []
    for object_type in object_types:
        fields = authz.get_constraint_fields_for_object_type(object_type) or ["databaseId"]
        variants = {f: _variants(f, values_by_field.get(f, []), limit) for f in fields}
        objects = [{"object__type": object_type}]
        for field in fields:
            objects.extend({"object__type": object_type, field: v} for v in variants[field])
        if pair_limit and object_type not in UNNAMED_TYPES:
            for first, second in itertools.combinations(fields, 2):
                for a, b in itertools.product(variants[first][:pair_limit + 2], variants[second][:pair_limit + 2]):
                    objects.append({"object__type": object_type, first: a, second: b})
        requests.extend((obj, act) for obj in objects for act in ACTIONS)
    return requests


def _object_types_for(constraints):
    """Every type a constraint names, every registered type, the requested extension types and the
    types no line names -- never ``extOrphan``, whose lines no request asks about."""
    named = {constraint["objectType"] for constraint in constraints}
    types = (named | set(ALLOWED_CONSTRAINT_OBJECT_TYPES) | set(REQUESTED_EXTENSION_TYPES)) - {"extOrphan"}
    return sorted(types) + list(UNNAMED_TYPES)


# ---------------------------------------------------------------------------------------------
# Comparator
# ---------------------------------------------------------------------------------------------
def _service(policy_text, enforcer_class):
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = USER_ID
    svc._mfaEnabled = True
    svc._model_text = authz.PERMISSION_CONSTRAINT_POLICY
    new_model = model.Model()
    new_model.load_model_from_text(authz.PERMISSION_CONSTRAINT_POLICY)
    svc._enforcer = enforcer_class(model=new_model, adapter=string_adapter.StringAdapter(policy_text),
                                   enable_log=False)
    return svc


def _routed_service(policy_text):
    """Through the production factory."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = USER_ID
    svc._mfaEnabled = True
    svc._model_text = authz.PERMISSION_CONSTRAINT_POLICY
    svc._enforcer = None
    svc._create_casbin_enforcer(policy_text)
    assert type(svc._enforcer) is ObjectTypeRoutingEnforcer
    return svc


def _enhanced(svc, obj):
    """The request object ``CasbinEnforcerService.enforce`` hands the enforcer."""
    enhanced = authz.PERMISSION_CONSTRAINT_FIELDS.copy()
    for key, value in svc._scrub_object_fields(obj).items():
        enhanced[key] = "" if value is None else value
    return enhanced


def _outcome(enforcer, obj, act):
    try:
        return enforcer.enforce(f"user::{USER_ID}", obj, act)
    except Exception as error:  # the outcome under comparison includes which exception
        return type(error).__name__


def _mismatches(policy_text, requests, compare_raw=True):
    """Every request on which the routed side and the all-lines side differ: in the service's
    decision, and (``compare_raw``) in the enforcer's own outcome, a decision or the exception it
    raises. The service catches every exception, so only the raw comparison shows WHICH line a walk
    stopped at when that line raises."""
    current = _service(policy_text, ExpressionCachingEnforcer)
    routed = _routed_service(policy_text)
    differences = []
    for obj, act in requests:
        decision_current = current.enforce(dict(obj), act)
        decision_routed = routed.enforce(dict(obj), act)
        raw_current = raw_routed = None
        if compare_raw:
            raw_current = _outcome(current._enforcer, _enhanced(current, obj), act)
            raw_routed = _outcome(routed._enforcer, _enhanced(routed, obj), act)
        if decision_current is not decision_routed or raw_current != raw_routed:
            differences.append((obj, act, (decision_current, raw_current), (decision_routed, raw_routed)))
    return differences


def _variant_text(policy_text, keep_effect):
    """The policy with only its ``keep_effect`` lines, each turned into an allow."""
    lines = []
    for line in policy_text.splitlines():
        if line.startswith("p,"):
            head, effect = line.rsplit(",", 1)
            if effect.strip() != keep_effect:
                continue
            line = f"{head}, allow"
        lines.append(line)
    return "\n".join(lines)


def _categories(policy_text, requests):
    """Count of requests per category: which kinds of line the policy matches for it."""
    allows = _service(_variant_text(policy_text, "allow"), ExpressionCachingEnforcer)
    denies = _service(_variant_text(policy_text, "deny"), ExpressionCachingEnforcer)
    counts = dict.fromkeys(CATEGORIES, 0)
    for obj, act in requests:
        allow, deny = allows.enforce(dict(obj), act), denies.enforce(dict(obj), act)
        counts[{(True, False): "only-allow", (False, True): "only-deny",
                (True, True): "both", (False, False): "neither"}[(allow, deny)]] += 1
    return counts


def _generated_requests(name):
    """The request matrix for a generated set. The combined sets carry several hundred lines, so
    their all-lines side is slow: they draw fewer values per field than the single-source sets and
    no two-field objects (every line in them is also in a single-source set, which has both)."""
    _, constraints = _generated(name)
    limit, pair_limit = (4, 0) if name.startswith("all:") else (6, 3)
    return _requests(_object_types_for(constraints), _criteria_values(constraints), limit, pair_limit)


def _raw_requests():
    return _requests(RAW_TYPES, _RAW_VALUES, limit=len(_RAW_VALUES["databaseId"]))


# ---------------------------------------------------------------------------------------------
# Equivalence
# ---------------------------------------------------------------------------------------------
GENERATED_SET_NAMES = (
    [f"template:{name}" for name in shipped_constraints.template_names()]
    + [f"seeded:{role}" for role in shipped_constraints.SEEDED_ROLE_CONSTRUCTS]
    + ["synthetic:one-role", "synthetic:two-roles", "all:mfa", "all:no-mfa"]
)


@pytest.mark.unit
@pytest.mark.parametrize("set_name", GENERATED_SET_NAMES)
def test_routed_decisions_equal_all_lines_decisions_on_generated_policy(set_name):
    text, _ = _generated(set_name)
    requests = _generated_requests(set_name)
    assert len(requests) > 500
    assert _mismatches(text, requests, compare_raw=False) == []

    if set_name.startswith("all:"):
        # Hundreds of lines: the matrix is the reduced one and the category walk is skipped. Every
        # line here also sits in a single-source set above, whose categories are checked.
        return
    counts = _categories(text, requests)
    # A shipped template can be a pure deny overlay (deny-tagged-assets) or carry no deny at all;
    # the synthetic sets carry both and must realise every category.
    assert counts["neither"] > 0 and counts["only-allow"] + counts["only-deny"] > 0, counts
    if set_name.startswith("synthetic:"):
        assert all(counts[c] > 0 for c in CATEGORIES), counts


@pytest.mark.unit
@pytest.mark.parametrize("set_name", sorted(RAW_SETS))
def test_routed_outcomes_equal_all_lines_outcomes_on_hand_written_policy(set_name):
    assert _mismatches(RAW_SETS[set_name], _raw_requests()) == []


@pytest.mark.unit
def test_shared_line_policy_realises_every_category_and_raises_where_it_did():
    """The hand-written sets are not vacuous: the shared-line set reaches all four categories, and
    the raising sets raise for some requests and decide others. A raising SHARED line sits in every
    walk, so in those sets only a deny that ends the walk before it decides anything."""
    requests = _raw_requests()
    counts = _categories(_SHARED_LINE_TEXT, requests)
    assert all(counts[c] > 0 for c in CATEGORIES), counts

    for text, raised in ((_RAISING_LINE_TEXT, {"SyntaxError", "FunctionNotDefined"}),
                         (_FIELD_COUNT_LINE_TEXT, {"RuntimeError"})):
        routed = _routed_service(text)
        outcomes = {_outcome(routed._enforcer, _enhanced(routed, obj), act) for obj, act in requests}
        assert {False} | raised <= outcomes, outcomes


# ---------------------------------------------------------------------------------------------
# The comparison catches a broken routing
# ---------------------------------------------------------------------------------------------
def _routes_without(predicate):
    """A ``_build_policy_routes`` replacement that removes the lines ``predicate`` selects."""
    original = ObjectTypeRoutingEnforcer._build_policy_routes

    def build(self):
        routes = original(self)
        if routes is not None:
            routes.by_type = {t: [line for line in lines if not predicate(line, routes)]
                              for t, lines in routes.by_type.items()}
            routes.shared = [line for line in routes.shared if not predicate(line, routes)]
        return routes

    return build


@pytest.mark.unit
def test_comparison_fails_when_routing_drops_the_deny_lines():
    text, _ = _generated("synthetic:two-roles")
    requests = _generated_requests("synthetic:two-roles")
    with patch.object(ObjectTypeRoutingEnforcer, "_build_policy_routes",
                      _routes_without(lambda line, routes: line[-1] == "deny")):
        differences = _mismatches(text, requests, compare_raw=False)
    assert differences
    assert all(current[0] is False and routed[0] is True for _, _, current, routed in differences)


@pytest.mark.unit
def test_comparison_fails_when_routing_misroutes_a_type():
    text, _ = _generated("seeded:admin")
    requests = _generated_requests("seeded:admin")
    real_gate = authz._literal_gate_object_type

    def misrouting_gate(tree):
        object_type = real_gate(tree)
        return "database" if object_type == "asset" else object_type

    with patch.object(authz, "_literal_gate_object_type", misrouting_gate):
        differences = _mismatches(text, requests, compare_raw=False)
    assert differences
    assert {obj["object__type"] for obj, _, _, _ in differences} == {"asset"}


@pytest.mark.unit
def test_comparison_fails_when_routing_drops_the_shared_lines():
    requests = _raw_requests()
    with patch.object(ObjectTypeRoutingEnforcer, "_build_policy_routes",
                      _routes_without(lambda line, routes: any(line is s for s in routes.shared))):
        differences = _mismatches(_SHARED_LINE_TEXT, requests)
    assert differences
    # A dropped shared DENY shows up as a widened grant, a dropped shared ALLOW as a narrowed one.
    assert {routed[0] for _, _, _, routed in differences} == {True, False}


# ---------------------------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
def test_every_generated_line_is_routed_to_exactly_its_own_type():
    """On generated policy no line is shared, each type's list holds exactly that type's lines in
    policy order, and the lists partition the policy -- the routing actually narrows the walk."""
    text, _ = _generated("all:mfa")
    enforcer = _routed_service(text)._enforcer
    routes = enforcer._policy_routes
    policy = enforcer.get_policy()
    assert routes is not None and routes.shared == []
    assert sum(len(lines) for lines in routes.by_type.values()) == len(policy)
    for object_type, lines in routes.by_type.items():
        gate = f"regexMatch(r.obj.object__type, '^{object_type}\\\\Z')"
        assert lines == [line for line in policy if line[1].startswith(f"({gate})")]
    assert set(routes.by_type) >= {"asset", "database", "api", "web", "extWidget", "extOrphan"}


@pytest.mark.unit
def test_shared_lines_are_listed_for_every_type_in_policy_order():
    enforcer = _routed_service(_SHARED_LINE_TEXT)._enforcer
    routes = enforcer._policy_routes
    policy = enforcer.get_policy()
    shared_rules = [line[1] for line in routes.shared]
    assert len(routes.shared) == 6
    assert any("'^.*\\\\Z'" in rule for rule in shared_rules)
    assert any("'^asset|database\\\\Z'" in rule for rule in shared_rules)
    assert any(" || " in rule for rule in shared_rules)
    assert any(rule.startswith("!(") for rule in shared_rules)
    for object_type, lines in routes.by_type.items():
        assert lines == [line for line in policy
                         if any(line is s for s in routes.shared) or f"'^{object_type}\\\\Z'))" in line[1]
                         and line[1].startswith("(regexMatch(r.obj.object__type")]
    assert set(routes.by_type) == {"asset", "database", "pipeline", "tag"}


@pytest.mark.unit
def test_deny_all_and_role_only_policies_are_not_routed():
    for text in (authz.POLICY_TEXT_DENY_ALL, _ROLE_ONLY_TEXT):
        svc = _routed_service(text)
        assert svc._enforcer._policy_routes is None
        assert svc.enforce({"object__type": "asset", "databaseId": "db1"}, "GET") is False


@pytest.mark.unit
def test_requests_outside_the_service_shape_walk_every_line():
    """A request the router cannot read safely walks the full list: a non-dict object, a missing,
    non-string or string-subclass type, a non-string subject, a disabled enforcer, and a policy list
    that is no longer the one the routes were built from."""
    enforcer = _routed_service(_generated("synthetic:two-roles")[0])._enforcer
    subject = f"user::{USER_ID}"
    full = {"object__type": "asset", "databaseId": "db1"}

    class Text(str):
        pass

    class Mapping(dict):
        pass

    assert enforcer._route((subject, dict(full), "GET")) is not None
    for rvals in [
        (subject, Mapping(full), "GET"),
        (subject, {"databaseId": "db1"}, "GET"),
        (subject, {"object__type": ["asset"]}, "GET"),
        (subject, {"object__type": Text("asset")}, "GET"),
        (Text(subject), dict(full), "GET"),
        (subject, dict(full)),
    ]:
        assert enforcer._route(rvals) is None, rvals

    enforcer.enabled = False
    assert enforcer._route((subject, dict(full), "GET")) is None
    enforcer.enabled = True

    assertion = enforcer.model["p"]["p"]
    assertion.policy = list(assertion.policy)
    assert enforcer._route((subject, dict(full), "GET")) is None


@pytest.mark.unit
def test_the_full_policy_list_is_restored_after_every_check_including_a_raising_one():
    svc = _routed_service(_RAISING_LINE_TEXT)
    assertion = svc._enforcer.model["p"]["p"]
    full = assertion.policy
    length = len(full)
    assert svc.enforce({"object__type": "asset", "databaseId": "db9"}, "GET") is False
    for object_type, error in (("database", simpleeval.FunctionNotDefined), ("pipeline", SyntaxError)):
        with pytest.raises(error):
            svc._enforcer.enforce(f"user::{USER_ID}", _enhanced(svc, {"object__type": object_type}), "GET")
        assert assertion.policy is full and len(full) == length


@pytest.fixture
def _aggressive_thread_switching():
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


@pytest.mark.unit
def test_concurrent_checks_of_different_types_on_one_service_stay_correct(_aggressive_thread_switching):
    """Each check swaps in its type's list under the instance lock. Eight threads alternate object
    types on one service under a 1 us switch interval; every answer must be the all-lines answer."""
    text, _ = _generated("synthetic:two-roles")
    svc = _routed_service(text)
    reference = _service(text, ExpressionCachingEnforcer)
    cases = [
        ({"object__type": "asset", "databaseId": "db1", "tags": []}, "GET"),
        ({"object__type": "asset", "databaseId": "db1", "tags": ["locked"]}, "GET"),
        ({"object__type": "database", "databaseId": "db2"}, "GET"),
        ({"object__type": "database", "databaseId": "db1"}, "GET"),
        ({"object__type": "api", "route__path": "/database/x"}, "PUT"),
        ({"object__type": "nonesuch"}, "GET"),
    ]
    expected = [reference.enforce(dict(obj), act) for obj, act in cases]
    assert {True, False} <= set(expected)
    failures = []

    def worker(offset):
        for i in range(150):
            index = (i + offset) % len(cases)
            obj, act = cases[index]
            if svc.enforce(dict(obj), act) is not expected[index]:
                failures.append((offset, i, obj, act))

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert failures == []
    assert svc._enforcer.model["p"]["p"].policy is svc._enforcer._policy_routes.source
