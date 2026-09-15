# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A criterion value's complexity is checked when a constraint is saved, and only then.

The rule that refuses a catastrophically backtracking criterion value belongs on the write path: the
value becomes a Casbin `regexMatch(...)` pattern re-evaluated for every policy line on every
authorization decision. It must NOT be applied when a constraint is read back. Constraints already in
the table were written before the rule existed, and a row that fails the response model does not
disappear -- the handler falls back to the raw DynamoDB item, whose `criteriaAnd`/`criteriaOr` are JSON
strings rather than objects, so the listing changes shape for the caller instead of failing loudly.
That is the backwards-compatibility requirement: keep the stored constraints working, check them on the
next save.

The split is carried by two models -- `ConstraintCriteriaModel` on every request path,
`ConstraintCriteriaResponseModel` on the read path -- so it is a property of the code and not of a
handler remembering which one to use.
"""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from models.roleConstraints import (
    ConstraintCriteriaModel,
    ConstraintCriteriaResponseModel,
    ConstraintResponseModel,
    CreateConstraintRequestModel,
    TemplateConstraintDefinition,
)

CATASTROPHIC = '(a+)+b'          # a repeating quantifier over a group that itself repeats
BACKREFERENCE = r'(a)\1'
SHIPPED = '.*'                    # the wildcard every shipped permission template uses


def _criterion(value):
    return {'field': 'databaseId', 'operator': 'equals', 'value': value}


def _create_request(value):
    return {
        'identifier': 'my-constraint', 'name': 'my-constraint', 'description': 'd',
        'objectType': 'asset', 'criteriaAnd': [_criterion(value)], 'criteriaOr': [],
        'groupPermissions': [], 'userPermissions': [],
    }


def _stored_row(value):
    return {
        'constraintId': 'my-constraint#group#my-role', 'name': 'my-constraint',
        'description': 'd', 'objectType': 'asset',
        'criteriaAnd': [_criterion(value)], 'criteriaOr': [],
        'groupPermissions': [], 'userPermissions': [],
    }


@pytest.mark.unit
class TestSavingChecksTheValue:

    @pytest.mark.parametrize('value', [CATASTROPHIC, BACKREFERENCE])
    def test_a_save_is_refused(self, value):
        with pytest.raises(ValidationError) as raised:
            CreateConstraintRequestModel(**_create_request(value))
        assert 'Cannot repeat a group' in str(raised.value)

    @pytest.mark.parametrize('value', [CATASTROPHIC, BACKREFERENCE])
    def test_the_criteria_model_itself_is_what_refuses_it(self, value):
        """Named explicitly: this is the model both the constraint route and the template import
        parse through, so it is where the write-time rule covers every request. The importer
        additionally re-checks the value substitution produces, which is the one a parse cannot see."""
        with pytest.raises(ValidationError):
            ConstraintCriteriaModel(**_criterion(value))

    def test_a_template_definition_is_refused_too(self):
        with pytest.raises(ValidationError):
            TemplateConstraintDefinition(name='n', description='d', objectType='asset',
                                         criteriaAnd=[_criterion(CATASTROPHIC)],
                                         criteriaOr=[], groupPermissions=[])

    def test_a_shipped_value_still_saves(self):
        """OVER-TIGHTENING CATCHER: refusing this fails every permission-template import."""
        model = CreateConstraintRequestModel(**_create_request(SHIPPED))
        assert model.criteriaAnd[0].value == SHIPPED


@pytest.mark.unit
class TestReadingDoesNotCheckTheValue:

    @pytest.mark.parametrize('value', [CATASTROPHIC, BACKREFERENCE, 'x' * 400])
    def test_a_constraint_stored_before_the_rule_reads_back_as_stored(self, value):
        model = ConstraintResponseModel(**_stored_row(value))
        assert model.criteriaAnd[0].value == value

    def test_the_response_criteria_model_carries_no_value_rule(self):
        assert ConstraintCriteriaResponseModel(**_criterion(CATASTROPHIC)).value == CATASTROPHIC

    def test_the_response_model_is_not_the_request_model(self):
        """CONTROL for the whole file. If the read path ever points back at the write model, the
        assertions above start failing -- and this one names why."""
        assert (ConstraintResponseModel.__fields__['criteriaAnd'].type_
                is ConstraintCriteriaResponseModel)
        assert ConstraintCriteriaResponseModel is not ConstraintCriteriaModel

    def test_the_response_model_still_requires_the_criteria_shape(self):
        """CONTROL: 'no value rule' is not 'no model'. A row missing a criterion field is still a
        malformed row and still falls back to the raw item."""
        with pytest.raises(ValidationError):
            ConstraintCriteriaResponseModel(field='databaseId', operator='equals')

    def test_a_stored_list_valued_criterion_reads_back(self):
        model = ConstraintResponseModel(**_stored_row(['tag-one', CATASTROPHIC]))
        assert model.criteriaAnd[0].value == ['tag-one', CATASTROPHIC]
