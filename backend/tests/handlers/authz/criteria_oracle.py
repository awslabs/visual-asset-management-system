# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""What a constraint means, computed directly in Python from its stored data.

The oracle the authorization tests compare the Casbin enforcer against. Nothing here builds or reads
policy text: a criterion is judged from its raw value with ``re`` and ``in``, and a decision from the
raw constraints, the caller's active roles and the request.

* ``criterion_holds`` -- one criterion against one field value (the operator semantics the rule
  generator documents: ``equals`` is ``^value\\Z``, ``contains`` is ``(?s:.*)value(?s:.*)``, ...).
* ``policy_decision`` -- a whole constraint set: a line exists for each permission of a constraint with
  at least one usable criterion; it matches when the request object's type matches the constraint's
  type, every AND criterion holds and, when there are OR criteria, one of them does; the decision is
  "some allow line matches and no deny line does".
"""

import re

from backend.backend.common.constants import (
    ALWAYS_ALLOWED_OBJECT_KEYS,
    PERMISSION_CONSTRAINT_FIELDS,
    get_constraint_fields_for_object_type,
)

REGEX_PATTERNS = {
    "equals": lambda v: "^" + v + r"\Z",
    "contains": lambda v: "(?s:.*)" + v + "(?s:.*)",
    "does_not_contain": lambda v: "(?s:.*)" + v + "(?s:.*)",
    "starts_with": lambda v: "^" + v + ".*",
    "ends_with": lambda v: "(?s:.*)" + v + r"\Z",
}
NEGATING_OPERATORS = ("does_not_contain", "is_not_one_of")
MEMBERSHIP_OPERATORS = ("is_one_of", "is_not_one_of")
OPERATORS = tuple(REGEX_PATTERNS) + MEMBERSHIP_OPERATORS


def criterion_values(value):
    return [str(entry) for entry in value] if isinstance(value, list) else [str(value)]


def intended_pattern(operator, value):
    """The string the compiled rule should compare for one value: the regex for a regex operator,
    the value itself for a membership operator."""
    return REGEX_PATTERNS[operator](value) if operator in REGEX_PATTERNS else value


def criterion_holds(operator, values, entity):
    """What the criterion means for ``entity``, from the raw ``values``: True or False, or None when a
    value is not a usable pattern for it (production then denies the whole check)."""
    try:
        if operator in REGEX_PATTERNS:
            hit = any(re.match(REGEX_PATTERNS[operator](v), entity) is not None for v in values)
        else:
            hit = any(v in entity for v in values)
    except (re.error, TypeError):
        return None
    return (not hit) if operator in NEGATING_OPERATORS else hit


def rewritten_form(value):
    """``value`` as pycasbin's rule-text rewrites would alter it (``util.escape_assertion`` and the
    ``&&`` / ``||`` / ``!`` replacement in ``CoreEnforcer._get_expression``)."""
    value = re.sub(r"\b([pr])(\d*)\.", r"\1\2_", value)
    return value.replace("&&", "and").replace("||", "or").replace("!", "not")


def usable_criteria(constraint, key):
    """The criteria the policy builder turns into clauses for one constraint list."""
    criteria = list(constraint.get(key) or [])
    if key == "criteriaAnd":
        criteria.extend(constraint.get("criteria") or [])
    object_type = constraint.get("objectType")
    valid_fields = get_constraint_fields_for_object_type(object_type) if object_type else None
    return [criterion for criterion in criteria
            if criterion["field"] in PERMISSION_CONSTRAINT_FIELDS
            and (valid_fields is None or criterion["field"] in valid_fields)
            and criterion["operator"] in OPERATORS]


def request_object(obj):
    """The object the service hands Casbin: out-of-type fields dropped, every field present."""
    valid_fields = get_constraint_fields_for_object_type(obj.get("object__type"))
    if valid_fields:
        allowed = set(valid_fields) | ALWAYS_ALLOWED_OBJECT_KEYS
        obj = {key: value for key, value in obj.items() if key in allowed}
    enhanced = PERMISSION_CONSTRAINT_FIELDS.copy()
    for key, value in obj.items():
        enhanced[key] = "" if value is None else value
    return enhanced


class UndecidableRequest(Exception):
    """A criterion value is not a usable pattern for the request's field."""


def policy_decision(constraints, active_roles, user_id, obj, act):
    """The decision a constraint set prescribes for one request."""
    entity = request_object(obj)
    allowed = denied = False
    for constraint in constraints:
        and_criteria = usable_criteria(constraint, "criteriaAnd")
        or_criteria = usable_criteria(constraint, "criteriaOr")
        if not and_criteria and not or_criteria:
            continue
        effects = [permission.get("permissionType") or "allow"
                   for permission in constraint.get("groupPermissions") or []
                   if permission["groupId"] in active_roles and permission["permission"] == act]
        effects += [permission.get("permissionType") or "allow"
                    for permission in constraint.get("userPermissions") or []
                    if permission["userId"] == user_id and permission["permission"] == act]
        if not effects:
            continue
        if re.match("^" + constraint["objectType"] + r"\Z", entity["object__type"]) is None:
            continue

        def holds(criterion):
            result = criterion_holds(criterion["operator"], criterion_values(criterion["value"]),
                                     entity[criterion["field"]])
            if result is None:
                raise UndecidableRequest(criterion)
            return result

        if not all(holds(criterion) for criterion in and_criteria):
            continue
        if or_criteria and not any(holds(criterion) for criterion in or_criteria):
            continue
        allowed = allowed or "allow" in effects
        denied = denied or "deny" in effects
    return allowed and not denied
