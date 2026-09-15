# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The quantifier-ambiguity budget on a constraint criterion value.

`test_constraint_regex_complexity.py` covers the two shapes that need a GROUP — a quantifier applied
to a group that itself repeats or alternates, and a backreference. A third family needs no group at
all and was accepted by that rule: quantifiers that are merely NUMEROUS. `'a*' * 14 + 'b'` (29
characters, inside the pre-existing STRING_256 bound) takes 58.7 s against a 24-character run of 'a',
and `'.*' * 13 + 'z'` (27 characters) takes 11.1 s against an ordinary 23-character asset name — no
planted entity required, because `.` matches whatever is already there. Each added quantifier
multiplies the cost, and the value is re-evaluated by `re.match` for every policy line on every
`enforce()` call, so either one hangs authorization for the affected role until the Lambda times out.

The budget is a bound on the estimated search space rather than a count, because the per-quantifier
cost is not uniform: an unbounded quantifier divides the subject with every other unbounded one
(comb(256, k)), while an optional or a bounded repeat contributes only its own range. That is what
lets three wildcards stay accepted — `/database/.*/assets/.*/files/.*` is a legitimate route
constraint — while a fourth, which measures 0.97 s against an adversarial subject, does not.

TIMING IS ASSERTED, not just the verdict. A rule that agrees with the code it guards proves nothing
about the hazard; `TestTheAcceptedWorstCaseIsActuallyFast` matches the most expensive value this
validator still admits against the longest subject a rule can be compared with, and bounds the wall
clock. OVER-TIGHTENING is the other risk, and `test_constraint_regex_complexity.py` carries the
accept-side corpus read from the shipped permission templates; the classes here add the values a
constraint author plausibly writes.
"""

import re
import sys
import time

import pytest


def _validators():
    """Resolve through `sys.modules` at call time, the way `models/roleConstraints.py` does."""
    return sys.modules['common.validators']


def _regex(value):
    return _validators().validate_regex('criteriaValue', value)


@pytest.mark.unit
class TestNumerousQuantifiersAreRejected:
    @pytest.mark.parametrize('value,why', [
        ('a*' * 14 + 'b', 'the measured 58.7 s value: adjacent stars on one literal'),
        ('.*' * 13 + 'z', 'the measured 11.1 s value: adjacent wildcards, no planted subject'),
        ('.*a' * 5 + 'z', 'separated by a literal, which is MORE expensive than adjacent'),
        ('a+' * 6 + 'b', 'plus behaves as star for this purpose'),
        ('a{1,}' * 6 + 'b', 'an open-ended brace is an unbounded quantifier'),
        ('a?' * 30 + 'b', 'a long chain of optionals'),
        ('a{1,50}' * 5 + 'b', 'bounded ranges multiply out just as far'),
    ])
    def test_an_over_budget_value_is_rejected(self, value, why):
        (valid, message) = _regex(value)
        assert valid is False, f"{value!r} must be rejected ({why})"
        assert 'criteriaValue' in message
        assert 'quantifier' in message

    def test_the_rejection_reaches_the_dispatcher(self):
        """The gate has to fire on the path roleConstraints uses — the REGEX name — not only on a
        direct call to validate_regex."""
        (valid, _) = _validators().validate({
            'criteriaValue': {'value': '.*' * 13 + 'z', 'validator': 'REGEX',
                              'allowGlobalKeyword': True}})
        assert valid is False

    def test_the_two_earlier_families_still_have_their_own_message(self):
        """CONTROL that the budget did not swallow the group / backreference rule: `(a+)+b` is
        rejected for repeating a repeating group, not for its quantifier count."""
        (valid, message) = _regex('(a+)+b')
        assert valid is False
        assert 'repeats' in message


@pytest.mark.unit
class TestPlausibleAuthoredValuesStillAccepted:
    """OVER-TIGHTENING CATCHERS. A rejection here is a 400 on constraint create/update for a value a
    permission author would reasonably write."""

    @pytest.mark.parametrize('value', [
        '.*',                                   # the shipped wildcard
        '^prod-.*$',
        '.*-dev',
        '/database/.*/assets/.*',               # a two-wildcard route constraint
        '/database/.*/assets/.*/files/.*',      # three wildcards: the practical ceiling
        '[a-z]+-[0-9]+',
        '[a-z]+-[0-9]+-[a-z]+',
        r'^[a-zA-Z0-9\-._\s]{1,256}$',          # object_name_pattern itself
        'colou?r',
        r'https?://.*',
        r'asset_\d{4}',
        r'\d{4}\d{2}',                          # adjacent EXACT counts add no ambiguity
        'Turbine Housing v2',
        '(prod|dev)-team',                       # un-quantified alternation
    ])
    def test_a_plausible_value_is_accepted(self, value):
        (valid, message) = _regex(value)
        assert valid is True, f"{value!r} must stay accepted: {message}"

    def test_a_template_placeholder_brace_is_not_read_as_a_quantifier(self):
        """`{{DATABASE_ID}}` is a substitution placeholder and `{{` is a literal to re, so it must not
        be counted as a bounded repeat."""
        assert _regex('{{DATABASE_ID}}')[0] is True
        assert _regex('{{TAG_VALUE}}')[0] is True

    def test_a_quantifier_inside_a_character_class_is_a_literal(self):
        assert _regex(r'[a*+?]+')[0] is True

    def test_an_escaped_quantifier_is_a_literal(self):
        assert _regex(r'v2\*\+\?')[0] is True

    def test_a_lazy_modifier_is_part_of_its_quantifier(self):
        """`.*?` is ONE lazy quantifier, not a star plus an optional.

        Asserted through the verdict rather than through the estimate, because the mock validators
        module re-exports only the public names. Three lazy wildcards sit inside the budget at three
        unbounded quantifiers; counting each trailing `?` as a separate optional would multiply the
        estimate by eight and push the same value over, so acceptance is what shows the modifier is
        absorbed. The four-quantifier neighbour is the control that the budget is still in force.
        """
        assert _regex('.*?-dev')[0] is True
        assert _regex('.*?a.*?a.*?az')[0] is True
        assert _regex('.*?a.*?a.*?a.*?az')[0] is False


@pytest.mark.unit
class TestTheAcceptedWorstCaseIsActuallyFast:
    """The budget's purpose, asserted in wall clock rather than in agreement with the code.

    The subject is the longest a rule is compared against (OBJECT_NAME's 256 characters) and is a
    single run of the separator character, which is the shape that maximises the split count.
    """

    # The interpolation shape the Casbin rule builder emits for `equals`.
    @staticmethod
    def _evaluate(value, subject):
        started = time.perf_counter()
        re.match('^' + value + r'\Z', subject)
        return time.perf_counter() - started

    @pytest.mark.parametrize('value', [
        '.*a.*a.*az',                   # three unbounded, separated: the estimate's worst admitted
        '.*.*.*z',                      # three unbounded, adjacent
        'a?' * 23 + 'b',                # the longest optional chain the budget admits
        r'^[a-zA-Z0-9\-._\s]{1,256}$',
    ])
    def test_an_accepted_value_evaluates_quickly_against_the_longest_subject(self, value):
        assert _regex(value)[0] is True, f"{value!r} is expected to be an ACCEPTED value"
        elapsed = max(self._evaluate(value, 'a' * 256), self._evaluate(value, 'a' * 24))
        assert elapsed < 1.0, f"{value!r} is accepted but took {elapsed:.3f}s to evaluate"

    def test_the_rejected_neighbour_of_the_worst_case_is_the_slow_one(self):
        """POSITIVE CONTROL on the line's position: one more quantifier than the budget admits is
        both rejected AND measurably expensive, so the boundary is not arbitrary."""
        value = '.*a.*a.*a.*az'
        assert _regex(value)[0] is False
        elapsed = self._evaluate(value, 'a' * 256)
        assert elapsed > 0.1, (
            f"{value!r} took only {elapsed:.3f}s, so this control no longer demonstrates the hazard "
            "the budget bounds")
