# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Control-character rejection on the single-line free-text fields of the pipeline, workflow, and
template request models.

`name` (from pipelineName / workflowName) and `category` are ABAC constraint fields
(PERMISSION_CONSTRAINT_FIELDS), matched by the Casbin `equals` operator as
regexMatch(value, '^<constraint>$'). Python's '$' also matches immediately before a TRAILING
NEWLINE, so "finance-pipeline\\n" satisfies a constraint authored for "finance-pipeline" while
being a distinct stored value — two names, one rule. The same fields are interpolated into
single-line audit and application log entries, where an embedded newline splits one record into two
forgeable-looking lines.

The rule bounds C0/C1 control characters only. Unicode letters, spaces, and punctuation are
untouched, so every built-in pipeline/workflow/template name registered from
backendPipelines/*/vamsSchema (e.g. "NVIDIA Cosmos 3 Super (64B)", "CAD/Mesh Metadata Extraction")
still parses. The multi-line authored bodies — configBody, webFormJson, inputInstructions — are
deliberately exempt.

Dependency-free (pure pydantic models)."""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.models.pipelines import (
    CreatePipelineRequestModel,
    UpdatePipelineRequestModel,
    CreateTemplateRequestModel,
    UpdateTemplateRequestModel,
    validate_no_control_characters,
)
from backend.backend.models.workflows import (
    CreateWorkflowRequestModel,
    UpdateWorkflowRequestModel,
)

# Representative C0/C1 control characters: newline (the Casbin '$' bypass), carriage return, NUL,
# tab, and a C1 code point.
CONTROL_CHARS = ("\n", "\r", "\x00", "\t", "\x85")


def _pipeline(**kw):
    base = {
        "databaseId": "pipe-db-1",
        "pipelineName": "3D Basic Conversion",
        "executionConfig": {"executionType": "Lambda"},
    }
    base.update(kw)
    return CreatePipelineRequestModel(**base)


def _workflow(**kw):
    base = {
        "databaseId": "wf-db-1",
        "workflowName": "3D Basic Conversion",
        "specifiedPipelines": [{"pipelineId": "conversion-3d-basic"}],
    }
    base.update(kw)
    return CreateWorkflowRequestModel(**base)


@pytest.mark.unit
class TestControlCharactersRejected:
    """A control character in a name / category / description is rejected at parse time."""

    @pytest.mark.parametrize("char", CONTROL_CHARS)
    def test_pipeline_name_rejects_control_char(self, char):
        with pytest.raises(ValidationError):
            _pipeline(pipelineName=f"finance-pipeline{char}")

    @pytest.mark.parametrize("char", CONTROL_CHARS)
    def test_workflow_name_rejects_control_char(self, char):
        with pytest.raises(ValidationError):
            _workflow(workflowName=f"finance-workflow{char}")

    @pytest.mark.parametrize("char", CONTROL_CHARS)
    def test_template_name_rejects_control_char(self, char):
        with pytest.raises(ValidationError):
            CreateTemplateRequestModel(templateName=f"Convert to GLB{char}")

    def test_pipeline_category_rejects_newline(self):
        with pytest.raises(ValidationError):
            _pipeline(category="Conversion\nGenAI")

    def test_pipeline_description_allows_newline(self):
        # Not an ABAC constraint field, and the pipeline form offers a multi-line textarea.
        assert _pipeline(description="line one\nline two") is not None

    def test_workflow_category_rejects_newline(self):
        with pytest.raises(ValidationError):
            _workflow(category="Conversion\nGenAI")

    def test_workflow_description_allows_newline(self):
        # Not an ABAC constraint field, and the workflow builder offers a multi-line textarea.
        assert _workflow(description="line one\nline two") is not None

    def test_update_models_reject_control_char(self):
        # The update path stores the same fields, so it carries the same rule as create.
        with pytest.raises(ValidationError):
            UpdatePipelineRequestModel(pipelineName="p\n")
        with pytest.raises(ValidationError):
            UpdatePipelineRequestModel(category="Conversion\r")
        with pytest.raises(ValidationError):
            UpdateWorkflowRequestModel(workflowName="w\n")
        with pytest.raises(ValidationError):
            UpdateWorkflowRequestModel(category="Conversion\r")
        with pytest.raises(ValidationError):
            UpdateTemplateRequestModel(templateName="t\n")

    def test_trailing_newline_is_the_casbin_bypass_shape(self):
        """The specific value a '^<value>$' constraint would match while being a distinct name."""
        with pytest.raises(ValidationError):
            _pipeline(pipelineName="finance-pipeline\n")

    def test_error_message_names_the_field_not_the_value(self):
        # Rule 11: the message identifies the field, never echoing the submitted value.
        with pytest.raises(ValidationError) as exc:
            _pipeline(pipelineName="secret-name\n")
        message = str(exc.value)
        assert "pipelineName" in message
        assert "secret-name" not in message


@pytest.mark.unit
class TestLegitimateNamesStillAccepted:
    """The bound must not reject any name the system legitimately stores today."""

    # The real registered names/categories from backendPipelines/*/vamsSchema.
    BUILT_IN_NAMES = (
        "3D Basic Conversion",
        "CAD/Mesh Metadata Extraction",
        "NVIDIA Cosmos 3 Super Image2Video (64B)",
        "NVIDIA Gr00t N1.5 3B Fine-Tuning",
        "RapidPipeline (EKS)",
        "Point Cloud Potree Viewer",
    )
    BUILT_IN_CATEGORIES = ("3D Reconstruction", "Conversion", "GenAI", "Preview", "Simulation")

    @pytest.mark.parametrize("name", BUILT_IN_NAMES)
    def test_built_in_pipeline_names_accepted(self, name):
        assert _pipeline(pipelineName=name).pipelineName == name

    @pytest.mark.parametrize("name", BUILT_IN_NAMES)
    def test_built_in_workflow_names_accepted(self, name):
        assert _workflow(workflowName=name).workflowName == name

    @pytest.mark.parametrize("category", BUILT_IN_CATEGORIES)
    def test_built_in_categories_accepted(self, category):
        assert _pipeline(category=category).category == category

    def test_unicode_and_punctuation_accepted(self):
        # Non-ASCII letters and an em dash are ordinary display text, not control characters.
        name = "Conversión 3D — café (v2)"
        assert _pipeline(pipelineName=name).pipelineName == name
        assert _workflow(workflowName=name).workflowName == name

    def test_built_in_descriptions_accepted(self):
        description = ("3D Gaussian Splat Pipeline - auto process images and videos into 3D "
                       "splats (objects and 360 environments).")
        assert _pipeline(description=description).description == description

    def test_multi_line_template_bodies_are_exempt(self):
        """configBody / webFormJson / inputInstructions are authored documents, not single-line text."""
        model = CreateTemplateRequestModel(
            templateName="WGS84 to OSGB36 (LAZ output)",
            configFormat="yaml",
            configBody="specificationVersion: jobtemplate-2023-09\nname: transform\n",
            inputInstructions="Step 1: pick a CRS.\nStep 2: run.",
        )
        assert "\n" in model.configBody
        assert "\n" in model.inputInstructions


@pytest.mark.unit
class TestValidateNoControlCharactersHelper:
    """The shared helper both model files call."""

    def test_empty_and_none_are_noops(self):
        validate_no_control_characters(None, "field")
        validate_no_control_characters("", "field")

    def test_non_string_is_a_noop(self):
        # Type coercion is pydantic's job; this rule only inspects strings.
        validate_no_control_characters(5, "field")

    def test_clean_value_passes(self):
        validate_no_control_characters("Conversión 3D — café", "field")

    def test_control_char_raises_valueerror(self):
        with pytest.raises(ValueError):
            validate_no_control_characters("a\nb", "field")


@pytest.mark.unit
class TestDescriptionMaySpanLines:
    """`description` is NOT an ABAC constraint field, so the control-char guard skips it.

    The guard exists because `name` / `category` are matched by Casbin `^<value>$` rules, which
    in Python also match a trailing newline - a real authorization bypass. `description` appears
    in PERMISSION_CONSTRAINT_FIELDS nowhere, and the web ships it as a multi-line textarea in
    both the workflow and pipeline builders, so guarding it rejected exactly what that control
    produces. Log integrity is not an argument either: auditLogging writes values through
    `json.dumps`, which escapes a newline rather than emitting one.
    """

    MULTILINE = "Converts CAD to GLB.\n\nStep 1: import\nStep 2: decimate"

    def test_a_workflow_description_may_span_lines(self):
        request = CreateWorkflowRequestModel(
            databaseId="db1", workflowId="wf1", workflowName="n",
            description=self.MULTILINE, specifiedPipelines=[{"pipelineId": "pipe1"}])
        assert request.description == self.MULTILINE

    def test_a_workflow_update_description_may_span_lines(self):
        request = UpdateWorkflowRequestModel(
            databaseId="db1", workflowId="wf1", description=self.MULTILINE)
        assert request.description == self.MULTILINE

    def test_a_pipeline_description_may_span_lines(self):
        request = CreatePipelineRequestModel(
            databaseId="db1", pipelineId="pipe1", pipelineName="n",
            description=self.MULTILINE, pipelineType="lambda", executionType="Lambda")
        assert request.description == self.MULTILINE

    def test_a_tab_in_a_description_is_also_allowed(self):
        request = CreateWorkflowRequestModel(
            databaseId="db1", workflowId="wf1", workflowName="n",
            description="col1\tcol2", specifiedPipelines=[{"pipelineId": "pipe1"}])
        assert request.description == "col1\tcol2"

    def test_the_abac_fields_are_still_guarded(self):
        """The #95 guarantee: narrowing the guard must not widen the bypass it closed."""
        with pytest.raises(ValidationError):
            CreateWorkflowRequestModel(
                databaseId="db1", workflowId="wf1", workflowName="admin\n",
                specifiedPipelines=[{"pipelineId": "pipe1"}])
        with pytest.raises(ValidationError):
            CreateWorkflowRequestModel(
                databaseId="db1", workflowId="wf1", workflowName="n", category="prod\n",
                specifiedPipelines=[{"pipelineId": "pipe1"}])
        with pytest.raises(ValidationError):
            CreatePipelineRequestModel(
                databaseId="db1", pipelineId="pipe1", pipelineName="admin\n",
                pipelineType="lambda", executionType="Lambda")
