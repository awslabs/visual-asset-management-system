# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The shipped compliance schema templates (`documentation/complianceSchemaTemplates/*.json`): every
template keeps its envelope, its `schemaBody` parses as a `VamsRulesV1Schema` whose rules each build
their typed model, and the schema service's validator accepts it — so a template registered through
`POST /compliance/schemas` evaluates rather than failing as a non-vams-rules body. The pipeline
template targets the built-in 3D conversion pipeline as it is seeded."""

import json
import os

import pytest

from handlers.compliance import complianceSchemaService as svc
from models.compliance import MetadataRule, PipelineRule, RelationshipRule, VamsRulesV1Schema

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
TEMPLATES_DIR = os.path.join(_REPO_ROOT, "documentation", "complianceSchemaTemplates")
CONVERSION_SCHEMA_DIR = os.path.join(
    _REPO_ROOT, "backendPipelines", "conversion", "3dBasic", "vamsSchema")

ENVELOPE_KEYS = {"metadata", "schemaName", "description", "schemaBody"}
# The GLOBAL metadata schema the metadata-schema defaults construct seeds; the one metadata-schema
# reference a template can rely on being present.
SEEDED_METADATA_SCHEMA = ("GLOBAL", "defaultAsset")


def _template_paths():
    paths = sorted(os.path.join(TEMPLATES_DIR, name) for name in os.listdir(TEMPLATES_DIR)
                   if name.endswith(".json"))
    assert len(paths) >= 4, "the shipped template set was not found"
    return paths


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _rules(path):
    return VamsRulesV1Schema(**_load(path)["schemaBody"]).parse_rules()


@pytest.mark.unit
class TestEveryShippedTemplate:

    @pytest.mark.parametrize("path", _template_paths(), ids=os.path.basename)
    def test_the_envelope_is_kept_and_the_body_is_a_vams_rules_document(self, path):
        template = _load(path)
        assert set(template) == ENVELOPE_KEYS
        assert template["schemaName"] == os.path.basename(path)[:-len(".json")]
        assert set(template["metadata"]) >= {"name", "description", "version"}
        body = template["schemaBody"]
        assert body["schemaFormat"] == "vams-rules-v1"
        rules = VamsRulesV1Schema(**body).parse_rules()
        assert rules
        assert all(isinstance(rule, (MetadataRule, PipelineRule, RelationshipRule))
                   for rule in rules.values())

    @pytest.mark.parametrize("path", _template_paths(), ids=os.path.basename)
    def test_the_schema_service_accepts_the_body(self, path):
        assert svc.validate_schema_body(_load(path)["schemaBody"]) == (True, None)

    @pytest.mark.parametrize("path", _template_paths(), ids=os.path.basename)
    def test_metadata_rules_reference_the_seeded_metadata_schema(self, path):
        for rule in _rules(path).values():
            if isinstance(rule, MetadataRule):
                ref = rule.metadataSchemaRef
                assert (ref.databaseId, ref.schemaName) == SEEDED_METADATA_SCHEMA

    def test_the_set_exercises_every_rule_type(self):
        kinds = {type(rule) for path in _template_paths() for rule in _rules(path).values()}
        assert kinds == {MetadataRule, PipelineRule, RelationshipRule}


@pytest.mark.unit
class TestThe3dModelQualityTemplate:
    """Its pipeline rule targets the built-in 3D conversion pipeline exactly as the bundle under
    `backendPipelines/conversion/3dBasic/vamsSchema/` registers it."""

    def _pipeline_rule(self):
        rules = _rules(os.path.join(TEMPLATES_DIR, "3d-model-quality.json"))
        pipeline_rules = [rule for rule in rules.values() if isinstance(rule, PipelineRule)]
        assert len(pipeline_rules) == 1
        return pipeline_rules[0]

    def test_the_pipeline_ref_names_the_seeded_workflow_pipeline_and_template(self):
        ref = self._pipeline_rule().pipelineRef
        assert (ref.databaseId, ref.workflowId) == ("GLOBAL", "conversion-3d-basic")
        assert (ref.pipelineDatabaseId, ref.pipelineId) == ("GLOBAL", "conversion-3d-basic")
        assert ref.templateId == "convert-to-glb"
        template_path = os.path.join(CONVERSION_SCHEMA_DIR, "templates", f"{ref.templateId}.json")
        assert _load(template_path)["templateId"] == ref.templateId

    def test_the_selection_matches_one_file_by_the_pipelines_own_extensions(self):
        """The filter names source formats the pipeline accepts and leaves out the format its
        template writes back into the asset: with the output selected too, the asset's own
        converted file would be a second candidate and `matching` would no longer resolve to one
        file on the next evaluation."""
        rule = self._pipeline_rule()
        pipeline_config = _load(os.path.join(CONVERSION_SCHEMA_DIR, "pipeline.json"))["systemConfig"]
        assert pipeline_config["inputFileArity"] == "one"
        assert pipeline_config["assetScope"] == {"wholeAsset": False}
        assert rule.inputFiles.mode == "matching"
        accepted = pipeline_config["inputFileFilters"]["allow"]
        template_path = os.path.join(CONVERSION_SCHEMA_DIR, "templates", f"{rule.pipelineRef.templateId}.json")
        output_extension = json.loads(_load(template_path)["configBody"])["outputType"]
        assert f"*{output_extension}" in accepted, "the pipeline accepts its own output format"
        assert rule.inputFiles.filter == [
            pattern for pattern in accepted if pattern != f"*{output_extension}"]
        assert "leaves out *.glb" in _load(os.path.join(TEMPLATES_DIR, "3d-model-quality.json"))["description"]

    def test_the_checks_read_only_the_measurements_every_execution_provides(self):
        """The conversion pipeline writes no compliance-output document, so the rule can rely only
        on the two measurements derived from the execution itself."""
        fields = {check.outputField for check in self._pipeline_rule().checks}
        assert fields == {"execution_success", "processing_duration_seconds"}
