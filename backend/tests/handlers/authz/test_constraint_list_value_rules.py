# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A list-valued criteria value must become one comparison per element.

``ConstraintCriteriaModel.value`` is ``Union[str, List[str]]`` and ``MAX_CRITERIA_VALUES`` bounds the
list, so a multi-value criterion is a first-class stored shape reachable through the constraint API,
template import, and the CLI. Interpolating the container itself into the rule yields the repr
(``"['locked', 'approved']"``), which no real attribute equals or contains, so:

* an ``is_one_of`` ALLOW silently matches nothing, and
* an ``is_not_one_of`` DENY silently matches everything it was written to exempt, or -- read the other
  way -- an ``is_one_of`` DENY stops denying, which is the fail-OPEN direction.

Both fail with no error, no validation message and no log line, so these tests drive the REAL rule
builder and the REAL Casbin enforcer and assert on the DECISION rather than on the rule text, except
where the rule text is itself the contract (the string-value positive controls).

The escaping guarantee has to survive the expansion: every element is a separate quoted literal, so a
crafted element must not be able to terminate its literal and inject expression syntax.
"""

import pytest
from unittest.mock import patch
from casbin import FastEnforcer, model
from casbin.persist.adapters import string_adapter

from backend.backend.handlers.authz import CasbinEnforcerService
from backend.backend.common.constants import (
    PERMISSION_CONSTRAINT_POLICY,
    PERMISSION_CONSTRAINT_FIELDS,
)

USER_ID = "tester"


def _rules(criteria, object_type=None):
    """Call the real rule generator without __init__ (which would need DynamoDB)."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    return svc._generate_criteria_object_rules(criteria, object_type=object_type)


def _rule(field, operator, value, object_type=None):
    generated = _rules(
        [{"field": field, "operator": operator, "value": value}], object_type=object_type)
    assert len(generated) == 1, f"expected one rule, got {generated}"
    return generated[0]


def _enforcer(obj_rule, effect="allow"):
    policy_text = (
        "g, user::tester, 'role::r'\n"
        f"p, 'role::r', {obj_rule}, GET, {effect}"
    )
    new_model = model.Model()
    new_model.load_model_from_text(PERMISSION_CONSTRAINT_POLICY)
    return FastEnforcer(
        model=new_model, adapter=string_adapter.StringAdapter(policy_text), enable_log=False)


def _decide(obj_rule, effect="allow", **attrs):
    """Run one generated rule through a real Enforcer over a real-shaped asset object."""
    obj = PERMISSION_CONSTRAINT_FIELDS.copy()
    obj["object__type"] = "asset"
    obj.update(attrs)
    try:
        return _enforcer(obj_rule, effect).enforce("user::tester", obj, "GET")
    except Exception:
        # An unevaluable expression fails closed in production (enforce() catches and denies);
        # mirror that here so a malformed rule reads as a denial rather than an error.
        return False


def _constraint(constraint_id, object_type="asset", criteria_and=None, criteria_or=None,
                group_perms=None):
    return {
        "constraintId": constraint_id,
        "objectType": object_type,
        "criteriaAnd": criteria_and or [],
        "criteriaOr": criteria_or or [],
        "groupPermissions": group_perms or [],
        "userPermissions": [],
    }


def _service(policies, roles=("roleA",)):
    """A service running the REAL policy-text generation and REAL enforce() wrapper."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = USER_ID
    svc._mfaEnabled = True
    user_roles = [{"userId": USER_ID, "roleName": role} for role in roles]
    with patch.object(CasbinEnforcerService, "_read_current_user_roles_from_table",
                      return_value=user_roles), \
         patch.object(CasbinEnforcerService, "_read_policies_batch_optimized",
                      return_value=policies):
        policy_text = svc._create_policy_text_helper()
    new_model = model.Model()
    new_model.load_model_from_text(PERMISSION_CONSTRAINT_POLICY)
    svc._enforcer = FastEnforcer(
        model=new_model, adapter=string_adapter.StringAdapter(policy_text), enable_log=False)
    return svc


def _asset(svc, action="GET", **attrs):
    obj = {"object__type": "asset"}
    obj.update(attrs)
    return svc.enforce(obj, action)


@pytest.mark.unit
class TestListValuedMembershipMatchesEveryElement:
    """``is_one_of`` over a list must match an attribute carrying ANY listed value."""

    @pytest.mark.parametrize("tag", ["locked", "approved"])
    def test_allow_grants_an_asset_tagged_with_any_listed_value(self, tag):
        rule = _rule("tags", "is_one_of", ["locked", "approved"])
        assert _decide(rule, tags=[tag]) is True

    def test_allow_does_not_grant_an_unlisted_tag(self):
        """The other half: expansion must not degenerate into a match-everything rule."""
        rule = _rule("tags", "is_one_of", ["locked", "approved"])
        assert _decide(rule, tags=["public"]) is False
        assert _decide(rule, tags=[]) is False

    def test_allow_grants_when_a_listed_value_sits_beside_others(self):
        rule = _rule("tags", "is_one_of", ["locked", "approved"])
        assert _decide(rule, tags=["public", "approved", "draft"]) is True

    @pytest.mark.parametrize("tag", ["secret", "classified"])
    def test_deny_blocks_every_listed_value(self, tag):
        """THE VOIDED DENY. A `tags is_one_of [...]` deny is the shipped deny-tagged-assets shape
        widened to several tags; every listed tag must be blocked."""
        rule = _rule("tags", "is_one_of", ["secret", "classified"])
        assert _decide(rule, effect="allow", tags=[tag]) is True, (
            "positive control: the same rule as an ALLOW matches, so a False below is the deny "
            "firing rather than the rule matching nothing")
        assert _decide(rule, effect="deny", tags=[tag]) is False

    def test_deny_leaves_an_unlisted_tag_alone(self):
        rule = _rule("tags", "is_one_of", ["secret", "classified"])
        # A deny that does not match yields no allow either, so the default-deny result is False;
        # assert through a matching allow so the deny's non-participation is observable.
        svc = _service([
            _constraint("A", criteria_and=[{"field": "databaseId", "operator": "equals",
                                            "value": "db1"}],
                        group_perms=[{"groupId": "roleA", "permission": "GET",
                                      "permissionType": "allow"}]),
            _constraint("D", criteria_and=[{"field": "tags", "operator": "is_one_of",
                                            "value": ["secret", "classified"]}],
                        group_perms=[{"groupId": "roleA", "permission": "GET",
                                      "permissionType": "deny"}]),
        ])
        assert _asset(svc, databaseId="db1", tags=["public"]) is True
        assert _asset(svc, databaseId="db1", tags=["secret"]) is False
        assert _asset(svc, databaseId="db1", tags=["classified"]) is False
        assert rule  # the directly generated rule is the same shape the service emitted


@pytest.mark.unit
class TestListValuedNegatedMembership:
    """``is_not_one_of`` over a list must exclude EVERY listed value, not just the container."""

    @pytest.mark.parametrize("tag", ["secret", "classified"])
    def test_allow_gated_by_is_not_one_of_excludes_every_listed_value(self, tag):
        """THE OVER-GRANT. An ALLOW written as `tags is_not_one_of [secret, classified]` admitted
        exactly the assets it was written to exclude."""
        rule = _rule("tags", "is_not_one_of", ["secret", "classified"])
        assert _decide(rule, tags=["public"]) is True, (
            "positive control: an unlisted tag is still granted")
        assert _decide(rule, tags=[tag]) is False

    def test_a_listed_value_cannot_be_slipped_past_by_pairing_it_with_another(self):
        """The negation wraps the WHOLE alternation, so one listed tag is enough to exclude."""
        rule = _rule("tags", "is_not_one_of", ["secret", "classified"])
        assert _decide(rule, tags=["public", "secret"]) is False
        assert _decide(rule, tags=["secret", "classified"]) is False

    def test_deny_gated_by_is_not_one_of_blocks_the_unlisted_asset(self):
        rule = _rule("tags", "is_not_one_of", ["secret", "classified"])
        assert _decide(rule, effect="deny", tags=["public"]) is False
        # A listed tag does not satisfy the deny, so the deny does not fire.
        svc = _service([
            _constraint("A", criteria_and=[{"field": "databaseId", "operator": "equals",
                                            "value": "db1"}],
                        group_perms=[{"groupId": "roleA", "permission": "GET",
                                      "permissionType": "allow"}]),
            _constraint("D", criteria_and=[{"field": "tags", "operator": "is_not_one_of",
                                            "value": ["secret", "classified"]}],
                        group_perms=[{"groupId": "roleA", "permission": "GET",
                                      "permissionType": "deny"}]),
        ])
        assert _asset(svc, databaseId="db1", tags=["secret"]) is True
        assert _asset(svc, databaseId="db1", tags=["public"]) is False


@pytest.mark.unit
class TestListValuedRegexOperatorsOnSingleValuedFields:
    """The list-valued shape is accepted on a single-valued field too, so the pattern-matching
    operators must expand as well. ``ConstraintCriteriaModel`` only refuses them on a LIST FIELD
    (``tags``); ``databaseId equals ["db1", "db2"]`` stores cleanly."""

    @pytest.mark.parametrize("database_id", ["db-alpha", "db-beta"])
    def test_equals_over_a_list_matches_each_alternative(self, database_id):
        rule = _rule("databaseId", "equals", ["db-alpha", "db-beta"])
        assert _decide(rule, databaseId=database_id) is True

    def test_equals_over_a_list_stays_exact(self):
        rule = _rule("databaseId", "equals", ["db-alpha", "db-beta"])
        assert _decide(rule, databaseId="db-gamma") is False
        assert _decide(rule, databaseId="db-alphax") is False
        # The '\Z' anchor must survive the expansion, so a trailing newline is still a distinct value.
        assert _decide(rule, databaseId="db-alpha\n") is False

    @pytest.mark.parametrize("asset_name", ["xSecrety", "xDrafty"])
    def test_contains_over_a_list_matches_each_alternative(self, asset_name):
        rule = _rule("assetName", "contains", ["Secret", "Draft"])
        assert _decide(rule, assetName=asset_name) is True

    def test_does_not_contain_over_a_list_excludes_every_alternative(self):
        """The negation must cover the whole alternation: a name containing either value is out."""
        rule = _rule("assetName", "does_not_contain", ["Secret", "Draft"])
        assert _decide(rule, assetName="PublicModel") is True, "positive control: clean name granted"
        assert _decide(rule, assetName="mySecretFile") is False
        assert _decide(rule, assetName="myDraftFile") is False
        assert _decide(rule, assetName="mySecretDraft") is False
        # The newline-crossing wildcard must survive the expansion (an under-matching deny is a
        # bypass), for every alternative.
        assert _decide(rule, assetName="pre\nSecret") is False
        assert _decide(rule, assetName="pre\nDraft") is False

    @pytest.mark.parametrize("database_id", ["proj-x", "team-y"])
    def test_starts_with_over_a_list_matches_each_alternative(self, database_id):
        rule = _rule("databaseId", "starts_with", ["proj-", "team-"])
        assert _decide(rule, databaseId=database_id) is True
        assert _decide(rule, databaseId="other-z") is False

    @pytest.mark.parametrize("database_id", ["a-prod", "b-stage"])
    def test_ends_with_over_a_list_matches_each_alternative(self, database_id):
        rule = _rule("databaseId", "ends_with", ["-prod", "-stage"])
        assert _decide(rule, databaseId=database_id) is True
        assert _decide(rule, databaseId="a-dev") is False

    def test_ends_with_over_a_list_keeps_the_true_end_anchor(self):
        rule = _rule("databaseId", "ends_with", ["-prod", "-stage"])
        assert _decide(rule, databaseId="a-prod\n") is False


@pytest.mark.unit
class TestSingleStringValuesAreUnchanged:
    """POSITIVE CONTROL for the whole change. The shipped ``deny-tagged-assets.json`` template and
    every seeded constraint use a single STRING value, so the emitted rule text for a scalar must be
    exactly what it was -- the expansion is additive."""

    @pytest.mark.parametrize("operator,expected", [
        ("equals", "regexMatch(r.obj.assetName, '^Secret\\\\Z')"),
        ("contains", "regexMatch(r.obj.assetName, '(?s:.*)Secret(?s:.*)')"),
        ("does_not_contain", "!(regexMatch(r.obj.assetName, '(?s:.*)Secret(?s:.*)'))"),
        ("starts_with", "regexMatch(r.obj.assetName, '^Secret.*')"),
        ("ends_with", "regexMatch(r.obj.assetName, '(?s:.*)Secret\\\\Z')"),
    ])
    def test_scalar_regex_operator_rule_text_is_exact(self, operator, expected):
        assert _rule("assetName", operator, "Secret") == expected

    def test_scalar_membership_rule_text_is_a_bare_membership_test(self):
        assert _rule("tags", "is_one_of", "locked") == "'locked' in r.obj.tags"

    def test_scalar_negated_membership_scopes_the_negation_to_the_membership_test(self):
        """The negation must apply to the membership test and nothing else, which is what the
        parentheses state explicitly."""
        rule = _rule("tags", "is_not_one_of", "locked")
        assert rule == "!('locked' in r.obj.tags)"
        assert _decide(rule, tags=["public"]) is True
        assert _decide(rule, tags=["locked"]) is False

    def test_a_single_element_list_emits_the_same_rule_as_the_scalar(self):
        """A one-element list is the scalar case, so it must not acquire a wrapper group."""
        for operator in ("equals", "contains", "starts_with", "ends_with", "is_one_of"):
            assert _rule("assetName", operator, ["Secret"]) == _rule(
                "assetName", operator, "Secret"), operator
        for operator in ("does_not_contain", "is_not_one_of"):
            assert _rule("assetName", operator, ["Secret"]) == _rule(
                "assetName", operator, "Secret"), operator

    def test_the_shipped_deny_tagged_assets_criterion_still_denies(self):
        """End-to-end through the real policy-text path with the template's scalar value."""
        svc = _service([
            _constraint("A", criteria_and=[{"field": "databaseId", "operator": "contains",
                                            "value": ".*"}],
                        group_perms=[{"groupId": "roleA", "permission": "PUT",
                                      "permissionType": "allow"}]),
            _constraint("D", criteria_and=[{"field": "tags", "operator": "is_one_of",
                                            "value": "locked"}],
                        group_perms=[{"groupId": "roleA", "permission": "PUT",
                                      "permissionType": "deny"}]),
        ])
        assert _asset(svc, action="PUT", databaseId="db1", tags=["public"]) is True
        assert _asset(svc, action="PUT", databaseId="db1", tags=["locked"]) is False

    def test_the_admin_contains_wildcard_still_matches_everything(self):
        rule = _rule("databaseId", "contains", ".*")
        for database_id in ("anything", "x\ny", ""):
            assert _decide(rule, databaseId=database_id) is True, database_id


@pytest.mark.unit
class TestEscapingSurvivesTheExpansion:
    """Each element gets its own quoted literal, so each element must be escaped."""

    _PAYLOAD = "x' or True or 'x"

    def test_an_injecting_element_does_not_grant_arbitrary_access(self):
        rule = _rule("databaseId", "is_one_of", ["safe-db", self._PAYLOAD])
        assert _decide(rule, databaseId="some-database-the-author-never-named") is False

    def test_every_element_is_escaped_not_only_the_first(self):
        rule = _rule("databaseId", "is_one_of", ["safe-db", self._PAYLOAD])
        assert rule.count("\\'") == 2, f"payload element was not escaped: {rule!r}"

    def test_an_injecting_element_cannot_force_a_negated_rule_to_a_constant(self):
        rule = _rule("databaseId", "is_not_one_of", ["safe-db", self._PAYLOAD])
        # Inert literal: an unrelated database is not a member, so the negation is True -- but a
        # LISTED value must still be excluded, which a constant-True expression could not do.
        assert _decide(rule, databaseId="unrelated") is True
        assert _decide(rule, databaseId="safe-db") is False

    def test_a_regex_breakout_element_is_neutralized(self):
        payload = ".') or True or regexMatch(r.obj.assetName, '."
        rule = _rule("databaseId", "equals", ["db-alpha", payload])
        assert _decide(rule, databaseId="db-alpha") is True, "positive control: literal alternative"
        assert _decide(rule, databaseId="any-other-database") is False

    def test_every_expanded_rule_is_a_valid_expression(self):
        """A malformed group would make the enforcer deny EVERYTHING for the role, so the emitted
        text has to parse and evaluate for every operator."""
        for operator in ("equals", "contains", "does_not_contain", "starts_with", "ends_with",
                         "is_one_of", "is_not_one_of"):
            rule = _rule("databaseId", operator, ["db-alpha", "db-beta"])
            enforcer = _enforcer(rule)
            obj = PERMISSION_CONSTRAINT_FIELDS.copy()
            obj["object__type"] = "asset"
            obj["databaseId"] = "db-alpha"
            # Raises rather than returning a bool if the expression is malformed.
            assert isinstance(enforcer.enforce("user::tester", obj, "GET"), bool), operator


@pytest.mark.unit
class TestTheExpandedGroupIsIsolatedInsideTheCombinedRule:
    """``_create_policy_text_helper`` joins the AND criteria with ``&&`` and wraps the OR criteria in
    one ``(... || ...)``. Casbin rewrites ``&&``/``||`` to Python ``and``/``or``, where ``and`` binds
    TIGHTER than ``or`` -- so an unparenthesized multi-value alternation would re-associate as
    ``(sibling and first) or rest`` and grant on ``rest`` alone, ignoring every sibling criterion.
    These tests go through the REAL policy-text generation so the joining is the production one."""

    def test_a_multi_value_and_criterion_does_not_escape_its_sibling_criteria(self):
        svc = _service([
            _constraint("A", criteria_and=[{"field": "databaseId", "operator": "equals",
                                            "value": "db1"},
                                           {"field": "assetType", "operator": "equals",
                                            "value": [".glb", ".gltf"]}],
                        group_perms=[{"groupId": "roleA", "permission": "GET",
                                      "permissionType": "allow"}]),
        ])
        assert _asset(svc, databaseId="db1", assetType=".glb") is True
        assert _asset(svc, databaseId="db1", assetType=".gltf") is True
        assert _asset(svc, databaseId="db1", assetType=".exe") is False
        # The escape: a matching LATER alternative must not carry the rule past the databaseId AND.
        assert _asset(svc, databaseId="db2", assetType=".gltf") is False
        assert _asset(svc, databaseId="db2", assetType=".glb") is False

    def test_a_multi_value_deny_criterion_does_not_escape_its_sibling_criteria(self):
        """Same trap on a DENY, where re-association denies assets the constraint never described."""
        svc = _service([
            _constraint("A", criteria_and=[{"field": "databaseId", "operator": "contains",
                                            "value": ".*"}],
                        group_perms=[{"groupId": "roleA", "permission": "GET",
                                      "permissionType": "allow"}]),
            _constraint("D", criteria_and=[{"field": "databaseId", "operator": "equals",
                                            "value": "db1"},
                                           {"field": "tags", "operator": "is_one_of",
                                            "value": ["secret", "classified"]}],
                        group_perms=[{"groupId": "roleA", "permission": "GET",
                                      "permissionType": "deny"}]),
        ])
        assert _asset(svc, databaseId="db1", tags=["secret"]) is False
        assert _asset(svc, databaseId="db1", tags=["classified"]) is False
        assert _asset(svc, databaseId="db1", tags=["public"]) is True
        # db2 is outside the deny's AND, so a listed tag there must remain granted.
        assert _asset(svc, databaseId="db2", tags=["classified"]) is True

    def test_a_multi_value_criterion_inside_the_or_group_stays_one_alternative(self):
        svc = _service([
            _constraint("A", criteria_and=[{"field": "databaseId", "operator": "equals",
                                            "value": "db1"}],
                        criteria_or=[{"field": "tags", "operator": "is_one_of",
                                      "value": ["approved", "released"]},
                                     {"field": "assetName", "operator": "starts_with",
                                      "value": "override-"}],
                        group_perms=[{"groupId": "roleA", "permission": "GET",
                                      "permissionType": "allow"}]),
        ])
        assert _asset(svc, databaseId="db1", tags=["approved"]) is True
        assert _asset(svc, databaseId="db1", tags=["released"]) is True
        assert _asset(svc, databaseId="db1", tags=[], assetName="override-x") is True
        assert _asset(svc, databaseId="db1", tags=["draft"], assetName="model") is False
        # The AND group must still gate the whole rule for every alternative.
        assert _asset(svc, databaseId="db2", tags=["released"]) is False


@pytest.mark.unit
class TestEmptyValueList:
    """An empty list carries no alternative. The positive operators must then match nothing
    (fail-closed on an allow) rather than emit a syntactically broken group that denies the role
    everything."""

    def test_is_one_of_over_an_empty_list_matches_nothing(self):
        assert _decide(_rule("tags", "is_one_of", []), tags=["locked"]) is False

    def test_is_not_one_of_over_an_empty_list_excludes_nothing(self):
        assert _decide(_rule("tags", "is_not_one_of", []), tags=["locked"]) is True

    def test_an_empty_list_does_not_break_the_rest_of_the_policy(self):
        """The empty criterion is joined into the same AND expression as its siblings, so a broken
        group there would take the whole constraint down."""
        svc = _service([
            _constraint("A", criteria_and=[{"field": "databaseId", "operator": "equals",
                                            "value": "db1"},
                                           {"field": "tags", "operator": "is_not_one_of",
                                            "value": []}],
                        group_perms=[{"groupId": "roleA", "permission": "GET",
                                      "permissionType": "allow"}]),
        ])
        assert _asset(svc, databaseId="db1", tags=["anything"]) is True
        assert _asset(svc, databaseId="db2", tags=["anything"]) is False
