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
class TestPipelineInputFileFilters:
    """inputFileFilters keys are restricted to allow/exclude — an absent `allow` list means
    allow-all at execute time, so a typo would silently widen the filter."""

    def test_allow_exclude_accepted(self):
        m = _create({"inputFileFilters": {"allow": ["*.glb"], "exclude": ["*.tmp"]}})
        assert m.systemConfig["inputFileFilters"]["allow"] == ["*.glb"]

    def test_unknown_filter_key_rejected(self):
        with pytest.raises(ValidationError):
            _create({"inputFileFilters": {"allowed": ["*.glb"]}})

    def test_update_model_rejects_unknown_filter_key(self):
        with pytest.raises(ValidationError):
            UpdatePipelineRequestModel(systemConfig={"inputFileFilters": {"deny": ["*.glb"]}})

    @pytest.mark.parametrize("pattern", ["*", "**", "*.*", "/*", "/**", " * "])
    def test_match_everything_exclude_rejected(self, pattern):
        # Exclude is applied AFTER allow, so a match-everything exclude removes every file and makes
        # the pipeline permanently unrunnable — always a mistake rather than an intent. An empty
        # exclude list is how "exclude nothing" is expressed.
        with pytest.raises(ValidationError):
            _create({"inputFileFilters": {"allow": ["*.glb"], "exclude": [pattern]}})

    def test_match_everything_allow_is_accepted(self):
        # The same pattern in `allow` is fine: it means allow-all, which is also what an absent allow
        # list means, and the chain reads it as "defer to the next level down".
        m = _create({"inputFileFilters": {"allow": ["*"], "exclude": []}})
        assert m.systemConfig["inputFileFilters"]["allow"] == ["*"]

    def test_empty_exclude_accepted(self):
        m = _create({"inputFileFilters": {"exclude": []}})
        assert m.systemConfig["inputFileFilters"]["exclude"] == []

    def test_update_model_rejects_match_everything_exclude(self):
        with pytest.raises(ValidationError):
            UpdatePipelineRequestModel(systemConfig={"inputFileFilters": {"exclude": ["*"]}})

    def test_template_override_exclude_is_validated_too(self):
        # A template's `overrides` can carry inputFileFilters, so the same rule must apply there —
        # otherwise the restriction could be reintroduced one level down the chain.
        from backend.backend.models.pipelines import CreateTemplateRequestModel
        with pytest.raises(ValidationError):
            CreateTemplateRequestModel(
                databaseId="db1", pipelineId="p1", templateName="T", configFormat="json",
                configBody="{}", overrides={"inputFileFilters": {"exclude": ["*"]}})


@pytest.mark.unit
class TestSqsResourceValidation:
    """SQS pipelines must name a queue: the URL becomes the sendMessage task's QueueUrl, so an
    absent value would deploy a state machine with an empty target."""

    def _create(self, execution_config):
        return CreatePipelineRequestModel(
            databaseId="db1", pipelineName="P", executionConfig=execution_config, systemConfig={})

    def test_queue_url_required(self):
        with pytest.raises(ValidationError):
            self._create({"executionType": "SQS"})

    def test_empty_sqs_block_rejected(self):
        with pytest.raises(ValidationError):
            self._create({"executionType": "SQS", "sqs": {}})

    def test_valid_queue_url_accepted(self):
        url = "https://sqs.us-west-2.amazonaws.com/123456789012/my-queue"
        m = self._create({"executionType": "SQS", "sqs": {"queueUrl": url}})
        assert m.executionConfig["sqs"]["queueUrl"] == url

    def test_update_model_rejects_sqs_without_queue(self):
        with pytest.raises(ValidationError):
            UpdatePipelineRequestModel(executionConfig={"executionType": "SQS", "sqs": {}})


@pytest.mark.unit
class TestDeadlineCloudResourceValidation:
    """DeadlineCloud createJob only queues the job — completion arrives via the task-token callback,
    so waitForCallback Enabled plus the target farm + queue are required at authoring time."""

    def _create(self, execution_config):
        return CreatePipelineRequestModel(
            databaseId="db1", pipelineName="P", executionConfig=execution_config, systemConfig={})

    def _config(self, **overrides):
        config = {"executionType": "DeadlineCloud", "waitForCallback": "Enabled",
                  "deadlineCloud": {"farmId": "farm-1", "queueId": "queue-1"}}
        config.update(overrides)
        return config

    def test_complete_config_accepted(self):
        m = self._create(self._config())
        assert m.executionConfig["deadlineCloud"]["farmId"] == "farm-1"

    def test_callback_disabled_rejected(self):
        with pytest.raises(ValidationError):
            self._create(self._config(waitForCallback="Disabled"))

    def test_callback_omitted_rejected(self):
        config = self._config()
        del config["waitForCallback"]
        with pytest.raises(ValidationError):
            self._create(config)

    @pytest.mark.parametrize("missing", ["farmId", "queueId"])
    def test_missing_target_rejected(self, missing):
        config = self._config()
        del config["deadlineCloud"][missing]
        with pytest.raises(ValidationError):
            self._create(config)

    def test_update_model_rejects_callback_disabled(self):
        with pytest.raises(ValidationError):
            UpdatePipelineRequestModel(
                executionConfig=self._config(waitForCallback="Disabled"))

    @pytest.mark.parametrize("field", ["priority", "maxRetriesPerTask", "maxFailedTasksCount"])
    def test_non_integer_setting_rejected(self, field):
        # The createJob task state casts these to int; catch a bad value on the pipeline that holds
        # it rather than later at workflow save.
        config = self._config()
        config["deadlineCloud"][field] = "abc"
        with pytest.raises(ValidationError):
            self._create(config)

    @pytest.mark.parametrize("field", ["priority", "maxRetriesPerTask", "maxFailedTasksCount"])
    def test_negative_setting_rejected(self, field):
        config = self._config()
        config["deadlineCloud"][field] = -1
        with pytest.raises(ValidationError):
            self._create(config)

    def test_zero_priority_accepted(self):
        # 0 is the lowest valid Deadline priority, not an absent value.
        config = self._config()
        config["deadlineCloud"]["priority"] = 0
        assert self._create(config).executionConfig["deadlineCloud"]["priority"] == 0


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
