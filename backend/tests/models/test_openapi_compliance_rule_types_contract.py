# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract tests binding documentation/VAMS_API.yaml to the vams-rules-v1 models.

The schema body's `rules` documents one schema per rule type (`oneOf`), and the rule result names
the rule types it can carry. Both lists must be exactly `RULE_TYPE_MODELS`, and the bounds the
spec states for the file-structure, version-coverage and asset-attribute rules must be the ones
the models enforce — a spec that lags the models documents a request the API refuses.
"""

from pathlib import Path

import pytest

from models.compliance import (
    MAX_CHECKS_PER_RULE, MAX_PATH_PATTERNS, MAX_PATTERN_LENGTH, MAX_TAGS_PER_CHECK,
    RULE_TYPE_MODELS,
)

yaml = pytest.importorskip("yaml")

SPEC_PATH = Path(__file__).resolve().parents[3] / "documentation" / "VAMS_API.yaml"


@pytest.fixture(scope="module")
def schemas():
    if not SPEC_PATH.is_file():
        pytest.skip(f"OpenAPI spec not found at {SPEC_PATH}")
    with open(SPEC_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)["components"]["schemas"]


def _ref(schemas, ref):
    return schemas[ref.rsplit("/", 1)[-1]]


def _rule_schemas(schemas):
    one_of = schemas["vamsRulesV1Schema"]["properties"]["rules"]["additionalProperties"]["oneOf"]
    return {_ref(schemas, entry["$ref"])["properties"]["ruleType"]["enum"][0]: _ref(schemas, entry["$ref"])
            for entry in one_of}


def _check_properties(rule_schema):
    return rule_schema["properties"]["checks"]["items"]["properties"]


@pytest.mark.unit
class TestComplianceRuleTypesContract:

    def test_the_schema_body_documents_one_schema_per_rule_type(self, schemas):
        assert sorted(_rule_schemas(schemas)) == sorted(RULE_TYPE_MODELS)

    def test_the_rule_result_names_every_rule_type(self, schemas):
        assert sorted(schemas["complianceRuleResult"]["properties"]["ruleType"]["enum"]) == sorted(
            RULE_TYPE_MODELS)

    @pytest.mark.parametrize("rule_type", ["fileStructure", "versionCoverage", "assetAttributes"])
    def test_the_check_count_bound_is_the_models(self, schemas, rule_type):
        assert _rule_schemas(schemas)[rule_type]["properties"]["checks"]["maxItems"] == (
            MAX_CHECKS_PER_RULE)

    @pytest.mark.parametrize("rule_type,field", [
        ("fileStructure", "requiredPaths"), ("fileStructure", "forbiddenPaths"),
        ("versionCoverage", "excludePaths"),
    ])
    def test_the_pattern_list_bounds_are_the_models(self, schemas, rule_type, field):
        prop = _check_properties(_rule_schemas(schemas)[rule_type])[field]
        assert prop["maxItems"] == MAX_PATH_PATTERNS
        assert (prop["items"]["minLength"], prop["items"]["maxLength"]) == (1, MAX_PATTERN_LENGTH)

    def test_the_asset_attribute_bounds_are_the_models(self, schemas):
        props = _check_properties(_rule_schemas(schemas)["assetAttributes"])
        assert props["field"]["enum"] == ["assetName", "description", "tags"]
        for key in ("matches", "notMatches"):
            assert props[key]["maxLength"] == MAX_PATTERN_LENGTH
        for key in ("requiredTags", "forbiddenTags"):
            assert props[key]["maxItems"] == MAX_TAGS_PER_CHECK
            assert props[key]["items"]["maxLength"] == 256
