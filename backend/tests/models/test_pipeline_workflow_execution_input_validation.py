# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Input validation on the pipeline / workflow / execution API request models and their sub-models.

Each rejection case is paired with an acceptance case for the legitimate input nearest to it, so a
bound cannot be tightened past what the API accepts and stores today. Asset ids carrying dots and
spaces, file paths carrying spaces and unicode, and unicode metadata/tag values are all legitimate.

The nested-model cases are the point of the file: `tagSchema` and `pipelineExecutionParameters` are
declared as plain dict collections on the request, so the sub-model that describes their entries only
runs if the request model explicitly drives it.
"""

import pytest

from backend.backend.models import executions as ex
from backend.backend.models import pipelines as pl
from backend.backend.models import workflows as wf


def _pipeline(system_config=None, **kw):
    kw.setdefault("databaseId", "mydb1")
    kw.setdefault("pipelineName", "a pipeline")
    kw.setdefault("executionConfig", {"executionType": "Lambda"})
    return pl.CreatePipelineRequestModel(systemConfig=system_config or {}, **kw)


def _workflow(**kw):
    kw.setdefault("databaseId", "mydb1")
    kw.setdefault("workflowName", "a workflow")
    kw.setdefault("specifiedPipelines", [{"pipelineId": "pipe1"}])
    return wf.CreateWorkflowRequestModel(**kw)


# ==================== pipeline systemConfig ====================

@pytest.mark.unit
class TestAuxPreviewPipelineSuffix:
    """The suffix is appended to an input file's auxiliary-bucket preview prefix to build an S3 key
    (templateRender), so traversal and backslash forms must not reach it."""

    @pytest.mark.parametrize("suffix", ["../../../etc", "/a/../../b", ".."])
    def test_traversal_rejected(self, suffix):
        with pytest.raises(Exception):
            _pipeline({"auxPreviewPipelineSuffix": suffix})

    def test_backslash_rejected(self):
        with pytest.raises(Exception):
            _pipeline({"auxPreviewPipelineSuffix": "\\PotreeViewer"})

    def test_oversized_rejected(self):
        with pytest.raises(Exception):
            _pipeline({"auxPreviewPipelineSuffix": "/" + "x" * pl.MAX_AUX_PREVIEW_SUFFIX_LENGTH})

    def test_real_viewer_suffix_accepted(self):
        # The value the built-in point-cloud Potree pipeline registers.
        req = _pipeline({"auxPreviewPipelineSuffix": "/PotreeViewer"})
        assert req.systemConfig["auxPreviewPipelineSuffix"] == "/PotreeViewer"

    def test_empty_suffix_accepted(self):
        assert _pipeline({"auxPreviewPipelineSuffix": ""}) is not None


@pytest.mark.unit
class TestSystemConfigBooleanGates:
    """A truthy STRING in a boolean gate reads back as True. For allowCustomTemplateOverride that
    means accepting caller-supplied template bodies on a pipeline configured to refuse them."""

    @pytest.mark.parametrize("key", ["requireTemplate", "allowCustomTemplateOverride"])
    @pytest.mark.parametrize("value", ["false", "true", "0", 1])
    def test_non_boolean_rejected(self, key, value):
        with pytest.raises(Exception):
            _pipeline({key: value})

    @pytest.mark.parametrize("key", ["requireTemplate", "allowCustomTemplateOverride"])
    @pytest.mark.parametrize("value", [True, False])
    def test_real_boolean_accepted(self, key, value):
        assert _pipeline({key: value}).systemConfig[key] is value

    def test_omitted_gates_accepted(self):
        assert _pipeline({"inputFileArity": "one"}) is not None


@pytest.mark.unit
class TestInputFileFilterBounds:
    """Every filter pattern is matched against every candidate input file, so the list length
    multiplies the per-execution match work."""

    def test_too_many_patterns_rejected(self):
        with pytest.raises(Exception):
            _pipeline({"inputFileFilters": {
                "allow": ["*.glb"] * (pl.MAX_INPUT_FILE_FILTER_PATTERNS + 1)}})

    def test_oversized_pattern_rejected(self):
        with pytest.raises(Exception):
            _pipeline({"inputFileFilters": {
                "allow": ["x" * (pl.MAX_INPUT_FILE_FILTER_PATTERN_LENGTH + 1)]}})

    def test_realistic_filters_accepted(self):
        req = _pipeline({"inputFileFilters": {
            "allow": ["*.glb", "*.usdz", "*.obj"], "exclude": ["*.tmp"]}})
        assert req.systemConfig["inputFileFilters"]["allow"] == ["*.glb", "*.usdz", "*.obj"]

    def test_at_cap_accepted(self):
        assert _pipeline({"inputFileFilters": {
            "allow": ["*.glb"] * pl.MAX_INPUT_FILE_FILTER_PATTERNS}}) is not None


# ==================== template tag schema (nested sub-model) ====================

@pytest.mark.unit
class TestTagSchemaFieldBoundsRunOnDictPaths:
    """`tagSchema` on the template create/update requests is typed List[Dict[str, Any]], so
    TemplateTagFieldModel does not run implicitly — the request model must drive it."""

    def test_too_many_fields_rejected_on_create(self):
        with pytest.raises(Exception):
            pl.CreateTemplateRequestModel(
                templateName="t",
                tagSchema=[{"tagKey": "k%d" % i} for i in range(pl.MAX_TAG_SCHEMA_FIELDS + 1)])

    def test_too_many_fields_rejected_on_update(self):
        with pytest.raises(Exception):
            pl.UpdateTemplateRequestModel(
                tagSchema=[{"tagKey": "k%d" % i} for i in range(pl.MAX_TAG_SCHEMA_FIELDS + 1)])

    def test_too_many_fields_rejected_on_set_tag_schema(self):
        with pytest.raises(Exception):
            pl.SetTagSchemaRequestModel(
                fields=[{"tagKey": "k%d" % i} for i in range(pl.MAX_TAG_SCHEMA_FIELDS + 1)])

    def test_oversized_label_rejected_through_the_dict_path(self):
        with pytest.raises(Exception):
            pl.CreateTemplateRequestModel(
                templateName="t",
                tagSchema=[{"tagKey": "k", "label": "L" * (pl.MAX_TAG_TEXT_LENGTH + 1)}])

    def test_oversized_description_rejected_through_the_dict_path(self):
        with pytest.raises(Exception):
            pl.CreateTemplateRequestModel(
                templateName="t",
                tagSchema=[{"tagKey": "k", "description": "d" * (pl.MAX_TAG_TEXT_LENGTH + 1)}])

    def test_oversized_tag_key_rejected_through_the_dict_path(self):
        with pytest.raises(Exception):
            pl.CreateTemplateRequestModel(
                templateName="t",
                tagSchema=[{"tagKey": "k" * (pl.MAX_TAG_KEY_LENGTH + 1)}])

    def test_non_object_entry_rejected(self):
        with pytest.raises(Exception):
            pl.CreateTemplateRequestModel(templateName="t", tagSchema=["not-an-object"])

    def test_realistic_tag_schema_accepted_on_create(self):
        # The shape the built-in GenAI templates register.
        req = pl.CreateTemplateRequestModel(templateName="t", tagSchema=[
            {"tagKey": "PROMPT", "type": "string", "default": "", "label": "Prompt",
             "description": "The generation prompt"},
            {"tagKey": "EVAL_STEPS", "type": "integer", "default": 300},
        ])
        assert [f["tagKey"] for f in req.tagSchema] == ["PROMPT", "EVAL_STEPS"]

    def test_tag_schema_entries_stay_dicts(self):
        # validate_tag_schema inspects dicts and rejects model instances as "not an object", so the
        # validation pass must not reshape the entries.
        req = pl.CreateTemplateRequestModel(
            templateName="t", tagSchema=[{"tagKey": "PROMPT", "type": "string"}])
        assert isinstance(req.tagSchema[0], dict)

    def test_omitted_tag_schema_accepted(self):
        assert pl.CreateTemplateRequestModel(templateName="t").tagSchema is None


@pytest.mark.unit
class TestTemplateTagFieldModel:
    def test_oversized_enum_list_rejected(self):
        with pytest.raises(Exception):
            pl.TemplateTagFieldModel(
                tagKey="k", type="enum", enumValues=["e"] * (pl.MAX_TAG_ENUM_VALUES + 1))

    def test_oversized_enum_value_rejected(self):
        with pytest.raises(Exception):
            pl.TemplateTagFieldModel(
                tagKey="k", type="enum",
                enumValues=["e" * (pl.MAX_TAG_ENUM_VALUE_LENGTH + 1)])

    def test_oversized_default_rejected(self):
        with pytest.raises(Exception):
            pl.TemplateTagFieldModel(tagKey="k", default="d" * (pl.MAX_TAG_DEFAULT_LENGTH + 1))

    def test_oversized_non_string_default_rejected(self):
        # A default is typed Any, so it is bounded by its serialized length.
        with pytest.raises(Exception):
            pl.TemplateTagFieldModel(tagKey="k", type="string-list",
                                     default=["x" * 100] * pl.MAX_TAG_DEFAULT_LENGTH)

    def test_enum_with_values_accepted(self):
        field = pl.TemplateTagFieldModel(tagKey="MODE", type="enum",
                                         enumValues=["fast", "slow"], default="fast")
        assert field.enumValues == ["fast", "slow"]

    @pytest.mark.parametrize("default", ["a prompt", 300, 1.5, True, ["a", "b"], None])
    def test_typed_defaults_accepted(self, default):
        assert pl.TemplateTagFieldModel(tagKey="k", default=default).default == default

    def test_unicode_default_accepted(self):
        assert pl.TemplateTagFieldModel(tagKey="k", default="éà中文 \U0001F389").default


# ==================== workflow ====================

@pytest.mark.unit
class TestOutputTargetBooleanGate:
    """allowOverride decides whether an execute request may redirect output away from the input
    asset, so a truthy string stored here opens the override the author disabled."""

    @pytest.mark.parametrize("value", ["false", "true", 0])
    def test_non_boolean_rejected(self, value):
        with pytest.raises(Exception):
            _workflow(systemConfig={"outputTarget": {"locationType": "asset",
                                                     "allowOverride": value}})

    def test_non_boolean_rejected_when_location_type_omitted(self):
        # allowOverride is read at execute time whether or not a locationType is declared (the
        # default is 'asset'), so the gate check cannot sit behind the locationType early return.
        with pytest.raises(Exception):
            _workflow(systemConfig={"outputTarget": {"allowOverride": "false"}})

    @pytest.mark.parametrize("value", [True, False])
    def test_real_boolean_accepted(self, value):
        req = _workflow(systemConfig={"outputTarget": {"locationType": "none",
                                                       "allowOverride": value}})
        assert req.systemConfig["outputTarget"]["allowOverride"] is value

    def test_arity_none_with_override_accepted(self):
        assert _workflow(systemConfig={
            "inputFileArity": "none",
            "outputTarget": {"locationType": "asset", "allowOverride": True}}) is not None


@pytest.mark.unit
class TestSpecifiedPipelinesBounds:
    """Each step becomes a Step Functions task state in one state-machine definition, plus a
    pipeline-execution row and a config + manifest S3 object per run."""

    def test_too_many_pipelines_rejected_on_create(self):
        with pytest.raises(Exception):
            _workflow(specifiedPipelines=[{"pipelineId": "pipe%03d" % i}
                                          for i in range(wf.MAX_SPECIFIED_PIPELINES + 1)])

    def test_too_many_pipelines_rejected_on_update(self):
        with pytest.raises(Exception):
            wf.UpdateWorkflowRequestModel(
                specifiedPipelines=[{"pipelineId": "pipe%03d" % i}
                                    for i in range(wf.MAX_SPECIFIED_PIPELINES + 1)])

    def test_at_cap_accepted(self):
        req = _workflow(specifiedPipelines=[{"pipelineId": "pipe%03d" % i}
                                            for i in range(wf.MAX_SPECIFIED_PIPELINES)])
        assert len(req.specifiedPipelines) == wf.MAX_SPECIFIED_PIPELINES

    def test_realistic_multi_step_workflow_accepted(self):
        req = _workflow(specifiedPipelines=[
            {"pipelineId": "convert-1", "pipelineDatabaseId": "GLOBAL", "jobName": "job_1"},
            {"pipelineId": "preview-1", "jobName": "job_2"},
        ])
        assert len(req.specifiedPipelines) == 2


@pytest.mark.unit
class TestSpecifiedPipelineDefaultTemplateId:
    """defaultTemplateId resolves the fallback template as a DynamoDB sort key at execute time."""

    @pytest.mark.parametrize("bad", ["../../evil", "a/b", "x", "has space"])
    def test_malformed_rejected(self, bad):
        with pytest.raises(Exception):
            _workflow(specifiedPipelines=[{"pipelineId": "pipe1", "defaultTemplateId": bad}])

    def test_valid_template_id_accepted(self):
        req = _workflow(specifiedPipelines=[
            {"pipelineId": "pipe1", "defaultTemplateId": "tmpl-a_1"}])
        assert req.specifiedPipelines[0].defaultTemplateId == "tmpl-a_1"

    def test_omitted_default_template_id_accepted(self):
        assert _workflow(specifiedPipelines=[{"pipelineId": "pipe1"}]) is not None


@pytest.mark.unit
class TestTriggerDefaultTemplateIds:
    """The map is {'<pipelineDatabaseId>:<pipelineId>': templateId}; all three parts are DynamoDB
    key values used to resolve the template a headless run launches with."""

    def test_key_without_separator_rejected(self):
        # A separator-less key is named as such rather than reported as a malformed pipeline id,
        # which is what the bare id rule on the empty second half would otherwise produce.
        with pytest.raises(Exception) as excinfo:
            wf.SetTriggerRequestModel(defaultTemplateIds={"nocolonhere": "tmpl-a"})
        assert "pipelineDatabaseId" in str(excinfo.value)

    def test_malformed_pipeline_id_in_key_rejected(self):
        with pytest.raises(Exception):
            wf.SetTriggerRequestModel(defaultTemplateIds={"mydb1:../evil": "tmpl-a"})

    def test_malformed_database_id_in_key_rejected(self):
        with pytest.raises(Exception):
            wf.SetTriggerRequestModel(defaultTemplateIds={"../evil:pipe1": "tmpl-a"})

    def test_malformed_template_id_value_rejected(self):
        with pytest.raises(Exception):
            wf.SetTriggerRequestModel(defaultTemplateIds={"mydb1:pipe1": "../../evil"})

    def test_too_many_entries_rejected(self):
        with pytest.raises(Exception):
            wf.SetTriggerRequestModel(defaultTemplateIds={
                ("mydb1:p%04d" % i): "tmpl-a"
                for i in range(wf.MAX_TRIGGER_DEFAULT_TEMPLATES + 1)})

    def test_realistic_map_accepted(self):
        req = wf.SetTriggerRequestModel(defaultTemplateIds={
            "GLOBAL:pipe1": "tmpl-a", "mydb1:pipe2": "tmpl-b"})
        assert req.defaultTemplateIds["GLOBAL:pipe1"] == "tmpl-a"

    def test_empty_value_accepted_as_no_default(self):
        # An empty templateId means "no default for this pipeline" and is skipped downstream.
        assert wf.SetTriggerRequestModel(
            defaultTemplateIds={"mydb1:pipe1": ""}).defaultTemplateIds == {"mydb1:pipe1": ""}

    def test_empty_map_accepted(self):
        assert wf.SetTriggerRequestModel(defaultTemplateIds={}).defaultTemplateIds == {}

    def test_trigger_filter_pattern_flood_rejected(self):
        with pytest.raises(Exception):
            wf.SetTriggerRequestModel(inputFileFilters={
                "allow": ["*.glb"] * (pl.MAX_INPUT_FILE_FILTER_PATTERNS + 1)})

    def test_realistic_trigger_filters_accepted(self):
        req = wf.SetTriggerRequestModel(
            inputFileFilters={"allow": ["*.glb", "*.usdz"], "exclude": ["*.tmp"]})
        assert req.inputFileFilters["exclude"] == ["*.tmp"]


# ==================== execute request ====================

@pytest.mark.unit
class TestPipelineExecutionParametersNested:
    """`pipelineExecutionParameters` is typed Dict[str, Dict[str, Any]], so the
    PipelineExecutionParameters sub-model only runs because the request model drives it."""

    def test_too_many_entries_rejected(self):
        with pytest.raises(Exception):
            ex.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={
                ("pipe%04d" % i): {}
                for i in range(ex.MAX_PIPELINE_EXECUTION_PARAMETERS + 1)})

    @pytest.mark.parametrize("bad_key", ["../../evil", "x", "has space", "a/b"])
    def test_malformed_pipeline_id_key_rejected(self, bad_key):
        with pytest.raises(Exception):
            ex.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={bad_key: {}})

    def test_malformed_template_id_rejected(self):
        with pytest.raises(Exception):
            ex.ExecuteWorkflowRequestV2Model(
                pipelineExecutionParameters={"pipe1": {"templateId": "../evil"}})

    def test_non_object_entry_rejected(self):
        with pytest.raises(Exception):
            ex.ExecuteWorkflowRequestV2Model(
                pipelineExecutionParameters={"pipe1": "not-an-object"})

    def test_too_many_template_tags_rejected(self):
        with pytest.raises(Exception):
            ex.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={"pipe1": {
                "templateTags": [{"key": "k%d" % i, "value": "v"}
                                 for i in range(ex.MAX_TEMPLATE_TAGS_PER_PIPELINE + 1)]}})

    def test_oversized_tag_key_rejected(self):
        with pytest.raises(Exception):
            ex.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={"pipe1": {
                "templateTags": [{"key": "k" * (ex.MAX_TEMPLATE_TAG_KEY_LENGTH + 1),
                                  "value": "v"}]}})

    def test_oversized_tag_value_rejected(self):
        with pytest.raises(Exception):
            ex.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={"pipe1": {
                "templateTags": [{"key": "k",
                                  "value": "v" * (ex.MAX_TEMPLATE_TAG_VALUE_LENGTH + 1)}]}})

    def test_oversized_custom_template_override_rejected(self):
        with pytest.raises(Exception):
            ex.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={"pipe1": {
                "customTemplateOverride": "x" * (
                    ex.MAX_CUSTOM_TEMPLATE_OVERRIDE_LENGTH + 1)}})

    def test_parameters_stay_raw_dicts(self):
        # The resolution path reads these with .get() and persists templateTags verbatim into the
        # config snapshot, so the validation pass must not reshape them.
        req = ex.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={
            "pipe1": {"templateId": "tmpl-a", "templateTags": [{"key": "PROMPT", "value": "p"}]}})
        entry = req.pipelineExecutionParameters["pipe1"]
        assert isinstance(entry, dict) and isinstance(entry["templateTags"][0], dict)

    def test_long_genai_prompt_accepted(self):
        # A template tag legitimately carries a long generation prompt.
        req = ex.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={"pipe1": {
            "templateId": "tmpl-a",
            "templateTags": [{"key": "PROMPT", "value": "p" * 20000}]}})
        assert len(req.pipelineExecutionParameters["pipe1"]["templateTags"][0]["value"]) == 20000

    @pytest.mark.parametrize("value", [300, 1.5, True, ["a", "b"], None, "éà中文 \U0001F389"])
    def test_typed_and_unicode_tag_values_accepted(self, value):
        req = ex.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={"pipe1": {
            "templateTags": [{"key": "K", "value": value}]}})
        assert req.pipelineExecutionParameters["pipe1"]["templateTags"][0]["value"] == value

    def test_large_but_legal_override_accepted(self):
        assert ex.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={"pipe1": {
            "customTemplateOverride": "x" * (1024 * 1024)}}) is not None

    def test_empty_parameters_accepted(self):
        assert ex.ExecuteWorkflowRequestV2Model().pipelineExecutionParameters == {}


@pytest.mark.unit
class TestRelativeFileKeyPathSafety:
    """relativeFileKey becomes part of an S3 key, so traversal forms must not reach it. Spaces and
    unicode are legitimate in a file path and stay accepted."""

    def test_backslash_rejected(self):
        # No ".." in this key, so only the backslash guard can reject it.
        with pytest.raises(Exception) as excinfo:
            ex.ExecuteWorkflowRequestV2Model(inputFiles=[{
                "databaseId": "mydb1", "assetId": "a1",
                "relativeFileKey": "/dir\\escape.glb"}])
        assert "backslash" in str(excinfo.value)

    def test_traversal_rejected(self):
        with pytest.raises(Exception):
            ex.ExecuteWorkflowRequestV2Model(inputFiles=[{
                "databaseId": "mydb1", "assetId": "a1",
                "relativeFileKey": "/../../etc/passwd"}])

    def test_missing_leading_slash_rejected(self):
        with pytest.raises(Exception):
            ex.ExecuteWorkflowRequestV2Model(inputFiles=[{
                "databaseId": "mydb1", "assetId": "a1", "relativeFileKey": "no-slash.glb"}])

    def test_spaces_and_parens_accepted(self):
        req = ex.ExecuteWorkflowRequestV2Model(inputFiles=[{
            "databaseId": "mydb1", "assetId": "My Asset v1.2.glb",
            "relativeFileKey": "/a folder/my file (1).glb"}])
        assert req.inputFiles[0].relativeFileKey == "/a folder/my file (1).glb"

    def test_unicode_path_accepted(self):
        req = ex.ExecuteWorkflowRequestV2Model(inputFiles=[{
            "databaseId": "GLOBAL", "assetId": "assét.glb",
            "relativeFileKey": "/dössier/fïle é.glb"}])
        assert req.inputFiles[0].relativeFileKey == "/dössier/fïle é.glb"

    @pytest.mark.parametrize("key", ["/", "/folder/", "/x"])
    def test_whole_asset_and_folder_selections_accepted(self, key):
        req = ex.ExecuteWorkflowRequestV2Model(inputFiles=[{
            "databaseId": "mydb1", "assetId": "a1", "relativeFileKey": key}])
        assert req.inputFiles[0].relativeFileKey == key
