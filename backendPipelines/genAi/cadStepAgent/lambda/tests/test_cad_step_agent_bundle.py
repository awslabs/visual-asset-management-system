# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Consistency checks on the GenAI CAD STEP agent vamsSchema bundles.

The pipeline takes zero (generate) or one (modify) input file, and a workflow's inputFileArity is a
hard gate with no zero-or-one value, so the built-in ships TWO workflows over ONE pipeline: the main
bundle (`vamsSchema/`) registers the pipeline, both templates and the modify workflow; a template-less
second bundle (`vamsSchema/generateWorkflow/`) re-registers the identical pipeline and adds the generate
workflow. Both name their template through `specifiedPipelines[].defaultTemplateId`."""

import hashlib
import json
import os
import re
import sys

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA_ROOT = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "vamsSchema"))
_GENERATE_ROOT = os.path.join(_SCHEMA_ROOT, "generateWorkflow")
_REPO_ROOT = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "..", "..", ".."))
_CONSTRUCT = os.path.join(
    _REPO_ROOT, "infra", "lib", "nestedStacks", "pipelines", "genAi", "cadStepAgent", "constructs",
    "cadStepAgent-construct.ts")

PIPELINE_ID = "genai-cad-step-agent"
STEP_PATTERNS = {"*.stp", "*.step"}
STEP_EXTENSIONS = ".stp,.step"
TYPED_TAG_TYPES = {"integer", "number", "boolean", "string-list"}


def _load(root, *parts):
    with open(os.path.join(root, *parts), encoding="utf-8") as handle:
        return json.load(handle)


def _templates():
    template_dir = os.path.join(_SCHEMA_ROOT, "templates")
    return [_load(_SCHEMA_ROOT, "templates", name) for name in sorted(os.listdir(template_dir))
            if name.endswith(".json")]


def _digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


@pytest.mark.unit
class TestBundleLayout:
    def test_the_two_bundles_register_one_identical_pipeline(self):
        assert _digest(os.path.join(_SCHEMA_ROOT, "pipeline.json")) == _digest(
            os.path.join(_GENERATE_ROOT, "pipeline.json"))

    def test_the_second_bundle_carries_no_templates(self):
        # A single-template bundle has that template promoted to default on import, so a template in
        # the second bundle would fight the modify template for the pipeline's one default slot.
        assert not os.path.exists(os.path.join(_GENERATE_ROOT, "templates"))

    def test_pipeline_arity_is_the_lowest_any_template_needs(self):
        assert _load(_SCHEMA_ROOT, "pipeline.json")["systemConfig"]["inputFileArity"] == "none"

    def test_pipeline_requires_a_template_and_allows_a_custom_override(self):
        sc = _load(_SCHEMA_ROOT, "pipeline.json")["systemConfig"]
        assert sc["requireTemplate"] is True
        assert sc["allowCustomTemplateOverride"] is True

    def test_pipeline_json_carries_no_arn_or_account(self):
        text = json.dumps(_load(_SCHEMA_ROOT, "pipeline.json"))
        assert "arn:" not in text
        assert not re.search(r"\b[0-9]{12}\b", text)
        assert _load(_SCHEMA_ROOT, "pipeline.json")["executionConfig"]["lambda"] == {}

    def test_callback_is_enabled_with_a_bounded_timeout(self):
        ec = _load(_SCHEMA_ROOT, "pipeline.json")["executionConfig"]
        assert ec["waitForCallback"] == "Enabled"
        assert 300 <= int(ec["taskTimeout"]) <= 14400


@pytest.mark.unit
class TestWorkflows:
    def test_modify_workflow_takes_one_file_and_defaults_to_the_modify_template(self):
        wf = _load(_SCHEMA_ROOT, "workflow.json")
        assert wf["systemConfig"]["inputFileArity"] == "one"
        refs = wf["specifiedPipelines"]
        assert refs == [{"pipelineDatabaseId": "GLOBAL", "pipelineId": PIPELINE_ID,
                         "defaultTemplateId": "cad-step-agent-modify"}]

    def test_generate_workflow_takes_no_file_and_names_a_selectable_output_asset(self):
        wf = _load(_GENERATE_ROOT, "workflow.json")
        assert wf["systemConfig"]["inputFileArity"] == "none"
        # A no-input workflow has no asset to lock its output to, so the destination is chosen per run.
        assert wf["systemConfig"]["outputTarget"] == {"locationType": "asset", "allowOverride": True}
        assert wf["specifiedPipelines"][0]["defaultTemplateId"] == "cad-step-agent-generate"
        assert wf["triggers"] == []

    def test_neither_workflow_chains_on_another_workflows_output(self):
        for root in (_SCHEMA_ROOT, _GENERATE_ROOT):
            assert _load(root, "workflow.json")["systemConfig"]["allowWorkflowTriggerChaining"] is False

    def test_neither_workflow_sets_a_per_run_output_folder(self):
        # A modify run must land on the input file's own path, so no prefix extension is declared.
        for root in (_SCHEMA_ROOT, _GENERATE_ROOT):
            assert "defaultOutputFileBaseExecutionPathExtension" not in _load(root, "workflow.json")["systemConfig"]

    def test_trigger_default_template_exists_and_is_disarmed(self):
        trigger = _load(_SCHEMA_ROOT, "workflow.json")["triggers"][0]
        template_ids = {template["templateId"] for template in _templates()}
        for template_id in trigger["defaultTemplateIds"].values():
            assert template_id in template_ids
        assert trigger["enabled"] is False


@pytest.mark.unit
class TestTemplates:
    def test_exactly_one_default_template(self):
        defaults = [t["templateId"] for t in _templates() if t.get("isDefault")]
        assert defaults == ["cad-step-agent-modify"]

    def test_modify_template_raises_arity_to_one_for_step_files(self):
        modify = next(t for t in _templates() if t["templateId"] == "cad-step-agent-modify")
        assert modify["overrides"]["inputFileArity"] == "one"
        assert set(modify["overrides"]["inputFileFilters"]["allow"]) == STEP_PATTERNS

    def test_generate_template_keeps_the_pipelines_no_input_arity(self):
        generate = next(t for t in _templates() if t["templateId"] == "cad-step-agent-generate")
        assert "overrides" not in generate

    def test_every_placeholder_is_declared_and_every_tag_is_referenced(self):
        for template in _templates():
            declared = {field["tagKey"] for field in template["tagSchema"]}
            used = set(re.findall(r"{{([A-Za-z0-9_]+)}}", template["configBody"]))
            assert used == declared, (template["templateId"], used ^ declared)

    def test_json_body_quotes_placeholders_by_tag_type(self):
        for template in _templates():
            body = template["configBody"]
            assert template["configFormat"] == "json"
            for field in template["tagSchema"]:
                key = field["tagKey"]
                quoted = f'"{{{{{key}}}}}"' in body
                bare = re.search(r'(?<!")\{\{' + key + r'\}\}(?!")', body) is not None
                if field.get("type", "string") in TYPED_TAG_TYPES:
                    assert bare and not quoted, (template["templateId"], key, "typed tag must be bare")
                else:
                    assert quoted and not bare, (template["templateId"], key, "string tag must be quoted")

    def test_every_tag_has_a_usable_value_without_the_caller(self):
        # A required tag with no default cannot be satisfied by a zero-argument execute (a trigger, a
        # script), so every tag carries a default and none is required.
        for template in _templates():
            for field in template["tagSchema"]:
                assert field.get("required") is False, (template["templateId"], field["tagKey"])
                assert "default" in field, (template["templateId"], field["tagKey"])
            prompt = next(f for f in template["tagSchema"] if f["tagKey"] == "PROMPT")
            assert prompt["default"].strip(), template["templateId"]

    def test_tag_keys_do_not_collide_with_system_tags(self):
        source = open(os.path.join(_REPO_ROOT, "backend", "backend", "common", "workflows",
                                   "templateTags.py"), encoding="utf-8").read()
        system_names = {n.lower() for n in re.findall(r'^[A-Z_0-9]+ = "([A-Za-z0-9_]+)"', source, re.M)}
        for template in _templates():
            for field in template["tagSchema"]:
                assert field["tagKey"].lower() not in system_names, field["tagKey"]
                assert not field["tagKey"].lower().startswith("metadata_"), field["tagKey"]

    def test_enum_tags_declare_their_values(self):
        for template in _templates():
            for field in template["tagSchema"]:
                if field.get("type") == "enum":
                    assert field.get("enumValues"), field["tagKey"]
                    assert field.get("default") in field["enumValues"], field["tagKey"]


@pytest.mark.unit
class TestExtensionRule:
    """The STEP allow list appears identically wherever an input is admitted."""

    def test_modify_template_workflow_filters_and_trigger_agree(self):
        wf = _load(_SCHEMA_ROOT, "workflow.json")
        modify = next(t for t in _templates() if t["templateId"] == "cad-step-agent-modify")
        assert set(wf["systemConfig"]["inputFileFilters"]["allow"]) == STEP_PATTERNS
        assert set(wf["triggers"][0]["inputFileFilters"]["allow"]) == STEP_PATTERNS
        assert set(modify["overrides"]["inputFileFilters"]["allow"]) == STEP_PATTERNS

    def test_open_pipeline_default_matches(self):
        source = open(os.path.join(_LAMBDA_DIR, "openPipeline.py"), encoding="utf-8").read()
        assert f'os.environ.get("ALLOWED_INPUT_FILEEXTENSIONS", "{STEP_EXTENSIONS}")' in source

    def test_cdk_construct_literal_matches(self):
        assert os.path.isfile(_CONSTRUCT), _CONSTRUCT
        source = open(_CONSTRUCT, encoding="utf-8").read()
        match = re.search(r"const allowed(?:Input)?(?:File)?Extensions\s*=\s*([^;]*);", source)
        assert match, "no allow-list declaration in the construct"
        assert "".join(re.findall(r'"([^"]*)"', match.group(1))) == STEP_EXTENSIONS

    def test_descriptions_name_both_step_spellings(self):
        for root in (_SCHEMA_ROOT, _GENERATE_ROOT):
            for name in ("pipeline.json", "workflow.json"):
                description = _load(root, name)["description"]
                assert ".stp" in description and ".step" in description, (root, name)


@pytest.mark.unit
class TestImporterAcceptsBothBundles:
    """The backend importer's structural pass accepts each bundle as authored."""

    @pytest.fixture(autouse=True)
    def _importer(self):
        backend_dir = os.path.join(_REPO_ROOT, "backend", "backend")
        sys.path.insert(0, backend_dir)
        try:
            import importlib
            self.importer = importlib.import_module("common.workflows.vamsSchemaImport")
            yield
        finally:
            sys.path.remove(backend_dir)

    @staticmethod
    def _bundle(root):
        bundle = {"pipeline": _load(root, "pipeline.json"), "workflow": _load(root, "workflow.json")}
        template_dir = os.path.join(root, "templates")
        if os.path.isdir(template_dir):
            bundle["templates"] = [_load(root, "templates", n) for n in sorted(os.listdir(template_dir))]
        return bundle

    def test_no_unknown_keys(self):
        for root in (_SCHEMA_ROOT, _GENERATE_ROOT):
            assert self.importer.unknown_bundle_keys(self._bundle(root)) == []

    def test_requests_build_with_the_pipeline_and_workflow_ids(self):
        overrides = {"lambdaName": "vamsExecuteCadStepAgent-fn"}
        main = self.importer.build_import_requests(
            self._bundle(_SCHEMA_ROOT), resource_overrides=overrides,
            id_overrides={"pipelineId": PIPELINE_ID, "workflowId": f"{PIPELINE_ID}-modify"})
        generate = self.importer.build_import_requests(
            self._bundle(_GENERATE_ROOT), resource_overrides=overrides,
            id_overrides={"pipelineId": PIPELINE_ID, "workflowId": f"{PIPELINE_ID}-generate"})
        main_text = json.dumps(main)
        generate_text = json.dumps(generate)
        assert f"{PIPELINE_ID}-modify" in main_text and "cad-step-agent-generate" in main_text
        assert f"{PIPELINE_ID}-generate" in generate_text and "templates" not in generate_text.replace(
            "defaultTemplateId", "")
