#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The vamsSchema bundle declares what the pipeline processes, on every surface that matches files (the
pipeline filter, the workflow filter, the file-upload trigger filter), and carries the system-record fields
the importer, the workflow lock and the template renderer read.

The pipeline takes every file: the allow list on all three surfaces is the one wildcard, the exclude list
keeps the preview thumbnails other pipelines write out of it (chaining is on, so without that exclusion
every `.previewFile.*` a thumbnail pipeline produces would start a run), and the CDK runtime gate is the
same wildcard. The classifier's ALLOW_LIST is the set of extensions with dedicated handling, which these
tests still tie to the viewer catalog."""

import importlib
import importlib.util
import json
import os
import re
import sys

import pytest

import sysgenai_harness as h

fc = h.load_local("fileClassifier")
cv = h.load_local("classificationVocabulary")

PIPELINE_JSON = os.path.join(h.VAMS_SCHEMA_DIR, "pipeline.json")
WORKFLOW_JSON = os.path.join(h.VAMS_SCHEMA_DIR, "workflow.json")
TEMPLATES_DIR = os.path.join(h.VAMS_SCHEMA_DIR, "templates")
TEMPLATE_JSON = os.path.join(TEMPLATES_DIR, "system-genai-metadata-default.json")

# The importer rejects a match-everything pattern in an exclude list (executionValidation.MATCH_EVERYTHING_PATTERNS);
# on an allow list "*" means "any file", which is this pipeline's contract.
MATCH_EVERYTHING_PATTERNS = ("*", "**", "*.*", "/*", "/**")
ANY_FILE_ALLOW = ["*"]
# The one exclusion: the preview thumbnails the preview pipelines write beside a file.
PREVIEW_EXCLUDE = ["*.previewFile.*"]
EXTENSION_RE = re.compile(r"^\.[a-z0-9_]+$")
ID_RE = re.compile(r"^[-_a-zA-Z0-9]{3,63}$")
TAG_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

EXPECTED_TAGS = {
    "SEED_WITH_EXISTING_METADATA": ("boolean", True),
    "RESOLVE_DEPENDENCIES": ("boolean", True),
    "RENDER_VIEWS": ("integer", 8),
    "MAX_TEXT_CHARS": ("integer", 12000),
    "METADATA_WRITE_TARGET": ("enum", "file"),
    "REUSE_EXISTING_KEYS": ("boolean", True),
    "EMBEDDING_INCLUDE_TEXT_EXCERPT": ("boolean", True),
    "WRITE_EXTRACTED_METADATA": ("boolean", True),
    "EXTRACT_GEO_LOCATION": ("boolean", True),
    "VIDEO_SEGMENT_SECONDS": ("integer", 0),
    "CONTENT_CHUNKING": ("boolean", True),
}
# A string or enum placeholder sits inside the JSON string it fills; every other type stands bare.
QUOTED_TAG_TYPES = {"string", "enum"}
EXPECTED_CONFIG_KEYS = {
    "seedWithExistingMetadata": bool, "resolveDependencies": bool, "renderViews": int,
    "maxTextChars": int, "metadataWriteTarget": str, "reuseExistingKeys": bool, "embeddingIncludeTextExcerpt": bool,
    "writeExtractedMetadata": bool, "extractGeoLocation": bool, "videoSegmentSeconds": int, "contentChunking": bool,
    "fieldMapping": dict, "classificationVocabulary": dict,
}
SCENE_TEMPLATE_JSON = os.path.join(TEMPLATES_DIR, "system-genai-metadata-scene-to-asset.json")
kr = h.load_local("keyReuse")


CDK_CONSTRUCT = os.path.join(h.REPO_ROOT, "infra", "lib", "nestedStacks", "pipelines", "system", "genAiMetadata",
                             "constructs", "systemGenAiMetadata-construct.ts")


def _backend_template_body_storage():
    """backend/backend/common/workflows/templateBodyStorage.py by path (it imports only hashlib and Decimal)."""
    path = os.path.join(h.REPO_ROOT, "backend", "backend", "common", "workflows", "templateBodyStorage.py")
    spec = importlib.util.spec_from_file_location("sysgenai_backend_templateBodyStorage", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cdk_allow_list_literal():
    """The construct's `allowedInputFileExtensions` value: every quoted piece of the declaration joined, so a
    literal written across several operands reads the same as a single-line one (the shape the cross-tree
    gate test in backendPipelines/tests/test_open_pipeline_extension_gates.py reads too)."""
    with open(CDK_CONSTRUCT, encoding="utf-8") as fh:
        source = fh.read()
    match = re.search(r"const allowedInputFileExtensions\s*=\s*([^;]*);", source)
    assert match, f"no allowedInputFileExtensions declaration in {CDK_CONSTRUCT}"
    return "".join(re.findall(r'"([^"]*)"', match.group(1)))


def catalog_extensions():
    return {extension.lower() for viewer in h.catalog_viewers() if viewer.get("enabled") is True
            for extension in viewer.get("supportedExtensions", []) if extension != "*"}


pipeline = h.read_json_file(PIPELINE_JSON)
workflow = h.read_json_file(WORKFLOW_JSON)
template = h.read_json_file(TEMPLATE_JSON)
scene_template = h.read_json_file(SCENE_TEMPLATE_JSON)


def _allow_lists():
    lists = {
        "pipeline": pipeline["systemConfig"]["inputFileFilters"],
        "workflow": workflow["systemConfig"]["inputFileFilters"],
    }
    for index, trigger in enumerate(workflow["triggers"]):
        lists[f"trigger[{index}]"] = trigger["inputFileFilters"]
    return lists


@pytest.mark.unit
class TestAllowLists:
    def test_every_surface_admits_any_file_and_excludes_only_previews(self):
        for name, filters in _allow_lists().items():
            assert filters["allow"] == ANY_FILE_ALLOW, name
            assert filters["exclude"] == PREVIEW_EXCLUDE, name

    def test_the_exclusion_is_not_a_match_everything_pattern(self):
        # The importer rejects these in an exclude list; the bundle would fail to register.
        for name, filters in _allow_lists().items():
            for pattern in filters["exclude"]:
                assert pattern not in MATCH_EVERYTHING_PATTERNS, (name, pattern)

    def test_the_exclusion_matches_a_preview_thumbnail_and_not_its_source(self):
        import fnmatch
        for pattern in PREVIEW_EXCLUDE:
            assert fnmatch.fnmatch("scan/pump.e57.previewFile.gif", pattern)
            assert fnmatch.fnmatch("model.glb.previewFile.png", pattern)
            assert not fnmatch.fnmatch("scan/pump.e57", pattern)
            assert not fnmatch.fnmatch("notes.previewfile", pattern)

    def test_the_classifier_allow_list_covers_the_viewer_catalog(self):
        # ALLOW_LIST is the set of extensions with dedicated handling: the viewer catalog plus the office formats.
        expected = catalog_extensions() - fc.EXCLUDED_EXTENSIONS
        assert set(fc.ALLOW_LIST) >= expected
        assert len(expected) >= 80
        assert {".docx", ".xlsx", ".pptx"} <= set(fc.ADDITIONAL_EXTENSIONS) <= set(fc.ALLOW_LIST)
        for extension in fc.ALLOW_LIST:
            assert EXTENSION_RE.match(extension), extension
        assert len(fc.ALLOW_LIST) == len(set(fc.ALLOW_LIST))

    def test_every_extension_of_the_allow_list_classifies_to_a_named_class(self):
        # An extension with dedicated handling never falls to `other`; the two sniffed ones are decided from the bytes.
        for extension in fc.ALLOW_LIST:
            if extension in fc.SNIFFED_EXTENSIONS:
                continue
            file_class, branch = fc.classify(extension, lambda n: b"")
            assert file_class != fc.CLASS_OTHER, extension
            assert branch in fc.RENDER_BRANCHES, extension

    def test_an_unlisted_extension_takes_the_media_branch_as_other(self):
        for extension in (".zzz", ".bak", ".dat", "", None):
            assert fc.classify(extension, lambda n: b"") == (fc.CLASS_OTHER, fc.BRANCH_MEDIA)

    def test_the_cdk_runtime_gate_admits_any_file(self):
        """The construct's `allowedInputFileExtensions` literal is what openPipeline gates uploads on at run
        time; a finite list there would reject, before the state machine starts, every file the bundle's
        wildcard admits, and no unit test of the handlers would notice."""
        assert _cdk_allow_list_literal() == "*"


@pytest.mark.unit
class TestPipelineRecord:
    def test_identity(self):
        assert pipeline["pipelineName"] == "SYSTEM - GenAI Metadata Generation"
        assert pipeline["category"] == "SYSTEM - GenAI"
        assert pipeline["isSystem"] is True
        assert "pipelineId" not in pipeline  # the CDK idOverrides supply system-genai-metadata

    def test_execution_config(self):
        assert pipeline["executionConfig"] == {
            "executionType": "Lambda", "waitForCallback": "Enabled", "taskTimeout": "18000",
            "taskHeartbeatTimeout": "", "lambda": {},
        }

    def test_system_config(self):
        system_config = pipeline["systemConfig"]
        assert system_config["inputFileArity"] == "one"
        assert system_config["assetScope"] == {"wholeAsset": False}
        # metadataSchema on both gates: a step receives the schema section only when the workflow and the step
        # both ask, and schema-aware key reuse reads it.
        assert system_config["metadataInputs"] == {"assetMetadata": True, "fileMetadata": True,
                                                   "fileAttributes": True, "databaseMetadata": True,
                                                   "metadataSchema": True}
        assert system_config["requireTemplate"] is False
        assert system_config["allowCustomTemplateOverride"] is True

    def test_only_importer_known_keys(self):
        assert set(pipeline) <= {"pipelineName", "category", "description", "isSystem", "executionConfig",
                                 "systemConfig"}


@pytest.mark.unit
class TestWorkflowRecord:
    def test_identity(self):
        assert workflow["workflowName"] == "SYSTEM - GenAI Metadata Generation"
        assert workflow["category"] == "SYSTEM - GenAI"
        assert workflow["isSystem"] is True
        assert "workflowId" not in workflow and "specifiedPipelines" not in workflow

    def test_system_config(self):
        system_config = workflow["systemConfig"]
        assert system_config["inputFileArity"] == "one"
        assert system_config["assetScope"] == {"crossAssetAllowed": False, "singleAssetOnly": True,
                                               "wholeAssetAllowed": False, "folderAllowed": False}
        # metadataSchema on both gates: a step receives the schema section only when the workflow and the step
        # both ask, and schema-aware key reuse reads it.
        assert system_config["metadataInputs"] == {"assetMetadata": True, "fileMetadata": True,
                                                   "fileAttributes": True, "databaseMetadata": True,
                                                   "metadataSchema": True}
        assert system_config["concurrencyRestriction"] == "perInputFileVersion"
        assert system_config["outputTarget"] == {"locationType": "asset", "allowOverride": False}
        assert system_config["allowWorkflowTriggerChaining"] is True

    def test_the_file_upload_trigger(self):
        assert len(workflow["triggers"]) == 1
        trigger = workflow["triggers"][0]
        assert trigger["triggerType"] == "fileUpload"
        assert trigger["defaultTemplateIds"] == {"GLOBAL:system-genai-metadata": "system-genai-metadata-default"}
        # The importer defaults an omitted enabled to True; the deploy-time triggerEnabled decides, so
        # the bundle states false explicitly.
        assert "enabled" in trigger and trigger["enabled"] is False
        assert set(trigger) == {"triggerType", "inputFileFilters", "defaultTemplateIds", "enabled"}

    def test_only_importer_known_keys(self):
        assert set(workflow) <= {"workflowName", "category", "description", "isSystem", "systemConfig",
                                 "triggers"}


@pytest.mark.unit
class TestTemplate:
    def test_bundle_layout(self):
        # assetSummary/ is the second registration's bundle (the asset summary workflow).
        assert sorted(os.listdir(h.VAMS_SCHEMA_DIR)) == ["assetSummary", "pipeline.json", "templates",
                                                         "workflow.json"]
        assert sorted(os.listdir(TEMPLATES_DIR)) == ["system-genai-metadata-default.json",
                                                     "system-genai-metadata-scene-to-asset.json"]

    def test_identity(self):
        assert template["templateId"] == "system-genai-metadata-default"
        assert ID_RE.match(template["templateId"])
        assert template["configFormat"] == "json"
        assert template["allowCustomEdit"] is True
        assert template["isDefault"] is True
        assert template["templateName"] and template["description"] and template["inputInstructions"]

    def test_tag_schema(self):
        fields = {field["tagKey"]: field for field in template["tagSchema"]}
        assert set(fields) == set(EXPECTED_TAGS)
        for key, (tag_type, default) in EXPECTED_TAGS.items():
            field = fields[key]
            assert TAG_KEY_RE.match(key) and not key.startswith("metadata_")
            assert field["type"] == tag_type
            assert field["required"] is False
            assert field["default"] == default and type(field["default"]) is type(default)
            assert field["label"] and field["description"]

    def test_every_tag_is_referenced_by_the_body(self):
        body = template["configBody"]
        for key in EXPECTED_TAGS:
            assert "{{" + key + "}}" in body, key
        # Typed tags sit bare in a json body; quoting one hands the Lambda "8" where it expects 8. A string or
        # enum tag sits inside the string it fills.
        for key, (tag_type, _default) in EXPECTED_TAGS.items():
            quoted = '"{{' + key + '}}"' in body
            assert quoted is (tag_type in QUOTED_TAG_TYPES), key

    def test_the_rendered_body_is_the_configuration_the_lambdas_read(self):
        config = _rendered_config()
        assert set(config) == set(EXPECTED_CONFIG_KEYS)
        for key, expected_type in EXPECTED_CONFIG_KEYS.items():
            assert type(config[key]) is expected_type, key
        assert config == {"seedWithExistingMetadata": True, "resolveDependencies": True, "renderViews": 8,
                          "maxTextChars": 12000, "metadataWriteTarget": "file", "reuseExistingKeys": True,
                          "embeddingIncludeTextExcerpt": True, "writeExtractedMetadata": True,
                          "extractGeoLocation": True, "videoSegmentSeconds": 0, "contentChunking": True,
                          "fieldMapping": kr.FIELD_MAPPING_DEFAULT,
                          "classificationVocabulary": cv.DEFAULT_VOCABULARY}

    def test_the_write_target_tag(self):
        field = {field["tagKey"]: field for field in template["tagSchema"]}["METADATA_WRITE_TARGET"]
        assert field["enumValues"] == ["file", "asset", "both"]
        assert field["default"] in field["enumValues"]

    def test_the_field_mapping_is_a_plain_object_equal_to_the_module_default(self):
        """fieldMapping is edited like the vocabulary (`--config-body-file`), so it sits in the body verbatim and
        a deploy restores the shipped default."""
        mapping = _rendered_config()["fieldMapping"]
        assert mapping == kr.FIELD_MAPPING_DEFAULT
        assert kr.normalize_field_mapping(mapping) == (kr.FIELD_MAPPING_DEFAULT, [])
        assert "{{" not in json.dumps(mapping)

    def test_the_input_instructions_name_every_body_key(self):
        for key in EXPECTED_CONFIG_KEYS:
            assert key in template["inputInstructions"], key

    def test_the_segment_tags_carry_their_notes(self):
        """VIDEO_SEGMENT_SECONDS is off by default and its description says why (the per-window cost);
        CONTENT_CHUNKING is on by default. The rendered body places both before the vocabulary object."""
        fields = {field["tagKey"]: field for field in template["tagSchema"]}
        video = fields["VIDEO_SEGMENT_SECONDS"]
        assert video["default"] == 0 and video["type"] == "integer"
        assert "USD" in video["description"] and "360" in video["description"] and "off" in video["description"]
        chunking = fields["CONTENT_CHUNKING"]
        assert chunking["default"] is True and chunking["type"] == "boolean"
        assert "1,600" in chunking["description"] and "1,000" in chunking["description"]
        keys = list(_rendered_config())
        assert keys[-4:] == ["videoSegmentSeconds", "contentChunking", "fieldMapping", "classificationVocabulary"]
        assert len(template["tagSchema"]) == 11

    def test_the_vocabulary_is_a_plain_object_equal_to_the_module_default(self):
        """The vocabulary is edited with `vamscli pipeline template update --config-body-file`, so it sits in
        the body verbatim rather than behind a tag; the module default is what the CDK re-registration restores."""
        assert _rendered_config()["classificationVocabulary"] == cv.DEFAULT_VOCABULARY
        assert "classificationVocabulary" not in {field["tagKey"] for field in template["tagSchema"]}
        assert "{{" not in json.dumps(_rendered_config()["classificationVocabulary"])

    def test_the_vocabulary_stays_small(self):
        encoded = json.dumps(_rendered_config()["classificationVocabulary"], separators=(",", ":")).encode("utf-8")
        assert 1500 < len(encoded) < cv.VOCABULARY_MAX_BYTES == 6 * 1024, len(encoded)

    def test_the_body_stays_inline_on_the_template_row(self):
        storage = _backend_template_body_storage()
        size = len(template["configBody"].encode("utf-8"))
        assert 3000 < size <= storage.INLINE_THRESHOLD_BYTES == 300 * 1024, size

    def test_every_placeholder_in_the_body_is_a_declared_tag(self):
        # The inverse of test_every_tag_is_referenced_by_the_body: an undeclared {{X}} renders unresolved.
        placeholders = set(re.findall(r"\{\{([A-Za-z0-9_]+)\}\}", template["configBody"]))
        assert placeholders == set(EXPECTED_TAGS)


def _rendered_config(source=None):
    """The body with every tag at its default: a quoted (string, enum) tag as its bare text inside its quotes,
    every other tag as its JSON value."""
    source = source or template
    defaults = {field["tagKey"]: field["default"] for field in source["tagSchema"]}
    types = {field["tagKey"]: field["type"] for field in source["tagSchema"]}
    rendered = source["configBody"]
    for key, default in defaults.items():
        value = str(default) if types[key] in QUOTED_TAG_TYPES else json.dumps(default)
        rendered = rendered.replace("{{" + key + "}}", value)
    return json.loads(rendered)


@pytest.mark.unit
class TestSceneToAssetTemplate:
    """The second template describes a scene file that stands for the whole asset and writes to the file and the
    asset. It is the default template with three differences: its identity, not the default, and the write
    target's default."""

    def test_identity(self):
        assert scene_template["templateId"] == "system-genai-metadata-scene-to-asset"
        assert ID_RE.match(scene_template["templateId"])
        assert scene_template["isDefault"] is False
        assert scene_template["templateName"] and scene_template["description"]
        assert scene_template["templateName"] != template["templateName"]

    def test_it_writes_to_both_the_file_and_the_asset(self):
        assert _rendered_config(scene_template)["metadataWriteTarget"] == "both"

    def test_it_is_the_default_template_apart_from_identity_and_the_write_target(self):
        def comparable(source):
            body = {key: value for key, value in source.items()
                    if key not in ("templateId", "templateName", "description", "isDefault")}
            body["tagSchema"] = [dict(field, default=None) if field["tagKey"] == "METADATA_WRITE_TARGET" else field
                                 for field in body["tagSchema"]]
            return body
        assert comparable(scene_template) == comparable(template)

    def test_only_one_template_of_the_bundle_is_the_default(self):
        assert [t["templateId"] for t in (template, scene_template) if t["isDefault"]] == [
            "system-genai-metadata-default"]
        assert workflow["triggers"][0]["defaultTemplateIds"] == {
            "GLOBAL:system-genai-metadata": "system-genai-metadata-default"}


@pytest.mark.unit
class TestTemplatesRenderThroughTheBackend:
    """Each template's tag schema and body pass the backend's own tag validation and substitution, so the
    importer and the execute path accept what this bundle ships."""

    @pytest.fixture(autouse=True)
    def _backend(self):
        backend_dir = os.path.join(h.REPO_ROOT, "backend", "backend")
        sys.path.insert(0, backend_dir)
        try:
            self.tag_schema = importlib.import_module("common.workflows.templateTagSchema")
            self.resolution = importlib.import_module("common.workflows.templateResolution")
            yield
        finally:
            sys.path.remove(backend_dir)

    def _render(self, source, provided):
        errors, filled = self.tag_schema.validate_tags(source["tagSchema"], provided)
        assert errors == [], (source["templateId"], errors)
        body, errors = self.resolution._substitute_user_tags(source["configBody"], filled, source["configFormat"])
        assert errors == [], (source["templateId"], errors)
        return json.loads(body)

    @pytest.mark.parametrize("source", [template, scene_template], ids=lambda t: t["templateId"])
    def test_every_tag_schema_is_valid(self, source):
        assert self.tag_schema.validate_tag_schema(source["tagSchema"]) == []

    @pytest.mark.parametrize("source", [template, scene_template], ids=lambda t: t["templateId"])
    def test_the_defaults_render_to_the_configuration_the_lambdas_read(self, source):
        assert self._render(source, []) == _rendered_config(source)

    @pytest.mark.parametrize("target", ["file", "asset", "both"])
    def test_every_write_target_renders(self, target):
        config = self._render(template, [{"key": "METADATA_WRITE_TARGET", "value": target}])
        assert config["metadataWriteTarget"] == target

    def test_an_unlisted_write_target_is_refused(self):
        errors, _filled = self.tag_schema.validate_tags(template["tagSchema"],
                                                        [{"key": "METADATA_WRITE_TARGET", "value": "database"}])
        assert errors

    @pytest.mark.parametrize("value", [True, False])
    def test_the_reuse_and_dependency_switches_render(self, value):
        config = self._render(template, [{"key": "REUSE_EXISTING_KEYS", "value": value},
                                         {"key": "RESOLVE_DEPENDENCIES", "value": value}])
        assert config["reuseExistingKeys"] is value and config["resolveDependencies"] is value
