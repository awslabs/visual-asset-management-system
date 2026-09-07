# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Where the "a Lambda pipeline never stores an empty invoke target" guarantee actually lives.

`_validate_execution_config` accepts an empty `lambda.resourceId` on both create and update, and it
has to: the model sees no stored row, so it cannot tell "auto-provision one for me" (create) from
"keep the one this pipeline already runs" (update). The guarantee is handler-side —
`_carry_over_provisioned_lambda` then `_provision_lambda_for_pipeline`, which RAISES when the
deployment cannot provision. That half is asserted where it is kept, in
tests/handlers/pipelines/test_pipelineService_update_integrity.py
(`test_partial_lambda_config_carries_over_the_stored_function`,
`test_switch_into_lambda_with_no_target_is_refused`); this file covers the model half and the shape
of its contract.
"""

import pytest

from backend.backend.models.pipelines import (
    CreatePipelineRequestModel,
    UpdatePipelineRequestModel,
    _validate_execution_config,
)

_LAMBDA_NO_TARGET = {"executionType": "Lambda", "lambda": {}}


@pytest.mark.unit
class TestTheModelAcceptsAnEmptyLambdaTarget:
    """Both verbs, since both callers pass the same thing — a request the handler completes."""

    def test_create_accepts_a_lambda_config_with_no_resource_id(self):
        request = CreatePipelineRequestModel(
            databaseId="mydb", pipelineName="P", executionConfig=dict(_LAMBDA_NO_TARGET))
        assert request.executionConfig["lambda"] == {}

    def test_update_accepts_a_lambda_config_with_no_resource_id(self):
        request = UpdatePipelineRequestModel(executionConfig=dict(_LAMBDA_NO_TARGET))
        assert request.executionConfig["lambda"] == {}

    def test_a_malformed_target_is_still_rejected(self):
        """CONTROL: accepting an EMPTY target is not the same as accepting any target — the format
        rules on a supplied resourceId still apply."""
        with pytest.raises(ValueError):
            _validate_execution_config(
                {"executionType": "Lambda", "lambda": {"resourceId": "not a valid name!"}})

    @pytest.mark.temporary  # pins the removal of the dead require_lambda_resource_id kwarg
    def test_the_validator_takes_no_lambda_target_requirement_flag(self):
        """The removed flag was documented as "set on the update path" while no caller passed it, so
        the model advertised a guarantee it did not provide."""
        import inspect

        parameters = inspect.signature(_validate_execution_config).parameters
        assert list(parameters) == ["execution_config"], list(parameters)
        assert "require_lambda_resource_id" not in (_validate_execution_config.__doc__ or "")
