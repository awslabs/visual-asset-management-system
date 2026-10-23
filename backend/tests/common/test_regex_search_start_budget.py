# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A compliance rule pattern is searched, so its budget counts the start positions the search tries.

The evaluator runs every `fileStructure`, `versionCoverage` and `assetAttributes` pattern with
`re.search` (`common/compliance/evaluationEngine.py`). A pattern that is not anchored at the start is
retried at every position of the value, which divides the subject like one more unbounded quantifier:
`.*a.*az` estimates comb(1024, 2) = 523,776 as a match but takes 1.7 s searched against a
1024-character key (comb(1024, 3) = 1.8e8), and `'a?' * 23 + 'b'`, accepted as a name pattern at
8.4e6, takes 37 s searched against 256 characters. A Casbin criterion is matched with the anchors the
rule builder adds, so its budget is unchanged.

[in-dev D10] Found 2026-10-20 in round 11b on feature/fmm-compliance @ 0f913495b; compliance code
exists only on this branch.
"""

import importlib.util
import pathlib
import re
import sys
import time

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from models.common import validation_error_message
from models.compliance import AssetAttributeCheck, FileStructureCheck, VersionCoverageCheck

TWO_UNBOUNDED = ".*a.*az"
THREE_UNBOUNDED = ".*a.*a.*az"
OPTIONAL_CHAIN = "a?" * 23 + "b"


def _validators():
    return sys.modules['common.validators']


def _anchored_at_start(pattern):
    spec = importlib.util.spec_from_file_location(
        '_shipped_common_validators_search_start',
        str(pathlib.Path(__file__).resolve().parents[2] / 'backend' / 'common' / 'validators.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._anchored_at_start(pattern)


def _message(model, **fields):
    with pytest.raises(ValidationError) as raised:
        model(**fields)
    return validation_error_message(raised.value)


@pytest.mark.unit
class TestASearchedPatternCountsItsStartPositions:

    def test_an_unanchored_pattern_costs_one_more_quantifier(self):
        v = _validators()
        assert v.validate_regex('p', TWO_UNBOUNDED, subject_length=1024)[0] is True
        assert v.validate_regex('p', TWO_UNBOUNDED, subject_length=1024, searched=True)[0] is False

    def test_an_anchored_pattern_costs_what_a_match_does(self):
        assert _validators().validate_regex(
            'p', '^' + TWO_UNBOUNDED, subject_length=1024, searched=True)[0] is True

    def test_a_bounded_chain_is_multiplied_by_the_start_positions(self):
        v = _validators()
        assert v.validate_regex('p', OPTIONAL_CHAIN, searched=True)[0] is False
        assert v.validate_regex('p', '^' + OPTIONAL_CHAIN, searched=True)[0] is True

    def test_a_criterion_value_is_not_searched(self):
        """Casbin criteria name `REGEX` with no modifier: the 256-character match budget."""
        v = _validators()
        assert v.validate_regex('criteriaValue', THREE_UNBOUNDED)[0] is True
        assert v.validate(
            {'criteriaValue': {'value': THREE_UNBOUNDED, 'validator': 'REGEX'}})[0] is True

    def test_searched_in_the_spec_is_applied_and_must_be_a_bool(self):
        v = _validators()
        assert v.validate({'p': {'value': THREE_UNBOUNDED, 'validator': 'REGEX',
                                 'searched': True}})[0] is False
        with pytest.raises(Exception, match='searched field in validator for p field'):
            v.validate({'p': {'value': 'abc', 'validator': 'REGEX', 'searched': 'yes'}})


@pytest.mark.unit
class TestAnchoring:

    @pytest.mark.parametrize('pattern', [
        '^models/', r'\Amodels/', '(?i)^(tbd|todo)$', '(?i)(?s)^a.*b', r'^a\|b', '^[|]x',
        '^[]|]x', '^[^]|]x', '^(a|b)/.*',
    ])
    def test_anchored(self, pattern):
        assert _anchored_at_start(pattern) is True

    @pytest.mark.parametrize('pattern,why', [
        (r'\.glb$', 'no start anchor'),
        ('(^|/)x', 'the anchor is inside an alternation'),
        ('^a|.*b', 'a top-level alternative starts anywhere'),
        ('(?m)^a', "MULTILINE '^' matches after every newline"),
        ('(?x)^a # (\n|.*b', 'VERBOSE comments are not read'),
        ('^[](]|.*b', "a leading ']' is a class literal, so '(' is inside the class"),
        ('[^a]x', "a negated class is not an anchor"),
    ])
    def test_not_anchored(self, pattern, why):
        assert _anchored_at_start(pattern) is False, why


@pytest.mark.unit
class TestRulePatternsAreSearched:

    @pytest.mark.parametrize('model,field', [
        (FileStructureCheck, 'requiredPaths'),
        (FileStructureCheck, 'forbiddenPaths'),
        (VersionCoverageCheck, 'excludePaths'),
    ])
    def test_an_unanchored_path_pattern_admits_one_unbounded_quantifier(self, model, field):
        model(name='c', **{field: [r'.*\.glb$']})
        model(name='c', **{field: ['^' + TWO_UNBOUNDED]})
        message = _message(model, name='c', **{field: [TWO_UNBOUNDED]})
        assert message.startswith(f'{field} is invalid.')
        assert TWO_UNBOUNDED not in message

    @pytest.mark.parametrize('key', ['matches', 'notMatches'])
    def test_an_unanchored_name_pattern_admits_two(self, key):
        AssetAttributeCheck(name='c', field='assetName', **{key: TWO_UNBOUNDED})
        AssetAttributeCheck(name='c', field='assetName', **{key: '^' + THREE_UNBOUNDED})
        assert _message(AssetAttributeCheck, name='c', field='description',
                        **{key: THREE_UNBOUNDED}).startswith(f'{key} is invalid.')
        assert _message(AssetAttributeCheck, name='c', field='description',
                        **{key: OPTIONAL_CHAIN}).startswith(f'{key} is invalid.')


@pytest.mark.unit
class TestTheSearchedWorstCaseIsActuallyFast:
    """The budget asserted in wall clock with the evaluator's own call, `re.search`."""

    @staticmethod
    def _search(pattern, subject):
        started = time.perf_counter()
        re.search(pattern, subject)
        return time.perf_counter() - started

    @pytest.mark.parametrize('pattern,length', [
        ('.*az', 1024),                    # the unanchored path pattern the budget admits
        ('^' + TWO_UNBOUNDED, 1024),       # the anchored path pattern the budget admits
        (TWO_UNBOUNDED, 256),              # the unanchored name pattern the budget admits
    ])
    def test_an_accepted_pattern_searches_quickly(self, pattern, length):
        assert self._search(pattern, 'a' * length) < 1.0

    def test_the_rejected_unanchored_path_pattern_is_the_slow_one(self):
        """POSITIVE CONTROL: the pattern this rule newly rejects is measurably expensive searched
        against a 1024-character key."""
        elapsed = self._search(TWO_UNBOUNDED, 'a' * 1024)
        assert elapsed > 0.1, (
            f"{TWO_UNBOUNDED!r} took only {elapsed:.3f}s searched against 1024 characters, so this "
            "control no longer demonstrates the hazard")
