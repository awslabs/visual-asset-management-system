# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A permission-template import is a constraint save, so the value it stores is checked.

`ConstraintCriteriaModel` checks a criterion value when the request is parsed, which covers
`POST`/`PUT /auth/constraints`. The template import is different: the value that reaches DynamoDB is
produced AFTER parsing, by substituting caller-supplied `variableValues` into the template body. A
template whose criterion is `{{DATABASE_ID}}` therefore parses clean whatever the caller passes for
`DATABASE_ID` -- and the substituted result is what becomes the Casbin `regexMatch(...)` pattern
re-evaluated for every policy line on every authorization decision.

Checking the submitted body and not the stored value is the gap this file covers. The accept side is
the shipped templates themselves, substituted the way an operator would: refusing one of those would
fail the permission bootstrap of a new deployment, which is a worse outcome than the defect.
"""

import json
import pathlib

import pytest

from backend.backend.handlers.auth import authConstraintsTemplateService as svc  # noqa: E402

# What an operator supplies when importing the shipped templates.
ORDINARY_VARIABLE_VALUES = {
    'ROLE_NAME': 'my-database-admin',
    'DATABASE_ID': 'my-database',
    'TAG_VALUE': 'confidential',
}

# The two shapes Option A refuses: a repeating quantifier over a group that itself repeats, and a
# backreference. Everything else a criterion may use stays accepted.
CATASTROPHIC_VALUE = '(a+)+b'


def _template_files():
    templates = (pathlib.Path(__file__).resolve().parents[4]
                 / 'documentation' / 'permissionsTemplates')
    return sorted(templates.glob('*.json'))


def _constraints_of(path):
    document = json.loads(path.read_text(encoding='utf-8'))
    return [
        {
            'name': constraint['name'],
            'description': constraint['description'],
            'objectType': constraint['objectType'],
            'criteriaAnd': constraint.get('criteriaAnd', []),
            'criteriaOr': constraint.get('criteriaOr', []),
            'groupPermissions': constraint.get('groupPermissions', []),
        }
        for constraint in document.get('constraints', []) or []
    ]


@pytest.mark.unit
class TestASubstitutedValueIsChecked:

    def test_a_catastrophic_value_arriving_through_a_variable_is_refused(self):
        """The attack the parse-time check cannot see: the template body is a placeholder, and the
        pattern is supplied as the variable's value."""
        constraints = [{
            'name': 'db-admin', 'description': 'd', 'objectType': 'asset',
            'criteriaAnd': [{'field': 'databaseId', 'operator': 'equals',
                             'value': '{{DATABASE_ID}}'}],
            'criteriaOr': [], 'groupPermissions': [],
        }]
        substituted = svc.substitute_variables(
            constraints, {'ROLE_NAME': 'r', 'DATABASE_ID': CATASTROPHIC_VALUE})
        # The substitution really did produce the pattern -- otherwise the assertion below would pass
        # for the wrong reason.
        assert substituted[0]['criteriaAnd'][0]['value'] == CATASTROPHIC_VALUE
        with pytest.raises(svc.VAMSGeneralErrorResponse):
            svc.validate_substituted_criteria_values(substituted)

    def test_a_catastrophic_value_in_a_list_valued_criterion_is_refused(self):
        """Each element of a list-valued criterion becomes its own clause in the emitted rule."""
        substituted = [{
            'criteriaAnd': [], 'criteriaOr': [
                {'field': 'tags', 'operator': 'is_one_of', 'value': ['ok', CATASTROPHIC_VALUE]}],
        }]
        with pytest.raises(svc.VAMSGeneralErrorResponse):
            svc.validate_substituted_criteria_values(substituted)

    def test_a_backreference_is_refused(self):
        substituted = [{
            'criteriaAnd': [{'field': 'databaseId', 'operator': 'equals', 'value': r'(a)\1'}],
            'criteriaOr': [],
        }]
        with pytest.raises(svc.VAMSGeneralErrorResponse):
            svc.validate_substituted_criteria_values(substituted)

    def test_the_check_runs_inside_the_import(self):
        """Wired into the import, not merely available: a helper nothing calls guards nothing."""
        import inspect
        source = inspect.getsource(svc.import_template_constraints)
        assert 'validate_substituted_criteria_values(substituted_constraints)' in source


@pytest.mark.unit
class TestTheShippedTemplatesStillImport:
    """OVER-TIGHTENING CATCHERS, run over the real files."""

    def test_the_templates_are_found(self):
        files = _template_files()
        assert len(files) >= 6, f"expected the shipped permission templates, found {files}"

    @pytest.mark.parametrize('path', _template_files(), ids=lambda path: path.name)
    def test_a_shipped_template_passes_after_substitution(self, path):
        constraints = _constraints_of(path)
        assert constraints, f"{path.name} carries no constraints -- check the fixture, not the rule"
        substituted = svc.substitute_variables(constraints, ORDINARY_VARIABLE_VALUES)
        # No exception: every criterion value the template stores is a value a constraint may hold.
        svc.validate_substituted_criteria_values(substituted)

    def test_a_criterion_with_no_value_key_is_skipped_rather_than_rejected(self):
        """The helper walks caller-shaped dicts, so a missing or non-string value must not raise
        from the walk itself -- the model's own rules answer that case."""
        svc.validate_substituted_criteria_values([{'criteriaAnd': [{'field': 'databaseId'}],
                                                   'criteriaOr': None}])
