# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the outputTarget.locationType validation on the workflow create/update models.
locationType must be one of ("asset","none"). Results-only ("none") may take input files (no
coupling to inputFileArity). An 'asset' output with inputFileArity 'none' requires allowOverride so
an output asset can be chosen at execute time."""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.models.workflows import (
    CreateWorkflowRequestModel,
    UpdateWorkflowRequestModel,
    OUTPUT_LOCATION_TYPES,
    INPUT_FILE_ARITIES,
)


def _create(system_config):
    return CreateWorkflowRequestModel(
        databaseId="GLOBAL", workflowName="WF",
        specifiedPipelines=[{"pipelineId": "pipe1"}],
        systemConfig=system_config,
    )


@pytest.mark.unit
class TestOutputTargetLocationTypeValidation:
    def test_location_types_constant(self):
        assert OUTPUT_LOCATION_TYPES == ("asset", "none")

    def test_default_asset_ok(self):
        m = _create({"inputFileArity": "one",
                     "outputTarget": {"locationType": "asset", "allowOverride": False}})
        assert m.systemConfig["outputTarget"]["locationType"] == "asset"

    def test_results_only_none_with_arity_none_ok(self):
        m = _create({"inputFileArity": "none",
                     "outputTarget": {"locationType": "none", "allowOverride": False}})
        assert m.systemConfig["outputTarget"]["locationType"] == "none"

    def test_unknown_location_type_rejected(self):
        with pytest.raises(ValidationError):
            _create({"inputFileArity": "one", "outputTarget": {"locationType": "bucket"}})

    @pytest.mark.parametrize("arity", ["one", "multi"])
    def test_results_only_none_allows_input_files(self, arity):
        # Results-only ('none') MAY take input files (e.g. metadata analysis emitting only results).
        m = _create({"inputFileArity": arity, "outputTarget": {"locationType": "none"}})
        assert m.systemConfig["outputTarget"]["locationType"] == "none"

    def test_asset_output_with_arity_none_requires_override(self):
        # No input files + asset output + no override => no execution can supply an output asset.
        with pytest.raises(ValidationError):
            _create({"inputFileArity": "none",
                     "outputTarget": {"locationType": "asset", "allowOverride": False}})

    def test_asset_output_with_arity_none_and_override_ok(self):
        m = _create({"inputFileArity": "none",
                     "outputTarget": {"locationType": "asset", "allowOverride": True}})
        assert m.systemConfig["outputTarget"]["allowOverride"] is True

    def test_absent_output_target_ok(self):
        # No outputTarget at all -> no locationType constraint fires.
        m = _create({"inputFileArity": "one"})
        assert m.workflowName == "WF"

    def test_update_model_validates_location_type(self):
        with pytest.raises(ValidationError):
            UpdateWorkflowRequestModel(systemConfig={"inputFileArity": "one",
                                                     "outputTarget": {"locationType": "bogus"}})

    def test_update_model_results_only_ok(self):
        m = UpdateWorkflowRequestModel(systemConfig={"inputFileArity": "none",
                                                     "outputTarget": {"locationType": "none"}})
        assert m.systemConfig["outputTarget"]["locationType"] == "none"


@pytest.mark.unit
class TestInputFileArityValidation:
    def test_arities_constant(self):
        assert INPUT_FILE_ARITIES == ("none", "one", "multi")

    @pytest.mark.parametrize("arity", ["none", "one", "multi"])
    def test_valid_arities_accepted(self, arity):
        # 'none' also requires locationType none (or absent outputTarget) — omit outputTarget here.
        m = _create({"inputFileArity": arity})
        assert m.systemConfig["inputFileArity"] == arity

    def test_invalid_arity_rejected(self):
        with pytest.raises(ValidationError):
            _create({"inputFileArity": "seventeen"})

    def test_update_model_rejects_invalid_arity(self):
        with pytest.raises(ValidationError):
            UpdateWorkflowRequestModel(systemConfig={"inputFileArity": "lots"})


@pytest.mark.unit
class TestSubDashboardUrlValidation:
    def _create_url(self, url):
        return CreateWorkflowRequestModel(
            databaseId="GLOBAL", workflowName="WF",
            specifiedPipelines=[{"pipelineId": "pipe1"}],
            systemConfig={"inputFileArity": "one"},
            subDashboardUrl=url,
        )

    @pytest.mark.parametrize("url", ["https://example.com/d", "http://host/x", ""])
    def test_allowed_urls(self, url):
        m = self._create_url(url)
        assert m.subDashboardUrl == url

    @pytest.mark.parametrize("url", [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://host/x",
        "//example.com",
    ])
    def test_dangerous_or_relative_schemes_rejected(self, url):
        with pytest.raises(ValidationError):
            self._create_url(url)

    def test_update_model_rejects_javascript_url(self):
        with pytest.raises(ValidationError):
            UpdateWorkflowRequestModel(subDashboardUrl="javascript:alert(1)")


@pytest.mark.unit
class TestSpecifiedPipelineIdValidation:
    def _create(self, pipeline_ref):
        return CreateWorkflowRequestModel(
            databaseId="GLOBAL", workflowName="WF",
            specifiedPipelines=[pipeline_ref],
            systemConfig={"inputFileArity": "one"},
        )

    def test_valid_pipeline_ref_accepted(self):
        m = self._create({"pipelineId": "pipe1", "pipelineDatabaseId": "db1"})
        assert m.specifiedPipelines[0].pipelineId == "pipe1"

    def test_pipeline_ref_defaults_database_ok(self):
        m = self._create({"pipelineId": "pipe1"})
        assert m.specifiedPipelines[0].pipelineDatabaseId is None

    def test_bad_pipeline_id_rejected(self):
        # "x" is too short for the ID pattern (min 3 chars).
        with pytest.raises(ValidationError):
            self._create({"pipelineId": "x"})

    def test_bad_pipeline_database_id_rejected(self):
        with pytest.raises(ValidationError):
            self._create({"pipelineId": "pipe1", "pipelineDatabaseId": "!!"})


@pytest.mark.unit
class TestWorkflowSystemConfigShapeValidation:
    """Workflow systemConfig validates the assetScope / metadataInputs / inputFileFilters value
    shapes identically to pipeline systemConfig (shared validator)."""

    def test_valid_shapes_accepted(self):
        m = _create({
            "assetScope": {"crossAssetAllowed": True, "singleAssetOnly": False},
            "metadataInputs": {"assetMetadata": True, "fileMetadata": False},
            "inputFileFilters": {"allow": ["*.glb"], "exclude": []},
        })
        assert m.systemConfig["assetScope"]["crossAssetAllowed"] is True

    def test_unknown_asset_scope_key_rejected(self):
        with pytest.raises(ValidationError):
            _create({"assetScope": {"bogus": True}})

    def test_non_boolean_metadata_value_rejected(self):
        with pytest.raises(ValidationError):
            _create({"metadataInputs": {"assetMetadata": "yes"}})

    def test_non_list_filter_rejected(self):
        with pytest.raises(ValidationError):
            _create({"inputFileFilters": {"allow": "*.glb"}})
