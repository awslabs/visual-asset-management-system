# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A pattern-matching operator may not be aimed at a list-valued constraint field.

``tags`` holds a list on an asset. The pattern-matching operators compile to ``regexMatch(...)``, which
Casbin evaluates through Python's ``re`` — and ``re`` raises ``TypeError`` on a list. ``CasbinEnforcer``
catches that and returns False, so ONE such criterion makes the role fail EVERY check on that object
type, including entities the criterion was never about: an operator following the docs to protect a few
tagged assets locked the role out of all of them.

It fails closed, so nothing leaks. But the constraint stores cleanly and only misbehaves at
authorization time, where it reads as a permissions problem rather than a malformed rule. Refusing the
write is the one point where the author can still be told what to use instead.

``is_one_of`` / ``is_not_one_of`` compile to a plain ``in`` test and are the operators that work here.
"""

import pytest

from models.roleConstraints import ConstraintCriteriaModel

_REGEX_OPERATORS = ("equals", "contains", "does_not_contain", "starts_with", "ends_with")
_MEMBERSHIP_OPERATORS = ("is_one_of", "is_not_one_of")


def _list_fields():
    from common.constants import PERMISSION_CONSTRAINT_FIELDS

    return sorted(
        name for name, sample in PERMISSION_CONSTRAINT_FIELDS.items() if isinstance(sample, list))


def _string_fields():
    from common.constants import PERMISSION_CONSTRAINT_FIELDS

    return sorted(
        name for name, sample in PERMISSION_CONSTRAINT_FIELDS.items() if isinstance(sample, str))


@pytest.mark.unit
class TestListFieldOperatorGuard:

    def test_at_least_one_constraint_field_is_list_valued(self):
        """POSITIVE CONTROL for the parametrized tests below. If no field were list-valued they would
        collect zero cases and pass while asserting nothing."""
        assert _list_fields(), "no list-valued constraint field found; the guard tests are vacuous"

    @pytest.mark.parametrize("operator", _REGEX_OPERATORS)
    def test_every_regex_operator_is_rejected_on_a_list_field(self, operator):
        for field in _list_fields():
            with pytest.raises(ValueError) as excinfo:
                ConstraintCriteriaModel(field=field, operator=operator, value="locked")
            message = str(excinfo.value)
            # The message must point at the fix, not merely refuse: the author has no other signal.
            assert "is_one_of" in message, message
            assert "is_not_one_of" in message, message

    @pytest.mark.parametrize("operator", _MEMBERSHIP_OPERATORS)
    def test_the_membership_operators_are_accepted_on_a_list_field(self, operator):
        """The other half of the guard: it must not refuse the operators it recommends."""
        for field in _list_fields():
            model = ConstraintCriteriaModel(field=field, operator=operator, value="locked")
            assert model.operator == operator

    @pytest.mark.parametrize("operator", _REGEX_OPERATORS)
    def test_regex_operators_still_work_on_string_fields(self, operator):
        """REGRESSION GUARD. The guard keys off the field's type, so every single-valued field must be
        unaffected — these operators are how nearly every shipped constraint is written."""
        for field in _string_fields():
            model = ConstraintCriteriaModel(field=field, operator=operator, value="x")
            assert model.field == field

    def test_a_list_valued_criteria_value_is_still_allowed_on_a_list_field(self):
        """`is_one_of` with several values is the natural way to match any of a set of tags."""
        for field in _list_fields():
            model = ConstraintCriteriaModel(
                field=field, operator="is_one_of", value=["locked", "approved"])
            assert model.value == ["locked", "approved"]

    def test_an_unknown_field_is_not_blocked_by_this_guard(self):
        """The guard must only speak about fields it knows. An unknown/deprecated field is skipped
        elsewhere (`_generate_criteria_object_rules` drops it), so it must not be rejected here."""
        model = ConstraintCriteriaModel(field="notAConstraintField", operator="contains", value="x")
        assert model.field == "notAConstraintField"


@pytest.mark.unit
class TestShippedTemplateUsesAWorkingOperator:
    """The template that prompted this must itself be saveable."""

    def test_deny_tagged_assets_template_criteria_are_accepted(self):
        import json
        import os

        path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..",
            "documentation", "permissionsTemplates", "deny-tagged-assets.json")
        template = json.load(open(os.path.abspath(path), encoding="utf-8"))
        criteria = [c for constraint in template["constraints"]
                    for key in ("criteriaAnd", "criteriaOr")
                    for c in constraint.get(key, [])]
        assert criteria, "template declares no criteria; this test would assert nothing"
        for criterion in criteria:
            # The template's value is a {{VARIABLE}} placeholder, substituted by apply_template.py.
            ConstraintCriteriaModel(field=criterion["field"], operator=criterion["operator"],
                                    value="locked")
