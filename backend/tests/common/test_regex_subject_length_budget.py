# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The backtracking budget is computed for the longest subject a pattern runs against.

`validate_regex` estimates a pattern's worst-case search space against a subject of
`MAX_REGEX_SUBJECT_LENGTH` (256) characters, the bound of every value a Casbin constraint criterion
is compared with. A compliance path pattern (`requiredPaths`, `forbiddenPaths`, `excludePaths`) is
searched in a file's S3 key, which may be `MAX_S3_OBJECT_KEY_BYTES` (1024) long. Unbounded
quantifiers contribute comb(subject_length, k), so a pattern safe at 256 is not necessarily safe at
1024: three unbounded quantifiers separated by literals estimate 2.8e6 at 256 and 1.8e8 at 1024
(measured 1.8 s against a 1024-character run), against a budget of 1e7. Path patterns are therefore
checked at 1024, carried as `subjectLength` in the `REGEX` spec; a criterion value and a name or
description pattern keep the 256 default.
"""

import importlib.util
import math
import pathlib
import re
import sys
import time

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from models.common import validation_error_message
from models.compliance import AssetAttributeCheck, FileStructureCheck, VersionCoverageCheck

# Accepted at 256 (comb(256, 3) = 2,763,520) and rejected at 1024 (comb(1024, 3) = 178,433,024).
FLIPS_AT_1024 = ".*a.*a.*az"
# Its neighbour with one quantifier fewer, accepted at both (comb(1024, 2) = 523,776).
ACCEPTED_AT_1024 = ".*a.*az"


def _validators():
    """The module as models resolve it, through `sys.modules` at call time."""
    return sys.modules['common.validators']


def _shipped_validators():
    """The shipped module loaded from disk, for its private estimate (the mock re-exports public
    names only)."""
    spec = importlib.util.spec_from_file_location(
        '_shipped_common_validators_subject_length',
        str(pathlib.Path(__file__).resolve().parents[2] / 'backend' / 'common' / 'validators.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _message(model, **fields):
    with pytest.raises(ValidationError) as raised:
        model(**fields)
    return validation_error_message(raised.value)


@pytest.mark.unit
class TestTheSubjectLengthMovesTheBudget:

    def test_the_flipping_pattern_is_accepted_at_256_and_rejected_at_1024(self):
        v = _validators()
        assert v.validate_regex('p', FLIPS_AT_1024)[0] is True
        (valid, message) = v.validate_regex('p', FLIPS_AT_1024, subject_length=1024)
        assert valid is False
        assert 'quantifier' in message

    def test_one_quantifier_fewer_is_accepted_at_1024(self):
        assert _validators().validate_regex('p', ACCEPTED_AT_1024, subject_length=1024)[0] is True

    def test_the_default_subject_length_is_256(self):
        v = _validators()
        assert v.MAX_REGEX_SUBJECT_LENGTH == 256
        assert v.validate_regex('p', FLIPS_AT_1024) == v.validate_regex(
            'p', FLIPS_AT_1024, subject_length=256)

    def test_the_estimate_is_comb_of_the_subject_length(self):
        estimate = _shipped_validators()._backtracking_search_space
        assert estimate(ACCEPTED_AT_1024, 256) == math.comb(256, 2)
        assert estimate(ACCEPTED_AT_1024, 1024) == math.comb(1024, 2)
        assert estimate(ACCEPTED_AT_1024) == math.comb(256, 2)

    def test_the_clamp_follows_the_subject_length(self):
        """comb() peaks at half the subject and falls to 0 past it, so the counted quantifiers are
        clamped at subject_length // 2. A clamp left at 256 // 2 would let 25 quantifiers over a
        20-character subject estimate comb(20, 25) == 0 and pass."""
        estimate = _shipped_validators()._backtracking_search_space
        peak = math.comb(20, 10)
        assert [estimate('.*' * k, 20) for k in (10, 15, 25)] == [peak, peak, peak]


@pytest.mark.unit
class TestTheDispatcherCarriesTheSubjectLength:

    def test_a_regex_spec_without_subject_length_is_unchanged(self):
        """Casbin criteria name `REGEX` with no modifier and keep the 256 budget."""
        assert _validators().validate(
            {'criteriaValue': {'value': FLIPS_AT_1024, 'validator': 'REGEX'}})[0] is True

    def test_subject_length_in_the_spec_is_applied(self):
        (valid, message) = _validators().validate(
            {'p': {'value': FLIPS_AT_1024, 'validator': 'REGEX', 'subjectLength': 1024}})
        assert valid is False
        assert message.startswith('p is invalid.')

    @pytest.mark.parametrize('bad', [0, -1, '1024', 1024.0, True, None])
    def test_a_malformed_subject_length_is_a_programming_error(self, bad):
        with pytest.raises(Exception, match='subjectLength field in validator for p field'):
            _validators().validate(
                {'p': {'value': 'abc', 'validator': 'REGEX', 'subjectLength': bad}})


@pytest.mark.unit
class TestPathPatternsAreCheckedAtTheS3KeyLength:

    @pytest.mark.parametrize('model,field', [
        (FileStructureCheck, 'requiredPaths'),
        (FileStructureCheck, 'forbiddenPaths'),
        (VersionCoverageCheck, 'excludePaths'),
    ])
    def test_a_path_pattern_over_the_1024_budget_is_rejected(self, model, field):
        message = _message(model, name='c', **{field: [FLIPS_AT_1024]})
        assert message.startswith(f'{field} is invalid.')
        assert 'Too many open-ended quantifiers' in message
        assert FLIPS_AT_1024 not in message

    @pytest.mark.parametrize('model,field', [
        (FileStructureCheck, 'requiredPaths'),
        (FileStructureCheck, 'forbiddenPaths'),
        (VersionCoverageCheck, 'excludePaths'),
    ])
    def test_a_path_pattern_within_the_1024_budget_is_accepted(self, model, field):
        model(name='c', **{field: ['^' + ACCEPTED_AT_1024]})

    @pytest.mark.parametrize('key', ['matches', 'notMatches'])
    def test_a_name_pattern_keeps_the_256_budget(self, key):
        AssetAttributeCheck(name='c', field='assetName', **{key: '^' + FLIPS_AT_1024})


@pytest.mark.unit
class TestTheAcceptedWorstCaseAtTheS3KeyLengthIsFast:
    """The budget asserted in wall clock at 1024 characters, anchored as a `^` path pattern is (the
    match then starts at the first position only)."""

    @staticmethod
    def _search(pattern, subject):
        started = time.perf_counter()
        re.search(pattern, subject)
        return time.perf_counter() - started

    def test_the_accepted_neighbour_evaluates_quickly(self):
        assert self._search('^' + ACCEPTED_AT_1024, 'a' * 1024) < 1.0

    def test_the_rejected_pattern_is_the_slow_one(self):
        """POSITIVE CONTROL: the pattern the 1024 budget rejects is measurably expensive against a
        1024-character key, so the change of subject length is not arbitrary."""
        elapsed = self._search('^' + FLIPS_AT_1024, 'a' * 1024)
        assert elapsed > 0.1, (
            f"{FLIPS_AT_1024!r} took only {elapsed:.3f}s against 1024 characters, so this control no "
            "longer demonstrates the hazard the 1024 budget bounds")
