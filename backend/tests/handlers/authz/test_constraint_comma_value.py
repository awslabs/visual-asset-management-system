# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A criteria value must not be able to change the SHAPE of the Casbin policy line it lands in.

Casbin's policy reader is structure-unaware and does not honour quoting:

* ``casbin.persist.adapters.StringAdapter`` splits the policy text on newlines, and
* ``casbin.persist.adapter.load_policy_line`` splits each line on ``,`` at bracket depth 0, tracking
  ``()`` and ``[]`` as depth.

A value carrying one of those characters therefore alters the line's field count rather than its
content. The reported case is a SINGLE-valued ``is_one_of``, which
``_generate_criteria_object_rules`` used to emit as a bare clause -- it parenthesised the group only
when ``len(clauses) > 1``::

    p, 'role::r', <objtype rule> && 'a,b' in r.obj.tags, GET, allow
                                        ^ splits here -> five fields, not four

The blast radius is the whole role, not the one criterion: the policy text is built per USER across
every role they hold, and a row whose width does not match the ``p`` definition makes the enforcer
fail as a unit. So a value that looks like a harmless tag name ("Ops,Eng") denies every route and
every entity for every holder of the role, admin included -- with no validation message and no log
line at write time.

The same reader treats brackets as depth, so ``a]`` pops the stack early and ``a\\)`` leaves it
unbalanced, and a raw newline starts a new record. Those shapes fail identically and reach EVERY
operator, including the ones whose comma is already contained by ``regexMatch(...)``. The containment
has two halves, and both are asserted here:

* ``_escape_rule_value`` rewrites each structural character as its ``\\xNN`` source escape, which
  Python's parser (simpleeval compiles the matcher) turns back into exactly the same character. This
  is the half that covers a line terminator and an unbalanced bracket, which no wrapper can contain;
* ``_generate_criteria_object_rules`` parenthesises the clause group unconditionally, so every
  emitted clause is a bracketed span at the depth the reader counts. This is the half that holds if
  a separator ever reaches the clause text anyway.

Parentheses around one complete boolean sub-expression cannot change a verdict, and
``TestParenthesisingIsDecisionNeutral`` measures that on the real enforcer rather than asserting it.
The rule text a plain value produces is pinned below in its bracketed form.

These tests assert the DECISION and the FIELD COUNT through Casbin's real reader rather than only the
rule text, and pin the rule text where the text itself is the contract. Note the fix WIDENS access: a
stored comma constraint stops poisoning and starts evaluating, so a role that was deny-all begins
authorizing whatever its other constraints grant.
"""

import glob
import itertools
import os
import re
from datetime import datetime

import pytest
from casbin import FastEnforcer, model
from casbin.persist.adapter import load_policy_line
from casbin.persist.adapters import string_adapter
from unittest.mock import patch

from backend.backend.handlers import authz
from backend.backend.handlers.authz import CasbinEnforcerService
from backend.backend.common.constants import (
    PERMISSION_CONSTRAINT_POLICY,
    PERMISSION_CONSTRAINT_FIELDS,
)

USER_ID = "tester"
COMMA_VALUE = "a,b"

# Fields of the `p` policy definition minus the leading "p" key: sub, obj_rule, act, eft.
POLICY_FIELD_COUNT = 4

_REGEX_OPERATORS = ("equals", "contains", "starts_with", "ends_with")
_ALL_OPERATORS = _REGEX_OPERATORS + ("does_not_contain", "is_one_of", "is_not_one_of")
_NEGATING_OPERATORS = ("does_not_contain", "is_not_one_of")
_MEMBERSHIP_OPERATORS = ("is_one_of", "is_not_one_of")

# Values whose characters the policy reader treats as structure: the field separator, the two
# bracket pairs it counts as depth (each in a shape that is unbalanced on its own), and the line
# terminators plus NUL, which is not parseable as source at all.
_STRUCTURAL_VALUES = (
    "a,b", ",", "a,b,c", "a]", "[ab", "a\\)", "a(b", "a)b", "(x)", "[ab]",
    "a\nb", "a\rb", "a\x00b", "a\tb",
)

# Values with no structural character. The emitted rule text for these is the contract other authz
# tests pin, so it must not move.
_PLAIN_VALUES = (
    "Secret", "locked", ".*", "^abc$", "a|b", "name with spaces", "GLOBAL", "db1", "a.b", "\\d+",
)


def _strip_outer_group(rule):
    """The same clause with the generator's enclosing parentheses removed.

    Removes the outer pair only when it spans the WHOLE clause, so a rule that merely starts and
    ends with a bracket for other reasons is returned untouched. Every raw bracket in an emitted
    rule comes from the generator (a value's own brackets are escaped), so the depth walk is exact.
    """
    if not (rule.startswith("(") and rule.endswith(")")):
        return rule
    depth = 0
    for index, character in enumerate(rule):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0 and index != len(rule) - 1:
                return rule
    return rule[1:-1]


@pytest.fixture(autouse=True)
def _clear_enforcer_caches():
    """``casbin_user_enforcer_map`` / ``casbin_user_policy_map`` are module-level globals with no
    per-test reset. Clearing on both sides keeps a stale entry from another module out of these
    tests and keeps anything these tests leave behind from reaching the next module."""
    authz.casbin_user_enforcer_map.clear()
    authz.casbin_user_policy_map.clear()
    yield
    authz.casbin_user_enforcer_map.clear()
    authz.casbin_user_policy_map.clear()


def _rules(criteria, object_type=None):
    """Call the real rule generator without __init__ (which would need DynamoDB)."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    return svc._generate_criteria_object_rules(criteria, object_type=object_type)


def _rule(field, operator, value):
    generated = _rules([{"field": field, "operator": operator, "value": value}])
    assert len(generated) == 1, f"expected one rule, got {generated}"
    return generated[0]


def _field_for(operator):
    """A field the operator can legitimately compare: the membership operators take the list-valued
    field, the regex operators a string one."""
    return "tags" if operator in _MEMBERSHIP_OPERATORS else "assetName"


def _policy_row(obj_rule, effect="allow"):
    """Tokenize a policy line with casbin's REAL reader and return the stored field list."""
    new_model = model.Model()
    new_model.load_model_from_text(PERMISSION_CONSTRAINT_POLICY)
    load_policy_line(f"p, 'role::r', {obj_rule}, GET, {effect}", new_model)
    rows = new_model.model["p"]["p"].policy
    assert len(rows) == 1, f"line produced {len(rows)} rows"
    return rows[0]


def _constraint(constraint_id, criteria_and, object_type="asset", permission="GET",
                permission_type="allow"):
    return {
        "constraintId": constraint_id,
        "objectType": object_type,
        "criteriaAnd": criteria_and,
        "criteriaOr": [],
        "groupPermissions": [{"groupId": "roleA", "permission": permission,
                              "permissionType": permission_type}],
        "userPermissions": [],
    }


def _production_service(policies):
    """A service built through the PRODUCTION policy-text and enforcer-creation path.

    ``_create_casbin_enforcer`` is what falls back to POLICY_TEXT_DENY_ALL when the policy text does
    not load, so going through it is what makes the poisoning observable as a denial rather than as a
    raised exception in the test.
    """
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = USER_ID
    svc._mfaEnabled = True
    svc._model_text = PERMISSION_CONSTRAINT_POLICY
    svc._dateTime_Cached = datetime.now()
    svc._enforcer = None
    user_roles = [{"userId": USER_ID, "roleName": "roleA"}]
    with patch.object(CasbinEnforcerService, "_read_current_user_roles_from_table",
                      return_value=user_roles), \
            patch.object(CasbinEnforcerService, "_read_policies_batch_optimized",
                         return_value=policies):
        policy_text = svc._create_policy_text_helper()
    svc._create_casbin_enforcer(policy_text)
    return svc


def _asset(svc, action="GET", **attrs):
    obj = {"object__type": "asset"}
    obj.update(attrs)
    return svc.enforce(obj, action)


def _decide(obj_rule, effect="allow", **attrs):
    policy_text = ("g, user::tester, 'role::r'\n"
                   f"p, 'role::r', {obj_rule}, GET, {effect}")
    new_model = model.Model()
    new_model.load_model_from_text(PERMISSION_CONSTRAINT_POLICY)
    enforcer = FastEnforcer(
        model=new_model, adapter=string_adapter.StringAdapter(policy_text), enable_log=False)
    obj = PERMISSION_CONSTRAINT_FIELDS.copy()
    obj["object__type"] = "asset"
    obj.update(attrs)
    try:
        return enforcer.enforce("user::tester", obj, "GET")
    except Exception:
        # An unevaluable expression fails closed in production (enforce() catches and denies);
        # mirror that so a malformed rule reads as a denial rather than an error.
        return False


@pytest.mark.unit
class TestCommaInASingleValuedMembershipCriterion:
    """The one emitted shape the reported comma can break."""

    def test_a_scalar_comma_value_stays_one_policy_field(self):
        row = _policy_row(_rule("tags", "is_one_of", COMMA_VALUE))
        assert len(row) == POLICY_FIELD_COUNT, f"policy row split into {len(row)} fields: {row}"

    def test_a_single_element_list_comma_value_stays_one_policy_field(self):
        """A ONE-element list takes the same bare-clause branch as a scalar."""
        row = _policy_row(_rule("tags", "is_one_of", [COMMA_VALUE]))
        assert len(row) == POLICY_FIELD_COUNT, f"policy row split into {len(row)} fields: {row}"

    def test_an_unrelated_constraint_still_authorizes_beside_a_comma_constraint(self):
        """THE damage assertion. The policy text is built per user across all their roles, so a
        corrupt line takes down constraints that have nothing to do with the comma. Checking only
        the comma criterion's own outcome would pass while the role stayed dead."""
        svc = _production_service([
            _constraint("A", [{"field": "databaseId", "operator": "equals", "value": "db1"}]),
            _constraint("B", [{"field": "tags", "operator": "is_one_of", "value": COMMA_VALUE}]),
        ])
        assert _asset(svc, databaseId="db1", tags=[]) is True, (
            "the unrelated allow constraint stopped authorizing")
        assert _asset(svc, databaseId="db2", tags=[]) is False, (
            "the unrelated constraint must still be scoped, not turned into an allow-all")

    def test_the_unrelated_constraint_authorizes_without_the_comma_constraint(self):
        """POSITIVE CONTROL for the test above: the same unrelated constraint, same helper, no comma
        value. Proves a False there is the corruption rather than a mis-built fixture."""
        svc = _production_service([
            _constraint("A", [{"field": "databaseId", "operator": "equals", "value": "db1"}]),
            _constraint("B", [{"field": "tags", "operator": "is_one_of", "value": "ab"}]),
        ])
        assert _asset(svc, databaseId="db1", tags=[]) is True
        assert _asset(svc, databaseId="db2", tags=[]) is False

    def test_the_comma_criterion_evaluates_once_it_is_contained(self):
        """A contained comma value is a normal membership test: a tag literally named "a,b" matches
        and nothing else does. Driven through the REAL generated rule, so it pins the round trip
        (escape -> policy text -> Python parse -> evaluate) and not just a hand-written string."""
        rule = _rule("tags", "is_one_of", COMMA_VALUE)
        assert _decide(rule, tags=["a,b"]) is True
        assert _decide(rule, tags=["a", "b"]) is False
        assert _decide(rule, tags=[]) is False

    def test_the_comma_is_escaped_in_the_value_and_the_clause_is_bracketed(self):
        """The rule text IS the contract here: it is what Casbin's reader tokenizes. The comma
        becomes its source escape, and the clause sits inside parentheses, so the reader sees a
        bracketed span either way. Both halves are pinned because either one alone keeps the field
        count right, and a silent loss of one would not show up in a decision test."""
        assert _rule("tags", "is_one_of", COMMA_VALUE) == "('a\\x2cb' in r.obj.tags)"
        assert _rule("assetName", "equals", COMMA_VALUE) == (
            "(regexMatch(r.obj.assetName, '^a\\x2cb\\\\Z'))")


@pytest.mark.unit
class TestPolicyLineStructure:
    """The field count is the discriminator, so it is proven to discriminate."""

    def test_an_unescaped_comma_clause_splits_the_line(self):
        """NEGATIVE CONTROL. A hand-written unescaped clause yields one field too many, which is
        exactly what the assertions here detect. Without this the field-count checks could be
        passing against a reader that never splits."""
        row = _policy_row("'a,b' in r.obj.tags")
        assert len(row) == POLICY_FIELD_COUNT + 1
        clean = _policy_row("'ab' in r.obj.tags")
        assert len(clean) == POLICY_FIELD_COUNT

    def test_an_unescaped_bracket_clause_breaks_the_reader_outright(self):
        """NEGATIVE CONTROL for the bracket half. ``load_policy_line`` pops its depth stack on
        ``]``, so an unescaped one unbalances the line and the closing paren pops empty."""
        with pytest.raises(IndexError):
            _policy_row("regexMatch(r.obj.assetName, '^a]\\\\Z')")

    @pytest.mark.parametrize("operator", _ALL_OPERATORS)
    @pytest.mark.parametrize("value", _STRUCTURAL_VALUES + _PLAIN_VALUES)
    def test_every_operator_and_shape_emits_exactly_one_well_formed_policy_row(
            self, operator, value):
        """The structural check the next value that needs escaping will trip, whatever character it
        turns out to be: every emitted line is read by Casbin's own reader, which must neither raise
        nor produce a row of the wrong width. Covers scalar, one-element list (the bare-clause
        branch) and multi-element list (the parenthesised branch) for every operator."""
        field = _field_for(operator)
        for shape in (value, [value], [value, "zz"]):
            rule = _rule(field, operator, shape)
            row = _policy_row(rule)
            assert len(row) == POLICY_FIELD_COUNT, (
                f"{operator} / {shape!r} emitted a {len(row)}-field row: {rule!r}")

    @pytest.mark.parametrize("value", _STRUCTURAL_VALUES)
    def test_a_structural_value_survives_a_deny_line_too(self, value):
        """The effect field sits after the rule, so a split line also corrupts the effect."""
        row = _policy_row(_rule("tags", "is_one_of", value), effect="deny")
        assert len(row) == POLICY_FIELD_COUNT
        assert row[-1] == "deny"


@pytest.mark.unit
class TestEveryClauseIsABracketedSpan:
    """The structural half: the clause group is parenthesised for EVERY operator and shape.

    ``load_policy_line`` counts '()' as depth, so a clause that is one bracketed span cannot
    contribute a depth-0 separator no matter what it contains. That is what makes the field count
    right even for a value the escaping did not reach, which is the reason to wrap the single-clause
    case that used to be emitted bare.
    """

    @pytest.mark.parametrize("operator", _ALL_OPERATORS)
    @pytest.mark.parametrize("value", _STRUCTURAL_VALUES + _PLAIN_VALUES)
    def test_the_clause_spans_from_the_first_bracket_to_the_last_character(self, operator, value):
        """Scalar, one-element list (the branch that used to skip the wrapper) and multi-element
        list, for every operator."""
        field = _field_for(operator)
        for shape in (value, [value], [value, "zz"]):
            rule = _rule(field, operator, shape)
            prefix = "!(" if operator in _NEGATING_OPERATORS else "("
            assert rule.startswith(prefix), rule
            assert rule.endswith(")"), rule
            assert _strip_outer_group(rule) != rule or operator in _NEGATING_OPERATORS, rule

    def test_the_bracket_alone_contains_a_raw_separator(self):
        """THE POINT of the wrapper, measured on Casbin's real reader and independently of the
        escaping: the same raw comma that adds a field to a bare clause adds none inside a bracketed
        one. Paired with the bare-clause control above, this is the discriminator."""
        assert len(_policy_row("'a,b' in r.obj.tags")) == POLICY_FIELD_COUNT + 1
        assert len(_policy_row("('a,b' in r.obj.tags)")) == POLICY_FIELD_COUNT

    def test_a_single_criterion_constraint_still_authorizes_end_to_end(self):
        """POSITIVE CONTROL through the production policy-text path: the wrapped single clause is
        still a working grant, and still a scoped one."""
        svc = _production_service([
            _constraint("A", [{"field": "databaseId", "operator": "equals", "value": "db1"}]),
        ])
        assert _asset(svc, databaseId="db1", tags=[]) is True
        assert _asset(svc, databaseId="db2", tags=[]) is False


@pytest.mark.unit
class TestParenthesisingIsDecisionNeutral:
    """Parentheses around one complete boolean sub-expression cannot change a verdict.

    Measured rather than asserted: each generated clause is decided by a real Casbin enforcer twice,
    once as emitted and once with the enclosing group removed, and the two verdicts must agree for
    every operator, value shape and entity. The negating operators were always wrapped (``!(...)``),
    so nothing is stripped for them -- ``test_the_stripping_is_not_a_no_op`` states which branch is
    which so the comparison cannot pass vacuously.
    """

    _ENTITIES = {
        "membership": [["Secret", "locked"], [], ["a,b"], ["locked"], ["zz"], ["(x)"]],
        "scalar": ["Secret", "zz", "", "a,b", "Secretive", "x\ny", "GLOBAL"],
    }

    @pytest.mark.parametrize("operator", _ALL_OPERATORS)
    def test_the_verdict_is_the_same_with_and_without_the_group(self, operator):
        field = _field_for(operator)
        entities = self._ENTITIES[
            "membership" if operator in _MEMBERSHIP_OPERATORS else "scalar"]
        for value in _STRUCTURAL_VALUES + _PLAIN_VALUES:
            for shape in (value, [value], [value, "zz"]):
                rule = _rule(field, operator, shape)
                bare = _strip_outer_group(rule)
                for entity in entities:
                    for effect in ("allow", "deny"):
                        wrapped_verdict = _decide(rule, effect=effect, **{field: entity})
                        bare_verdict = _decide(bare, effect=effect, **{field: entity})
                        assert wrapped_verdict is bare_verdict, (
                            f"{operator} value={shape!r} entity={entity!r} effect={effect}: "
                            f"{rule!r} decided {wrapped_verdict}, {bare!r} decided "
                            f"{bare_verdict}")

    @pytest.mark.parametrize("operator", _ALL_OPERATORS)
    def test_the_stripping_is_not_a_no_op(self, operator):
        """CONTROL for the comparison above. A non-negating operator must really have had a group
        removed, otherwise the two verdicts are the same expression twice."""
        rule = _rule(_field_for(operator), operator, "locked")
        stripped_something = _strip_outer_group(rule) != rule
        assert stripped_something is (operator not in _NEGATING_OPERATORS), rule


@pytest.mark.unit
class TestPlainValueRuleTextIsPinned:
    """REGRESSION GUARD on the emitted expression itself.

    A criterion with no structural character carries its value through verbatim -- no escape
    sequence is introduced -- and the clause is wrapped in the parentheses the generator now adds
    unconditionally. Nothing else about the text moves: the ``regexMatch`` anchors that
    ``test_constraint_rule_anchors`` reasons about and the operator wrappers are as they were. The
    emitted expression IS the authorization behaviour of every constraint in the product, so the
    exact strings are pinned here; a change that re-quotes or re-shapes a clause has to come back
    through this class and say so.
    """

    @pytest.mark.parametrize("operator,expected", [
        ("equals", "(regexMatch(r.obj.assetName, '^Secret\\\\Z'))"),
        ("contains", "(regexMatch(r.obj.assetName, '(?s:.*)Secret(?s:.*)'))"),
        ("does_not_contain", "!(regexMatch(r.obj.assetName, '(?s:.*)Secret(?s:.*)'))"),
        ("starts_with", "(regexMatch(r.obj.assetName, '^Secret.*'))"),
        ("ends_with", "(regexMatch(r.obj.assetName, '(?s:.*)Secret\\\\Z'))"),
    ])
    def test_scalar_regex_operator_text_keeps_its_pattern_and_gains_only_the_group(
            self, operator, expected):
        assert _rule("assetName", operator, "Secret") == expected

    def test_scalar_membership_is_a_bracketed_membership_test(self):
        assert _rule("tags", "is_one_of", "locked") == "('locked' in r.obj.tags)"

    def test_scalar_negated_membership_keeps_its_scoped_negation(self):
        assert _rule("tags", "is_not_one_of", "locked") == "!('locked' in r.obj.tags)"

    def test_the_object_type_rule_keeps_its_pattern(self):
        """Every emitted policy line carries this clause, so a change here changes every rule."""
        assert _rule("object__type", "equals", "asset") == (
            "(regexMatch(r.obj.object__type, '^asset\\\\Z'))")

    @pytest.mark.parametrize("value", _PLAIN_VALUES)
    @pytest.mark.parametrize("operator", _ALL_OPERATORS)
    def test_a_plain_value_is_interpolated_verbatim(self, operator, value):
        """Whatever wrapper the operator adds, a value with no structural character appears in the
        rule exactly as stored -- no escape sequences introduced."""
        rule = _rule(_field_for(operator), operator, value)
        assert value.replace("\\", "\\\\") in rule, rule
        assert "\\x" not in rule, rule


@pytest.mark.unit
class TestEscapingIsSemanticallyTransparent:
    """A ``\\xNN`` escape must change the SOURCE only, never the value Casbin compares.

    The oracle computes each answer directly in Python from the RAW value, so agreement proves the
    escape -> policy text -> Python parse -> evaluate round trip is lossless rather than merely
    self-consistent.
    """

    _PATTERNS = {
        "equals": lambda v: "^" + v + r"\Z",
        "contains": lambda v: "(?s:.*)" + v + "(?s:.*)",
        "does_not_contain": lambda v: "(?s:.*)" + v + "(?s:.*)",
        "starts_with": lambda v: "^" + v + ".*",
        "ends_with": lambda v: "(?s:.*)" + v + r"\Z",
    }

    @classmethod
    def _oracle(cls, operator, values, entity):
        try:
            if operator in cls._PATTERNS:
                hit = any(re.match(cls._PATTERNS[operator](v), entity) is not None for v in values)
            else:
                hit = any(v in entity for v in values)
        except Exception:
            # An uncompilable pattern denies in production; the oracle must agree on that too.
            return False
        return (not hit) if operator in _NEGATING_OPERATORS else hit

    @pytest.mark.parametrize("operator", _ALL_OPERATORS)
    def test_the_decision_matches_the_raw_value_semantics(self, operator):
        field = _field_for(operator)
        membership = operator in _MEMBERSHIP_OPERATORS
        entities = (
            [["Secret", "locked"], [], ["a,b"], ["a)b"], ["a]"], ["a\nb"], ["zz"], ["(x)"]]
            if membership else
            ["Secret", "zz", "", "a,b", "a)b", "a]", "a\nb", "(x)", "a b"]
        )
        for value, entity in itertools.product(_STRUCTURAL_VALUES + _PLAIN_VALUES, entities):
            for shape in (value, [value], [value, "zz"]):
                values = shape if isinstance(shape, list) else [shape]
                rule = _rule(field, operator, shape)
                got = _decide(rule, **{field: entity})
                want = self._oracle(operator, values, entity)
                assert got is want, (
                    f"{operator} value={shape!r} entity={entity!r}: rule {rule!r} decided {got}, "
                    f"raw-value semantics say {want}")

    def test_a_multi_value_membership_still_matches_each_value(self):
        """POSITIVE CONTROL: the ordinary multi-value criterion keeps working element by element."""
        rule = _rule("tags", "is_one_of", ["locked", "approved"])
        assert _decide(rule, tags=["locked"]) is True
        assert _decide(rule, tags=["approved"]) is True
        assert _decide(rule, tags=["other"]) is False

    def test_the_admin_wildcard_still_grants(self):
        """POSITIVE CONTROL: the seeded admin "allow all databases" constraint is
        operator=contains, value=".*" -- escaping must leave it a wildcard."""
        rule = _rule("databaseId", "contains", ".*")
        assert _decide(rule, databaseId="anything-at-all") is True

    @pytest.mark.parametrize("payload", ["x' or True or 'x",
                                         ".') or True or regexMatch(r.obj.databaseId, '."])
    def test_an_injection_payload_is_still_inert(self, payload):
        """The escaping added here must not reopen the expression-injection hole: the payload stays
        a single literal and grants nothing on an unrelated entity."""
        for operator in ("equals", "contains", "starts_with", "ends_with", "is_one_of"):
            rule = _rule("databaseId", operator, payload)
            assert _decide(rule, databaseId="some-unrelated-database") is False, rule
            assert "\\'" in rule, f"value was not quote-escaped: {rule!r}"


@pytest.mark.unit
class TestRuleTextAssertionInventory:
    """DELETION DETECTOR for the regression sweep. The emitted expression IS the authorization
    behaviour of the product, so the authz tests that pin rule text are load-bearing. The trap when
    a change does move the text is to "fix" those tests by deleting the assertions instead of
    re-anchoring them, which leaves the expression unpinned. This counts the rule-text references
    across the authz tests and requires the total not to shrink; moving them between files is fine.
    """

    MINIMUM_RULE_TEXT_REFERENCES = 14

    def test_the_authz_tests_still_pin_the_emitted_rule_text(self):
        directory = os.path.dirname(os.path.abspath(__file__))
        total = 0
        for path in glob.glob(os.path.join(directory, "test_*.py")):
            with open(path, encoding="utf-8") as handle:
                total += len(re.findall(r"r\.obj\.", handle.read()))
        assert total >= self.MINIMUM_RULE_TEXT_REFERENCES, (
            f"rule-text assertions dropped from {self.MINIMUM_RULE_TEXT_REFERENCES} to {total}; "
            "re-anchor them to the new emitted strings rather than deleting them")
