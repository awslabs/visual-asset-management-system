# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The workflow concurrencyRestriction closed set carries the per-file-version lock value.

The create and update validators both test membership in CONCURRENCY_RESTRICTIONS, so the tuple is
the single place the value is admitted; the accept/reject pairs below prove both validators read it."""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.models.workflows import (
    CONCURRENCY_RESTRICTIONS,
    CreateWorkflowRequestModel,
    UpdateWorkflowRequestModel,
)


def _create(system_config):
    return CreateWorkflowRequestModel(
        databaseId="GLOBAL", workflowName="WF",
        specifiedPipelines=[{"pipelineId": "pipe1"}],
        systemConfig=system_config,
    )


@pytest.mark.unit
class TestConcurrencyRestrictionValues:
    def test_the_closed_set_ends_with_the_version_lock(self):
        assert CONCURRENCY_RESTRICTIONS == ("none", "perAsset", "perInputFile", "perInputFileVersion")

    def test_create_accepts_per_input_file_version(self):
        m = _create({"inputFileArity": "one", "concurrencyRestriction": "perInputFileVersion"})
        assert m.systemConfig["concurrencyRestriction"] == "perInputFileVersion"

    def test_update_accepts_per_input_file_version(self):
        m = UpdateWorkflowRequestModel(systemConfig={"concurrencyRestriction": "perInputFileVersion"})
        assert m.systemConfig["concurrencyRestriction"] == "perInputFileVersion"

    def test_the_three_existing_values_still_validate(self):
        for value in ("none", "perAsset", "perInputFile"):
            assert _create({"inputFileArity": "one", "concurrencyRestriction": value}).systemConfig[
                "concurrencyRestriction"] == value

    def test_an_unknown_value_is_rejected_by_both_validators(self):
        # Control: widening the tuple must not have loosened membership into a prefix or substring test.
        with pytest.raises(ValidationError):
            _create({"inputFileArity": "one", "concurrencyRestriction": "perInputFileVersions"})
        with pytest.raises(ValidationError):
            UpdateWorkflowRequestModel(systemConfig={"concurrencyRestriction": "perFileVersion"})
