# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The three validator names pipeline sub-process registration uses for its descriptive fields.

A Step Functions state name is bounded by ASL at 80 characters and may carry spaces and punctuation
(this repository deploys a Choice state named `Job Complete?`), so the rule refuses only control
characters. A display label is the same class at 128. The log source type is a closed set."""

import pytest


def validate(params):
    from common.validators import validate as _validate
    return _validate(params)


def _check(validator, value):
    return validate({'field': {'value': value, 'validator': validator}})[0]


@pytest.mark.unit
class TestSfnStateName:
    @pytest.mark.parametrize('value', [
        'Job Complete?', 'Preview3dThumbnailBatchJob', 'CosmosBatchJob-text2world2B',
        "Rob's step, take 2 & more!", 'Résumé état', 'x' * 80, 'a',
    ])
    def test_legal_names_are_accepted(self, value):
        assert _check('SFN_STATE_NAME', value) is True

    @pytest.mark.parametrize('value', ['x' * 81, 'tab\there', 'line\nbreak', 'del\x7fchar'])
    def test_over_length_and_control_characters_are_refused(self, value):
        assert _check('SFN_STATE_NAME', value) is False


@pytest.mark.unit
class TestDisplayLabel:
    def test_128_is_the_bound(self):
        assert _check('DISPLAY_LABEL', 'y' * 128) is True
        assert _check('DISPLAY_LABEL', 'y' * 129) is False

    def test_control_characters_are_refused(self):
        assert _check('DISPLAY_LABEL', 'bad\x01label') is False

    def test_a_pipeline_label_is_accepted(self):
        assert _check('DISPLAY_LABEL', 'Preview 3D thumbnail state machine') is True


@pytest.mark.unit
class TestLogSourceType:
    @pytest.mark.parametrize('value', ['stateMachine', 'lambda', 'batch', 'ecs', 'container', 'custom'])
    def test_every_member_of_the_closed_set_is_accepted(self, value):
        assert _check('LOG_SOURCE_TYPE', value) is True

    @pytest.mark.parametrize('value', ['kubernetes', 'Batch', 'state-machine', 'lambda '])
    def test_anything_else_is_refused(self, value):
        assert _check('LOG_SOURCE_TYPE', value) is False
