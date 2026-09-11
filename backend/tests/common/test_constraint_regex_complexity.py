# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A REGEX criterion value must not be able to make every authorization decision backtrack.

`REGEX` is used only on constraint criterion values -- `ConstraintCriteriaModel.validate_criteria_value`
and `CreateConstraintRequestModel.validate_fields` in `models/roleConstraints.py`, and
`validate_substituted_criteria_values` in `handlers/auth/authConstraintsTemplateService.py` -- so what
this validator accepts is exactly what a constraint author can store as a criterion value. That value
is interpolated verbatim into a Casbin
`regexMatch(r.obj.<field>, '^{value}\\Z')` clause -- `_escape_rule_value` escapes only the backslash
and the single quote, deliberately preserving regex semantics -- and the clause is re-evaluated by
`re.match` for every policy line on every `enforce()` call, with no bound other than the Lambda
timeout.

`validate_regex` used to accept anything that compiled, so `(a+)+b` (5 characters, well inside the
pre-existing STRING_256 bound) was storable, and every authorization decision for the affected role
then backtracked exponentially against any entity field carrying a long run of 'a' -- OBJECT_NAME
permits 256 characters, which is ample.

The two shapes that blow up are a repeating quantifier applied to a group that itself repeats, and one
applied to a group that alternates. Alternation is rejected without asking whether the branches
overlap, because that question is not answerable by inspection here (`(a|.)*` overlaps and `(a|b)*`
does not, and the two differ only in a metacharacter): the trade is a deliberately broader rule, and
`TestLegitimateConstraintValuesStillAccepted` is what bounds the cost of it.

OVER-TIGHTENING is the whole risk on this change -- a validator that rejected the operators the shipped
permission templates use would fail every template import and every seeded-constraint round trip. The
accept-side class carries the full set of distinct criterion values found in
`documentation/permissionsTemplates/*.json` and in the seeded admin/read-only constraints in
`infra/lib/nestedStacks/auth/constructs/`.
"""

import json
import pathlib
import sys

import pytest


def _validators():
    """Resolve through `sys.modules` at call time, the way `models/roleConstraints.py` does."""
    return sys.modules['common.validators']


def _regex(value):
    return _validators().validate_regex('criteriaValue', value)


# Every distinct criterion value the shipped permission templates and the seeded admin / read-only
# constraints carry. Literals, one wildcard, and the two template placeholders.
SHIPPED_CRITERION_VALUES = [
    '.*',
    'GLOBAL',
    '{{DATABASE_ID}}',
    '{{TAG_VALUE}}',
    'archiveAsset',
    'archiveFile',
    '/logs',
    '/permanent',
    '/amplify-config',
    '/api/amplify-config',
    '/addon/physna/viewer',
    '/asset-links',
    '/assetIngestion',
    '/assets',
    '/auth/api-keys',
    '/auth/loginProfile',
    '/auth/routes',
    '/auth/tags',
    '/auth/user/api-keys',
    '/buckets',
    '/check-subscription',
    '/comments',
    '/database',
    '/databases',
    '/executions',
    '/ingest-asset',
    '/metadata',
    '/metadataschema',
    '/pipelines',
    '/search',
    '/secure-config',
    '/subscriptions',
    '/tag-types',
    '/tags',
    '/unsubscribe',
    '/upload',
    '/uploads',
    '/workflows',
    '/workflows/executions',
    '/workflows/executions/',
]


@pytest.mark.unit
class TestCatastrophicallyBacktrackingValuesAreRejected:

    @pytest.mark.parametrize('value,why', [
        ('(a+)+b', 'the canonical nested quantifier'),
        ('(a*)*', 'nested star'),
        (r'(\w+)*', 'nested quantifier through a shorthand class'),
        ('((a+))+', 'nested one level deeper -- the inner repeat must propagate outward'),
        ('(?:a+)+', 'a non-capturing group is still a group'),
        ('(?P<run>a+)+', 'a named group is still a group'),
        ('(a{2,})+', 'a braced quantifier counts as repeating'),
        ('(a|a)*$', 'alternation with identical branches under a star'),
        ('(a|ab)+', 'alternation with overlapping branches under a plus'),
        ('(a+)+' * 3, 'repeated occurrences, any one of which is enough'),
    ])
    def test_a_nested_quantifier_is_rejected(self, value, why):
        (valid, message) = _regex(value)
        assert valid is False, f"{value!r} must be rejected ({why})"
        assert 'criteriaValue' in message

    @pytest.mark.parametrize('value', [r'(a)\1', r'(a)\g<1>', '(?P<n>a)(?P=n)'])
    def test_a_backreference_is_rejected(self, value):
        assert _regex(value)[0] is False, f"{value!r} must be rejected"

    def test_an_uncompilable_value_is_still_rejected_with_its_own_message(self):
        """CONTROL that the pre-existing compile check was not replaced by the complexity check."""
        (valid, message) = _regex('(unclosed')
        assert valid is False
        assert 'properly formatted regex' in message

    def test_the_check_is_reachable_through_the_dispatcher(self):
        """The gate has to fire on the path `roleConstraints` actually uses -- the REGEX name -- not
        only on a direct call to `validate_regex`."""
        (valid, _) = _validators().validate({
            'criteriaValue': {'value': '(a+)+b', 'validator': 'REGEX',
                              'allowGlobalKeyword': True}})
        assert valid is False


@pytest.mark.unit
class TestLegitimateConstraintValuesStillAccepted:
    """OVER-TIGHTENING CATCHERS. Every value here is one a shipped template, a seeded constraint, or
    a documented operator depends on; a rejection is an outage on constraint create, constraint
    update, and permission-template import alike."""

    @pytest.mark.parametrize('value', SHIPPED_CRITERION_VALUES)
    def test_a_shipped_criterion_value_is_accepted(self, value):
        (valid, message) = _regex(value)
        assert valid is True, f"{value!r} is a shipped constraint value: {message}"

    @pytest.mark.parametrize('value', [
        '^abc$',                # anchors
        'a|b',                  # top-level alternation, not under a quantifier
        '(x)',                  # an un-quantified group
        '(abc)+',               # a quantified group whose body neither repeats nor alternates
        '(a+)?',                # at-most-once outer quantifier cannot blow up
        '[a-z]+',               # a quantified character class is linear
        r'[a+]+',               # a quantifier inside a class is a literal
        '-_a-zA-Z0-9',          # the character list from the shipped admin constraint
        r'\.glb$',              # an escaped metacharacter
        'abc.def',
        'Turbine Housing v2',   # a name with spaces
        "housing-rev.a_v2",     # ordinary identifier punctuation
    ])
    def test_a_legitimate_pattern_is_accepted(self, value):
        (valid, message) = _regex(value)
        assert valid is True, f"{value!r} must stay accepted: {message}"

    def test_an_escaped_paren_is_not_read_as_a_group(self):
        """A literal '(' in a tag name must not make the following quantifier look like a group
        repeat."""
        assert _regex(r'housing \(rev a\)+')[0] is True

    def test_the_global_keyword_still_short_circuits_in_the_dispatcher(self):
        """CONTROL that the dispatcher path is intact: GLOBAL is accepted by the keyword branch
        before any regex rule runs."""
        assert _validators().validate({
            'criteriaValue': {'value': 'GLOBAL', 'validator': 'REGEX',
                              'allowGlobalKeyword': True}})[0] is True


def _shipped_template_criterion_values():
    """Every criterion value in `documentation/permissionsTemplates/*.json`, read at test time.

    Read from the files rather than transcribed, so a template that adds an operator this validator
    would refuse fails here instead of at the operator's first import.
    """
    templates = (pathlib.Path(__file__).resolve().parents[3]
                 / 'documentation' / 'permissionsTemplates')
    values = []
    files = sorted(templates.glob('*.json'))
    for path in files:
        document = json.loads(path.read_text(encoding='utf-8'))
        for constraint in document.get('constraints', []) or []:
            for key in ('criteriaAnd', 'criteriaOr'):
                for criteria in constraint.get(key, []) or []:
                    value = criteria.get('value')
                    values.extend(value if isinstance(value, list) else [value])
    return files, values


@pytest.mark.unit
class TestTheShippedTemplatesStillImport:
    """The accept side, taken from the shipped files themselves rather than from a transcription.

    `TestLegitimateConstraintValuesStillAccepted` above pins a hand-maintained list; this class is
    what keeps that list honest and what catches a template edit. A value refused here fails every
    `POST /auth/constraints/import` of that template -- the whole permission bootstrap for a new
    deployment -- so the count assertions matter as much as the per-value ones: a glob that resolved
    to the wrong directory would otherwise report success over zero files.
    """

    def test_the_templates_are_found(self):
        files, values = _shipped_template_criterion_values()
        assert len(files) >= 6, f"expected the shipped permission templates, found {files}"
        assert len(values) >= 30, f"expected the templates' criterion values, found {len(values)}"

    def test_every_shipped_criterion_value_is_accepted(self):
        _, values = _shipped_template_criterion_values()
        rejected = [value for value in values if not _regex(value)[0]]
        assert not rejected, f"these shipped template values must stay accepted: {sorted(set(rejected))}"

    def test_the_transcribed_list_still_covers_the_files(self):
        """A value in a template but not in SHIPPED_CRITERION_VALUES means the list above has gone
        stale, which is how the parametrized accept-side class quietly stops covering a real value."""
        _, values = _shipped_template_criterion_values()
        untracked = sorted(set(values) - set(SHIPPED_CRITERION_VALUES))
        assert not untracked, f"add these to SHIPPED_CRITERION_VALUES: {untracked}"
