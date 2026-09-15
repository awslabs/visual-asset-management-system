# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-019 -- a criteria value must not be able to carry a Casbin policy-line separator.

``ConstraintCriteriaModel.value`` is validated by STRING_256 (a length check) and REGEX (an
``re.compile`` check). Neither looks at the characters, so a dedicated validator rejects the ones
that would pass both and then misbehave later, inside the generated Casbin policy text:

  * ``,``  is the policy-line field separator casbin splits on at bracket depth 0, so a comma in a
    single-valued ``is_one_of`` value adds a field to the row and poisons the user's whole policy
    (see tests/handlers/authz/test_constraint_comma_value.py for that mechanism end to end);
  * ``\\n`` ends a policy LINE, so a newline in a value can append or truncate policy rules;
  * ``\\r``, ``\\t`` and ``\\x00`` are equally never part of a legitimate database id, asset name or tag
    name, and they reach the audit log and the policy expression verbatim.

This is the write-time half of the fix -- the point where the constraint author can still be told. The
generator-side containment is the other half and is required as well: a value-rejecting validator does
nothing for the constraints ALREADY stored.

The controls in this file are the over-tightening catchers. The regex operators legitimately need
metacharacters (``.*``, ``^``, ``$``, ``|``, parentheses), names legitimately contain spaces, and
``GLOBAL`` is a reserved keyword the validator explicitly allows -- a validator that rejects punctuation
broadly would break the shipped permission templates and every seeded constraint.
"""

import pytest

from models.roleConstraints import ConstraintCriteriaModel

_CONTROL_CHARACTER_VALUES = [
    "a\nb",
    "a\rb",
    "a\tb",
    "a\x00b",
]


def _membership(value):
    return ConstraintCriteriaModel(field="tags", operator="is_one_of", value=value)


def _pattern(value, operator="contains"):
    return ConstraintCriteriaModel(field="databaseId", operator=operator, value=value)


@pytest.mark.unit
class TestSeparatorCharactersAreRejected:

    def test_a_comma_in_a_scalar_membership_value_is_rejected(self):
        with pytest.raises(ValueError):
            _membership("a,b")

    def test_a_comma_in_a_scalar_pattern_value_is_rejected(self):
        with pytest.raises(ValueError):
            _pattern("a,b")

    @pytest.mark.parametrize("value", _CONTROL_CHARACTER_VALUES)
    def test_control_characters_in_a_scalar_value_are_rejected(self, value):
        with pytest.raises(ValueError):
            _membership(value)

    @pytest.mark.parametrize("value", _CONTROL_CHARACTER_VALUES)
    def test_control_characters_in_a_list_element_are_rejected(self, value):
        with pytest.raises(ValueError):
            _membership(["clean", value])


@pytest.mark.unit
class TestTheCharacterRuleIsALiveConstraint:
    """Introspect the PARSED model, not the declaration text.

    Pydantic v1 collects an unrecognized ``Field()`` kwarg into ``field_info.extra`` instead of
    raising, so a v2 spelling (``pattern=``) becomes an inert annotation that constrains nothing --
    the model imports, the tests pass, and the field is wide open
    (``tests/models/test_no_dead_field_kwargs.py`` guards that repo-wide). The character rule is
    therefore carried by a field validator, and the registration is what is asserted here: a rule
    that is declared but not wired reads exactly like one that is.
    """

    def test_the_value_field_carries_the_dedicated_validator(self):
        field = ConstraintCriteriaModel.__fields__['value']
        registered = {validator.func.__name__
                      for validator in field.class_validators.values()}
        assert 'reject_policy_line_separator_characters' in registered, registered

    def test_no_inert_field_kwarg_stands_in_for_the_rule(self):
        """An empty ``extra`` is what proves every declared kwarg on the field is one pydantic v1
        actually recognizes."""
        field_info = ConstraintCriteriaModel.__fields__['value'].field_info
        assert (field_info.extra or {}) == {}, field_info.extra


@pytest.mark.unit
class TestLegitimateValuesStillAccepted:
    """OVER-TIGHTENING CATCHERS. Each of these is a value the shipped constraints actually use."""

    @pytest.mark.parametrize("value", [".*", "^abc$", "a|b", "(x)", "abc.def", "-_a-zA-Z0-9"])
    def test_regex_metacharacters_are_still_accepted(self, value):
        assert _pattern(value).value == value

    def test_the_admin_wildcard_is_still_accepted(self):
        """`databaseId contains .*` is how the shipped admin constraint grants everything."""
        assert _pattern(".*").value == ".*"

    @pytest.mark.parametrize("value", ["name with spaces", "Turbine Housing v2", "GLOBAL"])
    def test_names_and_the_global_keyword_are_still_accepted(self, value):
        assert ConstraintCriteriaModel(
            field="assetName", operator="equals", value=value).value == value

    def test_a_clean_two_element_list_still_round_trips(self):
        model = _membership(["locked", "approved"])
        assert model.value == ["locked", "approved"]

    def test_a_clean_scalar_still_round_trips(self):
        assert _membership("locked").value == "locked"

    def test_the_existing_length_bound_still_applies(self):
        """CONTROL that the validator still runs at all: the pre-existing STRING_256 bound is intact,
        so a passing accept-test above is the value being allowed rather than validation being
        skipped."""
        with pytest.raises(ValueError):
            _membership("x" * 257)
