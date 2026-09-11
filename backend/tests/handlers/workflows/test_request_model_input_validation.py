# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Input validation on the pipeline / workflow / execution API REQUEST models.

Each class pairs a rejection test for the malformed shape with an acceptance test proving the rule
does not narrow what the system legitimately stores. The legitimate values are taken from the
built-in registration bundles (backendPipelines/**/vamsSchema), which are the widest real inputs:
names carry unicode, parentheses, slashes and apostrophes, template bodies are multi-line, and asset
ids carry dots and spaces.
"""

import pytest

from backend.backend.models import executions as em
from backend.backend.models import pipelines as pm
from backend.backend.models import workflows as wm


def _lambda_config():
    return {"executionType": "Lambda"}


def _deadline_config(**overrides):
    deadline = {"farmId": "farm-abc123", "queueId": "queue-abc123", "template": "specVersion: x"}
    deadline.update(overrides)
    return {"executionType": "DeadlineCloud", "waitForCallback": "Enabled",
            "deadlineCloud": deadline}


def _pipeline_ref():
    return [{"pipelineId": "pipeline-one"}]


# Control characters that must be rejected in a single-line field: NUL, LF, CR, and a C1 code point.
_CONTROL_VALUES = ("a\x00b", "a\nb", "a\rb", "a\x85b")


@pytest.mark.unit
class TestSingleLineTextRejectsControlCharacters:
    """`pipelineName` / `workflowName` / `category` are ABAC constraint fields (surfaced as `name`
    and `category` on the Tier-2 Casbin object) and are matched by the Casbin `equals` operator as
    regexMatch(value, '^<constraint>$'). Python's '$' also matches immediately before a trailing
    newline, so a value with one satisfies a constraint written without it while remaining a
    distinct stored value. All three also land in single-line log entries, where an embedded newline
    splits one record into two."""

    @pytest.mark.parametrize("value", _CONTROL_VALUES)
    def test_pipeline_name_rejects_control_characters(self, value):
        with pytest.raises(Exception):
            pm.CreatePipelineRequestModel(
                databaseId="database1", pipelineName=value, executionConfig=_lambda_config())

    @pytest.mark.parametrize("field", ("pipelineName", "category"))
    def test_pipeline_update_rejects_control_characters(self, field):
        with pytest.raises(Exception):
            pm.UpdatePipelineRequestModel(**{field: "a\nb"})

    def test_description_is_not_guarded_on_either_model(self):
        # Not an ABAC constraint field, and both builders offer a multi-line textarea, so
        # guarding it rejected exactly what that control produces (#104).
        assert pm.UpdatePipelineRequestModel(description="a\nb") is not None
        assert wm.UpdateWorkflowRequestModel(description="a\nb") is not None

    @pytest.mark.parametrize("value", _CONTROL_VALUES)
    def test_workflow_name_rejects_control_characters(self, value):
        with pytest.raises(Exception):
            wm.CreateWorkflowRequestModel(
                databaseId="database1", workflowName=value, specifiedPipelines=_pipeline_ref())

    @pytest.mark.parametrize("field", ("workflowName", "category"))
    def test_workflow_update_rejects_control_characters(self, field):
        with pytest.raises(Exception):
            wm.UpdateWorkflowRequestModel(**{field: "a\rb"})

    def test_template_name_rejects_control_characters(self):
        with pytest.raises(Exception):
            pm.CreateTemplateRequestModel(templateName="a\nb")
        with pytest.raises(Exception):
            pm.UpdateTemplateRequestModel(templateName="a\rb")

    def test_casbin_equals_would_match_a_trailing_newline_value(self):
        # Why the rule exists, expressed against the operator the enforcer builds.
        import re
        constraint = "^finance-pipeline$"
        assert re.match(constraint, "finance-pipeline\n") is not None
        assert re.match(constraint, "finance-pipelineX") is None


@pytest.mark.unit
class TestSingleLineTextAcceptsLegitimateNames:
    """Every built-in bundle name/category/description must still parse. These carry unicode
    em-dashes and accents, parentheses, slashes, apostrophes, commas and plus signs."""

    def test_pipeline_accepts_built_in_style_names(self):
        request = pm.CreatePipelineRequestModel(
            databaseId="database1",
            pipelineName="NVIDIA Cosmos 3 Super Image2Video (64B) — café",
            category="3D Reconstruction",
            description="Auto process 'images' + videos into 3D splats. path/like_this-ok",
            executionConfig=_lambda_config())
        assert request.category == "3D Reconstruction"

    def test_workflow_accepts_built_in_style_names(self):
        request = wm.CreateWorkflowRequestModel(
            databaseId="database1",
            workflowName="CAD/Mesh Metadata Extraction",
            category="Conversion",
            description="Coordinate transformation for point cloud data between CRS systems",
            specifiedPipelines=_pipeline_ref())
        assert request.workflowName == "CAD/Mesh Metadata Extraction"

    def test_template_accepts_multi_line_bodies(self):
        # The authored documents are deliberately exempt: only templateName is single-line.
        request = pm.CreateTemplateRequestModel(
            templateName="WGS84 to OSGB36 (LAZ output)",
            configFormat="raw",
            configBody="line one\nline two\n",
            inputInstructions="step 1\nstep 2")
        assert "\n" in request.configBody
        assert "\n" in request.inputInstructions


@pytest.mark.unit
class TestDeadlineCloudExecutionConfig:
    """The DeadlineCloud sub-block is baked into the deployed state machine's createJob task, so a
    malformed value surfaces at deploy/launch time rather than at save."""

    @pytest.mark.parametrize("farm_id", ('farm"; evil', "farm id", "farm\nid", "f"))
    def test_farm_id_must_be_an_id(self, farm_id):
        with pytest.raises(Exception):
            pm.CreatePipelineRequestModel(
                databaseId="database1", pipelineName="p",
                executionConfig=_deadline_config(farmId=farm_id))

    def test_queue_id_must_be_an_id(self):
        with pytest.raises(Exception):
            pm.CreatePipelineRequestModel(
                databaseId="database1", pipelineName="p",
                executionConfig=_deadline_config(queueId="queue id with spaces"))

    def test_storage_profile_id_must_be_an_id_when_supplied(self):
        with pytest.raises(Exception):
            pm.CreatePipelineRequestModel(
                databaseId="database1", pipelineName="p",
                executionConfig=_deadline_config(storageProfileId="not a valid id!"))

    def test_template_type_restricted_to_the_two_deadline_dialects(self):
        with pytest.raises(Exception):
            pm.CreatePipelineRequestModel(
                databaseId="database1", pipelineName="p",
                executionConfig=_deadline_config(templateType="EXCEL"))

    def test_template_body_is_bounded(self):
        # The template is embedded verbatim in the state-machine definition, which Step Functions
        # caps at 1 MB — an unbounded body fails the deploy after the row is already written.
        oversized = "x" * (pm.MAX_DEADLINE_TEMPLATE_LENGTH + 1)
        with pytest.raises(Exception):
            pm.CreatePipelineRequestModel(
                databaseId="database1", pipelineName="p",
                executionConfig=_deadline_config(template=oversized))

    def test_a_well_formed_deadline_config_is_accepted(self):
        request = pm.CreatePipelineRequestModel(
            databaseId="database1", pipelineName="Isaac Lab RL Training",
            executionConfig=_deadline_config(
                templateType="YAML", storageProfileId="sp-abc123", priority="50",
                template="x" * pm.MAX_DEADLINE_TEMPLATE_LENGTH))
        assert request.executionConfig["deadlineCloud"]["templateType"] == "YAML"


@pytest.mark.unit
class TestExecuteInputFileVersionId:
    """An S3 VersionId is URL-safe; the value is stored on the input/metadata rows and echoed in log
    lines, so the charset is pinned on the request rather than left to S3 to reject at read time."""

    def _request(self, version_id):
        return em.ExecuteWorkflowRequestV2Model(inputFiles=[{
            "databaseId": "database1", "assetId": "asset.glb",
            "relativeFileKey": "/model.glb", "versionId": version_id}])

    @pytest.mark.parametrize("version_id", ('v\n"; drop', "has space", "has/slash", "has:colon"))
    def test_malformed_version_id_rejected(self, version_id):
        with pytest.raises(Exception):
            self._request(version_id)

    @pytest.mark.parametrize("version_id", [
        "ZtnKwD9RuPg_34Bon2Iwrb5GG3lsYYyz",   # a real deployed S3 VersionId
        "_aHYAca3lg6s3QSKRyR3Z6bCgIaXIGOP",   # leading underscore is legitimate
        "null",                                # unversioned object
        "a.b-c_d",
        "",                                    # omitted -> latest
    ])
    def test_real_version_ids_accepted(self, version_id):
        assert self._request(version_id).inputFiles[0].versionId == version_id

    def test_asset_ids_and_paths_with_dots_and_spaces_still_accepted(self):
        # The asset id rule is ASSET_ID (not the strict ID pattern), and file paths carry spaces.
        request = em.ExecuteWorkflowRequestV2Model(inputFiles=[{
            "databaseId": "database1", "assetId": "my asset v1.2.glb",
            "relativeFileKey": "/sub folder/my model v1.2.glb"}])
        assert request.inputFiles[0].assetId == "my asset v1.2.glb"
