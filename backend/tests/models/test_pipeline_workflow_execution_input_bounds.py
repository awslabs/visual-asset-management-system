# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Input-validation bounds on the pipeline / workflow / execution API request models.

Three classes of rule are covered, each paired with a legitimate-input test so a bound cannot be
tightened into rejecting what the system stores today:

  - NESTED SUB-MODEL VALIDATION. A field typed ``List[Dict[str, Any]]`` or
    ``Dict[str, Dict[str, Any]]`` never runs the sub-model that describes it, so its per-field rules
    are dead on that path. ``tagSchema`` (TemplateTagFieldModel) and
    ``pipelineExecutionParameters`` (PipelineExecutionParameters) are both carried that way.
  - UNBOUNDED COLLECTIONS. A list or map with no cap lets one request drive unbounded fan-out, an
    unbounded DynamoDB row, or an oversized Step Functions definition.
  - TRUTHY-STRING BOOLEANS and PATH TRAVERSAL. A non-bool value in a boolean gate is stored and read
    back as ``True``, inverting the gate; a value that becomes part of an S3 key must not carry
    ``..`` or a backslash.

Bounds are deliberately generous: the built-in pipelines under ``backendPipelines/*/vamsSchema``
declare at most three tag fields and a handful of filter patterns, so every cap here sits orders of
magnitude above real authoring.
"""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.models import pipelines as pm
from backend.backend.models import workflows as wm
from backend.backend.models import executions as em


def _pipeline(system_config=None, **kw):
    return pm.CreatePipelineRequestModel(
        databaseId="mydb1", pipelineName="P",
        executionConfig={"executionType": "Lambda"},
        systemConfig=system_config if system_config is not None else {}, **kw)


def _workflow(**kw):
    kw.setdefault("specifiedPipelines", [{"pipelineId": "pipe1"}])
    return wm.CreateWorkflowRequestModel(databaseId="mydb1", workflowName="W", **kw)


# ==================== nested sub-model validation ====================

@pytest.mark.unit
class TestTagSchemaSubModelRuns:
    """``tagSchema`` is typed List[Dict[str, Any]] on the template create/update models, so
    TemplateTagFieldModel's own field bounds only apply if the model is invoked explicitly."""

    def test_oversized_label_rejected_on_create(self):
        with pytest.raises(ValidationError):
            pm.CreateTemplateRequestModel(
                templateName="T", tagSchema=[{"tagKey": "K", "label": "L" * 100000}])

    def test_oversized_label_rejected_on_update(self):
        with pytest.raises(ValidationError):
            pm.UpdateTemplateRequestModel(
                tagSchema=[{"tagKey": "K", "description": "D" * 100000}])

    def test_oversized_tag_key_rejected(self):
        with pytest.raises(ValidationError):
            pm.CreateTemplateRequestModel(
                templateName="T", tagSchema=[{"tagKey": "k" * 5000}])

    def test_oversized_serialized_default_rejected(self):
        with pytest.raises(ValidationError):
            pm.CreateTemplateRequestModel(
                templateName="T", tagSchema=[{"tagKey": "K", "default": "d" * 100000}])

    def test_non_object_entry_rejected(self):
        with pytest.raises(ValidationError):
            pm.CreateTemplateRequestModel(templateName="T", tagSchema=["notadict"])

    def test_a_realistic_tag_schema_still_passes(self):
        # Mirrors the shape the built-in cosmos-transfer / gr00t templates declare.
        request = pm.CreateTemplateRequestModel(templateName="T", tagSchema=[
            {"tagKey": "PROMPT", "type": "string", "default": "a prompt",
             "label": "Prompt", "description": "The generation prompt."},
            {"tagKey": "MODE", "type": "enum", "enumValues": ["fast", "slow"], "default": "fast"},
            {"tagKey": "EVAL_STEPS", "type": "integer", "default": 300},
            {"tagKey": "TAGS", "type": "string-list", "default": ["a", "b"]},
        ])
        # The entries stay plain dicts: templateTagSchema.validate_tag_schema inspects dicts and
        # would reject model instances as "not an object", and the handler persists them verbatim.
        assert all(isinstance(f, dict) for f in request.tagSchema)
        assert request.tagSchema[0]["tagKey"] == "PROMPT"

    def test_set_tag_schema_path_keeps_typed_fields(self):
        request = pm.SetTagSchemaRequestModel(fields=[
            {"tagKey": "PROMPT", "type": "string", "default": "a prompt"}])
        assert request.fields[0].tagKey == "PROMPT"


@pytest.mark.unit
class TestPipelineExecutionParametersSubModelRuns:
    """``pipelineExecutionParameters`` is typed Dict[str, Dict[str, Any]], so
    PipelineExecutionParameters' bounds only apply if the model is invoked explicitly."""

    def test_map_key_must_be_a_pipeline_id(self):
        with pytest.raises(ValidationError):
            em.ExecuteWorkflowRequestV2Model(
                pipelineExecutionParameters={"../../evil": {}})

    def test_bad_template_id_rejected(self):
        with pytest.raises(ValidationError):
            em.ExecuteWorkflowRequestV2Model(
                pipelineExecutionParameters={"pipe1": {"templateId": "../../evil"}})

    def test_oversized_tag_key_rejected(self):
        with pytest.raises(ValidationError):
            em.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={
                "pipe1": {"templateTags": [{"key": "k" * 5000, "value": "v"}]}})

    def test_oversized_tag_value_rejected(self):
        with pytest.raises(ValidationError):
            em.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={
                "pipe1": {"templateTags": [{"key": "K", "value": "v" * 200000}]}})

    def test_non_object_parameter_block_rejected(self):
        with pytest.raises(ValidationError):
            em.ExecuteWorkflowRequestV2Model(
                pipelineExecutionParameters={"pipe1": "notadict"})

    def test_typed_and_long_tag_values_still_pass(self):
        # A tag legitimately carries a long GenAI prompt and non-string typed values.
        request = em.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={
            "pipe1": {"templateId": "tmpl-a", "templateTags": [
                {"key": "PROMPT", "value": "p" * 20000},
                {"key": "STEPS", "value": 300},
                {"key": "FLAG", "value": True},
                {"key": "RATE", "value": 1.5},
                {"key": "LIST", "value": ["a", "b"]},
                {"key": "NONE", "value": None},
                {"key": "UNICODE", "value": "éà中文 \U0001F389"},
            ]}})
        # The blocks stay raw dicts: the resolution path reads them with .get() and persists the
        # caller's templateTags verbatim into the config snapshot.
        assert isinstance(request.pipelineExecutionParameters["pipe1"], dict)
        assert request.pipelineExecutionParameters["pipe1"]["templateId"] == "tmpl-a"


# ==================== unbounded collections ====================

@pytest.mark.unit
class TestCollectionBounds:
    def test_tag_schema_field_count_capped(self):
        over = [{"tagKey": "k%d" % i} for i in range(pm.MAX_TAG_SCHEMA_FIELDS + 1)]
        with pytest.raises(ValidationError):
            pm.SetTagSchemaRequestModel(fields=over)
        with pytest.raises(ValidationError):
            pm.CreateTemplateRequestModel(templateName="T", tagSchema=over)

    def test_enum_values_capped(self):
        with pytest.raises(ValidationError):
            pm.SetTagSchemaRequestModel(fields=[{
                "tagKey": "K", "type": "enum",
                "enumValues": ["e"] * (pm.MAX_TAG_ENUM_VALUES + 1)}])

    def test_enum_value_length_capped(self):
        with pytest.raises(ValidationError):
            pm.SetTagSchemaRequestModel(fields=[{
                "tagKey": "K", "type": "enum",
                "enumValues": ["e" * (pm.MAX_TAG_ENUM_VALUE_LENGTH + 1)]}])

    def test_input_file_filter_pattern_count_capped(self):
        over = ["*.x"] * (pm.MAX_INPUT_FILE_FILTER_PATTERNS + 1)
        with pytest.raises(ValidationError):
            _pipeline({"inputFileFilters": {"allow": over}})
        with pytest.raises(ValidationError):
            wm.SetTriggerRequestModel(inputFileFilters={"allow": over})

    def test_input_file_filter_pattern_length_capped(self):
        with pytest.raises(ValidationError):
            _pipeline({"inputFileFilters": {
                "allow": ["x" * (pm.MAX_INPUT_FILE_FILTER_PATTERN_LENGTH + 1)]}})

    def test_specified_pipelines_capped(self):
        over = [{"pipelineId": "pipe%03d" % i} for i in range(wm.MAX_SPECIFIED_PIPELINES + 1)]
        with pytest.raises(ValidationError):
            _workflow(specifiedPipelines=over)
        with pytest.raises(ValidationError):
            wm.UpdateWorkflowRequestModel(specifiedPipelines=over)

    def test_trigger_default_template_map_capped(self):
        over = {("mydb1:p%04d" % i): "tmpl1"
                for i in range(wm.MAX_TRIGGER_DEFAULT_TEMPLATES + 1)}
        with pytest.raises(ValidationError):
            wm.SetTriggerRequestModel(defaultTemplateIds=over)

    def test_pipeline_execution_parameter_map_capped(self):
        over = {("pipe%04d" % i): {}
                for i in range(em.MAX_PIPELINE_EXECUTION_PARAMETERS + 1)}
        with pytest.raises(ValidationError):
            em.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters=over)

    def test_template_tags_per_pipeline_capped(self):
        over = [{"key": "k%d" % i, "value": "v"}
                for i in range(em.MAX_TEMPLATE_TAGS_PER_PIPELINE + 1)]
        with pytest.raises(ValidationError):
            em.ExecuteWorkflowRequestV2Model(
                pipelineExecutionParameters={"pipe1": {"templateTags": over}})

    def test_custom_template_override_capped(self):
        with pytest.raises(ValidationError):
            em.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={
                "pipe1": {"customTemplateOverride":
                          "x" * (em.MAX_CUSTOM_TEMPLATE_OVERRIDE_LENGTH + 1)}})

    def test_collections_at_their_cap_still_pass(self):
        # Each cap is inclusive, and a workflow with the maximum step count is legitimate.
        _workflow(specifiedPipelines=[{"pipelineId": "pipe%03d" % i}
                                      for i in range(wm.MAX_SPECIFIED_PIPELINES)])
        pm.SetTagSchemaRequestModel(fields=[{"tagKey": "k%d" % i}
                                           for i in range(pm.MAX_TAG_SCHEMA_FIELDS)])
        _pipeline({"inputFileFilters": {
            "allow": ["*.x"] * pm.MAX_INPUT_FILE_FILTER_PATTERNS}})
        em.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={
            "pipe1": {"customTemplateOverride": "x" * 1024}})

    def test_a_realistic_filter_and_trigger_still_passes(self):
        _pipeline({"inputFileFilters": {"allow": ["*.glb", "*.usdz"], "exclude": ["*.tmp"]}})
        request = wm.SetTriggerRequestModel(
            inputFileFilters={"allow": ["*.glb", "*.usdz"], "exclude": ["*.tmp"]},
            defaultTemplateIds={"GLOBAL:pipe1": "tmpl-a", "mydb1:pipe2": "tmpl-b"})
        assert request.defaultTemplateIds["GLOBAL:pipe1"] == "tmpl-a"


# ==================== id rules on values used as DynamoDB keys ====================

@pytest.mark.unit
class TestIdRulesOnKeyValues:
    def test_workflow_ref_default_template_id_validated(self):
        with pytest.raises(ValidationError):
            _workflow(specifiedPipelines=[
                {"pipelineId": "pipe1", "defaultTemplateId": "../../evil"}])

    def test_trigger_default_template_id_value_validated(self):
        with pytest.raises(ValidationError):
            wm.SetTriggerRequestModel(defaultTemplateIds={"mydb1:pipe1": "../../evil"})

    def test_trigger_default_template_composite_key_shape_required(self):
        with pytest.raises(ValidationError):
            wm.SetTriggerRequestModel(defaultTemplateIds={"nocolon": "tmpl1"})

    def test_trigger_default_template_pipeline_id_validated(self):
        with pytest.raises(ValidationError):
            wm.SetTriggerRequestModel(defaultTemplateIds={"mydb1:../evil": "tmpl1"})

    def test_valid_ids_and_global_database_still_pass(self):
        # A pipeline may live in the GLOBAL database, and an empty templateId means "no default".
        wm.SetTriggerRequestModel(defaultTemplateIds={
            "GLOBAL:pipe1": "tmpl-a", "mydb1:pipe2": "tmpl-b", "mydb1:pipe3": ""})
        request = _workflow(specifiedPipelines=[{
            "pipelineId": "pipe1", "pipelineDatabaseId": "GLOBAL",
            "jobName": "job_1", "defaultTemplateId": "tmpl-a"}])
        assert request.specifiedPipelines[0].defaultTemplateId == "tmpl-a"


# ==================== boolean gates ====================

@pytest.mark.unit
class TestBooleanGates:
    @pytest.mark.parametrize("key", pm._SYSTEM_CONFIG_BOOLEAN_KEYS)
    def test_truthy_string_rejected_in_pipeline_system_config(self, key):
        # bool("false") is True, so a string here inverts the gate the author set.
        with pytest.raises(ValidationError):
            _pipeline({key: "false"})

    def test_truthy_string_rejected_in_output_target_allow_override(self):
        with pytest.raises(ValidationError):
            _workflow(systemConfig={"outputTarget": {"allowOverride": "false"}})

    def test_allow_override_checked_without_a_declared_location_type(self):
        # allowOverride is read at execute time whether or not locationType is declared.
        with pytest.raises(ValidationError):
            _workflow(systemConfig={"outputTarget": {"allowOverride": "true"}})

    def test_real_booleans_still_pass(self):
        _pipeline({"requireTemplate": True, "allowCustomTemplateOverride": False})
        _pipeline({"requireTemplate": False, "allowCustomTemplateOverride": True})
        request = _workflow(systemConfig={
            "inputFileArity": "none",
            "outputTarget": {"locationType": "asset", "allowOverride": True}})
        assert request.systemConfig["outputTarget"]["allowOverride"] is True


# ==================== path-traversal safety on S3 key components ====================

@pytest.mark.unit
class TestS3KeyComponentSafety:
    # The suffix is appended to an input file's aux-bucket preview prefix to build an S3 key.
    # Traversal and backslash cases are separated so each rule is exercised on its own: a value
    # carrying both would pass on either check and prove nothing about the other.
    @pytest.mark.parametrize("suffix", ["../../etc", "/a/../../b"])
    def test_aux_preview_suffix_traversal_rejected(self, suffix):
        with pytest.raises(ValidationError):
            _pipeline({"auxPreviewPipelineSuffix": suffix})

    @pytest.mark.parametrize("suffix", ["\\evil", "/a\\b"])
    def test_aux_preview_suffix_backslash_rejected(self, suffix):
        assert ".." not in suffix
        with pytest.raises(ValidationError):
            _pipeline({"auxPreviewPipelineSuffix": suffix})

    def test_aux_preview_suffix_length_capped(self):
        with pytest.raises(ValidationError):
            _pipeline({"auxPreviewPipelineSuffix":
                       "/" + "v" * pm.MAX_AUX_PREVIEW_SUFFIX_LENGTH})

    def test_real_aux_preview_suffix_still_passes(self):
        # The value the built-in pcPotreeViewer pipeline registers.
        request = _pipeline({"auxPreviewPipelineSuffix": "/PotreeViewer"})
        assert request.systemConfig["auxPreviewPipelineSuffix"] == "/PotreeViewer"

    @pytest.mark.parametrize("key", ["/a\\b.glb", "/dir\\file.glb", "/\\a.glb"])
    def test_relative_file_key_backslash_rejected(self, key):
        # A backslash is a legal S3 key character, so it survives into the key rather than being
        # rejected downstream, and it is how a Windows-style traversal is written. Each case carries
        # NO '..' so only the backslash rule can reject it — a value with both would pass on the
        # traversal check alone and prove nothing about this guard.
        assert ".." not in key
        with pytest.raises(ValidationError):
            em.ExecuteWorkflowRequestV2Model(inputFiles=[{
                "databaseId": "mydb1", "assetId": "a1", "relativeFileKey": key}])

    def test_relative_file_key_traversal_rejected(self):
        with pytest.raises(ValidationError):
            em.ExecuteWorkflowRequestV2Model(inputFiles=[{
                "databaseId": "mydb1", "assetId": "a1",
                "relativeFileKey": "/a/../../b.glb"}])

    def test_legitimate_file_paths_still_pass(self):
        # Spaces, parentheses, dots, unicode, and the whole-asset / folder forms are all legitimate.
        request = em.ExecuteWorkflowRequestV2Model(inputFiles=[
            {"databaseId": "mydb1", "assetId": "My Asset v1.2.glb",
             "relativeFileKey": "/a folder/my file (1).glb"},
            {"databaseId": "GLOBAL", "assetId": "assét.glb",
             "relativeFileKey": "/dössier/fïle é.glb"},
            {"databaseId": "mydb1", "assetId": "a1", "relativeFileKey": "/"},
            {"databaseId": "mydb1", "assetId": "a1", "relativeFileKey": "/folder/"},
        ])
        assert request.inputFiles[0].assetId == "My Asset v1.2.glb"
        assert request.inputFiles[2].relativeFileKey == "/"
