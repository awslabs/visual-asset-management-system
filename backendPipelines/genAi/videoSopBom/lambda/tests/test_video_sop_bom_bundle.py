#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""What ships in the genAi/videoSopBom lambda directory and vamsSchema bundle.

Three groups: the vendored files (customLogging redaction, manifestHelper and config_schema.json
identity), the registration bundle's invariants (arity, scopes, the five-places allow list, two
templates with one default, every tag referenced with type-driven quoting, no trigger, the timeout
chain's bundle member), and the bridge between the templates and the config schema the lambda
validates against (the rendered defaults conform)."""

import copy
import hashlib
import importlib.util
import json
import os
import re
import sys

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PIPELINE_DIR = os.path.dirname(_LAMBDA_DIR)
_SCHEMA_ROOT = os.path.join(_PIPELINE_DIR, "vamsSchema")
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_PIPELINE_DIR)))

# The five-places allow list (master plan Global Constraints). Spelled as the CDK / openPipeline
# literal; the bundle's fnmatch spelling is derived by _as_patterns.
_ALLOWED_EXTENSIONS = ".mp4,.mov,.m4v,.webm,.mkv"


def _load_by_path(module_name, path):
    """Execute a lambda-directory module from its file under a suite-private name.

    conftest stubs `customLogging` so the handlers import without a live powertools setup; the
    redaction test needs the REAL logger module, so it is loaded by path rather than by name."""
    assert os.path.isfile(path), path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestCustomLoggingRedaction:
    """Neither task token is written to any log (spec D18): the formatter redacts every spelling
    the handlers log under, in nested dicts and in lists of dicts."""

    def _logger_module(self):
        return _load_by_path(
            "video_sop_bom_customlogging_logger_undertest",
            os.path.join(_LAMBDA_DIR, "customLogging", "logger.py"))

    @pytest.mark.parametrize("key", [
        "authorization", "externalSfnTaskToken", "sfnExternalTaskToken",
        "taskToken", "TaskToken", "TASK_TOKEN",
    ])
    def test_every_token_spelling_is_redacted(self, key):
        mod = self._logger_module()
        masked = mod.mask_sensitive_data(event={"message": {key: "AQoDYXdzEJr", "keep": 1}})
        assert masked["message"][key] == "<redacted>"
        assert masked["message"]["keep"] == 1

    def test_the_spec_keys_are_declared(self):
        # The spec names four keys; the module may redact more, never fewer.
        mod = self._logger_module()
        assert {"authorization", "externalSfnTaskToken", "taskToken", "TASK_TOKEN"} <= set(mod.KEYS_TO_REDACT)

    def test_lists_of_dicts_are_walked(self):
        # A list of dicts is where the coordinateTransform copy's walk stops: it recurses into dict
        # VALUES only, so a token keyed inside a list member would have passed through. (The redactor
        # keys on dict keys; no WP02 handler logs a Batch name/value environment list.)
        mod = self._logger_module()
        masked = mod.mask_sensitive_data(event={"environment": [{"TASK_TOKEN": "x"}, {"other": "y"}]})
        assert masked["environment"] == [{"TASK_TOKEN": "<redacted>"}, {"other": "y"}]

    def test_a_redacted_key_holding_a_dict_is_still_redacted(self):
        mod = self._logger_module()
        masked = mod.mask_sensitive_data(event={"authorization": {"nested": "secret"}})
        assert masked["authorization"] == "<redacted>"

    def test_unrelated_values_pass_through_unchanged(self):
        mod = self._logger_module()
        event = {"inputFiles": [{"key": "a/b.mp4", "versionId": "v1"}], "count": 2, "flag": True}
        assert mod.mask_sensitive_data(event=copy.deepcopy(event)) == event

    def test_safe_logger_builds_a_powertools_logger(self):
        mod = self._logger_module()
        logger = mod.safeLogger(service="VideoSopBomTest")
        assert logger.service == "VideoSopBomTest"

    def test_the_built_logger_redacts_a_logged_dict_end_to_end(self):
        # The tests above exercise mask_sensitive_data directly; this one logs THROUGH the built
        # Logger, so a safeLogger that dropped `logger_formatter=CustomFormatter()` fails here (it
        # passes every other test in this class while writing the token). The handler is injected
        # the way powertools allows (Logger(logger_handler=...)); the service name is unique in this
        # process because powertools wires a logging.Logger by name once and ignores a second
        # handler for the same name.
        import io
        import logging
        mod = self._logger_module()
        buf = io.StringIO()
        logger = mod.safeLogger(service="VideoSopBomRedactionE2E",
                                logger_handler=logging.StreamHandler(buf))
        logger.info({"TaskToken": "AQoDYXdzEJr", "sfnExternalTaskToken": "AQoDYXdzEJr",
                     "keep": "visible-control"})
        out = buf.getvalue()
        assert "AQoDYXdzEJr" not in out
        assert out.count("<redacted>") == 2
        # Positive control: the line was written and an unredacted key survives in it.
        assert "visible-control" in out


def _normalized_digest(path):
    """sha256 of the file's text after universal-newline normalization — the identity test in
    cosmos/transfer/lambda/tests/test_manifest_metadata_v2.py hashes the same way, so a CRLF
    checkout does not read as drift."""
    with open(path, "r", encoding="utf-8", newline=None) as fh:
        return hashlib.sha256(fh.read().encode("utf-8")).hexdigest()


_CANONICAL_MANIFEST_HELPER = os.path.join(
    _REPO_ROOT, "backendPipelines", "genAi", "nvidia", "cosmos", "transfer", "lambda",
    "manifestHelper.py")
_CONTAINER_CONFIG_SCHEMA = os.path.join(
    _PIPELINE_DIR, "container", "video_sop_bom_pipeline", "schemas", "config_schema.json")


@pytest.mark.unit
class TestVendoredCopies:
    """The two files this lambda vendors are copies, and the canonical paths must EXIST — a test
    that skipped on a missing canonical would pass while proving nothing."""

    def test_manifest_helper_is_byte_identical_to_the_canonical_copy(self):
        assert os.path.isfile(_CANONICAL_MANIFEST_HELPER), _CANONICAL_MANIFEST_HELPER
        local = os.path.join(_LAMBDA_DIR, "manifestHelper.py")
        assert os.path.isfile(local), local
        assert _normalized_digest(local) == _normalized_digest(_CANONICAL_MANIFEST_HELPER)

    def test_config_schema_is_byte_identical_to_the_container_copy(self):
        assert os.path.isfile(_CONTAINER_CONFIG_SCHEMA), (
            f"{_CONTAINER_CONFIG_SCHEMA} is missing; WP00 ships it and this lambda validates "
            f"against a copy of it")
        local = os.path.join(_LAMBDA_DIR, "config_schema.json")
        assert os.path.isfile(local), local
        assert _normalized_digest(local) == _normalized_digest(_CONTAINER_CONFIG_SCHEMA)

    def test_the_local_manifest_helper_is_the_one_imported(self):
        # Every pipeline ships a module called manifestHelper; conftest puts THIS lambda dir first
        # on sys.path, and the origin is asserted so a stray sibling copy cannot answer for it.
        import manifestHelper
        loaded = os.path.normcase(os.path.normpath(os.path.abspath(manifestHelper.__file__)))
        assert loaded == os.path.normcase(os.path.normpath(os.path.join(_LAMBDA_DIR, "manifestHelper.py")))
        for name in ("resolve_pipeline_inputs", "manifest_location", "fetch_manifest",
                     "fetch_metadata", "fetch_input_configuration", "to_legacy_vams_view",
                     "pipeline_execution_id_from_event_prefix", "parse_s3_uri"):
            assert callable(getattr(manifestHelper, name)), name


def _load_json(*parts):
    with open(os.path.join(_SCHEMA_ROOT, *parts), encoding="utf-8") as handle:
        return json.load(handle)


def _templates():
    """{file stem: parsed template} for every top-level templates/*.json."""
    template_dir = os.path.join(_SCHEMA_ROOT, "templates")
    return {name[:-5]: _load_json("templates", name)
            for name in sorted(os.listdir(template_dir)) if name.endswith(".json")}


def _as_patterns(extensions):
    """'.mp4,.mov' or ['*.mp4', '.mov'] -> {'*.mp4', '*.mov'}: the bundle's fnmatch spelling."""
    members = extensions.split(",") if isinstance(extensions, str) else list(extensions)
    patterns = set()
    for member in members:
        member = member.strip().lower()
        if not member:
            continue
        if member.startswith("*."):
            patterns.add(member)
        elif member.startswith("."):
            patterns.add("*" + member)
        else:
            patterns.add("*." + member)
    return patterns


_EXPECTED_PATTERNS = _as_patterns(_ALLOWED_EXTENSIONS)
_TAG_REF = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
_TYPED_TAG_TYPES = ("integer", "number", "boolean", "string-list")
_EXPECTED_FULL_DEFAULTS = {
    "mode": "full", "languageCode": "en-US", "videoOrder": "selection", "productName": "",
    "contributors": "", "maxKeyFrames": 60, "partLevelBase": "0", "generateLabSummary": True,
    "additionalInstructions": "",
}
_EXPECTED_TRANSCRIPT_DEFAULTS = {"mode": "transcript", "languageCode": "en-US", "videoOrder": "selection"}


def _open_pipeline_default():
    source = open(os.path.join(_LAMBDA_DIR, "openPipeline.py"), encoding="utf-8").read()
    match = re.search(
        r'os\.environ\.get\(\s*"ALLOWED_INPUT_FILEEXTENSIONS"\s*,\s*"([^"]*)"\s*\)', source)
    assert match, "openPipeline.py declares no ALLOWED_INPUT_FILEEXTENSIONS default"
    return match.group(1)


def _cdk_allow_list():
    """The literal WP04's construct passes to the openPipeline builder (same regex as the
    cross-pipeline extension-gate harness). Asserted to EXIST: this arm fails, never skips, until
    WP04 lands the construct."""
    path = os.path.join(_REPO_ROOT, "infra", "lib", "nestedStacks", "pipelines", "genAi",
                        "videoSopBom", "constructs", "videoSopBom-construct.ts")
    assert os.path.isfile(path), f"{path} is missing; WP04 creates it"
    source = open(path, encoding="utf-8").read()
    match = re.search(r"const allowedInputFileExtensions\s*=\s*([^;]*);", source)
    assert match, "no allowedInputFileExtensions declaration in the construct"
    return "".join(re.findall(r'"([^"]*)"', match.group(1)))


def _render_with_defaults(template):
    """The configBody with every tag at its default (or blank for a string/enum without one),
    substituted the way templateResolution does: a string is JSON-escaped inside the template's
    own quotes, a typed value becomes a JSON literal."""
    body = template["configBody"]
    for tag in template["tagSchema"]:
        value = tag.get("default")
        if value is None:
            value = ""
        stand_in = json.dumps(value)[1:-1] if isinstance(value, str) else json.dumps(value)
        body = body.replace("{{" + tag["tagKey"] + "}}", stand_in)
    return json.loads(body)


@pytest.mark.unit
class TestPipelineAndWorkflowBundle:
    def test_identity_and_category(self):
        pipeline, workflow = _load_json("pipeline.json"), _load_json("workflow.json")
        assert pipeline["pipelineName"] == "Video SOP/BOM Extraction"
        assert workflow["workflowName"] == "Video SOP/BOM Extraction"
        assert pipeline["category"] == "GenAI" and workflow["category"] == "GenAI"
        # Ids come from the CDK idOverrides (a literal a backend test greps), never from the bundle.
        assert "pipelineId" not in pipeline and "workflowId" not in workflow

    def test_execution_config_is_the_callback_shape_with_the_bundle_timeout(self):
        execution = _load_json("pipeline.json")["executionConfig"]
        assert execution == {"executionType": "Lambda", "waitForCallback": "Enabled",
                             "taskTimeout": "30600", "taskHeartbeatTimeout": "", "lambda": {}}

    def test_arity_is_multi_at_both_levels(self):
        assert _load_json("pipeline.json")["systemConfig"]["inputFileArity"] == "multi"
        assert _load_json("workflow.json")["systemConfig"]["inputFileArity"] == "multi"

    def test_whole_asset_and_folder_selection_are_refused_at_both_levels(self):
        pipeline_scope = _load_json("pipeline.json")["systemConfig"]["assetScope"]
        workflow_scope = _load_json("workflow.json")["systemConfig"]["assetScope"]
        assert pipeline_scope == {"wholeAsset": False, "folderAllowed": False}
        assert workflow_scope == {"crossAssetAllowed": False, "singleAssetOnly": True,
                                  "wholeAssetAllowed": False, "folderAllowed": False}

    def test_metadata_inputs_match_at_both_levels(self):
        expected = {"assetMetadata": True, "fileMetadata": False, "fileAttributes": False}
        assert _load_json("pipeline.json")["systemConfig"]["metadataInputs"] == expected
        assert _load_json("workflow.json")["systemConfig"]["metadataInputs"] == expected

    def test_template_gates(self):
        system = _load_json("pipeline.json")["systemConfig"]
        assert system["requireTemplate"] is True
        assert system["allowCustomTemplateOverride"] is True

    def test_the_allow_list_is_identical_in_the_bundle_and_the_open_pipeline_default(self):
        pipeline_filters = _load_json("pipeline.json")["systemConfig"]["inputFileFilters"]
        workflow_filters = _load_json("workflow.json")["systemConfig"]["inputFileFilters"]
        assert _as_patterns(pipeline_filters["allow"]) == _EXPECTED_PATTERNS
        assert _as_patterns(workflow_filters["allow"]) == _EXPECTED_PATTERNS
        assert pipeline_filters["exclude"] == [] and workflow_filters["exclude"] == []
        assert _open_pipeline_default() == _ALLOWED_EXTENSIONS

    def test_the_cdk_allow_list_matches_the_bundle(self):
        assert _cdk_allow_list() == _ALLOWED_EXTENSIONS
        assert _as_patterns(_cdk_allow_list()) == _EXPECTED_PATTERNS

    def test_the_description_names_exactly_the_accepted_formats(self):
        for description in (_load_json("pipeline.json")["description"],
                            _load_json("workflow.json")["description"]):
            for token in ("MP4", "MOV", "M4V", "WebM", "MKV"):
                assert token in description, token
            for legacy in ("AVI", "WMV", "FLV"):
                assert not re.search(rf"\b{legacy}\b", description, re.IGNORECASE), legacy
            assert len(description) <= 1024

    def test_no_trigger_and_the_per_run_output_folder(self):
        workflow = _load_json("workflow.json")
        assert "triggers" not in workflow
        system = workflow["systemConfig"]
        assert system["defaultOutputFileBaseExecutionPathExtension"] == "/{{executionId}}/"
        assert system["concurrencyRestriction"] == "none"
        assert system["outputTarget"] == {"locationType": "asset", "allowOverride": False}
        assert system["allowWorkflowTriggerChaining"] is False

    def test_the_workflow_leaves_the_pipeline_ref_to_the_importer(self):
        # vamsSchemaImport defaults specifiedPipelines to the single {GLOBAL, pipelineId} ref, so
        # the bundle carries no id-bearing block that could drift from the CDK literal.
        assert "specifiedPipelines" not in _load_json("workflow.json")

    def test_the_system_config_keys_are_the_documented_set(self):
        assert set(_load_json("pipeline.json")["systemConfig"]) == {
            "inputFileArity", "assetScope", "metadataInputs", "requireTemplate",
            "allowCustomTemplateOverride", "inputFileFilters"}
        assert set(_load_json("workflow.json")["systemConfig"]) == {
            "inputFileArity", "assetScope", "metadataInputs", "inputFileFilters",
            "concurrencyRestriction", "outputTarget", "allowWorkflowTriggerChaining",
            "defaultOutputFileBaseExecutionPathExtension"}


@pytest.mark.unit
class TestTemplates:
    def test_exactly_two_templates_with_one_default(self):
        templates = _templates()
        assert set(templates) == {"video-sop-bom-full", "video-sop-bom-transcript-only"}
        for stem, template in templates.items():
            assert template["templateId"] == stem
        assert templates["video-sop-bom-full"]["isDefault"] is True
        assert templates["video-sop-bom-transcript-only"].get("isDefault") is not True

    def test_every_template_is_a_json_body_with_instructions(self):
        for template in _templates().values():
            assert template["configFormat"] == "json"
            assert template["allowCustomEdit"] is True
            assert template["templateName"].strip() and len(template["templateName"]) <= 256
            assert len(template["description"]) <= 1024
            assert template["inputInstructions"].strip()
            assert len(template["inputInstructions"]) <= 4096

    def test_the_full_body_renders_the_nine_configuration_keys(self):
        assert _render_with_defaults(_templates()["video-sop-bom-full"]) == _EXPECTED_FULL_DEFAULTS

    def test_the_transcript_body_renders_exactly_three_keys(self):
        assert _render_with_defaults(_templates()["video-sop-bom-transcript-only"]) == \
            _EXPECTED_TRANSCRIPT_DEFAULTS

    def test_every_declared_tag_is_referenced_and_every_reference_is_declared(self):
        for stem, template in _templates().items():
            declared = {tag["tagKey"] for tag in template["tagSchema"]}
            referenced = set(_TAG_REF.findall(template["configBody"]))
            assert declared == referenced, (stem, declared ^ referenced)

    def test_quoting_is_type_driven(self):
        # A typed placeholder is the whole JSON value; a string/enum one sits inside the quotes.
        for stem, template in _templates().items():
            body = template["configBody"]
            for tag in template["tagSchema"]:
                placeholder = "{{" + tag["tagKey"] + "}}"
                if tag["type"] in _TYPED_TAG_TYPES:
                    assert f'"{placeholder}"' not in body, (stem, tag["tagKey"])
                    assert f":{placeholder}" in body, (stem, tag["tagKey"])
                else:
                    assert f'"{placeholder}"' in body, (stem, tag["tagKey"])

    def test_no_tag_is_required_and_every_typed_or_enum_tag_has_a_valid_default(self):
        for stem, template in _templates().items():
            for tag in template["tagSchema"]:
                assert tag.get("required") is not True, (stem, tag["tagKey"])
                if tag["type"] in _TYPED_TAG_TYPES:
                    assert tag.get("default") is not None, (stem, tag["tagKey"])
                if tag["type"] == "enum":
                    assert isinstance(tag.get("enumValues"), list) and tag["enumValues"]
                    assert tag["default"] in tag["enumValues"], (stem, tag["tagKey"])
                assert set(tag) <= {"tagKey", "type", "required", "default", "enumValues",
                                    "label", "description"}, (stem, tag["tagKey"])

    def test_the_tag_set_is_d15(self):
        templates = _templates()
        full = {tag["tagKey"]: tag for tag in templates["video-sop-bom-full"]["tagSchema"]}
        assert set(full) == {"LANGUAGE_CODE", "VIDEO_ORDER", "PRODUCT_NAME", "CONTRIBUTORS",
                             "MAX_KEY_FRAMES", "PART_LEVEL_BASE", "GENERATE_LAB_SUMMARY",
                             "ADDITIONAL_INSTRUCTIONS"}
        assert full["LANGUAGE_CODE"]["type"] == "enum" and full["LANGUAGE_CODE"]["default"] == "en-US"
        assert "auto" in full["LANGUAGE_CODE"]["enumValues"]
        assert full["VIDEO_ORDER"]["type"] == "enum" and full["VIDEO_ORDER"]["default"] == "selection"
        assert full["VIDEO_ORDER"]["enumValues"] == ["selection", "filename"]
        assert full["PRODUCT_NAME"]["type"] == "string" and "default" not in full["PRODUCT_NAME"]
        assert full["CONTRIBUTORS"]["type"] == "string" and "default" not in full["CONTRIBUTORS"]
        assert full["MAX_KEY_FRAMES"]["type"] == "integer" and full["MAX_KEY_FRAMES"]["default"] == 60
        assert full["PART_LEVEL_BASE"]["type"] == "enum" and full["PART_LEVEL_BASE"]["default"] == "0"
        assert full["PART_LEVEL_BASE"]["enumValues"] == ["0", "1"]
        assert full["GENERATE_LAB_SUMMARY"]["type"] == "boolean"
        assert full["GENERATE_LAB_SUMMARY"]["default"] is True
        assert full["ADDITIONAL_INSTRUCTIONS"]["type"] == "string"
        transcript = {tag["tagKey"]: tag
                      for tag in templates["video-sop-bom-transcript-only"]["tagSchema"]}
        assert set(transcript) == {"LANGUAGE_CODE", "VIDEO_ORDER"}
        for key in transcript:
            assert transcript[key] == full[key], key

    def test_enum_tags_mirror_the_config_schema(self):
        import configSchema
        properties = configSchema.load_config_schema()["properties"]
        full = {tag["tagKey"]: tag for tag in _templates()["video-sop-bom-full"]["tagSchema"]}
        assert full["LANGUAGE_CODE"]["enumValues"] == properties["languageCode"]["enum"]
        assert full["VIDEO_ORDER"]["enumValues"] == properties["videoOrder"]["enum"]
        assert full["PART_LEVEL_BASE"]["enumValues"] == properties["partLevelBase"]["enum"]
        key_frames = properties["maxKeyFrames"]
        assert key_frames["minimum"] <= full["MAX_KEY_FRAMES"]["default"] <= key_frames["maximum"]

    def test_rendered_defaults_validate_against_the_config_schema(self):
        import configSchema
        schema = configSchema.load_config_schema()
        for stem, template in _templates().items():
            assert configSchema.validate_config(_render_with_defaults(template), schema) == [], stem

    def test_input_instructions_state_the_input_rules(self):
        for stem, template in _templates().items():
            text = template["inputInstructions"].lower()
            for fragment in ("1-4", "whole-asset", "folder", "video_order", "4 gb", "16 gb",
                             "240 minutes", "results/summary.json", "sop-bom/"):
                assert fragment in text, (stem, fragment)

    def test_the_bundle_never_describes_a_no_speech_success(self):
        # A run whose audio carries no detectable speech is refused on the task token
        # (VideoSopBomInputRejected), so transcript.vtt and transcript.srt are unconditional outputs
        # of a successful run. Prose that qualifies them as "only when speech" or describes an empty
        # deliverable set would describe an outcome this pipeline never produces.
        texts = [_load_json("pipeline.json")["description"], _load_json("workflow.json")["description"]]
        for template in _templates().values():
            texts.append(template["description"])
            texts.append(template["inputInstructions"])
            texts.extend(tag["description"] for tag in template["tagSchema"])
        for text in texts:
            lowered = text.lower()
            assert "only when speech" not in lowered, text
            assert "empty deliverable" not in lowered, text
            assert "no speech" not in lowered, text

    def test_every_tag_has_a_label_and_a_description(self):
        for stem, template in _templates().items():
            for tag in template["tagSchema"]:
                assert tag["label"].strip() and len(tag["label"]) <= 1024, (stem, tag["tagKey"])
                assert tag["description"].strip() and len(tag["description"]) <= 1024, (stem, tag["tagKey"])
