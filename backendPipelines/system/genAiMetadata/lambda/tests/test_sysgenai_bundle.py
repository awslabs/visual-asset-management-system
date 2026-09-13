#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The vamsSchema bundle declares exactly what the classifier processes, on every surface that matches
files (the pipeline filter, the workflow filter, the file-upload trigger filter), and carries the
system-record fields the importer, the workflow lock and the template renderer read.

The three allow lists are hand-authored copies of one list, so identity is asserted rather than
assumed; a wildcard or an empty list on any of them is "any file" to executionValidation and would
put the pipeline in front of every upload."""

import importlib.util
import json
import os
import re

import pytest

import sysgenai_harness as h

fc = h.load_local("fileClassifier")
cv = h.load_local("classificationVocabulary")

PIPELINE_JSON = os.path.join(h.VAMS_SCHEMA_DIR, "pipeline.json")
WORKFLOW_JSON = os.path.join(h.VAMS_SCHEMA_DIR, "workflow.json")
TEMPLATES_DIR = os.path.join(h.VAMS_SCHEMA_DIR, "templates")
TEMPLATE_JSON = os.path.join(TEMPLATES_DIR, "system-genai-metadata-default.json")

# The importer rejects a match-everything pattern only in exclude lists; on an allow list it means
# "any file", which is why the finite form is asserted here (executionValidation.MATCH_EVERYTHING_PATTERNS).
MATCH_EVERYTHING_PATTERNS = ("*", "**", "*.*", "/*", "/**")
PATTERN_RE = re.compile(r"^\*\.[a-z0-9_]+$")
ID_RE = re.compile(r"^[-_a-zA-Z0-9]{3,63}$")
TAG_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

EXPECTED_TAGS = {
    "SEED_WITH_EXISTING_METADATA": ("boolean", True),
    "INCLUDE_SIBLING_FILES": ("boolean", True),
    "RENDER_VIEWS": ("integer", 8),
    "MAX_TEXT_CHARS": ("integer", 12000),
    "WRITE_ASSET_KEYWORDS": ("boolean", False),
    "EMBEDDING_INCLUDE_TEXT_EXCERPT": ("boolean", True),
    "WRITE_EXTRACTED_METADATA": ("boolean", True),
    "EXTRACT_GEO_LOCATION": ("boolean", True),
}
EXPECTED_CONFIG_KEYS = {
    "seedWithExistingMetadata": bool, "includeSiblingFiles": bool, "renderViews": int,
    "maxTextChars": int, "writeAssetKeywords": bool, "embeddingIncludeTextExcerpt": bool,
    "writeExtractedMetadata": bool, "extractGeoLocation": bool, "classificationVocabulary": dict,
}


def _backend_template_body_storage():
    """backend/backend/common/workflows/templateBodyStorage.py by path (it imports only hashlib and Decimal)."""
    path = os.path.join(h.REPO_ROOT, "backend", "backend", "common", "workflows", "templateBodyStorage.py")
    spec = importlib.util.spec_from_file_location("sysgenai_backend_templateBodyStorage", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def catalog_extensions():
    config = h.read_json_file(h.VIEWER_CONFIG)
    return {extension.lower() for viewer in config["viewers"] if viewer.get("enabled") is True
            for extension in viewer.get("supportedExtensions", []) if extension != "*"}


pipeline = h.read_json_file(PIPELINE_JSON)
workflow = h.read_json_file(WORKFLOW_JSON)
template = h.read_json_file(TEMPLATE_JSON)
EXPECTED_ALLOW = [f"*{extension}" for extension in fc.ALLOW_LIST]


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
    def test_the_three_surfaces_declare_one_identical_list(self):
        for name, filters in _allow_lists().items():
            assert filters["allow"] == EXPECTED_ALLOW, name
            assert filters["exclude"] == [], name

    def test_the_list_is_finite_and_never_open(self):
        for name, filters in _allow_lists().items():
            assert filters["allow"], f"{name}: an empty allow list means any file"
            for pattern in filters["allow"]:
                assert pattern not in MATCH_EVERYTHING_PATTERNS, (name, pattern)
                assert PATTERN_RE.match(pattern), (name, pattern)
            assert len(filters["allow"]) == len(set(filters["allow"])), name

    def test_the_list_covers_the_viewer_catalog(self):
        expected = {f"*{extension}" for extension in catalog_extensions() - fc.EXCLUDED_EXTENSIONS}
        assert set(pipeline["systemConfig"]["inputFileFilters"]["allow"]) >= expected
        assert len(expected) >= 80

    @pytest.mark.temporary  # pins the drop of .fls/.fws relative to the thumbnail allow list
    def test_the_faro_formats_are_not_admitted(self):
        # test_the_three_surfaces_declare_one_identical_list already forbids them (the classifier's
        # ALLOW_LIST is the viewer catalog); this pin exists only until the thumbnail list is retired.
        for filters in _allow_lists().values():
            assert "*.fls" not in filters["allow"] and "*.fws" not in filters["allow"]


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
        assert system_config["metadataInputs"] == {"assetMetadata": True, "fileMetadata": True,
                                                   "fileAttributes": True, "databaseMetadata": True}
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
        assert system_config["metadataInputs"] == {"assetMetadata": True, "fileMetadata": True,
                                                   "fileAttributes": True, "databaseMetadata": True}
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
        assert sorted(os.listdir(h.VAMS_SCHEMA_DIR)) == ["pipeline.json", "templates", "workflow.json"]
        assert os.listdir(TEMPLATES_DIR) == ["system-genai-metadata-default.json"]

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
        # Typed tags sit bare in a json body; quoting one hands the Lambda "8" where it expects 8.
        for key in EXPECTED_TAGS:
            assert '"{{' + key + '}}"' not in body, key

    def test_the_rendered_body_is_the_configuration_the_lambdas_read(self):
        config = _rendered_config()
        assert set(config) == set(EXPECTED_CONFIG_KEYS)
        for key, expected_type in EXPECTED_CONFIG_KEYS.items():
            assert type(config[key]) is expected_type, key
        assert config == {"seedWithExistingMetadata": True, "includeSiblingFiles": True, "renderViews": 8,
                          "maxTextChars": 12000, "writeAssetKeywords": False,
                          "embeddingIncludeTextExcerpt": True, "writeExtractedMetadata": True,
                          "extractGeoLocation": True, "classificationVocabulary": cv.DEFAULT_VOCABULARY}

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


def _rendered_config():
    rendered = template["configBody"]
    for key, (_tag_type, default) in EXPECTED_TAGS.items():
        rendered = rendered.replace("{{" + key + "}}", json.dumps(default))
    return json.loads(rendered)
