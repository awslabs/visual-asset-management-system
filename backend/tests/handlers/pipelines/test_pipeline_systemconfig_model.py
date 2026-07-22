# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for pipeline systemConfig validation on the create/update models — the
inputFileArity enum (none/one/multi) must be enforced at authoring time."""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.models.pipelines import (
    CreatePipelineRequestModel,
    UpdatePipelineRequestModel,
    INPUT_FILE_ARITIES,
)


def _create(system_config):
    return CreatePipelineRequestModel(
        databaseId="db1",
        pipelineName="P",
        executionConfig={"executionType": "Lambda", "lambda": {"resourceId": "fn"}},
        systemConfig=system_config,
    )


@pytest.mark.unit
class TestPipelineInputFileArity:
    def test_arities_constant(self):
        assert INPUT_FILE_ARITIES == ("none", "one", "multi")

    @pytest.mark.parametrize("arity", ["none", "one", "multi"])
    def test_valid_arity_accepted(self, arity):
        m = _create({"inputFileArity": arity})
        assert m.systemConfig["inputFileArity"] == arity

    def test_absent_system_config_ok(self):
        m = _create({})
        assert m.pipelineName == "P"

    def test_invalid_arity_rejected(self):
        with pytest.raises(ValidationError):
            _create({"inputFileArity": "seventeen"})

    def test_update_model_rejects_invalid_arity(self):
        with pytest.raises(ValidationError):
            UpdatePipelineRequestModel(systemConfig={"inputFileArity": "lots"})

    def test_update_model_accepts_valid_arity(self):
        m = UpdatePipelineRequestModel(systemConfig={"inputFileArity": "multi"})
        assert m.systemConfig["inputFileArity"] == "multi"


@pytest.mark.unit
class TestPipelineAssetScope:
    """assetScope accepts both the CDK registration shorthand ({wholeAsset}) and the canonical
    four *Allowed keys; unknown keys and non-boolean values are rejected."""

    def test_wholeasset_shorthand_accepted(self):
        # The vamsSchema/pipeline.json registration payloads use this exact shape.
        m = _create({"inputFileArity": "one", "assetScope": {"wholeAsset": True}})
        assert m.systemConfig["assetScope"]["wholeAsset"] is True

    def test_canonical_allowed_keys_accepted(self):
        m = _create({"assetScope": {
            "crossAssetAllowed": False, "singleAssetOnly": True,
            "wholeAssetAllowed": False, "folderAllowed": False}})
        assert m.systemConfig["assetScope"]["singleAssetOnly"] is True

    def test_unknown_asset_scope_key_rejected(self):
        with pytest.raises(ValidationError):
            _create({"assetScope": {"bogusKey": True}})

    def test_non_boolean_asset_scope_value_rejected(self):
        with pytest.raises(ValidationError):
            _create({"assetScope": {"wholeAsset": "yes"}})


@pytest.mark.unit
class TestLambdaResourceIdValidation:
    def _create(self, execution_config):
        return CreatePipelineRequestModel(
            databaseId="db1", pipelineName="P", executionConfig=execution_config, systemConfig={})

    @pytest.mark.parametrize("resource_id", [
        "vams-smoke-mock-sync",
        "my_fn",
        "fn:PROD",
        "fn:$LATEST",
        "arn:aws:lambda:us-west-2:123456789012:function:x",
    ])
    def test_valid_lambda_resource_ids_accepted(self, resource_id):
        m = self._create({"executionType": "Lambda", "lambda": {"resourceId": resource_id}})
        assert m.executionConfig["lambda"]["resourceId"] == resource_id

    @pytest.mark.parametrize("resource_id", [
        "bad name with spaces",
        "fn/slash",
        "arn:not-a-valid-arn",  # starts with arn: but is not a well-formed ARN
    ])
    def test_invalid_lambda_resource_ids_rejected(self, resource_id):
        with pytest.raises(ValidationError):
            self._create({"executionType": "Lambda", "lambda": {"resourceId": resource_id}})
