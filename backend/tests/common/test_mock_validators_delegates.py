# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The `common.validators` module tests resolve must be the real dispatcher.

Models call `validate()` through `sys.modules['common.validators']`, so a stand-in that returns
`(True, "")` for a validator name it does not implement makes every model check on that name a
no-op inside the suite while the deployed handler enforces it. These tests exercise the names the
pipeline/workflow/execution models rely on with values the real rules reject.
"""

import sys

import pytest


def validate(params):
    """Resolve the dispatcher the way the models do: a function-body import, which reads
    `sys.modules['common.validators']` at CALL time and therefore picks up the stand-in the autouse
    mock fixture installs. A module-level import here would bind at collection time instead and
    test a module the models never see."""
    from common.validators import validate as _validate
    return _validate(params)


# One rejected value per validator name reachable from the orchestration models.
REJECTED_VALUES = [
    ('ID', '!!'),
    ('ASSET_ID', '../escape'),
    ('OBJECT_NAME', 'name/with/slash'),
    ('UUID', 'garbage'),
    ('GUID', 'garbage'),
    ('STRING_30', 'x' * 31),
    ('STRING_JSON', '{not json'),
    ('NUMBER', 'abc'),
    ('BOOL', 'maybe'),
    ('EMAIL', 'not-an-email'),
    ('USERID', 'ab'),
    ('RELATIVE_FILE_PATH', 'no-leading-slash'),
    ('ISO8601_UTC', '2026-13-01T00:00:00Z'),
    ('FILE_EXTENSION', 'glb'),
    ('SQS_QUEUE_URL', 'ftp://evil.example/queue'),
    ('EVENTBRIDGE_BUS_ARN', 'arn:aws:s3:::my-bucket'),
    ('EVENTBRIDGE_SOURCE', 'aws.reserved'),
    ('ARN', 'not-an-arn'),
    ('CLOUDWATCH_LOG_GROUP_ARN', 'arn:aws:logs:us-east-1:123456789012:log-stream:x'),
    ('CLOUDWATCH_LOG_GROUP_NAME', 'has space and *'),
    ('LOG_STREAM_NAME', 'has:colon'),
    ('S3_BUCKET_NAME', 'Not_A_Bucket'),
    ('ID_ARRAY', ['!!']),
    ('UUID_ARRAY', ['garbage']),
    ('STRING_256_ARRAY', ['x' * 257]),
    ('OBJECT_NAME_ARRAY', ['name/with/slash']),
    ('EMAIL_ARRAY', ['not-an-email']),
    ('USERID_ARRAY', ['ab']),
    ('RELATIVE_FILE_PATH_ARRAY', ['no-leading-slash']),
    ('DOWNLOAD_KEY_ARRAY', ['../escape']),
]

ACCEPTED_VALUES = [
    ('ID', 'my-pipeline-1'),
    ('OBJECT_NAME', 'My Pipeline 1'),
    ('SQS_QUEUE_URL', 'https://sqs.us-east-1.amazonaws.com/123456789012/my-queue'),
    ('EVENTBRIDGE_BUS_ARN', 'arn:aws:events:us-east-1:123456789012:event-bus/my-bus'),
    ('EVENTBRIDGE_SOURCE', 'com.example.vams'),
    ('ARN', 'arn:aws:states:us-east-1:123456789012:execution:sm:exec'),
    ('ISO8601_UTC', '2026-01-31T12:00:00Z'),
    ('ID_ARRAY', ['my-pipeline-1', 'my-pipeline-2']),
]


@pytest.mark.unit
class TestMockValidatorsDelegate:
    """The resolved dispatcher enforces every validator name the models declare."""

    def test_resolved_module_exposes_the_real_pattern_constants(self):
        module = sys.modules['common.validators']
        # A stand-in that re-implements the rules drifts from these; sourcing them from the real
        # module is what keeps the suite's accept/reject boundary equal to production's.
        assert module.execution_id_pattern.startswith('^(?:[0-9a-f]{32}')
        assert 'csp\\.hci\\.ic\\.gov' in module.aws_dns_suffix_group
        assert callable(module.validate_sqs_queue_url)

    @pytest.mark.parametrize('validator,value', REJECTED_VALUES)
    def test_invalid_value_is_rejected(self, validator, value):
        (valid, message) = validate({'field': {'value': value, 'validator': validator}})
        assert valid is False, f"{validator} accepted {value!r}"
        assert 'field' in message

    @pytest.mark.parametrize('validator,value', ACCEPTED_VALUES)
    def test_valid_value_is_accepted(self, validator, value):
        (valid, message) = validate({'field': {'value': value, 'validator': validator}})
        assert valid is True, f"{validator} rejected {value!r}: {message}"

    def test_global_keyword_is_gated_by_the_flag(self):
        assert validate({'databaseId': {'value': 'GLOBAL', 'validator': 'ID',
                                        'allowGlobalKeyword': True}})[0] is True
        assert validate({'databaseId': {'value': 'GLOBAL', 'validator': 'ID'}})[0] is False
        assert validate({'databaseId': {'value': 'global', 'validator': 'ID',
                                        'allowGlobalKeyword': True}})[0] is False

    def test_non_string_value_for_a_scalar_validator_is_rejected(self):
        assert validate({'field': {'value': 5, 'validator': 'ID'}})[0] is False
        assert validate({'field': {'value': {'a': 1}, 'validator': 'ID'}})[0] is False

    def test_optional_empty_value_skips_only_its_own_field(self):
        (valid, message) = validate({
            'first': {'value': None, 'validator': 'ID', 'optional': True},
            'second': {'value': '!!', 'validator': 'ID'},
        })
        assert valid is False
        assert 'second' in message
