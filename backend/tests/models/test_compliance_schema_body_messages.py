# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Rule 11 on the vams-rules-v1 schema-body model: the messages the `VamsRulesV1Schema` root
validator raises describe the rule that failed and never carry the caller-supplied rule name or
ruleType value, so `validation_error_message` over them is caller-safe. The rule vocabulary the
message names is the model's own."""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from models.common import validation_error_message
from models.compliance import RULE_TYPE_MODELS, VamsRulesV1Schema

SECRET_RULE_NAME = "secret<rule-name>"
SECRET_RULE_TYPE = "not<a-rule-type>"
SECRET_VALUE = "secret<value>"


def _errors_for(body):
    with pytest.raises(ValidationError) as raised:
        VamsRulesV1Schema(**body)
    return raised.value


@pytest.mark.unit
class TestVamsRulesV1SchemaMessagesDoNotEchoInput:

    def test_an_unknown_rule_type_names_the_vocabulary_not_the_submitted_value(self):
        error = _errors_for({"schemaFormat": "vams-rules-v1",
                             "rules": {SECRET_RULE_NAME: {"ruleType": SECRET_RULE_TYPE}}})
        message = validation_error_message(error)
        assert SECRET_RULE_NAME not in message
        assert SECRET_RULE_TYPE not in message
        for rule_type in RULE_TYPE_MODELS:
            assert rule_type in message

    def test_a_rule_that_is_not_an_object_is_not_echoed(self):
        error = _errors_for({"schemaFormat": "vams-rules-v1", "rules": {SECRET_RULE_NAME: SECRET_VALUE}})
        message = validation_error_message(error)
        assert SECRET_RULE_NAME not in message
        assert SECRET_VALUE not in message
        assert "must be an object" in message

    def test_a_missing_rule_type_is_reported_without_the_rule_name(self):
        error = _errors_for({"schemaFormat": "vams-rules-v1",
                             "rules": {SECRET_RULE_NAME: {"enforcement": "warn"}}})
        assert SECRET_RULE_NAME not in validation_error_message(error)

    def test_the_model_class_name_stays_out_of_the_caller_safe_message(self):
        error = _errors_for({"schemaFormat": "vams-rules-v1", "rules": {}})
        message = validation_error_message(error)
        assert "VamsRulesV1Schema" not in message
        assert "validation error" not in message
        assert "At least one rule is required" in message

    def test_a_valid_body_parses_every_rule_type(self):
        body = {"schemaFormat": "vams-rules-v1", "rules": {
            "a": {"ruleType": "relationship", "enforcement": "warn",
                  "checks": [{"name": "c", "direction": "parents", "relationshipType": "parentChild",
                              "minCount": 1}]},
            "b": {"ruleType": "metadata", "enforcement": "inform",
                  "metadataSchemaRef": {"databaseId": "GLOBAL", "schemaName": "Asset Schema"},
                  "checks": [{"name": "c", "validateRequired": True}]},
        }}
        parsed = VamsRulesV1Schema(**body).parse_rules()
        assert {name: type(rule).__name__ for name, rule in parsed.items()} == {
            "a": "RelationshipRule", "b": "MetadataRule"}
